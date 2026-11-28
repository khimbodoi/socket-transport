# Socket Transport & Chat Python

Đồ án TCP gồm module truyền thông điệp dùng chung, bản echo dòng lệnh và ứng dụng chat nhiều người bằng Tkinter. Tích hợp từ branch `feat/giao-dien-chat` tại commit `010ceca`.

## Chức năng hiện có

| Nhóm | Hành vi |
| --- | --- |
| Kết nối | Server đa luồng, nhiều GUI kết nối đồng thời, nhận tin trong lúc đang nhập |
| Chat | Văn bản UTF-8, nhiều dòng, tên hiển thị, giờ gửi, phân tách theo phòng |
| Phòng | Tạo/vào Public hoặc Private, chuyển phòng, lưu phòng gần nhất |
| Quản trị | Chủ phòng duyệt/từ chối yêu cầu, kick thành viên, xóa phòng; thành viên rời phòng |
| Thành viên | Xem Admin, thành viên đã tham gia, người đang ở phòng, yêu cầu chờ duyệt |
| File/ảnh | Chia file thành mảnh nhị phân 48 KiB; tối đa 5 MiB/file; lưu file nhận về máy |
| Xem ảnh | PNG/GIF bằng Tkinter; thêm Pillow để hỗ trợ các định dạng như JPEG/WebP |
| Lịch sử | SQLite lưu chat, file, phòng, thành viên; phát lại 40 mục gần nhất khi vào phòng |
| Thu hồi | Người gửi thu hồi chat/file/ảnh; cập nhật giao diện những người đang trong phòng và cờ lịch sử |
| Echo cũ | `server.py` + `client.py` và `transport.py` vẫn dùng được riêng |

## Yêu cầu

- Python 3 có `tkinter` và `sqlite3`. Môi trường kiểm thử: Windows, Python 3.14.8, Tk 8.6, SQLite 3.54.0.
- Không có thư viện ngoài bắt buộc. Kiểm tra:

```powershell
python -X utf8 -c "import tkinter, sqlite3; print('Environment OK')"
```

- Tùy chọn cho ảnh JPEG/WebP: `python -m pip install Pillow`. Bộ kiểm thử hiện chạy khi **không cài Pillow**, đã xác minh PNG. Chưa xác minh nhánh Pillow/JPEG.
- Dùng `-X utf8` để log/trợ giúp tiếng Việt không lỗi khi terminal hoặc công cụ chuyển hướng đầu ra dùng mã hóa khác.

## Chạy nhanh trên một máy

Mở **ba terminal**, chuyển vào thư mục chứa mã nguồn. Dùng cổng `65433` để dễ phân biệt với bản echo cũ ở `65432`.

Terminal 1 — server chat, sử dụng DB demo riêng:

```powershell
python -X utf8 chat_server.py --h 127.0.0.1 --p 65433 --db demo_chat.db
```

Terminal 2 — cửa sổ An:

```powershell
python -X utf8 gui_client.py --h 127.0.0.1 --p 65433 --name "An"
```

Terminal 3 — cửa sổ Binh:

```powershell
python -X utf8 gui_client.py --h 127.0.0.1 --p 65433 --name "Binh"
```

Trong **cả hai GUI**, bấm **Kết nối**. Ban đầu vào `#chung` nếu chưa có phòng gần nhất trong DB. Nhập một câu ở An, nhấn Enter; cả hai cửa sổ phải nhận được. Shift+Enter xuống dòng. GUI bỏ qua tin chỉ có khoảng trắng; bản echo cũ vẫn cho gửi nội dung rỗng.

GUI phải kết nối với **`chat_server.py`**. `client.py` cũ dùng với **`server.py`** cũ. Hai cặp dùng cùng framing nhưng khác giao thức ứng dụng, không chạy chéo. Chỉ một server dùng một địa chỉ/cổng tại cùng thời điểm.

## Tham số

| Chương trình | Tham số | Mặc định |
| --- | --- | --- |
| Cả server chat và GUI | `--host` / `--h` | `127.0.0.1` |
| Cả server chat và GUI | `--port` / `--p` | `65432`, từ 1 đến 65535 |
| Server chat | `--db` | `chat_history.db` cạnh mã nguồn |
| GUI | `--name` / `--n` | `Minh Khang` |
| GUI | `--room` / `--r` | Khôi phục phòng gần nhất; nếu chưa có thì `chung` |
| Cả hai | `--help` / `-h` | Hiện trợ giúp rồi thoát |

Tên trùng lúc đang online được server đổi thành dạng `An (2)`. Muốn khôi phục quyền chủ phòng và lịch sử cá nhân phải dùng đúng tên trước đó.

### Lệnh trong ô chat

| Lệnh | Chức năng |
| --- | --- |
| `///` hoặc `/members` | Hiện thành viên trong khung chat |
| `/join ten-phong` | Vào/tạo phòng Public; phòng Private đã có vẫn phải xin duyệt |
| `/kick ten-nguoi` | Admin kick thành viên của phòng hiện tại |
| `/leave` | Rời phòng, về `#chung` |
| `/delete` | Admin xóa phòng sau khi xác nhận |
| `/quit` | Ngắt kết nối GUI, cửa sổ vẫn mở |

Tạo Private bằng ô tên phòng + checkbox Private khi **đã kết nối**. Các phòng mặc định `chung`, `hoc-tap`, `do-an-mang`, `giai-tri` là phòng hệ thống, không có Admin và không thể xóa.

## Cấu trúc mã nguồn

