# Hướng dẫn kiểm thử chat trên cùng máy

Mục tiêu: bạn tự xác nhận các chức năng đã tích hợp. Chỉ dùng tên, phòng và dữ liệu demo. Không cần máy thứ hai cho các bước dưới đây.

## 1. Chuẩn bị và bật chương trình

Mở PowerShell/CMD trong thư mục dự án. Ví dụ thư mục hiện tại trên máy bạn:

```powershell
cd "C:\Users\User\Documents\mạng máy tính\đồ án socket"
```

Mở ba terminal riêng. Trong mỗi terminal đều chuyển đến cùng thư mục trên.

Terminal 1:

```powershell
python -X utf8 chat_server.py --h 127.0.0.1 --p 65433 --db demo_chat.db
```

Phải thấy `[READY]` và `[SQLITE]`. Giữ terminal này chạy.

Terminal 2:

```powershell
python -X utf8 gui_client.py --h 127.0.0.1 --p 65433 --name "An"
```

Terminal 3:

```powershell
python -X utf8 gui_client.py --h 127.0.0.1 --p 65433 --name "Binh"
```

Trong mỗi cửa sổ bấm **Kết nối**. Hai cửa sổ phải vào `#chung` nếu DB demo còn mới. Nếu DB đã dùng trước đó, bấm phòng `chung` ở cả hai để bắt đầu cùng nơi. Không chạy `server.py` cũ thay cho `chat_server.py`.

## 2. Chat thời gian thực và tiếng Việt

1. An nhập `Xin chào Bình 🌏`, nhấn Enter. Cả hai cửa sổ phải hiện cùng nội dung, người gửi An và giờ gửi.
2. Binh đang gõ dở, chưa Enter. An gửi thêm câu nữa. Binh vẫn nhận được trong lúc đang nhập.
3. Binh dùng Shift+Enter xuống dòng rồi Enter gửi. An phải thấy đủ các dòng.
4. Nhấn Enter khi ô nhập trống: GUI không gửi và không lỗi.

Đạt khi không mất chữ, không lặp tin và cửa sổ vẫn thao tác được.

## 3. Tạo/vào Public và tách biệt phòng

1. An nhập tên `demo-public` vào ô tạo/vào phòng. Không chọn Private. Bấm nút tạo/vào.
2. An phải thấy phòng hiện tại là `#demo-public` và mình là Admin.
3. Binh còn ở `#chung`. An gửi `Chỉ trong phòng public`. Binh chưa được thấy câu này ở sảnh.
4. Binh bấm `demo-public` trong danh sách. Không phải chờ duyệt; nhận lại câu vừa gửi từ lịch sử.
5. Binh gửi câu trả lời; An nhận ngay.

Tên phòng tự chuẩn hóa: bỏ `#`, chữ thường, khoảng trắng thành `-`, tối đa 32 ký tự sau chuẩn hóa.

## 4. Tạo Private, duyệt và từ chối

1. Khi đã kết nối, An nhập `demo-private`, tích **Phòng riêng tư (Private)** rồi tạo phòng.
2. Binh bấm vào phòng đó. Binh vẫn ở phòng cũ và thấy thông báo chờ Admin duyệt; không xem được nội dung phòng kín.
3. An thấy yêu cầu của Binh kèm nút **Duyệt / Từ chối**. Bấm **Từ chối** lần đầu. Binh nhận thông báo từ chối.
4. Binh bấm xin vào lại. Lần này An bấm **Duyệt**.
5. Binh được chuyển sang `#demo-private`. Hai bên gửi tin qua lại.

Nếu An chưa kết nối, thao tác tạo Private phải báo cần kết nối trước. Đừng dùng `--room` để tạo Private: tham số này chỉ chọn tên phòng, không chọn chế độ riêng tư.

## 5. Thành viên và quyền Admin

1. Trong `demo-private`, gõ `///` rồi Enter. Phải thấy An là Admin và có Binh trong danh sách.
2. Bấm **Thành viên** để mở hộp thoại, xem người đang ở phòng.
3. Ở An, dùng **Kick** bên cạnh Binh. Binh bị chuyển về `#chung`.
4. Binh bấm lại phòng kín: phải xin duyệt lại. An duyệt để tiếp tục các bước sau.
5. Nếu muốn thử chặn quyền trực tiếp, ở Binh gõ `/kick An`: server phải trả lỗi `KICK_DENIED`, An vẫn ở phòng.

Đây là quyền theo tên hiển thị, chưa có mật khẩu. Không coi việc duyệt tên là chứng minh danh tính.

## 6. Rời và xóa phòng

1. Trong phòng demo, Binh bấm **Rời phòng** hoặc gõ `/leave`.
2. Binh về `#chung`, phòng vừa rời bị ẩn khỏi danh sách riêng của Binh. Muốn vào lại, nhập lại tên phòng; Private phải xin duyệt.
3. Tạo một phòng Public demo khác, cho cả hai vào.
4. Binh gõ `/delete`: server từ chối vì không phải Admin.
5. An bấm **Xóa phòng**, đọc hộp thoại rồi chỉ xác nhận với phòng demo này.
6. Phòng biến mất đối với hai cửa sổ, mọi người về `#chung`. Lịch sử của phòng bị xóa.

