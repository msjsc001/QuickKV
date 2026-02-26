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
class StyledItemDelegate(QStyledItemDelegate):
    def __init__(self, themes, settings):
        super().__init__()
        self.themes = themes
        self.settings = settings

    def paint(self, painter, option, index):
        theme = self.themes[self.settings.theme]
        painter.save()
        rect = option.rect
        full_text = index.data(Qt.DisplayRole)
        lines = full_text.split('\n')
        
        # --- 核心改动：读取分组高亮数据 ---
        block_data = index.data(Qt.UserRole)
        highlight_groups = block_data.get('highlight_groups', {}) if self.settings.highlight_matches else {}
        
        # --- 定义多种高亮颜色 ---
        highlight_colors = [
            QColor(theme['highlight_color']),
            QColor("#e5c07b"), # 黄色
            QColor("#c678dd"), # 紫色
            QColor("#98c379"), # 绿色
            QColor("#e06c75")  # 红色
        ]

        # 绘制背景
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(theme['item_selected_bg']))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(theme['item_hover_bg']))
        else:
            painter.fillRect(rect, QColor(theme['bg_color']))

        fm = option.fontMetrics
        line_height = fm.height()
        padding_v = 5
        padding_h = 8
        
        char_offset = 0
        for i, line in enumerate(lines):
            current_x = rect.x() + padding_h
            text_rect_y = rect.y() + padding_v + i * line_height
            
            line = line.replace('\t', '    ') # 将 Tab 替换为 4 个空格一样的显示

            text_to_draw = line
            if i == 0 and line.startswith('- '):
                text_to_draw = line[2:].strip()
                char_offset += len(line) - len(text_to_draw)

            for char_idx, char in enumerate(text_to_draw):
                global_char_idx = char_offset + char_idx
                char_width = fm.horizontalAdvance(char)
                char_rect = QRect(current_x, text_rect_y, char_width, line_height)

                # --- 判断当前字符属于哪个高亮组 ---
                highlight_group_idx = -1
                for group_idx, indices in highlight_groups.items():
                    if global_char_idx in indices:
                        highlight_group_idx = group_idx
                        break
                
                # 设置画笔颜色
                is_selected = option.state & QStyle.State_Selected
                if highlight_group_idx != -1:
                    # 使用模运算来循环选择高亮颜色
                    color_idx = highlight_group_idx % len(highlight_colors)
                    painter.setPen(highlight_colors[color_idx])
                elif is_selected:
                    painter.setPen(QColor(theme['item_selected_text']))
                else:
                    if i > 0:
                         child_color_base = QColor(theme['text_color'])
                         child_color_base.setAlpha(150)
                         painter.setPen(child_color_base)
                    else:
                         painter.setPen(QColor(theme['text_color']))

                painter.drawText(char_rect, Qt.AlignVCenter | Qt.AlignLeft, char)
                current_x += char_width
            
            char_offset += len(line) + 1

        painter.restore()
        
        # 分隔线（保持不变）
        painter.save()
        pen = painter.pen()
        pen.setColor(QColor(theme['border_color']))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()

    def sizeHint(self, option, index):
        full_text = index.data(Qt.DisplayRole)
        lines = full_text.split('\n')
        fm = option.fontMetrics
        line_height = fm.height()
        padding = 10
        
        height = len(lines) * line_height + padding
        
        size = super().sizeHint(option, index)
        size.setHeight(height)
        return size
