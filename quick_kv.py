# -*- coding: utf-8 -*-
"""
QuickKV v1.0.3
"""
import sys
import os
import webbrowser
import configparser
import hashlib
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QSystemTrayIcon, QMenu, QSizeGrip,
                             QGraphicsDropShadowEffect, QPushButton,
                             QInputDialog, QMessageBox, QStyledItemDelegate, QStyle)
from PySide6.QtCore import (Qt, Signal, Slot, QObject, QFileSystemWatcher,
                          QTimer, QEvent, QRect)
from PySide6.QtGui import QIcon, QAction, QCursor, QPixmap, QPainter, QColor
import keyboard
import pyperclip
from pypinyin import pinyin, Style

# --- 全局配置 ---
def get_base_path():
    """获取基础路径，用于定位外部文件（如config和词库）"""
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 exe
        return os.path.dirname(sys.executable)
    else:
        # 如果是直接运行的 .py
        return os.path.abspath(".")

def resource_path(relative_path):
    """获取内部资源的路径（如图标），这部分会被打包进exe"""
    try:
        # PyInstaller 创建一个临时文件夹，并将路径存储在 _MEIPASS 中
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- 外部数据文件（可读写，放在exe旁边） ---
BASE_PATH = get_base_path()
WORD_FILE = os.path.join(BASE_PATH, "词库.md")
CONFIG_FILE = os.path.join(BASE_PATH, "config.ini")

# --- 内部资源（只读，打包进exe） ---
ICON_PATH = resource_path("icon.png")

# --- 其他配置 ---
HOTKEY = "ctrl+space"
DEBUG_MODE = True
VERSION = "1.0.3" # 版本号

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
        text = index.data(Qt.DisplayRole)

        # 根据状态绘制背景
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(theme['item_selected_bg']))
            painter.setPen(QColor(theme['item_selected_text']))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(theme['item_hover_bg']))
            painter.setPen(QColor(theme['text_color']))
        else:
            # 恢复为最稳妥的方案：为普通状态明确绘制背景
            painter.fillRect(rect, QColor(theme['bg_color']))
            painter.setPen(QColor(theme['text_color']))

        # 绘制文字 (带内边距)
        # QListWidget::item 的 padding 已经包含在 option.rect 中，我们只需对齐
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, "  " + text)
        painter.restore()

    def sizeHint(self, option, index):
        # 确保项目有足够的高度
        size = super().sizeHint(option, index)
        size.setHeight(36) # 可以根据字体大小动态调整
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

        self.hotkeys_enabled = self.config.getboolean('General', 'hotkeys_enabled', fallback=True)
        self.width = self.config.getint('Window', 'width', fallback=450)
        self.height = self.config.getint('Window', 'height', fallback=300)
        self.theme = self.config.get('Theme', 'mode', fallback='dark')
        self.font_size = self.config.getint('Font', 'size', fallback=14)
        self.multi_word_search = self.config.getboolean('Search', 'multi_word_search', fallback=True)
        self.pinyin_initial_search = self.config.getboolean('Search', 'pinyin_initial_search', fallback=True)
        self.last_sorted_hash = self.config.get('Data', 'last_sorted_hash', fallback='')

    def save(self):
        self.config['General']['hotkeys_enabled'] = str(self.hotkeys_enabled)
        self.config['Window']['width'] = str(self.width)
        self.config['Window']['height'] = str(self.height)
        self.config['Theme']['mode'] = self.theme
        self.config['Font']['size'] = str(self.font_size)
        self.config['Search']['multi_word_search'] = str(self.multi_word_search)
        self.config['Search']['pinyin_initial_search'] = str(self.pinyin_initial_search)
        self.config['Data']['last_sorted_hash'] = str(self.last_sorted_hash)
        
        with open(self.file_path, 'w', encoding='utf-8') as configfile:
            self.config.write(configfile)
        log(f"配置已保存到 {self.file_path}")

