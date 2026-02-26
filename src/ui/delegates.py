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
                          QTimer, QEvent, QRect, QRectF, QProcess)
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

    def _create_text_document(self, text, option, block_data):
        from PySide6.QtGui import QTextDocument, QTextOption, QTextCursor, QTextCharFormat, QFont
        
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        
        # 应用自动换行设置
        text_option = QTextOption()
        if self.settings.word_wrap_enabled:
            text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        else:
            text_option.setWrapMode(QTextOption.NoWrap)
        doc.setDefaultTextOption(text_option)

        # 考虑到绘制时的 padding 
        padding_h = 8
        
        # 修复：获取 listwidget 的安全纯视口宽度，防止横行滚动条导致的内部画布无限扩张
        # 备用容错：如果 option.widget 未传入， fallback 回 option.rect.width()
        if option.widget:
             available_width = option.widget.viewport().width() - padding_h * 2
        else:
             available_width = option.rect.width() - padding_h * 2
        
        # 为来源标签预留一点右侧空间防止被挡住（如果单行且显示标签）
        if self.settings.show_source_enabled and not self.settings.word_wrap_enabled:
             available_width -= 80

        if available_width > 0:
            doc.setTextWidth(available_width)
            
        theme = self.themes[self.settings.theme]
        highlight_groups = block_data.get('highlight_groups', {}) if self.settings.highlight_matches else {}
        
        highlight_colors = [
            theme['highlight_color'],
            "#e5c07b", # 黄色
            "#c678dd", # 紫色
            "#98c379", # 绿色
            "#e06c75"  # 红色
        ]

        text = text.replace('\t', '    ')
        lines = text.split('\n')
        
        # 判断选中状态决定基础色
        is_selected = option.state & QStyle.State_Selected
        base_color = theme['item_selected_text'] if is_selected else theme['text_color']

        html_parts = []
        html_parts.append(f'<div style="color: {base_color}; white-space: pre-wrap;">')

        char_offset = 0
        for i, line in enumerate(lines):
            text_to_draw = line
            if i == 0 and line.startswith('- '):
                text_to_draw = line[2:].strip()
                char_offset += len(line) - len(text_to_draw)

            # --- 对每一行进行高亮处理 ---
            line_html = ""
            for char_idx, char in enumerate(text_to_draw):
                global_char_idx = char_offset + char_idx
                
                # 特殊HTML字符转义
                safe_char = char.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                
                highlight_group_idx = -1
                for group_idx, indices in highlight_groups.items():
                    if global_char_idx in indices:
                        highlight_group_idx = group_idx
                        break
                        
                if highlight_group_idx != -1:
                    color_idx = highlight_group_idx % len(highlight_colors)
                    color = highlight_colors[color_idx]
                    line_html += f'<span style="color: {color};">{safe_char}</span>'
                else:
                    if i > 0 and not is_selected:
                        # 子节点的颜色稍暗一点 (透明度不支持以这种形式写在内联style，用近似的暗色代替，此处简化为继承)
                        line_html += f'<span style="opacity: 0.7;">{safe_char}</span>'
                    else:
                        line_html += safe_char
            
            html_parts.append(line_html)
            if i < len(lines) - 1:
                html_parts.append('<br>')
                
            char_offset += len(line) + 1 # +1 for newline

        html_parts.append('</div>')
        doc.setHtml(''.join(html_parts))
        
        # 调整文档的边距，对应原先的 padding_v = 5
        doc.setDocumentMargin(0) 
        
        return doc


    def paint(self, painter, option, index):
        theme = self.themes[self.settings.theme]
        painter.save()
        rect = option.rect
        full_text = index.data(Qt.DisplayRole)
        block_data = index.data(Qt.UserRole)
        
        # 绘制背景
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(theme['item_selected_bg']))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(theme['item_hover_bg']))
        else:
            painter.fillRect(rect, QColor(theme['bg_color']))

        # 创建 QTextDocument 以支持换行和HTML高亮
        doc = self._create_text_document(full_text, option, block_data)
        
        padding_v = 5
        padding_h = 8
        painter.translate(rect.left() + padding_h, rect.top() + padding_v)
        
        # 截断超出范围的绘制 (如果是单行)
        clip_rect = QRectF(0, 0, rect.width() - padding_h*2, rect.height() - padding_v*2)
        doc.drawContents(painter, clip_rect)
        
        painter.translate(-(rect.left() + padding_h), -(rect.top() + padding_v))

        # --- 新增功能：绘制“归属显示”徽章 (Badge) ---
        if self.settings.show_source_enabled:
            source_path = block_data.get('source_path', '')
            if source_path:
                basename = os.path.basename(source_path)
                name_without_ext = os.path.splitext(basename)[0]
                
                # 确定徽章颜色
                if self.settings.theme == 'dark':
                    badge_bg = QColor(60, 60, 80, 180) # 深蓝偏灰半透明
                    badge_text_color = QColor("#abb2bf")
                else:
                    badge_bg = QColor(220, 220, 220, 180) # 浅灰半透明
                    badge_text_color = QColor("#5c6370")

                painter.save()
                painter.setRenderHint(QPainter.Antialiasing)
                
                badge_font = option.font
                # 处理字体大小计算，防止有些字体默认未设置PointSize返回-1导致的警告
                if badge_font.pointSizeF() > 0:
                    badge_font.setPointSizeF(badge_font.pointSizeF() * 0.8)
                elif badge_font.pixelSize() > 0:
                    badge_font.setPixelSize(int(badge_font.pixelSize() * 0.8))
                    
                painter.setFont(badge_font)
                fm = painter.fontMetrics()
                
                text_width = fm.horizontalAdvance(name_without_ext)
                text_height = fm.height()
                
                b_pad_h = 6
                b_pad_v = 2
                badge_width = text_width + b_pad_h * 2
                badge_height = text_height + b_pad_v * 2
                
                # 修复：确保徽章固定在可视区内 (viewport) 的右侧，而不是整个无限延展的 rect 右侧
                if option.widget:
                    visible_right = option.widget.viewport().width()
                else:
                    visible_right = rect.right()
                
                # 固定在区域右下角，留出边距
                badge_x = rect.left() + visible_right - badge_width - 8
                badge_y = rect.bottom() - badge_height - 3
                badge_rect = QRectF(badge_x, badge_y, badge_width, badge_height)
                
                painter.setPen(Qt.NoPen)
                painter.setBrush(badge_bg)
                painter.drawRoundedRect(badge_rect, 4, 4)
                
                painter.setPen(badge_text_color)
                painter.drawText(badge_rect, Qt.AlignCenter, name_without_ext)
                painter.restore()

        # 分隔线
        pen = painter.pen()
        pen.setColor(QColor(theme['border_color']))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()

    def sizeHint(self, option, index):
        full_text = index.data(Qt.DisplayRole)
        block_data = index.data(Qt.UserRole)
        
        # 为了精确计算高度，我们必须在 sizeHint 中就注入正确的可用宽度
        # 覆写 option 的 rect.width 为 viewport 的宽度，或者直接在 _create_text_document 内部判断
        
        doc = self._create_text_document(full_text, option, block_data)
        
        padding_v = 5
        # 返回文档计算出的实际高度加上上下边距
        height = int(doc.size().height()) + padding_v * 2 + 2 # +2 为分隔线等稍微留余量
        
        # 不让单个项目高度无限扩大，但也足够大以适应换行
        # 如果关闭了换行，doc 会按照一行的高度进行计算
        size = super().sizeHint(option, index)
        size.setHeight(height)
        return size
