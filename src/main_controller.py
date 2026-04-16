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
import time
from ctypes import wintypes
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QSystemTrayIcon, QMenu, QSizeGrip,
                             QGraphicsDropShadowEffect, QPushButton,
                             QInputDialog, QMessageBox, QStyledItemDelegate, QStyle, QFileDialog,
                             QCheckBox, QWidgetAction, QScrollArea, QLabel, QFrame, QDialog)
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
from core.template_renderer import TemplateRenderer, TemplateRenderError
from ui.search_popup import SearchPopup
from ui.components import HotkeyDialog, DisclaimerDialog, ScrollableMessageBox, get_disclaimer_html_text, EditDialog, TemplateInputDialog
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

    MODIFIER_VKS = {
        'ctrl': 0x11,
        'shift': 0x10,
        'alt': 0x12,
        'lwin': 0x5B,
        'rwin': 0x5C,
    }

    def __init__(self, app, word_manager, settings_manager, ranking_state_manager=None, template_renderer=None):
        super().__init__(); self.app = app; self.word_manager = word_manager; self.settings = settings_manager; self.ranking_state = ranking_state_manager; self.template_renderer = template_renderer or TemplateRenderer(); self.menu = None; self.auto_library_menu = None
        self.popup = SearchPopup(self.word_manager, self.settings)
        self.popup.controller = self # 将 controller 实例传递给 popup
        self.show_popup_signal.connect(self.popup.show_and_focus)
        self.hide_popup_signal.connect(self.popup.hide)
        self.popup.suggestion_selected.connect(self.on_suggestion_selected)
        
        self.hotkey_manager = NativeHotkeyManager(self.settings.hotkey)
        self.hotkey_manager.hotkey_triggered.connect(self.on_hotkey_triggered)
        self.hotkey_manager.hotkey_registration_failed.connect(self.on_hotkey_registration_failed)
        if self.settings.hotkeys_enabled:
            self.hotkey_manager.start()
            
        # 初始化连续字符串盲打触发器
        self.hotkey_manager.update_string_trigger(
            self.settings.string_trigger_enabled, 
            self.settings.string_trigger_str
        )

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

        # 初始化剪贴板定时清除
        self.clipboard_timestamps = {}
        self.sync_clipboard_timestamps()
        
        self.clipboard_clear_timer = QTimer(self)
        self.clipboard_clear_timer.timeout.connect(self.check_clipboard_auto_clear)
        self.clipboard_clear_timer.start(60000) # 每分钟检查一次

        self.ignore_next_clipboard_change = False # 用于防止记录自己的输出
        self.app.clipboard().dataChanged.connect(self.on_clipboard_changed)
        self.user32 = ctypes.windll.user32
        self.quickkv_pid = os.getpid()
        self.last_popup_target_hwnd = 0
        self.pending_paste_request = None
        self.popup_paste_grace_ms = 50
        self.shortcut_paste_grace_ms = 50
        self.shortcut_erase_grace_ms = 25
        self.paste_poll_interval_ms = 20
        self.paste_timeout_ms = 650
        self.pending_paste_timer = QTimer(self)
        self.pending_paste_timer.setInterval(self.paste_poll_interval_ms)
        self.pending_paste_timer.timeout.connect(self._poll_pending_paste)

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
        
        if was_added:
            self.sync_clipboard_timestamps(current_time=time.time())
            if self.popup.isVisible():
                self.popup.update_list(self.popup.search_box.text())

    def sync_clipboard_timestamps(self, current_time=None):
        """将时间戳与当前剪贴板历史的完整块内容对齐。"""
        if current_time is None:
            current_time = time.time()

        existing_timestamps = getattr(self, 'clipboard_timestamps', {})
        synced_timestamps = {}
        for block in self.word_manager.clipboard_history:
            key = block['full_content']
            synced_timestamps[key] = existing_timestamps.get(key, current_time)

        self.clipboard_timestamps = synced_timestamps

    def _start_detached_process(self, program, arguments):
        """兼容 PySide6 不同返回值形态的 detached 启动封装。"""
        result = QProcess.startDetached(program, arguments)
        if isinstance(result, tuple):
            success, pid = result
        else:
            success, pid = bool(result), None
        return bool(success), pid

    def _get_foreground_hwnd(self):
        try:
            return int(self.user32.GetForegroundWindow())
        except Exception:
            return 0

    def _get_window_process_id(self, hwnd):
        if not hwnd:
            return 0

        pid = wintypes.DWORD()
        try:
            self.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        except Exception:
            return 0
        return int(pid.value)

    def _capture_target_hwnd(self):
        hwnd = self._get_foreground_hwnd()
        log(f"捕获目标窗口 hwnd={hwnd}")
        return hwnd

    def _is_quickkv_foreground(self, hwnd):
        return bool(hwnd) and self._get_window_process_id(hwnd) == self.quickkv_pid

    def _get_pressed_modifiers(self):
        pressed = []
        for name, vk in self.MODIFIER_VKS.items():
            try:
                if self.user32.GetAsyncKeyState(vk) & 0x8000:
                    pressed.append(name)
            except Exception:
                continue
        return pressed

    def schedule_paste(self, target_hwnd, origin='popup', grace_ms=None):
        if grace_ms is None:
            grace_ms = self.popup_paste_grace_ms if origin == 'popup' else self.shortcut_paste_grace_ms

        self.pending_paste_request = {
            'target_hwnd': int(target_hwnd or 0),
            'origin': origin,
            'scheduled_at': time.monotonic(),
            'grace_ms': int(grace_ms),
            'stable_hits': 0,
            'last_foreground_hwnd': None,
        }
        if not self.pending_paste_timer.isActive():
            self.pending_paste_timer.start()

        log(
            f"已调度粘贴 | origin={origin} target_hwnd={int(target_hwnd or 0)} "
            f"grace_ms={int(grace_ms)} timeout_ms={self.paste_timeout_ms}"
        )

    def _poll_pending_paste(self):
        request = self.pending_paste_request
        if not request:
            self.pending_paste_timer.stop()
            return

        foreground_hwnd = self._get_foreground_hwnd()
        elapsed_ms = int((time.monotonic() - request['scheduled_at']) * 1000)

        if request['target_hwnd'] and foreground_hwnd == request['target_hwnd']:
            if request['last_foreground_hwnd'] == foreground_hwnd:
                request['stable_hits'] += 1
            else:
                request['stable_hits'] = 1
        else:
            request['stable_hits'] = 0
        request['last_foreground_hwnd'] = foreground_hwnd

        pressed_modifiers = self._get_pressed_modifiers()
        quickkv_foreground = self._is_quickkv_foreground(foreground_hwnd)
        grace_ready = elapsed_ms >= request['grace_ms']
        target_stable = request['target_hwnd'] and request['stable_hits'] >= 2

        if grace_ready and not quickkv_foreground and not pressed_modifiers and target_stable:
            self.pending_paste_request = None
            self.pending_paste_timer.stop()
            self.perform_paste_now(
                origin=request['origin'],
                target_hwnd=request['target_hwnd'],
                foreground_hwnd=foreground_hwnd,
                elapsed_ms=elapsed_ms,
                forced=False,
                modifiers=pressed_modifiers,
            )
            return

        if elapsed_ms >= self.paste_timeout_ms:
            self.pending_paste_request = None
            self.pending_paste_timer.stop()
            self.perform_paste_now(
                origin=request['origin'],
                target_hwnd=request['target_hwnd'],
                foreground_hwnd=foreground_hwnd,
                elapsed_ms=elapsed_ms,
                forced=True,
                modifiers=pressed_modifiers,
            )

    def on_hotkey_triggered(self):
        # 这个信号现在是从 NativeHotkeyManager 线程发出的
        if not self.settings.hotkeys_enabled: return
        if self.popup.isVisible():
            log("热键触发：关闭窗口。"); self.hide_popup_signal.emit()
        else:
            self.last_popup_target_hwnd = self._capture_target_hwnd()
            log(f"热键触发：打开窗口。目标 hwnd={self.last_popup_target_hwnd}")
            self.show_popup_signal.emit()

    @Slot(str)
    def on_hotkey_registration_failed(self, message):
        """统一处理系统级热键注册失败，避免 UI 与配置状态脱节。"""
        self.hotkey_manager.stop_hotkey()
        if self.settings.hotkeys_enabled:
            self.settings.hotkeys_enabled = False
            self.settings.save()
        if hasattr(self, 'toggle_hotkeys_action'):
            self.toggle_hotkeys_action.setChecked(False)
        log(f"CRITICAL: {message}")
        QMessageBox.warning(self.popup, "热键注册失败", message)

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
            if lib.get('kind', 'file') == 'folder':
                dir_path = lib.get('path')
            else:
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
        self.sync_clipboard_timestamps()
        if self.shortcut_listener and self.settings.shortcut_code_enabled:
            self.shortcut_listener.update_shortcuts()
        if self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())
        # 重新构建菜单（特别是自动加载菜单）以反映变化
        self.rebuild_auto_library_menu()
        log("--- 全量重载完成 ---")
    def _find_block_by_full_content(self, text):
        search_pool = self.word_manager.clipboard_history + self.word_manager.word_blocks
        for block in search_pool:
            if block['full_content'] == text:
                return block
        return None

    def _build_output_content(self, selected_text, found_block):
        if not found_block:
            return selected_text.replace('- ', '', 1)

        if found_block['exclude_parent']:
            return '\n'.join(found_block['raw_lines'][1:])

        first_line = found_block['parent']
        return '\n'.join([first_line] + found_block['raw_lines'][1:])

    @Slot(str)
    def on_suggestion_selected(self, text, target_hwnd=None, origin='popup'):
        log(f"已选择词条块: '{text}'")
        found_block = self._find_block_by_full_content(text)
        content_to_paste = self._build_output_content(text, found_block)

        rendered_content = self.render_template_output(content_to_paste)
        if rendered_content is None:
            log("模板输出已取消或失败，本次不执行粘贴。")
            return

        content_to_paste = rendered_content
        self.record_entry_usage(found_block)

        self.ignore_next_clipboard_change = True
        pyperclip.copy(content_to_paste)
        log("已复制处理后的内容到剪贴板，并设置忽略标志。")

        resolved_target_hwnd = int(target_hwnd or self.last_popup_target_hwnd or 0)
        self.schedule_paste(resolved_target_hwnd, origin=origin)

    def perform_paste(self):
        self.perform_paste_now(origin='manual')

    def perform_paste_now(self, origin='manual', target_hwnd=0, foreground_hwnd=0, elapsed_ms=0, forced=False, modifiers=None):
        """
        根据用户设置，通过 PowerShell 执行不同的粘贴操作。
        保持默认主链尽量窄，只保留全局手动模式。
        """
        mode = self.settings.paste_mode
        modifiers = modifiers or []
        log(
            f"准备执行粘贴 | origin={origin} mode={mode} target_hwnd={int(target_hwnd or 0)} "
            f"foreground_hwnd={int(foreground_hwnd or 0)} elapsed_ms={elapsed_ms} "
            f"forced={forced} modifiers={','.join(modifiers) or 'none'}"
        )

        ps_script = ""
        if mode == PASTE_MODE_CTRL_V:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v')"
            )
        elif mode == PASTE_MODE_CTRL_SHIFT_V:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('+^v')"
            )
        elif mode == PASTE_MODE_TYPING:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$clipboardText = Get-Clipboard -Raw; "
                "$escapedText = $clipboardText -replace '([\\+\\^\\%\\~\\(\\)\\[\\]\\{\\}])', '{$1}'; "
                "[System.Windows.Forms.SendKeys]::SendWait($escapedText)"
            )

        if not ps_script:
            log(f"CRITICAL: 未识别的粘贴模式: {mode}")
            return

        try:
            success, pid = self._start_detached_process(
                "powershell.exe",
                ["-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script]
            )
            if success:
                log(f"PowerShell 粘贴命令 ({mode}) 已成功派发。PID={pid}")
            else:
                log(f"CRITICAL: PowerShell 粘贴命令 ({mode}) 派发失败，startDetached 返回 False。")
        except Exception as e:
            log(f"CRITICAL: 启动 PowerShell 粘贴进程时发生严重错误: {e}")

    def record_entry_usage(self, block):
        """仅记录真正完成输出的普通词条使用行为。"""
        if not block or block.get('is_clipboard'):
            return
        if not self.ranking_state:
            return

        entry_id = block.get('entry_id')
        if not entry_id:
            return

        self.ranking_state.record_use(entry_id)
        self.word_manager.refresh_ranking_metadata()

    def toggle_block_favorite(self, block):
        """切换普通词条的收藏状态，并刷新当前结果。"""
        if not block or block.get('is_clipboard'):
            return
        if not self.ranking_state:
            return

        entry_id = block.get('entry_id')
        if not entry_id:
            return

        new_state = self.ranking_state.toggle_favorite(entry_id)
        self.word_manager.refresh_ranking_metadata()
        state_text = "已收藏" if new_state else "已取消收藏"
        log(f"{state_text}: {block.get('parent', '')}")
        if self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())

    def render_template_output(self, content_to_paste):
        """在最终输出前执行中文模板渲染。"""
        if not self.template_renderer or not self.template_renderer.contains_template(content_to_paste):
            return content_to_paste

        try:
            prepared_template = self.template_renderer.prepare(content_to_paste)
        except TemplateRenderError as e:
            QMessageBox.warning(self.popup, "模板错误", str(e))
            return None

        input_values = {}
        if prepared_template.input_fields:
            dialog = TemplateInputDialog(
                self.popup,
                prepared_template.input_fields,
                THEMES[self.settings.theme],
                self.settings.font_size
            )
            if dialog.exec() != QDialog.Accepted:
                return None
            input_values = dialog.get_values()

        clipboard = self.app.clipboard()
        clipboard_text = clipboard.text() if clipboard.mimeData().hasText() else ""

        try:
            return self.template_renderer.render(
                prepared_template,
                input_values=input_values,
                clipboard_text=clipboard_text,
            )
        except TemplateRenderError as e:
            QMessageBox.warning(self.popup, "模板错误", str(e))
            return None

    @Slot(str, str)
    def add_entry(self, text, target_path=None):
        # 如果没有指定目标词库，则弹出选择框
        if target_path is None:
            writable_targets = self.get_writable_library_targets()
            if len(writable_targets) > 1:
                target_labels = [target['label'] for target in writable_targets]
                selected_label, ok = QInputDialog.getItem(self.popup, "选择词库", "请选择要添加到的词库:", target_labels, 0, False)
                if ok and selected_label:
                    target_path = next((target['path'] for target in writable_targets if target['label'] == selected_label), None)
                else:
                    return # 用户取消
            elif len(writable_targets) == 1:
                target_path = writable_targets[0]['path']
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
                self.sync_clipboard_timestamps(current_time=time.time())
                if self.popup.isVisible():
                    self.popup.update_list(self.popup.search_box.text())
            else:
                log(f"从剪贴板历史删除 '{item_content}' 失败")
                QMessageBox.warning(self.popup, "警告", "条目已添加到新词库，但从剪贴板历史中删除失败。")
        else:
            QMessageBox.warning(self.popup, "错误", f"无法将条目添加到 {os.path.basename(target_path)}")

    def restart_file_observer(self):
        """在手动词库结构变化后重建目录监控。"""
        self.stop_file_observer()
        self.start_file_observer()

    def _manual_library_exists(self, path, kind):
        norm_path = normalize_library_path(path)
        for lib in self.settings.libraries:
            lib_kind = lib.get('kind', 'file')
            lib_path = lib.get('path')
            if lib_kind == kind and lib_path and normalize_library_path(lib_path) == norm_path:
                return True
        return False

    def get_writable_library_targets(self):
        """返回所有真实可写入的 md 文件目标。"""
        targets = []
        seen_paths = set()
        configured_libraries = self.settings.libraries + self.settings.auto_libraries

        for lib in configured_libraries:
            lib_path = lib.get('path')
            lib_kind = lib.get('kind', 'file')
            if lib_kind == 'folder':
                candidate_paths = list_eligible_md_files(lib_path)
            else:
                candidate_paths = [os.path.abspath(lib_path)] if is_eligible_library_file(lib_path) else []

            for candidate_path in candidate_paths:
                norm_path = normalize_library_path(candidate_path)
                if norm_path in seen_paths:
                    continue
                seen_paths.add(norm_path)
                parent_dir = os.path.dirname(candidate_path)
                targets.append({
                    'label': f"{os.path.basename(candidate_path)} [{parent_dir}]",
                    'path': candidate_path,
                })

        return sorted(targets, key=lambda item: item['label'].lower())

    @Slot()
    def add_library(self):
        file_path, _ = QFileDialog.getOpenFileName(self.popup, "选择一个词库文件", "", "Markdown 文件 (*.md)")
        if file_path:
            file_path = os.path.abspath(file_path)
            if not is_eligible_library_file(file_path):
                QMessageBox.warning(self.popup, "错误", "所选文件不是可用的 md 词库文件。")
                return

            if self._manual_library_exists(file_path, 'file'):
                QMessageBox.information(self.popup, "提示", "该词库已在列表中。")
                return
            
            self.settings.libraries.append({"path": file_path, "enabled": True, "kind": "file"})
            self.settings.save()
            self.restart_file_observer()
            self.perform_full_reload() # 立即执行重载，因为这是用户直接操作
            self.rebuild_library_menu()

    @Slot()
    def add_library_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self.popup, "选择一个词库文件夹")
        if folder_path:
            folder_path = os.path.abspath(folder_path)
            if self._manual_library_exists(folder_path, 'folder'):
                QMessageBox.information(self.popup, "提示", "该词库文件夹已在列表中。")
                return

            self.settings.libraries.append({"path": folder_path, "enabled": True, "kind": "folder"})
            self.settings.save()
            self.restart_file_observer()
            self.perform_full_reload()
            self.rebuild_library_menu()

            if not list_eligible_md_files(folder_path):
                QMessageBox.information(
                    self.popup,
                    "提示",
                    "该文件夹当前没有可加载的 .md 文件，已保留在配置中；后续新增 .md 文件会自动生效。"
                )

    @Slot(str)
    def remove_library(self, path):
        norm_path = normalize_library_path(path)
        self.settings.libraries = [
            lib for lib in self.settings.libraries
            if not lib.get('path') or normalize_library_path(lib.get('path')) != norm_path
        ]
        self.settings.save()
        self.restart_file_observer()
        self.perform_full_reload() # 立即执行重载
        self.rebuild_library_menu()

    @Slot(str)
    def toggle_library_enabled(self, path):
        norm_path = normalize_library_path(path)
        for lib in self.settings.libraries:
            lib_path = lib.get('path')
            if lib_path and normalize_library_path(lib_path) == norm_path:
                lib['enabled'] = not lib.get('enabled', True)
                break
        self.settings.save()
        self.restart_file_observer()
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
        
        add_folder_action = QAction("添加MD词库文件夹", self.library_menu)
        add_folder_action.triggered.connect(self.add_library_folder)
        self.library_menu.addAction(add_folder_action)

        add_file_action = QAction("添加MD文件", self.library_menu)
        add_file_action.triggered.connect(self.add_library)
        self.library_menu.addAction(add_file_action)
        self.library_menu.addSeparator()

        for lib in self.settings.libraries:
            lib_path = lib.get('path')
            lib_kind = lib.get('kind', 'file')
            raw_name = os.path.basename(lib_path) or lib_path
            lib_name = f"[文件夹] {raw_name}" if lib_kind == 'folder' else raw_name
            
            # 主操作行
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(5, 5, 5, 5)
            
            checkbox = QCheckBox(lib_name)
            checkbox.setChecked(lib.get('enabled', True))
            checkbox.toggled.connect(lambda _, p=lib_path: self.toggle_library_enabled(p))
            
            open_button = QPushButton("📂") # 打开文件夹图标
            open_button.setFixedSize(20, 20)
            open_button.setToolTip("打开词库文件夹" if lib_kind == 'folder' else "打开词库文件所在文件夹")
            open_button.clicked.connect(lambda _, p=lib_path, k=lib_kind: self.open_library_location(p, k))

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
    def open_library_location(self, path, kind='file'):
        """在文件浏览器中打开指定词库的对应位置。"""
        try:
            target = path if kind == 'folder' else os.path.dirname(path)
            webbrowser.open(target)
            log(f"尝试打开词库位置: {target}")
        except Exception as e:
            log(f"打开词库位置失败: {e}")
            QMessageBox.warning(self.popup, "错误", f"无法打开文件路径：\n{path}\n\n错误: {e}")

    @Slot(str, str)
    def on_shortcut_matched(self, full_content, shortcut_code):
        """处理快捷码匹配成功的事件"""
        target_hwnd = self._capture_target_hwnd()
        log(f"主控制器收到快捷码匹配信号: {shortcut_code} | target_hwnd={target_hwnd}")
        QTimer.singleShot(
            self.shortcut_erase_grace_ms,
            lambda content=full_content, code=shortcut_code, hwnd=target_hwnd: self._commit_shortcut_match(content, code, hwnd)
        )

    def _commit_shortcut_match(self, full_content, shortcut_code, target_hwnd):
        for _ in range(len(shortcut_code)):
            self.shortcut_listener.keyboard_controller.press(keyboard.Key.backspace)
            self.shortcut_listener.keyboard_controller.release(keyboard.Key.backspace)

        self.on_suggestion_selected(full_content, target_hwnd=target_hwnd, origin='shortcut')

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
        if mode not in SUPPORTED_PASTE_MODES:
            return
        if self.settings.paste_mode != mode:
            self.settings.paste_mode = mode
            self.settings.save()
            log(f"粘贴模式已切换为: {mode}")

    @Slot()
    def toggle_hotkeys_enabled(self):
        enable_hotkeys = not self.settings.hotkeys_enabled
        if enable_hotkeys:
            success, normalized_hotkey, error_message = self.hotkey_manager.reregister(self.settings.hotkey)
            if not success:
                if hasattr(self, 'toggle_hotkeys_action'):
                    self.toggle_hotkeys_action.setChecked(False)
                QMessageBox.warning(self.popup, "热键注册失败", error_message)
                return

            self.settings.hotkeys_enabled = True
            self.settings.hotkey = normalized_hotkey
            log(f"快捷键已启用: {normalized_hotkey}")
        else:
            self.hotkey_manager.unregister_all()
            self.settings.hotkeys_enabled = False
            log("快捷键已禁用。")

        self.settings.save()
        
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
    def toggle_word_wrap(self):
        """切换自动换行的启用状态"""
        self.settings.word_wrap_enabled = not self.settings.word_wrap_enabled
        self.settings.save()
        log(f"自动换行: {'开启' if self.settings.word_wrap_enabled else '关闭'}")
        if hasattr(self, 'word_wrap_action'):
            self.word_wrap_action.setChecked(self.settings.word_wrap_enabled)
        if self.popup.isVisible():
            self.popup.update_list(self.popup.search_box.text())

    @Slot()
    def toggle_show_source(self):
        """切换归属显示的启用状态"""
        self.settings.show_source_enabled = not self.settings.show_source_enabled
        self.settings.save()
        log(f"词条归属显示: {'开启' if self.settings.show_source_enabled else '关闭'}")
        if hasattr(self, 'show_source_action'):
            self.show_source_action.setChecked(self.settings.show_source_enabled)
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
        """弹出对话框以设置新的快捷键或触发字符串"""
        dialog = HotkeyDialog(
            parent=self.popup,
            current_hotkey=self.settings.hotkey,
            current_hotkey_enabled=self.settings.hotkeys_enabled,
            current_string_trigger_enabled=self.settings.string_trigger_enabled,
            current_string_trigger_str=self.settings.string_trigger_str,
            theme=THEMES[self.settings.theme],
            font_size=self.settings.font_size
        )
        if dialog.exec():
            hot_en, hot_str, str_en, str_val = dialog.get_settings()
            old_hotkeys_enabled = self.settings.hotkeys_enabled
            old_hotkey = self.settings.hotkey
            old_string_enabled = self.settings.string_trigger_enabled
            old_string_value = self.settings.string_trigger_str

            normalized_hotkey = self.settings.hotkey
            if hot_str.strip():
                parsed_hotkey = self.hotkey_manager.validate_hotkey(hot_str)
                if not parsed_hotkey["valid"]:
                    QMessageBox.warning(self.popup, "设置错误", parsed_hotkey["error"])
                    return
                normalized_hotkey = parsed_hotkey["normalized"]
            elif not hot_en:
                normalized_hotkey = old_hotkey

            changed = (
                hot_en != old_hotkeys_enabled
                or normalized_hotkey != old_hotkey
                or str_en != old_string_enabled
                or str_val != old_string_value
            )

            if not changed:
                return

            if hot_en:
                success, normalized_hotkey, error_message = self.hotkey_manager.reregister(normalized_hotkey)
                if not success:
                    if old_hotkeys_enabled:
                        restore_success, restored_hotkey, restore_error = self.hotkey_manager.reregister(old_hotkey)
                        if restore_success:
                            log(f"新热键注册失败，已恢复旧热键: {restored_hotkey}")
                        else:
                            log(f"CRITICAL: 新热键注册失败且恢复旧热键失败: {restore_error}")
                    else:
                        self.hotkey_manager.unregister_all()

                    QMessageBox.warning(self.popup, "热键注册失败", error_message)
                    return
            else:
                self.hotkey_manager.unregister_all()

            self.hotkey_manager.update_string_trigger(str_en, str_val)

            self.settings.hotkeys_enabled = hot_en
            self.settings.hotkey = normalized_hotkey
            self.settings.string_trigger_enabled = str_en
            self.settings.string_trigger_str = str_val

            self.settings.save()
            log(f"触发配置已更新 | 组合键: {hot_en} '{normalized_hotkey}' | 连续字符串: {str_en} '{str_val}'")
            QMessageBox.information(None, "成功", "触发方式设置已更新！")

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
                self.clipboard_timestamps = {}
                self.sync_clipboard_timestamps()
                QMessageBox.information(None, "成功", "剪贴板历史已清空！")
                if self.popup.isVisible():
                    self.popup.update_list("")
            else:
                QMessageBox.warning(None, "错误", "清空剪贴板历史失败！")

    @Slot()
    def toggle_clipboard_auto_clear(self):
        self.settings.clipboard_auto_clear_enabled = not self.settings.clipboard_auto_clear_enabled
        self.settings.save()
        if hasattr(self, 'clipboard_auto_clear_action'):
            self.clipboard_auto_clear_action.setChecked(self.settings.clipboard_auto_clear_enabled)
        
        if self.settings.clipboard_auto_clear_enabled:
            current_time = time.time()
            self.sync_clipboard_timestamps(current_time=current_time)
            self.check_clipboard_auto_clear()
        
        log(f"剪贴板定时清除功能: {'开启' if self.settings.clipboard_auto_clear_enabled else '关闭'}")

    @Slot()
    def set_clipboard_auto_clear_minutes(self):
        current_minutes = self.settings.clipboard_auto_clear_minutes
        new_minutes, ok = QInputDialog.getInt(None, "设置清除时间",
                                              "请输入经过多少分钟后清除:",
                                              current_minutes, 1, 1440, 1)
        if ok and new_minutes != current_minutes:
            self.settings.clipboard_auto_clear_minutes = new_minutes
            self.settings.save()
            log(f"剪贴板清除时间已更新为: {new_minutes} 分钟")
            self.check_clipboard_auto_clear()
            QMessageBox.information(None, "成功", f"剪贴板清除时间已设置为 {new_minutes} 分钟！")

    @Slot()
    def check_clipboard_auto_clear(self):
        if not self.settings.clipboard_auto_clear_enabled:
            return
        
        current_time = time.time()
        threshold = self.settings.clipboard_auto_clear_minutes * 60
        items_to_delete = []
        
        for text, timestamp in list(self.clipboard_timestamps.items()):
            if current_time - timestamp > threshold:
                items_to_delete.append(text)
                
        if items_to_delete:
            deleted_any = False
            for full_content in items_to_delete:
                if self.word_manager.clipboard_source and self.word_manager.clipboard_source.delete_entry(full_content):
                    deleted_any = True
                if full_content in self.clipboard_timestamps:
                    del self.clipboard_timestamps[full_content]
            
            if deleted_any:
                log(f"已自动清除过期的剪贴板内容: {len(items_to_delete)} 条")
                self.word_manager.load_clipboard_history() # 重新加载以更新内部状态
                self.sync_clipboard_timestamps(current_time=current_time)
                if self.popup.isVisible():
                    self.popup.update_list(self.popup.search_box.text())

    # --- 新增：自动重启相关方法 ---
    @Slot()
    def perform_restart(self):
        """执行重启操作"""
        log("执行重启...")
        self.settings.save()
        self.popup.hide()
        self.hotkey_manager.stop()
        if self.shortcut_listener:
            self.shortcut_listener.stop()
        self.stop_file_observer()
        QTimer.singleShot(100, self._restart_process)

    def _restore_services_after_failed_restart(self):
        """重启派发失败后恢复本实例的运行态。"""
        hotkey_restore_failed = ""
        if self.settings.hotkeys_enabled:
            success, normalized_hotkey, error_message = self.hotkey_manager.reregister(self.settings.hotkey)
            if success:
                self.settings.hotkey = normalized_hotkey
            else:
                self.settings.hotkeys_enabled = False
                self.settings.save()
                if hasattr(self, 'toggle_hotkeys_action'):
                    self.toggle_hotkeys_action.setChecked(False)
                hotkey_restore_failed = error_message

        self.hotkey_manager.update_string_trigger(
            self.settings.string_trigger_enabled,
            self.settings.string_trigger_str
        )

        if self.shortcut_listener and self.settings.shortcut_code_enabled:
            self.shortcut_listener.start()
        self.start_file_observer()

        if hotkey_restore_failed:
            QMessageBox.warning(
                self.popup,
                "热键恢复失败",
                f"重启派发失败后，当前实例的热键恢复也失败了：\n{hotkey_restore_failed}"
            )

    def _restart_process(self):
        """实际的重启进程调用"""
        try:
            log(f"准备重启: sys.executable={sys.executable}, sys.argv={sys.argv}")
            restart_args = sys.argv[1:] if getattr(sys, 'frozen', False) else sys.argv
            success, pid = self._start_detached_process(sys.executable, restart_args)
            if not success:
                raise RuntimeError("QProcess.startDetached 返回 False。")

            log(f"重启新进程已成功启动。PID={pid}")
            self.app.quit()
        except Exception as e:
            log(f"重启失败: {e}")
            self._restore_services_after_failed_restart()
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
            found_files = {
                os.path.join(AUTO_LOAD_DIR, f)
                for f in os.listdir(AUTO_LOAD_DIR)
                if f.endswith('.md') and is_eligible_library_file(os.path.join(AUTO_LOAD_DIR, f))
            }
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
                self.settings.auto_libraries.append({"path": path, "enabled": True, "kind": "file"})
                log(f"发现并添加新自动词库: {os.path.basename(path)}")

        if removed_files:
            self.settings.auto_libraries = [lib for lib in self.settings.auto_libraries if lib['path'] not in removed_files]
            for path in removed_files:
                log(f"移除不存在的自动词库: {os.path.basename(path)}")
        
        self.settings.save()
        return True # 确认发生了变化
