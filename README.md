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

## Đăng bài thật

App có 3 chế độ chạy (chọn ở ô "Chế độ"):
- **🧪 Chạy thử (Mock)**: giả lập, không chạm vào Facebook — dùng để test giao diện.
- **🌐 Thật (Requests)**: dùng HTTP trực tiếp (nhẹ). Lưu ý: từ 2026 Facebook chặn
  mạnh client HTTP ở mbasic/m.facebook.com ("Trình duyệt này không hỗ trợ").
- **🌍 Thật (Trình duyệt)**: điều khiển trình duyệt Chromium **thật** bằng Playwright
  trên www.facebook.com — qua được mọi lớp kiểm tra client. Cần cài:
  `pip install playwright` + `playwright install chromium`.
  Cookies dùng chung với chế độ Requests (nhập 1 lần bằng nút 🍪).

1. `pip install -r requirements.txt`
2. Ở panel trái, nhập Email/Mật khẩu Facebook → bấm "🔐 Đăng nhập & lưu cookies".
   Mật khẩu chỉ dùng một lần để lấy cookies, không lưu lại.
3. Ở "THÔNG SỐ CHẠY", chuyển **Chế độ** sang "🌐 Thật (Requests)".
4. Bấm "▶ BẮT ĐẦU ĐĂNG".

Lưu ý:
- Nếu Facebook yêu cầu xác minh (checkpoint), app báo lỗi và dừng —
  hãy mở trình duyệt thật để xác minh rồi đăng nhập lại. App không tự vượt checkpoint/captcha.
- **Đăng nhập bằng cookies (khuyên dùng):** trên trình duyệt đã đăng nhập Facebook,
  dùng tiện ích Cookie-Editor → Export (Header String / JSON) hoặc Get cookies.txt,
  rồi bấm "🍪 Nhập cookies từ trình duyệt" trong app và dán vào. Không cần nhập mật khẩu.
- App dùng `curl_cffi` để giả lập Chrome thật ở mức TLS/HTTP2 (cài bằng
  `pip install curl_cffi`). Nếu thiếu, app vẫn chạy bằng `requests` nhưng Facebook
  có thể chặn và báo "Trình duyệt này không hỗ trợ".
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

## Đóng gói thành app Windows (.exe)

Cách nhanh nhất: double-click file `build_windows.bat` (tự cài PyInstaller và build).
File `.exe` sẽ nằm ở `dist\ProAutoPoster.exe` — copy đi đâu cũng chạy được,
không cần cài Python trên máy đó.

Làm tay:
```bat
pip install pyinstaller requests
pyinstaller --noconfirm --onefile --windowed --name "ProAutoPoster" main.py
```

Lưu ý:
- Phải build trên Windows mới ra file `.exe` chạy trên Windows.
- File `.exe` khoảng 30-50MB (đã gồm Python + tkinter + requests bên trong).
- App PyInstaller đôi khi bị antivirus báo nhầm (false positive) — thêm vào
  whitelist là được.
- Dữ liệu app vẫn lưu ở `%USERPROFILE%\.proautoposter`.

## Roadmap

- [x] `FacebookClient` thật: login bằng cookies/session, `post_to_group`, `comment` qua requests (text)
- [x] Quét UID thành viên nhóm (`scan_group_uids`)
- [x] API check trạng thái tài khoản (`check_account_status`)
- [x] Hỗ trợ proxy v4/v6 cho RequestsFacebookClient (parse nhiều định dạng, set_proxy, test_proxy)
- [ ] Đăng kèm ảnh/video qua requests
- [x] Đăng nhập bằng cookies (paste chuỗi cookies nhiều định dạng: Netscape/JSON/Header)
- [x] UI nhập proxy cố định theo tài khoản (desktop): ô nhập + Lưu/Kiểm tra, tự dùng khi chạy
- [ ] API check nội dung comment, check link, get info nâng cao
- [ ] Tích hợp Golike (giữ nguyên interface `FacebookClient`, UI/Scheduler không phải sửa)
- [x] Hoàn thiện tab Auto Comment (quét bài đã đăng → comment up bài theo vòng)
- [x] Tự động kiểm tra cập nhật từ GitHub (nút "🔄 Kiểm tra cập nhật" trong app)
- [ ] Đóng gói .exe bằng PyInstaller

## Tự động cập nhật

App có nút **"🔄 Kiểm tra cập nhật"** (panel trái): so `version.json` local với bản
trên GitHub, nếu có bản mới sẽ tải `main.zip` về và chép đè code (bỏ qua
`.git/__pycache__/build/dist`). Dữ liệu tài khoản/cookies nằm ở
`~/.proautoposter` nên không bị ảnh hưởng.
- Chạy từ source (`python main.py`): app tự khởi động lại để dùng bản mới.
- Chạy file `.exe`: sau khi cập nhật, chạy lại `build_windows.bat` để build exe mới.

Quy trình ra bản mới: sửa code → tăng `"version"` trong `version.json`
(ghi chú thay đổi vào `"notes"`) → commit + push lên GitHub.

## Lưu ý

- Không lưu mật khẩu plaintext trong file JSON (chỉ lưu username + metadata + cookies).
- Tự động hóa tài khoản cá nhân có thể vi phạm điều khoản của Facebook —
  hãy ưu tiên API chính thức / fanpage và tuân thủ chính sách nền tảng.
