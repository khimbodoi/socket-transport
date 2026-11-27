# TCP Echo - Đồ án Socket Python

Ứng dụng dòng lệnh để thực hành giao tiếp TCP: client gửi một thông điệp, server nhận và trả lại nguyên nội dung. Các thông điệp dùng header độ dài để xử lý việc TCP chia hoặc gộp dữ liệu.

Đây là phần nền kết nối và transport của nhóm, **chưa phải ứng dụng chat hoàn chỉnh**. Server hiện phục vụ các client **lần lượt**, mỗi client có thể gửi nhiều thông điệp trên cùng kết nối.

## Mục lục

- [Yêu cầu môi trường](#yêu-cầu-môi-trường)
- [Cấu trúc mã nguồn](#cấu-trúc-mã-nguồn)
- [Chạy nhanh trên cùng máy](#chạy-nhanh-trên-cùng-máy)
- [Tham số dòng lệnh](#tham-số-dòng-lệnh)
- [Giao thức truyền dữ liệu](#giao-thức-truyền-dữ-liệu)
- [Module transport](#module-transport)
- [Timeout và vòng đời kết nối](#timeout-và-vòng-đời-kết-nối)
- [Kiểm thử](#kiểm-thử)
- [Chạy trên hai máy LAN](#chạy-trên-hai-máy-lan)
- [Khắc phục lỗi thường gặp](#khắc-phục-lỗi-thường-gặp)
- [Giới hạn và bàn giao](#giới-hạn-và-bàn-giao)

## Yêu cầu môi trường

- Python 3. Môi trường đã dùng để kiểm thử hiện tại: Python 3.14 trên Windows, tại `C:\msys64\ucrt64\bin\python.exe`.
- Không cần cài package bên ngoài. `socket` và `argparse` có sẵn trong thư viện chuẩn.
- Server và client phải dùng cùng định dạng giao thức và cùng giới hạn kích thước.

Kiểm tra môi trường:

```powershell
python --version
python -c "import socket, argparse; print('Environment OK')"
```

Các lệnh bên dưới dùng được trong PowerShell hoặc CMD, trừ lệnh chẩn đoán `Test-NetConnection` chỉ dành cho PowerShell.

## Cấu trúc mã nguồn

```text
đồ án socket/
├── README.md          # Hướng dẫn chạy và bàn giao
├── server.py          # Lắng nghe, quản lý phiên, echo và xử lý lỗi
├── client.py          # Nhập văn bản, gửi yêu cầu, hiển thị phản hồi
├── transport.py       # Framing và các hàm gửi/nhận dùng chung
└── test_oversize.py   # Gửi header vượt giới hạn để kiểm tra server
```

Thư mục còn chứa tài liệu, PDF và code demo tham khảo; chúng không phải phụ thuộc để chạy chương trình này. Khi chuyển sang máy khác, tối thiểu cần chép `transport.py` cùng với `server.py` hoặc `client.py` tương ứng.

## Chạy nhanh trên cùng máy

### 1. Chạy server

Trong terminal thứ nhất:

```powershell
cd "C:\Users\User\Documents\mạng máy tính\đồ án socket"
python server.py
```

Kết quả mong đợi:

```text
Đang chờ tại 127.0.0.1:65432
```

Giữ terminal này chạy. Server chờ kết nối nên chưa trả về dấu nhắc lệnh là bình thường.

### 2. Chạy client

Mở terminal thứ hai:

```powershell
cd "C:\Users\User\Documents\mạng máy tính\đồ án socket"
python client.py
```

Ví dụ phiên làm việc:

```text
Bạn: Xin chào
Server trả lại: Xin chào
Bạn: /quit
```

- Nhập câu rồi nhấn Enter để gửi.
- Enter trống gửi một thông điệp có nội dung 0 byte, vẫn hợp lệ.
- `/quit` kết thúc client tại chỗ, không gửi chuỗi này cho server.
- Ctrl+C dừng client hoặc server với thông báo tương ứng.
- Khi client thoát, server đóng phiên cũ rồi tiếp tục chờ client mới.

## Tham số dòng lệnh

| Tham số | Bí danh | Mặc định | Ý nghĩa |
|---|---|---|---|
| `--host` | `--h` | `127.0.0.1` | Server: địa chỉ IPv4 để bind; client: địa chỉ server đích |
| `--port` | `--p` | `65432` | Port nguyên từ 1 đến 65535 |
| `--help` | `-h` | - | Hiển thị trợ giúp rồi thoát |

`--h` có hai dấu gạch, khác `-h` dành cho trợ giúp. IP/port đích của client phải khớp endpoint server đang lắng nghe.

Ví dụ dùng port khác:

```powershell
# Terminal server
python server.py --h 127.0.0.1 --p 65433
```

```powershell
# Terminal client
python client.py --host 127.0.0.1 --port 65433
```

```powershell
python server.py --help
python client.py --help
```

Thời hạn chờ và `MAX_SIZE` hiện đặt trong code, chưa có tham số dòng lệnh để thay đổi.

## Giao thức truyền dữ liệu

TCP cung cấp luồng byte, không giữ ranh giới mỗi lần `send`. Vì vậy mỗi thông điệp được đóng thành frame:

```text
+------------------------------+-------------------------+
| Header: 4 byte unsigned       | Payload: N byte         |
| Big-endian, giá trị bằng N    | Nội dung nguyên bản     |
+------------------------------+-------------------------+
```

- Header chỉ ghi số byte payload, không tính chính 4 byte header.
- `MAX_SIZE = 64 * 1024`, tức tối đa **65.536 byte payload**; frame tối đa 65.540 byte.
- Header được tạo bằng `len(payload).to_bytes(4, byteorder="big")`.
- Bên nhận đọc đủ 4 byte, đổi thành số bằng `int.from_bytes`, kiểm tra giới hạn rồi đọc đủ N byte.
- Payload có thể rỗng: header bằng 0, kết quả nhận là `b""`. Không nhầm với `None` báo kết thúc phiên.
- Transport xử lý byte; client mã hóa/giải mã văn bản bằng UTF-8. Độ dài được tính **sau encode**, không tính số ký tự.
- Server trả lại payload nguyên bản trong một frame mới, không giải mã văn bản.

Không ghép dữ liệu mới vào cùng kết nối sau lỗi framing hoặc timeout giữa thông điệp. Bản hiện tại đóng phiên lỗi và yêu cầu client kết nối mới.

## Module transport

Chỉ import các hàm cần dùng:

```python
from transport import MAX_SIZE, send_msg, recv_msg
```

| API | Đầu vào | Kết quả / hành vi |
|---|---|---|
| `send_msg(sock, payload)` | Socket TCP đã kết nối; payload kiểu `bytes` | Kiểm tra giới hạn, gửi header và toàn bộ payload; trả `None` khi không lỗi |
| `recv_msg(sock)` | Socket TCP đã kết nối | Trả `bytes` của một thông điệp; trả `None` nếu EOF trước header mới |
| `recv_exact(sock, size, allow_eof=False)` | Số byte không âm cần đọc; hàm hỗ trợ nội bộ | Tích lũy và trả đủ byte; `size=0` trả `b""` |

### Quy tắc EOF và lỗi

- `allow_eof=True` chỉ cho phép trả `None` nếu đầu kia kết thúc chiều gửi **trước khi nhận byte đầu tiên** của phần đang đọc. `recv_msg` dùng tùy chọn này khi đọc header mới.
- EOF giữa header hoặc payload gây `ConnectionError`, không trả thông điệp dở.
- Độ dài vượt `MAX_SIZE` gây `ValueError`; server từ chối ngay sau header, không chờ payload.
- Timeout hoặc lỗi socket được truyền cho chương trình gọi để xử lý.
- `send_msg` thành công chưa phải xác nhận ứng dụng đích đã lưu/hiển thị nội dung. Bản echo nhận phản hồi và có thể so sánh nội dung; chưa có ACK/message ID tổng quát.

Module không tự tạo kết nối, không nhập bàn phím và không cập nhật giao diện. Người gọi quản lý socket, timeout và vòng đời. Nếu sau này dùng nhiều thread, cần một nơi kiểm soát việc gửi frame trên mỗi socket; module hiện chưa có khóa/queue đồng thời.

## Timeout và vòng đời kết nối

| Phía | Cấu hình hiện tại | Tác động |
|---|---|---|
| Client | `create_connection(..., timeout=5)` | Thời hạn kết nối và các thao tác socket tiếp theo là 5 giây |
| Server | `conn.settimeout(10)` | Thời hạn thao tác socket trên từng kết nối là 10 giây |
| Server listen | Chưa đặt timeout | `accept()` tiếp tục chờ client tới khi có kết nối hoặc người dùng dừng |

Timeout không giới hạn thời gian `input()`. Khi client đang chờ người dùng nhập, nó chưa đọc socket nên có thể chưa biết server đã đóng phiên vì không hoạt động. Lỗi/EOF được phát hiện khi thử gửi/nhận tiếp.

Đây chưa phải deadline tổng cho cả thông điệp: một frame đi qua nhiều lần `recv`. Mức 10 giây dùng để dễ kiểm thử, có thể quá ngắn cho người đang nhập. Cần thống nhất chính sách phù hợp trước khi tích hợp app.

Các khối `with` đóng socket khi thoát. Lỗi một phiên được xử lý trước khi server quay lại `accept`. Ctrl+C được bắt ở mức server ngoài cùng. Chương trình hiện chủ yếu báo lỗi bằng chữ; chưa quy định mã thoát riêng cho mọi lỗi chạy.

## Kiểm thử

### Các ca cơ bản nên chạy lại sau khi thay đổi

| Ca | Cách thử | Kết quả mong đợi |
|---|---|---|
| Văn bản | Gửi `hi`, tiếng Việt, nhiều câu | Phản hồi nguyên nội dung trên cùng kết nối |
| Rỗng | Enter trống | Nhận phản hồi rỗng, vẫn gửi được tin tiếp |
| Thoát | Nhập `/quit`, sau đó chạy client mới | Server tiếp tục phục vụ mà không restart |
| Không có server | Chạy client khi server chưa mở | Thông báo kết nối bị từ chối, không traceback |
| Sai port | Chạy với `--p abc`, `--p 0`, `--p 65536` | argparse báo lỗi trước khi mở socket |
| Port bị chiếm | Chạy hai server cùng IP/port | Server thứ hai báo lỗi; server đầu vẫn hoạt động |
| Timeout | Kết nối rồi không gửi hơn 10 giây | Server đóng phiên và phục vụ client mới |
| Dừng | Ctrl+C trong terminal | Thông báo thoát/dừng; socket được đóng |

### Kiểm tra server từ chối thông điệp quá lớn

`test_oversize.py` hiện cố định đích **127.0.0.1:65432**, chưa đọc tham số `--p`. Thoát client đang dùng trước khi thử vì server phục vụ từng phiên lần lượt.

1. Chạy server tại port 65432:

   ```powershell
   python server.py --p 65432
   ```

2. Terminal khác chạy:

   ```powershell
   python test_oversize.py
   ```

3. Server cần hiện:

   ```text
   Thông điệp không hợp lệ: Thông điệp quá lớn
   Đang chờ client tiếp theo...
   ```

4. Chạy client bình thường, gửi `hi` để xác nhận phục hồi mà không restart server.

File test chỉ gửi header khai báo **65.537 byte**, không gửi toàn bộ payload. Dòng `Sent oversized payload length` phía test chỉ chứng minh đã gửi header; cần quan sát server và thử kết nối tiếp mới kết luận ca đạt.

### Phạm vi đã kiểm tra

Trong quá trình phát triển trên cùng máy đã thử: nhiều tin, UTF-8, tin rỗng, 10.000 byte, biên 65.536 byte, chia header/nội dung, gộp frame, header bị cắt, vượt giới hạn, timeout và phục hồi. Sau khi thêm argparse đã thử bí danh, port hợp lệ/không hợp lệ, port bị chiếm và echo qua port tùy chọn. Nhánh KeyboardInterrupt của server đã được kiểm bằng mô phỏng; thao tác Ctrl+C trực tiếp cần kiểm lại trong terminal khi bàn giao.

Đây là ghi nhận kiểm thử trong quá trình phát triển, không phải bảo đảm mọi trường hợp đều đúng hoặc có sẵn một bộ test tự động đầy đủ trong thư mục. Mỗi lần bàn giao cần ghi phiên bản code, môi trường, bước thử và kết quả thực tế.

## Chạy trên hai máy LAN

**Chưa kiểm chứng trên hai máy thật vì hiện chưa có máy thứ hai.** Các bước dưới đây là hướng dẫn chuẩn bị; xem thêm `NOTE - Tiếp tục khi có máy thứ hai.md` trong thư mục.

1. Máy A và B kết nối vào cùng mạng cho phép thiết bị liên lạc. Mạng khách/trường có thể bật client isolation.
2. Trên A, chạy `ipconfig`, lấy IPv4 của đúng Wi-Fi/Ethernet đang dùng, không lấy card VPN/ảo.
3. Trên A, chạy:

   ```powershell
   python server.py --host 0.0.0.0 --port 65432
   ```

4. Nếu Windows hỏi quyền firewall, cho phép đúng app/Python trên profile mạng phù hợp. Không tắt toàn bộ firewall.
5. Chép `client.py` **và `transport.py` cùng phiên bản** sang B, để cùng thư mục.
6. Trên B, thay IP ví dụ bằng IPv4 thật của A rồi chạy:

   ```powershell
   python client.py --host 192.168.1.10 --port 65432
   ```

7. Gửi `hi`, tiếng Việt, `/quit`, rồi kết nối lại; lưu bằng chứng ở cả hai máy.

`0.0.0.0` dùng để bind phía server; không phải địa chỉ đích của client. `127.0.0.1` trên B là chính B, không phải A.

Chẩn đoán từ PowerShell trên B, thay IP thật:

```powershell
Test-NetConnection 192.168.1.10 -Port 65432
```

Lệnh này tạo một kết nối thử; đợi server xử lý xong rồi chạy client. `TcpTestSucceeded: True` xác nhận kết nối TCP tới port, chưa xác nhận toàn bộ giao thức ứng dụng.

## Khắc phục lỗi thường gặp

| Hiện tượng | Kiểm tra / xử lý |
|---|---|
| `can't open file` | Chuyển đúng thư mục hoặc chạy bằng đường dẫn đầy đủ. `cd` vào thư mục, không vào file `.py` |
| `No module named transport` | Đặt `transport.py` cùng thư mục script, kiểm tra tên/đuôi file và lưu file. IDE nên mở đúng thư mục đồ án |
| Editor gạch đỏ import nhưng terminal chạy được | Kiểm tra thư mục workspace và Python interpreter; không kết luận lỗi runtime chỉ từ gạch đỏ |
| WinError 10048 | IP/port đang bị chiếm; dừng server cũ hoặc chọn port khác cho cả hai phía |
| Kết nối bị từ chối | Server chưa mở, đã dừng hoặc client trỏ sai IP/port |
| Timeout | Kiểm tra mạng, server và việc peer có tiếp tục gửi/nhận trong thời hạn hay không |
| WinError 10053/10054 | Kết nối bị hủy/reset; xem log cả hai đầu. Không suy ra nguyên nhân chính xác chỉ từ mã lỗi |
| Client vẫn hiện `Bạn:` sau server timeout | Client đang chờ bàn phím, chưa kiểm tra socket; gửi tiếp mới có thể phát hiện đóng/lỗi |
| Sửa code nhưng chạy như cũ | Lưu file và khởi động lại tiến trình. Tiến trình đang chạy không tự nạp code vừa lưu |

## Giới hạn và bàn giao

- Chưa thử LAN hai máy; chưa hỗ trợ Internet/NAT/relay.
- Server xử lý tuần tự, chưa có nhiều client đồng thời hoặc broadcast.
- Client theo chu kỳ nhập → gửi → chờ phản hồi, chưa nhận độc lập khi người dùng đang nhập.
- Chưa có GUI, tài khoản, lưu lịch sử, truyền file, ACK/message ID hoặc chống gửi trùng.
- TCP thuần chưa mã hóa/xác thực; không mô tả đây là kênh truyền bảo mật.
- Server sử dụng IPv4; xử lý riêng lỗi port bị chiếm hiện dựa vào WinError 10048 của Windows. Chưa xác nhận tính tương thích đầy đủ với hệ điều hành khác.
- `server.py` và `client.py` là entry script, có thực thi khi import; chỉ `transport.py` dành cho tái sử dụng hiện tại.
- Chưa có đề chính thức trong bộ mã hiện tại; cần đối chiếu yêu cầu lớp trước khi mở rộng chức năng hoặc chốt tiêu chí hoàn thành.

Khi tích hợp nhóm, thống nhất định dạng payload với người phụ trách nghiệp vụ, người sở hữu module framing và cách gọi từ UI. Giữ mỗi kết nối có một nơi đọc dữ liệu rõ ràng. Khi chia sẻ bản mới, gửi đủ các file phụ thuộc cùng phiên bản và kiểm lại hướng dẫn chạy từ bản sạch.
