# -*- coding: utf-8 -*-
"""
QuickKV v1.0.5.28
"""
import sys
import os
import webbrowser
import configparser
import hashlib
import json
import re
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
AUTO_LOAD_DIR = os.path.join(BASE_PATH, "MD词库-需自动载入的请放入")
CACHE_FILE = os.path.join(BASE_PATH, "cache.json") # 新增：缓存文件路径

# --- 内部资源 ---
ICON_PATH = resource_path("icon.png")

# --- 其他配置 ---
DEBUG_MODE = True
VERSION = "1.0.5.28" # 版本号

def log(message):
    if DEBUG_MODE:
        print(f"[LOG] {message}")

# --- 主题颜色定义 ---
THEMES = {
    "dark": {
        "bg_color": "#21252b", "border_color": "#3c424b", "text_color": "#d1d5db",
        "title_color": "#8c929c", # 新增：弱化的标题颜色
        "input_bg_color": "#2c313a", "item_hover_bg": "#3a3f4b",
        "item_selected_bg": "#405061", "item_selected_text": "#d1d5db"
    },
    "light": {
        "bg_color": "#fdfdfd", "border_color": "#cccccc", "text_color": "#202020",
        "title_color": "#a0a0a0", # 新增：弱化的标题颜色
        "input_bg_color": "#ffffff", "item_hover_bg": "#f0f0f0",
        "item_selected_bg": "#dbe4ee", "item_selected_text": "#202020"
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
        
        # 在每个项目底部画一条分隔线
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
        self.config.read(self.file_path, encoding='utf-8')
        if not self.config.has_section('Window'): self.config.add_section('Window')
        if not self.config.has_section('Theme'): self.config.add_section('Theme')
        if not self.config.has_section('Font'): self.config.add_section('Font')
        if not self.config.has_section('Search'): self.config.add_section('Search')
        if not self.config.has_section('Data'): self.config.add_section('Data')
        if not self.config.has_section('General'): self.config.add_section('General')
        if not self.config.has_section('Clipboard'): self.config.add_section('Clipboard')
        if not self.config.has_section('Restart'): self.config.add_section('Restart')
        if not self.config.has_section('Paste'): self.config.add_section('Paste')

        self.hotkeys_enabled = self.config.getboolean('General', 'hotkeys_enabled', fallback=True)
        self.shortcut_code_enabled = self.config.getboolean('General', 'shortcut_code_enabled', fallback=False) # 新增：快捷码功能开关
        self.hotkey = self.config.get('General', 'hotkey', fallback='ctrl+space')
        self.paste_mode = self.config.get('Paste', 'mode', fallback='ctrl_v')
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

        auto_libraries_str = self.config.get('General', 'auto_libraries', fallback='[]')
        try:
            self.auto_libraries = json.loads(auto_libraries_str)
        except json.JSONDecodeError:
            self.auto_libraries = []

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
        self.config['General']['libraries'] = json.dumps(self.libraries, ensure_ascii=False)
        self.config['General']['auto_libraries'] = json.dumps(self.auto_libraries, ensure_ascii=False)
        self.config['Clipboard']['enabled'] = str(self.clipboard_memory_enabled)
        self.config['Clipboard']['count'] = str(self.clipboard_memory_count)
        self.config['Restart']['enabled'] = str(self.auto_restart_enabled)
        self.config['Restart']['interval_minutes'] = str(self.auto_restart_interval)
        self.config['Paste']['mode'] = self.paste_mode
        
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
        """尝试从文件加载缓存"""
        if not os.path.exists(CACHE_FILE):
            log("缓存文件不存在，将创建新缓存。")
            return {}
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            # 简单版本校验
            if cache_data.get("version") != "1.0":
                log("缓存版本不兼容，将创建新缓存。")
                return {}
            log("成功从文件加载缓存。")
            return cache_data.get("files", {})
        except (json.JSONDecodeError, Exception) as e:
            log(f"加载缓存失败: {e}，将创建新缓存。")
            return {}

    def _save_cache(self):
        """将当前缓存数据保存到文件"""
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({"version": "1.0", "files": self.cache}, f, ensure_ascii=False, indent=2)
            log("缓存已成功保存。")
        except Exception as e:
            log(f"保存缓存失败: {e}")

    def _preprocess_block(self, block):
        """对单个词条块进行预处理"""
        parent_text = block['parent']
        block['parent_lower'] = parent_text.lower()
        block['pinyin_initials'] = self._get_pinyin_initials(parent_text)
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
            oldest_item = self.clipboard_history.pop(-1) # 移除最旧的
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
        """聚合所有启用的词库数据 (此方法现在由 reload_all 替代)"""
        # 这个方法现在是多余的，因为 reload_all() 已经处理了聚合。
        # 保留一个空实现或直接移除，并更新调用点。
        # 为了安全起见，暂时保留，但其逻辑已被移至 reload_all。
        pass

    def _calculate_match_score(self, parent_text, query_lower, keywords):
        score = 0
        parent_lower = parent_text.lower()

        # 基础分：只要包含就给分
        if query_lower in parent_lower:
            score += 10
        
        # 奖励分
        if parent_lower == query_lower:
            score += 1000  # 完全匹配
        elif parent_lower.startswith(query_lower):
            score += 100   # 起始匹配
        
        # 长度惩罚 (倒数形式，差值越大，加分越少)
        length_diff = len(parent_lower) - len(query_lower)
        if length_diff >= 0:
            score += 50 / (length_diff + 1)

        # 多词匹配奖励
        if keywords and all(kw in parent_lower for kw in keywords):
            keywords_len = sum(len(kw) for kw in keywords)
            # 关键词占比越高，分数越高
            score += (keywords_len / len(parent_lower)) * 30
        
        return score

    def find_matches(self, query, multi_word_search_enabled=False, pinyin_search_enabled=False):
        # 1. 特殊情况：无搜索时，按要求显示
        if not query:
            if self.settings.clipboard_memory_enabled:
                # 预处理剪贴板历史以便显示
                return [self._preprocess_block(b) for b in self.clipboard_history]
            else:
                return self.word_blocks # 返回所有词库

        # 2. 有搜索时，构建搜索池并进行评分排序
        search_pool = self.word_blocks[:] # 创建副本
        if self.settings.clipboard_memory_enabled:
            # 实时预处理剪贴板历史记录以进行搜索
            processed_clipboard = [self._preprocess_block(b) for b in self.clipboard_history]
            search_pool.extend(processed_clipboard)

        query_lower = query.lower()
        scored_blocks = []
        
        keywords = [k for k in query_lower.split(' ') if k] if multi_word_search_enabled and ' ' in query_lower.strip() else []

        for block in search_pool:
            parent_text = block['parent']
            # 使用缓存的 parent_lower 和 pinyin_initials
            parent_lower = block.get('parent_lower', parent_text.lower())
            parent_initials_list = block.get('pinyin_initials', []) if pinyin_search_enabled else []
            
            is_match, is_pinyin_match = False, False

            # --- 匹配逻辑 ---
            if keywords:
                all_keywords_matched = True
                for kw in keywords:
                    keyword_found = kw in parent_lower or any(kw in initials for initials in parent_initials_list)
                    if not keyword_found:
                        all_keywords_matched = False
                        break
                
                if all_keywords_matched:
                    is_match = True
                    if pinyin_search_enabled and not all(kw in parent_lower for kw in keywords):
                        is_pinyin_match = True
            else:
                text_match = query_lower in parent_lower
                pinyin_match = pinyin_search_enabled and any(query_lower in initials for initials in parent_initials_list)

                if text_match or pinyin_match:
                    is_match = True
                    if pinyin_match and not text_match: is_pinyin_match = True

            # --- 计分 ---
            if is_match:
                score = self._calculate_match_score(parent_text, query_lower, keywords)
                if is_pinyin_match: score *= 0.5
                scored_blocks.append((block, score))

        # --- 排序 ---
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
            item.setData(Qt.UserRole, block) # 存储完整数据块
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
        if self._running:
            self._running = False
            # 发送一个空消息来唤醒 GetMessageA 循环，使其能检查 _running 标志
            # 需要获取线程ID来发送消息
            ctypes.windll.user32.PostThreadMessageA(self.thread.ident, 0x0012, 0, 0) # WM_QUIT
            self.thread.join(timeout=1) # 等待线程结束
            log("原生快捷键监听线程已停止。")

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
        if not self._running:
            return False

        try:
            char = None
            if hasattr(key, 'char'):
                char = key.char
            elif key == keyboard.Key.space:
                char = ' '
            
            if char:
                self.typed_buffer += char
                buffer_lower = self.typed_buffer.lower()
                for code, block in self.shortcut_map.items():
                    if buffer_lower.endswith(code):
                        log(f"快捷码 '{code}' 匹配成功!")
                        self.shortcut_matched.emit(block['full_content'], code)
                        self.typed_buffer = "" # 重置缓冲区
                        break 
            else:
                self.typed_buffer = ""

        except Exception as e:
            log(f"快捷码监听器处理按键时出错: {e}")
            self.typed_buffer = "" 

        if len(self.typed_buffer) > 50:
            self.typed_buffer = self.typed_buffer[-50:]

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
        if self._running:
            self._running = False
            if self.listener:
                self.listener.stop()
            if self.thread:
                self.thread.join(timeout=1)
            log("快捷码监听服务已停止。")

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
                if self.ignore_next_clipboard_change:
                    log("忽略本次剪贴板变化（由程序自身触发）。")
                    self.ignore_next_clipboard_change = False
                    self.last_clipboard_text = current_text # 仍然更新 last_clipboard_text 以防止重复记录
                    return

                # --- 换行符规范化 ---
                normalized_text = '\n'.join(current_text.splitlines())
                log(f"检测到新的剪贴板内容 (规范化后): '{normalized_text}'")
                self.last_clipboard_text = current_text # 原始文本用于比较
                was_added = self.word_manager.add_to_clipboard_history(normalized_text)
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
        """根据用户设置，通过 PowerShell 执行不同的粘贴操作"""
        mode = self.settings.paste_mode
        log(f"准备执行粘贴，模式: {mode}")

        ps_command = ""
        if mode == 'ctrl_v':
            ps_command = (
                "powershell.exe -WindowStyle Hidden -Command "
                "\"Start-Sleep -Milliseconds 150; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v')\""
            )
        elif mode == 'ctrl_shift_v':
            ps_command = (
                "powershell.exe -WindowStyle Hidden -Command "
                "\"Start-Sleep -Milliseconds 150; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('+^v')\""
            )
        elif mode == 'typing':
            ps_command = (
                "powershell.exe -WindowStyle Hidden -Command "
                "\"Start-Sleep -Milliseconds 150; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$clipboardText = Get-Clipboard; "
                "$escapedText = $clipboardText -replace '([\\+\\^\\%\\~\\(\\)\\[\\]\\{\\}])', '{$1}'; "
                "[System.Windows.Forms.SendKeys]::SendWait($escapedText)\""
            )

        if ps_command:
            try:
                QProcess.startDetached(ps_command)
                log(f"PowerShell 粘贴命令 ({mode}) 已成功派发。")
            except Exception as e:
                log(f"启动 PowerShell 粘贴进程时发生错误: {e}")
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

        is_clipboard = found_block.get('is_clipboard', False)
        source_path = found_block.get('source_path')
        
        source = self.word_manager.clipboard_source if is_clipboard else self.word_manager.get_source_by_path(source_path)

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

        is_clipboard = found_block.get('is_clipboard', False)
        source_path = found_block.get('source_path')

        source = self.word_manager.clipboard_source if is_clipboard else self.word_manager.get_source_by_path(source_path)

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

    # --- 确保自动加载文件夹存在 ---
    if not os.path.exists(AUTO_LOAD_DIR):
        try:
            os.makedirs(AUTO_LOAD_DIR)
            log(f"已创建自动加载词库文件夹: {AUTO_LOAD_DIR}")
        except Exception as e:
            log(f"创建自动加载文件夹失败: {e}")
    
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
    
    font_size_action = QAction("设置字体大小(&F)..."); font_size_action.triggered.connect(controller.set_font_size); menu.addAction(font_size_action)

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