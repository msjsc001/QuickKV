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
from core.word_source import WordSource

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
