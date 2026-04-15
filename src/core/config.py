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


from utils.paths import get_base_path, resource_path

def get_device_id():
    """
    获取设备的唯一标识符，依次尝试:
    1. 主板 UUID (wmic)
    2. 系统盘卷序列号 (wmic)
    3. MAC地址
    """
    identifier = None
    try:
        # 1. 尝试主板 UUID
        uuid_output = subprocess.check_output("wmic csproduct get uuid", shell=True, text=True, stderr=subprocess.DEVNULL)
        uuid = uuid_output.strip().split('\n')[-1].strip()
        if uuid and "UUID" not in uuid and len(uuid) > 5:
            identifier = uuid
            log("使用主板 UUID 作为设备ID。")
    except Exception as e:
        log(f"获取主板 UUID 失败: {e}")

    if not identifier:
        try:
            # 2. 尝试系统盘卷序列号
            vol_serial_output = subprocess.check_output("wmic path win32_logicaldisk where \"DeviceID='%SystemDrive%'\" get VolumeSerialNumber", shell=True, text=True, stderr=subprocess.DEVNULL)
            vol_serial = vol_serial_output.strip().split('\n')[-1].strip()
            if vol_serial and "VolumeSerialNumber" not in vol_serial and len(vol_serial) > 2:
                identifier = vol_serial
                log("使用系统卷序列号作为设备ID。")
        except Exception as e:
            log(f"获取卷序列号失败: {e}")

    if not identifier:
        try:
            # 3. 尝试 MAC 地址
            # 需要导入 uuid 模块，请确保文件顶部有 import uuid
            mac = ':'.join(__import__('re').findall('..', '%012x' % __import__('uuid').getnode()))
            if mac and mac != "00:00:00:00:00:00":
                identifier = mac
                log("使用 MAC 地址作为设备ID。")
        except Exception as e:
            log(f"获取 MAC 地址失败: {e}")

    if not identifier:
        # 最终备用方案
        identifier = "generic_fallback_id_all_failed"
        log("所有设备ID获取方法均失败，使用最终备用ID。")

    return hashlib.sha256(identifier.encode()).hexdigest()

BASE_PATH = get_base_path()
USER_DATA_DIR = os.path.join(BASE_PATH, "用户数据")
AUTO_LOAD_DIR = os.path.join(BASE_PATH, "MD词库-需自动载入的请放入")

WORD_FILE = os.path.join(AUTO_LOAD_DIR, "词库.md")
CONFIG_FILE = os.path.join(USER_DATA_DIR, "config.ini")
CLIPBOARD_HISTORY_FILE = os.path.join(AUTO_LOAD_DIR, "剪贴板词库-勿删.md")
CACHE_FILE = os.path.join(USER_DATA_DIR, "cache.json")

ICON_PATH = resource_path("icon.png")

DEBUG_MODE = True
VERSION = "1.3.2" # 别名、文件夹词库、剪贴板与热键链闭环修复

def log(message):
    if DEBUG_MODE:
        print(f"[LOG] {message}")

def normalize_library_path(path):
    """统一词库路径格式，避免大小写和相对路径导致的重复判断失真。"""
    return os.path.normcase(os.path.abspath(path))

def get_internal_library_paths():
    """返回所有不应混入普通词库池的内部专用文件路径。"""
    return {
        normalize_library_path(CLIPBOARD_HISTORY_FILE),
    }

def is_internal_library_file(path):
    if not path:
        return False
    return normalize_library_path(path) in get_internal_library_paths()

def is_eligible_library_file(path):
    """判断一个文件是否可以作为普通词库文件参与加载。"""
    return (
        bool(path)
        and os.path.isfile(path)
        and path.lower().endswith(".md")
        and not is_internal_library_file(path)
    )

def list_eligible_md_files(folder_path):
    """列出目录首层可参与普通词库加载的 md 文件。"""
    if not folder_path or not os.path.isdir(folder_path):
        return []

    files = []
    try:
        for name in os.listdir(folder_path):
            full_path = os.path.join(folder_path, name)
            if is_eligible_library_file(full_path):
                files.append(os.path.abspath(full_path))
    except Exception as e:
        log(f"扫描目录 {folder_path} 时列出 md 文件失败: {e}")
        return []

    return sorted(files, key=lambda path: os.path.basename(path).lower())

# --- 主题颜色定义 ---
THEMES = {
    "dark": {
        "bg_color": "#21252b", "border_color": "#3c424b", "text_color": "#d1d5db",
        "title_color": "#8c929c",
        "input_bg_color": "#2c313a", "item_hover_bg": "#3a3f4b",
        "item_selected_bg": "#405061", "item_selected_text": "#d1d5db",
        "highlight_color": "#5294e2" # 新增：高亮颜色
    },
    "light": {
        "bg_color": "#fdfdfd", "border_color": "#cccccc", "text_color": "#202020",
        "title_color": "#a0a0a0",
        "input_bg_color": "#ffffff", "item_hover_bg": "#f0f0f0",
        "item_selected_bg": "#dbe4ee", "item_selected_text": "#202020",
        "highlight_color": "#007acc" # 新增：高亮颜色
    }
}

# --- 自定义列表项绘制代理 ---
