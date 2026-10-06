# PRO AUTO POSTER 2.0

Phần mềm desktop tự động đăng bài & bình luận Facebook (Python + Tkinter, dark theme bám sát file thiết kế).

## Chạy thử

```bash
cd ~/workspace/pro-auto-poster
pip install -r requirements.txt
python3 main.py
```

Lần chạy đầu sẽ tạo thư mục dữ liệu `~/.proautoposter/`
(accounts.json, groups.json, config.json, cookies_*.json).

Mặc định app chạy ở chế độ **🧪 Chạy thử (Mock)**: toàn bộ luồng
(spin nội dung → đăng từng nhóm → nghỉ ngẫu nhiên → auto comment)
được mô phỏng mà không cần tài khoản thật. Xem log ở panel TERMINAL.

## Cấu trúc

```
main.py                 Điểm khởi chạy
core/
  spintax.py            Sinh biến thể {a|b}, hỗ trợ lồng nhau + biến {group_name}
  accounts.py           Quản lý profile, HWID máy, lưu JSON (không lưu mật khẩu)
  scheduler.py          Chạy chiến dịch đa luồng, nghỉ ngẫu nhiên, dừng khẩn
  facebook_client.py    Interface Facebook + bản giả lập MockFacebookClient
  app_log.py            Log thread-safe cho panel Terminal
ui/
  main_window.py        Giao diện chính
```

## Đăng bài thật (Requests)

1. `pip install -r requirements.txt`
2. Ở panel trái, nhập Email/Mật khẩu Facebook → bấm "🔐 Đăng nhập & lưu cookies".
   Mật khẩu chỉ dùng một lần để lấy cookies, không lưu lại.
3. Ở "THÔNG SỐ CHẠY", chuyển **Chế độ** sang "🌐 Thật (Requests)".
4. Bấm "▶ BẮT ĐẦU ĐĂNG".

Lưu ý:
- Nếu Facebook yêu cầu xác minh (checkpoint), app báo lỗi và dừng —
  hãy mở trình duyệt thật để xác minh rồi đăng nhập lại. App không tự vượt checkpoint/captcha.
- Đăng kèm ảnh/video qua requests chưa hỗ trợ (app sẽ báo lỗi rõ ràng).
- Form/selector của Facebook có thể đổi theo thời gian; các điểm mong manh được gom
  trong `core/fb_requests_client.py` (các hàm `_find_*`) để dễ chỉnh.

## Auto Comment (up bài)

- Mỗi bài đăng thành công qua tab **Đăng Bài** được tự động lưu vào
  `~/.proautoposter/posted.json` (post_id, nhóm, thời gian, số lần đã up).
- Tab **Auto Comment**: soạn nội dung comment (hỗ trợ spintax + `{group_name}`),
  chọn số vòng up và thời gian nghỉ, bấm **BẮT ĐẦU AUTO COMMENT**.
  Có thể thêm bài thủ công theo định dạng `post_id|group_id|tên nhóm`.
- Cột "đã up" trong danh sách được cập nhật theo thời gian thực.

## Chạy trên Google Colab

Colab không chạy được giao diện Tkinter — chỉ chạy phần core
(đăng bài, auto comment, spintax...). File `colab_demo.ipynb` trong project
là notebook làm sẵn.

Cách đưa code lên Colab:
- **GitHub (khuyên dùng):** push repo lên GitHub rồi chạy
  `!git clone https://github.com/<user>/pro-auto-poster.git` trong notebook.
  Code mới push lên → chạy `git pull` để cập nhật.
- **Zip:** nén project thành `pro-auto-poster.zip`, upload vào `/content`.

Lưu ý: IP của Colab là IP datacenter nên Facebook dễ yêu cầu checkpoint
khi đăng nhập; dữ liệu `~/.proautoposter` mất khi runtime ngắt (dùng cell
Google Drive trong notebook để giữ lại).

## Roadmap

- [x] `FacebookClient` thật: login bằng cookies/session, `post_to_group`, `comment` qua requests (text)
- [x] Quét UID thành viên nhóm (`scan_group_uids`)
- [x] API check trạng thái tài khoản (`check_account_status`)
- [ ] Đăng kèm ảnh/video qua requests
- [ ] API check nội dung comment, check link, get info nâng cao
- [ ] Tích hợp Golike (giữ nguyên interface `FacebookClient`, UI/Scheduler không phải sửa)
- [x] Hoàn thiện tab Auto Comment (quét bài đã đăng → comment up bài theo vòng)
- [ ] Đóng gói .exe bằng PyInstaller

## Lưu ý

- Không lưu mật khẩu plaintext trong file JSON (chỉ lưu username + metadata + cookies).
- Tự động hóa tài khoản cá nhân có thể vi phạm điều khoản của Facebook —
  hãy ưu tiên API chính thức / fanpage và tuân thủ chính sách nền tảng.
