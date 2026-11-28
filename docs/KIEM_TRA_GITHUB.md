# Kiểm tra trạng thái GitHub và cách làm việc nhóm

**Ngày kiểm tra:** 28/11/2026. Repository: [khimbodoi/socket-transport](https://github.com/khimbodoi/socket-transport).

## Kết quả trước đợt cập nhật biên bản này

| Hạng mục | Kết quả |
| --- | --- |
| Branch mặc định | `main`, commit `3347aa2e11b07822e8022562755b9005922c1ef3` |
| Tích hợp giao diện/chat | [PR #1](https://github.com/khimbodoi/socket-transport/pull/1) đã merge |
| `feat/giao-dien-chat` | Commit cuối `010cecabc424699e8dfdc27c5b9a95472b9c7672` đã nằm trong lịch sử `main`; branch này không có commit riêng còn chờ tích hợp |
| `codex/integrate-chat-20261127` | Nội dung đã tích hợp vào `main`; không cần merge lại branch này |
| PR đang mở | Không có tại thời điểm kiểm tra trước khi tạo PR tài liệu của đợt này |
| Kiểm thử tự động trên GitHub | Chưa có cấu hình `.github/workflows` trong phiên bản đã kiểm tra; không có commit status được trả về cho commit nêu trên. Các kết quả trong biên bản chạy cục bộ |
| Branch protection/rulesets | Chưa xác minh; không kết luận đã bật hoặc tắt |

Đợt cập nhật này chỉ bổ sung tài liệu kết quả và kế hoạch; không sửa code ứng dụng. Commit kiểm thử trong biên bản là commit runtime trước cập nhật tài liệu, vì vậy sẽ khác SHA của commit tài liệu mới trên `main`.

## Cách tránh phải tích hợp lại từ đầu

1. Mỗi người clone repository và cập nhật `main` trước khi bắt đầu việc mới. Nếu có thay đổi chưa commit, lưu vào branch riêng trước khi pull.
2. Tạo branch cho từng việc từ `main` mới nhất, sửa và kiểm thử ở branch đó; không tiếp tục dùng branch cũ đã merge để làm việc không liên quan.
3. Push branch và mở PR vào `main`. Người review đọc diff, kiểm tra lỗi/xung đột và kết quả test trước khi merge.
4. Sau merge, mọi người cập nhật `main` bằng `git pull --ff-only`. Nếu lệnh báo phân kỳ, kiểm tra các commit cục bộ trước; không dùng reset/force-push để bỏ qua.
5. Trước demo, kiểm tra server và client đều dùng cùng phiên bản/giao thức. Khi cần đối chiếu, ghi SHA từ `git rev-parse HEAD`.

Thư mục đang chạy `đồ án socket` hiện là bản sao mã nguồn, không phải Git checkout. Việc sửa tại đó không tự xuất hiện trên GitHub. Nhóm nên thống nhất dùng một checkout làm nơi phát triển; giữ bản sao phục vụ demo nếu cần, nhưng phải ghi phiên bản và đồng bộ có chủ đích.

## Việc còn nên làm

- Thêm CI cho các bài chạy không cần GUI; với bài Tkinter cần runner có màn hình hoặc thiết lập display phù hợp. Phân biệt bài lỗi đã biết với gate hồi quy đang đạt.
- Sau khi CI ổn định, cân nhắc yêu cầu PR và checks trước merge. Chỉ thay cài đặt repository sau khi nhóm thống nhất.
- Giữ kế hoạch sửa và biên bản gắn với commit thực tế; không coi một PR merge thành công là bằng chứng mọi chức năng đã đạt.
