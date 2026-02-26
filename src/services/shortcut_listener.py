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

# --- 快捷码监听器 ---
class ShortcutListener(QObject):
    shortcut_matched = Signal(str, str) # 发送匹配到的词条内容和快捷码本身

    def __init__(self, word_manager):
        super().__init__()
        self.word_manager = word_manager
        self.listener = None
        self.typed_buffer = ""
        self.shortcut_map = {}
        self.thread = None
        self._running = False
        self.keyboard_controller = keyboard.Controller()

    def update_shortcuts(self):
        """从词库更新快捷码映射"""
        self.shortcut_map = {}
        all_blocks = self.word_manager.word_blocks + self.word_manager.clipboard_history
        for block in all_blocks:
            if block.get('shortcut_code'):
                self.shortcut_map[block['shortcut_code'].lower()] = block
        log(f"快捷码监听器已更新，共 {len(self.shortcut_map)} 个快捷码。")

    def _on_press(self, key):
        """
        pynput 的按键事件回调函数。
        【已加固】增加了顶级的异常捕获，防止任何意外错误导致监听线程崩溃。
        """
        try:
            if not self._running:
                return False # 停止监听

            char = None
            if hasattr(key, 'char'):
                char = key.char
            elif key == keyboard.Key.space:
                char = ' '
            
            if char:
                self.typed_buffer += char
                buffer_lower = self.typed_buffer.lower()
                # 从最长的快捷码开始匹配，避免短码提前触发
                # (这是一个小的优化，但对健壮性有好处)
                sorted_codes = sorted(self.shortcut_map.keys(), key=len, reverse=True)
                for code in sorted_codes:
                    if buffer_lower.endswith(code):
                        log(f"快捷码 '{code}' 匹配成功!")
                        block = self.shortcut_map[code]
                        self.shortcut_matched.emit(block['full_content'], code)
                        self.typed_buffer = "" # 重置缓冲区
                        return # 匹配成功后立即返回
            else:
                # 任何非字符键（如Ctrl, Shift, Enter）都会重置缓冲区
                self.typed_buffer = ""

            # 限制缓冲区长度，防止内存无限增长
            if len(self.typed_buffer) > 50:
                self.typed_buffer = self.typed_buffer[-50:]

        except Exception as e:
            # 【关键】捕获所有未知异常，记录日志，并重置状态，但绝不让线程退出
            log(f"CRITICAL: 快捷码监听器 _on_press 发生严重错误: {e}")
            self.typed_buffer = ""

    def _listen(self):
        """监听线程的实际运行函数"""
        log("快捷码监听线程已启动。")
        with keyboard.Listener(on_press=self._on_press) as self.listener:
            self.listener.join()
        log("快捷码监听线程已停止。")

    def start(self):
        if not self._running:
            self._running = True
            self.update_shortcuts()
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("快捷码监听服务已启动。")

    def stop(self):
        """
        停止监听线程。
        【已加固】增加了对 listener 和 thread 对象的检查。
        """
        if self._running:
            self._running = False
            try:
                if self.listener and self.listener.is_alive():
                    self.listener.stop()
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=1.5)
            except Exception as e:
                log(f"CRITICAL: 停止快捷码监听服务时出错: {e}")
            finally:
                self.listener = None
                self.thread = None
                log("快捷码监听服务已停止。")
