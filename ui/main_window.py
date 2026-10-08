# -*- coding: utf-8 -*-
"""Cửa sổ chính PRO AUTO POSTER - Tkinter dark theme, bám sát ảnh thiết kế."""
import functools
import json
import os
import re
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

from core.accounts import BASE_DIR, AccountManager, get_hwid
from core.app_log import AppLog
from core.comment_scheduler import CommentJob, CommentScheduler
from core.facebook_client import MockFacebookClient
from core.posted_store import PostedStore
from core.proxy import parse_proxy
from core.updater import (apply_update, check_update, get_local_version,
                          is_frozen, restart_app)

try:
    from core.fb_requests_client import FacebookError, RequestsFacebookClient
    _HAS_FB_REQUESTS = True
except Exception:  # noqa: BLE001 - app vẫn chạy ở chế độ Mock nếu thiếu requests
    FacebookError = Exception
    RequestsFacebookClient = None
    _HAS_FB_REQUESTS = False
try:
    from core.fb_requests_client import HTTP_ENGINE
except Exception:  # noqa: BLE001
    HTTP_ENGINE = "requests"
try:
    from core.fb_playwright_client import PlaywrightFacebookClient
    _HAS_PLAYWRIGHT = True
except Exception:  # noqa: BLE001 - thiếu playwright thì ẩn chế độ trình duyệt
    PlaywrightFacebookClient = None
    _HAS_PLAYWRIGHT = False
from core.scheduler import PostJob, Scheduler
from core.spintax import Spintax

BG = "#15151f"
PANEL = "#1d1d2b"
FIELD = "#262637"
ACCENT = "#ff9f1c"
TEAL = "#2ec4b6"
TEXT = "#eceaf4"
MUTED = "#9a97ad"

DEFAULT_CONTENT = (
    "🔥 {TISSOT CARSON PREMIUM|Tissot Carson Powermatic 80}\n"
    "⏰ {Sự sang trọng đến từ những điều đơn giản}\n"
    "⚙️ Automatic"
)
DEFAULT_COMMENT = "Chấm bài giúp mình nhé {group_name}!"


def make_button(parent, text, bg, command=None, fg="white"):
    return tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg,
        activebackground=bg, activeforeground=fg, relief="flat",
        font=("Segoe UI", 10, "bold"), padx=8, pady=6, cursor="hand2",
    )


