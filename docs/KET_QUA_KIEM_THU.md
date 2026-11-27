# Kết quả kiểm thử tích hợp — 27/11/2026

## Phạm vi

- Branch nguồn: `feat/giao-dien-chat`, commit `010ceca` (4 commit sau `main` tại `7812ef0`).
- Bản tích hợp khôi phục tài liệu echo, thêm hướng dẫn chat và sửa lỗi có bài tái hiện.
- Windows, Python **3.14.8**, Tk **8.6**, SQLite **3.54.0**; không có Pillow.
- Thực thi TCP trên loopback thật; dùng DB/thư mục tạm và port tự cấp. Có bài dùng hai GUI Tkinter thật với vòng xử lý sự kiện, không chỉ gọi protocol giả lập.

## Kết quả

Lệnh:

```powershell
python -X utf8 -m unittest -v test_client_integration test_regression
```

**20/20 bài đạt:** 4 bài ban đầu và 16 bài bổ sung. Các assertion bao gồm:

| Phạm vi | Đã xác minh |
| --- | --- |
| Framing | Header chia nhỏ, frame gộp, payload rỗng, EOF bình thường, header/body bị cắt |
| JSON/binary | Schema sai kiểu, giới hạn payload/file/chunk; JSON lỗi và frame quá lớn không làm mất khả năng nhận client mới |
| Chat | Nhiều client, UTF-8/emoji, người gửi và phòng lấy từ session, phòng tách biệt |
| Phòng/quyền | Public, Private, xin vào, duyệt/từ chối, kick, xin lại, xem thành viên, rời, xóa và chặn quyền người thường |
| Lưu trữ | Phòng/vai trò/phòng gần nhất còn sau restart, phát lại chat và file từ DB |
| File | File rỗng, 133120 byte, đúng 5242880 byte và PNG; so hash nội dung tải qua TCP với dữ liệu gốc |
| Thu hồi | Chat và file, chỉ người gửi có quyền; DB xóa content/BLOB, GUI bỏ ảnh khỏi khung chat |
| GUI | Hai cửa sổ nhận/gửi qua TCP, gửi file qua thao tác hàm của nút, PNG preview, lưu file và so byte, thu hồi, reconnect và xem lịch sử; không lỗi callback |
| Echo | Chạy `server.py`/`client.py` bằng subprocess, gửi Unicode/rỗng, thoát `/quit` |

Đã kiểm tra trợ giúp của `chat_server.py` và `gui_client.py` bằng `python -X utf8 ... --help`. Không có kiểm tra CI trên GitHub ở branch gốc; kết quả trên được chạy cục bộ.

## Lỗi phát hiện và sửa trước merge

1. Chat yêu cầu vừa 64 KiB nhưng phản hồi thêm sender/room/timestamp/id bị quá giới hạn, làm đóng phiên và lưu lịch sử không phát lại được. Đã kiểm tra kích thước phản hồi trước khi lưu và trả `MESSAGE_TOO_LARGE`; phiên tiếp tục dùng được.
2. Người bị kick có thể hoàn tất file đang gửi vào phòng cũ. Đã kiểm tra phòng còn tồn tại, phòng hiện tại và tư cách thành viên trước khi lưu file.
3. Chọn tạo Private trước khi kết nối làm tạo Public vì `hello` không mang chế độ riêng tư. Đã yêu cầu kết nối trước khi tạo Private.
4. `type` JSON là list/dict gây `TypeError`. Đã chuyển thành `ProtocolError` đúng luồng xử lý.
5. Chat/file trước `hello` và gửi lại `hello` để đổi danh tính trong phiên chưa được chặn. Đã thêm kiểm tra đầu phiên.
6. Thêm kiểm tra tổng số chunk theo kích thước khai báo, miền chỉ số binary và kích thước file nhận ở GUI.

## Giới hạn kết luận

- Chưa xác minh LAN hai máy thật, Internet/NAT, tải lớn, tình huống tranh chấp đồng thời ở mọi điểm hoặc tất cả thời điểm ngắt giữa truyền file.
- PNG được kiểm thử không cần Pillow; JPEG/WebP và nhánh Pillow chưa thử.
- Hộp thoại chọn/lưu file được thay đường dẫn tạm trong test, chưa phải thao tác chuột thủ công toàn bộ GUI. Người dùng kiểm lại theo `KIEM_THU.md`.
- Định danh/quyền được lưu theo tên hiển thị, chưa có mật khẩu hay xác thực. Người dùng khác có thể dùng lại tên khi chủ tên offline; đây là demo nhóm tin cậy.
- Thu hồi không xóa file đã được người nhận lưu; file/DB không được mã hóa. Không tuyên bố bản này đủ an toàn để triển khai công khai.
