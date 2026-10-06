# -*- coding: utf-8 -*-
"""
Parse chuỗi cookies nhiều định dạng về danh sách chuẩn
[{name, value, domain, path}].

Hỗ trợ:
  - Netscape (file cookies.txt từ tiện ích Get cookies.txt / Cookie-Editor)
  - JSON (export từ tiện ích Cookie-Editor)
  - Header: "c_user=123; xs=abc; ..."
"""
import json


def _parse_netscape(text):
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, path, _, _, name, value = parts[:7]
        if name:
            cookies.append({"name": name, "value": value,
                            "domain": domain or ".facebook.com",
                            "path": path or "/"})
    return cookies


def _parse_json(text):
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("cookies", [])
    cookies = []
    for c in data:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        cookies.append({
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": c.get("domain") or ".facebook.com",
            "path": c.get("path") or "/",
        })
    return cookies


def _parse_header(text):
    s = text.strip()
    if s.lower().startswith("cookie:"):
        s = s[len("cookie:"):].strip()
    cookies = []
    for part in s.replace("\r", "").replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip().strip('"')
        if not name or name.lower() in (
                "expires", "path", "domain", "max-age", "samesite",
                "secure", "httponly", "partitioned"):
            continue
        cookies.append({"name": name, "value": value,
                        "domain": ".facebook.com", "path": "/"})
    return cookies


def parse_cookie_string(raw):
    """Tự nhận diện định dạng, trả về list {name, value, domain, path}.
    Ném ValueError nếu chuỗi trống hoặc không parse được cookie nào."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("Chuỗi cookies trống.")
    if s[0] in "[{":
        cookies = _parse_json(s)
    elif "\t" in s or s.startswith("#"):
        cookies = _parse_netscape(s)
    else:
        cookies = _parse_header(s)
    if not cookies:
        raise ValueError(
            "Không parse được cookie nào. Hãy copy nguyên văn từ tiện ích "
            "Cookie-Editor (JSON / Header String) hoặc Get cookies.txt.")
    return cookies
