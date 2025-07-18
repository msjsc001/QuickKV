# -*- coding: utf-8 -*-
"""
一个轻量级的、系统级的中文联想输入工具 (v17.0 - 滚动条修复版)。

核心功能:
- [修复] 修正了列表控件的布局策略，确保在内容过多时能正确显示滚动条。
- 基于稳定版v7.0，保留了可靠的右下角八向缩放功能。
- 调整了夜间模式的选中项颜色，使其更清晰、更易分辨。
- 在系统托盘菜单中新增“设置字体大小”功能，实时生效并自动保存。
- 界面回归极简设计，无左上角图标。

依赖库:
- PySide6
- keyboard
- pyperclip
"""
import sys
import os
import webbrowser
import configparser
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QSystemTrayIcon, QMenu, QSizeGrip,
                             QGraphicsDropShadowEffect, QPushButton,
                             QInputDialog, QMessageBox)
from PySide6.QtCore import (Qt, Signal, Slot, QObject, QFileSystemWatcher,
                          QTimer, QEvent, QRect)
from PySide6.QtGui import QIcon, QAction, QCursor, QPixmap, QPainter, QColor
import keyboard
import pyperclip

# --- 全局配置 ---
WORD_FILE = "词库.md"
CONFIG_FILE = "config.ini"
HOTKEY = "ctrl+space"
DEBUG_MODE = True
ICON_PATH = "icon.png"

def log(message):
    if DEBUG_MODE:
        print(f"[LOG] {message}")

