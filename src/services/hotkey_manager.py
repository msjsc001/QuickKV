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

# --- 原生快捷键管理器 (Windows) ---
class NativeHotkeyManager(QObject):
    hotkey_triggered = Signal(int)

    def __init__(self, hotkey_str):
        super().__init__()
        self.user32 = ctypes.windll.user32
        self.hotkey_id = 1
        self.mod, self.vk = self._parse_hotkey(hotkey_str)
        self._running = False
        self.thread = None

    def _parse_hotkey(self, hotkey_str):
        parts = hotkey_str.lower().split('+')
        vk_code = 0
        mod_code = 0
        # Modifiers
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        
        if 'ctrl' in parts: mod_code |= MOD_CONTROL
        if 'alt' in parts: mod_code |= MOD_ALT
        if 'shift' in parts: mod_code |= MOD_SHIFT
        if 'win' in parts: mod_code |= MOD_WIN
        
        key = parts[-1]
        if len(key) == 1:
            vk_code = ord(key.upper())
        else:
            # 映射常用功能键, 更多键需要扩展
            vk_map = {'space': 0x20, 'enter': 0x0D, 'esc': 0x1B, 'f1': 0x70}
            vk_code = vk_map.get(key, 0)
            
        return mod_code, vk_code

    def _listen(self):
        self.user32.RegisterHotKey(None, self.hotkey_id, self.mod, self.vk)
        log(f"原生快捷键已注册 (ID: {self.hotkey_id})")
        
        try:
            msg = wintypes.MSG()
            while self._running and self.user32.GetMessageA(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312: # WM_HOTKEY
                    if msg.wParam == self.hotkey_id:
                        self.hotkey_triggered.emit(self.hotkey_id)
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageA(ctypes.byref(msg))
        finally:
            self.user32.UnregisterHotKey(None, self.hotkey_id)
            log("原生快捷键已注销。")

    def reregister(self, new_hotkey_str):
        """动态重新注册快捷键"""
        self.stop()
        self.mod, self.vk = self._parse_hotkey(new_hotkey_str)
        self.start()

    def start(self):
        if not self._running:
            self._running = True
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("原生快捷键监听线程已启动。")

    def stop(self):
        """
        停止监听线程。
        【已加固】增加了对线程状态的检查和更安全的退出机制。
        """
        if self._running and self.thread:
            self._running = False
            try:
                # PostThreadMessageA 需要一个有效的线程ID。
                # 如果线程已经结束，ident 可能会无效。
                if self.thread.is_alive():
                    ctypes.windll.user32.PostThreadMessageA(self.thread.ident, 0x0012, 0, 0) # WM_QUIT
                    self.thread.join(timeout=1.5) # 给予更长的超时时间
            except Exception as e:
                log(f"CRITICAL: 停止原生快捷键监听线程时出错: {e}")
            finally:
                self.thread = None
                log("原生快捷键监听服务已停止。")
