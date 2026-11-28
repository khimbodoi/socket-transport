# Changelog

Lịch sử thay đổi theo ngày, bản mới nhất ở trên. Chưa phát hành phiên bản có tag. Mã Dxx trỏ tới cùng một vấn đề trong biên bản và kế hoạch sửa; số issue/PR của GitHub là số riêng.

## 2026-11-28 — Sửa D03: kết nối lại không bị worker cũ ghi đè

### Đã sửa

- **D03, worker kết nối:** trước đây hủy kết nối A rồi kết nối B, worker A hoàn tất muộn có thể thay socket B hoặc phát lỗi cho lần B. Bây giờ mỗi lần kết nối được cấp `_connection_id`; hủy/dọn kết nối làm mã cũ hết hiệu lực. Worker thành công muộn đóng socket riêng; worker lỗi muộn không đổi trạng thái hoặc báo lỗi cho lần mới.
- Giữ việc kiểm tra mã và cập nhật trạng thái trong cùng `_state_lock`. Đăng ký luồng nhận cùng lúc với socket được chấp nhận.
- **Người thực hiện:** chủ dự án tự sửa `network_client.py`; Codex review, kiểm thử và chuẩn bị bản cập nhật. Không sửa giao thức trên dây, GUI hay quy tắc phòng trong đợt này. Chỉ dọn khoảng trắng cuối dòng khi đóng gói code.

### Kiểm thử

- Ca tái hiện gốc A → hủy → B → kết quả A đến muộn: **đạt 3/3 lần** trên bản người dùng đã lưu.
- Thêm `test_network_lifecycle.py`: 4 ca kiểm tra thành công, timeout, từ chối và lỗi socket đến muộn. Dùng TCP loopback thật, đồng bộ thứ tự bằng Event; không phải chờ mạng ngẫu nhiên.
- Bộ mới **4/4 đạt**; toàn bộ bộ hiện có và bộ mới **24/24 đạt**. Đây là chạy cục bộ, chưa có CI trên GitHub.

```powershell
python -X utf8 -m unittest -v test_network_lifecycle
python -X utf8 -m unittest -v test_client_integration test_regression test_network_lifecycle
```

Đã xác nhận ca hồi quy thất bại đúng lỗi trên code cũ tại `ac3c505`, rồi đạt trên bản sửa. [Log trước sửa](docs/kiem-thu/d03-before-20261128.txt).

Log: [D03](docs/kiem-thu/d03-20261128.txt), [toàn bộ 24 bài](docs/kiem-thu/d03-full-20261128.txt). Test tự dùng port trống, không cần mở server trước; bộ tổng hợp cần Tkinter và màn hình cho bài GUI.

### Còn mở

| Mã | Mô tả để nhóm nhận biết | Trạng thái |
| --- | --- | --- |
| D01 | Người khác dùng lại tên offline có thể nhận quyền/lịch sử gắn với tên đó | Chờ chốt phạm vi xác thực |
| D02 | Hai người đăng nhập đồng thời có thể được cấp cùng tên | Chưa sửa |
| D04 | Người bị kick vẫn có thể hoàn tất ghi file nếu kick xen giữa kiểm tra và lưu | Chưa sửa phần đồng thời |
| D05 | Chat chờ lưu có thể tạo lại phòng Private vừa xóa thành Public | Chưa sửa |
| D06 | Lịch sử đến muộn có thể làm tin đã thu hồi xuất hiện lại | Chưa sửa |
| D07 | Gửi chat đợi khóa file trên luồng Tk, giao diện đứng chờ | Chưa sửa |
| D08 | Thông tin file quá dài làm phản hồi/lịch sử vượt frame | Chưa sửa |
| D09 | Bộ đệm file thiếu kiểm tra số byte khai báo, quota và hạn chờ đầy đủ | Chưa sửa |
| D10 | ID ngoài miền SQLite gây exception ở worker của client | Chưa sửa |
| D11 | Danh sách thành viên có tên dài vượt giới hạn frame | Chưa sửa |
| D03 — theo dõi tiếp | Tác vụ gửi file đang chạy và sự kiện đã xếp hàng khi đổi phiên | Chưa xác minh đầy đủ; không thuộc phần đã đóng trong đợt này |
| LAN | Kiểm thử trên hai máy thật | Chưa thử vì chưa có máy thứ hai |

**24 bài đạt không có nghĩa các mục đang mở đã hết lỗi.** Chưa chạy lại toàn bộ 14 ca chuyên sâu sau bản sửa này; giữ nguyên bằng chứng lịch sử bên dưới. Chi tiết ưu tiên/tiêu chí: [kế hoạch sửa](docs/KE_HOACH_FIX.md). Nhận việc và theo dõi bằng checklist tại [issue #3](https://github.com/khimbodoi/socket-transport/issues/3); liên kết PR sửa sẽ được ghi tại issue.

## 2026-11-28 — Kiểm thử chuyên sâu, lập kế hoạch

- [PR #2](https://github.com/khimbodoi/socket-transport/pull/2): bổ sung biên bản, kế hoạch sửa và kiểm tra GitHub; không sửa runtime.
- Runtime được kiểm tra: `3347aa2`. Bộ hiện có đạt 20/20; bộ chuyên sâu 14 ca có 3 đạt và 11 không đạt, lặp lại qua ba lần chạy. Các mã D01–D11 được ghi nhận ở thời điểm này; D12 dọn widget đạt, không tính là bug.

## 2026-11-27 — Tích hợp ứng dụng chat với nền transport

- [PR #1](https://github.com/khimbodoi/socket-transport/pull/1): tích hợp branch `feat/giao-dien-chat`, giữ bản echo và bổ sung tài liệu/test. Chức năng gồm GUI, chat nhiều người, phòng Public/Private, quản trị, file/ảnh, SQLite và thu hồi.
- Đã sửa: chat quá dài sau khi server bổ sung metadata; tạo Private khi chưa kết nối; JSON `type` sai kiểu; gửi trước `hello` hoặc gửi lại `hello`; bổ sung kiểm tra chunk và kích thước file.
- Đã thêm kiểm tra quyền trước khi hoàn tất file sau kick. Kiểm thử sau đó cho thấy vẫn còn trường hợp đồng thời D04, vì vậy không coi vấn đề này đã xử lý hoàn toàn.
- Bộ tích hợp 20/20 đạt trong phạm vi lúc đó. Xem [biên bản](docs/KET_QUA_KIEM_THU.md) để biết giới hạn và các phát hiện sau tích hợp.

## Quy ước cập nhật tiếp

Mỗi PR ghi mã lỗi, biểu hiện trước/sau, người thực hiện, lệnh và kết quả test, phần chưa xử lý. Cập nhật changelog, checklist issue và kế hoạch cùng đợt; không tự gán việc cho thành viên khi nhóm chưa thống nhất. Chỉ đánh dấu đã sửa khi có bằng chứng kiểm thử. Giữ kết quả kiểm thử cũ để biết lỗi được phát hiện và sửa ở thời điểm nào.