# --- 主题颜色定义 ---
THEMES = {
    "dark": {
        "bg_color": "#282c34", "border_color": "#444", "text_color": "#abb2bf",
        "input_bg_color": "#3a3f4b", "item_hover_bg": "#383c4a",
        "item_selected_bg": "#0288d1", "item_selected_text": "#ffffff"
    },
    "light": {
        "bg_color": "#fdfdfd", "border_color": "#cccccc", "text_color": "#202020",
        "input_bg_color": "#ffffff", "item_hover_bg": "#f0f0f0",
        "item_selected_bg": "#0078d7", "item_selected_text": "#ffffff"
    }
}

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
        
        self.width = self.config.getint('Window', 'width', fallback=450)
        self.height = self.config.getint('Window', 'height', fallback=300)
        self.theme = self.config.get('Theme', 'mode', fallback='dark')
        self.font_size = self.config.getint('Font', 'size', fallback=14)

    def save(self):
        self.config['Window']['width'] = str(self.width)
        self.config['Window']['height'] = str(self.height)
        self.config['Theme']['mode'] = self.theme
        self.config['Font']['size'] = str(self.font_size)
        
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

    def load_words(self):
        log(f"开始从 {self.file_path} 加载词库...")
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f: lines = f.readlines()
            self.words = sorted([line.strip()[2:].strip() for line in lines if line.strip().startswith('- ')])
            log(f"成功加载 {len(self.words)} 个词条。")
        except FileNotFoundError:
            log(f"词库文件不存在，在 {self.file_path} 创建一个新文件。")
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write("- 这是一个示例词条\n- Hello World\n" + "\n".join([f"- 示例词条 {i}" for i in range(30)]))
            self.words = ["这是一个示例词条", "Hello World"] + [f"示例词条 {i}" for i in range(30)]
        except Exception as e:
            log(f"加载词库时发生错误: {e}")
            self.words = []

    def find_matches(self, query):
        if not query: return self.words
        query_lower = query.lower()
        return [word for word in self.words if query_lower in word.lower()]


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
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True) # 启用鼠标跟踪以更新光标
        self.container = QWidget()
        self.container.setMouseTracking(True)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(self.container)
        shadow = QGraphicsDropShadowEffect(self); shadow.setBlurRadius(15); shadow.setColor(QColor(0, 0, 0, 80)); shadow.setOffset(0, 2)
        self.container.setGraphicsEffect(shadow)
        
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(1, 1, 1, 1)
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
        self.search_box.setMouseTracking(True)
        self.list_widget.setMouseTracking(True)

        container_layout.addLayout(title_bar_layout)
        container_layout.addWidget(self.search_box)
        container_layout.addWidget(self.list_widget, 1)
        
        self.apply_theme()
        self.resize(self.settings.width, self.settings.height)
        self.setMinimumSize(250, 150) # 设置一个合理的最小尺寸
        self.search_box.textChanged.connect(self.update_list)
        self.list_widget.itemClicked.connect(self.on_item_selected)
        self.list_widget.itemActivated.connect(self.on_item_selected)

    def _update_resize_cursor(self, pos):
        m = self.resize_margin
        rect = self.rect()
        self.resize_edge["top"] = abs(pos.y()) < m
        self.resize_edge["bottom"] = abs(pos.y() - rect.height()) < m
        self.resize_edge["left"] = abs(pos.x()) < m
        self.resize_edge["right"] = abs(pos.x() - rect.width()) < m

        if self.resize_edge["top"] and self.resize_edge["left"]: self.setCursor(Qt.SizeFDiagCursor)
        elif self.resize_edge["top"] and self.resize_edge["right"]: self.setCursor(Qt.SizeBDiagCursor)
        elif self.resize_edge["bottom"] and self.resize_edge["left"]: self.setCursor(Qt.SizeBDiagCursor)
        elif self.resize_edge["bottom"] and self.resize_edge["right"]: self.setCursor(Qt.SizeFDiagCursor)
        elif self.resize_edge["top"]: self.setCursor(Qt.SizeVerCursor)
        elif self.resize_edge["bottom"]: self.setCursor(Qt.SizeVerCursor)
        elif self.resize_edge["left"]: self.setCursor(Qt.SizeHorCursor)
        elif self.resize_edge["right"]: self.setCursor(Qt.SizeHorCursor)
        else: self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if any(self.resize_edge.values()):
                self.resizing = True
                self.resize_start_pos = event.globalPosition().toPoint()
                self.resize_start_geom = self.geometry()
            elif pos.y() < 35:
                self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self.resizing:
            delta = event.globalPosition().toPoint() - self.resize_start_pos
            geom = self.resize_start_geom
            new_geom = QRect(geom)

            if self.resize_edge["top"]: new_geom.setTop(geom.top() + delta.y())
            if self.resize_edge["bottom"]: new_geom.setBottom(geom.bottom() + delta.y())
            if self.resize_edge["left"]: new_geom.setLeft(geom.left() + delta.x())
            if self.resize_edge["right"]: new_geom.setRight(geom.right() + delta.x())
            
            if new_geom.width() < self.minimumWidth() or new_geom.height() < self.minimumHeight():
                return
            self.setGeometry(new_geom)

        elif event.buttons() == Qt.LeftButton and self.drag_position is not None:
            self.move(event.globalPosition().toPoint() - self.drag_position)
        else:
            self._update_resize_cursor(pos)
        event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.resizing = False
        self.drag_position = None
        self.resize_start_pos = None
        self.resize_start_geom = None
        self.unsetCursor()
        event.accept()
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


    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress:
            is_outside = not self.geometry().contains(event.globalPosition().toPoint())
            is_input_empty = self.search_box.text() == ""
            if is_outside and is_input_empty and not self.pinned: # 增加对pinned状态的判断
                log("检测到在窗口外单击，且输入框为空，自动隐藏。")
                self.hide(); return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        QApplication.instance().installEventFilter(self); super().showEvent(event)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self); self.settings.width = self.width(); self.settings.height = self.height(); self.settings.save(); super().hideEvent(event)

    def apply_theme(self):
        theme = THEMES[self.settings.theme]
        font_size = self.settings.font_size
        self.container.setStyleSheet(f"background-color: {theme['bg_color']}; border: 1px solid {theme['border_color']}; border-radius: 8px;")
        self.search_box.setStyleSheet(f"background-color: {theme['input_bg_color']}; color: {theme['text_color']}; border: 1px solid {theme['border_color']}; border-radius: 4px; padding: 8px; font-size: {font_size}px; margin: 0px 8px 4px 8px;")
        self.list_widget.setStyleSheet(f"color: {theme['text_color']}; border: none; padding-left: 8px; font-size: {font_size}px; QListWidget::item {{ padding: 8px; border-radius: 4px;}} QListWidget::item:hover {{ background-color: {theme['item_hover_bg']}; }} QListWidget::item:selected {{ background-color: {theme['item_selected_bg']}; color: {theme['item_selected_text']}; }}")
        self.close_button.setStyleSheet(f"QPushButton {{ background-color: transparent; color: {theme['text_color']}; border: none; font-size: 16px; font-weight: bold; }} QPushButton:hover {{ color: white; background-color: #E81123; border-radius: 4px; }}")
        self._update_pin_button_style() # 应用主题时更新图钉按钮样式

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
    
    @Slot(str)
    def update_list(self, text):
        self.list_widget.clear(); self.list_widget.addItems(self.word_manager.find_matches(text))
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
        super().__init__(); self.app = app; self.word_manager = word_manager; self.settings = settings_manager
        self.popup = SearchPopup(self.word_manager, self.settings)
        self.show_popup_signal.connect(self.popup.show_and_focus)
        self.hide_popup_signal.connect(self.popup.hide)
        self.popup.suggestion_selected.connect(self.on_suggestion_selected)
        try:
            keyboard.add_hotkey(hotkey, self.on_hotkey_triggered)
            log(f"全局热键 '{hotkey}' 注册成功。")
        except Exception as e:
            log(f"注册热键失败，可能是权限问题: {e}")
        self.file_watcher = QFileSystemWatcher([self.word_manager.file_path])
        self.file_watcher.fileChanged.connect(self.schedule_reload)
        self.reload_timer = QTimer(self); self.reload_timer.setSingleShot(True); self.reload_timer.setInterval(300); self.reload_timer.timeout.connect(self.reload_word_file)

    def on_hotkey_triggered(self):
        if self.popup.isVisible():
            log("热键触发：关闭窗口。"); self.hide_popup_signal.emit()
        else:
            log("热键触发：打开窗口。"); self.show_popup_signal.emit()

    @Slot()
    def schedule_reload(self): log("检测到文件变化，安排重载..."); self.reload_timer.start()
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
    def toggle_theme(self):
        new_theme = "light" if self.settings.theme == "dark" else "dark"
        self.settings.theme = new_theme; self.settings.save()
        log(f"切换主题为: {new_theme}"); self.popup.apply_theme()
        if hasattr(self, 'toggle_theme_action'): self.toggle_theme_action.setText(f"切换到 {'夜间' if new_theme == 'light' else '日间'} 模式")
        
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

