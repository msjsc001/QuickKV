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
from ui.delegates import StyledItemDelegate
from ui.components import EditDialog, ScrollableMessageBox


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
        self.setAttribute(Qt.WA_TranslucentBackground) # 重新禁用透明属性
        self.setMouseTracking(True) # 启用鼠标跟踪以更新光标
        self.container = QWidget(self) # 将 container 直接作为子控件
        self.container.setMouseTracking(True)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0) # 移除边距，让 container 填满窗口
        main_layout.addWidget(self.container)
        shadow = QGraphicsDropShadowEffect(self); shadow.setBlurRadius(15); shadow.setColor(QColor(0, 0, 0, 80)); shadow.setOffset(0, 2)
        self.container.setGraphicsEffect(shadow) # 移除阴影
        
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(1, 1, 1, 1) # 恢复紧凑的边距
        container_layout.setSpacing(4)
        
        title_bar_layout = QHBoxLayout()
        title_bar_layout.setContentsMargins(8, 4, 4, 0)
        
        self.title_label = QLabel(f"QuickKV v{VERSION}")
        title_bar_layout.addWidget(self.title_label)
        
        title_bar_layout.addStretch()
        
        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(24, 24)
        self.close_button.clicked.connect(self.hide)
        title_bar_layout.addWidget(self.close_button)
        
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

        # 【关键修复】为所有子控件安装事件过滤器
        # 无边框窗口中，QLineEdit/QListWidget等交互控件会抢占鼠标事件，
        # 导致窗口边缘的缩放逻辑无法被触发。通过事件过滤器拦截边缘区域的鼠标事件来解决。
        for w in [self.container, self.search_box, self.list_widget,
                  self.list_widget.viewport(), self.title_label, self.close_button]:
            w.installEventFilter(self)

    @Slot()
    def force_list_update(self):
        """强制列表视口刷新"""
        self.list_widget.viewport().update()

    def _check_edge(self, window_pos):
        """检查窗口坐标是否处于边缘区域，更新 resize_edge 字典并返回对应的光标形状（或 None）。"""
        m = self.resize_margin
        rect = self.rect()

        on_top = abs(window_pos.y()) < m
        on_bottom = abs(window_pos.y() - rect.height()) < m
        on_left = abs(window_pos.x()) < m
        on_right = abs(window_pos.x() - rect.width()) < m

        self.resize_edge["top"] = on_top
        self.resize_edge["bottom"] = on_bottom
        self.resize_edge["left"] = on_left
        self.resize_edge["right"] = on_right

        if (on_top and on_left) or (on_bottom and on_right):
            return Qt.SizeFDiagCursor
        elif (on_top and on_right) or (on_bottom and on_left):
            return Qt.SizeBDiagCursor
        elif on_top or on_bottom:
            return Qt.SizeVerCursor
        elif on_left or on_right:
            return Qt.SizeHorCursor
        return None

    def _apply_resize_geometry(self, global_pos):
        """根据当前缩放边缘和鼠标全局位置，计算并设置新的窗口几何尺寸。"""
        delta = global_pos - self.resize_start_pos
        new_geom = QRect(self.resize_start_geom)

        if self.resize_edge["top"]: new_geom.setTop(self.resize_start_geom.top() + delta.y())
        if self.resize_edge["bottom"]: new_geom.setBottom(self.resize_start_geom.bottom() + delta.y())
        if self.resize_edge["left"]: new_geom.setLeft(self.resize_start_geom.left() + delta.x())
        if self.resize_edge["right"]: new_geom.setRight(self.resize_start_geom.right() + delta.x())

        # 确保尺寸不会小于最小值
        if new_geom.width() < self.minimumWidth():
            if self.resize_edge["left"]: new_geom.setLeft(self.resize_start_geom.right() - self.minimumWidth())
            else: new_geom.setWidth(self.minimumWidth())
        if new_geom.height() < self.minimumHeight():
            if self.resize_edge["top"]: new_geom.setTop(self.resize_start_geom.bottom() - self.minimumHeight())
            else: new_geom.setHeight(self.minimumHeight())

        self.setGeometry(new_geom)

    def eventFilter(self, watched, event):
        """
        事件过滤器：拦截子控件上的鼠标事件，使无边框窗口的全方位边缘缩放正常工作。
        核心问题：QLineEdit、QListWidget 等交互控件会抢先消费鼠标事件，导致 SearchPopup
        自身的 mousePressEvent/mouseMoveEvent 在窗口边缘区域无法被触发。
        本过滤器在子控件之前拦截事件，当鼠标处于窗口边缘 resize_margin 范围内时，
        优先执行缩放逻辑。
        """
        if event.type() == QEvent.MouseMove:
            # 将子控件坐标映射到窗口级坐标
            window_pos = self.mapFromGlobal(watched.mapToGlobal(event.position().toPoint()))

            if self.resizing:
                # 缩放进行中 - 更新窗口几何尺寸
                self._apply_resize_geometry(event.globalPosition().toPoint())
                return True  # 消费事件，不让子控件处理

            # 检测鼠标是否在窗口边缘
            cursor_shape = self._check_edge(window_pos)
            if cursor_shape is not None:
                # 在边缘区域 → 覆盖子控件（如 QLineEdit 的 I-beam）的光标
                watched.setCursor(cursor_shape)
            else:
                # 不在边缘 → 恢复子控件的默认光标
                watched.unsetCursor()

        elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            window_pos = self.mapFromGlobal(watched.mapToGlobal(event.position().toPoint()))
            cursor_shape = self._check_edge(window_pos)
            if cursor_shape is not None:
                # 在边缘按下鼠标 → 启动缩放，完全拦截事件
                self.resizing = True
                self.resize_start_pos = event.globalPosition().toPoint()
                self.resize_start_geom = self.geometry()
                return True  # 消费事件，阻止子控件处理（如文本框获焦）

        elif event.type() == QEvent.MouseButtonRelease:
            if self.resizing:
                # 缩放结束 → 重置所有状态
                self.resizing = False
                self.resize_start_pos = None
                self.resize_start_geom = None
                for k in self.resize_edge: self.resize_edge[k] = False
                self.unsetCursor()
                watched.unsetCursor()
                return True

        return super().eventFilter(watched, event)

    def _update_resize_cursor(self, pos):
        """供 SearchPopup 自身的 mouseMoveEvent 使用（当事件直接到达父窗口时）"""
        cursor_shape = self._check_edge(pos)
        if cursor_shape is not None:
            self.setCursor(cursor_shape)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            global_pos = event.globalPosition().toPoint()
            # log(f"mousePressEvent: pos={pos}, global_pos={global_pos}")
            self._update_resize_cursor(pos) # 在按下时就判断是否在边缘

            if any(self.resize_edge.values()):
                self.resizing = True
                self.resize_start_pos = global_pos
                self.resize_start_geom = self.geometry()
                # log(f"开始缩放: resize_edge={self.resize_edge}")
            # 仅当鼠标在标题栏区域（例如高度小于35）且未点击关闭按钮时，才开始拖动，并且此时不应该处于缩放状态
            elif pos.y() < 35 and not self.resizing:
                actual_widget = QApplication.widgetAt(global_pos)
                if actual_widget != self.close_button:
                    self.drag_position = global_pos - self.frameGeometry().topLeft()
                    # log(f"开始拖动: drag_position={self.drag_position}")
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()
        # log(f"mouseMoveEvent: pos={pos}, global_pos={global_pos}, resizing={self.resizing}, drag_position={self.drag_position}")

        if self.resizing:
            self._apply_resize_geometry(global_pos)

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



    def showEvent(self, event):
        super().showEvent(event)

    def hideEvent(self, event):
        self.settings.width = self.width(); self.settings.height = self.height(); self.settings.save(); super().hideEvent(event)

    def apply_theme(self):
        theme = THEMES[self.settings.theme]
        font_size = self.settings.font_size
        self.title_label.setStyleSheet(f"color: {theme['title_color']}; font-size: {font_size-2}px; font-weight: normal; background-color: transparent; border: none; padding-left: 4px;")
        self.container.setStyleSheet(f"background-color: {theme['bg_color']}; border: 1px solid {theme['border_color']}; border-radius: 8px;")
        self.search_box.setStyleSheet(f"background-color: {theme['input_bg_color']}; color: {theme['text_color']}; border: 1px solid {theme['border_color']}; border-radius: 0px; padding: 8px; font-size: {font_size}px; margin: 0px 0px 4px 0px;")
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
        # self._update_pin_button_style() # 应用主题时更新图钉按钮样式
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
        """在原位重新显示窗口，并抢夺焦点"""
        self.show()
        self.activateWindow()
        self.search_box.setFocus()
        self.search_box.clear()
        self.update_list("")
        self.list_widget.viewport().update()

    def gentle_reappear(self):
        """温柔地重新显示窗口，但不抢夺焦点"""
        log("执行温柔的窗口返回...")
        self.search_box.clear()
        self.update_list("")
        self.show() # 只显示，不激活，不设置焦点

    def gentle_reappear(self):
        """温柔地重新显示窗口，但不抢夺焦点"""
        log("执行温柔的窗口返回...")
        self.search_box.clear()
        self.update_list("")
        self.show() # 只显示，不激活，不设置焦点
    
    @Slot(str)
    def update_list(self, text):
        self.list_widget.clear()
        matched_blocks = self.word_manager.find_matches(
            text, self.settings.multi_word_search, self.settings.pinyin_initial_search
        )
        
        for block in matched_blocks:
            item = QListWidgetItem(block['full_content'])
            item.setData(Qt.UserRole, block)
            self.list_widget.addItem(item)
            
        if self.list_widget.count() > 0: self.list_widget.setCurrentRow(0)
    
    @Slot("QListWidgetItem")
    def on_item_selected(self, item):
        self.suggestion_selected.emit(item.text())
        self.hide()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
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
        
        # 获取所有已加载的词库（手动+自动）
        libraries = self.settings.libraries + self.settings.auto_libraries
        if not libraries:
            # 如果没有词库，则禁用此菜单项
            no_library_action = QAction("无可用词库", self)
            no_library_action.setEnabled(False)
            add_to_library_menu.addAction(no_library_action)
        else:
            for lib in libraries:
                lib_path = lib['path']
                # 如果目标路径是剪贴板文件本身，则不显示该选项
                if lib_path == CLIPBOARD_HISTORY_FILE:
                    continue
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
            libraries = self.settings.libraries + self.settings.auto_libraries
            if not libraries:
                no_library_action = QAction("无可用词库", self)
                no_library_action.setEnabled(False)
                add_to_library_menu.addAction(no_library_action)
            else:
                for lib in libraries:
                    lib_path = lib['path']
                    # 如果目标路径是剪贴板文件本身，则不显示该选项
                    if lib_path == CLIPBOARD_HISTORY_FILE:
                        continue
                    lib_name = os.path.basename(lib_path)
                    action = QAction(lib_name, self)
                    action.triggered.connect(lambda _, p=lib_path, i=item: self.controller.move_clipboard_item_to_library(i.text(), p))
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
