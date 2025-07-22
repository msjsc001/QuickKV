# -*- coding: utf-8 -*-
"""
QuickKV v1.0.5.9
"""
import sys
import os
import webbrowser
import configparser
import hashlib
import json
import threading
import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QSystemTrayIcon, QMenu, QSizeGrip,
                             QGraphicsDropShadowEffect, QPushButton,
                             QInputDialog, QMessageBox, QStyledItemDelegate, QStyle, QFileDialog,
                             QCheckBox, QWidgetAction)
from PySide6.QtCore import (Qt, Signal, Slot, QObject, QFileSystemWatcher,
                          QTimer, QEvent, QRect, QProcess)
from PySide6.QtGui import QIcon, QAction, QCursor, QPixmap, QPainter, QColor, QPalette
import pyperclip
from pypinyin import pinyin, Style

# --- 全局配置 ---
def get_base_path():
    """获取基础路径，用于定位外部文件（如config和词库）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.abspath(".")

def resource_path(relative_path):
    """获取内部资源的路径（如图标），这部分会被打包进exe"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- 外部数据文件 ---
BASE_PATH = get_base_path()
WORD_FILE = os.path.join(BASE_PATH, "词库.md")
CLIPBOARD_HISTORY_FILE = os.path.join(BASE_PATH, "剪贴板词库.md")
CONFIG_FILE = os.path.join(BASE_PATH, "config.ini")

# --- 内部资源 ---
ICON_PATH = resource_path("icon.png")

# --- 其他配置 ---
HOTKEY = "ctrl+space"
DEBUG_MODE = True
VERSION = "1.0.5.9" # 版本号

def log(message):
    if DEBUG_MODE:
        print(f"[LOG] {message}")

# --- 主题颜色定义 ---
THEMES = {
    "dark": {
        "bg_color": "#21252b", "border_color": "#3c424b", "text_color": "#d1d5db",
        "input_bg_color": "#2c313a", "item_hover_bg": "#3a3f4b",
        "item_selected_bg": "#09a2f1", "item_selected_text": "#ffffff"
    },
    "light": {
        "bg_color": "#fdfdfd", "border_color": "#cccccc", "text_color": "#202020",
        "input_bg_color": "#ffffff", "item_hover_bg": "#f0f0f0",
        "item_selected_bg": "#0078d7", "item_selected_text": "#ffffff"
    }
}

# --- 自定义列表项绘制代理 ---
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
        
        # 绘制背景
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(theme['item_selected_bg']))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(theme['item_hover_bg']))
        else:
            painter.fillRect(rect, QColor(theme['bg_color']))

        # 准备绘制文本
        fm = option.fontMetrics
        line_height = fm.height()
        padding_v = 5
        padding_h = 8
        
        for i, line in enumerate(lines):
            text_rect = QRect(rect.x() + padding_h, rect.y() + padding_v + i * line_height, rect.width() - (padding_h * 2), line_height)
            
            if i == 0:
                parent_text = line[2:].strip() if line.startswith('- ') else line
                if option.state & QStyle.State_Selected:
                    painter.setPen(QColor(theme['item_selected_text']))
                else:
                    painter.setPen(QColor(theme['text_color']))
                painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, parent_text)
            else:
                child_color_base = QColor(theme['item_selected_text']) if option.state & QStyle.State_Selected else QColor(theme['text_color'])
                child_color_base.setAlpha(150)
                painter.setPen(child_color_base)
                painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, line)

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

