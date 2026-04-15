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
        self.active_file_paths = set()
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

    def _normalize_aliases(self, aliases):
        """清洗别名列表，保留原顺序并按小写去重。"""
        normalized_aliases = []
        seen = set()
        for alias in aliases or []:
            clean_alias = alias.strip()
            if not clean_alias:
                continue
            alias_key = clean_alias.lower()
            if alias_key in seen:
                continue
            seen.add(alias_key)
            normalized_aliases.append(clean_alias)
        return normalized_aliases

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
        aliases = self._normalize_aliases(block.get('aliases', []))
        block['aliases'] = aliases
        block['alias_search_entries'] = [
            {
                'text': alias,
                'char_map': self._build_char_map(alias)
            }
            for alias in aliases
        ]
        # 移除旧的、不再使用的键
        if 'hybrid_initials' in block:
            del block['hybrid_initials']
        return block

    def _match_keyword_in_char_map(self, keyword, char_map, pinyin_search_enabled=False, used_indices=None):
        """在指定字符映射表中寻找单个关键词的最佳命中。"""
        if not keyword or not char_map:
            return None

        used_indices = used_indices or set()
        best_match = None

        for start_idx in range(len(char_map)):
            match_indices = []
            match_types = []
            kw_ptr = 0
            map_ptr = start_idx

            while kw_ptr < len(keyword) and map_ptr < len(char_map):
                if map_ptr in used_indices:
                    map_ptr += 1
                    continue

                char_info = char_map[map_ptr]
                original_match = False
                if keyword[kw_ptr:].startswith(char_info['char'].lower()):
                    match_indices.append(map_ptr)
                    match_types.append('original')
                    kw_ptr += len(char_info['char'])
                    map_ptr += 1
                    original_match = True
                elif pinyin_search_enabled:
                    pinyin_matched = False
                    for pinyin_key in char_info['keys']:
                        if keyword[kw_ptr:].startswith(pinyin_key):
                            match_indices.append(map_ptr)
                            match_types.append('pinyin')
                            kw_ptr += len(pinyin_key)
                            map_ptr += 1
                            pinyin_matched = True
                            break
                    if not pinyin_matched and not original_match:
                        break
                else:
                    break

            if kw_ptr != len(keyword):
                continue

            current_match_indices = set(match_indices)
            if used_indices and not used_indices.isdisjoint(current_match_indices):
                continue

            score = sum(2 if match_type == 'original' else 1 for match_type in match_types)
            if current_match_indices and len(current_match_indices) == (max(current_match_indices) - min(current_match_indices) + 1):
                score *= 1.5
            if 0 in current_match_indices:
                score *= 1.2

            match = {
                'score': score,
                'indices': current_match_indices,
            }
            if best_match is None or score > best_match['score']:
                best_match = match

        return best_match

    def _clear_highlight_groups(self):
        for block in self.word_blocks:
            if 'highlight_groups' in block:
                del block['highlight_groups']
        for block in self.clipboard_history:
            if 'highlight_groups' in block:
                del block['highlight_groups']

    def _expand_library_entries(self, libraries):
        """将文件/文件夹两类词库条目展开为真实可加载的 md 文件。"""
        expanded_paths = {}
        for lib in libraries:
            if not lib.get('enabled', True):
                continue

            lib_kind = lib.get('kind', 'file')
            if lib_kind == 'folder':
                candidate_paths = list_eligible_md_files(lib.get('path'))
            else:
                lib_path = lib.get('path')
                candidate_paths = [os.path.abspath(lib_path)] if is_eligible_library_file(lib_path) else []

            for candidate_path in candidate_paths:
                norm_path = normalize_library_path(candidate_path)
                expanded_paths[norm_path] = os.path.abspath(candidate_path)

        return expanded_paths

    def reload_all(self):
        """通过缓存机制重新加载所有词库"""
        log("--- 开始重载所有词库 ---")
        self.cache = self._load_cache()

        norm_to_original = self._expand_library_entries(self.settings.libraries)
        norm_to_original.update(self._expand_library_entries(self.settings.auto_libraries))
        unique_enabled_paths = set(norm_to_original.values())
        self.active_file_paths = set(norm_to_original.keys())
        self.sources = []

        new_word_blocks = []
        cache_updated = False

        for norm_path, original_path in norm_to_original.items():
            current_hash = self._get_file_hash(original_path)
            cached_file = self.cache.get(norm_path)

            if cached_file and cached_file.get('hash') == current_hash:
                log(f"缓存命中: {os.path.basename(original_path)}")
                new_word_blocks.extend([self._preprocess_block(block) for block in cached_file['data']])
            else:
                log(f"缓存未命中或已过期: {os.path.basename(original_path)}")
                source = WordSource(original_path) # WordSource.load() is called here
                
                preprocessed_data = [self._preprocess_block(block) for block in source.word_blocks]
                
                self.cache[norm_path] = {
                    "hash": current_hash,
                    "data": preprocessed_data
                }
                new_word_blocks.extend(preprocessed_data)
                cache_updated = True

        # 移除缓存中不再启用的词库
        paths_to_remove = set(self.cache.keys()) - set(norm_to_original.keys())
        if paths_to_remove:
            for path in paths_to_remove:
                del self.cache[path]
            cache_updated = True

        self.word_blocks = new_word_blocks
        self.word_blocks.sort(key=lambda block: self._get_pinyin_sort_key(block['parent']))
        
        if cache_updated:
            self._save_cache()

        log(f"已聚合 {len(self.word_blocks)} 个词条从 {len(unique_enabled_paths)} 个启用的词库。")
        
        # 加载剪贴板历史（它不使用主缓存）
        self.load_clipboard_history()

    def load_clipboard_history(self):
        """加载剪贴板历史文件"""
        if not os.path.exists(CLIPBOARD_HISTORY_FILE):
            try:
                with open(CLIPBOARD_HISTORY_FILE, 'w', encoding='utf-8') as f:
                    f.write("")
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

        full_content_to_add = f"- {text}"
        # 避免重复添加
        if any(block['full_content'] == full_content_to_add for block in self.clipboard_source.word_blocks):
            log(f"剪贴板历史中已存在: '{text}'")
            return False

        # 限制历史数量
        while len(self.clipboard_source.word_blocks) >= self.settings.clipboard_memory_count:
            oldest_item = self.clipboard_source.word_blocks.pop(0) # 移除最旧的
            self.clipboard_source.delete_entry(oldest_item['full_content'])
            log(f"剪贴板历史已满，移除最旧条目: {oldest_item['parent']}")

        # 添加新条目
        if self.clipboard_source.add_entry(full_content_to_add):
            log(f"已添加新剪贴板历史: '{text}'")
            # 重新加载以更新内部状态
            self.reload_all()
            return True
        return False

    def clear_clipboard_history(self):
        """清空剪贴板历史"""
        if not self.clipboard_source: return
        try:
            # 删除文件内容，保持历史真正为空
            with open(self.clipboard_source.file_path, 'w', encoding='utf-8') as f:
                f.write("")
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
            self._clear_highlight_groups()

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
            alias_entries = block.get('alias_search_entries', [])
            if not char_map and not alias_entries:
                continue

            highlight_groups = {}
            total_score = 0
            all_keywords_found = True
            used_indices_for_block = set()

            for kw_idx, kw in enumerate(keywords):
                best_target = None

                parent_match = self._match_keyword_in_char_map(
                    kw,
                    char_map,
                    pinyin_search_enabled=pinyin_search_enabled,
                    used_indices=used_indices_for_block
                )
                if parent_match:
                    best_target = {
                        'target': 'parent',
                        'score': parent_match['score'],
                        'indices': parent_match['indices']
                    }

                for alias_entry in alias_entries:
                    alias_match = self._match_keyword_in_char_map(
                        kw,
                        alias_entry.get('char_map', []),
                        pinyin_search_enabled=pinyin_search_enabled
                    )
                    if not alias_match:
                        continue

                    alias_score = alias_match['score'] * 0.75
                    if best_target is None or alias_score > best_target['score']:
                        best_target = {
                            'target': 'alias',
                            'score': alias_score,
                            'indices': set()
                        }

                if not best_target:
                    all_keywords_found = False
                    break

                total_score += best_target['score']
                if best_target['target'] == 'parent':
                    used_indices_for_block.update(best_target['indices'])
                    highlight_groups[kw_idx] = best_target['indices']

            if all_keywords_found:
                # 距离惩罚
                if len(highlight_groups) > 1:
                    all_indices = set().union(*highlight_groups.values())
                    span = max(all_indices) - min(all_indices)
                    total_score /= (1 + span * 0.1)

                # --- 精确高亮 ---
                parent_text = block['parent']
                full_content = block['full_content']
                parent_start_in_full = full_content.find(parent_text)
                if parent_start_in_full != -1 and highlight_groups:
                    block['highlight_groups'] = {
                        g_idx: {idx + parent_start_in_full for idx in g_indices}
                        for g_idx, g_indices in highlight_groups.items()
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
        norm_search_path = os.path.normcase(os.path.abspath(path))
        for source in self.sources:
            if os.path.normcase(os.path.abspath(source.file_path)) == norm_search_path:
                return source
        
        # 如果在 self.sources 中找不到，说明可能是个刚添加或变动的词库
        # 创建一个临时的 WordSource 对象来处理这种情况
        log(f"在内存中未找到 source，为路径 {path} 创建临时 WordSource 实例。")
        clipboard_norm_path = normalize_library_path(CLIPBOARD_HISTORY_FILE)
        if norm_search_path in self.active_file_paths or norm_search_path == clipboard_norm_path:
            new_source = WordSource(path)
            self.sources.append(new_source) # 添加到列表中以备后用
            return new_source
            
        return None
