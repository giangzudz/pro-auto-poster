# -*- coding: utf-8 -*-
"""
Tự động kiểm tra & cập nhật app từ GitHub.

Cách hoạt động:
  - File version.json trên GitHub (raw) chứa {"version", "notes", "sha256"}.
  - Nút "Kiểm tra cập nhật" so version local với remote.
  - Có bản mới -> tải main.zip về, giải nén, chép đè code vào thư mục app
    (bỏ qua .git/__pycache__/build/dist), rồi:
      + chạy từ source (python main.py): tự khởi động lại app.
      + chạy file .exe: nhắc chạy lại build_windows.bat để build exe mới.
  - Chỉ dùng thư viện chuẩn (urllib/zipfile), không cần requests.
  - Dữ liệu người dùng nằm ở ~/.proautoposter (ngoài thư mục app) nên không bị đụng.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

REPO = "giangzudz/pro-auto-poster"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/version.json"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"

SKIP_DIRS = {".git", "__pycache__", "build", "dist"}
SKIP_EXTS = (".pyc", ".spec")


class UpdateError(Exception):
    """Lỗi trong quá trình kiểm tra/tải cập nhật."""


def is_frozen():
    """True khi đang chạy file .exe đóng gói (PyInstaller)."""
    return getattr(sys, "frozen", False)


def app_dir():
    """Thư mục gốc của app: chỗ chứa main.py (source) hoặc file .exe (frozen)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.abspath(os.path.dirname(__file__))  # .../core
    return os.path.abspath(os.path.join(here, os.pardir))


def get_local_version(appdir=None):
    """Đọc version local, trả về '0.0.0' nếu chưa có version.json."""
    try:
        with open(os.path.join(appdir or app_dir(), "version.json"),
                  encoding="utf-8") as fh:
            return str(json.load(fh).get("version", "0.0.0"))
    except (OSError, ValueError):
        return "0.0.0"


def _parse_ver(s):
    parts = []
    for chunk in str(s).strip().split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group(0)) if m else 0)
    return tuple(parts) or (0,)


def is_newer(remote, local):
    """True nếu bản remote mới hơn local (so theo từng cụm số)."""
    return _parse_ver(remote) > _parse_ver(local)


def fetch_remote_info(timeout=15):
    """Tải version.json từ GitHub. Ném UpdateError nếu thất bại."""
    try:
        with urllib.request.urlopen(VERSION_URL, timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - gom mọi lỗi mạng
        raise UpdateError(
            "Không kiểm tra được cập nhật. Kiểm tra mạng, rồi chắc chắn "
            "đã push code mới (có version.json) lên GitHub.") from exc
    if not isinstance(info, dict) or not info.get("version"):
        raise UpdateError("version.json trên GitHub không hợp lệ.")
    return info


def check_update(appdir=None):
    """Trả về dict {version, notes, sha256, local} nếu có bản mới,
    None nếu đã dùng bản mới nhất."""
    local = get_local_version(appdir)
    info = fetch_remote_info()
    remote = str(info.get("version", "0.0.0"))
    if is_newer(remote, local):
        return {"version": remote,
                "notes": str(info.get("notes", "")),
                "sha256": str(info.get("sha256", "")),
                "local": local}
    return None


def download_zip(dest_path, timeout=120):
    try:
        urllib.request.urlretrieve(ZIP_URL, dest_path)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"Tải bản cập nhật thất bại: {exc}") from exc
    return dest_path


def verify_sha256(path, expected):
    """Kiểm tra mã sha256 nếu version.json có cung cấp (bỏ qua khi trống)."""
    if not expected:
        return True
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        raise UpdateError(
            "File tải về không khớp mã xác thực (sha256). Hãy thử lại.")
    return True


def apply_zip(zip_path, appdir=None):
    """Chép đè code từ zip lên thư mục app. Bỏ qua .git/__pycache__/build/dist."""
    target = appdir or app_dir()
    tmp = tempfile.mkdtemp(prefix="pap_update_")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        entries = [e for e in os.listdir(tmp)
                   if os.path.isdir(os.path.join(tmp, e))]
        if len(entries) != 1:
            raise UpdateError("File zip cập nhật không đúng cấu trúc.")
        root = os.path.join(tmp, entries[0])
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel = os.path.relpath(dirpath, root)
            dest_dir = target if rel == "." else os.path.join(target, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for fn in filenames:
                if fn.endswith(SKIP_EXTS):
                    continue
                shutil.copy2(os.path.join(dirpath, fn),
                             os.path.join(dest_dir, fn))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return target


def apply_update(info, appdir=None, timeout=120):
    """Tải zip của bản mới (theo info từ check_update), xác thực rồi chép đè.
    Trả về version mới."""
    tmp_zip = os.path.join(tempfile.gettempdir(), "pap_update.zip")
    try:
        download_zip(tmp_zip, timeout=timeout)
        verify_sha256(tmp_zip, info.get("sha256", ""))
        apply_zip(tmp_zip, appdir)
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass
    return info["version"]


def restart_app():
    """Khởi động lại app (chỉ dùng khi chạy từ source)."""
    main_py = os.path.join(app_dir(), "main.py")
    os.execv(sys.executable, [sys.executable, main_py])
