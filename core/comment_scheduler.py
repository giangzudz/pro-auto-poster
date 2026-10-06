# -*- coding: utf-8 -*-
"""
Auto Comment (up bài): bình luận đẩy các bài đã đăng theo số vòng,
nghỉ ngẫu nhiên giữa các lượt, phản hồi nút DỪNG ngay lập tức.
"""
import random
import threading
import time
from dataclasses import dataclass, field

from core.spintax import Spintax


@dataclass
class CommentJob:
    posts: list = field(default_factory=list)  # [{"post_id","group_id","group_name"}]
    comment: str = ""
    rounds: int = 3
    delay_min: int = 30
    delay_max: int = 90


class CommentScheduler:
    def __init__(self, log):
        self.log = log
        self.running = False
        self._thread = None
        self._stop = threading.Event()

    def start(self, job, client, on_done=None, on_commented=None):
        if self.running:
            self.log.warning("Đã có tiến trình auto comment đang chạy.")
            return False
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(job, client, on_done, on_commented), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self.running:
            self._stop.set()
            self.log.warning("Đang yêu cầu dừng auto comment...")

    # ---------------- nội bộ ----------------
    def _wait(self, seconds):
        """Nghỉ seconds giây nhưng vẫn phản hồi nút Dừng. True = bị dừng."""
        for _ in range(seconds):
            if self._stop.is_set():
                return True
            time.sleep(1)
        return False

    def _run(self, job, client, on_done, on_commented):
        spinner = Spintax()
        try:
            total = job.rounds * len(job.posts)
            op, stopped = 0, False
            self.log.info(f"Bắt đầu auto comment: {len(job.posts)} bài x {job.rounds} vòng "
                          f"| nghỉ {job.delay_min}-{job.delay_max}s.")
            for rnd in range(1, job.rounds + 1):
                if self._stop.is_set():
                    stopped = True
                    break
                self.log.info(f"--- Vòng {rnd}/{job.rounds} ---")
                for p in job.posts:
                    if self._stop.is_set():
                        stopped = True
                        break
                    op += 1
                    pid = p.get("post_id", "")
                    variables = {"group_name": p.get("group_name", ""),
                                 "group_id": p.get("group_id", ""),
                                 "round": rnd}
                    msg = spinner.render(job.comment, variables)
                    try:
                        client.comment(pid, msg)
                        self.log.success(
                            f"[{op}/{total}] Đã up bài {p.get('group_name') or pid} "
                            f"(vòng {rnd})")
                        if on_commented:
                            on_commented(pid)
                    except Exception as exc:  # noqa: BLE001 - ghi log mọi lỗi runtime
                        self.log.error(f"Lỗi up bài {p.get('group_name') or pid}: {exc}")
                    if op < total and not self._stop.is_set():
                        delay = random.randint(job.delay_min, job.delay_max)
                        self.log.info(f"Nghỉ {delay}s...")
                        if self._wait(delay):
                            stopped = True
                            break
                if stopped:
                    break
            if stopped:
                self.log.warning("Đã dừng auto comment.")
            else:
                self.log.success("Hoàn tất auto comment.")
        finally:
            self.running = False
            if on_done:
                on_done()
