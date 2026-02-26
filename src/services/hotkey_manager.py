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
        
        # 字符串触发控制
        self.keyboard_listener = None
        self.string_trigger_enabled = False
        self.string_trigger_str = ""
        self.trigger_queue = None

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
        self.stop_hotkey()
        self.mod, self.vk = self._parse_hotkey(new_hotkey_str)
        self.start_hotkey()
        
    def unregister_all(self):
        """只停用系统组合键，不影响字符串触发"""
        self.stop_hotkey()

    def start_hotkey(self):
        if not self._running:
            self._running = True
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("原生快捷键监听线程已启动。")
            
    def stop_hotkey(self):
        if self._running and self.thread:
            self._running = False
            try:
                if self.thread.is_alive():
                    ctypes.windll.user32.PostThreadMessageA(self.thread.ident, 0x0012, 0, 0)
                    self.thread.join(timeout=1.5)
            except Exception as e:
                pass
            finally:
                self.thread = None
                log("原生快捷键监听服务已停止。")

    def start(self):
        self.start_hotkey()

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

        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
            log("字符串盲打监听器已停止。")

    
    # --- 连续字符串盲打触发 (Continuous String Trigger) ---
    def update_string_trigger(self, enabled, trigger_str):
        self.string_trigger_enabled = enabled
        self.string_trigger_str = trigger_str
        
        # 总是先关掉旧的监听器
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
            
        if self.string_trigger_enabled and self.string_trigger_str and len(self.string_trigger_str) >= 2:
            from collections import deque
            from pynput.keyboard import Controller
            
            self.trigger_queue = deque(maxlen=len(self.string_trigger_str))
            
            def on_press(key):
                try:
                    # 获取输入的字符
                    if hasattr(key, 'char') and key.char is not None:
                        char = key.char
                    else:
                        # 遇到控制键等非字符输入，直接清空队列，防止把 "1" "Enter" "2" 匹配为 "12"
                        self.trigger_queue.clear()
                        return
                        
                    self.trigger_queue.append(char)
                    current_str = "".join(self.trigger_queue)
                    
                    if current_str == self.string_trigger_str:
                        log(f"命中连续字符序列: {self.string_trigger_str}")
                        self.trigger_queue.clear()
                        
                        # 触发自动擦除补偿机制 (Auto-Erase Compensation)
                        ctrl = Controller()
                        for _ in range(len(self.string_trigger_str)):
                            from pynput.keyboard import Key
                            ctrl.press(Key.backspace)
                            ctrl.release(Key.backspace)
                            
                        # 发送唤醒信号
                        self.hotkey_triggered.emit(9999) # 使用特征ID 9999 代表字符串触发
                except Exception as e:
                    log(f"键盘监听异常: {e}")

            self.keyboard_listener = keyboard.Listener(on_press=on_press)
            self.keyboard_listener.start()
            log(f"字符串盲打监听器已启动 (识别序列: '{self.string_trigger_str}')")
