# -*- coding: utf-8 -*-
import json
import os
import hashlib
from datetime import datetime

from core.config import *


class RankingStateManager:
    """管理收藏与最近使用状态，不污染词库正文。"""

    STATE_VERSION = 1

    def __init__(self, file_path):
        self.file_path = file_path
        self.state = self._default_state()
        self.load()

    def _default_state(self):
        return {
            "version": self.STATE_VERSION,
            "favorites": {},
            "usage_stats": {},
        }

    def _ensure_state_shape(self, data):
        if not isinstance(data, dict):
            return self._default_state()

        favorites = data.get("favorites", {})
        usage_stats = data.get("usage_stats", {})

        if not isinstance(favorites, dict):
            favorites = {}
        if not isinstance(usage_stats, dict):
            usage_stats = {}

        normalized_usage = {}
        for entry_id, meta in usage_stats.items():
            if not isinstance(meta, dict):
                continue
            count = meta.get("count", 0)
            last_used_at = meta.get("last_used_at", "")
            try:
                count = max(0, int(count))
            except (TypeError, ValueError):
                count = 0
            if not isinstance(last_used_at, str):
                last_used_at = ""
            normalized_usage[str(entry_id)] = {
                "count": count,
                "last_used_at": last_used_at,
            }

        normalized_favorites = {
            str(entry_id): bool(flag)
            for entry_id, flag in favorites.items()
            if bool(flag)
        }

        return {
            "version": self.STATE_VERSION,
            "favorites": normalized_favorites,
            "usage_stats": normalized_usage,
        }

    def load(self):
        if not os.path.exists(self.file_path):
            self.state = self._default_state()
            log("排序状态文件不存在，将使用默认状态。")
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            self.state = self._ensure_state_shape(raw_data)
            log("已加载收藏与最近使用状态。")
        except Exception as e:
            self.state = self._default_state()
            log(f"加载排序状态失败，将回退到默认状态: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            log("收藏与最近使用状态已保存。")
        except Exception as e:
            log(f"保存排序状态失败: {e}")

    def make_entry_id(self, source_path, full_content):
        normalized_source = normalize_library_path(source_path or "")
        raw_identity = f"{normalized_source}\n{full_content or ''}"
        return hashlib.sha1(raw_identity.encode("utf-8")).hexdigest()

    def is_favorite(self, entry_id):
        return bool(self.state["favorites"].get(entry_id, False))

    def get_usage_meta(self, entry_id):
        meta = self.state["usage_stats"].get(entry_id, {})
        return {
            "count": int(meta.get("count", 0) or 0),
            "last_used_at": meta.get("last_used_at", "") or "",
        }

    def set_favorite(self, entry_id, is_favorite):
        if is_favorite:
            self.state["favorites"][entry_id] = True
        else:
            self.state["favorites"].pop(entry_id, None)
        self.save()

    def toggle_favorite(self, entry_id):
        new_state = not self.is_favorite(entry_id)
        self.set_favorite(entry_id, new_state)
        return new_state

    def record_use(self, entry_id, used_at=None):
        if not entry_id:
            return

        if used_at is None:
            used_at = datetime.now().astimezone()

        usage_meta = self.state["usage_stats"].setdefault(entry_id, {
            "count": 0,
            "last_used_at": "",
        })
        usage_meta["count"] = int(usage_meta.get("count", 0) or 0) + 1
        usage_meta["last_used_at"] = used_at.isoformat()
        self.save()

    def cleanup_orphans(self, valid_entry_ids):
        valid_ids = {entry_id for entry_id in valid_entry_ids if entry_id}
        changed = False

        for bucket_name in ("favorites", "usage_stats"):
            bucket = self.state.get(bucket_name, {})
            stale_ids = [entry_id for entry_id in bucket.keys() if entry_id not in valid_ids]
            if stale_ids:
                changed = True
                for stale_id in stale_ids:
                    bucket.pop(stale_id, None)

        if changed:
            self.save()

        return changed