# --- main入口 ---
def create_default_icon(path):
    # ... (代码无变化)
    if not os.path.exists(path):
        pixmap = QPixmap(64, 64); pixmap.fill(Qt.transparent); painter = QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing); painter.setBrush(QColor("#0078d7")); painter.setPen(Qt.NoPen); painter.drawRoundedRect(12, 12, 40, 40, 10, 10); painter.end(); pixmap.save(path)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    create_default_icon(ICON_PATH)
    
    settings_manager = SettingsManager(CONFIG_FILE)
    word_manager = WordManager(WORD_FILE)
    controller = MainController(app, word_manager, settings_manager, HOTKEY)
    
    tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), app); tray_icon.setToolTip("快捷联想输入工具")
    menu = QMenu()
    
    open_action = QAction("打开词库文件(&O)"); open_action.triggered.connect(lambda: webbrowser.open(os.path.abspath(WORD_FILE))); menu.addAction(open_action)
    
    initial_toggle_text = f"切换到 {'夜间' if settings_manager.theme == 'light' else '日间'} 模式"
    controller.toggle_theme_action = QAction(initial_toggle_text); controller.toggle_theme_action.triggered.connect(controller.toggle_theme); menu.addAction(controller.toggle_theme_action)
    
    font_size_action = QAction("设置字体大小(&F)..."); font_size_action.triggered.connect(controller.set_font_size); menu.addAction(font_size_action)

    menu.addSeparator()
    quit_action = QAction("退出(&Q)"); quit_action.triggered.connect(app.quit); menu.addAction(quit_action)
    
    tray_icon.setContextMenu(menu); tray_icon.show()
    
    log("程序启动成功，正在后台运行。")
    print(f"按下 '{HOTKEY}' 来激活或关闭窗口。")
    print(f"当前主题: {settings_manager.theme}。右键点击托盘图标可进行设置。")
    
    sys.exit(app.exec())