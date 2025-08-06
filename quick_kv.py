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
from PySide6.QtCore import (Qt, Signal, Slot, QObject, QFileSystemWatcher,
                          QTimer, QEvent, QRect, QProcess)
from PySide6.QtGui import QIcon, QAction, QCursor, QPixmap, QPainter, QColor, QPalette, QActionGroup
import pyperclip
from pypinyin import pinyin, Style
from pynput import keyboard
from fuzzywuzzy import fuzz

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

# --- 全局配置 ---
def get_device_id():
    """
    获取设备的唯一标识符，依次尝试:
    1. 主板 UUID (wmic)
    2. 系统盘卷序列号 (wmic)
    3. MAC地址
    """
    identifier = None
    try:
        # 1. 尝试主板 UUID
        uuid_output = subprocess.check_output("wmic csproduct get uuid", shell=True, text=True, stderr=subprocess.DEVNULL)
        uuid = uuid_output.strip().split('\n')[-1].strip()
        if uuid and "UUID" not in uuid and len(uuid) > 5:
            identifier = uuid
            log("使用主板 UUID 作为设备ID。")
    except Exception as e:
        log(f"获取主板 UUID 失败: {e}")

    if not identifier:
        try:
            # 2. 尝试系统盘卷序列号
            vol_serial_output = subprocess.check_output("wmic path win32_logicaldisk where \"DeviceID='%SystemDrive%'\" get VolumeSerialNumber", shell=True, text=True, stderr=subprocess.DEVNULL)
            vol_serial = vol_serial_output.strip().split('\n')[-1].strip()
            if vol_serial and "VolumeSerialNumber" not in vol_serial and len(vol_serial) > 2:
                identifier = vol_serial
                log("使用系统卷序列号作为设备ID。")
        except Exception as e:
            log(f"获取卷序列号失败: {e}")

    if not identifier:
        try:
            # 3. 尝试 MAC 地址
            # 需要导入 uuid 模块，请确保文件顶部有 import uuid
            mac = ':'.join(__import__('re').findall('..', '%012x' % __import__('uuid').getnode()))
            if mac and mac != "00:00:00:00:00:00":
                identifier = mac
                log("使用 MAC 地址作为设备ID。")
        except Exception as e:
            log(f"获取 MAC 地址失败: {e}")

    if not identifier:
        # 最终备用方案
        identifier = "generic_fallback_id_all_failed"
        log("所有设备ID获取方法均失败，使用最终备用ID。")

    return hashlib.sha256(identifier.encode()).hexdigest()


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
CONFIG_FILE = os.path.join(BASE_PATH, "config.ini")
AUTO_LOAD_DIR = os.path.join(BASE_PATH, "MD词库-需自动载入的请放入")
CLIPBOARD_HISTORY_FILE = os.path.join(AUTO_LOAD_DIR, "剪贴板词库-勿删.md")
CACHE_FILE = os.path.join(BASE_PATH, "cache.json") # 新增：缓存文件路径
HELP_DOCS_DIR = os.path.join(BASE_PATH, "帮助文档")

# --- 内部资源 ---
ICON_PATH = resource_path("icon.png")

# --- 其他配置 ---
DEBUG_MODE = True
VERSION = "1.0.5.40" # 版本号

def log(message):
    if DEBUG_MODE:
        print(f"[LOG] {message}")

