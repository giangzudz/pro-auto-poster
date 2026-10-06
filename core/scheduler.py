# -*- coding: utf-8 -*-
"""
Scheduler: chạy chiến dịch đăng bài trên luồng nền,
nghỉ ngẫu nhiên giữa các nhóm, phản hồi nút DỪNG ngay lập tức.
"""
import random
import threading
import time
from dataclasses import dataclass, field

from core.spintax import Spintax


@dataclass
class PostJob:
    content: str
    groups: list = field(default_factory=list)  # [{"id":..., "name":...}]
    media: str = None
    delay_min: int = 60
    delay_max: int = 120
    auto_comment: bool = True
    comment_text: str = ""
    use_fanpage: bool = False
    headless: bool = False


class Scheduler:
    def __init__(self, log):
        self.log = log
        self.running = False
        self._thread = None
        self._stop = threading.Event()

    def start(self, job, client, on_done=None, on_posted=None):
        if self.running:
            self.log.warning("Đã có chiến dịch đang chạy.")
            return False
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(job, client, on_done, on_posted), daemon=True
        )
        self._thread.start()
        return True

    def stop(self):
        if self.running:
            self._stop.set()
            self.log.warning("Đang yêu cầu dừng...")

    # ---------------- nội bộ ----------------
    def _wait(self, seconds):
        """Nghỉ seconds giây nhưng vẫn phản hồi nút Dừng. True = bị dừng."""
        for _ in range(seconds):
            if self._stop.is_set():
                return True
            time.sleep(1)
        return False

    def _run(self, job, client, on_done, on_posted):
        spinner = Spintax()
        try:
            total = len(job.groups)
            self.log.info(
                f"Bắt đầu chiến dịch: {total} nhóm | nghỉ {job.delay_min}-{job.delay_max}s."
            )
            for idx, group in enumerate(job.groups, 1):
                if self._stop.is_set():
                    self.log.warning("Đã dừng chiến dịch.")
                    break
                gid = group.get("id", "")
                gname = group.get("name") or gid
                variables = {"group_name": gname, "group_id": gid, "index": idx}
                message = spinner.render(job.content, variables)
                try:
                    result = client.post_to_group(gid, message, job.media)
                    self.log.success(f"[{idx}/{total}] Đã đăng nhóm {gname}")
                    if job.auto_comment and job.comment_text.strip():
                        cmsg = spinner.render(job.comment_text, variables)
                        client.comment(result.get("post_id"), cmsg)
                        self.log.info("↳ Đã tự động comment up bài.")
                    if on_posted:
                        try:
                            on_posted({"id": gid, "name": gname},
                                      result.get("post_id"), message)
                        except Exception as hook_exc:  # noqa: BLE001
                            self.log.warning(f"Không lưu được bài đã đăng: {hook_exc}")
                except Exception as exc:  # noqa: BLE001 - ghi log mọi lỗi runtime
                    self.log.error(f"[{idx}/{total}] Lỗi nhóm {gname}: {exc}")
                if idx < total and not self._stop.is_set():
                    delay = random.randint(job.delay_min, job.delay_max)
                    self.log.info(f"Nghỉ {delay}s trước nhóm tiếp theo...")
                    if self._wait(delay):
                        self.log.warning("Đã dừng chiến dịch.")
                        break
            else:
                self.log.success("Hoàn tất chiến dịch.")
        finally:
            self.running = False
            if on_done:
                on_done()
