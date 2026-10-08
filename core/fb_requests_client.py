# -*- coding: utf-8 -*-
"""
RequestsFacebookClient: đăng bài / bình luận Facebook thật bằng `requests`
qua giao diện m.facebook.com (nhẹ, không cần trình duyệt).

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
from core.cookie_parser import parse_cookie_string
from core.facebook_client import FacebookClient
from core.proxy import parse_proxy

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from curl_cffi import requests as _crequests
    _HAS_CURL_CFFI = True
except ImportError:
    _crequests = None
    _HAS_CURL_CFFI = False

# Động cơ HTTP đang dùng: "curl_cffi" giả lập TLS/HTTP2 của Chrome thật
# (để qua lớp kiểm tra client của Facebook), "requests" là fallback.
HTTP_ENGINE = "curl_cffi" if _HAS_CURL_CFFI else "requests"


def _make_session():
    """Tạo session HTTP. Ưu tiên curl_cffi giả lập Chrome thật;
    tự thử target khác nếu version curl_cffi không hỗ trợ."""
    if _HAS_CURL_CFFI:
        for target in ("chrome120", "chrome"):
            try:
                return _crequests.Session(impersonate=target)
            except Exception:  # noqa: BLE001 - thử target khác
                continue
        try:
            return _crequests.Session()
        except Exception:  # noqa: BLE001 - rớt xuống requests
            pass
    return requests.Session()


MFB = "https://m.facebook.com"
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
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


class _LinkParser(HTMLParser):
    """Trích mọi <a>: (href, text)."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._cur = [dict(attrs).get("href", ""), ""]

    def handle_data(self, data):
        if self._cur is not None:
            self._cur[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self.links.append(tuple(self._cur))
            self._cur = None


def _parse_links(page_html):
    parser = _LinkParser()
    parser.feed(page_html)
    return parser.links


# ---------------- client ----------------
class RequestsFacebookClient(FacebookClient):
    def __init__(self, account, proxy=None):
        super().__init__(account)
        _require_requests()
        self.session = _make_session()
        self.http_engine = HTTP_ENGINE
        self.session.headers.update({
            "User-Agent": MOBILE_UA,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        })
        username = (account or {}).get("username", "default")
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(username))
        self.cookies_path = os.path.join(BASE_DIR, f"cookies_{safe}.json")
        self.proxy = None
        if proxy:
            self.set_proxy(proxy)

    # ---------- proxy ----------
    def set_proxy(self, proxy):
        """Gán proxy cho mọi request (nhận mọi định dạng của parse_proxy).
        Trả về URL chuẩn đã dùng. Ví dụ: "1.2.3.4:8080:user:pass"."""
        url = parse_proxy(proxy)
        if url.startswith("socks"):
            try:
                import socks  # noqa: F401  (PySocks)
            except ImportError:
                raise FacebookError(
                    'Proxy SOCKS cần cài thêm: pip install "requests[socks]"')
        self.proxy = url
        self.session.proxies = {"http": url, "https": url}
        return url

    def test_proxy(self, timeout=10):
        """Kiểm tra proxy sống không + xem IP hiện tại.
        Trả về {'ok': True, 'ip': ..., 'latency_ms': ...}."""
        t0 = time.time()
        try:
            r = self.session.get("https://api.ipify.org?format=json", timeout=timeout)
            r.raise_for_status()
            ip = r.json().get("ip", "?")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "ip": ip, "latency_ms": int((time.time() - t0) * 1000)}

    # ---------- tiện ích ----------
    @staticmethod
    def _abs(action, base=MFB):
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
            if "login" in form.get("action", "").lower():
                return form
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

    def _page_hint(self, resp):
        """Đoán nguyên nhân khi trang không chứa form mong đợi."""
        url = getattr(resp, "url", "") or ""
        html_text = getattr(resp, "text", "") or ""
        if "checkpoint" in url:
            return " Tài khoản đang bị checkpoint — hãy xác minh trên trình duyệt thật."
        low = html_text.lower()
        if ("unsupported-interstitial" in low
                or "trình duyệt này không hỗ trợ" in low):
            hint = (" Facebook từ chối kết nối từ app (báo 'Trình duyệt này "
                    "không hỗ trợ').")
            if getattr(self, "http_engine", "") == "requests":
                hint += (" App đang chạy bằng 'requests' (chưa cài curl_cffi) — "
                         "hãy cài: pip install curl_cffi, rồi thử lại.")
            else:
                hint += (" Thử lần lượt: 1) nhập cookies MỚI từ trình duyệt đang "
                         "đăng nhập (nút 🍪), 2) nếu vẫn lỗi, Facebook có thể đang "
                         "chặn IP này — thử proxy khác hoặc mạng khác "
                         "(vd: phát 4G từ điện thoại).")
            return hint
        if self._find_login_form(_parse_forms(html_text)) is not None:
            return (" Phiên đăng nhập đã hết hạn (cookies không còn hiệu lực) — "
                    "hãy nhập cookies mới bằng nút '🍪 Nhập cookies từ trình duyệt'.")
        if ("logout.php" not in low and "đăng xuất" not in low
                and ("đăng nhập" in low or "/login.php" in low)):
            return (" Trang trả về ở trạng thái chưa đăng nhập — "
                    "cookies có thể đã hết hạn, hãy nhập cookies mới.")
        if "tham gia nhóm" in low or "join group" in low:
            return (" Tài khoản chưa tham gia nhóm này (hoặc chưa được duyệt) — "
                    "hãy join nhóm bằng trình duyệt trước khi đăng.")
        if ("không tìm thấy" in low or "không tồn tại" in low
                or "content not found" in low or "page not found" in low
                or "nội dung này hiện không hiển thị" in low):
            return (" ID không đúng hoặc tài khoản không có quyền xem/đăng. "
                    "Kiểm tra lại: ID nhóm lấy từ URL facebook.com/groups/<số>/ .")
        return ""

    def _find_composer_link(self, html_text):
        """Tìm link sang trang composer riêng (Facebook hay dùng link
        'Bạn đang nghĩ gì?' thay vì form soạn bài inline)."""
        for href, _text in _parse_links(html_text):
            if href and "composer" in href.lower():
                return self._abs(href)
        return None

    def _dump_debug(self, filename, resp):
        """Lưu HTML trang về ~/.proautoposter/ để debug. Không bao giờ ném lỗi."""
        try:
            path = os.path.join(BASE_DIR, filename)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(getattr(resp, "text", "") or "")
            return path
        except OSError:
            return ""

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
        data = self.session.cookies.get_dict()
        with open(self.cookies_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def load_cookies(self):
        try:
            with open(self.cookies_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        jar = self.session.cookies
        jar.clear()
        for name, value in data.items():
            jar.set(name, str(value), domain=".facebook.com", path="/")
        return self._is_logged_in()

    def ensure_login(self):
        """Nạp cookies đã lưu. Ném LoginRequired nếu chưa đăng nhập lần nào."""
        if self._is_logged_in() or self.load_cookies():
            return True
        raise LoginRequired("Chưa đăng nhập. Hãy nhập Email/Mật khẩu rồi bấm "
                            "'Đăng nhập & lưu cookies'.")

    def import_cookies(self, cookie_string):
        """Nhập cookies từ chuỗi copy ở trình duyệt (nhiều định dạng).
        Không cần email/mật khẩu. Ném FacebookError nếu cookies không hợp lệ."""
        parsed = parse_cookie_string(cookie_string)
        jar = self.session.cookies
        jar.clear()  # xóa bộ cookies cũ để tránh lẫn với bộ mới
        for c in parsed:
            jar.set(c["name"], c["value"],
                    domain=c.get("domain") or ".facebook.com",
                    path=c.get("path") or "/")
        if not self._is_logged_in():
            raise FacebookError(
                "Cookies không hợp lệ: thiếu cookie c_user "
                "(trình duyệt copy từ chưa đăng nhập Facebook?).")
        self.save_cookies()
        return True

    # ---------- FacebookClient API ----------
    def login(self, email=None, password=None):
        """Đăng nhập 1 lần để lấy cookies (mật khẩu không được lưu lại)."""
        if not email or not password:
            raise LoginFailed("Thiếu email hoặc mật khẩu.")
        try:
            home = self._get(MFB)
        except Exception as exc:  # noqa: BLE001
            raise FacebookError(
                "Không tải được trang đăng nhập Facebook. Nguyên nhân thường gặp: "
                "IP này bị Facebook chặn ngay từ cổng (rất hay xảy ra với IP "
                "datacenter như Google Colab) — lúc này còn chưa tới bước kiểm tra "
                "email/mật khẩu. Thử dùng proxy residential:\n"
                '  client.set_proxy("http://user:pass@host:port")\n'
                "rồi login lại, hoặc đăng nhập từ mạng gia đình."
            ) from exc
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

    def post_to_group(self, group_id, message, media_path=None,
                      use_fanpage=False, rotate_voice=False):
        # NOTE: chế độ requests chưa hỗ trợ đăng bằng Fanpage (chỉ có ở chế độ
        # trình duyệt) — tham số được chấp nhận để tương thích interface.
        self.ensure_login()
        if media_path:
            raise PostFailed("Đăng kèm ảnh/video qua requests chưa hỗ trợ - "
                             "hãy đăng bài text trước.")
        page = self._get(f"{MFB}/groups/{group_id}")
        form = self._find_composer_form(_parse_forms(page.text))
        if form is None or not form["textareas"]:
            # Facebook có thể đặt ô soạn bài dưới dạng link sang trang composer riêng
            composer_url = self._find_composer_link(page.text)
            if composer_url:
                page = self._get(composer_url)
                form = self._find_composer_form(_parse_forms(page.text))
        if form is None or not form["textareas"]:
            dbg = self._dump_debug(f"debug_group_{group_id}.html", page)
            hint = self._page_hint(page)
            if dbg:
                hint += f" (đã lưu HTML trang tại {dbg} — gửi file này để mình kiểm tra)"
            raise PostFailed(
                f"Không tìm thấy khung soạn bài trong nhóm {group_id}." + hint)
        payload = dict(form["inputs"])
        payload[form["textareas"][0]] = message
        resp = self.session.post(self._abs(form["action"], f"{MFB}/groups/{group_id}"),
                                 data=payload, timeout=30)
        resp.raise_for_status()
        if self._looks_like_success(resp.text, message):
            post_id = (self._extract_post_id(resp.text)
                       or f"mfb_{group_id}_{int(time.time())}")
            return {"post_id": post_id}
        raise PostFailed("Facebook không trả về dấu hiệu đăng thành công.")

    def like(self, post_id, use_fanpage=False, rotate_voice=False):
        raise FacebookError(
            "Chế độ requests chưa hỗ trợ like - "
            "hãy dùng chế độ Thật (Trình duyệt).")

    def comment(self, post_id, message, use_fanpage=False, rotate_voice=False):
        # NOTE: chế độ requests chưa hỗ trợ bình luận bằng Fanpage.
        if str(post_id).strip().startswith("http"):
            raise FacebookError(
                "Chế độ requests không mở được link đầy đủ - "
                "hãy dùng chế độ Thật (Trình duyệt).")
        self.ensure_login()
        fbid = str(post_id).split("_")[-1]
        page = self._get(f"{MFB}/story.php?story_fbid={fbid}")
        form = self._find_comment_form(_parse_forms(page.text))
        if form is None or not form["textareas"]:
            dbg = self._dump_debug(f"debug_post_{fbid}.html", page)
            hint = self._page_hint(page)
            if dbg:
                hint += f" (đã lưu HTML trang tại {dbg} — gửi file này để mình kiểm tra)"
            raise PostFailed("Không tìm thấy khung bình luận." + hint)
        payload = dict(form["inputs"])
        payload[form["textareas"][0]] = message
        resp = self.session.post(self._abs(form["action"]), data=payload, timeout=30)
        resp.raise_for_status()
        comment_id = self._extract_comment_id(resp.text) or f"c_{int(time.time())}"
        return {"comment_id": comment_id}

    def get_group_info(self, group_id):
        self.ensure_login()
        page = self._get(f"{MFB}/groups/{group_id}")
        m = re.search(r"<title>(.*?)</title>", page.text, re.S)
        name = html.unescape(m.group(1)).strip() if m else str(group_id)
        name = re.sub(r"\s*[|\-–]\s*Facebook.*$", "", name).strip() or str(group_id)
        return {"id": group_id, "name": name}

    def scan_group_uids(self, group_id, limit=100):
        """Quét UID/username thành viên nhóm (tối đa 20 trang, nghỉ 2s/trang)."""
        self.ensure_login()
        found, seen = [], set()
        url = f"{MFB}/browse/group/members/?id={group_id}"
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
        resp = self._get(MFB)
        if "checkpoint" in resp.url:
            return {"status": "checkpoint"}
        return {"status": "live" if self._is_logged_in() else "die"}
