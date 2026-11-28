# Kế hoạch sửa sau kiểm thử chuyên sâu

**Ngày:** 28/11/2026. **Trạng thái:** D03 đã sửa phần worker kết nối; D07 đã sửa phần GUI chờ gửi mạng; các mục khác chờ nhóm xử lý. Theo dõi tại [issue #3](https://github.com/khimbodoi/socket-transport/issues/3) và [changelog](../CHANGELOG.md).

Áp dụng cho code tại commit `3347aa2e11b07822e8022562755b9005922c1ef3`. Xem [biên bản](KET_QUA_KIEM_THU.md) để phân biệt kết quả chạy thực tế với tiêu chí nghiệm thu dự kiến.

## Cách đọc mức ưu tiên

- **P1:** ưu tiên trước khi chốt phần chat/phòng/file vì có thể sai quyền, sai dữ liệu hoặc sai phiên kết nối. D01 là giới hạn thiết kế đã biết, cần nhóm chốt phạm vi xác thực.
- **P2:** xử lý tiếp để tăng độ ổn định, kiểm soát tài nguyên và phản hồi lỗi rõ ràng.
- Các mức này dùng để sắp việc cho đồ án, không phải điểm CVSS hoặc kết luận sản phẩm đã được chứng nhận bảo mật.

## Danh sách vấn đề và hướng xử lý

| Mã | Ưu tiên | Kết quả đã quan sát | Module cần sửa | Hướng sửa và tiêu chí nghiệm thu |
| --- | --- | --- | --- | --- |
| D01 | P1 nếu dùng ngoài nhóm tin cậy | Dùng lại tên của người đã offline được gắn với quyền/lịch sử lưu theo tên đó | `chat_server.py`, `storage.py`, client/protocol | Tách ID người dùng khỏi tên hiển thị và có cơ chế xác thực phiên. Người dùng mới trùng tên không nhận quyền hoặc lịch sử riêng của người cũ. Nếu chưa làm tài khoản, phải giữ giới hạn demo nhóm tin cậy trong README; đổi tên tự động không thay thế xác thực. |
| D02 | P1 | Hai thao tác đăng nhập đồng thời được cấp cùng tên | `chat_server.py` | Giữ chỗ tên và đăng ký session trong cùng vùng khóa; giải phóng khi thất bại/ngắt. Đăng nhập đồng thời cùng tên phải nhận danh tính phân biệt. |
| D03 | P1 | Một lần kết nối cũ đã hủy có thể ghi đè socket của lần kết nối mới | `network_client.py` | **Đã sửa worker kết nối:** mã thế hệ được kiểm tra cùng khóa khi cập nhật; kết quả thành công/lỗi muộn bị bỏ qua. Ca gốc đạt 3/3 và 4 ca hồi quy mới đạt. **Theo dõi tiếp:** việc gắn tác vụ file/sự kiện đã xếp hàng đúng phiên chưa xác minh đầy đủ. |
| D04 | P1 | Kick diễn ra giữa bước kiểm tra quyền và bước lưu file; file vẫn được lưu vào phòng cũ | `chat_server.py`, `storage.py` | Gộp kiểm tra tư cách thành viên và ghi dữ liệu thành thao tác nhất quán với kick/xóa phòng; hủy transfer khi mất quyền. Sau khi kick đã hoàn tất, thao tác lưu đang chờ không được ghi vào phòng cũ. |
| D05 | P1 | Chat đang chờ lưu có thể tạo lại phòng Private vừa bị xóa, thành phòng Public | `storage.py`, `chat_server.py` | Không tự tạo phòng trong đường lưu chat/file. Chỉ luồng tạo/vào phòng được tạo phòng; kiểm tra phiên bản/trạng thái phòng trong transaction. Xóa phòng phải vô hiệu các thao tác ghi còn chờ. |
| D06 | P1 | Sự kiện thu hồi đến trước một mục lịch sử cũ; GUI có thể hiển thị lại nội dung đã thu hồi | `chat_server.py`, `gui_client.py` | Quy định thứ tự/phiên bản sự kiện; GUI lưu trạng thái thu hồi theo ID kể cả khi chưa có widget. Replay đến muộn không khôi phục chat, file hoặc ảnh đã thu hồi. |
| D07 | P2 | Luồng Tk bị giữ khi gửi chat phải chờ khóa gửi file | `gui_client.py`, `network_client.py` | **Đã sửa callback GUI:** hàng đợi 128 frame riêng từng kết nối, worker gửi nền, chờ hello xác nhận trước thao tác, ngắt hủy ngay. 7 ca hồi quy đạt, bộ tổng hợp 31/31 đạt. **Theo dõi tiếp:** deadline tổng cho gửi/hello chưa triển khai; không coi enqueue thành server đã nhận. |
| D08 | P1 | Metadata file được nhận/lưu/xác nhận nhưng phản hồi mở rộng vượt giới hạn frame; phát lại lịch sử gặp lỗi | `protocol.py`, `chat_server.py` | Giới hạn tên file theo byte UTF-8 và kiểm tra toàn bộ phản hồi trước khi lưu/ACK. Một bản ghi cũ không hợp lệ không được làm ngắt toàn bộ lịch sử; cần xử lý và log rõ. |
| D09 | P2 | Dữ liệu file chờ có thể vượt kích thước đã khai báo; chưa có hạn chờ/quota tổng đầy đủ | `chat_server.py` | Kiểm tra số byte và chỉ số chunk trước khi giữ dữ liệu; giới hạn số transfer/dung lượng theo phiên và tổng, thêm TTL. Hủy/ngắt/đổi phòng phải thu hồi bộ đệm; dữ liệu vượt khai báo bị từ chối sớm. |
| D10 | P2 | ID ngoài miền số nguyên SQLite gây exception không bắt ở worker của client | `protocol.py`, `chat_server.py` | Giới hạn ID trong miền `1..2**63-1`, trả lỗi dữ liệu có cấu trúc trước khi gọi DB. Phiên vẫn tiếp tục sau yêu cầu không hợp lệ; không có exception thoát worker. |
| D11 | P2 | Danh sách thành viên tổng hợp vượt giới hạn frame khi tên quá dài | `protocol.py`, `chat_server.py` | Giới hạn tên theo byte UTF-8; thiết kế ngân sách/phân trang cho danh sách thành viên và phòng. Kiểm thử sát giới hạn: trả dữ liệu/lỗi rõ ràng, không đóng phiên vì phản hồi quá lớn. |

D04 và D05 có chung vấn đề tính nhất quán của thao tác phòng; D08 và D11 cùng liên quan ngân sách frame. **11 ca không đạt không được hiểu thành 11 lỗ hổng độc lập.**

## Thứ tự triển khai đề xuất

1. **Nhất quán phòng và dữ liệu:** D04, D05, D06. Chốt thứ tự khóa/transaction để tránh deadlock; kiểm tra cả chat và file.
2. **Vòng đời phiên:** D02, D03. Cấp tên nguyên tử, vô hiệu worker và tác vụ gửi của phiên cũ. Chốt phạm vi D01 với nhóm trước khi bổ sung tài khoản.
3. **Giới hạn dữ liệu:** D08, D09, D10, D11. Xác minh cả request, phản hồi bổ sung metadata, lịch sử và bộ đệm đang chờ.
4. **Độ phản hồi GUI:** D07. Tách gửi khỏi luồng Tk, có giới hạn hàng đợi và thông báo khi bận.
5. **Nghiệm thu:** chạy lại 20 bài hiện có, các ca hồi quy ứng với mục đã sửa, kiểm thử thủ công và LAN hai máy khi có thiết bị.

Mỗi nhóm sửa nên có PR riêng, mô tả hành vi trước/sau và kết quả kiểm thử. Chỉ đánh dấu một mục đã sửa sau khi chạy lại ca tương ứng trên commit mới. D03 đã sửa phần worker kết nối; D07 đã sửa phần GUI chờ gửi mạng; D01, D02, D04–D06, D08–D11 và phần theo dõi tiếp của D03/D07 còn mở. Chủ dự án tự sửa D03 và phần đầu D07, Codex hoàn thiện hello/phiên theo yêu cầu; các mục còn lại chưa phân công người phụ trách hoặc ngày hoàn thành.

## Những hành vi đã kiểm chứng đạt

- C01: 10 client gửi đồng thời tổng cộng 100 tin; kiểm tra đủ 1.000 lượt nhận, không mất/trùng tin trong phạm vi bài thử.
- C02: hai file gửi song song từ một client cùng chat; tổng 524.288 byte đúng nội dung/hash, chat vẫn đến.
- D12: kiểm tra dọn widget file của GUI; không còn widget thừa trong bài thử. Không ghi nhận mục này là bug.

Các ca đồng thời được điều phối để kiểm tra thứ tự thread cụ thể; kết quả không ước lượng xác suất lỗi trong sử dụng hằng ngày. Chưa kiểm chứng tải lớn, LAN hai máy hay mọi thứ tự thực thi.