```text
transport.py                Framing TCP dùng chung, header 4 byte big-endian
protocol.py                 JSON điều khiển và định dạng mảnh file nhị phân
network_client.py           Kết nối/nhận/gửi nền, queue sự kiện, khóa gửi
chat_server.py              Server đa luồng, điều phối phòng/chat/file
storage.py                  SQLite, phòng, thành viên, lịch sử và quyền theo tên
gui_client.py               Tkinter, xem ảnh, lưu file, thao tác quản trị
server.py + client.py       Bản echo dòng lệnh ban đầu
test_oversize.py            Test header vượt giới hạn, cố định port 65432
test_client_integration.py  4 bài kiểm thử ban đầu của branch giao diện
test_regression.py          16 bài kiểm thử bổ sung khi tích hợp
docs/KIEM_THU.md            Hướng dẫn kiểm thử thủ công từng chức năng
docs/KET_QUA_KIEM_THU.md    Môi trường, kết quả và giới hạn kiểm thử
docs/KE_HOACH_FIX.md        Các lỗi đang mở, ưu tiên và tiêu chí nghiệm thu
docs/KIEM_TRA_GITHUB.md     Trạng thái tích hợp và quy trình làm việc nhóm
docs/README-echo.md         Tài liệu bản echo trước khi tích hợp GUI
```

Không có knowledge graph sẵn trong repo; quan hệ module ở trên được đối chiếu trực tiếp từ import và các đường gọi trong mã nguồn.

## Giao thức và lưu trữ

- Mỗi frame: `[độ dài payload: 4 byte unsigned big-endian][payload]`.
- `transport.MAX_SIZE = 65536` byte áp dụng **mỗi frame**, không phải toàn bộ file.
- JSON có `version=1`, `type` và các trường theo schema. Bắt đầu bằng `hello`; không đổi tên trong cùng phiên.
- File: `file_start` → nhiều mảnh bắt đầu bằng `BINARY_MAGIC` → `file_end`; mỗi mảnh dữ liệu 48 KiB. Tổng tối đa `5 * 1024 * 1024` byte. `file_saved` xác nhận server đã lưu.
- Server bổ sung thông tin người gửi/phòng/thời gian/ID, vì vậy nội dung chat thực tế phải nhỏ hơn 64 KiB; tin quá dài bị báo lỗi trước khi lưu.
- Mỗi socket có khóa gửi để tránh các thread trộn byte. GUI xử lý sự kiện mạng từ queue trên luồng Tkinter.
- SQLite lưu file dạng BLOB. Thu hồi xóa nội dung/BLOB trong bản ghi và để lại trạng thái thu hồi. File đã tải ra ổ đĩa người nhận vẫn tồn tại.
- GUI/server chat giữ kết nối khi người dùng chưa nhập; khác bản echo cũ có timeout chờ dữ liệu. Chưa có heartbeat/deadline hoàn chỉnh cho truyền file.

## Kiểm thử

```powershell
python -X utf8 -m unittest -v test_client_integration test_regression
```

Kết quả tại thời điểm tích hợp: **20 bài đạt**, gồm TCP thật, hai GUI, SQLite tạm, file đến 5 MiB và echo cũ. Test tự dùng port rảnh/DB tạm; không cần bật server trước. Cần môi trường có màn hình và Tkinter cho các bài GUI. Không dùng `unittest discover` tùy tiện: `test_oversize.py` là script thao tác mạng khi import, không phải unittest.

Kiểm thử bổ sung ngày **28/11/2026**: bộ hiện có vẫn đạt 20/20; bộ chuyên sâu 14 ca có **11 ca không đạt và 3 ca đạt**, cùng kết quả qua ba lần chạy. Các vấn đề đang mở gồm vòng đời kết nối, thao tác phòng đồng thời, thứ tự lịch sử/thu hồi, giới hạn dữ liệu và độ phản hồi GUI. Chưa sửa code ứng dụng trong đợt ghi nhận này.

Xem [kiểm thử thủ công](docs/KIEM_THU.md), [báo cáo kết quả](docs/KET_QUA_KIEM_THU.md), [kế hoạch sửa](docs/KE_HOACH_FIX.md) và [trạng thái GitHub](docs/KIEM_TRA_GITHUB.md). Kết quả bộ cũ không phải bằng chứng mọi tình huống đều đạt.

## Giới hạn cần biết

- **Tên hiển thị là định danh, chưa có tài khoản/mật khẩu.** Khi chủ phòng offline, người khác dùng đúng tên có thể nhận quyền theo tên đó. Private/Admin chỉ phù hợp demo trong nhóm tin cậy, chưa phải xác thực an toàn.
- Chưa mã hóa TLS, chưa giới hạn tổng số phiên/file đang chờ hoặc tổng dung lượng DB. Chưa kiểm thử chịu tải hay triển khai Internet.
- Lịch sử chỉ hiển thị 40 mục gần nhất; DB có thể lưu nhiều hơn. Trạng thái online là đang ở phòng hiện tại, không phải mọi người đang kết nối server.
- Thu hồi không xóa bản sao người nhận đã lưu; xóa phòng xóa cả lịch sử phòng, chỉ nên thử với phòng demo.
- LAN hai máy thật vẫn **chưa kiểm thử** do chưa có máy thứ hai. Khi có: server bind `--h 0.0.0.0`, GUI dùng IPv4 thật của máy server và cùng port; cho phép đúng app qua firewall mạng riêng, không tắt firewall.
- Khi hết thử nghiệm: ngắt các GUI, dừng server bằng Ctrl+C. Giữ DB nếu muốn dùng lại lịch sử.

Để dùng lại bản echo: chạy `python -X utf8 server.py --p 65432` và `python -X utf8 client.py --p 65432` ở hai terminal; xem [tài liệu echo](docs/README-echo.md).