# --- 词库管理器 ---
class WordManager:
    # ... (代码无变化)
    def __init__(self, file_path):
        self.file_path = file_path
        self.words = []
        self.load_words()

    def _get_pinyin_sort_key(self, text):
        """获取用于排序的拼音key（辅助函数）"""
        return "".join(item[0] for item in pinyin(text, style=Style.NORMAL))

    def _get_pinyin_initials(self, text):
        """获取文本的拼音首字母（小写）"""
        return "".join(item[0] for item in pinyin(text, style=Style.FIRST_LETTER))

    def load_words(self):
        log(f"开始从 {self.file_path} 加载词库...")
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            raw_words = [line.strip()[2:].strip() for line in lines if line.strip().startswith('- ')]
            # 加载时就按拼音排序
            self.words = sorted(raw_words, key=self._get_pinyin_sort_key)
            log(f"成功加载并排序 {len(self.words)} 个词条。")
        except FileNotFoundError:
            log(f"词库文件不存在，在 {self.file_path} 创建一个新文件。")
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write("- 这是一个示例词条\n- Hello World\n" + "\n".join([f"- 示例词条 {i}" for i in range(30)]))
            self.words = ["这是一个示例词条", "Hello World"] + [f"示例词条 {i}" for i in range(30)]
        except Exception as e:
            log(f"加载词库时发生错误: {e}")
            self.words = []

    def find_matches(self, query, multi_word_search_enabled=False, pinyin_search_enabled=False):
        if not query:
            return self.words

        query_lower = query.lower()
        matches = []

        if multi_word_search_enabled and ' ' in query_lower.strip():
            keywords = [k for k in query_lower.split(' ') if k]
            if not keywords:
                matches = [word for word in self.words if query_lower in word.lower()]
            else:
                if pinyin_search_enabled:
                    # 模式: 多词 + 拼音
                    matches = [
                        word for word in self.words
                        if all(
                            (keyword in word.lower() or keyword in self._get_pinyin_initials(word))
                            for keyword in keywords
                        )
                    ]
                else:
                    # 模式: 仅多词
                    matches = [
                        word for word in self.words
                        if all(keyword in word.lower() for keyword in keywords)
                    ]
        else:
            # 模式: 单短语搜索 (无论多词开关是否开启)
            if pinyin_search_enabled:
                # 模式: 单短语 + 拼音
                matches = [
                    word for word in self.words
                    if query_lower in word.lower() or query_lower in self._get_pinyin_initials(word)
                ]
            else:
                # 模式: 仅单短语
                matches = [word for word in self.words if query_lower in word.lower()]

        return sorted(matches, key=self._get_pinyin_sort_key)

    def _calculate_sorted_hash(self, lines):
        """计算排序后内容的哈希值"""
        valid_lines = [line.strip() for line in lines if line.strip().startswith('- ')]
        sorted_lines = sorted(valid_lines, key=lambda line: self._get_pinyin_sort_key(line[2:].strip()))
        # 将所有行连接成一个字符串，然后计算哈希
        content_string = "\n".join(sorted_lines)
        return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

    def sort_and_save_words(self):
        """读取、按拼音排序并保存词库文件，返回新内容的哈希值"""
        log(f"开始按拼音排序并保存词库文件: {self.file_path}")
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            valid_lines = [line.strip() for line in lines if line.strip().startswith('- ')]
            sorted_lines = sorted(valid_lines, key=lambda line: self._get_pinyin_sort_key(line[2:].strip()))
            
            # 写入文件
            sorted_content = '\n'.join(sorted_lines) + '\n'
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(sorted_content)
            
            # 计算并返回新内容的哈希值
            new_hash = hashlib.sha256(sorted_content.encode('utf-8')).hexdigest()
            log(f"词库文件按拼音排序并保存成功。新哈希: {new_hash}")
            return new_hash
        except Exception as e:
            log(f"按拼音排序和保存词库时发生错误: {e}")
            return None


