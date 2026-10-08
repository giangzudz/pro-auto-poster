# -*- coding: utf-8 -*-
"""
Tầng giao tiếp Facebook.

FacebookClient: interface chuẩn. Hãy tạo lớp con và cài đặt các phương thức
bằng requests / Graph API, hoặc tích hợp dịch vụ của bạn (vd. Golike)
mà vẫn giữ nguyên interface này để Scheduler và UI không phải sửa.

MockFacebookClient: bản giả lập để chạy thử UI + Scheduler
mà không cần tài khoản thật.
"""
import time


class FacebookClient:
    """Interface - các phương thức cần được override ở lớp con."""

    def __init__(self, account):
        self.account = account

    # ---------------- bắt buộc ----------------
    def login(self):
        """Đăng nhập / nạp session. Trả về True nếu thành công."""
        raise NotImplementedError("Chưa cài đặt login() - hãy override ở lớp con.")

    def post_to_group(self, group_id, message, media_path=None,
                      use_fanpage=False, rotate_voice=False):
        """Đăng bài lên nhóm. Trả về dict, tối thiểu {'post_id': ...}."""
        raise NotImplementedError("Chưa cài đặt post_to_group().")

    def comment(self, post_id, message, use_fanpage=False, rotate_voice=False):
        """Bình luận vào bài viết. Trả về dict, tối thiểu {'comment_id': ...}."""
        raise NotImplementedError("Chưa cài đặt comment().")

    def like(self, post_id, use_fanpage=False, rotate_voice=False):
        """Thả like cho bài viết. Trả về dict, tối thiểu {'liked': True}."""
        raise NotImplementedError("Chưa cài đặt like().")

    # ---------------- mở rộng (theo danh sách tính năng) ----------------
    def get_group_info(self, group_id):
        """Lấy thông tin nhóm (tên, số thành viên...)."""
        raise NotImplementedError("Chưa cài đặt get_group_info().")

    def scan_group_uids(self, group_id, limit=100):
        """Quét UID thành viên nhóm."""
        raise NotImplementedError("Chưa cài đặt scan_group_uids().")

    def check_account_status(self):
        """Kiểm tra trạng thái tài khoản (live/die/checkpoint)."""
        raise NotImplementedError("Chưa cài đặt check_account_status().")


class MockFacebookClient(FacebookClient):
    """Giả lập: không gọi mạng, chờ một chút rồi trả kết quả mẫu."""

    def login(self):
        return True

    def post_to_group(self, group_id, message, media_path=None,
                      use_fanpage=False, rotate_voice=False):
        time.sleep(0.5)
        return {"post_id": f"mock_{group_id}_{int(time.time())}"}

    def comment(self, post_id, message, use_fanpage=False, rotate_voice=False):
        time.sleep(0.3)
        return {"comment_id": f"mock_c_{int(time.time())}"}

    def like(self, post_id, use_fanpage=False, rotate_voice=False):
        time.sleep(0.3)
        return {"liked": True, "as_page": "", "already": False}

    def get_group_info(self, group_id):
        return {"id": group_id, "name": f"Nhóm {group_id}", "members": 0}

    def scan_group_uids(self, group_id, limit=100):
        return []

    def check_account_status(self):
        return {"status": "live"}
