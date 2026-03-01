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


from core.config import *
from core.settings import SettingsManager
from core.word_manager import WordManager
from main_controller import MainController
from ui.components import DisclaimerDialog
from PySide6.QtNetwork import QLocalServer, QLocalSocket



# --- main入口 ---
if __name__ == "__main__":
    # --- 启用高DPI支持 ---
    # PySide6 默认启用缩放，仅保留取整策略即可
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # --- 确保应用目录结构存在 ---
    if not os.path.exists(USER_DATA_DIR):
        try: os.makedirs(USER_DATA_DIR)
        except Exception: pass

    if not os.path.exists(AUTO_LOAD_DIR):
        try:
            os.makedirs(AUTO_LOAD_DIR)
            log(f"已创建自动加载词库文件夹: {AUTO_LOAD_DIR}")
        except Exception as e:
            log(f"创建自动加载文件夹失败: {e}")

    # --- 确保默认词库存在 ---
    if not os.path.exists(WORD_FILE):
        try:
            with open(WORD_FILE, 'w', encoding='utf-8') as f:
                f.write("- 恭喜，安装成功！这是一个示例词条\n按回车即可输出这块内容。\n\n- 这是带快捷码的第二条 ``k:test``\n只要在任何地方输入 test，就会自动替换为本行文本。\n")
            log(f"已创建默认词库文件: {WORD_FILE}")
        except Exception as e:
            log(f"创建默认词库文件失败: {e}")

    settings_manager = SettingsManager(CONFIG_FILE)
    word_manager = WordManager(settings_manager)
    controller = MainController(app, word_manager, settings_manager)
    
    # --- 首次启动与版本更新检查 ---
    current_device_id = get_device_id()
    accepted_info = settings_manager.accepted_disclaimer_info

    # 检查设备ID或软件版本是否不匹配
    if accepted_info.get('id') != current_device_id or accepted_info.get('version') != VERSION:
        disclaimer_dialog = DisclaimerDialog(theme=THEMES.get(settings_manager.theme), font_size=settings_manager.font_size)
        if disclaimer_dialog.exec() == QDialog.Accepted:
            # 用户同意后，同时记录当前设备ID和软件版本
            settings_manager.accepted_disclaimer_info = {
                'id': current_device_id,
                'version': VERSION
            }
            settings_manager.save()
            log(f"用户已接受版本 {VERSION} 的协议。")
        else:
            log("用户未接受协议，程序退出。")
            sys.exit(0)

    tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), app); tray_icon.setToolTip("QuickKV")
    
    # 点击托盘图标触发主界面
    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.Trigger:
            controller.on_hotkey_triggered()
            
    tray_icon.activated.connect(on_tray_activated)
    menu = QMenu()
    controller.menu = menu # 将menu实例传递给controller
    
    # --- 版本号标题 ---
    version_action = QAction(f"QuickKV v{VERSION}")
    version_action.setEnabled(False)
    menu.addAction(version_action)
    menu.addSeparator()
    
    # --- 重要声明与用户协议 ---
    show_disclaimer_action = QAction("重要声明与用户协议")
    show_disclaimer_action.triggered.connect(controller.show_disclaimer)
    menu.addAction(show_disclaimer_action)
    menu.addSeparator()

    # --- 主要功能 ---
    controller.toggle_hotkeys_action = QAction("启用快捷键", checkable=True)
    controller.toggle_hotkeys_action.setChecked(settings_manager.hotkeys_enabled)
    controller.toggle_hotkeys_action.triggered.connect(controller.toggle_hotkeys_enabled)
    menu.addAction(controller.toggle_hotkeys_action)
    
    set_hotkey_action = QAction("自定义快捷键...")
    set_hotkey_action.triggered.connect(controller.set_hotkey)
    menu.addAction(set_hotkey_action)

    controller.toggle_shortcut_code_action = QAction("开启快捷码", checkable=True)
    controller.toggle_shortcut_code_action.setChecked(settings_manager.shortcut_code_enabled)
    controller.toggle_shortcut_code_action.triggered.connect(controller.toggle_shortcut_code_enabled)
    menu.addAction(controller.toggle_shortcut_code_action)

    # --- 自动重启 ---
    restart_menu = QMenu("间隔时间自动重启")
    controller.auto_restart_action = QAction("间隔时间自动重启", checkable=True)
    controller.auto_restart_action.setChecked(settings_manager.auto_restart_enabled)
    controller.auto_restart_action.triggered.connect(controller.toggle_auto_restart)
    restart_menu.addAction(controller.auto_restart_action)

    set_restart_interval_action = QAction("设定间隔时间...")
    set_restart_interval_action.triggered.connect(controller.set_auto_restart_interval)
    restart_menu.addAction(set_restart_interval_action)
    restart_menu.addSeparator()
    restart_now_action = QAction("立即重启")
    restart_now_action.triggered.connect(controller.perform_restart)
    restart_menu.addAction(restart_now_action)
    
    menu.addMenu(restart_menu)

    # --- 粘贴方式 ---
    paste_mode_menu = QMenu("软件粘贴方式")
    paste_mode_group = QActionGroup(paste_mode_menu)
    paste_mode_group.setExclusive(True)

    paste_ctrl_v_action = QAction("Ctrl+V (默认)", checkable=True)
    paste_ctrl_v_action.setChecked(settings_manager.paste_mode == 'ctrl_v')
    paste_ctrl_v_action.triggered.connect(lambda: controller.set_paste_mode('ctrl_v'))
    paste_mode_menu.addAction(paste_ctrl_v_action)
    paste_mode_group.addAction(paste_ctrl_v_action)
    paste_mode_menu.addSeparator()
    paste_ctrl_shift_v_action = QAction("Ctrl+Shift+V", checkable=True)
    paste_ctrl_shift_v_action.setChecked(settings_manager.paste_mode == 'ctrl_shift_v')
    paste_ctrl_shift_v_action.triggered.connect(lambda: controller.set_paste_mode('ctrl_shift_v'))
    paste_mode_menu.addAction(paste_ctrl_shift_v_action)
    paste_mode_group.addAction(paste_ctrl_shift_v_action)
    paste_mode_menu.addSeparator()
    paste_typing_action = QAction("输入模式", checkable=True)
    paste_typing_action.setChecked(settings_manager.paste_mode == 'typing')
    paste_typing_action.triggered.connect(lambda: controller.set_paste_mode('typing'))
    paste_mode_menu.addAction(paste_typing_action)
    paste_mode_group.addAction(paste_typing_action)

    menu.addMenu(paste_mode_menu)
    menu.addSeparator()

    # --- 词库选择 ---
    library_menu = QMenu("词库选择")
    controller.library_menu = library_menu
    menu.addMenu(library_menu)

    # --- 自动载入的MD词库 ---
    auto_library_menu = QMenu("自动载入的md词库")
    controller.auto_library_menu = auto_library_menu
    menu.addMenu(auto_library_menu) # 直接添加到主菜单
    
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

    controller.clipboard_auto_clear_action = QAction("启用定时清除", checkable=True)
    controller.clipboard_auto_clear_action.setChecked(settings_manager.clipboard_auto_clear_enabled)
    controller.clipboard_auto_clear_action.triggered.connect(controller.toggle_clipboard_auto_clear)
    clipboard_menu.addAction(controller.clipboard_auto_clear_action)

    set_auto_clear_time_action = QAction("设置清除时间...")
    set_auto_clear_time_action.triggered.connect(controller.set_clipboard_auto_clear_minutes)
    clipboard_menu.addAction(set_auto_clear_time_action)

    clipboard_menu.addSeparator()
    clear_history_action = QAction("清空")
    clear_history_action.triggered.connect(controller.clear_clipboard_history_menu)
    clipboard_menu.addAction(clear_history_action)
    
    menu.addMenu(clipboard_menu)
    menu.addSeparator()
    
    initial_toggle_text = f"切换到 {'夜间' if settings_manager.theme == 'light' else '日间'} 模式"
    controller.toggle_theme_action = QAction(initial_toggle_text); controller.toggle_theme_action.triggered.connect(controller.toggle_theme); menu.addAction(controller.toggle_theme_action)
    
    controller.highlight_matches_action = QAction("高亮匹配字符", checkable=True)
    controller.highlight_matches_action.setChecked(settings_manager.highlight_matches)
    controller.highlight_matches_action.triggered.connect(controller.toggle_highlight_matches)
    menu.addAction(controller.highlight_matches_action)

    controller.word_wrap_action = QAction("词条-自动换行", checkable=True)
    controller.word_wrap_action.setChecked(settings_manager.word_wrap_enabled)
    controller.word_wrap_action.triggered.connect(controller.toggle_word_wrap)
    menu.addAction(controller.word_wrap_action)

    controller.show_source_action = QAction("词条-归属显示", checkable=True)
    controller.show_source_action.setChecked(settings_manager.show_source_enabled)
    controller.show_source_action.triggered.connect(controller.toggle_show_source)
    menu.addAction(controller.show_source_action)
    menu.addSeparator()

    font_size_action = QAction("设置字体大小(&F)..."); font_size_action.triggered.connect(controller.set_font_size); menu.addAction(font_size_action)

    # --- 帮助 ---
    menu.addSeparator()
    help_action = QAction("帮助与更新")
    help_action.triggered.connect(controller.open_help_docs)
    menu.addAction(help_action)

    # --- 退出 ---
    menu.addSeparator()
    quit_action = QAction("退出(&Q)"); quit_action.triggered.connect(app.quit); menu.addAction(quit_action)

    controller.apply_menu_theme() # 初始化时应用主题
    controller.rebuild_library_menu() # 首次构建手动词库菜单
    controller.scan_and_update_auto_libraries() # 首次扫描以同步自动词库列表
    controller.rebuild_auto_library_menu()      # 首次强制构建菜单UI
    controller.rebuild_auto_library_menu()      # 基于扫描结果，首次强制构建自动词库菜单UI
    tray_icon.setContextMenu(menu); tray_icon.show()
    
    log("程序启动成功，正在后台运行。")
    print(f"按下 '{settings_manager.hotkey}' 来激活或关闭窗口。")
    print(f"当前主题: {settings_manager.theme}。右键点击托盘图标可进行设置。")
    
    # 连接 aboutToQuit 信号到清理函数
    app.aboutToQuit.connect(controller.cleanup_and_exit)
    
    sys.exit(app.exec())