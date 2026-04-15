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

HOTKEY_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
HOTKEY_MODIFIER_FLAGS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}
HOTKEY_SPECIAL_VK_MAP = {
    "space": 0x20,
    "enter": 0x0D,
    "esc": 0x1B,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
UNSUPPORTED_HOTKEY_KEYS = {"tab", "backspace"}


def parse_hotkey_string(hotkey_str):
    """校验并规范化热键字符串，同时输出 Windows 注册所需的 mod/vk。"""
    raw_hotkey = (hotkey_str or "").strip().lower()
    if not raw_hotkey:
        return {
            "valid": False,
            "normalized": "",
            "mod_code": 0,
            "vk_code": 0,
            "error": "请先输入一个快捷键。",
        }

    parts = [part.strip().lower() for part in raw_hotkey.split("+")]
    if any(not part for part in parts):
        return {
            "valid": False,
            "normalized": raw_hotkey,
            "mod_code": 0,
            "vk_code": 0,
            "error": "快捷键格式无效，请重新输入。",
        }

    modifier_parts = parts[:-1]
    main_key = parts[-1]

    invalid_modifier = next((part for part in modifier_parts if part not in HOTKEY_MODIFIER_ORDER), None)
    if invalid_modifier:
        return {
            "valid": False,
            "normalized": raw_hotkey,
            "mod_code": 0,
            "vk_code": 0,
            "error": f"不支持的修饰键：{invalid_modifier}",
        }

    if main_key in HOTKEY_MODIFIER_ORDER:
        return {
            "valid": False,
            "normalized": raw_hotkey,
            "mod_code": 0,
            "vk_code": 0,
            "error": "快捷键必须包含且只包含一个主键。",
        }

    if main_key in UNSUPPORTED_HOTKEY_KEYS:
        return {
            "valid": False,
            "normalized": raw_hotkey,
            "mod_code": 0,
            "vk_code": 0,
            "error": f"当前版本不支持将 {main_key} 作为快捷键主键。",
        }

    normalized_main_key = main_key
    modifier_set = set(modifier_parts)
    vk_code = HOTKEY_SPECIAL_VK_MAP.get(main_key, 0)

    if not vk_code:
        if len(main_key) != 1 or not main_key.isprintable() or main_key.isspace() or main_key == "+":
            return {
                "valid": False,
                "normalized": raw_hotkey,
                "mod_code": 0,
                "vk_code": 0,
                "error": "仅支持单个可打印字符、space、enter、esc 与 F1-F12。",
            }

        if main_key.isalpha():
            normalized_main_key = main_key.lower()

        scan_code = ctypes.windll.user32.VkKeyScanW(normalized_main_key)
        if scan_code == -1:
            return {
                "valid": False,
                "normalized": raw_hotkey,
                "mod_code": 0,
                "vk_code": 0,
                "error": f"系统无法识别字符热键：{main_key}",
            }

        vk_code = scan_code & 0xFF
        shift_state = (scan_code >> 8) & 0xFF
        if shift_state & 0x01:
            modifier_set.add("shift")
        if shift_state & 0x02:
            modifier_set.add("ctrl")
        if shift_state & 0x04:
            modifier_set.add("alt")

    normalized_modifiers = [mod for mod in HOTKEY_MODIFIER_ORDER if mod in modifier_set]
    mod_code = sum(HOTKEY_MODIFIER_FLAGS[mod] for mod in normalized_modifiers)
    normalized_hotkey = "+".join(normalized_modifiers + [normalized_main_key])

    return {
        "valid": True,
        "normalized": normalized_hotkey,
        "mod_code": mod_code,
        "vk_code": vk_code,
        "error": "",
    }


# --- 原生快捷键管理器 (Windows) ---
class NativeHotkeyManager(QObject):
    hotkey_triggered = Signal(int)
    hotkey_registration_failed = Signal(str)

    def __init__(self, hotkey_str):
        super().__init__()
        self.user32 = ctypes.windll.user32
        self.hotkey_id = 1
        self.hotkey_str = ""
        self.mod = 0
        self.vk = 0
        self._set_hotkey(hotkey_str)
        self._running = False
        self.thread = None
        
        # 字符串触发控制
        self.keyboard_listener = None
        self.string_trigger_enabled = False
        self.string_trigger_str = ""
        self.trigger_queue = None

    def _set_hotkey(self, hotkey_str):
        parsed = parse_hotkey_string(hotkey_str)
        self.hotkey_str = parsed["normalized"] or (hotkey_str or "").strip().lower()
        self.mod = parsed["mod_code"]
        self.vk = parsed["vk_code"]
        return parsed

    def validate_hotkey(self, hotkey_str):
        return parse_hotkey_string(hotkey_str)

    def _preflight_hotkey_registration(self, parsed_hotkey):
        if not parsed_hotkey["valid"]:
            return False, parsed_hotkey["error"]

        test_hotkey_id = self.hotkey_id + 1000
        success = bool(self.user32.RegisterHotKey(None, test_hotkey_id, parsed_hotkey["mod_code"], parsed_hotkey["vk_code"]))
        if success:
            self.user32.UnregisterHotKey(None, test_hotkey_id)
            return True, ""

        return False, f"系统级热键 {parsed_hotkey['normalized']} 注册失败，可能已被其他程序占用。"

    def _listen(self):
        registered = bool(self.user32.RegisterHotKey(None, self.hotkey_id, self.mod, self.vk))
        if not registered:
            self._running = False
            message = f"系统级热键 {self.hotkey_str or '未设置'} 注册失败，可能已被其他程序占用。"
            log(f"CRITICAL: {message}")
            self.hotkey_registration_failed.emit(message)
            return

        log(f"原生快捷键已注册 (ID: {self.hotkey_id}, 热键: {self.hotkey_str})")

        try:
            msg = wintypes.MSG()
            while self._running and self.user32.GetMessageA(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312: # WM_HOTKEY
                    if msg.wParam == self.hotkey_id:
                        self.hotkey_triggered.emit(self.hotkey_id)
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageA(ctypes.byref(msg))
        finally:
            if registered:
                self.user32.UnregisterHotKey(None, self.hotkey_id)
                log("原生快捷键已注销。")
            self._running = False

    def reregister(self, new_hotkey_str):
        """动态重新注册快捷键"""
        self.stop_hotkey()
        parsed = self._set_hotkey(new_hotkey_str)
        success, error_message = self._preflight_hotkey_registration(parsed)
        if not success:
            log(f"CRITICAL: {error_message}")
            return False, self.hotkey_str, error_message

        self.start_hotkey()
        return True, self.hotkey_str, ""
        
    def unregister_all(self):
        """只停用系统组合键，不影响字符串触发"""
        self.stop_hotkey()

    def start_hotkey(self):
        parsed = self._set_hotkey(self.hotkey_str)
        if not parsed["valid"]:
            message = parsed["error"] or "快捷键配置无效，无法启动监听。"
            log(f"CRITICAL: {message}")
            self.hotkey_registration_failed.emit(message)
            return False

        if not self._running:
            self._running = True
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("原生快捷键监听线程已启动。")
        return True
            
    def stop_hotkey(self):
        if self.thread:
            self._running = False
            try:
                if self.thread.is_alive() and self.thread.ident:
                    ctypes.windll.user32.PostThreadMessageA(self.thread.ident, 0x0012, 0, 0)
                    self.thread.join(timeout=1.5)
            except Exception as e:
                log(f"CRITICAL: 停止原生快捷键监听线程时出错: {e}")
            finally:
                self.thread = None
                log("原生快捷键监听服务已停止。")

    def start(self):
        return self.start_hotkey()

    def stop(self):
        """
        停止监听线程。
        【已加固】增加了对线程状态的检查和更安全的退出机制。
        """
        self.stop_hotkey()

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