Không thử xóa trên phòng đang dùng thật. Các phòng hệ thống mặc định không thể xóa.

## 7. File, ảnh, lưu về máy

Cho hai người về cùng một phòng. Chuẩn bị một file `.txt`, một ảnh PNG nhỏ và một file >64 KiB nhưng ≤5 MiB.

Có thể tạo file demo từ terminal thứ tư:

```powershell
python -X utf8 -c "from pathlib import Path; Path('demo_128k.bin').write_bytes(bytes(range(256))*512); Path('demo_6m.bin').write_bytes(bytes(6*1024*1024))"
```

1. An bấm nút gửi file/ảnh, chọn file TXT. Binh phải thấy tên file và nút **Lưu về máy**.
2. Binh lưu vào một đường dẫn khác. Mở file so nội dung.
3. Gửi `demo_128k.bin`. Binh lưu lại thành `demo_128k_nhan.bin`.
4. Trong PowerShell kiểm tra SHA-256:

```powershell
Get-FileHash .\demo_128k.bin -Algorithm SHA256
Get-FileHash .\demo_128k_nhan.bin -Algorithm SHA256
```

Hai giá trị hash phải giống nhau. Trong CMD có thể dùng `certutil -hashfile ten-file SHA256`.

5. Gửi PNG: Binh thấy ảnh xem trước và lưu lại được. JPEG/WebP chỉ có xem trước nếu cài Pillow; truyền/lưu file vẫn độc lập với việc xem trước.
6. Thử chọn `demo_6m.bin`: GUI phải báo vượt 5 MiB, không gửi. Gửi lại câu ngắn để xác nhận phiên vẫn dùng được.

## 8. Thu hồi chat và file/ảnh

1. An gửi câu `Tin nhắn cần thu hồi`. Ở An bấm **Thu hồi** ngay cạnh tin đó.
2. Cả hai cửa sổ thay nội dung bằng `Tin nhắn đã được thu hồi`.
3. An gửi PNG, chờ Binh nhận, sau đó thu hồi. Ảnh và nút tải của mục đó phải biến mất ở khung chat Binh.
4. Binh không có nút thu hồi tin của An.

Nếu Binh đã lưu file ra máy trước khi thu hồi, bản sao đó vẫn còn. Thu hồi chỉ cập nhật chat và bản ghi phía server.

## 9. Lịch sử SQLite và kết nối lại

1. Hai người vào `demo-lich-su`, gửi một câu, một file PNG, rồi thu hồi một mục khác.
2. Bấm **Ngắt kết nối** ở cả hai; đóng GUI. Dừng server bằng Ctrl+C.
3. Chạy lại server với **đúng `--db demo_chat.db`**, cùng port.
4. Mở GUI cùng tên An/Binh, không truyền `--room`, bấm Kết nối.
5. Phải khôi phục phòng gần nhất hợp lệ, vai trò Admin và 40 mục lịch sử gần nhất. Tin chưa thu hồi còn nguyên; mục đã thu hồi chỉ còn thông báo.

Nếu đổi DB hoặc đổi tên, kết quả khôi phục sẽ khác. Trong bài kiểm thử này nên giữ dưới 40 mục để dễ quan sát.

## 10. Mất kết nối, sai cổng, dữ liệu lỗi

- Khi đang chat, dừng server bằng Ctrl+C. GUI phải báo mất kết nối và cho kết nối lại sau khi server được bật lại.
- Đổi GUI sang một port **không có server**, bấm Kết nối: phải báo bị từ chối; giao diện vẫn thao tác được.
- Port `0`, `65536`, `abc`: phải bị từ chối, không mở kết nối.
- Các ca header chia nhỏ, gộp frame, EOF giữa dữ liệu, JSON sai kiểu, file đang gửi bị kick và chat sát giới hạn đã có test tự động. Chạy:

```powershell
python -X utf8 -m unittest -v test_client_integration test_regression
```

Mong đợi: `Ran 20 tests` và `OK`. Không cần tự dựng các gói lỗi bằng tay.

## 11. Kiểm tra lại bản echo của bạn

Dừng server chat nếu nó trùng cổng. Hai terminal riêng:

```powershell
python -X utf8 server.py --p 65432
```

```powershell
python -X utf8 client.py --p 65432
```

Gửi tiếng Việt, Enter trống, `/quit`, rồi chạy client lần nữa. Server echo phải tiếp tục phục vụ. `test_oversize.py` chỉ gửi header vượt giới hạn đến port cố định 65432; xem thông báo từ chối bên server và kết nối lại để xác nhận phục hồi.

## 12. Ghi kết quả

Ghi phiên bản commit, Python, tên ca, đạt/không đạt, log lỗi và ảnh hai cửa sổ. LAN hai máy thật, tải nhiều người dùng và ảnh JPEG/Pillow chưa nằm trong kết quả đã xác minh lần này.
