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


import builtins
# Dependency Injection
from core.config import *
from ui.search_popup import SearchPopup
from ui.components import HotkeyDialog, DisclaimerDialog, ScrollableMessageBox, get_disclaimer_html_text
from services.hotkey_manager import NativeHotkeyManager
from services.shortcut_listener import ShortcutListener
from PySide6.QtNetwork import QLocalServer, QLocalSocket


# --- 文件监控处理器 (Watchdog) ---
class LibraryChangeHandler(FileSystemEventHandler):
    """使用 Watchdog 处理文件系统事件的处理器。"""
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        log("Watchdog 事件处理器已初始化。")

    def on_any_event(self, event):
        """
        捕获所有文件系统事件 (创建, 删除, 修改, 移动)。
        - 忽略目录事件。
        - 只关心 .md 文件的变化。
        - 触发带有防抖功能的重载调度器。
        """
        if event.is_directory:
            return

        # 不论是源路径还是目标路径（用于移动事件），只要是.md文件就触发
        if event.src_path.endswith('.md') or (hasattr(event, 'dest_path') and event.dest_path.endswith('.md')):
            log(f"Watchdog 检测到事件: {event.event_type} - {event.src_path}")
            # 【关键修复】通过发射信号来安全地通知主线程，而不是直接调用方法
            self.controller.thread_safe_reload_signal.emit()


