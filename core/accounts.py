# -*- coding: utf-8 -*-
"""Quản lý tài khoản & profile: lưu JSON, HWID máy. Không lưu mật khẩu plaintext."""
import hashlib
import json
import os
import uuid
from datetime import datetime

BASE_DIR = os.path.join(os.path.expanduser("~"), ".proautoposter")
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")


def get_hwid():
    """Mã định danh máy, định dạng PRO-XXXXXXXXXXXX."""
    digest = hashlib.md5(str(uuid.getnode()).encode("utf-8")).hexdigest()[:12].upper()
    return "PRO-" + digest


class AccountManager:
    def __init__(self, path=ACCOUNTS_PATH):
        self.path = path
        self.accounts = {}
        self.load()
        if not self.accounts:
            # Seed giống ảnh chụp màn hình để chạy thử giao diện
            self.add("testcho", display_name="testcho", expiry="2026-09-23")

    def add(self, username, display_name="", expiry="", note=""):
        self.accounts[username] = {
            "username": username,
            "display_name": display_name or username,
            "expiry": expiry,  # HSD
            "note": note,
            "proxy": "",  # proxy riêng: "http://user:pass@host:port", trống = không dùng
            "cookies_file": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "last_login": "",
        }
        self.save()

    def remove(self, username):
        self.accounts.pop(username, None)
        self.save()

    def get(self, username):
        return self.accounts.get(username)

    def get_proxy(self, username):
        """Proxy riêng của tài khoản ('' = không dùng)."""
        return (self.accounts.get(username) or {}).get("proxy", "") or ""

    def set_proxy(self, username, proxy):
        """Lưu proxy riêng cho tài khoản. Trả về False nếu không có tài khoản."""
        acc = self.accounts.get(username)
        if acc is None:
            return False
        acc["proxy"] = (proxy or "").strip()
        self.save()
        return True

    def list(self):
        return list(self.accounts.values())

    def touch_login(self, username):
        acc = self.accounts.get(username)
        if acc:
            acc["last_login"] = datetime.now().isoformat(timespec="seconds")
            self.save()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.accounts = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.accounts = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, ensure_ascii=False, indent=2)
