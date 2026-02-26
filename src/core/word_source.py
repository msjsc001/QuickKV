# -*- coding: utf-8 -*-
import sys
import os
import webbrowser
import configparser
import hashlib
import json
import subprocess
import re
import itertools
import threading
import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QSystemTrayIcon, QMenu, QSizeGrip,
                             QGraphicsDropShadowEffect, QPushButton,
                             QInputDialog, QMessageBox, QStyledItemDelegate, QStyle, QFileDialog,
                             QCheckBox, QWidgetAction, QScrollArea, QLabel, QFrame)
from PySide6.QtCore import (Qt, Signal, Slot, QObject,
                          QTimer, QEvent, QRect, QProcess)
from PySide6.QtGui import QIcon, QAction, QCursor, QPixmap, QPainter, QColor, QPalette, QActionGroup
import pyperclip
from pypinyin import pinyin, Style
from pynput import keyboard
from fuzzywuzzy import fuzz
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- 拼音库修正 ---
# 导入 pypinyin-dict 的高质量词典数据，以修正 pypinyin 默认词典中的罕见音问题
try:
    from pypinyin_dict.pinyin_data import kxhc1983
    kxhc1983.load()
    from pypinyin_dict.phrase_pinyin_data import cc_cedict
    cc_cedict.load()
    print("成功加载 pypinyin-dict 修正词典。")
except ImportError:
    print("警告: 未找到 pypinyin-dict 库，拼音首字母可能不准确。建议安装: pip install pypinyin-dict")


from core.config import *

# --- 词库数据源 ---
class WordSource:
    def __init__(self, file_path):
        self.file_path = file_path
        self.word_blocks = []
        self.load()

    def load(self):
        log(f"开始从 {self.file_path} 加载词库...")
        self.word_blocks = []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            current_block = None
            for line in lines:
                if line.startswith('- '):
                    if current_block:
                        current_block['full_content'] = '\n'.join(current_block['raw_lines'])
                        self.word_blocks.append(current_block)
                    
                    parent_text = line.strip()[2:].strip()
                    
                    # --- 新的元命令解析逻辑 ---
                    # 匹配所有 ``...`` 形式的元命令
                    meta_commands_pattern = r'``(.*?)``'
                    meta_commands = re.findall(meta_commands_pattern, parent_text)
                    
                    # 从原始文本中移除所有元命令，得到纯净的 parent_text
                    clean_parent_text = re.sub(meta_commands_pattern, '', parent_text).strip()

                    should_exclude = False
                    shortcut_code = None

                    # 遍历找到的所有元命令并进行处理
                    for command in meta_commands:
                        if command == '不出现':
                            should_exclude = True
                        elif command.startswith('k:'):
                            # 提取 'k:' 后面的内容作为快捷码
                            shortcut_code = command[2:].strip()
                    # --- 新逻辑结束 ---

                    current_block = {
                        'parent': clean_parent_text, # 使用纯净文本
                        'raw_lines': [line.rstrip()],
                        'exclude_parent': should_exclude,
                        'shortcut_code': shortcut_code, # 新增：快捷码
                        'source_path': self.file_path, # 标记来源
                        'is_clipboard': False # 默认非剪贴板
                    }
                elif current_block:
                    current_block['raw_lines'].append(line.rstrip())

            if current_block:
                current_block['full_content'] = '\n'.join(current_block['raw_lines'])
                self.word_blocks.append(current_block)
            
            log(f"成功从 {os.path.basename(self.file_path)} 加载 {len(self.word_blocks)} 个词条。")
        except FileNotFoundError:
            log(f"词库文件不存在: {self.file_path}")
        except Exception as e:
            log(f"加载 {self.file_path} 时发生错误: {e}")

    def add_entry(self, content):
        try:
            with open(self.file_path, 'a', encoding='utf-8') as f:
                f.write('\n' + content)
            return True
        except Exception as e:
            log(f"向 {self.file_path} 添加词条时发生错误: {e}")
            return False

    def update_entry(self, original_content, new_content):
        try:
            # 使用 WordSource 的加载逻辑来健壮地解析文件
            loader = WordSource(self.file_path)
            if not hasattr(loader, 'word_blocks'):
                return False

            all_blocks = loader.word_blocks
            
            # 寻找并替换要更新的块
            found = False
            new_blocks = []
            for block in all_blocks:
                if block['full_content'] == original_content:
                    # 创建一个新的 block 结构来代表更新后的内容
                    # 注意：这里我们只替换 full_content，其他字段不会被使用
                    new_blocks.append({'full_content': new_content})
                    found = True
                else:
                    new_blocks.append(block)
            
            if not found:
                log(f"update_entry: 在 {self.file_path} 中未找到要更新的内容")
                return False

            # 从更新后的块列表中重建文件内容
            # 使用 '\n' 作为分隔符，因为 add_entry 会在每个条目前加一个换行符
            new_file_content = '\n'.join([block['full_content'] for block in new_blocks])
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(new_file_content)
                
            return True
        except Exception as e:
            log(f"更新 {self.file_path} 时发生错误: {e}")
            return False

    def delete_entry(self, content_to_delete):
        try:
            # 使用 WordSource 的加载逻辑来健壮地解析文件
            loader = WordSource(self.file_path)
            if not hasattr(loader, 'word_blocks'):
                return False

            all_blocks = loader.word_blocks
            
            # 过滤掉要删除的块
            remaining_blocks = [block for block in all_blocks if block['full_content'] != content_to_delete]
            
            # 如果块的数量没有减少，说明没有找到要删除的内容
            if len(remaining_blocks) == len(all_blocks):
                log(f"delete_entry: 在 {self.file_path} 中未找到要删除的内容")
                return False

            # 从剩余的块中重建文件内容
            # 使用 '\n' 作为分隔符，因为 add_entry 会在每个条目前加一个换行符
            new_file_content = '\n'.join([block['full_content'] for block in remaining_blocks])
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(new_file_content)
                
            return True
        except Exception as e:
            log(f"删除 {self.file_path} 的词条时发生错误: {e}")
            return False