# --- 主控制器 ---
class MainController(QObject):
    show_popup_signal = Signal()
    hide_popup_signal = Signal()
    # 新增：用于从 watchdog 线程安全地触发重载的信号
    thread_safe_reload_signal = Signal()

    def __init__(self, app, word_manager, settings_manager):
        super().__init__(); self.app = app; self.word_manager = word_manager; self.settings = settings_manager; self.menu = None; self.auto_library_menu = None
        self.popup = SearchPopup(self.word_manager, self.settings)
        self.popup.controller = self # 将 controller 实例传递给 popup
        self.show_popup_signal.connect(self.popup.show_and_focus)
        self.hide_popup_signal.connect(self.popup.hide)
        self.popup.suggestion_selected.connect(self.on_suggestion_selected)
        
        self.hotkey_manager = NativeHotkeyManager(self.settings.hotkey)
        self.hotkey_manager.hotkey_triggered.connect(self.on_hotkey_triggered)
        if self.settings.hotkeys_enabled:
            self.hotkey_manager.start()

        # 新增：初始化快捷码监听器
        self.shortcut_listener = ShortcutListener(self.word_manager)
        self.shortcut_listener.shortcut_matched.connect(self.on_shortcut_matched)
        if self.settings.shortcut_code_enabled:
            self.shortcut_listener.start()

        # --- 新的 Watchdog 文件监控系统 ---
        self.full_reload_timer = QTimer(self)
        self.full_reload_timer.setSingleShot(True)
        self.full_reload_timer.setInterval(500) # 500ms 防抖
        self.full_reload_timer.timeout.connect(self.perform_full_reload)

        # 【关键修复】连接线程安全信号到实际的调度槽
        self.thread_safe_reload_signal.connect(self.schedule_full_reload)

        self.observer = None
        self.start_file_observer()

        # 新增：初始化自动重启定时器
        self.auto_restart_timer = QTimer(self)
        self.auto_restart_timer.timeout.connect(self.perform_restart)
        self.update_auto_restart_timer()

        self.ignore_next_clipboard_change = False # 用于防止记录自己的输出
        self.app.clipboard().dataChanged.connect(self.on_clipboard_changed)

    @Slot()
    def on_clipboard_changed(self):
        """处理剪贴板数据变化信号（事件驱动）"""
        if not self.settings.clipboard_memory_enabled:
            return

        # 检查剪贴板内容是否是文本
        clipboard = self.app.clipboard()
        if not clipboard.mimeData().hasText():
            return

        current_text = clipboard.text()
        
        # 防止程序自己触发的复制操作被重复记录
        if self.ignore_next_clipboard_change:
            log("忽略本次剪贴板变化（由程序自身触发）。")
            self.ignore_next_clipboard_change = False
            return

        # 避免空内容和重复内容
        if not current_text or current_text == getattr(self, "_last_clipboard_text", ""):
            return

        # --- 核心逻辑 ---
        self._last_clipboard_text = current_text
        # 换行符规范化
        normalized_text = '\n'.join(current_text.splitlines())
        log(f"检测到新的剪贴板内容 (事件驱动): '{normalized_text}'")
        
        was_added = self.word_manager.add_to_clipboard_history(normalized_text)
        
        # 如果添加成功且窗口可见，则刷新列表
        if was_added and self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())

    def on_hotkey_triggered(self):
        # 这个信号现在是从 NativeHotkeyManager 线程发出的
        if not self.settings.hotkeys_enabled: return
        if self.popup.isVisible():
            log("热键触发：关闭窗口。"); self.hide_popup_signal.emit()
        else:
            log("热键触发：打开窗口。"); self.show_popup_signal.emit()

    def start_file_observer(self):
        """启动 Watchdog 文件监控线程"""
        if self.observer and self.observer.is_alive():
            log("Watchdog 监控已在运行。")
            return

        self.observer = Observer()
        event_handler = LibraryChangeHandler(self)

        # 监控所有手动添加的词库所在的目录，以及自动加载目录
        watched_dirs = set()
        # 1. 添加自动加载目录
        if os.path.isdir(AUTO_LOAD_DIR):
            watched_dirs.add(AUTO_LOAD_DIR)

        # 2. 添加所有手动词库的父目录
        for lib in self.settings.libraries:
            dir_path = os.path.dirname(lib['path'])
            # 检查目录是否存在且未被添加过
            if os.path.isdir(dir_path):
                watched_dirs.add(dir_path)

        if not watched_dirs:
            log("没有找到有效的词库目录来监控。")
            return

        for path in watched_dirs:
            try:
                self.observer.schedule(event_handler, path, recursive=False) # 非递归，只监控指定目录
                log(f"Watchdog 正在监控目录: {path}")
            except Exception as e:
                log(f"CRITICAL: Watchdog 监控目录 {path} 失败: {e}")

        self.observer.start()
        log("Watchdog 监控线程已启动。")

    def stop_file_observer(self):
        """停止 Watchdog 文件监控线程"""
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=1.5)
            log("Watchdog 监控线程已停止。")
        self.observer = None

    @Slot()
    def schedule_full_reload(self):
        """（防抖）安排一个完整的词库扫描和重载"""
        log("检测到词库相关变化，安排全量重载...")
        self.full_reload_timer.start()

    @Slot()
    def perform_full_reload(self):
        """
        执行完整的词库重新加载流程。
        1. 重新扫描自动加载目录以发现新/删除的文件。
        2. 重新加载所有词库数据（利用缓存）。
        3. 更新快捷码。
        4. 如果UI可见，刷新列表。
        """
        log("--- 开始执行全量重载 ---")
        # 重新扫描自动加载目录，如果发生变化，则重启监视器
        if self.scan_and_update_auto_libraries():
             self.stop_file_observer()
             self.start_file_observer()

        self.word_manager.reload_all() # 核心：加载所有词库
        if self.shortcut_listener and self.settings.shortcut_code_enabled:
            self.shortcut_listener.update_shortcuts()
        if self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())
        # 重新构建菜单（特别是自动加载菜单）以反映变化
        self.rebuild_auto_library_menu()
        log("--- 全量重载完成 ---")
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
                # 输出父级（使用解析过的纯净文本）+ 子内容
                first_line = found_block['parent']
                content_to_paste = '\n'.join([first_line] + found_block['raw_lines'][1:])
        else:
            # 如果找不到块，作为备用方案，按旧方式处理
            content_to_paste = text.replace('- ', '', 1)

        self.ignore_next_clipboard_change = True
        pyperclip.copy(content_to_paste)
        log(f"已复制处理后的内容到剪贴板，并设置忽略标志。")
        
        # 无论何种模式，都执行粘贴
        QTimer.singleShot(150, self.perform_paste)

    def perform_paste(self):
        """
        根据用户设置，通过 PowerShell 执行不同的粘贴操作。
        【已加固】增加了对 QProcess.startDetached 的异常捕获。
        """
        mode = self.settings.paste_mode
        log(f"准备执行粘贴，模式: {mode}")

        ps_command = ""
        if mode == 'ctrl_v':
            ps_command = (
                "powershell.exe -WindowStyle Hidden -Command "
                "\"Start-Sleep -Milliseconds 100; " # 稍微缩短延迟
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v')\""
            )
        elif mode == 'ctrl_shift_v':
            ps_command = (
                "powershell.exe -WindowStyle Hidden -Command "
                "\"Start-Sleep -Milliseconds 100; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('+^v')\""
            )
        elif mode == 'typing':
            ps_command = (
                "powershell.exe -WindowStyle Hidden -Command "
                "\"Start-Sleep -Milliseconds 100; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$clipboardText = Get-Clipboard -Raw; " # 使用 -Raw 提高兼容性
                "$escapedText = $clipboardText -replace '([\\+\\^\\%\\~\\(\\)\\[\\]\\{\\}])', '{$1}'; "
                "[System.Windows.Forms.SendKeys]::SendWait($escapedText)\""
            )

        if ps_command:
            try:
                # QProcess.startDetached 返回一个布尔值，指示是否成功启动
                success = QProcess.startDetached(ps_command)
                if success:
                    log(f"PowerShell 粘贴命令 ({mode}) 已成功派发。")
                else:
                    log(f"CRITICAL: PowerShell 粘贴命令 ({mode}) 派发失败，startDetached 返回 False。")
            except Exception as e:
                # 捕获启动过程中的潜在异常
                log(f"CRITICAL: 启动 PowerShell 粘贴进程时发生严重错误: {e}")
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
                self.schedule_full_reload()
                self.popup.search_box.clear()
            else:
                QMessageBox.warning(self.popup, "错误", f"向 {os.path.basename(target_path)} 添加词条失败！")
    
    @Slot(str)
    def edit_entry(self, original_content):
        # Find the block to get its properties
        found_block = None
        search_pool = self.word_manager.word_blocks + self.word_manager.clipboard_history
        for block in search_pool:
            if block['full_content'] == original_content:
                found_block = block
                break
        
        if not found_block:
            QMessageBox.warning(self.popup, "错误", "找不到要编辑的词条。")
            return

        is_clipboard = found_block.get('source_path') == CLIPBOARD_HISTORY_FILE
        source_path = found_block.get('source_path')
        
        source = self.word_manager.get_source_by_path(source_path)

        if not source:
            QMessageBox.warning(self.popup, "错误", "找不到词条的来源文件对象。")
            return

        dialog = EditDialog(self.popup, original_content, THEMES[self.settings.theme], self.settings.font_size)
        if dialog.exec():
            new_content = dialog.get_text()
            if source.update_entry(original_content, new_content):
                # 统一调用全量重载，它会处理缓存、快捷码和UI刷新
                self.schedule_full_reload()
                
                if self.popup.isVisible():
                    self.popup.update_list(self.popup.search_box.text())
            else:
                QMessageBox.warning(self.popup, "错误", f"更新 {os.path.basename(source.file_path)} 中的词条失败！")

    @Slot(str)
    def delete_entry(self, content):
        # Find the block to get its properties
        found_block = None
        search_pool = self.word_manager.word_blocks + self.word_manager.clipboard_history
        for block in search_pool:
            if block['full_content'] == content:
                found_block = block
                break

        if not found_block:
            QMessageBox.warning(self.popup, "错误", "找不到要删除的词条。")
            return

        is_clipboard = found_block.get('source_path') == CLIPBOARD_HISTORY_FILE
        source_path = found_block.get('source_path')
 
        source = self.word_manager.get_source_by_path(source_path)

        if not source:
            QMessageBox.warning(self.popup, "错误", "找不到词条的来源文件对象。")
            return

        dialog = ScrollableMessageBox(
            parent=self.popup,
            title="确认删除",
            text=f"确定要从 <b>{os.path.basename(source.file_path)}</b> 中删除以下词条吗？<br><br>{content.replace(chr(10), '<br>')}",
            theme=THEMES[self.settings.theme],
            font_size=self.settings.font_size
        )
        
        if dialog.exec() == QDialog.Accepted:
            if source.delete_entry(content):
                # 统一调用全量重载
                self.schedule_full_reload()
                
                if self.popup.isVisible():
                    self.popup.update_list(self.popup.search_box.text())
            else:
                QMessageBox.warning(self.popup, "错误", f"从 {os.path.basename(source.file_path)} 删除词条失败！")

    def move_clipboard_item_to_library(self, item_content, target_path):
        """将剪贴板条目移动到指定的词库"""
        # 1. 提取纯文本
        text_to_add = item_content.replace('- ', '', 1).strip()

        # 2. 添加到目标词库
        source = self.word_manager.get_source_by_path(target_path)
        if source and source.add_entry(f"- {text_to_add}"):
            log(f"已将 '{text_to_add}' 添加到 {os.path.basename(target_path)}")
            self.schedule_full_reload() # 安排重载来更新所有状态

            # 3. 从剪贴板历史中删除
            if self.word_manager.clipboard_source.delete_entry(item_content):
                log(f"已从剪贴板历史中删除 '{item_content}'")
                # 4. 刷新
                self.word_manager.load_clipboard_history()
                if self.popup.isVisible():
                    self.popup.update_list(self.popup.search_box.text())
            else:
                log(f"从剪贴板历史删除 '{item_content}' 失败")
                QMessageBox.warning(self.popup, "警告", "条目已添加到新词库，但从剪贴板历史中删除失败。")
        else:
            QMessageBox.warning(self.popup, "错误", f"无法将条目添加到 {os.path.basename(target_path)}")

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
            self.perform_full_reload() # 立即执行重载，因为这是用户直接操作
            self.rebuild_library_menu()

    @Slot(str)
    def remove_library(self, path):
        self.settings.libraries = [lib for lib in self.settings.libraries if lib.get('path') != path]
        self.settings.save()
        self.perform_full_reload() # 立即执行重载
        self.rebuild_library_menu()

    @Slot(str)
    def toggle_library_enabled(self, path):
        for lib in self.settings.libraries:
            if lib.get('path') == path:
                lib['enabled'] = not lib.get('enabled', True)
                break
        self.settings.save()
        self.perform_full_reload() # 立即执行重载
        self.rebuild_library_menu()

    @Slot(str)
    def toggle_auto_library_enabled(self, path):
        for lib in self.settings.auto_libraries:
            if lib.get('path') == path:
                lib['enabled'] = not lib.get('enabled', True)
                break
        self.settings.save()
        self.perform_full_reload() # 立即执行重载
        self.rebuild_auto_library_menu()

    def open_auto_load_dir(self):
        try:
            webbrowser.open(AUTO_LOAD_DIR)
            log(f"尝试打开自动加载文件夹: {AUTO_LOAD_DIR}")
        except Exception as e:
            log(f"打开自动加载文件夹失败: {e}")
            QMessageBox.warning(self.popup, "错误", f"无法打开文件夹路径：\n{AUTO_LOAD_DIR}\n\n错误: {e}")

    def rebuild_auto_library_menu(self):
        self.auto_library_menu.clear()
        
        open_dir_action = QAction("打开-md词库文件夹", self.auto_library_menu)
        open_dir_action.triggered.connect(self.open_auto_load_dir)
        self.auto_library_menu.addAction(open_dir_action)
        self.auto_library_menu.addSeparator()

        if not self.settings.auto_libraries:
            no_lib_action = QAction("无自动加载词库", self.auto_library_menu)
            no_lib_action.setEnabled(False)
            self.auto_library_menu.addAction(no_lib_action)
        else:
            for lib in self.settings.auto_libraries:
                lib_path = lib.get('path')
                lib_name = os.path.basename(lib_path)
                action = QAction(lib_name, self.auto_library_menu)
                action.setCheckable(True)
                action.setChecked(lib.get('enabled', True))
                action.triggered.connect(lambda _, p=lib_path: self.toggle_auto_library_enabled(p))
                self.auto_library_menu.addAction(action)

    @Slot()
    def schedule_auto_lib_scan(self):
        """安排一个延迟的自动目录扫描，以避免在文件写入完成前触发。"""
        log("检测到自动加载目录变化，安排扫描...")
        self.auto_scan_timer.start()

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

        # 这个逻辑不再需要，因为 auto_library_menu 现在是顶级菜单

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

    @Slot(str, str)
    def on_shortcut_matched(self, full_content, shortcut_code):
        """处理快捷码匹配成功的事件"""
        log(f"主控制器收到快捷码匹配信号: {shortcut_code}")
        
        # 1. 删除用户输入的快捷码
        for _ in range(len(shortcut_code)):
            self.shortcut_listener.keyboard_controller.press(keyboard.Key.backspace)
            self.shortcut_listener.keyboard_controller.release(keyboard.Key.backspace)

        # 2. 粘贴内容 (复用 on_suggestion_selected 的逻辑)
        self.on_suggestion_selected(full_content)

    @Slot()
    def cleanup_and_exit(self):
        log("开始执行程序清理...")
        self.hotkey_manager.stop()
        if self.shortcut_listener:
            self.shortcut_listener.stop() # 退出时停止快捷码监听
        self.stop_file_observer() # 确保停止 watchdog
        log("所有监听器已停止，程序准备退出。")

    @Slot()
    def set_paste_mode(self, mode):
        """设置新的粘贴模式并保存"""
        if self.settings.paste_mode != mode:
            self.settings.paste_mode = mode
            self.settings.save()
            log(f"粘贴模式已切换为: {mode}")

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
    def toggle_shortcut_code_enabled(self):
        """切换快捷码功能的启用状态"""
        self.settings.shortcut_code_enabled = not self.settings.shortcut_code_enabled
        self.settings.save()
        if self.settings.shortcut_code_enabled:
            self.shortcut_listener.start()
            log("快捷码功能已启用。")
        else:
            self.shortcut_listener.stop()
            log("快捷码功能已禁用。")
        
        if hasattr(self, 'toggle_shortcut_code_action'):
            self.toggle_shortcut_code_action.setChecked(self.settings.shortcut_code_enabled)

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
    def toggle_highlight_matches(self):
        """切换匹配高亮的启用状态"""
        self.settings.highlight_matches = not self.settings.highlight_matches
        self.settings.save()
        log(f"匹配高亮: {'开启' if self.settings.highlight_matches else '关闭'}")
        if hasattr(self, 'highlight_matches_action'):
            self.highlight_matches_action.setChecked(self.settings.highlight_matches)
        # 强制刷新列表以立即看到效果
        if self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())

    @Slot()
    def toggle_pinyin_initial_search(self):
        self.settings.pinyin_initial_search = not self.settings.pinyin_initial_search
        self.settings.save()
        log(f"拼音首字母匹配: {'开启' if self.settings.pinyin_initial_search else '关闭'}")
        if hasattr(self, 'pinyin_search_action'):
            self.pinyin_search_action.setChecked(self.settings.pinyin_initial_search)

    @Slot()
    def set_hotkey(self):
        """弹出对话框以设置新的快捷键"""
        dialog = HotkeyDialog(
            parent=self.popup,
            current_hotkey=self.settings.hotkey,
            theme=THEMES[self.settings.theme],
            font_size=self.settings.font_size
        )
        if dialog.exec():
            new_hotkey = dialog.get_hotkey()
            if new_hotkey and new_hotkey != self.settings.hotkey:
                self.settings.hotkey = new_hotkey
                self.settings.save()
                self.hotkey_manager.reregister(new_hotkey)
                log(f"快捷键已更新为: {new_hotkey}")
                QMessageBox.information(None, "成功", f"快捷键已更新为 {new_hotkey}！\n请注意，某些组合键可能被系统或其他程序占用。")

    def apply_menu_theme(self, menu=None):
        target_menu = menu if menu else self.menu
        if not target_menu: return
        
        theme = THEMES[self.settings.theme]
        # 使用更具体的选择器确保子菜单继承样式，并增加部分边距修复
        target_menu.setStyleSheet(f"""
            QMenu {{
                background-color: {theme['bg_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 4px;
                color: {theme['text_color']};
                font-size: {self.settings.font_size}px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px 6px 24px;
                border-radius: 4px;
                background-color: transparent;
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
                margin: 4px 0px;
            }}
            QMenu::indicator {{
                width: 13px;
                height: 13px;
                left: 6px;
            }}
        """)

    # --- 新增：剪贴板菜单相关方法 ---
    @Slot()
    def toggle_clipboard_memory(self):
        self.settings.clipboard_memory_enabled = not self.settings.clipboard_memory_enabled
        self.settings.save()
        # self.update_clipboard_monitor_status()
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

    def show_disclaimer(self):
        """显示重要声明与协议对话框"""
        dialog = DisclaimerDialog(self.popup, THEMES[self.settings.theme], self.settings.font_size)
        # 对于已经同意过的用户，只显示信息，不提供“同意/不同意”选项
        dialog.agree_checkbox.setChecked(True)
        dialog.agree_checkbox.setVisible(False)
        dialog.agree_button.setText("关闭")
        dialog.disagree_button.setVisible(False)
        dialog.exec()

    def open_help_docs(self):
        """打开项目主页与帮助"""
        target_url = "https://github.com/msjsc001/QuickKV"
        try:
            webbrowser.open(target_url)
            log(f"已打开项目主页: {target_url}")
        except Exception as e:
            log(f"打开项目主页失败: {e}")
            QMessageBox.warning(self.popup, "错误", f"无法打开链接：\n{target_url}\n\n错误: {e}")  

    def scan_and_update_auto_libraries(self):
        """
        扫描自动加载文件夹，同步词库列表并保存状态。
        返回一个布尔值，指示列表是否发生了变化。
        """
        log("开始扫描自动加载词库文件夹...")
        if not os.path.isdir(AUTO_LOAD_DIR):
            if self.settings.auto_libraries:
                log(f"自动加载目录不存在: {AUTO_LOAD_DIR}，清空配置。")
                self.settings.auto_libraries = []
                self.settings.save()
                return True # 发生了变化
            return False

        try:
            found_files = {os.path.join(AUTO_LOAD_DIR, f) for f in os.listdir(AUTO_LOAD_DIR) if f.endswith('.md')}
        except Exception as e:
            log(f"扫描自动加载目录时出错: {e}")
            return False

        existing_paths = {lib['path'] for lib in self.settings.auto_libraries}
        
        new_files = found_files - existing_paths
        removed_files = existing_paths - found_files
        
        if not new_files and not removed_files:
            return False # 无变化

        # 如果有变化，则进行处理
        if new_files:
            for path in new_files:
                self.settings.auto_libraries.append({"path": path, "enabled": True})
                log(f"发现并添加新自动词库: {os.path.basename(path)}")

        if removed_files:
            self.settings.auto_libraries = [lib for lib in self.settings.auto_libraries if lib['path'] not in removed_files]
            for path in removed_files:
                log(f"移除不存在的自动词库: {os.path.basename(path)}")
        
        self.settings.save()
        return True # 确认发生了变化