# --- 设置管理器 ---
class SettingsManager:
    def __init__(self, file_path):
        self.config = configparser.ConfigParser()
        self.file_path = file_path
        self.load()

    def load(self):
        self.config.read(self.file_path, encoding='utf-8')
        if not self.config.has_section('Window'): self.config.add_section('Window')
        if not self.config.has_section('Theme'): self.config.add_section('Theme')
        if not self.config.has_section('Font'): self.config.add_section('Font')
        if not self.config.has_section('Search'): self.config.add_section('Search')
        if not self.config.has_section('Data'): self.config.add_section('Data')
        if not self.config.has_section('General'): self.config.add_section('General')
        if not self.config.has_section('Clipboard'): self.config.add_section('Clipboard')
        if not self.config.has_section('Restart'): self.config.add_section('Restart')

        self.hotkeys_enabled = self.config.getboolean('General', 'hotkeys_enabled', fallback=True)
        self.width = self.config.getint('Window', 'width', fallback=450)
        self.height = self.config.getint('Window', 'height', fallback=300)
        self.theme = self.config.get('Theme', 'mode', fallback='dark')
        self.font_size = self.config.getint('Font', 'size', fallback=14)
        self.multi_word_search = self.config.getboolean('Search', 'multi_word_search', fallback=True)
        self.pinyin_initial_search = self.config.getboolean('Search', 'pinyin_initial_search', fallback=True)
        
        self.clipboard_memory_enabled = self.config.getboolean('Clipboard', 'enabled', fallback=False)
        self.clipboard_memory_count = self.config.getint('Clipboard', 'count', fallback=10)
        
        self.auto_restart_enabled = self.config.getboolean('Restart', 'enabled', fallback=False)
        self.auto_restart_interval = self.config.getint('Restart', 'interval_minutes', fallback=3)
        
        libraries_str = self.config.get('General', 'libraries', fallback='[]')
        try:
            self.libraries = json.loads(libraries_str)
        except json.JSONDecodeError:
            self.libraries = []
        
        if not self.libraries and os.path.exists(WORD_FILE):
            self.libraries.append({"path": os.path.abspath(WORD_FILE), "enabled": True})
            log("已将旧的单一词库配置迁移到新的多词库系统。")

        self.libraries = [lib for lib in self.libraries if os.path.exists(lib.get('path'))]

    def save(self):
        self.config['General']['hotkeys_enabled'] = str(self.hotkeys_enabled)
        self.config['Window']['width'] = str(self.width)
        self.config['Window']['height'] = str(self.height)
        self.config['Theme']['mode'] = self.theme
        self.config['Font']['size'] = str(self.font_size)
        self.config['Search']['multi_word_search'] = str(self.multi_word_search)
        self.config['Search']['pinyin_initial_search'] = str(self.pinyin_initial_search)
        self.config['General']['libraries'] = json.dumps(self.libraries, ensure_ascii=False)
        self.config['Clipboard']['enabled'] = str(self.clipboard_memory_enabled)
        self.config['Clipboard']['count'] = str(self.clipboard_memory_count)
        self.config['Restart']['enabled'] = str(self.auto_restart_enabled)
        self.config['Restart']['interval_minutes'] = str(self.auto_restart_interval)
        
        with open(self.file_path, 'w', encoding='utf-8') as configfile:
            self.config.write(configfile)
        log(f"配置已保存到 {self.file_path}")

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
                    exclude_parent_tag = '``不出现``'
                    should_exclude = exclude_parent_tag in parent_text
                    if should_exclude:
                        parent_text = parent_text.replace(exclude_parent_tag, '').strip()

                    current_block = {
                        'parent': parent_text,
                        'raw_lines': [line.rstrip()],
                        'exclude_parent': should_exclude,
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
            with open(self.file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            updated_content = file_content.replace(original_content, new_content)
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            return True
        except Exception as e:
            log(f"更新 {self.file_path} 时发生错误: {e}")
            return False

    def delete_entry(self, content_to_delete):
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            lines_to_delete_str = content_to_delete.split('\n')
            
            found_at = -1
            for i in range(len(lines) - len(lines_to_delete_str) + 1):
                match = True
                for j in range(len(lines_to_delete_str)):
                    if lines[i+j].rstrip() != lines_to_delete_str[j]:
                        match = False
                        break
                if match:
                    found_at = i
                    break
            
            if found_at != -1:
                del lines[found_at : found_at + len(lines_to_delete_str)]
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                return True
            return False
        except Exception as e:
            log(f"删除 {self.file_path} 的词条时发生错误: {e}")
            return False

# --- 词库管理器 ---
class WordManager:
    def __init__(self, settings):
        self.settings = settings
        self.sources = []
        self.word_blocks = []
        # 新增：剪贴板历史专用
        self.clipboard_source = None
        self.clipboard_history = []
        self.reload_all()

    def _get_pinyin_sort_key(self, text):
        return "".join(item[0] for item in pinyin(text, style=Style.NORMAL))

    def _get_pinyin_initials(self, text):
        return "".join(item[0] for item in pinyin(text, style=Style.FIRST_LETTER))

    def reload_all(self):
        """重新加载所有词库，包括剪贴板历史"""
        # 加载普通词库
        self.sources = [WordSource(lib['path']) for lib in self.settings.libraries]
        self.aggregate_words()

        # 加载剪贴板历史
        self.load_clipboard_history()

    def load_clipboard_history(self):
        """加载剪贴板历史文件"""
        if not os.path.exists(CLIPBOARD_HISTORY_FILE):
            try:
                with open(CLIPBOARD_HISTORY_FILE, 'w', encoding='utf-8') as f:
                    f.write("- (这里是剪贴板历史记录)\n")
                log(f"已创建剪贴板历史文件: {CLIPBOARD_HISTORY_FILE}")
            except Exception as e:
                log(f"创建剪贴板历史文件失败: {e}")
                return

        self.clipboard_source = WordSource(CLIPBOARD_HISTORY_FILE)
        # 剪贴板历史按添加顺序（文件中的倒序）显示，所以我们直接逆序
        raw_history = list(reversed(self.clipboard_source.word_blocks))
        self.clipboard_history = []
        for block in raw_history:
            block['is_clipboard'] = True # 添加标志
            self.clipboard_history.append(block)
        log(f"已加载 {len(self.clipboard_history)} 条剪贴板历史。")


    def add_to_clipboard_history(self, text):
        """向剪贴板历史中添加新条目"""
        if not self.clipboard_source:
            log("剪贴板源未初始化，无法添加历史。")
            return False

        # 避免重复添加
        if any(block['parent'] == text for block in self.clipboard_history):
            log(f"剪贴板历史中已存在: '{text}'")
            return False

        # 限制历史数量
        while len(self.clipboard_history) >= self.settings.clipboard_memory_count:
            oldest_item = self.clipboard_history.pop(0) # 移除最旧的
            self.clipboard_source.delete_entry(oldest_item['full_content'])
            log(f"剪贴板历史已满，移除最旧条目: {oldest_item['parent']}")

        # 添加新条目
        content_to_add = f"- {text}"
        if self.clipboard_source.add_entry(content_to_add):
            log(f"已添加新剪贴板历史: '{text}'")
            # 重新加载以更新内部状态，并返回成功状态
            self.load_clipboard_history()
            return True
        return False

    def clear_clipboard_history(self):
        """清空剪贴板历史"""
        if not self.clipboard_source: return
        try:
            # 删除文件内容，保留一个标题行
            with open(self.clipboard_source.file_path, 'w', encoding='utf-8') as f:
                f.write("- (剪贴板历史已清空)\n")
            self.load_clipboard_history() # 重新加载
            log("剪贴板历史已清空。")
            return True
        except Exception as e:
            log(f"清空剪贴板历史失败: {e}")
            return False

    def aggregate_words(self):
        """聚合所有启用的词库数据"""
        self.word_blocks = []
        enabled_paths = {lib['path'] for lib in self.settings.libraries if lib['enabled']}
        for source in self.sources:
            if source.file_path in enabled_paths:
                self.word_blocks.extend(source.word_blocks)
        
        self.word_blocks.sort(key=lambda block: self._get_pinyin_sort_key(block['parent']))
        log(f"已聚合 {len(self.word_blocks)} 个词条从 {len(enabled_paths)} 个启用的词库。")

    def find_matches(self, query, multi_word_search_enabled=False, pinyin_search_enabled=False):
        # 根据需求调整：如果无输入且剪贴板开启，则只显示剪贴板
        if not query and self.settings.clipboard_memory_enabled:
            return self.clipboard_history

        # 1. 准备搜索池
        search_pool = []
        if self.settings.clipboard_memory_enabled:
            search_pool.extend(self.clipboard_history)
        search_pool.extend(self.word_blocks)

        # 2. 如果没有查询（但剪贴板关闭），返回词库
        if not query:
            return self.word_blocks

        query_lower = query.lower()
        matched_blocks = []
        target_blocks = search_pool # 使用合并后的池进行搜索

        if multi_word_search_enabled and ' ' in query_lower.strip():
            keywords = [k for k in query_lower.split(' ') if k]
            if not keywords:
                matched_blocks = [block for block in target_blocks if query_lower in block['parent'].lower()]
            else:
                if pinyin_search_enabled:
                    matched_blocks = [
                        block for block in target_blocks
                        if all(
                            (keyword in block['parent'].lower() or keyword in self._get_pinyin_initials(block['parent']))
                            for keyword in keywords
                        )
                    ]
                else:
                    matched_blocks = [
                        block for block in target_blocks
                        if all(keyword in block['parent'].lower() for keyword in keywords)
                    ]
        else:
            if pinyin_search_enabled:
                matched_blocks = [
                    block for block in target_blocks
                    if query_lower in block['parent'].lower() or query_lower in self._get_pinyin_initials(block['parent'])
                ]
            else:
                matched_blocks = [block for block in target_blocks if query_lower in block['parent'].lower()]
        
        # 搜索结果不需要再排序，以保持剪贴板历史在前的顺序，并让词库结果保持其原有拼音顺序
        return matched_blocks

    def get_source_by_path(self, path):
        for source in self.sources:
            if source.file_path == path:
                return source
        return None


# --- 编辑对话框 ---
from PySide6.QtWidgets import QDialog, QTextEdit, QDialogButtonBox

class EditDialog(QDialog):
    def __init__(self, parent=None, current_text="", theme=None, font_size=14):
        super().__init__(parent)
        self.setWindowTitle("编辑词条")
        self.setLayout(QVBoxLayout())
        
        self.text_edit = QTextEdit(self)
        self.text_edit.setPlainText(current_text)
        self.layout().addWidget(self.text_edit)
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout().addWidget(self.button_box)
        
        self.resize(400, 300)
        if theme:
            self.apply_theme(theme, font_size)

    def apply_theme(self, theme, font_size):
        self.setStyleSheet(f"background-color: {theme['bg_color']}; color: {theme['text_color']};")
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 4px;
                padding: 8px;
                font-size: {font_size}px;
            }}
        """)
        # 简单按钮样式
        btn_style = f"""
            QPushButton {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                padding: 5px 15px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {theme['item_hover_bg']};
            }}
            QPushButton:pressed {{
                background-color: {theme['item_selected_bg']};
                color: {theme['item_selected_text']};
            }}
        """
        for button in self.button_box.buttons():
            button.setStyleSheet(btn_style)

    def get_text(self):
        return self.text_edit.toPlainText()


# --- 搜索弹出窗口UI (滚动条修复) ---
class SearchPopup(QWidget):
    suggestion_selected = Signal(str)

    def __init__(self, word_manager, settings_manager):
        super().__init__()
        self.word_manager = word_manager
        self.settings = settings_manager
        self.controller = None # 用于存储 MainController 的引用
        self.drag_position = None
        self.resizing = False
        self.resize_margin = 8
        self.resize_edge = {"top": False, "bottom": False, "left": False, "right": False}
        self.resize_start_pos = None
        self.resize_start_geom = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        # self.setAttribute(Qt.WA_TranslucentBackground) # 重新禁用透明属性
        self.setMouseTracking(True) # 启用鼠标跟踪以更新光标
        self.container = QWidget(self) # 将 container 直接作为子控件
        self.container.setMouseTracking(True)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0) # 移除边距，让 container 填满窗口
        main_layout.addWidget(self.container)
        # shadow = QGraphicsDropShadowEffect(self); shadow.setBlurRadius(15); shadow.setColor(QColor(0, 0, 0, 80)); shadow.setOffset(0, 2)
        # self.container.setGraphicsEffect(shadow) # 移除阴影
        
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(1, 1, 1, 1) # 恢复紧凑的边距
        container_layout.setSpacing(4)
        
        title_bar_layout = QHBoxLayout()
        title_bar_layout.setContentsMargins(8, 4, 4, 0)
        title_bar_layout.addStretch()
        
        self.pin_button = QPushButton("📌") # 图钉按钮
        self.pin_button.setFixedSize(24, 24)
        self.pin_button.setCheckable(True) # 使按钮可切换状态
        self.pin_button.clicked.connect(self.toggle_pin)
        title_bar_layout.addWidget(self.pin_button)

        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(24, 24)
        self.close_button.clicked.connect(self.hide)
        title_bar_layout.addWidget(self.close_button)

        self.pinned = False # 初始化图钉状态
        
        self.search_box = QLineEdit(placeholderText="搜索...")
        self.list_widget = QListWidget(); self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # self.list_widget.setAutoFillBackground(True) # 移除此行，因为它无效
        self.search_box.setMouseTracking(True)
        self.list_widget.setMouseTracking(True)

        container_layout.addLayout(title_bar_layout)
        container_layout.addWidget(self.search_box)
        container_layout.addWidget(self.list_widget, 1)

        # 添加 QSizeGrip 用于右下角缩放
        self.size_grip = QSizeGrip(self)
        container_layout.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        
        # 设置自定义的绘图代理来完全控制项目渲染
        self.delegate = StyledItemDelegate(THEMES, self.settings)
        self.list_widget.setItemDelegate(self.delegate)
        
        self.apply_theme()
        self.resize(self.settings.width, self.settings.height)
        self.setMinimumSize(250, 150) # 设置一个合理的最小尺寸
        self.search_box.textChanged.connect(self.update_list)
        self.list_widget.itemClicked.connect(self.on_item_selected)
        self.list_widget.itemActivated.connect(self.on_item_selected)
        # 【终极修复】连接信号，在选中项改变时强制刷新整个列表，杜绝一切渲染残留
        self.list_widget.currentItemChanged.connect(self.force_list_update)

        # 启用上下文菜单
        self.search_box.setContextMenuPolicy(Qt.CustomContextMenu)
        self.search_box.customContextMenuRequested.connect(self.show_search_box_context_menu)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.show_list_widget_context_menu)

    @Slot()
    def force_list_update(self):
        """强制列表视口刷新"""
        self.list_widget.viewport().update()

    def _update_resize_cursor(self, pos):
        m = self.resize_margin
        rect = self.rect()
        
        on_top = abs(pos.y()) < m
        on_bottom = abs(pos.y() - rect.height()) < m
        on_left = abs(pos.x()) < m
        on_right = abs(pos.x() - rect.width()) < m

        self.resize_edge["top"] = on_top
        self.resize_edge["bottom"] = on_bottom
        self.resize_edge["left"] = on_left
        self.resize_edge["right"] = on_right

        if (on_top and on_left) or (on_bottom and on_right):
            self.setCursor(Qt.SizeFDiagCursor)
        elif (on_top and on_right) or (on_bottom and on_left):
            self.setCursor(Qt.SizeBDiagCursor)
        elif on_top or on_bottom:
            self.setCursor(Qt.SizeVerCursor)
        elif on_left or on_right:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            global_pos = event.globalPosition().toPoint()
            log(f"mousePressEvent: pos={pos}, global_pos={global_pos}")
            self._update_resize_cursor(pos) # 在按下时就判断是否在边缘

            if any(self.resize_edge.values()):
                self.resizing = True
                self.resize_start_pos = global_pos
                self.resize_start_geom = self.geometry()
                log(f"开始缩放: resize_edge={self.resize_edge}")
            elif pos.y() < 35:
                self.drag_position = global_pos - self.frameGeometry().topLeft()
                log(f"开始拖动: drag_position={self.drag_position}")
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()
        # log(f"mouseMoveEvent: pos={pos}, global_pos={global_pos}, resizing={self.resizing}, drag_position={self.drag_position}")

        if self.resizing:
            delta = global_pos - self.resize_start_pos
            geom = self.resize_start_geom
            new_geom = QRect(geom)

            if self.resize_edge["top"]: new_geom.setTop(geom.top() + delta.y())
            if self.resize_edge["bottom"]: new_geom.setBottom(geom.bottom() + delta.y())
            if self.resize_edge["left"]: new_geom.setLeft(geom.left() + delta.x())
            if self.resize_edge["right"]: new_geom.setRight(geom.right() + delta.x())
            
            # 确保尺寸不会小于最小值
            if new_geom.width() < self.minimumWidth():
                if self.resize_edge["left"]: new_geom.setLeft(geom.right() - self.minimumWidth())
                else: new_geom.setWidth(self.minimumWidth())

            if new_geom.height() < self.minimumHeight():
                if self.resize_edge["top"]: new_geom.setTop(geom.bottom() - self.minimumHeight())
                else: new_geom.setHeight(self.minimumHeight())

            self.setGeometry(new_geom)
            # log(f"缩放中: new_geom={new_geom}")

        elif event.buttons() & Qt.LeftButton and self.drag_position is not None:
            self.move(global_pos - self.drag_position)
            # log(f"拖动中: new_pos={global_pos - self.drag_position}")
        else:
            self._update_resize_cursor(pos)
        
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # 重置所有状态
        self.resizing = False
        self.drag_position = None
        self.resize_start_pos = None
        self.resize_start_geom = None
        for k in self.resize_edge: self.resize_edge[k] = False
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def toggle_pin(self):
        self.pinned = not self.pinned
        log(f"窗口图钉状态: {'已固定' if self.pinned else '未固定'}")
        self._update_pin_button_style()

    def _update_pin_button_style(self):
        theme = THEMES[self.settings.theme]
        if self.pinned:
            self.pin_button.setStyleSheet(f"QPushButton {{ background-color: {theme['item_selected_bg']}; color: {theme['item_selected_text']}; border: none; font-size: 16px; font-weight: bold; border-radius: 4px; }} QPushButton:hover {{ background-color: {theme['item_selected_bg']}; color: {theme['item_selected_text']}; }}")
        else:
            self.pin_button.setStyleSheet(f"QPushButton {{ background-color: transparent; color: {theme['text_color']}; border: none; font-size: 16px; font-weight: bold; }} QPushButton:hover {{ background-color: {theme['item_hover_bg']}; border-radius: 4px; }}")
        self.pin_button.update() # 【修复】强制按钮刷新，消除切换主题后的残留


    def showEvent(self, event):
        super().showEvent(event)

    def hideEvent(self, event):
        self.settings.width = self.width(); self.settings.height = self.height(); self.settings.save(); super().hideEvent(event)

    def apply_theme(self):
        theme = THEMES[self.settings.theme]
        font_size = self.settings.font_size
        self.container.setStyleSheet(f"background-color: {theme['bg_color']}; border: 1px solid {theme['border_color']}; border-radius: 8px;")
        self.search_box.setStyleSheet(f"background-color: {theme['input_bg_color']}; color: {theme['text_color']}; border: 1px solid {theme['border_color']}; border-radius: 4px; padding: 8px; font-size: {font_size}px; margin: 0px 8px 4px 8px;")
        # 绘图代理接管了 item 的样式，这里只需设置基础样式
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: {theme['bg_color']}; /* 【最终修复】确保列表自身有坚实的背景色 */
                color: {theme['text_color']};
                border: none;
                font-size: {font_size}px;
            }}
        """)
        # 设置 QSizeGrip 的样式，使其背景色与窗口背景色一致
        self.size_grip.setStyleSheet(f"""
            QSizeGrip {{
                background-color: transparent; /* 设置为完全透明 */
                border: none;
                padding: 8px; /* 增加内边距，使其向内移动 */
                margin: -8px; /* 负外边距抵消部分 padding，使其不占用额外空间 */
            }}
        """)
        self.close_button.setStyleSheet(f"QPushButton {{ background-color: transparent; color: {theme['text_color']}; border: none; font-size: 16px; font-weight: bold; }} QPushButton:hover {{ color: white; background-color: #E81123; border-radius: 4px; }}")
        self._update_pin_button_style() # 应用主题时更新图钉按钮样式
        self.list_widget.viewport().update() # 强制列表刷新以应用新主题

    def show_and_focus(self):
        log("显示并聚焦搜索窗口。")
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        screen_geom = screen.availableGeometry(); window_size = self.size()
        pos_x = cursor_pos.x() + 15; pos_y = cursor_pos.y() + 15
        # if pos_y + window_size.height() > screen_geom.y() + screen_geom.height():
        #     log("下方空间不足，窗口向上翻转。"); pos_y = cursor_pos.y() - window_size.height() - 15
        if pos_x + window_size.width() > screen_geom.x() + screen_geom.width():
            pos_x = screen_geom.x() + screen_geom.width() - window_size.width()
        self.move(pos_x, pos_y)
        self.reappear_in_place()

    def reappear_in_place(self):
        """在原位重新显示窗口，不移动位置"""
        self.show()
        self.activateWindow()
        self.search_box.setFocus()
        self.search_box.clear()
        self.update_list("")
        self.list_widget.viewport().update() # 【修复】确保窗口出现时列表被完全重绘
    
    @Slot(str)
    def update_list(self, text):
        self.list_widget.clear()
        matched_blocks = self.word_manager.find_matches(
            text, self.settings.multi_word_search, self.settings.pinyin_initial_search
        )
        
        for block in matched_blocks:
            item = QListWidgetItem(block['full_content'])
            item.setData(Qt.UserRole, block) # 存储完整数据块
            self.list_widget.addItem(item)
            
        if self.list_widget.count() > 0: self.list_widget.setCurrentRow(0)
    
    @Slot("QListWidgetItem")
    def on_item_selected(self, item):
        self.suggestion_selected.emit(item.text())
        self.hide() # 无论是否钉住，都先隐藏以释放焦点

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            if self.pinned: # 如果已固定，按ESC只清空输入框，不隐藏
                self.search_box.clear()
                self.update_list("")
            else:
                self.hide()
        elif key in [Qt.Key_Return, Qt.Key_Enter] and self.search_box.hasFocus():
             if self.list_widget.currentItem(): self.on_item_selected(self.list_widget.currentItem())
        elif key == Qt.Key_Down and self.search_box.hasFocus() and self.list_widget.count() > 0: self.list_widget.setFocus()
        elif key == Qt.Key_Up and self.list_widget.hasFocus() and self.list_widget.currentRow() == 0: self.search_box.setFocus()
        else: super().keyPressEvent(event)

    def show_search_box_context_menu(self, pos):
        menu = QMenu(self)
        
        # 创建“添加到词库”子菜单
        add_to_library_menu = QMenu("添加到词库", self)
        
        # 获取所有已加载的词库
        libraries = self.settings.libraries
        if not libraries:
            # 如果没有词库，则禁用此菜单项
            no_library_action = QAction("无可用词库", self)
            no_library_action.setEnabled(False)
            add_to_library_menu.addAction(no_library_action)
        else:
            for lib in libraries:
                lib_path = lib['path']
                lib_name = os.path.basename(lib_path)
                action = QAction(lib_name, self)
                action.triggered.connect(lambda _, p=lib_path: self.add_from_search_box_to_specific_library(p))
                add_to_library_menu.addAction(action)
        
        menu.addMenu(add_to_library_menu)
        
        # 应用主题
        self.controller.apply_menu_theme(menu)

        menu.exec(self.search_box.mapToGlobal(pos))

    def add_from_search_box_to_specific_library(self, target_path):
        text = self.search_box.text()
        if text:
            self.controller.add_entry(text, target_path)

    def show_list_widget_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item: return
        
        selected_block = item.data(Qt.UserRole)
        if not selected_block: return

        menu = QMenu(self)
        
        if selected_block.get('is_clipboard', False):
            # 剪贴板历史的右键菜单
            add_to_library_menu = QMenu("添加到词库", self)
            libraries = self.settings.libraries
            if not libraries:
                no_library_action = QAction("无可用词库", self)
                no_library_action.setEnabled(False)
                add_to_library_menu.addAction(no_library_action)
            else:
                for lib in libraries:
                    lib_path = lib['path']
                    lib_name = os.path.basename(lib_path)
                    action = QAction(lib_name, self)
                    action.triggered.connect(lambda _, p=lib_path, i=item: self.add_clipboard_item_to_specific_library(i, p))
                    add_to_library_menu.addAction(action)
            menu.addMenu(add_to_library_menu)

            edit_action = QAction("编辑", self)
            edit_action.triggered.connect(lambda: self.edit_item(item))
            menu.addAction(edit_action)

            delete_action = QAction("删除", self)
            delete_action.triggered.connect(lambda: self.delete_item(item))
            menu.addAction(delete_action)
        else:
            # 普通词库的右键菜单
            edit_action = QAction("编辑", self)
            edit_action.triggered.connect(lambda: self.edit_item(item))
            menu.addAction(edit_action)

            delete_action = QAction("删除", self)
            delete_action.triggered.connect(lambda: self.delete_item(item))
            menu.addAction(delete_action)
        
        # 应用主题
        self.controller.apply_menu_theme(menu)
             
        menu.exec(self.list_widget.mapToGlobal(pos))

    def add_from_search_box(self):
        text = self.search_box.text()
        if text:
            self.controller.add_entry(text)

    def edit_item(self, item):
        self.controller.edit_entry(item.text())

    def delete_item(self, item):
        self.controller.delete_entry(item.text())

    def add_clipboard_item_to_library(self, item):
        text = item.text().replace('- ', '', 1).strip()
        self.controller.add_entry(text)

    def add_clipboard_item_to_specific_library(self, item, target_path):
        text = item.text().replace('- ', '', 1).strip()
        self.controller.add_entry(text, target_path)

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

    def start(self):
        if not self._running:
            self._running = True
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("原生快捷键监听线程已启动。")

    def stop(self):
        if self._running:
            self._running = False
            # 发送一个空消息来唤醒 GetMessageA 循环，使其能检查 _running 标志
            # 需要获取线程ID来发送消息
            ctypes.windll.user32.PostThreadMessageA(self.thread.ident, 0x0012, 0, 0) # WM_QUIT
            self.thread.join(timeout=1) # 等待线程结束
            log("原生快捷键监听线程已停止。")

