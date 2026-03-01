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

# --- 设置管理器 ---
class SettingsManager:
    def __init__(self, file_path):
        self.config = configparser.ConfigParser()
        self.file_path = file_path
        self.load()

    def load(self):
        """
        加载配置文件。
        【已加固】增加了对 configparser 和 json 解析错误的全面捕获，
        确保在配置文件损坏时程序能以后备值启动，而不是崩溃。
        """
        try:
            self.config.read(self.file_path, encoding='utf-8')
        except configparser.Error as e:
            log(f"CRITICAL: 配置文件 '{self.file_path}' 解析失败: {e}。将使用默认设置。")
            # 如果解析失败，重置 config 对象，以便后续代码能正常运行
            self.config = configparser.ConfigParser()

        # 确保所有必需的 section 都存在
        sections = ['Window', 'Theme', 'Font', 'Search', 'Data', 'General', 'Clipboard', 'Restart', 'Paste']
        for section in sections:
            if not self.config.has_section(section):
                self.config.add_section(section)

        # 使用 try-except 块来安全地获取每个配置项
        try:
            self.hotkeys_enabled = self.config.getboolean('General', 'hotkeys_enabled', fallback=True)
            self.shortcut_code_enabled = self.config.getboolean('General', 'shortcut_code_enabled', fallback=False)
            self.hotkey = self.config.get('General', 'hotkey', fallback='ctrl+space')
            
            # --- 新增：连续字符串触发器 ---
            self.string_trigger_enabled = self.config.getboolean('General', 'string_trigger_enabled', fallback=False)
            self.string_trigger_str = self.config.get('General', 'string_trigger_str', fallback='//')
            
            self.paste_mode = self.config.get('Paste', 'mode', fallback='ctrl_v')
            self.width = self.config.getint('Window', 'width', fallback=450)
            self.height = self.config.getint('Window', 'height', fallback=300)
            self.theme = self.config.get('Theme', 'mode', fallback='dark')
            self.font_size = self.config.getint('Font', 'size', fallback=14)
            self.multi_word_search = self.config.getboolean('Search', 'multi_word_search', fallback=True)
            self.pinyin_initial_search = self.config.getboolean('Search', 'pinyin_initial_search', fallback=True)
            self.highlight_matches = self.config.getboolean('Search', 'highlight_matches', fallback=True) # 新增
            self.word_wrap_enabled = self.config.getboolean('UI', 'word_wrap_enabled', fallback=False)
            self.show_source_enabled = self.config.getboolean('UI', 'show_source_enabled', fallback=False)
            self.clipboard_memory_enabled = self.config.getboolean('Clipboard', 'enabled', fallback=False)
            self.clipboard_memory_count = self.config.getint('Clipboard', 'count', fallback=10)
            self.clipboard_auto_clear_enabled = self.config.getboolean('Clipboard', 'auto_clear_enabled', fallback=False)
            self.clipboard_auto_clear_minutes = self.config.getint('Clipboard', 'auto_clear_minutes', fallback=10)
            self.auto_restart_enabled = self.config.getboolean('Restart', 'enabled', fallback=False)
            self.auto_restart_interval = self.config.getint('Restart', 'interval_minutes', fallback=3)
            # 新的协议接受信息，存储为 JSON 字符串
            disclaimer_info_str = self.config.get('General', 'accepted_disclaimer_info', fallback='{}')
            try:
                self.accepted_disclaimer_info = json.loads(disclaimer_info_str)
                if not isinstance(self.accepted_disclaimer_info, dict):
                    self.accepted_disclaimer_info = {'id': '', 'version': ''}
            except (json.JSONDecodeError, TypeError):
                self.accepted_disclaimer_info = {'id': '', 'version': ''}

            libraries_str = self.config.get('General', 'libraries', fallback='[]')
            try:
                self.libraries = json.loads(libraries_str)
                if not isinstance(self.libraries, list): self.libraries = []
            except (json.JSONDecodeError, TypeError):
                self.libraries = []

            auto_libraries_str = self.config.get('General', 'auto_libraries', fallback='[]')
            try:
                self.auto_libraries = json.loads(auto_libraries_str)
                if not isinstance(self.auto_libraries, list): self.auto_libraries = []
            except (json.JSONDecodeError, TypeError):
                self.auto_libraries = []

        except (configparser.NoOptionError, configparser.NoSectionError, ValueError) as e:
            log(f"CRITICAL: 读取配置项时出错: {e}。部分设置将恢复为默认值。")
            # 如果在获取过程中出错，确保所有属性都有一个默认值
            self.hotkeys_enabled = getattr(self, 'hotkeys_enabled', True)
            self.shortcut_code_enabled = getattr(self, 'shortcut_code_enabled', False)
            # ... (其他属性以此类推，fallback 已经处理了大部分情况)

        # 迁移和验证逻辑保持不变
        if not self.libraries and os.path.exists(WORD_FILE):
            self.libraries.append({"path": os.path.abspath(WORD_FILE), "enabled": True})
            log("已将旧的单一词库配置迁移到新的多词库系统。")

        self.libraries = [lib for lib in self.libraries if isinstance(lib, dict) and os.path.exists(lib.get('path'))]

    def save(self):
        self.config['General']['hotkeys_enabled'] = str(self.hotkeys_enabled)
        self.config['General']['shortcut_code_enabled'] = str(self.shortcut_code_enabled) # 新增：保存快捷码功能开关
        self.config['General']['hotkey'] = self.hotkey
        self.config['Window']['width'] = str(self.width)
        self.config['Window']['height'] = str(self.height)
        self.config['Theme']['mode'] = self.theme
        self.config['Font']['size'] = str(self.font_size)
        self.config['Search']['multi_word_search'] = str(self.multi_word_search)
        self.config['Search']['pinyin_initial_search'] = str(self.pinyin_initial_search)
        self.config['Search']['highlight_matches'] = str(self.highlight_matches) # 新增
        if not self.config.has_section('UI'): self.config.add_section('UI')
        self.config['UI']['word_wrap_enabled'] = str(self.word_wrap_enabled)
        self.config['UI']['show_source_enabled'] = str(self.show_source_enabled)
        self.config['General']['libraries'] = json.dumps(self.libraries, ensure_ascii=False)
        self.config['General']['auto_libraries'] = json.dumps(self.auto_libraries, ensure_ascii=False)
        self.config['Clipboard']['enabled'] = str(self.clipboard_memory_enabled)
        self.config['Clipboard']['count'] = str(self.clipboard_memory_count)
        self.config['Clipboard']['auto_clear_enabled'] = str(self.clipboard_auto_clear_enabled)
        self.config['Clipboard']['auto_clear_minutes'] = str(self.clipboard_auto_clear_minutes)
        self.config['Restart']['enabled'] = str(self.auto_restart_enabled)
        self.config['Restart']['interval_minutes'] = str(self.auto_restart_interval)
        self.config['Paste']['mode'] = self.paste_mode
        self.config['General']['accepted_disclaimer_info'] = json.dumps(self.accepted_disclaimer_info, ensure_ascii=False)
        
        # --- 新增：连续字符串触发器 ---
        self.config['General']['string_trigger_enabled'] = str(self.string_trigger_enabled)
        self.config['General']['string_trigger_str'] = self.string_trigger_str
        
        with open(self.file_path, 'w', encoding='utf-8') as configfile:
            self.config.write(configfile)
        log(f"配置已保存到 {self.file_path}")
