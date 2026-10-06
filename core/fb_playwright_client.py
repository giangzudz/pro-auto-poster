# -*- coding: utf-8 -*-
"""
PlaywrightFacebookClient: đăng bài / bình luận Facebook bằng trình duyệt
Chromium THẬT (Playwright) trên www.facebook.com.

Dùng khi client HTTP (requests/curl_cffi) bị Facebook chặn ở mức
"Trình duyệt này không hỗ trợ" — trình duyệt thật vượt qua mọi lớp kiểm tra
client vì nó LÀ trình duyệt thật (chạy được JavaScript).

Yêu cầu:
    pip install playwright
    playwright install chromium

Cookies: tái sử dụng file cookies_*.json (cùng định dạng với
RequestsFacebookClient) — nhập 1 lần bằng nút "🍪 Nhập cookies" rồi dùng chung.

Thiết kế:
  - Browser mở LAZY ở lần thao tác đầu tiên (để rơi đúng vào worker thread
    của scheduler — Playwright yêu cầu dùng chung 1 thread).
  - ensure_login() KHÔNG mở browser, chỉ kiểm tra đã có cookies chưa.
  - Gọi close() khi xong việc để tắt trình duyệt.
"""
import json
import os
import re
import time
from urllib.parse import urlsplit

from core.accounts import BASE_DIR
from core.cookie_parser import parse_cookie_string
from core.facebook_client import FacebookClient
from core.fb_requests_client import (
    FacebookError,
    LoginFailed,
    LoginRequired,
    PostFailed,
)

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    sync_playwright = None
    _HAS_PLAYWRIGHT = False


def _require_playwright():
    if not _HAS_PLAYWRIGHT:
        raise FacebookError(
            "Thiếu 'playwright'. Cài bằng:\n"
            "  pip install playwright\n"
            "  playwright install chromium")


