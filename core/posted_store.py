# -*- coding: utf-8 -*-
"""Lưu trữ các bài đã đăng để tab Auto Comment dùng lại (up bài)."""
import json
import os
from datetime import datetime

from core.accounts import BASE_DIR

POSTED_PATH = os.path.join(BASE_DIR, "posted.json")


class PostedStore:
    """Danh sách bài đã đăng, lưu JSON. Mỗi bài: post_id, nhóm, snippet, số lần đã up."""

    def __init__(self, path=POSTED_PATH):
        self.path = path
        self.posts = []
        self.load()

    def add(self, group_id, group_name, post_id, snippet=""):
        if not post_id or any(p["post_id"] == post_id for p in self.posts):
            return
        self.posts.insert(0, {
            "post_id": post_id,
            "group_id": group_id,
            "group_name": group_name,
            "snippet": (snippet or "")[:80],
            "posted_at": datetime.now().isoformat(timespec="seconds"),
            "up_count": 0,
        })
        self.save()

    def remove(self, post_id):
        self.posts = [p for p in self.posts if p["post_id"] != post_id]
        self.save()

    def clear(self):
        self.posts = []
        self.save()

    def increment_up(self, post_id):
        for p in self.posts:
            if p["post_id"] == post_id:
                p["up_count"] = p.get("up_count", 0) + 1
                break
        self.save()

    def list(self):
        return list(self.posts)

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.posts = json.load(f) or []
        except (FileNotFoundError, json.JSONDecodeError):
            self.posts = []

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.posts, f, ensure_ascii=False, indent=2)