# --- 主题颜色定义 ---
THEMES = {
    "dark": {
        "bg_color": "#21252b", "border_color": "#3c424b", "text_color": "#d1d5db",
        "title_color": "#8c929c",
        "input_bg_color": "#2c313a", "item_hover_bg": "#3a3f4b",
        "item_selected_bg": "#405061", "item_selected_text": "#d1d5db",
        "highlight_color": "#5294e2" # 新增：高亮颜色
    },
    "light": {
        "bg_color": "#fdfdfd", "border_color": "#cccccc", "text_color": "#202020",
        "title_color": "#a0a0a0",
        "input_bg_color": "#ffffff", "item_hover_bg": "#f0f0f0",
        "item_selected_bg": "#dbe4ee", "item_selected_text": "#202020",
        "highlight_color": "#007acc" # 新增：高亮颜色
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

# --- 设置管理器 ---
class SettingsManager:
    def __init__(self, file_path):
        self.config = configparser.ConfigParser()
        self.file_path = file_path
        self.load()

    def load(self):
        """
        加载配置文件。
        【已加固】增加了对 configparser 和 json 解析错误的全面捕获，
        确保在配置文件损坏时程序能以后备值启动，而不是崩溃。
        """
        try:
            self.config.read(self.file_path, encoding='utf-8')
        except configparser.Error as e:
            log(f"CRITICAL: 配置文件 '{self.file_path}' 解析失败: {e}。将使用默认设置。")
            # 如果解析失败，重置 config 对象，以便后续代码能正常运行
            self.config = configparser.ConfigParser()

        # 确保所有必需的 section 都存在
        sections = ['Window', 'Theme', 'Font', 'Search', 'Data', 'General', 'Clipboard', 'Restart', 'Paste']
        for section in sections:
            if not self.config.has_section(section):
                self.config.add_section(section)

        # 使用 try-except 块来安全地获取每个配置项
        try:
            self.hotkeys_enabled = self.config.getboolean('General', 'hotkeys_enabled', fallback=True)
            self.shortcut_code_enabled = self.config.getboolean('General', 'shortcut_code_enabled', fallback=False)
            self.hotkey = self.config.get('General', 'hotkey', fallback='ctrl+space')
            self.paste_mode = self.config.get('Paste', 'mode', fallback='ctrl_v')
            self.width = self.config.getint('Window', 'width', fallback=450)
            self.height = self.config.getint('Window', 'height', fallback=300)
            self.theme = self.config.get('Theme', 'mode', fallback='dark')
            self.font_size = self.config.getint('Font', 'size', fallback=14)
            self.multi_word_search = self.config.getboolean('Search', 'multi_word_search', fallback=True)
            self.pinyin_initial_search = self.config.getboolean('Search', 'pinyin_initial_search', fallback=True)
            self.highlight_matches = self.config.getboolean('Search', 'highlight_matches', fallback=True) # 新增
            self.clipboard_memory_enabled = self.config.getboolean('Clipboard', 'enabled', fallback=False)
            self.clipboard_memory_count = self.config.getint('Clipboard', 'count', fallback=10)
            self.auto_restart_enabled = self.config.getboolean('Restart', 'enabled', fallback=False)
            self.auto_restart_interval = self.config.getint('Restart', 'interval_minutes', fallback=3)
            # 新的协议接受信息，存储为 JSON 字符串
            disclaimer_info_str = self.config.get('General', 'accepted_disclaimer_info', fallback='{}')
            try:
                self.accepted_disclaimer_info = json.loads(disclaimer_info_str)
                if not isinstance(self.accepted_disclaimer_info, dict):
                    self.accepted_disclaimer_info = {'id': '', 'version': ''}
            except (json.JSONDecodeError, TypeError):
                self.accepted_disclaimer_info = {'id': '', 'version': ''}

            libraries_str = self.config.get('General', 'libraries', fallback='[]')
            try:
                self.libraries = json.loads(libraries_str)
                if not isinstance(self.libraries, list): self.libraries = []
            except (json.JSONDecodeError, TypeError):
                self.libraries = []

            auto_libraries_str = self.config.get('General', 'auto_libraries', fallback='[]')
            try:
                self.auto_libraries = json.loads(auto_libraries_str)
                if not isinstance(self.auto_libraries, list): self.auto_libraries = []
            except (json.JSONDecodeError, TypeError):
                self.auto_libraries = []

        except (configparser.NoOptionError, configparser.NoSectionError, ValueError) as e:
            log(f"CRITICAL: 读取配置项时出错: {e}。部分设置将恢复为默认值。")
            # 如果在获取过程中出错，确保所有属性都有一个默认值
            self.hotkeys_enabled = getattr(self, 'hotkeys_enabled', True)
            self.shortcut_code_enabled = getattr(self, 'shortcut_code_enabled', False)
            # ... (其他属性以此类推，fallback 已经处理了大部分情况)

        # 迁移和验证逻辑保持不变
        if not self.libraries and os.path.exists(WORD_FILE):
            self.libraries.append({"path": os.path.abspath(WORD_FILE), "enabled": True})
            log("已将旧的单一词库配置迁移到新的多词库系统。")

        self.libraries = [lib for lib in self.libraries if isinstance(lib, dict) and os.path.exists(lib.get('path'))]

    def save(self):
        self.config['General']['hotkeys_enabled'] = str(self.hotkeys_enabled)
        self.config['General']['shortcut_code_enabled'] = str(self.shortcut_code_enabled) # 新增：保存快捷码功能开关
        self.config['General']['hotkey'] = self.hotkey
        self.config['Window']['width'] = str(self.width)
        self.config['Window']['height'] = str(self.height)
        self.config['Theme']['mode'] = self.theme
        self.config['Font']['size'] = str(self.font_size)
        self.config['Search']['multi_word_search'] = str(self.multi_word_search)
        self.config['Search']['pinyin_initial_search'] = str(self.pinyin_initial_search)
        self.config['Search']['highlight_matches'] = str(self.highlight_matches) # 新增
        self.config['General']['libraries'] = json.dumps(self.libraries, ensure_ascii=False)
        self.config['General']['auto_libraries'] = json.dumps(self.auto_libraries, ensure_ascii=False)
        self.config['Clipboard']['enabled'] = str(self.clipboard_memory_enabled)
        self.config['Clipboard']['count'] = str(self.clipboard_memory_count)
        self.config['Restart']['enabled'] = str(self.auto_restart_enabled)
        self.config['Restart']['interval_minutes'] = str(self.auto_restart_interval)
        self.config['Paste']['mode'] = self.paste_mode
        self.config['General']['accepted_disclaimer_info'] = json.dumps(self.accepted_disclaimer_info, ensure_ascii=False)
        
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
                    
                    # --- 新的元命令解析逻辑 ---
                    # 匹配所有 ``...`` 形式的元命令
                    meta_commands_pattern = r'``(.*?)``'
                    meta_commands = re.findall(meta_commands_pattern, parent_text)
                    
                    # 从原始文本中移除所有元命令，得到纯净的 parent_text
                    clean_parent_text = re.sub(meta_commands_pattern, '', parent_text).strip()

                    should_exclude = False
                    shortcut_code = None

                    # 遍历找到的所有元命令并进行处理
                    for command in meta_commands:
                        if command == '不出现':
                            should_exclude = True
                        elif command.startswith('k:'):
                            # 提取 'k:' 后面的内容作为快捷码
                            shortcut_code = command[2:].strip()
                    # --- 新逻辑结束 ---

                    current_block = {
                        'parent': clean_parent_text, # 使用纯净文本
                        'raw_lines': [line.rstrip()],
                        'exclude_parent': should_exclude,
                        'shortcut_code': shortcut_code, # 新增：快捷码
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
            # 使用 WordSource 的加载逻辑来健壮地解析文件
            loader = WordSource(self.file_path)
            if not hasattr(loader, 'word_blocks'):
                return False

            all_blocks = loader.word_blocks
            
            # 寻找并替换要更新的块
            found = False
            new_blocks = []
            for block in all_blocks:
                if block['full_content'] == original_content:
                    # 创建一个新的 block 结构来代表更新后的内容
                    # 注意：这里我们只替换 full_content，其他字段不会被使用
                    new_blocks.append({'full_content': new_content})
                    found = True
                else:
                    new_blocks.append(block)
            
            if not found:
                log(f"update_entry: 在 {self.file_path} 中未找到要更新的内容")
                return False

            # 从更新后的块列表中重建文件内容
            # 使用 '\n' 作为分隔符，因为 add_entry 会在每个条目前加一个换行符
            new_file_content = '\n'.join([block['full_content'] for block in new_blocks])
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(new_file_content)
                
            return True
        except Exception as e:
            log(f"更新 {self.file_path} 时发生错误: {e}")
            return False

    def delete_entry(self, content_to_delete):
        try:
            # 使用 WordSource 的加载逻辑来健壮地解析文件
            loader = WordSource(self.file_path)
            if not hasattr(loader, 'word_blocks'):
                return False

            all_blocks = loader.word_blocks
            
            # 过滤掉要删除的块
            remaining_blocks = [block for block in all_blocks if block['full_content'] != content_to_delete]
            
            # 如果块的数量没有减少，说明没有找到要删除的内容
            if len(remaining_blocks) == len(all_blocks):
                log(f"delete_entry: 在 {self.file_path} 中未找到要删除的内容")
                return False

            # 从剩余的块中重建文件内容
            # 使用 '\n' 作为分隔符，因为 add_entry 会在每个条目前加一个换行符
            new_file_content = '\n'.join([block['full_content'] for block in remaining_blocks])
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(new_file_content)
                
            return True
        except Exception as e:
            log(f"删除 {self.file_path} 的词条时发生错误: {e}")
            return False

# --- 词库管理器 ---
class WordManager:
    def __init__(self, settings):
        self.settings = settings
        self.sources = []
        self.word_blocks = []
        self.cache = {} # 新增：用于存储缓存数据
        # 新增：剪贴板历史专用
        self.clipboard_source = None
        self.clipboard_history = []
        self.reload_all()

    def _get_pinyin_sort_key(self, text):
        return "".join(item[0] for item in pinyin(text, style=Style.NORMAL))

    def _get_pinyin_initials(self, text):
        # 开启多音字模式，获取所有首字母
        initials_list = pinyin(text, style=Style.FIRST_LETTER, heteronym=True)
        
        # 生成所有可能的首字母组合
        import itertools
        # [[ 'd', 't'], ['q']] -> [('d', 'q'), ('t', 'q')]
        all_combinations = list(itertools.product(*initials_list))
        # -> ['dq', 'tq']
        return ["".join(combo) for combo in all_combinations]

    def _build_char_map(self, text):
        """
        为文本中的每个字符构建一个详细的搜索映射表。
        这是新搜索算法的核心，取代了旧的 _generate_hybrid_initials。
        """
        char_map = []
        for index, char in enumerate(text):
            char_lower = char.lower()
            # 默认搜索键是字符本身的小写形式
            keys = [char_lower]
            
            # 如果是汉字，添加所有可能的拼音首字母
            if '\u4e00' <= char <= '\u9fa5':
                initials = pinyin(char, style=Style.FIRST_LETTER, heteronym=True)[0]
                keys.extend(initials)
                # 去重，例如对于 '和'，keys 会是 ['h', 'h', 'h']，去重后为 ['h']
                keys = sorted(list(set(keys)))
            
            char_map.append({
                'char': char,
                'keys': keys,
                'index': index
            })
        return char_map

    def _get_file_hash(self, file_path):
        """计算文件的MD5哈希值"""
        hasher = hashlib.md5()
        try:
            with open(file_path, 'rb') as f:
                buf = f.read()
                hasher.update(buf)
            return hasher.hexdigest()
        except FileNotFoundError:
            return None

    def _load_cache(self):
        """
        尝试从文件加载缓存。
        【已加固】确保在任何情况下（文件不存在、JSON损坏、权限问题）都能安全返回一个空字典。
        """
        if not os.path.exists(CACHE_FILE):
            log("缓存文件不存在，将跳过加载。")
            return {}
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 健壮性检查：确保 cache_data 是一个字典
            if not isinstance(cache_data, dict):
                log("缓存文件格式错误：顶层结构不是一个字典。将创建新缓存。")
                return {}

            # 版本校验
            if cache_data.get("version") != VERSION:
                log(f"缓存版本不兼容 (需要 {VERSION}，但发现 {cache_data.get('version')})，将创建新缓存。")
                return {}
            
            files_data = cache_data.get("files")
            # 健壮性检查：确保 files 部分也是一个字典
            if not isinstance(files_data, dict):
                log("缓存文件格式错误：'files' 键对应的值不是一个字典。将创建新缓存。")
                return {}

            log("成功从文件加载缓存。")
            return files_data

        except json.JSONDecodeError as e:
            log(f"CRITICAL: 加载缓存失败 (JSON解析错误): {e}。将创建新缓存。")
            return {}
        except (IOError, OSError) as e:
            log(f"CRITICAL: 加载缓存失败 (文件读写错误): {e}。将创建新缓存。")
            return {}
        except Exception as e:
            log(f"CRITICAL: 加载缓存时发生未知严重错误: {e}。将创建新缓存。")
            return {}

    def _save_cache(self):
        """将当前缓存数据保存到文件"""
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({"version": VERSION, "files": self.cache}, f, ensure_ascii=False, indent=2)
            log("缓存已成功保存。")
        except Exception as e:
            log(f"保存缓存失败: {e}")

    def _preprocess_block(self, block):
        """对单个词条块进行预处理（已重构）"""
        parent_text = block['parent']
        block['parent_lower'] = parent_text.lower()
        # 新的核心数据结构：字符映射表
        block['char_map'] = self._build_char_map(parent_text)
        # 移除旧的、不再使用的键
        if 'hybrid_initials' in block:
            del block['hybrid_initials']
        return block

    def reload_all(self):
        """通过缓存机制重新加载所有词库"""
        log("--- 开始重载所有词库 ---")
        self.cache = self._load_cache()
        
        all_libs = self.settings.libraries + self.settings.auto_libraries
        enabled_paths = {lib['path'] for lib in all_libs if lib.get('enabled', True)}
        
        new_word_blocks = []
        cache_updated = False

        for path in enabled_paths:
            current_hash = self._get_file_hash(path)
            cached_file = self.cache.get(path)

            if cached_file and cached_file.get('hash') == current_hash:
                log(f"缓存命中: {os.path.basename(path)}")
                new_word_blocks.extend(cached_file['data'])
            else:
                log(f"缓存未命中或已过期: {os.path.basename(path)}")
                source = WordSource(path) # WordSource.load() is called here
                
                preprocessed_data = [self._preprocess_block(block) for block in source.word_blocks]
                
                self.cache[path] = {
                    "hash": current_hash,
                    "data": preprocessed_data
                }
                new_word_blocks.extend(preprocessed_data)
                cache_updated = True

        # 移除缓存中不再启用的词库
        paths_to_remove = set(self.cache.keys()) - enabled_paths
        if paths_to_remove:
            for path in paths_to_remove:
                del self.cache[path]
            cache_updated = True

        self.word_blocks = new_word_blocks
        self.word_blocks.sort(key=lambda block: self._get_pinyin_sort_key(block['parent']))
        
        if cache_updated:
            self._save_cache()

        log(f"已聚合 {len(self.word_blocks)} 个词条从 {len(enabled_paths)} 个启用的词库。")
        
        # 加载剪贴板历史（它不使用主缓存）
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
            self.clipboard_history.append(self._preprocess_block(block))
        log(f"已加载 {len(self.clipboard_history)} 条剪贴板历史。")

    def add_to_clipboard_history(self, text):
        """向剪贴板历史中添加新条目"""
        if not self.clipboard_source:
            # 确保 clipboard_source 已被初始化
            self.clipboard_source = WordSource(CLIPBOARD_HISTORY_FILE)

        # 在添加前，重新加载一次，确保拿到最新的历史记录
        self.clipboard_source.load()
        
        # 避免重复添加
        if any(block['parent'] == text for block in self.clipboard_source.word_blocks):
            log(f"剪贴板历史中已存在: '{text}'")
            return False

        # 限制历史数量
        while len(self.clipboard_source.word_blocks) >= self.settings.clipboard_memory_count:
            oldest_item = self.clipboard_source.word_blocks.pop(0) # 移除最旧的
            self.clipboard_source.delete_entry(oldest_item['full_content'])
            log(f"剪贴板历史已满，移除最旧条目: {oldest_item['parent']}")

        # 添加新条目
        content_to_add = f"- {text}"
        if self.clipboard_source.add_entry(content_to_add):
            log(f"已添加新剪贴板历史: '{text}'")
            # 重新加载以更新内部状态
            self.reload_all()
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
        """聚合所有启用的词库数据 (此方法现在由 reload_all 替代)"""
        # 这个方法现在是多余的，因为 reload_all() 已经处理了聚合。
        # 保留一个空实现或直接移除，并更新调用点。
        # 为了安全起见，暂时保留，但其逻辑已被移至 reload_all。
        pass

    def find_matches(self, query, multi_word_search_enabled=False, pinyin_search_enabled=False):
        """
        全新的、基于字符映射表的精确匹配算法。
        取代了旧的 fuzzywuzzy 模糊匹配。
        【已重构】根据用户需求，实现分情况显示逻辑。
        """
        # 1. 当搜索框为空时
        if not query:
            # 清理所有可能存在的高亮标记
            for block in self.word_blocks:
                if 'highlight_groups' in block: del block['highlight_groups']
            for block in self.clipboard_history:
                if 'highlight_groups' in block: del block['highlight_groups']

            # 如果剪贴板记忆开启，只显示剪贴板历史（按时间倒序）
            if self.settings.clipboard_memory_enabled:
                return self.clipboard_history
            # 如果剪贴板记忆关闭，返回空列表以提高性能
            else:
                return []

        # 2. 当有搜索词时（全局搜索模式）
        search_pool = self.word_blocks
        query_lower = query.lower()
        keywords = [k for k in query_lower.split(' ') if k] if multi_word_search_enabled and ' ' in query_lower.strip() else [query_lower]
        
        scored_blocks = []

        for block in search_pool:
            char_map = block.get('char_map', [])
            if not char_map: continue

            all_match_groups = {}
            total_score = 0
            all_keywords_found = True
            used_indices_for_block = set() # 新增：跟踪此块中已使用的索引

            for kw_idx, kw in enumerate(keywords):
                best_match_for_kw = None
                # 遍历所有可能的起始点
                for start_idx in range(len(char_map)):
                    match_indices = []
                    match_types = []
                    kw_ptr = 0
                    map_ptr = start_idx
                    
                    # 尝试从 start_idx 开始匹配
                    while kw_ptr < len(kw) and map_ptr < len(char_map):
                        # 跳过已经被使用的索引
                        if map_ptr in used_indices_for_block:
                            map_ptr += 1
                            continue

                        char_info = char_map[map_ptr]
                        
                        # 1. 优先原文匹配
                        original_match = False
                        if kw[kw_ptr:].startswith(char_info['char'].lower()):
                            match_len = len(char_info['char'])
                            match_indices.extend(range(map_ptr, map_ptr + 1)) # 原文匹配总是单字符
                            match_types.append('original')
                            kw_ptr += match_len
                            map_ptr += 1
                            original_match = True
                        
                        # 2. 拼音匹配
                        elif pinyin_search_enabled:
                            pinyin_matched = False
                            for pinyin_key in char_info['keys']:
                                if kw[kw_ptr:].startswith(pinyin_key):
                                    match_indices.append(map_ptr)
                                    match_types.append('pinyin')
                                    kw_ptr += len(pinyin_key)
                                    map_ptr += 1
                                    pinyin_matched = True
                                    break
                            if not pinyin_matched and not original_match:
                                break # 当前字符无法匹配，中断
                        else:
                            break
 
                    # 如果整个关键词都匹配成功
                    if kw_ptr == len(kw):
                        current_match_indices = set(match_indices)
                        # 检查找到的匹配是否与已使用的索引重叠
                        if used_indices_for_block.isdisjoint(current_match_indices):
                            score = 0
                            # 计算得分
                            for mt in match_types:
                                score += 2 if mt == 'original' else 1 # 原文匹配得分更高
                            
                            # 连续性奖励
                            if len(current_match_indices) > 0 and len(current_match_indices) == (max(current_match_indices) - min(current_match_indices) + 1):
                                score *= 1.5
                            
                            if best_match_for_kw is None or score > best_match_for_kw['score']:
                                best_match_for_kw = {
                                    'score': score,
                                    'indices': current_match_indices
                                }
                
                if best_match_for_kw:
                    # 将找到的最佳匹配的索引添加到已使用集合中
                    used_indices_for_block.update(best_match_for_kw['indices'])
                    all_match_groups[kw_idx] = best_match_for_kw['indices']
                    total_score += best_match_for_kw['score']
                else:
                    all_keywords_found = False
                    break
            
            if all_keywords_found:
                # 距离惩罚
                if len(all_match_groups) > 1:
                    all_indices = set().union(*all_match_groups.values())
                    span = max(all_indices) - min(all_indices)
                    total_score /= (1 + span * 0.1)

                # 开头匹配奖励
                if 0 in all_match_groups.get(0, set()):
                    total_score *= 1.2

                # --- 精确高亮 ---
                parent_text = block['parent']
                full_content = block['full_content']
                parent_start_in_full = full_content.find(parent_text)
                if parent_start_in_full != -1:
                    block['highlight_groups'] = {
                        g_idx: {idx + parent_start_in_full for idx in g_indices}
                        for g_idx, g_indices in all_match_groups.items()
                    }
                else:
                    block['highlight_groups'] = {}
                
                scored_blocks.append((block, total_score))

        scored_blocks.sort(key=lambda x: x[1], reverse=True)
        return [block for block, score in scored_blocks]


    def get_source_by_path(self, path):
        """
        通过路径获取 WordSource 对象。
        如果内存中不存在，则会创建一个新的临时实例。
        """
        for source in self.sources:
            if source.file_path == path:
                return source
        
        # 如果在 self.sources 中找不到，说明可能是个刚添加或变动的词库
        # 创建一个临时的 WordSource 对象来处理这种情况
        log(f"在内存中未找到 source，为路径 {path} 创建临时 WordSource 实例。")
        all_libs = self.settings.libraries + self.settings.auto_libraries
        if any(lib['path'] == path for lib in all_libs):
            new_source = WordSource(path)
            self.sources.append(new_source) # 添加到列表中以备后用
            return new_source
            
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
        self.agree_checkbox.setStyleSheet(f"QCheckBox {{ font-size: {font_size-1}px; }}")
        
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
            # 仅当鼠标在标题栏区域（例如高度小于35）且未点击关闭按钮时，才开始拖动
            elif pos.y() < 35:
                actual_widget = QApplication.widgetAt(global_pos)
                if actual_widget != self.close_button:
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

    def reregister(self, new_hotkey_str):
        """动态重新注册快捷键"""
        self.stop()
        self.mod, self.vk = self._parse_hotkey(new_hotkey_str)
        self.start()

    def start(self):
        if not self._running:
            self._running = True
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("原生快捷键监听线程已启动。")

    def stop(self):
        """
        停止监听线程。
        【已加固】增加了对线程状态的检查和更安全的退出机制。
        """
        if self._running and self.thread:
            self._running = False
            try:
                # PostThreadMessageA 需要一个有效的线程ID。
                # 如果线程已经结束，ident 可能会无效。
                if self.thread.is_alive():
                    ctypes.windll.user32.PostThreadMessageA(self.thread.ident, 0x0012, 0, 0) # WM_QUIT
                    self.thread.join(timeout=1.5) # 给予更长的超时时间
            except Exception as e:
                log(f"CRITICAL: 停止原生快捷键监听线程时出错: {e}")
            finally:
                self.thread = None
                log("原生快捷键监听服务已停止。")

# --- 快捷码监听器 ---
class ShortcutListener(QObject):
    shortcut_matched = Signal(str, str) # 发送匹配到的词条内容和快捷码本身

    def __init__(self, word_manager):
        super().__init__()
        self.word_manager = word_manager
        self.listener = None
        self.typed_buffer = ""
        self.shortcut_map = {}
        self.thread = None
        self._running = False
        self.keyboard_controller = keyboard.Controller()

    def update_shortcuts(self):
        """从词库更新快捷码映射"""
        self.shortcut_map = {}
        all_blocks = self.word_manager.word_blocks + self.word_manager.clipboard_history
        for block in all_blocks:
            if block.get('shortcut_code'):
                self.shortcut_map[block['shortcut_code'].lower()] = block
        log(f"快捷码监听器已更新，共 {len(self.shortcut_map)} 个快捷码。")

    def _on_press(self, key):
        """
        pynput 的按键事件回调函数。
        【已加固】增加了顶级的异常捕获，防止任何意外错误导致监听线程崩溃。
        """
        try:
            if not self._running:
                return False # 停止监听

            char = None
            if hasattr(key, 'char'):
                char = key.char
            elif key == keyboard.Key.space:
                char = ' '
            
            if char:
                self.typed_buffer += char
                buffer_lower = self.typed_buffer.lower()
                # 从最长的快捷码开始匹配，避免短码提前触发
                # (这是一个小的优化，但对健壮性有好处)
                sorted_codes = sorted(self.shortcut_map.keys(), key=len, reverse=True)
                for code in sorted_codes:
                    if buffer_lower.endswith(code):
                        log(f"快捷码 '{code}' 匹配成功!")
                        block = self.shortcut_map[code]
                        self.shortcut_matched.emit(block['full_content'], code)
                        self.typed_buffer = "" # 重置缓冲区
                        return # 匹配成功后立即返回
            else:
                # 任何非字符键（如Ctrl, Shift, Enter）都会重置缓冲区
                self.typed_buffer = ""

            # 限制缓冲区长度，防止内存无限增长
            if len(self.typed_buffer) > 50:
                self.typed_buffer = self.typed_buffer[-50:]

        except Exception as e:
            # 【关键】捕获所有未知异常，记录日志，并重置状态，但绝不让线程退出
            log(f"CRITICAL: 快捷码监听器 _on_press 发生严重错误: {e}")
            self.typed_buffer = ""

    def _listen(self):
        """监听线程的实际运行函数"""
        log("快捷码监听线程已启动。")
        with keyboard.Listener(on_press=self._on_press) as self.listener:
            self.listener.join()
        log("快捷码监听线程已停止。")

    def start(self):
        if not self._running:
            self._running = True
            self.update_shortcuts()
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
            log("快捷码监听服务已启动。")

    def stop(self):
        """
        停止监听线程。
        【已加固】增加了对 listener 和 thread 对象的检查。
        """
        if self._running:
            self._running = False
            try:
                if self.listener and self.listener.is_alive():
                    self.listener.stop()
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=1.5)
            except Exception as e:
                log(f"CRITICAL: 停止快捷码监听服务时出错: {e}")
            finally:
                self.listener = None
                self.thread = None
                log("快捷码监听服务已停止。")

# --- 主控制器 ---
class MainController(QObject):
    show_popup_signal = Signal()
    hide_popup_signal = Signal()

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

        self.file_watcher = QFileSystemWatcher(self)
        self.update_file_watcher()
        self.file_watcher.fileChanged.connect(self.schedule_reload)

        self.auto_dir_watcher = QFileSystemWatcher(self)
        if os.path.isdir(AUTO_LOAD_DIR):
            self.auto_dir_watcher.addPath(AUTO_LOAD_DIR)
        self.auto_dir_watcher.directoryChanged.connect(self.schedule_auto_lib_scan)

        self.reload_timer = QTimer(self); self.reload_timer.setSingleShot(True); self.reload_timer.setInterval(300); self.reload_timer.timeout.connect(self.reload_word_file)
        
        # 新增：用于延迟扫描自动加载目录的定时器
        self.auto_scan_timer = QTimer(self)
        self.auto_scan_timer.setSingleShot(True)
        self.auto_scan_timer.setInterval(500) # 500ms 延迟
        self.auto_scan_timer.timeout.connect(self.scan_and_update_auto_libraries)

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

    def update_file_watcher(self):
        """更新文件监控器以包含所有词库文件"""
        all_libs = self.settings.libraries + self.settings.auto_libraries
        paths = [lib['path'] for lib in all_libs]
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
        if self.shortcut_listener and self.settings.shortcut_code_enabled:
            self.shortcut_listener.update_shortcuts() # 更新快捷码地图
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
                self.reload_word_file()
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
                if is_clipboard:
                    self.word_manager.load_clipboard_history()
                else:
                    self.reload_word_file()
                
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
                if is_clipboard:
                    self.word_manager.load_clipboard_history()
                else:
                    self.reload_word_file()
                
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
            self.reload_word_file() # 重新加载词库以更新缓存

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
        self.word_manager.reload_all() # 改为调用 reload_all 以更新缓存
        self.rebuild_library_menu()

    @Slot(str)
    def toggle_auto_library_enabled(self, path):
        for lib in self.settings.auto_libraries:
            if lib.get('path') == path:
                lib['enabled'] = not lib.get('enabled', True)
                break
        self.settings.save()
        self.word_manager.reload_all() # 改为调用 reload_all 以更新缓存
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
        """打开帮助文档文件夹"""
        try:
            if not os.path.exists(HELP_DOCS_DIR):
                os.makedirs(HELP_DOCS_DIR)
                log(f"已创建帮助文档文件夹: {HELP_DOCS_DIR}")
            webbrowser.open(HELP_DOCS_DIR)
            log(f"尝试打开帮助文档文件夹: {HELP_DOCS_DIR}")
        except Exception as e:
            log(f"打开帮助文档文件夹失败: {e}")
            QMessageBox.warning(self.popup, "错误", f"无法打开文件夹路径：\n{HELP_DOCS_DIR}\n\n错误: {e}")  

    def scan_and_update_auto_libraries(self):
        """扫描自动加载文件夹，同步词库列表并保存状态"""
        log("开始扫描自动加载词库文件夹...")
        if not os.path.isdir(AUTO_LOAD_DIR):
            log(f"自动加载目录不存在: {AUTO_LOAD_DIR}")
            self.rebuild_auto_library_menu() # 确保即使目录被删除，菜单也能刷新
            return

        try:
            found_files = {os.path.join(AUTO_LOAD_DIR, f) for f in os.listdir(AUTO_LOAD_DIR) if f.endswith('.md')}
        except Exception as e:
            log(f"扫描自动加载目录时出错: {e}")
            return

        existing_paths = {lib['path'] for lib in self.settings.auto_libraries}
        
        new_files = found_files - existing_paths
        removed_files = existing_paths - found_files
        
        changed = False
        if new_files:
            for path in new_files:
                self.settings.auto_libraries.append({"path": path, "enabled": True})
                log(f"发现并添加新自动词库: {os.path.basename(path)}")
            changed = True

        if removed_files:
            self.settings.auto_libraries = [lib for lib in self.settings.auto_libraries if lib['path'] not in removed_files]
            for path in removed_files:
                log(f"移除不存在的自动词库: {os.path.basename(path)}")
            changed = True

        if changed:
            self.settings.save()
            self.reload_word_file() # 重新加载所有词库
            self.rebuild_auto_library_menu()
        else:
            # 如果没有变化，但菜单是空的（可能在启动时），也强制刷新一次
            if not self.auto_library_menu.actions():
                self.rebuild_auto_library_menu()


# --- main入口 ---
if __name__ == "__main__":
    # --- 启用高DPI支持 ---
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
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
        
    # --- 确保自动加载文件夹存在 ---
    if not os.path.exists(AUTO_LOAD_DIR):
        try:
            os.makedirs(AUTO_LOAD_DIR)
            log(f"已创建自动加载词库文件夹: {AUTO_LOAD_DIR}")
        except Exception as e:
            log(f"创建自动加载文件夹失败: {e}")

    tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), app); tray_icon.setToolTip("QuickKV")
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

    font_size_action = QAction("设置字体大小(&F)..."); font_size_action.triggered.connect(controller.set_font_size); menu.addAction(font_size_action)

    # --- 帮助 ---
    menu.addSeparator()
    help_action = QAction("帮助")
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