# --- 搜索弹出窗口UI (滚动条修复) ---
class SearchPopup(QWidget):
    suggestion_selected = Signal(str)

    def __init__(self, word_manager, settings_manager):
        super().__init__()
        self.word_manager = word_manager
        self.settings = settings_manager
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
        if pos_y + window_size.height() > screen_geom.y() + screen_geom.height():
            log("下方空间不足，窗口向上翻转。"); pos_y = cursor_pos.y() - window_size.height() - 15
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
        matches = self.word_manager.find_matches(text, self.settings.multi_word_search, self.settings.pinyin_initial_search)
        self.list_widget.clear()
        self.list_widget.addItems(matches)
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

# --- 主控制器 ---
class MainController(QObject):
    # ... (代码无变化)
    show_popup_signal = Signal()
    hide_popup_signal = Signal()

    def __init__(self, app, word_manager, settings_manager, hotkey):
        super().__init__(); self.app = app; self.word_manager = word_manager; self.settings = settings_manager; self.menu = None
        self.popup = SearchPopup(self.word_manager, self.settings)
        self.show_popup_signal.connect(self.popup.show_and_focus)
        self.hide_popup_signal.connect(self.popup.hide)
        self.popup.suggestion_selected.connect(self.on_suggestion_selected)
        
        self.hotkey = hotkey
        if self.settings.hotkeys_enabled:
            self.register_hotkeys()

        self.file_watcher = QFileSystemWatcher([self.word_manager.file_path])
        self.file_watcher.fileChanged.connect(self.schedule_reload)
        self.reload_timer = QTimer(self); self.reload_timer.setSingleShot(True); self.reload_timer.setInterval(300); self.reload_timer.timeout.connect(self.reload_word_file)

    def register_hotkeys(self):
        try:
            keyboard.add_hotkey(self.hotkey, self.on_hotkey_triggered)
            log("全局快捷键已注册。")
        except Exception as e:
            log(f"注册快捷键时发生错误: {e}")

    def unregister_hotkeys(self):
        try:
            keyboard.remove_hotkey(self.hotkey)
            log("全局快捷键已移除。")
        except Exception as e:
            log(f"移除快捷键时发生错误: {e}")

    def on_hotkey_triggered(self):
        if not self.settings.hotkeys_enabled: return
        if self.popup.isVisible():
            log("热键触发：关闭窗口。"); self.hide_popup_signal.emit()
        else:
            log("热键触发：打开窗口。"); self.show_popup_signal.emit()

    @Slot()
    def schedule_reload(self):
        log("检测到文件变化，安排重载...");
        self.reload_timer.start()
    @Slot()
    def reload_word_file(self):
        log("执行词库重载。"); self.word_manager.load_words()
        if self.popup.isVisible(): self.popup.update_list(self.popup.search_box.text())
    @Slot(str)
    def on_suggestion_selected(self, text):
        log(f"已选择词条: '{text}'")
        pyperclip.copy(text)
        
        # 延迟执行粘贴，确保焦点已切换
        QTimer.singleShot(200, self.perform_paste)

    def perform_paste(self):
        # 使用更可靠的打字方式输入，而不是模拟Ctrl+V
        clipboard_content = pyperclip.paste()
        if clipboard_content:
            keyboard.write(clipboard_content)
            log(f"已通过打字方式输入: '{clipboard_content}'")
        else:
            log("剪贴板为空，未执行输入。")
        
        # 如果窗口是固定的，则在粘贴后重新显示它
        if self.popup.pinned:
            log("图钉已启用，重新显示窗口。")
            # 再次延迟以确保粘贴完成
            QTimer.singleShot(50, self.popup.reappear_in_place)
    @Slot()
    def cleanup_and_exit(self):
        """在退出前执行清理工作，基于内容哈希校验来决定是否排序。"""
        try:
            with open(self.word_manager.file_path, 'r', encoding='utf-8') as f:
                current_lines = f.readlines()
            
            # 计算当前文件内容排序后的哈希值
            current_sorted_hash = self.word_manager._calculate_sorted_hash(current_lines)
            
            log(f"退出检查：当前排序后哈希 {current_sorted_hash}, 上次保存的哈希 {self.settings.last_sorted_hash}")

            # 如果当前排序后的哈希与上次保存的哈希不一致，说明文件内容有变动
            if current_sorted_hash != self.settings.last_sorted_hash:
                log("检测到词库内容已更改，正在执行退出前排序...")
                new_hash = self.word_manager.sort_and_save_words()
                if new_hash:
                    self.settings.last_sorted_hash = new_hash
                    self.settings.save()
                    log(f"新的排序哈希 {new_hash} 已保存。")
            else:
                log("词库内容未更改，无需排序，直接退出。")
        except FileNotFoundError:
            log("词库文件不存在，无需执行退出排序。")
        except Exception as e:
            log(f"执行退出清理时发生错误: {e}")

    @Slot()
    def toggle_hotkeys_enabled(self):
        self.settings.hotkeys_enabled = not self.settings.hotkeys_enabled
        self.settings.save()
        if self.settings.hotkeys_enabled:
            self.register_hotkeys()
            log("快捷键已启用。")
        else:
            self.unregister_hotkeys()
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

    def apply_menu_theme(self):
        if not self.menu: return
        theme = THEMES[self.settings.theme]
        self.menu.setStyleSheet(f"""
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

# --- main入口 ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    settings_manager = SettingsManager(CONFIG_FILE)
    word_manager = WordManager(WORD_FILE)
    controller = MainController(app, word_manager, settings_manager, HOTKEY)
    
    tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), app); tray_icon.setToolTip("QuickKV")
    menu = QMenu()
    controller.menu = menu # 将menu实例传递给controller
    controller.apply_menu_theme() # 初始化时应用主题
    
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
    menu.addSeparator()

    open_action = QAction("打开词库文件(&O)"); open_action.triggered.connect(lambda: webbrowser.open(os.path.abspath(WORD_FILE))); menu.addAction(open_action)
    
    # --- 设置 ---
    controller.multi_word_search_action = QAction("打空格多词包含搜索", checkable=True)
    controller.multi_word_search_action.setChecked(settings_manager.multi_word_search)
    controller.multi_word_search_action.triggered.connect(controller.toggle_multi_word_search)
    menu.addAction(controller.multi_word_search_action)

    controller.pinyin_search_action = QAction("拼音首字母匹配", checkable=True)
    controller.pinyin_search_action.setChecked(settings_manager.pinyin_initial_search)
    controller.pinyin_search_action.triggered.connect(controller.toggle_pinyin_initial_search)
    menu.addAction(controller.pinyin_search_action)


    initial_toggle_text = f"切换到 {'夜间' if settings_manager.theme == 'light' else '日间'} 模式"
    controller.toggle_theme_action = QAction(initial_toggle_text); controller.toggle_theme_action.triggered.connect(controller.toggle_theme); menu.addAction(controller.toggle_theme_action)
    
    font_size_action = QAction("设置字体大小(&F)..."); font_size_action.triggered.connect(controller.set_font_size); menu.addAction(font_size_action)

    # --- 退出 ---
    menu.addSeparator()
    quit_action = QAction("退出(&Q)"); quit_action.triggered.connect(app.quit); menu.addAction(quit_action)
    
    tray_icon.setContextMenu(menu); tray_icon.show()
    
    log("程序启动成功，正在后台运行。")
    print(f"按下 '{HOTKEY}' 来激活或关闭窗口。")
    print(f"当前主题: {settings_manager.theme}。右键点击托盘图标可进行设置。")
    
    # 连接 aboutToQuit 信号到清理函数
    app.aboutToQuit.connect(controller.cleanup_and_exit)
    
    sys.exit(app.exec())