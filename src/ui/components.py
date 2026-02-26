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


# --- 编辑对话框 ---
from PySide6.QtWidgets import QDialog, QTextEdit, QDialogButtonBox

class MarkdownTextEdit(QTextEdit):
    """自定义文本框，优化 Markdown 编写体验"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 强制开启自动换行
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setAcceptRichText(False)
        
        # 覆写 Tab 宽度为 4 个空格物理宽度，避免由于默认 80px Tab 导致的无序列表极度退缩
        font_metrics = self.fontMetrics()
        space_width = font_metrics.horizontalAdvance(' ')
        self.setTabStopDistance(space_width * 4)

    def keyPressEvent(self, event):
        # 将 Tab 键替换为 4 个空格，方便 Markdown 列表缩进
        if event.key() == Qt.Key_Tab:
            self.insertPlainText("    ")
            return
        super().keyPressEvent(event)

class EditDialog(QDialog):
    def __init__(self, parent=None, current_text="", theme=None, font_size=14):
        super().__init__(parent)
        self.setWindowTitle("编辑词条")
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(12, 12, 12, 12)
        
        self.text_edit = MarkdownTextEdit(self)
        self.text_edit.setPlainText(current_text)
        self.layout().addWidget(self.text_edit)
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # 将按钮栏稍微往下压一点
        self.layout().addSpacing(5)
        self.layout().addWidget(self.button_box)
        
        # 扩大默认尺寸给 Markdown 提供呼吸空间
        self.resize(600, 450)
        if theme:
            self.apply_theme(theme, font_size)

    def apply_theme(self, theme, font_size):
        self.setStyleSheet(f"background-color: {theme['bg_color']}; color: {theme['text_color']};")
        
        # 针对代码/Markdown强制使用等宽字体族群，并增加行边距
        font_family = 'Consolas, "Courier New", monospace, "Microsoft YaHei"'
        
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 6px;
                padding: 10px;
                font-family: {font_family};
                font-size: {font_size}px;
                selection-background-color: {theme['item_selected_bg']};
                selection-color: {theme['item_selected_text']};
            }}
        """)
        # 简单按钮样式
        btn_style = f"""
            QPushButton {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                padding: 6px 16px;
                border-radius: 4px;
                font-family: "Microsoft YaHei";
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
class ScrollableMessageBox(QDialog):
    def __init__(self, parent=None, title="", text="", theme=None, font_size=14):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.NoFrame)

        message_label = QLabel(text)
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignTop)
        
        scroll_area.setWidget(message_label)
        scroll_area.setMaximumHeight(300)

        layout.addWidget(scroll_area)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No, self)
        self.button_box.button(QDialogButtonBox.Yes).setText("确定")
        self.button_box.button(QDialogButtonBox.No).setText("取消")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        if theme:
            self.apply_theme(theme, font_size, message_label, scroll_area)

    def apply_theme(self, theme, font_size, message_label, scroll_area):
        self.setStyleSheet(f"QDialog {{ background-color: {theme['bg_color']}; }}")
        message_label.setStyleSheet(f"QLabel {{ color: {theme['text_color']}; font-size: {font_size}px; background-color: transparent; }}")

        scroll_area_style = f"""
            QScrollArea {{
                background-color: {theme['input_bg_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 4px;
                padding: 5px;
            }}
            QScrollBar:vertical {{
                border: none;
                background: {theme['input_bg_color']};
                width: 10px;
                margin: 0px 0px 0px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {theme['border_color']};
                min-height: 20px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """
        scroll_area.setStyleSheet(scroll_area_style)

        btn_style = f"""
            QPushButton {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                padding: 5px 15px;
                border-radius: 4px;
                min-width: 80px;
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


# --- 快捷键设置对话框 ---
from PySide6.QtGui import QKeySequence

class HotkeyLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hotkey = ""
        self.setPlaceholderText("点击这里设置快捷键")

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_unknown):
            return  # 忽略单独的修饰键

        modifiers = []
        if event.modifiers() & Qt.ControlModifier: modifiers.append("ctrl")
        if event.modifiers() & Qt.AltModifier: modifiers.append("alt")
        if event.modifiers() & Qt.ShiftModifier: modifiers.append("shift")

        # 映射常用功能键
        key_map = {
            Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
            Qt.Key_Escape: "esc", Qt.Key_Tab: "tab", Qt.Key_Backspace: "backspace"
        }
        key_text = key_map.get(key, QKeySequence(key).toString().lower())
        
        if key_text:
            modifiers.append(key_text)
            self.hotkey = "+".join(modifiers)
            self.setText(self.hotkey)

    def get_hotkey(self):
        return self.hotkey
class HotkeyDialog(QDialog):
    def __init__(self, parent=None, current_hotkey="", theme=None, font_size=14):
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.setMinimumWidth(350)
        
        layout = QVBoxLayout(self)
        
        info_label = QLabel("请点击下方输入框，然后按下您想设置的快捷键组合。")
        layout.addWidget(info_label)
        
        self.hotkey_input = HotkeyLineEdit(self)
        self.hotkey_input.setText(current_hotkey)
        self.hotkey_input.hotkey = current_hotkey
        layout.addWidget(self.hotkey_input)
        
        button_layout = QHBoxLayout()
        self.restore_button = QPushButton("恢复默认(ctrl+space)")
        self.restore_button.clicked.connect(self.restore_default)
        button_layout.addWidget(self.restore_button)
        button_layout.addStretch()
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        button_layout.addWidget(self.button_box)
        
        layout.addLayout(button_layout)
        
        if theme:
            self.apply_theme(theme, font_size, info_label)

    def restore_default(self):
        self.hotkey_input.setText("ctrl+space")
        self.hotkey_input.hotkey = "ctrl+space"

    def get_hotkey(self):
        return self.hotkey_input.get_hotkey()

    def apply_theme(self, theme, font_size, info_label):
        self.setStyleSheet(f"QDialog {{ background-color: {theme['bg_color']}; color: {theme['text_color']}; }}")
        info_label.setStyleSheet(f"QLabel {{ color: {theme['text_color']}; font-size: {font_size-1}px; }}")
        self.hotkey_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 4px;
                padding: 8px;
                font-size: {font_size}px;
            }}
        """)
        btn_style = f"""
            QPushButton {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                padding: 5px 15px;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background-color: {theme['item_hover_bg']}; }}
            QPushButton:pressed {{ background-color: {theme['item_selected_bg']}; color: {theme['item_selected_text']}; }}
        """
        for button in self.button_box.buttons() + [self.restore_button]:
            button.setStyleSheet(btn_style)
def get_disclaimer_html_text():
    """返回包含重要声明与协议的HTML格式文本"""
    return """
    <h2>⚠️ 重要声明与许可协议 (IMPORTANT DISCLAIMER & LICENSE)</h2>
    <p><strong>在下载、安装或以任何方式使用本软件 (QuickKV) 与代码前，请您务必仔细阅读并充分理解以下所有条款。本软件是基于 MIT 许可证 (MIT License) 发布的免费开源项目。一旦您开始使用本软件的任何部分（包括但不限于运行程序、查阅代码、修改或分发），即表示您已完全阅读、理解并无条件接受本声明及 MIT 许可证的全部内容。如果您不同意其中任何条款，请立即停止使用并彻底删除本软件的所有相关文件。</strong></p>
    
    <h3>1. “按原样”提供，不作任何保证 (AS-IS, WITHOUT ANY WARRANTY)</h3>
    <p>本软件按“原样”(AS IS) 提供，不附带任何形式的明示或暗示的保证，包括但不限于对软件的<strong>商业适用性 (MERCHANTABILITY)</strong>、<strong>特定用途适用性 (FITNESS FOR A PARTICULAR PURPOSE)</strong> 和 <strong>非侵权性 (NON-INFRINGEMENT)</strong> 的保证。作者不保证软件能满足您的所有需求，不保证软件运行不会中断或不出现任何错误，也不保证软件中的任何缺陷 (Bug) 都将被修正。所有条款以软件附带的 <a href="https://github.com/msjsc001/QuickKV/blob/main/LICENSE">MIT 许可证</a> 原文为准。</p>
    
    <h3>2. 核心功能与潜在风险告知 (CORE FUNCTIONALITY & POTENTIAL RISKS)</h3>
    <p>本软件的核心功能必然涉及<strong>全局键盘监听</strong> (用于实现快捷码、快捷键功能) 和<strong>剪贴板内容读写</strong> (用于粘贴及历史记忆功能)。您必须清楚地认识到这些功能存在的潜在风险：</p>
    <ul>
        <li><strong>键盘监听风险</strong>: 为了实现快捷码功能，程序会在后台监听您的键盘输入。尽管程序的设计初衷仅为匹配您设定的触发码，但该机制在技术原理上可以访问到您的全局键盘输入。</li>
        <li><strong>剪贴板隐私风险</strong>: 当“剪贴板记忆”功能开启时，程序会读取并以明文形式记录您复制到系统剪贴板的文本内容。这意味着，<strong>如果您复制了密码、银行卡号、身份证、私密对话等任何敏感信息，这些信息也可能被程序记录下来。</strong></li>
        <li><strong>安全软件警告</strong>: 由于上述功能的敏感性，部分安全软件、杀毒软件或系统防护工具可能会将本软件标记为“键盘记录器”、“风险软件”或“恶意程序”。这属于正常现象，请您基于对本项目开源代码的信任自行判断并选择是否“信任”或“允许运行”。</li>
    </ul>

    <h3>3. 风险自负与责任限制 (ASSUMPTION OF RISK & LIMITATION OF LIABILITY)</h3>
    <p>您明确理解并同意，使用本软件所引发的一切后果和风险，包括但不限于<strong>数据丢失或损坏、利润损失、业务中断、个人信息泄露、与其他软件的冲突、系统不稳定或崩溃等</strong>，完全由您本人承担。在任何法律允许的最大范围内，本软件作者或任何贡献者在任何情况下均不对任何因使用、无法使用或滥用本软件而导致的任何直接、间接、偶然、特殊、惩戒性或后果性的损害负责，即使已被告知可能发生此类损害。</p>

    <h3>4. 用户数据与隐私保护 (USER DATA & PRIVACY)</h3>
    <p>作者高度重视用户隐私。本软件为<strong>纯本地离线工具</strong>，您使用软件产生的所有数据，包括<strong>词库文件 (<code>.md</code>)、配置文件 (<code>config.ini</code>)、剪贴板历史记录等，均只会存储在您自己的电脑本地硬盘上</strong>。本软件不会以任何形式主动收集、存储或上传您的任何个人信息或使用数据到任何网络服务器。软件运行无需联网。</p>

    <h3>5. 合法合规使用承诺 (LAWFUL & COMPLIANT USE)</h3>
    <p>您承诺将在遵守您所在国家或地区所有适用法律法规的前提下使用本软件。严禁将本软件用于任何非法目的，包括但不限于窃取商业秘密、侵犯他人隐私、发送垃圾信息等任何违法违规行为。任何因您的非法使用或违规操作而导致的法律责任和后果，均由您自行承担，与本软件作者无关。此外，如果您在组织或公司环境（如工作电脑）中使用本软件，您有责任确保此行为符合该组织的信息安全策略和规定。</p>

    <h3>6. 开源透明与代码审查 (OPEN SOURCE & CODE AUDIT)</h3>
    <p>本软件是一个开源项目，所有源代码均在 GitHub 公开。我们鼓励并建议有能力的用户在安装使用前，<strong>自行审查代码</strong>，以确保其安全性和功能符合您的预期。作者保证其发布的官方版本不包含任何已知的恶意代码，但无法保证软件绝对没有缺陷。</p>

    <h3>7. 官方渠道与安全下载 (OFFICIAL CHANNEL & SECURE DOWNLOAD)</h3>
    <p>本项目的唯一官方发布渠道为 GitHub Releases 页面 (<code>https://github.com/msjsc001/QuickKV/releases</code>)。作者不对任何从第三方网站、论坛、社群、个人分享等非官方渠道获取的软件副本的安全性、完整性或一致性作任何保证。为避免潜在的恶意代码注入或版本篡改风险，请务必通过官方渠道下载。</p>

    <h3>8. 无专业支持义务 (NO OBLIGATION FOR SUPPORT)</h3>
    <p>本软件为免费提供，作者没有义务提供任何形式的商业级技术支持、更新、用户培训或后续服务。作者可能会通过 GitHub Issues 等社区渠道提供帮助，但这完全出于自愿且不作任何承诺，作者保留随时忽略或关闭任何问题的权利。</p>
    
    <p><strong>再次强调：继续使用本软件，即表示您已确认阅读、理解并同意遵守上述所有条款以及 MIT 许可证的全部内容。</strong></p>
    """

# --- 重要声明与协议对话框 ---
class DisclaimerDialog(QDialog):
    def __init__(self, parent=None, theme=None, font_size=14):
        super().__init__(parent)
        self.setWindowTitle("QuickKV - 重要声明与用户协议")
        self.setMinimumSize(600, 500)
        self.setModal(True) # 强制用户交互

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # 1. 醒目提示
        intro_label = QLabel("<b>欢迎使用 QuickKV！在开始前，请您务必阅读以下重要信息。本软件涉及键盘和剪贴板操作，了解其工作原理和潜在风险对您至关重要。</b>")
        intro_label.setWordWrap(True)
        intro_label.setStyleSheet("font-size: 16px; color: #e5c07b;") # 使用醒目的颜色
        layout.addWidget(intro_label)

        # 2. 协议内容滚动区域
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setHtml(get_disclaimer_html_text())
        layout.addWidget(self.text_area, 1) # 占据主要空间

        # 3. 确认勾选框
        self.agree_checkbox = QCheckBox("我已仔细阅读、完全理解并同意上述所有条款及 MIT 许可协议。我愿自行承担使用本软件的一切风险。")
        self.agree_checkbox.toggled.connect(self.on_checkbox_toggled)
        layout.addWidget(self.agree_checkbox)

        # 4. 按钮
        button_layout = QHBoxLayout()
        self.agree_button = QPushButton("同意并开始使用")
        self.agree_button.setEnabled(False) # 默认禁用
        self.agree_button.clicked.connect(self.accept)
        
        self.disagree_button = QPushButton("不同意并退出")
        self.disagree_button.clicked.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(self.disagree_button)
        button_layout.addWidget(self.agree_button)
        layout.addLayout(button_layout)

        if theme:
            self.apply_theme(theme, font_size)

    def on_checkbox_toggled(self, checked):
        self.agree_button.setEnabled(checked)

    def apply_theme(self, theme, font_size):
        self.setStyleSheet(f"QDialog {{ background-color: {theme['bg_color']}; color: {theme['text_color']}; }}")
        self.text_area.setStyleSheet(f"""
            QTextEdit {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 4px;
                padding: 8px;
                font-size: {font_size-1}px;
            }}
        """)
        self.agree_checkbox.setStyleSheet(f"""
            QCheckBox {{ 
                font-size: {font_size-1}px; 
                color: {theme['text_color']}; 
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                background-color: {theme['input_bg_color']};
                border: 1px solid {theme['border_color']};
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {theme.get('highlight_color', '#5294e2')};
                border: 1px solid {theme.get('highlight_color', '#5294e2')};
            }}
        """)
        
        btn_style = f"""
            QPushButton {{
                background-color: {theme['input_bg_color']};
                color: {theme['text_color']};
                border: 1px solid {theme['border_color']};
                padding: 8px 18px;
                border-radius: 4px;
                font-size: {font_size}px;
            }}
            QPushButton:hover {{ background-color: {theme['item_hover_bg']}; }}
            QPushButton:pressed {{ background-color: {theme['item_selected_bg']}; color: {theme['item_selected_text']}; }}
            QPushButton:disabled {{ background-color: #444; color: #888; }}
        """
        self.agree_button.setStyleSheet(btn_style)
        self.disagree_button.setStyleSheet(btn_style)
