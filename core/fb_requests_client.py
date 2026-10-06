# -*- coding: utf-8 -*-
"""
RequestsFacebookClient: đăng bài / bình luận Facebook thật bằng `requests`
qua giao diện mbasic (nhẹ, không cần trình duyệt).

Luồng sử dụng:
  1. login(email, password)  -> lấy cookies, lưu vào ~/.proautoposter/
  2. Các lần sau chỉ cần ensure_login() để nạp cookies (không nhập lại mật khẩu).

Thiết kế:
  - Mọi selector/endpooint mong manh gom ở các hàm _find_* / _ENDPOINT,
    dễ chỉnh khi Facebook đổi giao diện.
  - KHÔNG tự vượt checkpoint/captcha: gặp xác minh sẽ ném CheckpointRequired,
    bạn phải mở trình duyệt thật để xác minh rồi đăng nhập lại.
  - Chỉ hỗ trợ đăng bài TEXT qua requests; đăng kèm ảnh/video báo lỗi rõ ràng.
"""
import html
import json
import os
import re
import time
import urllib.parse
from html.parser import HTMLParser

from core.accounts import BASE_DIR
from core.facebook_client import FacebookClient

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


MBASIC = "https://mbasic.facebook.com"
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)

_PROFILE_BLACKLIST = {
    "login", "reg", "recover", "groups", "group", "story", "photo",
    "video", "watch", "marketplace", "messages", "notifications",
    "search", "settings", "help", "browse", "events", "pages",
    "profile.php", "checkpoint", "ads",
}


# ---------------- exceptions ----------------
class FacebookError(Exception):
    """Lỗi chung của RequestsFacebookClient."""


class LoginFailed(FacebookError):
    """Sai email/mật khẩu hoặc form đăng nhập đã thay đổi."""


class CheckpointRequired(FacebookError):
    """Facebook yêu cầu xác minh. Hãy mở trình duyệt thật để xác minh."""


class PostFailed(FacebookError):
    """Đăng bài / bình luận thất bại."""


class LoginRequired(FacebookError):
    """Chưa có cookies hợp lệ. Hãy đăng nhập trước."""


def _require_requests():
    if requests is None:
        raise FacebookError("Thiếu thư viện 'requests'. Cài bằng: pip install requests")


# ---------------- form parser (stdlib, không cần bs4) ----------------
class _FormParser(HTMLParser):
    """Trích mọi <form>: action, method, inputs, textarea."""

    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._current = {
                "action": attrs.get("action", ""),
                "method": attrs.get("method", "get").lower(),
                "inputs": {},
                "textareas": [],
            }
        elif self._current is not None:
            if tag == "input":
                name = attrs.get("name")
                if name:
                    self._current["inputs"][name] = attrs.get("value", "")
            elif tag == "textarea":
                name = attrs.get("name")
                if name:
                    self._current["textareas"].append(name)

    def handle_endtag(self, tag):
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _parse_forms(page_html):
    parser = _FormParser()
    parser.feed(page_html)
    return parser.forms


