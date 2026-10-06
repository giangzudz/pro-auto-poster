# -*- coding: utf-8 -*-
"""
Parse proxy nhiều định dạng về URL chuẩn cho requests.
Hỗ trợ IPv4 và IPv6, http/https/socks5/socks4.
"""
import re


def _check_port(port):
    if not str(port).isdigit() or not (1 <= int(port) <= 65535):
        raise ValueError(f"Port không hợp lệ: {port}")
    return str(port)


def _split_host_port(hostport):
    """Tách host/port, hỗ trợ IPv6 dạng [::1]:8080."""
    hostport = hostport.strip()
    if hostport.startswith("["):
        m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", hostport)
        if not m:
            raise ValueError(f"Proxy IPv6 sai định dạng: {hostport}")
        return f"[{m.group(1)}]", _check_port(m.group(2) or "8080")
    if hostport.count(":") > 1:
        raise ValueError(f"IPv6 phải đặt trong ngoặc vuông: [{hostport}]")
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
        return host.strip(), _check_port(port)
    return hostport, "8080"


def parse_proxy(raw):
    """Parse chuỗi proxy về URL chuẩn. Các dạng chấp nhận:
      http://user:pass@host:port | https://... | socks5://user:pass@host:port
      user:pass@host:port
      host:port:user:pass
      host:port
      [2001:db8::1]:8080  (IPv6)
    Ném ValueError nếu không hiểu định dạng.
    """
    s = (raw or "").strip().strip("'\"")
    if not s:
        raise ValueError("Chuỗi proxy trống.")

    # Đã có scheme: http://... | socks5://...
    m = re.match(r"^(https?|socks5h?|socks4a?)://(.+)$", s, re.I)
    if m:
        scheme, rest = m.group(1).lower(), m.group(2)
        auth, _, hostport = rest.rpartition("@")
        host, port = _split_host_port(hostport)
        prefix = f"{auth}@" if auth else ""
        return f"{scheme}://{prefix}{host}:{port}"

    # user:pass@host:port
    if "@" in s:
        auth, _, hostport = s.rpartition("@")
        host, port = _split_host_port(hostport)
        return f"http://{auth}@{host}:{port}"

    # [IPv6]:port  hoặc  [IPv6]:port:user:pass  (không scheme, không @)
    if s.startswith("["):
        m = re.match(r"^\[([^\]]+)\](?::(\d+))?(?::([^:]+):([^:]+))?$", s)
        if not m:
            raise ValueError(f"Proxy IPv6 sai định dạng: {s}")
        host = f"[{m.group(1)}]"
        port = _check_port(m.group(2) or "8080")
        if m.group(3):
            return f"http://{m.group(3)}:{m.group(4)}@{host}:{port}"
        return f"http://{host}:{port}"

    # host:port:user:pass  hoặc  host:port
    parts = s.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{_check_port(port)}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{_check_port(port)}"

    raise ValueError(
        f"Không hiểu định dạng proxy: {s}\n"
        "Các dạng hợp lệ: host:port | host:port:user:pass | "
        "user:pass@host:port | http(s)://... | socks5://... | [IPv6]:port"
    )