# --- 主控制器 ---
class MainController(QObject):
    show_popup_signal = Signal()
    hide_popup_signal = Signal()

    def __init__(self, app, word_manager, settings_manager):
        super().__init__(); self.app = app; self.word_manager = word_manager; self.settings = settings_manager; self.menu = None
        self.popup = SearchPopup(self.word_manager, self.settings)
        self.popup.controller = self # 将 controller 实例传递给 popup
        self.show_popup_signal.connect(self.popup.show_and_focus)
        self.hide_popup_signal.connect(self.popup.hide)
        self.popup.suggestion_selected.connect(self.on_suggestion_selected)
        
        self.hotkey_manager = NativeHotkeyManager(HOTKEY)
        self.hotkey_manager.hotkey_triggered.connect(self.on_hotkey_triggered)
        if self.settings.hotkeys_enabled:
            self.hotkey_manager.start()

        self.file_watcher = QFileSystemWatcher(self)
        self.update_file_watcher()
        self.file_watcher.fileChanged.connect(self.schedule_reload)
        self.reload_timer = QTimer(self); self.reload_timer.setSingleShot(True); self.reload_timer.setInterval(300); self.reload_timer.timeout.connect(self.reload_word_file)

        # 新增：初始化自动重启定时器
        self.auto_restart_timer = QTimer(self)
        self.auto_restart_timer.timeout.connect(self.perform_restart)
        self.update_auto_restart_timer()

    def update_clipboard_monitor_status(self):
        """根据设置启动或停止剪贴板监控"""
        if self.settings.clipboard_memory_enabled:
            self.last_clipboard_text = pyperclip.paste() # 初始化时获取一次
            self.clipboard_timer.start()
            log("剪贴板记忆功能已启动。")
        else:
            self.clipboard_timer.stop()
            log("剪贴板记忆功能已关闭。")

    @Slot()
    def check_clipboard(self):
        """检查剪贴板内容变化"""
        try:
            current_text = pyperclip.paste()
            if current_text and current_text != self.last_clipboard_text:
                log(f"检测到新的剪贴板内容: '{current_text}'")
                self.last_clipboard_text = current_text
                was_added = self.word_manager.add_to_clipboard_history(current_text)
                # 如果添加成功且窗口可见，则刷新
                if was_added and self.popup.isVisible():
                    self.popup.update_list(self.popup.search_box.text())
        except pyperclip.PyperclipException as e:
            # 可能是复制了非文本内容（如文件），忽略错误
            # log(f"无法获取剪贴板文本内容: {e}")
            pass

    def on_hotkey_triggered(self):
        # 这个信号现在是从 NativeHotkeyManager 线程发出的
        if not self.settings.hotkeys_enabled: return
        if self.popup.isVisible():
            log("热键触发：关闭窗口。"); self.hide_popup_signal.emit()
        else:
            log("热键触发：打开窗口。"); self.show_popup_signal.emit()

    def update_file_watcher(self):
        """更新文件监控器以包含所有词库文件"""
        paths = [lib['path'] for lib in self.settings.libraries]
        if self.file_watcher.files() != paths:
            self.file_watcher.removePaths(self.file_watcher.files())
            self.file_watcher.addPaths(paths)
            log(f"文件监控器已更新，正在监控: {paths}")

    @Slot()
    def schedule_reload(self):
        log("检测到文件变化，安排重载...");
        self.reload_timer.start()
    @Slot()
    def reload_word_file(self):
        log("执行所有词库重载。"); self.word_manager.reload_all()
        if self.popup.isVisible(): self.popup.update_list(self.popup.search_box.text())
    @Slot(str)
    def on_suggestion_selected(self, text):
        log(f"已选择词条块: '{text}'")
        
        content_to_paste = "" # 初始化为空
        
        # text 是 full_content，我们需要通过它找到原始块
        found_block = None
        all_blocks = self.word_manager.clipboard_history + self.word_manager.word_blocks
        for block in all_blocks:
            if block['full_content'] == text:
                found_block = block
                break
        
        # 剪贴板内容也可能是选择的目标
        if not found_block:
             for block in self.word_manager.clipboard_history:
                if block['full_content'] == text:
                    found_block = block
                    break

        if found_block:
            if found_block['exclude_parent']:
                # 只输出子内容
                content_to_paste = '\n'.join(found_block['raw_lines'][1:])
            else:
                # 输出父级（移除- ）+ 子内容
                first_line = found_block['raw_lines'][0].replace('- ', '', 1)
                content_to_paste = '\n'.join([first_line] + found_block['raw_lines'][1:])
        else:
            # 如果找不到块，作为备用方案，按旧方式处理
            content_to_paste = text.replace('- ', '', 1)

        pyperclip.copy(content_to_paste)
        log(f"已复制处理后的内容到剪贴板。")
        
        # 延迟执行粘贴，确保焦点已切换
        QTimer.singleShot(300, self.perform_paste)

    def perform_paste(self):
        """通过 PowerShell 调用 .NET SendKeys 执行粘贴，这是最强力的模拟方式"""
        log("准备通过 PowerShell 执行粘贴...")

        # 构造 PowerShell 命令
        # Start-Sleep -Milliseconds 100: 等待100毫秒，确保焦点已切换
        # Add-Type -AssemblyName System.Windows.Forms: 加载 .NET 的 Forms 库
        # [System.Windows.Forms.SendKeys]::SendWait('^v'): 发送 Ctrl+V 并等待其处理
        ps_command = (
            "powershell.exe -WindowStyle Hidden -Command "
            "\"Start-Sleep -Milliseconds 100; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "[System.Windows.Forms.SendKeys]::SendWait('^v')\""
        )

        try:
            # 使用 QProcess.startDetached 在后台静默执行 PowerShell 命令
            QProcess.startDetached(ps_command)
            log("PowerShell 粘贴命令已成功派发。")
        except Exception as e:
            log(f"启动 PowerShell 粘贴进程时发生错误: {e}")

        # 如果窗口是固定的，则在粘贴后重新显示它
        if self.popup.pinned:
            log("图钉已启用，重新显示窗口。")
            # 给予 PowerShell 充足的执行时间
            QTimer.singleShot(200, self.popup.reappear_in_place)
    @Slot(str, str)
    def add_entry(self, text, target_path=None):
        # 如果没有指定目标词库，则弹出选择框
        if target_path is None:
            if len(self.settings.libraries) > 1:
                lib_names = [os.path.basename(lib['path']) for lib in self.settings.libraries]
                lib_name, ok = QInputDialog.getItem(self.popup, "选择词库", "请选择要添加到的词库:", lib_names, 0, False)
                if ok and lib_name:
                    target_path = next((lib['path'] for lib in self.settings.libraries if os.path.basename(lib['path']) == lib_name), None)
                else:
                    return # 用户取消
            elif len(self.settings.libraries) == 1:
                target_path = self.settings.libraries[0]['path']
            else:
                QMessageBox.warning(self.popup, "错误", "没有可用的词库。请先添加一个。")
                return

        source = self.word_manager.get_source_by_path(target_path)
        if source:
            content = f"- {text}"
            if source.add_entry(content):
                self.reload_word_file()
                self.popup.search_box.clear()
            else:
                QMessageBox.warning(self.popup, "错误", f"向 {os.path.basename(target_path)} 添加词条失败！")
    
    @Slot(str)
    def edit_entry(self, original_content):
        source_path = None
        
        # Find the block to get its properties
        found_block = None
        all_blocks = self.word_manager.clipboard_history + self.word_manager.word_blocks
        for block in all_blocks:
            if block['full_content'] == original_content:
                found_block = block
                break
        
        if not found_block:
            QMessageBox.warning(self.popup, "错误", "找不到词条。")
            return

        is_clipboard = found_block.get('is_clipboard', False)

        if is_clipboard:
            source_path = self.word_manager.clipboard_source.file_path
        else:
            source_path = found_block.get('source_path')
        
        if not source_path:
            QMessageBox.warning(self.popup, "错误", "找不到词条的来源文件。")
            return

        source = self.word_manager.get_source_by_path(source_path) or self.word_manager.clipboard_source
        if not source:
            QMessageBox.warning(self.popup, "错误", "来源文件对象已丢失。")
            return

        dialog = EditDialog(self.popup, original_content, THEMES[self.settings.theme], self.settings.font_size)
        if dialog.exec():
            new_content = dialog.get_text()
            if source.update_entry(original_content, new_content):
                if is_clipboard:
                    self.word_manager.load_clipboard_history()
                    if self.popup.isVisible(): self.popup.update_list("")
                else:
                    self.reload_word_file()
            else:
                QMessageBox.warning(self.popup, "错误", f"更新 {os.path.basename(source_path)} 中的词条失败！")

    @Slot(str)
    def delete_entry(self, content):
        source_path = None
        
        # Find the block to get its properties
        found_block = None
        all_blocks = self.word_manager.clipboard_history + self.word_manager.word_blocks
        for block in all_blocks:
            if block['full_content'] == content:
                found_block = block
                break

        if not found_block:
            QMessageBox.warning(self.popup, "错误", "找不到词条。")
            return
            
        is_clipboard = found_block.get('is_clipboard', False)

        if is_clipboard:
            source_path = self.word_manager.clipboard_source.file_path
        else:
            source_path = found_block.get('source_path')

        if not source_path:
            QMessageBox.warning(self.popup, "错误", "找不到词条的来源文件。")
            return
            
        source = self.word_manager.get_source_by_path(source_path) or self.word_manager.clipboard_source
        if not source:
            QMessageBox.warning(self.popup, "错误", "来源文件对象已丢失。")
            return

        reply = QMessageBox.question(self.popup, "确认删除",
                                     f"确定要从 {os.path.basename(source_path)} 中删除以下词条吗？\n\n{content}",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            if source.delete_entry(content):
                if is_clipboard:
                    self.word_manager.load_clipboard_history()
                    if self.popup.isVisible(): self.popup.update_list("")
                else:
                    self.reload_word_file()
            else:
                QMessageBox.warning(self.popup, "错误", f"从 {os.path.basename(source_path)} 删除词条失败！")

    @Slot()
    def add_library(self):
        file_path, _ = QFileDialog.getOpenFileName(self.popup, "选择一个词库文件", "", "Markdown 文件 (*.md)")
        if file_path:
            # 检查是否已存在
            if any(lib['path'] == file_path for lib in self.settings.libraries):
                QMessageBox.information(self.popup, "提示", "该词库已在列表中。")
                return
            
            self.settings.libraries.append({"path": file_path, "enabled": True})
            self.settings.save()
            self.reload_word_file()
            self.rebuild_library_menu()

    @Slot(str)
    def remove_library(self, path):
        self.settings.libraries = [lib for lib in self.settings.libraries if lib.get('path') != path]
        self.settings.save()
        self.reload_word_file()
        self.rebuild_library_menu()

    @Slot(str)
    def toggle_library_enabled(self, path):
        for lib in self.settings.libraries:
            if lib.get('path') == path:
                lib['enabled'] = not lib.get('enabled', True)
                break
        self.settings.save()
        self.word_manager.aggregate_words()
        self.rebuild_library_menu()

    def rebuild_library_menu(self):
        self.library_menu.clear()
        
        add_action = QAction("添加md词库", self.library_menu)
        add_action.triggered.connect(self.add_library)
        self.library_menu.addAction(add_action)
        self.library_menu.addSeparator()

        for lib in self.settings.libraries:
            lib_path = lib.get('path')
            lib_name = os.path.basename(lib_path)
            
            # 主操作行
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(5, 5, 5, 5)
            
            checkbox = QCheckBox(lib_name)
            checkbox.setChecked(lib.get('enabled', True))
            checkbox.toggled.connect(lambda _, p=lib_path: self.toggle_library_enabled(p))
            
            open_button = QPushButton("📂") # 打开文件夹图标
            open_button.setFixedSize(20, 20)
            open_button.setToolTip("打开词库文件")
            open_button.clicked.connect(lambda _, p=lib_path: self.open_library_file(p))

            remove_button = QPushButton("❌") # 删除图标
            remove_button.setFixedSize(20, 20)
            remove_button.setToolTip("移除此词库")
            remove_button.clicked.connect(lambda _, p=lib_path: self.remove_library(p))
            
            layout.addWidget(checkbox)
            layout.addStretch()
            layout.addWidget(open_button)
            layout.addWidget(remove_button)
            
            action = QWidgetAction(self.library_menu)
            action.setDefaultWidget(widget)
            self.library_menu.addAction(action)

    @Slot(str)
    def open_library_file(self, path):
        """在文件浏览器中打开指定的词库文件"""
        try:
            # 使用 webbrowser 打开文件所在的目录，并选中该文件
            # 这在不同操作系统上行为可能略有不同，但通常是有效的
            webbrowser.open(os.path.dirname(path))
            log(f"尝试打开词库文件: {path}")
        except Exception as e:
            log(f"打开词库文件失败: {e}")
            QMessageBox.warning(self.popup, "错误", f"无法打开文件路径：\n{path}\n\n错误: {e}")

    @Slot()
    def cleanup_and_exit(self):
        self.hotkey_manager.stop()
        log("程序退出。")

    @Slot()
    def toggle_hotkeys_enabled(self):
        self.settings.hotkeys_enabled = not self.settings.hotkeys_enabled
        self.settings.save()
        if self.settings.hotkeys_enabled:
            self.hotkey_manager.start()
            log("快捷键已启用。")
        else:
            self.hotkey_manager.stop()
            log("快捷键已禁用。")
        
        if hasattr(self, 'toggle_hotkeys_action'):
            self.toggle_hotkeys_action.setChecked(self.settings.hotkeys_enabled)

    @Slot()
    def toggle_theme(self):
        new_theme = "light" if self.settings.theme == "dark" else "dark"
        self.settings.theme = new_theme; self.settings.save()
        log(f"切换主题为: {new_theme}"); self.popup.apply_theme(); self.apply_menu_theme()
        if hasattr(self, 'toggle_theme_action'): self.toggle_theme_action.setText(f"切换到 {'夜间' if new_theme == 'light' else '日间'} 模式")

    @Slot()
    def toggle_multi_word_search(self):
        self.settings.multi_word_search = not self.settings.multi_word_search
        self.settings.save()
        log(f"多词搜索模式: {'开启' if self.settings.multi_word_search else '关闭'}")
        if hasattr(self, 'multi_word_search_action'):
            self.multi_word_search_action.setChecked(self.settings.multi_word_search)
        
    @Slot()
    def set_font_size(self):
        current_size = self.settings.font_size
        new_size, ok = QInputDialog.getInt(None, "设置字体大小", "请输入新的字体大小 (例如: 14):", current_size, 8, 72, 1)
        
        if ok and new_size != current_size:
            self.settings.font_size = new_size
            self.settings.save()
            log(f"字体大小已更新为: {new_size}")
            self.popup.apply_theme()
            QMessageBox.information(None, "成功", f"字体大小已设置为 {new_size}！")

    @Slot()
    def toggle_pinyin_initial_search(self):
        self.settings.pinyin_initial_search = not self.settings.pinyin_initial_search
        self.settings.save()
        log(f"拼音首字母匹配: {'开启' if self.settings.pinyin_initial_search else '关闭'}")
        if hasattr(self, 'pinyin_search_action'):
            self.pinyin_search_action.setChecked(self.settings.pinyin_initial_search)

    def apply_menu_theme(self, menu=None):
        target_menu = menu if menu else self.menu
        if not target_menu: return
        
        theme = THEMES[self.settings.theme]
        target_menu.setStyleSheet(f"""
            QMenu {{
                background-color: {theme['bg_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 8px;
                color: {theme['text_color']};
                font-size: {self.settings.font_size}px;
                padding: 5px;
            }}
            QMenu::item {{
                padding: 8px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {theme['item_selected_bg']};
                color: {theme['item_selected_text']};
            }}
            QMenu::item:disabled {{
                color: #888;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {theme['border_color']};
                margin: 5px 0;
            }}
        """)

    # --- 新增：剪贴板菜单相关方法 ---
    @Slot()
    def toggle_clipboard_memory(self):
        self.settings.clipboard_memory_enabled = not self.settings.clipboard_memory_enabled
        self.settings.save()
        self.update_clipboard_monitor_status()
        if hasattr(self, 'clipboard_memory_action'):
            self.clipboard_memory_action.setChecked(self.settings.clipboard_memory_enabled)
        # 刷新列表
        if self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())

    @Slot()
    def set_clipboard_memory_count(self):
        current_count = self.settings.clipboard_memory_count
        new_count, ok = QInputDialog.getInt(None, "设置记忆次数",
                                             "请输入剪贴板记忆的最大条数:",
                                             current_count, 1, 100, 1)
        if ok and new_count != current_count:
            self.settings.clipboard_memory_count = new_count
            self.settings.save()
            log(f"剪贴板记忆次数已更新为: {new_count}")
            QMessageBox.information(None, "成功", f"剪贴板记忆次数已设置为 {new_count} 条！")

    @Slot()
    def clear_clipboard_history_menu(self):
        reply = QMessageBox.question(None, "确认清空",
                                     "确定要清空所有剪贴板历史记录吗？此操作不可恢复。",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            if self.word_manager.clear_clipboard_history():
                QMessageBox.information(None, "成功", "剪贴板历史已清空！")
                if self.popup.isVisible():
                    self.popup.update_list("")
            else:
                QMessageBox.warning(None, "错误", "清空剪贴板历史失败！")

    # --- 新增：自动重启相关方法 ---
    @Slot()
    def perform_restart(self):
        """执行重启操作"""
        log("执行重启...")
        # 退出前保存所有设置
        self.settings.save()
        # 隐藏窗口并注销热键，为重启做准备
        self.popup.hide()
        # self.unregister_hotkeys() # 已移除
        # 延迟执行重启，以确保事件循环处理了清理工作
        QTimer.singleShot(100, self._restart_process)

    def _restart_process(self):
        """实际的重启进程调用"""
        try:
            log(f"准备重启: sys.executable={sys.executable}, sys.argv={sys.argv}")
            # 使用 QProcess.startDetached() 来启动一个新进程
            # 这是在Qt应用中进行重启的更可靠方法
            QProcess.startDetached(sys.executable, sys.argv)
            # 当前进程正常退出
            self.app.quit()
        except Exception as e:
            log(f"重启失败: {e}")
            QMessageBox.critical(None, "错误", f"应用程序重启失败: {e}")

    def update_auto_restart_timer(self):
        """更新自动重启定时器的状态"""
        if self.settings.auto_restart_enabled and self.settings.auto_restart_interval > 0:
            interval_ms = self.settings.auto_restart_interval * 60 * 1000
            self.auto_restart_timer.start(interval_ms)
            log(f"自动重启定时器已启动，间隔: {self.settings.auto_restart_interval} 分钟。")
        else:
            self.auto_restart_timer.stop()
            log("自动重启定时器已停止。")

    @Slot()
    def toggle_auto_restart(self):
        """切换自动重启状态"""
        self.settings.auto_restart_enabled = not self.settings.auto_restart_enabled
        self.settings.save()
        self.update_auto_restart_timer()
        if hasattr(self, 'auto_restart_action'):
            self.auto_restart_action.setChecked(self.settings.auto_restart_enabled)

    @Slot()
    def set_auto_restart_interval(self):
        """设置自动重启间隔"""
        current_interval = self.settings.auto_restart_interval
        new_interval, ok = QInputDialog.getInt(None, "设置自动重启间隔",
                                               "请输入新的间隔分钟数 (0 表示禁用):",
                                               current_interval, 0, 1440, 1)
        if ok and new_interval != current_interval:
            self.settings.auto_restart_interval = new_interval
            self.settings.save()
            self.update_auto_restart_timer()
            QMessageBox.information(None, "成功", f"自动重启间隔已设置为 {new_interval} 分钟！")


# --- main入口 ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    settings_manager = SettingsManager(CONFIG_FILE)
    word_manager = WordManager(settings_manager)
    controller = MainController(app, word_manager, settings_manager)
    
    # 剪贴板监控初始化
    controller.last_clipboard_text = "" # 跟踪上一次的剪贴板内容
    controller.clipboard_timer = QTimer(controller)
    controller.clipboard_timer.setInterval(1000) # 每秒检查一次
    controller.clipboard_timer.timeout.connect(controller.check_clipboard)
    controller.update_clipboard_monitor_status()

    tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), app); tray_icon.setToolTip("QuickKV")
    menu = QMenu()
    controller.menu = menu # 将menu实例传递给controller
    
    # --- 版本号标题 ---
    version_action = QAction(f"QuickKV v{VERSION}")
    version_action.setEnabled(False)
    menu.addAction(version_action)
    menu.addSeparator()
    
    # --- 主要功能 ---
    controller.toggle_hotkeys_action = QAction("启用快捷键", checkable=True)
    controller.toggle_hotkeys_action.setChecked(settings_manager.hotkeys_enabled)
    controller.toggle_hotkeys_action.triggered.connect(controller.toggle_hotkeys_enabled)
    menu.addAction(controller.toggle_hotkeys_action)
    
    # --- 自动重启 ---
    restart_menu = QMenu("间隔时间自动重启")
    controller.auto_restart_action = QAction("间隔时间自动重启", checkable=True)
    controller.auto_restart_action.setChecked(settings_manager.auto_restart_enabled)
    controller.auto_restart_action.triggered.connect(controller.toggle_auto_restart)
    restart_menu.addAction(controller.auto_restart_action)

    set_restart_interval_action = QAction("设定间隔时间...")
    set_restart_interval_action.triggered.connect(controller.set_auto_restart_interval)
    restart_menu.addAction(set_restart_interval_action)

    restart_now_action = QAction("立即重启")
    restart_now_action.triggered.connect(controller.perform_restart)
    restart_menu.addAction(restart_now_action)
    
    menu.addMenu(restart_menu)
    menu.addSeparator()

    # --- 词库选择 ---
    library_menu = QMenu("词库选择")
    controller.library_menu = library_menu # 方便后续重建
    menu.addMenu(library_menu)
    
    # --- 设置 ---
    menu.addSeparator()
    controller.multi_word_search_action = QAction("打空格多词包含搜索", checkable=True)
    controller.multi_word_search_action.setChecked(settings_manager.multi_word_search)
    controller.multi_word_search_action.triggered.connect(controller.toggle_multi_word_search)
    menu.addAction(controller.multi_word_search_action)

    controller.pinyin_search_action = QAction("拼音首字母匹配", checkable=True)
    controller.pinyin_search_action.setChecked(settings_manager.pinyin_initial_search)
    controller.pinyin_search_action.triggered.connect(controller.toggle_pinyin_initial_search)
    menu.addAction(controller.pinyin_search_action)

    # --- 剪贴板记忆 ---
    clipboard_menu = QMenu("剪贴板文字记忆")
    controller.clipboard_memory_action = QAction("剪贴板文字记忆", checkable=True)
    controller.clipboard_memory_action.setChecked(settings_manager.clipboard_memory_enabled)
    controller.clipboard_memory_action.triggered.connect(controller.toggle_clipboard_memory)
    clipboard_menu.addAction(controller.clipboard_memory_action)

    set_count_action = QAction("记忆次数...")
    set_count_action.triggered.connect(controller.set_clipboard_memory_count)
    clipboard_menu.addAction(set_count_action)

    clear_history_action = QAction("清空")
    clear_history_action.triggered.connect(controller.clear_clipboard_history_menu)
    clipboard_menu.addAction(clear_history_action)
    
    menu.addMenu(clipboard_menu)
    menu.addSeparator()

    initial_toggle_text = f"切换到 {'夜间' if settings_manager.theme == 'light' else '日间'} 模式"
    controller.toggle_theme_action = QAction(initial_toggle_text); controller.toggle_theme_action.triggered.connect(controller.toggle_theme); menu.addAction(controller.toggle_theme_action)
    
    font_size_action = QAction("设置字体大小(&F)..."); font_size_action.triggered.connect(controller.set_font_size); menu.addAction(font_size_action)

    # --- 退出 ---
    menu.addSeparator()
    quit_action = QAction("退出(&Q)"); quit_action.triggered.connect(app.quit); menu.addAction(quit_action)
    
    controller.apply_menu_theme() # 初始化时应用主题
    controller.rebuild_library_menu() # 首次构建词库菜单
    tray_icon.setContextMenu(menu); tray_icon.show()
    
    log("程序启动成功，正在后台运行。")
    print(f"按下 '{HOTKEY}' 来激活或关闭窗口。")
    print(f"当前主题: {settings_manager.theme}。右键点击托盘图标可进行设置。")
    
    # 连接 aboutToQuit 信号到清理函数
    app.aboutToQuit.connect(controller.cleanup_and_exit)
    
    sys.exit(app.exec())