def _guard(fn):
    """Bọc các hàm xử lý nút bấm: lỗi không còn 'chết im' mà hiện ra log app."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            try:
                self.log.error(f"Lỗi ở '{fn.__name__}': {exc}")
            except Exception:  # noqa: BLE001
                pass
            traceback.print_exc()
    return wrapper


def section(parent, title):
    return tk.LabelFrame(
        parent, text=f"  {title}  ", bg=PANEL, fg=ACCENT,
        font=("Segoe UI", 10, "bold"), relief="flat",
        highlightbackground="#33334a", highlightthickness=1,
        labelanchor="n", padx=8, pady=8,
    )


def _resolve_short_link(url):
    """Theo redirect cua link rut gon (facebook.com/share/..., fb.me, fb.watch)
    de lay URL that co chua post ID. That bai thi tra ve URL goc."""
    low = (url or "").lower()
    if not any(k in low for k in ("/share/", "fb.me/", "fb.watch")):
        return url
    try:
        import requests
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
        }
        for method in ("head", "get"):
            try:
                r = getattr(requests, method)(
                    url, allow_redirects=True, timeout=10, headers=headers)
                final = (r.url or "").strip()
                if final and final != url and "facebook.com" in final:
                    return final
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return url


def _short_id(post_id):
    """Rut gon ID/URL de hien thi trong danh sach."""
    s = str(post_id or "")
    return s if len(s) <= 24 else s[:21] + "..."


def _guess_label_from_url(url):
    """Doan ten goi nho tu link: facebook.com/<ten>/posts/... -> <ten>."""
    m = re.search(r"facebook\.com/([^/?#]+)/", url or "")
    if m:
        seg = m.group(1)
        if seg not in ("groups", "photo.php", "story.php", "watch", "reel",
                       "videos", "posts", "permalink"):
            return seg
    return "Bài viết"


def _extract_post_id_from_url(url):
    """Tach post ID tu link Facebook hoac ID thuan.

    Tra ve (post_id, ten_goi_nho). Khong tach duoc -> (None, "")."""
    url = (url or "").strip()
    if re.fullmatch(r"\d+", url):
        return url, ""
    url = _resolve_short_link(url)
    # link pfbid (ID ma hoa, khong phai so): giu nguyen URL day du de mo truc tiep
    if re.search(r"pfbid[A-Za-z0-9]+", url):
        return url, _guess_label_from_url(url)
    base = re.split(r"[?#]", url, maxsplit=1)[0].rstrip("/")
    for pat in (r"/posts/(\d+)", r"/permalink/(\d+)",
                r"[?&]fbid=(\d+)", r"story_fbid=(\d+)",
                r"/videos/(\d+)", r"/reel/(\d+)"):
        m = re.search(pat, url)
        if m:
            return m.group(1), _guess_label_from_url(url)
    m = re.search(r"/(\d{6,})/?$", base)
    if m:
        return m.group(1), _guess_label_from_url(url)
    return None, ""


class MainWindow:
    def __init__(self, root):
        self.root = root
        root.title(f"PRO AUTO POSTER v{get_local_version()} - Tu Dong Dang Bai & Binh Luan Facebook")
        root.geometry("1280x800")
        root.configure(bg=BG)
        root.minsize(1100, 700)

        # ---------- state ----------
        self.log = AppLog()
        if _HAS_FB_REQUESTS and HTTP_ENGINE == "requests":
            self.log.warning(
                "Chưa cài 'curl_cffi' — Facebook có thể chặn kết nối "
                "(báo 'Trình duyệt này không hỗ trợ'). "
                "Cài bằng: pip install curl_cffi")
        self.accounts = AccountManager()
        self.spintax = Spintax()
        self.scheduler = Scheduler(self.log)
        self.comment_scheduler = CommentScheduler(self.log)
        self.posted_store = PostedStore()
        self.media_path = None
        self.group_lists = {"Mac_Dinh": []}
        self._load_group_lists()

        self.var_delay_min = tk.IntVar(value=60)
        self.var_delay_max = tk.IntVar(value=120)
        self.var_headless = tk.BooleanVar(value=False)
        self.var_auto_comment = tk.BooleanVar(value=True)
        self.var_fanpage = tk.BooleanVar(value=False)
        self.var_rotate_voice = tk.BooleanVar(value=False)
        self.var_auto_like = tk.BooleanVar(value=False)
        self.var_c_rounds = tk.IntVar(value=3)
        self.var_c_delay_min = tk.IntVar(value=30)
        self.var_c_delay_max = tk.IntVar(value=90)

        self._build_header()
        self._build_body()

        self.log.success(f"Hệ thống PRO AUTO POSTER v{get_local_version()} đã sẵn sàng.")
        acc = self.accounts.get("testcho")
        if acc:
            self.log.info(
                f"Chào mừng trở lại, {acc['display_name']}! Hạn dùng: {acc['expiry']}"
            )
        self._refresh_account_labels()
        self._refresh_groups_ui()
        self._load_posted()
        self._update_variant_badge()
        self.root.after(200, self._poll_log)

    # ================= header =================
    def _build_header(self):
        bar = tk.Frame(self.root, bg=BG, pady=8)
        bar.pack(fill="x", padx=12)
        tk.Label(
            bar, text="🔥 PRO AUTO\nPOSTER", fg=ACCENT, bg=BG,
            font=("Segoe UI", 14, "bold"), justify="left",
        ).pack(side="left")
        tk.Label(
            bar,
            text="PRO AUTO POSTER - Phần Mềm Tự Động Đăng Bài & Bình Luận Facebook",
            fg=TEXT, bg=BG, font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=16)
        make_button(bar, "❓ Hướng dẫn", "#2b2b3d", self._show_help, fg=TEAL).pack(side="right")

    # ================= body =================
    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.left = tk.Frame(body, bg=BG, width=270)
        self.left.pack(side="left", fill="y", padx=(0, 8))
        self.left.pack_propagate(False)

        self.center = tk.Frame(body, bg=BG)
        self.center.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.right = tk.Frame(body, bg=BG, width=360)
        self.right.pack(side="left", fill="both", expand=False)
        self.right.pack_propagate(False)

        self._build_left()
        self._build_center()
        self._build_right()

    # ================= left =================
    def _build_left(self):
        # Panel trai: phan cai dat cuon doc duoc; cum nut + o "Che do"
        # ghim co dinh o day panel de luc nao cung bam duoc.
        # LUU Y: phai pack bottom TRUOC canvas (packer chia o theo thu tu pack,
        # pack sau se bi chiem het cho va bien mat).
        bottom = tk.Frame(self.left, bg=BG)
        bottom.pack(side="bottom", fill="x", pady=(6, 0))

        canvas = tk.Canvas(self.left, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(self.left, orient="vertical", command=canvas.yview,
                           troughcolor=BG, bg=PANEL, activebackground=FIELD,
                           relief="flat", borderwidth=0)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win, width=e.width))

        def _on_wheel(e):
            # chi cuon panel trai khi chuot dang o tren no
            # (touchpad hay gui delta nho -> lam tron toi thieu 1 nac)
            x, y = self.left.winfo_pointerx(), self.left.winfo_pointery()
            lx, ly = self.left.winfo_rootx(), self.left.winfo_rooty()
            if (lx <= x <= lx + self.left.winfo_width()
                    and ly <= y <= ly + self.left.winfo_height()):
                units = int(-1 * (e.delta / 120))
                if units == 0:
                    units = -1 if e.delta > 0 else 1
                canvas.yview_scroll(units, "units")
                return "break"
        canvas.bind_all("<MouseWheel>", _on_wheel, add="+")
        acc_box = section(inner, "TÀI KHOẢN & PROFILE")
        acc_box.pack(fill="x", pady=(0, 8))

        self.lbl_user = tk.Label(acc_box, text="User: -", bg=PANEL, fg=TEXT,
                                 font=("Segoe UI", 10))
        self.lbl_user.pack(anchor="w")
        self.lbl_hsd = tk.Label(acc_box, text="HSD: -", bg=PANEL, fg=MUTED,
                                font=("Segoe UI", 10))
        self.lbl_hsd.pack(anchor="w", pady=(0, 6))

        row = tk.Frame(acc_box, bg=PANEL)
        row.pack(fill="x", pady=2)
        make_button(row, "🔑 Đổi Mật Khẩu", "#3a3a4d", self._change_password).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        make_button(row, "💳 Nạp Thêm HSD", "#3a3a4d", self._topup).pack(
            side="left", fill="x", expand=True)

        prof = tk.Frame(acc_box, bg=PANEL)
        prof.pack(fill="x", pady=6)
        self.cbo_profile = ttk.Combobox(
            prof, values=[a["username"] for a in self.accounts.list()], state="readonly")
        self.cbo_profile.pack(side="left", fill="x", expand=True)
        self.cbo_profile.bind("<<ComboboxSelected>>",
                              lambda _e: self._refresh_account_labels())
        if self.accounts.list():
            self.cbo_profile.current(0)
        make_button(prof, "+", TEAL, self._add_profile).pack(side="left", padx=4)
        make_button(prof, "✕", "#c93a4b", self._remove_profile).pack(side="left")

        make_button(acc_box, "● Mở Trình Duyệt Login", ACCENT,
                    self._open_login_browser).pack(fill="x", pady=(4, 2))

        tk.Label(acc_box, text="Đăng nhập FB (requests):", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))
        self.ent_fb_email = tk.Entry(acc_box, bg=FIELD, fg=TEXT, relief="flat",
                                     insertbackground=TEXT, font=("Segoe UI", 10))
        self.ent_fb_email.pack(fill="x", pady=2)
        self.ent_fb_email.insert(0, "Email / SĐT Facebook")
        self.ent_fb_email.bind(
            "<FocusIn>",
            lambda _e: self._clear_placeholder(self.ent_fb_email, "Email / SĐT Facebook"))
        self.ent_fb_pass = tk.Entry(acc_box, bg=FIELD, fg=TEXT, relief="flat",
                                    insertbackground=TEXT, show="*",
                                    font=("Segoe UI", 10))
        self.ent_fb_pass.pack(fill="x", pady=2)
        make_button(acc_box, "🔐 Đăng nhập & lưu cookies", "#1f6feb",
                    self._fb_login).pack(fill="x", pady=2)
        make_button(acc_box, "🍪 Nhập cookies từ trình duyệt", "#6d28d9",
                    self._import_cookies).pack(fill="x", pady=2)
        self.btn_update = make_button(acc_box, "🔄 Kiểm tra cập nhật", "#0e7490",
                                      self._check_update)
        self.btn_update.pack(fill="x", pady=2)

        tk.Label(acc_box, text="Proxy riêng (để trống = không dùng):", bg=PANEL,
                 fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))
        self.ent_proxy = tk.Entry(acc_box, bg=FIELD, fg=TEXT, relief="flat",
                                  insertbackground=TEXT, font=("Segoe UI", 10))
        self.ent_proxy.pack(fill="x", pady=2)
        self.ent_proxy.insert(0, "vd: http://user:pass@host:port")
        self.ent_proxy.bind(
            "<FocusIn>",
            lambda _e: self._clear_placeholder(self.ent_proxy,
                                               "vd: http://user:pass@host:port"))
        prow = tk.Frame(acc_box, bg=PANEL)
        prow.pack(fill="x", pady=2)
        make_button(prow, "💾 Lưu proxy", "#3a3a4d",
                    self._save_proxy).pack(side="left", fill="x", expand=True,
                                           padx=(0, 4))
        make_button(prow, "🌐 Kiểm tra", "#3a3a4d",
                    self._test_proxy).pack(side="left", fill="x", expand=True)

        set_box = section(inner, "THÔNG SỐ CHẠY")
        set_box.pack(fill="x", pady=(0, 8))

        drow = tk.Frame(set_box, bg=PANEL)
        drow.pack(fill="x")
        tk.Label(drow, text="Nghỉ (s):", bg=PANEL, fg=TEXT).pack(side="left")
        tk.Spinbox(drow, from_=5, to=3600, textvariable=self.var_delay_min, width=6,
                   bg=FIELD, fg=TEXT, relief="flat").pack(side="left", padx=4)
        tk.Label(drow, text="-", bg=PANEL, fg=MUTED).pack(side="left")
        tk.Spinbox(drow, from_=5, to=3600, textvariable=self.var_delay_max, width=6,
                   bg=FIELD, fg=TEXT, relief="flat").pack(side="left", padx=4)

        for text, var in [
            ("Chạy ngầm (Giấu trình duyệt)", self.var_headless),
            ("Tự động Comment sau khi Đăng", self.var_auto_comment),
            ("Đăng bằng Fanpage (Tự chuyển Page)", self.var_fanpage),
            ("Xoay vòng nhiều profile/Page", self.var_rotate_voice),
        ]:
            tk.Checkbutton(set_box, text=text, variable=var, bg=PANEL, fg=TEXT,
                           selectcolor=FIELD, activebackground=PANEL, anchor="w",
                           font=("Segoe UI", 9)).pack(fill="x", pady=2)

        # ---- noi dung cum dieu khien duoi day (khung bottom da pack o tren)
        mrow = tk.Frame(bottom, bg=PANEL)
        mrow.pack(fill="x", pady=(0, 4))
        tk.Label(mrow, text="Chế độ:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.cbo_mode = ttk.Combobox(mrow, state="readonly", width=20,
                                     values=["🧪 Chạy thử (Mock)", "🌐 Thật (Requests)",
                                             "🌍 Thật (Trình duyệt)"])
        self.cbo_mode.pack(side="left", padx=4, fill="x", expand=True)
        self.cbo_mode.current(0)

        make_button(bottom, "▶ BẮT ĐẦU ĐĂNG", ACCENT, self._on_start).pack(fill="x", pady=2)
        make_button(bottom, "● AUTO COMMENT (UP BÀI)", "#1f6feb",
                    self._on_auto_comment).pack(fill="x", pady=2)
        make_button(bottom, "⏹ DỪNG LẠI", "#5a1f28", self._on_stop,
                    fg="#ff8a8a").pack(fill="x", pady=2)

        foot = tk.Frame(bottom, bg=BG)
        foot.pack(fill="x", pady=(4, 0))
        tk.Label(foot, text="✨ Dev by Giang Vũ", bg="#2b2b12", fg=ACCENT,
                 font=("Segoe UI", 9, "bold"), pady=4).pack(fill="x")
        tk.Label(foot, text=f"HWID: {get_hwid()}", bg=BG, fg=MUTED,
                 font=("Consolas", 8)).pack(anchor="w", pady=2)
        make_button(foot, "Đăng xuất", "#33333f", self._logout, fg=MUTED).pack(anchor="e")

    # ================= center =================
    def _build_center(self):
        tabs = tk.Frame(self.center, bg=BG)
        tabs.pack(fill="x", pady=(0, 6))
        self.btn_tab_post = make_button(tabs, "📄 Đăng Bài", "#2b2b3d",
                                        lambda: self._show_tab("post"), fg=TEXT)
        self.btn_tab_post.pack(side="left", padx=(0, 6))
        self.btn_tab_comment = make_button(tabs, "💬 Auto Comment", "#2b2b3d",
                                           lambda: self._show_tab("comment"), fg=MUTED)
        self.btn_tab_comment.pack(side="left")

        self.tab_post = tk.Frame(self.center, bg=BG)
        self.tab_comment = tk.Frame(self.center, bg=BG)
        self._build_tab_post(self.tab_post)
        self._build_tab_comment(self.tab_comment)
        self._show_tab("post")

    def _show_tab(self, name):
        if name == "post":
            self.tab_comment.pack_forget()
            self.tab_post.pack(fill="both", expand=True)
            self.btn_tab_post.configure(fg=TEXT)
            self.btn_tab_comment.configure(fg=MUTED)
        else:
            self.tab_post.pack_forget()
            self.tab_comment.pack(fill="both", expand=True)
            self.btn_tab_comment.configure(fg=TEXT)
            self.btn_tab_post.configure(fg=MUTED)

    def _build_tab_post(self, parent):
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", pady=(0, 4))
        self.lbl_variants = tk.Label(toolbar, text="🔥 2 biến thể", bg="#3a2b12", fg=ACCENT,
                                     font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        self.lbl_variants.pack(side="left", padx=(0, 6))
        make_button(toolbar, "🤖 AI Tạo Spintax", "#3a2b12", self._ai_spintax,
                    fg=ACCENT).pack(side="left", padx=3)
        make_button(toolbar, "👁 Xem Thử Mẫu", "#123a2e", self._preview_samples,
                    fg=TEAL).pack(side="left", padx=3)
        make_button(toolbar, "💾 Lưu Cấu Hình", "#1f2b4d", self._save_config,
                    fg="#8ab4ff").pack(side="left", padx=3)

        tags = tk.Frame(parent, bg=BG)
        tags.pack(fill="x", pady=4)
        tk.Label(tags, text="🏷 Chèn Thẻ Tự Nhiên:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        for tag in ["group_name", "loi_chuc", "cam_ket", "lien_he"]:
            make_button(tags, f"+ {{{tag}}}", "#2b2b3d",
                        lambda t=tag: self._insert_tag(t), fg=MUTED).pack(side="left", padx=3)

        self.txt_content = tk.Text(parent, bg=FIELD, fg=TEXT, relief="flat",
                                   font=("Segoe UI", 11), wrap="word", height=8,
                                   insertbackground=TEXT)
        self.txt_content.pack(fill="x", pady=2)
        self.txt_content.insert("1.0", DEFAULT_CONTENT)
        self.txt_content.bind("<KeyRelease>", lambda _e: self._update_variant_badge())

        mrow = tk.Frame(parent, bg=BG)
        mrow.pack(fill="x", pady=4)
        make_button(mrow, "🖼 Đính Kèm Ảnh/Video", "#6d28d9",
                    self._attach_media).pack(side="left")
        self.lbl_media = tk.Label(mrow, text="Chưa chọn media nào", bg=BG, fg=MUTED,
                                  font=("Segoe UI", 9))
        self.lbl_media.pack(side="left", padx=8)
        make_button(mrow, "✕ Xóa", "#5a1f28", self._clear_media,
                    fg="#ff8a8a").pack(side="left")

        crow = tk.Frame(parent, bg=BG)
        crow.pack(fill="x", pady=2)
        tk.Label(crow, text="💬 Comment up bài:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.txt_comment = tk.Text(crow, bg=FIELD, fg=TEXT, relief="flat",
                                   font=("Segoe UI", 10), wrap="word", height=2,
                                   insertbackground=TEXT)
        self.txt_comment.pack(side="left", fill="x", expand=True, padx=6)
        self.txt_comment.insert("1.0", DEFAULT_COMMENT)

        gbox = section(parent, "DANH SÁCH NHÓM ĐĂNG")
        gbox.pack(fill="both", expand=True, pady=(6, 0))

        grow = tk.Frame(gbox, bg=PANEL)
        grow.pack(fill="x", pady=(0, 4))
        tk.Label(grow, text="Thư mục:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.cbo_group_list = ttk.Combobox(grow, values=list(self.group_lists.keys()),
                                           state="readonly", width=14)
        self.cbo_group_list.pack(side="left", padx=4)
        self.cbo_group_list.current(0)
        self.cbo_group_list.bind("<<ComboboxSelected>>",
                                 lambda _e: self._switch_group_list())
        make_button(grow, "＋ Tạo", TEAL, self._new_group_list).pack(side="left", padx=2)
        make_button(grow, "🗑 Xóa", "#c93a4b", self._delete_group_list).pack(side="left", padx=2)
        make_button(grow, "💾 Lưu DS Nhóm", "#1f6feb", self._save_group_lists).pack(
            side="left", padx=2)
        make_button(grow, "＋ Thêm 1 Dòng", TEAL, self._add_group_row).pack(side="left", padx=2)
        make_button(grow, "📋 Dán Hàng Loạt", "#6d28d9", self._paste_groups).pack(
            side="left", padx=2)

        lframe = tk.Frame(gbox, bg=PANEL)
        lframe.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(lframe)
        scroll.pack(side="right", fill="y")
        self.lst_groups = tk.Listbox(lframe, bg=FIELD, fg=TEXT, relief="flat",
                                     font=("Consolas", 10), selectbackground=ACCENT,
                                     yscrollcommand=scroll.set)
        self.lst_groups.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.lst_groups.yview)
        make_button(gbox, "🗑 Xóa dòng đã chọn", "#5a1f28", self._delete_group_row,
                    fg="#ff8a8a").pack(anchor="e", pady=4)

        self.lbl_group_count = tk.Label(gbox, text="Tổng số nhóm: 0", bg=PANEL, fg=MUTED,
                                        font=("Segoe UI", 9))
        self.lbl_group_count.pack(anchor="e")

    def _build_tab_comment(self, parent):
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", pady=(0, 4))
        make_button(toolbar, "🔄 Tải DS bài đã đăng", "#123a2e", self._load_posted,
                    fg=TEAL).pack(side="left", padx=3)
        make_button(toolbar, "＋ Thêm bài thủ công", TEAL,
                    self._add_post_manual).pack(side="left", padx=3)
        make_button(toolbar, "🗑 Xóa đã chọn", "#5a1f28", self._delete_post_selected,
                    fg="#ff8a8a").pack(side="left", padx=3)
        make_button(toolbar, "🧹 Xóa hết", "#2b2b3d", self._clear_posts,
                    fg=MUTED).pack(side="left", padx=3)

        lframe = tk.Frame(parent, bg=PANEL, highlightbackground="#33334a",
                          highlightthickness=1)
        lframe.pack(fill="both", expand=True, pady=4)
        scroll = tk.Scrollbar(lframe)
        scroll.pack(side="right", fill="y")
        self.lst_posts = tk.Listbox(lframe, bg=FIELD, fg=TEXT, relief="flat",
                                    font=("Consolas", 10), selectbackground=ACCENT,
                                    yscrollcommand=scroll.set, selectmode="extended")
        self.lst_posts.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.lst_posts.yview)
        self.lbl_post_count = tk.Label(parent, text="Tổng số bài: 0", bg=BG, fg=MUTED,
                                       font=("Segoe UI", 9))
        self.lbl_post_count.pack(anchor="e")

        crow = tk.Frame(parent, bg=BG)
        crow.pack(fill="x", pady=4)
        tk.Label(crow, text="💬 Nội dung comment up bài:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.txt_up_comment = tk.Text(crow, bg=FIELD, fg=TEXT, relief="flat",
                                      font=("Segoe UI", 10), wrap="word", height=3,
                                      insertbackground=TEXT)
        self.txt_up_comment.pack(fill="x", pady=2)
        self.txt_up_comment.insert(
            "1.0", "Up bài giúp mình nhé {group_name}! {👍|❤️|🔥}")

        srow = tk.Frame(parent, bg=BG)
        srow.pack(fill="x", pady=2)
        tk.Label(srow, text="Số vòng:", bg=BG, fg=TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(srow, from_=1, to=100, textvariable=self.var_c_rounds, width=5,
                   bg=FIELD, fg=TEXT, relief="flat").pack(side="left", padx=4)
        tk.Label(srow, text="Nghỉ (s):", bg=BG, fg=TEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))
        tk.Spinbox(srow, from_=0, to=3600, textvariable=self.var_c_delay_min, width=6,
                   bg=FIELD, fg=TEXT, relief="flat").pack(side="left", padx=4)
        tk.Label(srow, text="-", bg=BG, fg=MUTED).pack(side="left")
        tk.Spinbox(srow, from_=0, to=3600, textvariable=self.var_c_delay_max, width=6,
                   bg=FIELD, fg=TEXT, relief="flat").pack(side="left", padx=4)

        lrow = tk.Frame(parent, bg=BG)
        lrow.pack(fill="x", pady=2)
        tk.Checkbutton(lrow, text="👍 Tự động thả Like cho bài viết",
                       variable=self.var_auto_like, bg=BG, fg=TEXT,
                       selectcolor=FIELD, activebackground=BG, anchor="w",
                       font=("Segoe UI", 9)).pack(anchor="w")

        brow = tk.Frame(parent, bg=BG)
        brow.pack(fill="x", pady=6)
        make_button(brow, "▶ BẮT ĐẦU AUTO COMMENT", "#1f6feb",
                    self._on_start_comment).pack(side="left", fill="x", expand=True,
                                                 padx=(0, 4))
        make_button(brow, "⏹ DỪNG", "#5a1f28", self._on_stop_comment,
                    fg="#ff8a8a").pack(side="left", fill="x", expand=True)

    # ================= auto comment: handlers =================
    def _load_posted(self):
        self.lst_posts.delete(0, "end")
        for p in self.posted_store.list():
            posted = (p.get("posted_at", "")[:16] or "").replace("T", " ")
            self.lst_posts.insert(
                "end",
                f"{p.get('group_name') or p.get('group_id')} | "
                f"{_short_id(p['post_id'])} | "
                f"{posted} | đã up: {p.get('up_count', 0)}")
        self.lbl_post_count.configure(text=f"Tổng số bài: {self.lst_posts.size()}")

    def _add_post_manual(self):
        raw = simpledialog.askstring(
            "Thêm bài",
            "Dán link bài viết Facebook (vd. facebook.com/.../posts/123...)\n"
            "hoặc nhập theo dạng cũ: post_id|group_id|tên nhóm",
            parent=self.root)
        if not raw or not raw.strip():
            return
        raw = raw.strip()
        if "|" in raw:
            parts = [x.strip() for x in raw.split("|")]
            if not parts[0]:
                return
            self.posted_store.add(
                group_id=parts[1] if len(parts) > 1 else "",
                group_name=parts[2] if len(parts) > 2 else "",
                post_id=parts[0])
        else:
            post_id, label = _extract_post_id_from_url(raw)
            if not post_id:
                messagebox.showwarning(
                    "Thêm bài",
                    "Không tách được ID bài viết từ link.\n"
                    "Nếu là link share rút gọn, hãy mở nó trên trình duyệt,\n"
                    "rồi copy link trên thanh địa chỉ dán vào đây.")
                return
            self.posted_store.add(group_id="", group_name=label or "Bài viết",
                                  post_id=post_id)
        self._load_posted()

    def _delete_post_selected(self):
        posts = self.posted_store.list()
        ids = [posts[i]["post_id"] for i in self.lst_posts.curselection()
               if i < len(posts)]
        if not ids:
            return
        for pid in ids:
            self.posted_store.remove(pid)
        self._load_posted()

    def _clear_posts(self):
        if messagebox.askyesno("Xóa hết", "Xóa toàn bộ danh sách bài đã đăng?"):
            self.posted_store.clear()
            self._load_posted()

    def _record_posted(self, group, post_id, message):
        """Callback từ Scheduler (chạy trên luồng nền): lưu bài vừa đăng."""
        self.posted_store.add(group.get("id", ""), group.get("name", ""),
                              post_id, message)
        self.root.after(0, self._load_posted)

    def _on_post_upped(self, post_id):
        """Callback từ CommentScheduler (luồng nền): tăng đếm đã up."""
        self.posted_store.increment_up(post_id)
        self.root.after(0, self._load_posted)

    @_guard
    def _on_start_comment(self):
        posts = self.posted_store.list()
        if not posts:
            messagebox.showwarning(
                "Auto Comment",
                "Chưa có bài nào. Hãy chạy chiến dịch đăng bài trước "
                "hoặc thêm bài thủ công.")
            return
        comment = self.txt_up_comment.get("1.0", "end-1c")
        if not comment.strip() and not self.var_auto_like.get():
            messagebox.showwarning(
                "Auto Comment",
                "Nội dung comment đang trống (hoặc tick 'Tự động thả Like').")
            return
        client, mode_label = self._build_client()
        if client is None:
            return
        job = CommentJob(
            posts=posts,
            comment=comment,
            rounds=self.var_c_rounds.get(),
            delay_min=self.var_c_delay_min.get(),
            delay_max=self.var_c_delay_max.get(),
            use_fanpage=self.var_fanpage.get() or self.var_rotate_voice.get(),
            rotate_voice=self.var_rotate_voice.get(),
            auto_like=self.var_auto_like.get(),
        )
        started = self.comment_scheduler.start(
            job, client,
            on_done=lambda: self._finish_client(client, self._on_comment_done),
            on_commented=self._on_post_upped)
        if started:
            self.log.info(f"Auto comment bắt đầu (chế độ {mode_label}).")

    @_guard
    def _on_stop_comment(self):
        self.comment_scheduler.stop()

    def _finish_client(self, client, ui_done):
        """Đóng client sau khi job xong (chạy trong worker thread — đúng thread
        đã mở browser của Playwright), rồi mới báo UI."""
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        self.root.after(0, ui_done)

    def _on_comment_done(self):
        messagebox.showinfo("Hoàn tất",
                            "Auto comment đã kết thúc. Xem log để biết chi tiết.")

    # ================= right (log) =================
    def _build_right(self):
        head = tk.Frame(self.right, bg=BG)
        head.pack(fill="x", pady=(0, 4))
        tk.Label(head, text="⚡ TERMINAL / LOG HOẠT ĐỘNG", bg=BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        make_button(head, "✨ Dev by Giang Vũ", "#3a2b12", self._dev_info,
                    fg=ACCENT).pack(side="right", padx=2)
        make_button(head, "🧹 Dọn Log", "#2b2b3d", self._clear_log,
                    fg=MUTED).pack(side="right", padx=2)

        frame = tk.Frame(self.right, bg=PANEL,
                         highlightbackground="#33334a", highlightthickness=1)
        frame.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        self.txt_log = tk.Text(frame, bg="#101018", fg="#9ae6a0", relief="flat",
                               font=("Consolas", 10), wrap="word", state="disabled",
                               yscrollcommand=scroll.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.txt_log.yview)
        for tag, color in [("info", "#9ae6a0"), ("success", "#4ade80"),
                           ("warning", "#fbbf24"), ("error", "#f87171")]:
            self.txt_log.tag_config(tag, foreground=color)

    # ================= log polling =================
    def _poll_log(self):
        for ts, level, msg in self.log.drain():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", f"> [{ts}] {msg}\n", level)
            self.txt_log.configure(state="disabled")
            self.txt_log.see("end")
        self.root.after(200, self._poll_log)

    def _clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    # ================= nội dung / spintax =================
    def _content(self):
        return self.txt_content.get("1.0", "end-1c")

    def _update_variant_badge(self):
        try:
            n = self.spintax.count(self._content())
        except Exception:
            n = 0
        self.lbl_variants.configure(text=f"🔥 {n} biến thể")

    def _insert_tag(self, tag):
        self.txt_content.insert("insert", "{%s}" % tag)
        self._update_variant_badge()

    def _ai_spintax(self):
        samples = self.spintax.variants(self._content(), 5)
        self._popup("AI Tạo Spintax - 5 mẫu",
                    "\n\n---\n\n".join(samples) or "(không sinh được biến thể)")

    def _preview_samples(self):
        samples = self.spintax.variants(self._content(), 3)
        self._popup("Xem Thử Mẫu",
                    "\n\n---\n\n".join(samples) or "(không sinh được biến thể)")

    def _popup(self, title, text):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("560x400")
        win.configure(bg=BG)
        txt = tk.Text(win, bg=FIELD, fg=TEXT, font=("Segoe UI", 11), wrap="word")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", text)

    def _save_config(self):
        cfg = {
            "content": self._content(),
            "comment": self.txt_comment.get("1.0", "end-1c"),
            "delay_min": self.var_delay_min.get(),
            "delay_max": self.var_delay_max.get(),
        }
        path = os.path.join(BASE_DIR, "config.json")
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.log.success(f"Đã lưu cấu hình -> {path}")
        messagebox.showinfo("Lưu Cấu Hình", "Đã lưu cấu hình hiện tại.")

    # ================= media =================
    def _attach_media(self):
        path = filedialog.askopenfilename(
            title="Chọn ảnh/video",
            filetypes=[("Media", "*.png *.jpg *.jpeg *.mp4 *.mov"), ("Tất cả", "*.*")],
        )
        if path:
            self.media_path = path
            self.lbl_media.configure(text=os.path.basename(path))

    def _clear_media(self):
        self.media_path = None
        self.lbl_media.configure(text="Chưa chọn media nào")

    # ================= nhóm =================
    def _current_list_name(self):
        return self.cbo_group_list.get() or "Mac_Dinh"

    def _switch_group_list(self):
        self._refresh_groups_ui()

    def _refresh_groups_ui(self):
        self.lst_groups.delete(0, "end")
        for g in self.group_lists.get(self._current_list_name(), []):
            self.lst_groups.insert("end", f"{g['id']}  |  {g.get('name', '')}")
        self.lbl_group_count.configure(text=f"Tổng số nhóm: {self.lst_groups.size()}")

    def _parse_group_line(self, line):
        parts = [p.strip() for p in line.split("|", 1)]
        gid = parts[0]
        name = parts[1] if len(parts) > 1 else gid
        return (gid, name) if gid else (None, None)

    def _add_group_row(self):
        raw = simpledialog.askstring("Thêm nhóm",
                                     "Nhập ID nhóm (định dạng: id|tên nhóm):",
                                     parent=self.root)
        if not raw:
            return
        gid, name = self._parse_group_line(raw)
        if gid:
            self.group_lists.setdefault(self._current_list_name(), []).append(
                {"id": gid, "name": name})
            self._refresh_groups_ui()

    def _paste_groups(self):
        try:
            raw = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Dán Hàng Loạt", "Clipboard trống.")
            return
        added = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            gid, name = self._parse_group_line(line)
            if gid:
                self.group_lists.setdefault(self._current_list_name(), []).append(
                    {"id": gid, "name": name})
                added += 1
        self._refresh_groups_ui()
        self.log.info(f"Đã dán {added} nhóm vào danh sách.")

    def _delete_group_row(self):
        sel = list(self.lst_groups.curselection())
        if not sel:
            return
        groups = self.group_lists.get(self._current_list_name(), [])
        for i in sorted(sel, reverse=True):
            del groups[i]
        self._refresh_groups_ui()

    def _new_group_list(self):
        name = simpledialog.askstring("Tạo thư mục", "Tên thư mục mới:", parent=self.root)
        if name and name not in self.group_lists:
            self.group_lists[name] = []
            self.cbo_group_list.configure(values=list(self.group_lists.keys()))
            self.cbo_group_list.set(name)
            self._refresh_groups_ui()

    def _delete_group_list(self):
        name = self._current_list_name()
        if name == "Mac_Dinh":
            messagebox.showwarning("Xóa", "Không xóa thư mục mặc định.")
            return
        if messagebox.askyesno("Xóa", f"Xóa thư mục '{name}'?"):
            self.group_lists.pop(name, None)
            self.cbo_group_list.configure(values=list(self.group_lists.keys()))
            self.cbo_group_list.current(0)
            self._refresh_groups_ui()

    def _save_group_lists(self):
        path = os.path.join(BASE_DIR, "groups.json")
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.group_lists, f, ensure_ascii=False, indent=2)
        self.log.success(f"Đã lưu danh sách nhóm -> {path}")

    def _load_group_lists(self):
        path = os.path.join(BASE_DIR, "groups.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.group_lists = json.load(f) or {"Mac_Dinh": []}
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # ================= tài khoản =================
    def _refresh_account_labels(self):
        username = self.cbo_profile.get()
        acc = self.accounts.get(username) or {}
        self.lbl_user.configure(text=f"User: {username or '-'}")
        self.lbl_hsd.configure(text=f"HSD: {acc.get('expiry', '-')}")
        self.ent_proxy.delete(0, "end")
        self.ent_proxy.insert(
            0, self.accounts.get_proxy(username) or "vd: http://user:pass@host:port")

    def _add_profile(self):
        username = simpledialog.askstring("Thêm profile", "Tên tài khoản:",
                                          parent=self.root)
        if username:
            username = username.strip()
            self.accounts.add(username)
            self.cbo_profile.configure(values=[a["username"] for a in self.accounts.list()])
            self.cbo_profile.set(username)
            self._refresh_account_labels()

    def _remove_profile(self):
        username = self.cbo_profile.get()
        if username and messagebox.askyesno("Xóa", f"Xóa profile '{username}'?"):
            self.accounts.remove(username)
            vals = [a["username"] for a in self.accounts.list()]
            self.cbo_profile.configure(values=vals)
            if vals:
                self.cbo_profile.current(0)
            self._refresh_account_labels()

    def _change_password(self):
        messagebox.showinfo("Đổi Mật Khẩu",
                            "Chức năng đổi mật khẩu sẽ gọi API server ở bản tiếp theo.")

    def _topup(self):
        messagebox.showinfo("Nạp Thêm HSD",
                            "Chức năng nạp thêm hạn sử dụng sẽ gọi API server ở bản tiếp theo.")

    def _open_login_browser(self):
        self.log.info("Mở trình duyệt để đăng nhập (TODO: tích hợp selenium/playwright, lưu cookies).")
        messagebox.showinfo(
            "Login",
            "TODO: mở trình duyệt, đăng nhập Facebook rồi lưu cookies cho profile hiện tại.")

    def _logout(self):
        if messagebox.askyesno("Đăng xuất", "Đăng xuất khỏi ứng dụng?"):
            self.root.destroy()

    # ================= đăng nhập Facebook (requests) =================
    @staticmethod
    def _clear_placeholder(entry, placeholder):
        if entry.get() == placeholder:
            entry.delete(0, "end")

    def _fb_login(self):
        if not _HAS_FB_REQUESTS:
            messagebox.showerror("Thiếu thư viện",
                                 "Cần cài 'requests' trước:\n\npip install requests")
            return
        email = self.ent_fb_email.get().strip()
        password = self.ent_fb_pass.get()
        if not email or not password or email == "Email / SĐT Facebook":
            messagebox.showwarning("Đăng nhập FB",
                                   "Nhập email và mật khẩu Facebook trước.")
            return
        username = self.cbo_profile.get()
        client = RequestsFacebookClient(self.accounts.get(username))
        try:
            client.login(email, password)
        except FacebookError as exc:
            self.log.error(f"Đăng nhập FB thất bại: {exc}")
            messagebox.showerror("Đăng nhập FB", str(exc))
            return
        finally:
            self.ent_fb_pass.delete(0, "end")  # không giữ mật khẩu trong ô nhập
        self.accounts.touch_login(username)
        self.log.success(f"Đã đăng nhập FB và lưu cookies cho '{username}'.")
        messagebox.showinfo("Đăng nhập FB",
                            "Đăng nhập thành công, cookies đã được lưu.\n"
                            "Các lần sau không cần nhập lại mật khẩu.")

    def _import_cookies(self):
        if not _HAS_FB_REQUESTS:
            messagebox.showerror("Thiếu thư viện",
                                 "Cần cài 'requests' trước:\n\npip install requests")
            return
        username = self.cbo_profile.get()
        win = tk.Toplevel(self.root)
        win.title("Nhập cookies Facebook")
        win.geometry("580x440")
        win.configure(bg=BG)
        tk.Label(
            win,
            text="Dán chuỗi cookies copy từ trình duyệt đã đăng nhập Facebook\n"
                 "(tiện ích Cookie-Editor: Export -> Header String / JSON, "
                 "hoặc Get cookies.txt):",
            bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left"
        ).pack(anchor="w", padx=10, pady=(10, 4))
        txt = tk.Text(win, bg=FIELD, fg=TEXT, relief="flat",
                      font=("Consolas", 10), wrap="word", height=15,
                      insertbackground=TEXT)
        txt.pack(fill="both", expand=True, padx=10)
        txt.focus_set()

        def _save():
            raw = txt.get("1.0", "end-1c")
            try:
                RequestsFacebookClient(
                    self.accounts.get(username)).import_cookies(raw)
            except Exception as exc:  # noqa: BLE001 - hiển thị lỗi cho user
                messagebox.showerror("Cookies không hợp lệ", str(exc))
                return
            self.accounts.touch_login(username)
            self.log.success(f"Đã nhập cookies cho '{username}'.")
            messagebox.showinfo("Nhập cookies", "Đã lưu cookies thành công!")
            win.destroy()

        btnrow = tk.Frame(win, bg=BG)
        btnrow.pack(fill="x", padx=10, pady=10)
        make_button(btnrow, "💾 Lưu cookies", "#1f6feb", _save).pack(
            side="left", padx=(0, 6))
        make_button(btnrow, "Hủy", "#33333f", win.destroy, fg=MUTED).pack(
            side="left")

    # ---------- proxy theo tài khoản ----------
    @staticmethod
    def _mask_proxy(url):
        """Ẩn user:pass khi hiển thị/log: 'http://user:pass@h:p' -> 'h:p'."""
        return url.split("://", 1)[-1].rsplit("@", 1)[-1]

    def _save_proxy(self):
        username = self.cbo_profile.get()
        raw = self.ent_proxy.get().strip()
        if raw.startswith("vd:"):
            raw = ""
        if raw:
            try:
                parse_proxy(raw)
            except ValueError as exc:
                messagebox.showerror("Proxy không hợp lệ", str(exc))
                return
        self.accounts.set_proxy(username, raw)
        self.log.success(f"Đã lưu proxy cho '{username}'.")

    def _test_proxy(self):
        if not _HAS_FB_REQUESTS:
            messagebox.showerror("Thiếu thư viện",
                                 "Cần cài 'requests' trước:\n\npip install requests")
            return
        raw = self.ent_proxy.get().strip()
        if not raw or raw.startswith("vd:"):
            messagebox.showwarning("Kiểm tra proxy", "Hãy nhập proxy trước.")
            return
        try:
            client = RequestsFacebookClient(
                self.accounts.get(self.cbo_profile.get()))
            url = client.set_proxy(raw)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Proxy không hợp lệ", str(exc))
            return
        self.log.info(f"Đang kiểm tra proxy {self._mask_proxy(url)}...")
        threading.Thread(target=self._do_test_proxy,
                         args=(client, url), daemon=True).start()

    def _do_test_proxy(self, client, url):
        res = client.test_proxy()
        self.root.after(0, self._on_test_proxy_done, res, url)

    def _on_test_proxy_done(self, res, url):
        mask = self._mask_proxy(url)
        if res.get("ok"):
            msg = (f"Proxy {mask} hoạt động! "
                   f"IP hiện tại: {res['ip']} (ping {res['latency_ms']}ms)")
            self.log.success(msg)
            messagebox.showinfo("Kiểm tra proxy", msg)
        else:
            msg = f"Proxy {mask} lỗi: {res.get('error')}"
            self.log.error(msg)
            messagebox.showerror("Kiểm tra proxy", msg)

    # ---------- tự động cập nhật ----------
    def _check_update(self):
        self.btn_update.config(state="disabled")
        self.log.info("Đang kiểm tra cập nhật từ GitHub...")
        threading.Thread(target=self._do_check_update, daemon=True).start()

    def _do_check_update(self):
        try:
            info = check_update()
        except Exception as exc:  # noqa: BLE001 - báo lỗi ra log/dialog
            self.root.after(0, self._on_update_error, str(exc))
            return
        self.root.after(0, self._on_update_checked, info)

    def _on_update_error(self, msg):
        self.btn_update.config(state="normal")
        self.log.error(msg)
        messagebox.showerror("Kiểm tra cập nhật", msg)

    def _on_update_checked(self, info):
        self.btn_update.config(state="normal")
        if info is None:
            msg = f"Bạn đang dùng bản mới nhất (v{get_local_version()})."
            self.log.success(msg)
            messagebox.showinfo("Kiểm tra cập nhật", msg)
            return
        self.log.info(
            f"Có bản mới v{info['version']} (bạn đang dùng v{info['local']}).")
        text = (f"Có bản cập nhật v{info['version']} "
                f"(hiện tại v{info['local']}).")
        if info.get("notes", "").strip():
            text += f"\n\nGhi chú:\n{info['notes'].strip()}"
        text += "\n\nTải và cập nhật ngay?"
        if messagebox.askyesno("Có bản cập nhật", text):
            self._apply_update(info)

    def _apply_update(self, info):
        self.btn_update.config(state="disabled")
        self.log.info(f"Đang tải bản v{info['version']}...")
        threading.Thread(target=self._do_apply_update,
                         args=(info,), daemon=True).start()

    def _do_apply_update(self, info):
        try:
            new_ver = apply_update(info)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._on_update_error,
                            f"Cập nhật thất bại: {exc}")
            return
        self.root.after(0, self._on_update_done, new_ver)

    def _on_update_done(self, new_ver):
        self.btn_update.config(state="normal")
        self.log.success(f"Đã cập nhật lên v{new_ver}.")
        if is_frozen():
            messagebox.showinfo(
                "Cập nhật xong",
                f"Đã cập nhật code lên v{new_ver}.\n\n"
                "Bạn đang chạy file .exe — hãy chạy lại build_windows.bat "
                "để build bản exe mới rồi dùng.")
        elif messagebox.askyesno(
                "Cập nhật xong",
                f"Đã cập nhật lên v{new_ver}.\n\n"
                "Khởi động lại app ngay để dùng bản mới?"):
            self.log.info("Khởi động lại app...")
            self.root.update()
            restart_app()

    def _build_client(self):
        """Trả về (client, mô tả chế độ) hoặc (None, None) nếu chưa đủ điều kiện."""
        username = self.cbo_profile.get()
        account = self.accounts.get(username)
        if self.cbo_mode.get().startswith("🌍"):
            if not _HAS_PLAYWRIGHT:
                messagebox.showerror(
                    "Thiếu thư viện",
                    "Chế độ trình duyệt cần cài thêm:\n\n"
                    "pip install playwright\n"
                    "playwright install chromium")
                return None, None
            proxy = self.accounts.get_proxy(username)
            if proxy:
                try:
                    parse_proxy(proxy)
                except ValueError as exc:
                    messagebox.showerror(
                        "Proxy lỗi",
                        f"Proxy của '{username}' không hợp lệ:\n{exc}")
                    return None, None
                self.log.info(
                    f"Đã dùng proxy riêng cho tài khoản '{username}'.")
            client = PlaywrightFacebookClient(
                account, proxy=proxy, headless=self.var_headless.get())
            try:
                client.ensure_login()
            except FacebookError as exc:
                self.log.error(str(exc))
                messagebox.showwarning(
                    "Chưa đăng nhập FB",
                    f"{exc}\n\nBấm '🍪 Nhập cookies từ trình duyệt' ở panel trái.")
                return None, None
            return client, "thật (Trình duyệt)"
        if self.cbo_mode.get().startswith("🌐"):
            if not _HAS_FB_REQUESTS:
                messagebox.showerror("Thiếu thư viện",
                                     "Cần cài 'requests' trước:\n\npip install requests")
                return None, None
            client = RequestsFacebookClient(account)
            proxy = self.accounts.get_proxy(username)
            if proxy:
                try:
                    client.set_proxy(proxy)
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror(
                        "Proxy lỗi",
                        f"Proxy của '{username}' không hợp lệ:\n{exc}")
                    return None, None
                self.log.info(
                    f"Đã dùng proxy riêng cho tài khoản '{username}'.")
            try:
                client.ensure_login()
            except FacebookError as exc:
                self.log.error(str(exc))
                messagebox.showwarning(
                    "Chưa đăng nhập FB",
                    f"{exc}\n\nNhập Email/Mật khẩu ở panel trái rồi bấm "
                    "'🔐 Đăng nhập & lưu cookies'.")
                return None, None
            return client, "thật (Requests)"
        return MockFacebookClient(account), "chạy thử (Mock)"

    # ================= chạy chiến dịch =================
    @_guard
    def _on_start(self):
        groups = self.group_lists.get(self._current_list_name(), [])
        if not groups:
            messagebox.showwarning("Bắt đầu đăng",
                                   "Danh sách nhóm đang trống. Hãy thêm nhóm trước.")
            return
        if not self._content().strip():
            messagebox.showwarning("Bắt đầu đăng", "Nội dung bài đăng đang trống.")
            return
        client, mode_label = self._build_client()
        if client is None:
            return
        job = PostJob(
            content=self._content(),
            groups=groups,
            media=self.media_path,
            delay_min=self.var_delay_min.get(),
            delay_max=self.var_delay_max.get(),
            auto_comment=self.var_auto_comment.get(),
            comment_text=self.txt_comment.get("1.0", "end-1c"),
            use_fanpage=self.var_fanpage.get() or self.var_rotate_voice.get(),
            rotate_voice=self.var_rotate_voice.get(),
            headless=self.var_headless.get(),
        )
        started = self.scheduler.start(
            job, client,
            on_done=lambda: self._finish_client(client, self._on_campaign_done),
            on_posted=self._record_posted)
        if started:
            self.log.info(f"Chiến dịch bắt đầu (chế độ {mode_label}).")

    def _on_campaign_done(self):
        messagebox.showinfo("Hoàn tất", "Chiến dịch đã kết thúc. Xem log để biết chi tiết.")

    @_guard
    def _on_stop(self):
        self.scheduler.stop()

    @_guard
    def _on_auto_comment(self):
        self._show_tab("comment")
        self._load_posted()
        self.log.info("Mở tab Auto Comment: chọn bài rồi bấm BẮT ĐẦU AUTO COMMENT.")

    # ================= misc =================
    def _show_help(self):
        messagebox.showinfo(
            "Hướng dẫn",
            "1. Thêm profile tài khoản ở panel trái.\n"
            "2. Soạn nội dung (hỗ trợ spintax {a|b}).\n"
            "3. Thêm danh sách nhóm (ID nhóm).\n"
            "4. Nhấn BẮT ĐẦU ĐĂNG.")

    def _dev_info(self):
        messagebox.showinfo("Dev", "PRO AUTO POSTER 2.0\nDev by Giang Vũ")