# ---------------- client ----------------
class RequestsFacebookClient(FacebookClient):
    def __init__(self, account):
        super().__init__(account)
        _require_requests()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": MOBILE_UA,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        })
        username = (account or {}).get("username", "default")
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(username))
        self.cookies_path = os.path.join(BASE_DIR, f"cookies_{safe}.json")

    # ---------- tiện ích ----------
    @staticmethod
    def _abs(action, base=MBASIC):
        return urllib.parse.urljoin(base.rstrip("/") + "/", action)

    def _get(self, url, **kw):
        resp = self.session.get(url, timeout=30, **kw)
        resp.raise_for_status()
        return resp

    def _is_logged_in(self):
        return "c_user" in self.session.cookies.get_dict()

    @staticmethod
    def _guess_field(inputs, hints):
        for key in inputs:
            if any(h in key.lower() for h in hints):
                return key
        return None

    @staticmethod
    def _find_login_form(forms):
        for form in forms:
            names = " ".join(form["inputs"].keys()).lower()
            if "pass" in names and "email" in names:
                return form
        return None

    @staticmethod
    def _find_composer_form(forms):
        for form in forms:
            if form["textareas"] and "composer" in form["action"]:
                return form
        for form in forms:  # fallback: form có textarea, không phải form login
            names = " ".join(form["inputs"].keys()).lower()
            if form["textareas"] and "email" not in names:
                return form
        return None

    @staticmethod
    def _find_comment_form(forms):
        for form in forms:
            if form["textareas"] and "comment" in form["action"]:
                return form
        for form in forms:
            names = " ".join(form["inputs"].keys()).lower()
            if form["textareas"] and "email" not in names:
                return form
        return None

    @staticmethod
    def _looks_like_success(page_html, message):
        snippet = message.strip()[:40]
        return bool(snippet) and snippet in page_html

    @staticmethod
    def _extract_post_id(page_html):
        m = (re.search(r"story_fbid=(\d+)", page_html)
             or re.search(r'"top_level_post_id":"(\d+)"', page_html))
        return m.group(1) if m else None

    @staticmethod
    def _extract_comment_id(page_html):
        m = (re.search(r"comment_id=(\d+)", page_html)
             or re.search(r'"commentID":"(\d+)"', page_html))
        return m.group(1) if m else None

    # ---------- cookies ----------
    def save_cookies(self):
        os.makedirs(BASE_DIR, exist_ok=True)
        data = requests.utils.dict_from_cookiejar(self.session.cookies)
        with open(self.cookies_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def load_cookies(self):
        try:
            with open(self.cookies_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        self.session.cookies.update(requests.utils.cookiejar_from_dict(data))
        return self._is_logged_in()

    def ensure_login(self):
        """Nạp cookies đã lưu. Ném LoginRequired nếu chưa đăng nhập lần nào."""
        if self._is_logged_in() or self.load_cookies():
            return True
        raise LoginRequired("Chưa đăng nhập. Hãy nhập Email/Mật khẩu rồi bấm "
                            "'Đăng nhập & lưu cookies'.")

    # ---------- FacebookClient API ----------
    def login(self, email=None, password=None):
        """Đăng nhập 1 lần để lấy cookies (mật khẩu không được lưu lại)."""
        if not email or not password:
            raise LoginFailed("Thiếu email hoặc mật khẩu.")
        home = self._get(MBASIC)
        form = self._find_login_form(_parse_forms(home.text))
        if form is None:
            if self._is_logged_in():  # đã login sẵn (ít gặp)
                self.save_cookies()
                return True
            raise LoginFailed("Không tìm thấy form đăng nhập (giao diện Facebook đã đổi?).")
        payload = dict(form["inputs"])
        email_field = self._guess_field(payload, ["email"])
        pass_field = self._guess_field(payload, ["pass"])
        if not email_field or not pass_field:
            raise LoginFailed("Không nhận diện được ô email/mật khẩu.")
        payload[email_field] = email
        payload[pass_field] = password
        resp = self.session.post(self._abs(form["action"]), data=payload, timeout=30)
        if "checkpoint" in resp.url:
            raise CheckpointRequired(
                "Facebook yêu cầu xác minh danh tính. Hãy mở trình duyệt thật, "
                "đăng nhập và hoàn tất xác minh, sau đó thử lại.")
        if self._is_logged_in():
            self.save_cookies()
            return True
        raise LoginFailed("Đăng nhập thất bại (sai email/mật khẩu hoặc tài khoản bị hạn chế).")

    def post_to_group(self, group_id, message, media_path=None):
        self.ensure_login()
        if media_path:
            raise PostFailed("Đăng kèm ảnh/video qua requests chưa hỗ trợ - "
                             "hãy đăng bài text trước.")
        page = self._get(f"{MBASIC}/groups/{group_id}")
        form = self._find_composer_form(_parse_forms(page.text))
        if form is None or not form["textareas"]:
            raise PostFailed(f"Không tìm thấy khung soạn bài trong nhóm {group_id}.")
        payload = dict(form["inputs"])
        payload[form["textareas"][0]] = message
        resp = self.session.post(self._abs(form["action"], f"{MBASIC}/groups/{group_id}"),
                                 data=payload, timeout=30)
        resp.raise_for_status()
        if self._looks_like_success(resp.text, message):
            post_id = (self._extract_post_id(resp.text)
                       or f"mbasic_{group_id}_{int(time.time())}")
            return {"post_id": post_id}
        raise PostFailed("Facebook không trả về dấu hiệu đăng thành công.")

    def comment(self, post_id, message):
        self.ensure_login()
        fbid = str(post_id).split("_")[-1]
        page = self._get(f"{MBASIC}/story.php?story_fbid={fbid}")
        form = self._find_comment_form(_parse_forms(page.text))
        if form is None or not form["textareas"]:
            raise PostFailed("Không tìm thấy khung bình luận.")
        payload = dict(form["inputs"])
        payload[form["textareas"][0]] = message
        resp = self.session.post(self._abs(form["action"]), data=payload, timeout=30)
        resp.raise_for_status()
        comment_id = self._extract_comment_id(resp.text) or f"c_{int(time.time())}"
        return {"comment_id": comment_id}

    def get_group_info(self, group_id):
        self.ensure_login()
        page = self._get(f"{MBASIC}/groups/{group_id}")
        m = re.search(r"<title>(.*?)</title>", page.text, re.S)
        name = html.unescape(m.group(1)).strip() if m else str(group_id)
        name = re.sub(r"\s*[|\-–]\s*Facebook.*$", "", name).strip() or str(group_id)
        return {"id": group_id, "name": name}

    def scan_group_uids(self, group_id, limit=100):
        """Quét UID/username thành viên nhóm (tối đa 20 trang, nghỉ 2s/trang)."""
        self.ensure_login()
        found, seen = [], set()
        url = f"{MBASIC}/browse/group/members/?id={group_id}"
        pages = 0
        while url and len(found) < limit and pages < 20:
            pages += 1
            resp = self._get(url)
            for href in re.findall(r'href="([^"]+)"', resp.text):
                uid = self._uid_from_href(href)
                if uid and uid not in seen:
                    seen.add(uid)
                    found.append(uid)
                    if len(found) >= limit:
                        break
            url = self._next_page(resp.text, url)
            if url:
                time.sleep(2)
        return found

    @staticmethod
    def _uid_from_href(href):
        href = html.unescape(href)
        # Bỏ link phân trang / điều hướng
        if any(x in href for x in ("start=", "cursor=", "more_members")):
            return None
        path = href.split("?", 1)[0].lower()
        if "profile.php" in path:
            m = re.search(r"[?&]id=(\d+)", href)
            return m.group(1) if m else None
        if ".php" in path:
            return None
        m = re.match(r"^/([\w.]{5,})(?:[?/#]|$)", href)
        if m and m.group(1).lower() not in _PROFILE_BLACKLIST:
            return m.group(1)
        return None

    @staticmethod
    def _next_page(page_html, current_url):
        m = re.search(r'href="([^"]+)"[^>]*>\s*(?:Xem thêm|See more)', page_html)
        if m:
            return urllib.parse.urljoin(current_url, html.unescape(m.group(1)))
        return None

    def check_account_status(self):
        try:
            self.ensure_login()
        except LoginRequired:
            return {"status": "no_session"}
        resp = self._get(MBASIC)
        if "checkpoint" in resp.url:
            return {"status": "checkpoint"}
        return {"status": "live" if self._is_logged_in() else "die"}
