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
        self._voice_names = None  # lazy: danh sach ten voice de xoay vong
        self._voice_idx = 0

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

    def post_to_group(self, group_id, message, media_path=None,
                          use_fanpage=False, rotate_voice=False):
        if media_path:
            raise PostFailed("Chế độ trình duyệt hiện chỉ hỗ trợ đăng bài text.")
        self.ensure_login()
        self._ensure_browser()
        page = self._page
        page.goto(f"https://www.facebook.com/groups/{group_id}",
                  wait_until="domcontentloaded", timeout=60000)

        # 1. Bấm vào ô soạn bài để mở dialog ("Bạn viết gì đi..." / "Bạn đang nghĩ gì?")
        trigger = None
        try:
            t = page.get_by_text(re.compile(
                "Bạn viết gì đi|Bạn đang nghĩ gì|Write something|What's on your mind",
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

        # 2. Chờ hộp thoại mở ra, rồi chọn ĐÚNG dialog soạn bài dựa vào nội dung
        #    (không dùng .last/.first vì FB có nhiều dialog rỗng/lạ trong DOM —
        #    debug cho thấy .last từng vớ nhầm dialog chỉ có tiêu đề, không có
        #    khung soạn thảo). Dialog đúng = đang hiển thị + chứa khung soạn thảo
        #    mà KHÔNG phải khung bình luận ("Bình luận dưới tên ...").
        try:
            page.get_by_role("dialog").first.wait_for(
                state="visible", timeout=12000)
        except Exception:  # noqa: BLE001
            raise PostFailed(
                "Đã bấm ô soạn bài nhưng hộp thoại 'Tạo bài viết' không mở.")
        page.wait_for_timeout(1500)  # chờ nội dung dialog render xong

        def _is_comment_box(el):
            try:
                label = ((el.get_attribute("aria-label") or "") + " "
                         + (el.get_attribute("aria-placeholder") or "")).lower()
                return ("bình luận" in label or "binh luan" in label
                        or "comment" in label)
            except Exception:  # noqa: BLE001
                return False

        def _find_editor(scope):
            for sel in ["div[role='textbox'][contenteditable='true']",
                        "[contenteditable='true']",
                        "[data-lexical-editor='true']"]:
                try:
                    for el in scope.locator(sel).all():
                        try:
                            if el.is_visible() and not _is_comment_box(el):
                                return el
                        except Exception:  # noqa: BLE001
                            continue
                except Exception:  # noqa: BLE001
                    continue
            return None

        box, dialog = None, None
        try:
            dialogs = page.get_by_role("dialog").all()
        except Exception:  # noqa: BLE001
            dialogs = []
        for d in dialogs:
            try:
                if not d.is_visible():
                    continue
                el = _find_editor(d)
                if el is not None:
                    dialog = d
                    break
            except Exception:  # noqa: BLE001
                continue

        # 2b. Chuyển danh tính sang Fanpage nếu được yêu cầu.
        #     Làm TRƯỚC khi điền nội dung vì đổi danh tính có thể render lại dialog.
        as_page = ""
        detail = ""
        if use_fanpage and dialog is not None:
            ok, detail = self._switch_to_fanpage(page, dialog,
                                                 rotate=rotate_voice)
            if ok:
                as_page = detail
            page.wait_for_timeout(1000)

        # 3. Tìm khung nhập nội dung (tìm lại sau khi chuyển danh tính)
        if dialog is not None:
            box = _find_editor(dialog)
        if box is None:
            # fallback cuối: tìm toàn trang (vẫn loại trừ khung bình luận)
            box = _find_editor(page)

        # 4. Điền nội dung
        if box is None:
            raise PostFailed(
                "Không tìm thấy khung nhập nội dung bài viết "
                "(hộp thoại đã mở nhưng không thấy khung soạn thảo).")
        box.click()
        box.fill(message)
        page.wait_for_timeout(1200)

        # 5. Bấm nút Đăng (ưu tiên trong cùng dialog chứa khung soạn)
        scope_btn = dialog if dialog is not None else page
        btn = None
        for pat in ["^Đăng$", "^Post$"]:
            try:
                el = scope_btn.get_by_role(
                    "button", name=re.compile(pat, re.I)).first
                el.wait_for(state="visible", timeout=4000)
                btn = el
                break
            except Exception:  # noqa: BLE001
                continue
        if btn is not None:
            try:
                btn.click(timeout=10000)
            except Exception as exc:  # noqa: BLE001
                raise PostFailed(f"Bấm nút Đăng thất bại: {exc}")
        else:
            box.press("Control+Enter")  # fallback
        page.wait_for_timeout(5000)

        post_id = self._extract_post_id(page.content() or "")
        return {"post_id": post_id or f"pw_{group_id}_{int(time.time())}",
                "as_page": as_page,
                "fanpage_error": "" if as_page else detail}

    def _switch_to_fanpage(self, page, dialog, rotate=False):
        """Trong dialog Tạo bài viết: chuyển danh tính đăng sang Fanpage.

        Trả về (True, tên_page) nếu chuyển được, (False, lý_do) nếu không.
        Không raise — caller sẽ đăng tiếp bằng nick cá nhân nếu thất bại.
        """
        # B1: tìm nút mở bảng chọn danh tính trong dialog
        switcher = None
        for pat in ["Chọn hồ sơ", "Đăng với tư cách", "Chuyển sang",
                    "Đổi hồ sơ", "Giọng nói", "chuyển trang cá nhân",
                    "Choose profile", "Post as", "Switch profile",
                    "Current voice"]:
            try:
                for el in dialog.get_by_role(
                        "button", name=re.compile(pat, re.I)).all():
                    try:
                        if el.is_visible():
                            switcher = el
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if switcher is not None:
                    break
            except Exception:  # noqa: BLE001
                continue
        if switcher is None:
            return False, "không tìm thấy nút chuyển danh tính trong hộp thoại"
        try:
            switcher.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            return False, f"bấm nút chuyển danh tính thất bại: {exc}"
        page.wait_for_timeout(1500)
        target = self._next_voice_target(page) if rotate else None
        return self._pick_identity_from_chooser(page, target_name=target)

    @staticmethod
    def _read_chooser_identities(page):
        """Doc danh sach (element, ten) tu bang chon danh tinh dang mo.
        Ho tro kieu menu va kieu dialog. Tra ve list (rong neu khong doc duoc)."""
        options = []
        for role in ["menuitem", "menuitemradio", "option"]:
            try:
                for it in page.get_by_role(role).all():
                    try:
                        if not it.is_visible():
                            continue
                        name = (it.inner_text(timeout=2000) or "").strip()
                        if name:
                            options.append((it, name.split(chr(10))[0].strip()))
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                continue
        if options:
            return options
        try:
            dlg = page.get_by_role(
                "dialog",
                name=re.compile("Trang & trang cá nhân|Pages and profiles",
                                re.I)).first
            dlg.wait_for(state="visible", timeout=5000)
        except Exception:  # noqa: BLE001
            return []
        rows = []
        try:
            btns = dlg.get_by_role("button").all()
        except Exception:  # noqa: BLE001
            btns = []
        for btn in btns:
            try:
                if not btn.is_visible():
                    continue
                name = (btn.inner_text(timeout=2000) or "").strip()
                if not name:
                    continue
                first_line = name.split(chr(10))[0].strip()
                low = first_line.lower()
                if any(k in low for k in ["tạo", "create", "cài đặt",
                                         "setting", "chuyển tài khoản",
                                         "switch account", "tìm hiểu",
                                         "learn more", "đóng", "close"]):
                    continue
                rows.append((btn, first_line))
            except Exception:  # noqa: BLE001
                continue
        return rows

    @staticmethod
    def _click_identity(page, el, pname):
        try:
            el.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            return False, "bam chon ho so that bai: %s" % exc
        page.wait_for_timeout(1500)
        return True, pname

    def _next_voice_target(self, page):
        """Ten profile tiep theo de xoay vong (doc danh sach o lan dau).
        Bo muc dau (danh tinh hien tai luc doc). Tra ve None neu khong co."""
        if self._voice_names is None:
            identities = self._read_chooser_identities(page)
            self._voice_names = [nm for idx, (el, nm) in enumerate(identities)
                                 if idx > 0]
        if not self._voice_names:
            return None
        target = self._voice_names[self._voice_idx % len(self._voice_names)]
        self._voice_idx += 1
        return target

    def _pick_identity_from_chooser(self, page, target_name=None):
        """Chon ho so trong bang chon danh tinh dang mo.
        target_name=None -> lay muc dau tien khac danh tinh hien tai."""
        identities = self._read_chooser_identities(page)
        if not identities:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            return False, "không tìm thấy bảng chọn danh tính"
        if target_name:
            for el, nm in identities:
                if nm.lower() == target_name.lower():
                    return self._click_identity(page, el, nm)
            return False, "không thấy '%s' trong bảng chọn" % target_name
        cands = [(el, nm) for idx, (el, nm) in enumerate(identities)
                 if idx > 0 and "cá nhân" not in nm.lower()
                 and "personal" not in nm.lower()]
        if not cands:
            return False, "không tìm thấy hồ sơ/Page khác trong bảng chọn"
        el, pname = cands[0]
        return self._click_identity(page, el, pname)

    def _switch_comment_to_page(self, page, box, rotate=False):
        """Chuyển danh tính bình luận sang Fanpage.

        Nút chuyển là avatar có aria-label "Giọng nói hiện có, chuyển trang cá
        nhân" (Facebook gọi danh tính là "voice"; avatar dạng SVG nên không
        quét bằng thẻ img được). Trả về (True, tên_page) / (False, lý_do)."""
        try:
            bbox = box.bounding_box()
        except Exception:  # noqa: BLE001
            bbox = None
        if bbox is None:
            return False, "không đo được vị trí khung bình luận"
        cx = bbox["x"] + bbox["width"] / 2
        cy = bbox["y"] + bbox["height"] / 2

        # Chiến thuật A: nút "voice" gần khung comment nhất
        switcher = None
        cands = []
        for pat in ["giọng nói", "chuyển trang cá nhân", "current voice",
                    "switch profile"]:
            try:
                els = page.get_by_role(
                    "button", name=re.compile(pat, re.I)).all()
            except Exception:  # noqa: BLE001
                continue
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    bb = el.bounding_box()
                    if not bb:
                        continue
                    dist = (abs(bb["x"] + bb["width"] / 2 - cx)
                            + abs(bb["y"] + bb["height"] / 2 - cy))
                    cands.append((dist, el))
                except Exception:  # noqa: BLE001
                    continue
        if cands:
            cands.sort(key=lambda t: t[0])
            switcher = cands[0][1]

        # Chiến thuật B (dự phòng): nút bọc ngoài ảnh avatar gần khung
        if switcher is None:
            try:
                scope = box.locator("xpath=ancestor::div[8]")
                imgs = scope.locator("img").all() or page.locator("img").all()
            except Exception:  # noqa: BLE001
                try:
                    imgs = page.locator("img").all()
                except Exception:  # noqa: BLE001
                    imgs = []
            img_cands = []
            for im in imgs:
                try:
                    bb = im.bounding_box()
                    if not bb:
                        continue
                    w, h = bb["width"], bb["height"]
                    if w > 120 or h > 120 or w < 16 or h < 16:
                        continue
                    bx, by = bb["x"] + w / 2, bb["y"] + h / 2
                    if bx < bbox["x"] + 10 and abs(by - cy) < 60:
                        img_cands.append((abs(bx - cx) + abs(by - cy), im))
                except Exception:  # noqa: BLE001
                    continue
            if img_cands:
                img_cands.sort(key=lambda t: t[0])
                _, im = img_cands[0]
                for target in (im.locator("xpath=.."),
                               im.locator("xpath=../..")):
                    try:
                        if target.is_visible():
                            switcher = target
                            break
                    except Exception:  # noqa: BLE001
                        continue

        if switcher is None:
            return False, "không tìm thấy nút chuyển danh tính cạnh khung bình luận"
        before = self._count_menu_items(page)
        try:
            switcher.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            return False, f"bấm nút chuyển danh tính thất bại: {exc}"
        page.wait_for_timeout(1500)
        if not self._is_chooser_open(page, before):
            return False, "bấm nút chuyển nhưng bảng chọn không mở"
        target = self._next_voice_target(page) if rotate else None
        return self._pick_identity_from_chooser(page, target_name=target)


    @staticmethod
    def _is_chooser_open(page, before):
        """Kiem tra bang chon danh tinh co mo khong: so muc menu tang len
        hoac dialog 'Trang & trang ca nhan' xuat hien."""
        if PlaywrightFacebookClient._count_menu_items(page) > before:
            return True
        try:
            dlg = page.get_by_role(
                "dialog",
                name=re.compile("Trang & trang cá nhân|Pages and profiles",
                                re.I)).first
            return dlg.is_visible()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _count_menu_items(page):
        """Đếm số mục chọn (menuitem/option) đang hiển thị trên trang."""
        n = 0
        for role in ["menuitem", "menuitemradio", "option"]:
            try:
                for it in page.get_by_role(role).all():
                    try:
                        if it.is_visible():
                            n += 1
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                continue
        return n

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

    def comment(self, post_id, message, use_fanpage=False,
                rotate_voice=False):
        self.ensure_login()
        self._ensure_browser()
        page = self._page
        pid_str = str(post_id).strip()
        if pid_str.startswith("http://") or pid_str.startswith("https://"):
            target_url = pid_str  # link day du (vd. dang pfbid)
            fbid = "url"
        else:
            fbid = pid_str.split("_")[-1]
            if pid_str.startswith("pw_") or pid_str.startswith("mfb_"):
                raise PostFailed(
                    "Không comment được: ID bài viết không hợp lệ "
                    "(bài đăng ở chế độ trình duyệt chưa lấy được ID thật).")
            target_url = f"https://www.facebook.com/{fbid}"
        page.goto(target_url,
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        box = self._find_comment_box(page)
        if box is None:
            raise PostFailed(
                "Không tìm thấy khung bình luận (giao diện Facebook có thể "
                "đã đổi — hãy chụp màn hình trình duyệt lúc báo lỗi để "
                "mình chỉnh lại selector).")

        # chuyển danh tính bình luận sang Fanpage (làm trước khi điền)
        as_page = ""
        detail = ""
        if use_fanpage:
            ok, detail = self._switch_comment_to_page(
                page, box, rotate=rotate_voice)
            if ok:
                as_page = detail
                page.wait_for_timeout(1000)
            else:
                # debug: lưu HTML quanh khung comment để phân tích nút chuyển
                try:
                    container = box.locator("xpath=ancestor::div[8]")
                    dbg = f"debug_comment_{fbid}.html"
                    with open(dbg, "w", encoding="utf-8") as fh:
                        fh.write(container.inner_html())
                except Exception:  # noqa: BLE001
                    pass

        # điền bình luận — tìm lại khung trước mỗi lần thử vì FB có thể
        # render lại composer sau khi chuyển danh tính (gây "detached")
        for attempt in range(3):
            box = self._find_comment_box(page)
            if box is None:
                raise PostFailed(
                    "Không tìm thấy khung bình luận (giao diện Facebook có thể "
                    "đã đổi).")
            try:
                box.click(timeout=6000)
                page.wait_for_timeout(500)
                box.fill(message)
                page.wait_for_timeout(800)
                box.press("Enter")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    raise PostFailed(
                        f"Không điền được bình luận sau 3 lần thử: {exc}")
                page.wait_for_timeout(1500)
        page.wait_for_timeout(3500)
        if self._dismiss_profile_block_dialog(page):
            raise PostFailed(
                "Facebook chặn: mỗi bài chỉ cho 1 profile tương tác. "
                "Hãy dùng cùng 1 profile cho bài này (tắt xoay vòng).")

        comment_id = self._extract_post_id(page.content() or "")
        return {"comment_id": comment_id or f"c_{int(time.time())}",
                "as_page": as_page,
                "fanpage_error": "" if as_page else detail}

    @staticmethod
    def _dismiss_profile_block_dialog(page):
        """Phat hien va tat dialog 'Chuyen trang ca nhan de tuong tac'
        (Facebook chi cho 1 profile tuong tac voi 1 bai viet).
        Tra ve True neu da phat hien + tat."""
        try:
            dlg = page.get_by_role(
                "dialog",
                name=re.compile("Chuyển trang cá nhân để tương tác|"
                                "Switch profile to interact", re.I)).first
            if not dlg.is_visible():
                return False
        except Exception:  # noqa: BLE001
            return False
        for pat in ["^OK$", "Đóng", "^Close$"]:
            try:
                btn = dlg.get_by_role("button", name=re.compile(pat, re.I)).first
                if btn.is_visible():
                    btn.click(timeout=3000)
                    page.wait_for_timeout(800)
                    return True
            except Exception:  # noqa: BLE001
                continue
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            return True
        except Exception:  # noqa: BLE001
            return False

    def like(self, post_id, use_fanpage=False, rotate_voice=False):
        """Thả like cho bài viết (dùng voice hiện tại hoặc chuyển sang Page).
        Trả về {"liked": True, "as_page": ..., "already": ...}."""
        self.ensure_login()
        self._ensure_browser()
        page = self._page
        pid_str = str(post_id).strip()
        if pid_str.startswith("http://") or pid_str.startswith("https://"):
            target_url = pid_str
        else:
            fbid = pid_str.split("_")[-1]
            if pid_str.startswith("pw_") or pid_str.startswith("mfb_"):
                raise PostFailed(
                    "Không like được: ID bài viết không hợp lệ.")
            target_url = f"https://www.facebook.com/{fbid}"
        page.goto(target_url,
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        # chuyển voice sang Page nếu được yêu cầu (dùng nút voice ở khung comment)
        as_page = ""
        detail = ""
        if use_fanpage:
            box = self._find_comment_box(page)
            if box is not None:
                ok, detail = self._switch_comment_to_page(
                    page, box, rotate=rotate_voice)
                if ok:
                    as_page = detail
                    page.wait_for_timeout(1000)

        # nút Thích của bài viết: ưu tiên trong dialog, lấy nút đầu tiên
        # (thanh action của bài nằm trước các bình luận trong DOM)
        try:
            dialogs = page.get_by_role("dialog").all()
            scope = dialogs[0] if dialogs else page
        except Exception:  # noqa: BLE001
            scope = page
        like_btn = None
        for pat in ["^Thích$", "^Like$"]:
            try:
                btn = scope.get_by_role(
                    "button", name=re.compile(pat, re.I)).first
                btn.wait_for(state="visible", timeout=5000)
                like_btn = btn
                break
            except Exception:  # noqa: BLE001
                continue
        if like_btn is None:
            raise PostFailed("Không tìm thấy nút Thích của bài viết.")
        # đã like rồi thì bỏ qua để không bấm thành unlike
        try:
            if like_btn.get_attribute("aria-pressed") == "true":
                return {"liked": True, "as_page": as_page, "already": True}
        except Exception:  # noqa: BLE001
            pass
        try:
            like_btn.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            raise PostFailed(f"Bấm nút Thích thất bại: {exc}")
        page.wait_for_timeout(1500)
        if self._dismiss_profile_block_dialog(page):
            raise PostFailed(
                "Facebook chặn: mỗi bài chỉ cho 1 profile tương tác. "
                "Hãy dùng cùng 1 profile cho bài này (tắt xoay vòng).")
        return {"liked": True, "as_page": as_page, "already": False}

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
