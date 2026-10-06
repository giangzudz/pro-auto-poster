# -*- coding: utf-8 -*-
"""Log thread-safe: worker ghi, UI đọc theo đợt để hiển thị lên panel Terminal."""
import queue
from datetime import datetime


class AppLog:
    def __init__(self):
        self._queue = queue.Queue()

    def _put(self, level, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self._queue.put((ts, level, str(message)))

    def info(self, message):
        self._put("info", message)

    def success(self, message):
        self._put("success", message)

    def warning(self, message):
        self._put("warning", message)

    def error(self, message):
        self._put("error", message)

    def drain(self):
        """Lấy toàn bộ log chưa đọc (UI gọi mỗi 200ms)."""
        items = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items