class PlaywrightFacebookClient(FacebookClient):
    """Client Facebook dùng trình duyệt Chromium thật."""

    def __init__(self, account, proxy=None, headless=True):
        super().__init__(account)
        _require_playwright()
        self.proxy = (proxy or "").strip()
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        username = (account or {}).get("username", "default")
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(username))
        self.cookies_path = os.path.join(BASE_DIR, f"cookies_{safe}.json")
        self._cookies = self._load_cookies_dict()

    # ---------- cookies (dùng chung file với RequestsFacebookClient) ----------
    def _load_cookies_dict(self):
        try:
            with open(self.cookies_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {k: str(v) for k, v in data.items() if v}
        except (OSError, ValueError):
            return {}

    def save_cookies(self):
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(self.cookies_path, "w", encoding="utf-8") as fh:
            json.dump(self._cookies, fh)

    def import_cookies(self, cookie_string):
        """Nhập cookies từ chuỗi (nhiều định dạng), lưu để dùng cho browser."""
        parsed = parse_cookie_string(cookie_string)
        self._cookies = {c["name"]: c["value"] for c in parsed}
        if not self._cookies.get("c_user"):
            raise FacebookError(
                "Cookies không hợp lệ: thiếu cookie c_user "
                "(trình duyệt copy từ chưa đăng nhập Facebook?).")
        self.save_cookies()
        return True

    def ensure_login(self):
        """Chỉ kiểm tra đã có cookies chưa (KHÔNG mở browser)."""
        if not self._cookies.get("c_user"):
            self._cookies = self._load_cookies_dict()
        if not self._cookies.get("c_user"):
            raise LoginRequired(
                "Chưa có cookies. Hãy bấm '🍪 Nhập cookies từ trình duyệt' trước.")
        return True

    # ---------- browser ----------
    def _playwright_proxy(self):
        """Chuyển chuỗi proxy sang dict cho Playwright."""
        from core.proxy import parse_proxy
        url = parse_proxy(self.proxy)  # ném ValueError nếu sai định dạng
        p = urlsplit(url)
        opt = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            opt["username"] = p.username
        if p.password:
            opt["password"] = p.password
        return opt

    def _ensure_browser(self):
        """Mở browser ở lần dùng đầu tiên (chạy trong worker thread)."""
        if self._page is not None:
            return
        pw = sync_playwright().start()
        self._pw = pw
        try:
            browser = pw.chromium.launch(headless=self.headless)
            self._browser = browser
            ctx_kw = {
                "locale": "vi-VN",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"),
            }
            if self.proxy:
                ctx_kw["proxy"] = self._playwright_proxy()
            context = browser.new_context(**ctx_kw)
            self._context = context
            pw_cookies = [{"name": k, "value": v,
                           "domain": ".facebook.com", "path": "/"}
                          for k, v in self._cookies.items()]
            if pw_cookies:
                context.add_cookies(pw_cookies)
            page = context.new_page()
            self._page = page
            self._validate_session(page)
        except Exception:
            self.close()
            raise

    def _validate_session(self, page):
        """Kiểm tra cookies còn hiệu lực bằng cách mở facebook.com."""
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded",
                  timeout=60000)
        if self._looks_logged_out(page):
            raise LoginRequired(
                "Cookies hết hạn hoặc không hợp lệ "
                "(Facebook trả về trang đăng nhập). Hãy nhập cookies MỚI.")

    @staticmethod
    def _looks_logged_out(page):
        if "login" in page.url.lower():
            return True
        try:
            if page.locator('input[name="email"]').first.is_visible(timeout=4000):
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def close(self):
        """Tắt browser. An toàn khi gọi nhiều lần."""
        for attr in ("_context", "_browser"):
            try:
                obj = getattr(self, attr)
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
            setattr(self, attr, None)
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = None
        self._page = None

    # ---------- helpers ----------
    @staticmethod
    def _first_visible(page, role, patterns, timeout=8000):
        """Tìm phần tử đầu tiên khớp 1 trong các pattern (regex, không phân biệt hoa thường)."""
        for pat in patterns:
            try:
                el = page.get_by_role(role, name=re.compile(pat, re.I)).first
                el.wait_for(state="visible", timeout=timeout)
                return el
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _extract_post_id(html_text):
        m = (re.search(r'story_fbid["\']?\s*[:=]\s*["\']?(\d+)', html_text)
             or re.search(r'/posts/(\d+)', html_text)
             or re.search(r'permalink/(\d+)', html_text))
        return m.group(1) if m else None

    # ---------- FacebookClient API ----------
    def login(self, email=None, password=None):
        raise LoginFailed(
            "Chế độ trình duyệt không đăng nhập bằng mật khẩu. "
            "Hãy dùng nút '🍪 Nhập cookies từ trình duyệt' để nhập cookies trước.")

    def post_to_group(self, group_id, message, media_path=None):
        if media_path:
            raise PostFailed("Chế độ trình duyệt hiện chỉ hỗ trợ đăng bài text.")
        self.ensure_login()
        self._ensure_browser()
        page = self._page
        page.goto(f"https://www.facebook.com/groups/{group_id}",
                  wait_until="domcontentloaded", timeout=60000)

        # 1. Bấm vào ô "Bạn đang nghĩ gì?" để mở dialog soạn bài
        trigger = None
        try:
            t = page.get_by_text(re.compile("Bạn đang nghĩ gì|Write something",
                                            re.I)).first
            t.wait_for(state="visible", timeout=10000)
            trigger = t
        except Exception:  # noqa: BLE001
            pass
        if trigger is None:
            raise PostFailed(
                "Không tìm thấy ô soạn bài trong nhóm "
                "(chưa join nhóm / không có quyền đăng / giao diện đã đổi?).")
        trigger.click()

        # 2. Điền nội dung
        box = self._first_visible(
            page, "textbox",
            ["Tạo bài viết công khai", "Tạo bài viết", "Bạn đang nghĩ gì"])
        if box is None:
            # fallback: textbox contenteditable đang mở
            try:
                box = page.locator(
                    "div[role='textbox'][contenteditable='true']").first
                box.wait_for(state="visible", timeout=8000)
            except Exception:  # noqa: BLE001
                raise PostFailed("Không tìm thấy khung nhập nội dung bài viết.")
        box.click()
        box.fill(message)
        page.wait_for_timeout(1200)

        # 3. Bấm nút Đăng
        btn = self._first_visible(page, "button", ["^Đăng$", "^Post$"], timeout=8000)
        if btn is not None:
            try:
                btn.click(timeout=10000)
            except Exception as exc:  # noqa: BLE001
                raise PostFailed(f"Bấm nút Đăng thất bại: {exc}")
        else:
            box.press("Control+Enter")  # fallback
        page.wait_for_timeout(5000)

        post_id = self._extract_post_id(page.content() or "")
        return {"post_id": post_id or f"pw_{group_id}_{int(time.time())}"}

    @staticmethod
    def _find_comment_box(page, timeout=12000):
        """Tìm khung nhập bình luận theo nhiều cách (giao diện FB hay đổi).

        Ưu tiên khung nằm trong dialog/modal bài viết, vì sau khi mở
        permalink FB thường hiện bài trong một hộp thoại phủ lên trang.
        """
        patterns = ["Bình luận", "Viết bình luận", "Write a comment", "Comment"]
        scopes = []
        try:
            scopes.append(page.get_by_role("dialog"))
        except Exception:  # noqa: BLE001
            pass
        scopes.append(page)

        # 1. theo accessible name (VD tiếng Việt: "Bình luận dưới tên Giang")
        for scope in scopes:
            for pat in patterns:
                try:
                    el = scope.get_by_role(
                        "textbox", name=re.compile(pat, re.I)).first
                    el.wait_for(state="visible", timeout=2500)
                    return el
                except Exception:  # noqa: BLE001
                    continue

        # 2. bấm nút bình luận để mở khung nhập rồi tìm lại
        try:
            btn = page.get_by_role(
                "button", name=re.compile("Bình luận", re.I)).first
            btn.wait_for(state="visible", timeout=4000)
            btn.click(timeout=5000)
            page.wait_for_timeout(1500)
            for scope in scopes:
                for pat in patterns:
                    try:
                        el = scope.get_by_role(
                            "textbox", name=re.compile(pat, re.I)).first
                        el.wait_for(state="visible", timeout=2500)
                        return el
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001
            pass

        # 3. fallback: div contenteditable có aria-label chứa "bình luận"
        try:
            el = page.locator(
                "div[contenteditable='true'][aria-label*='Bình luận'],"
                "div[contenteditable='true'][aria-label*='bình luận'],"
                "div[contenteditable='true'][aria-label*='Comment'],"
                "div[contenteditable='true'][aria-label*='comment']"
            ).first
            el.wait_for(state="visible", timeout=3000)
            return el
        except Exception:  # noqa: BLE001
            return None

    def comment(self, post_id, message):
        self.ensure_login()
        self._ensure_browser()
        page = self._page
        fbid = str(post_id).split("_")[-1]
        if str(post_id).startswith("pw_") or str(post_id).startswith("mfb_"):
            raise PostFailed(
                "Không comment được: ID bài viết không hợp lệ "
                "(bài đăng ở chế độ trình duyệt chưa lấy được ID thật).")
        page.goto(f"https://www.facebook.com/{fbid}",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        box = self._find_comment_box(page)
        if box is None:
            raise PostFailed(
                "Không tìm thấy khung bình luận (giao diện Facebook có thể "
                "đã đổi — hãy chụp màn hình trình duyệt lúc báo lỗi để "
                "mình chỉnh lại selector).")
        box.click()
        page.wait_for_timeout(500)
        box.fill(message)
        page.wait_for_timeout(800)
        box.press("Enter")
        page.wait_for_timeout(3500)

        comment_id = self._extract_post_id(page.content() or "")
        return {"comment_id": comment_id or f"c_{int(time.time())}"}

    def get_group_info(self, group_id):
        self.ensure_login()
        self._ensure_browser()
        page = self._page
        page.goto(f"https://www.facebook.com/groups/{group_id}",
                  wait_until="domcontentloaded", timeout=60000)
        title = page.title() or ""
        name = re.sub(r"\s*[|\-–]\s*Facebook.*$", "", title).strip() or str(group_id)
        return {"id": group_id, "name": name}

    def scan_group_uids(self, group_id, limit=100):
        raise FacebookError(
            "Chế độ trình duyệt chưa hỗ trợ quét UID. "
            "Hãy dùng chế độ Thật (Requests) cho tính năng này.")

    def check_account_status(self):
        try:
            self.ensure_login()
        except LoginRequired:
            return {"status": "no_session"}
        try:
            self._ensure_browser()
        except LoginRequired:
            return {"status": "die"}
        except Exception:  # noqa: BLE001
            return {"status": "unknown"}
        return {"status": "live"}
