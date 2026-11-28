# Biên bản kết quả kiểm thử tích hợp

**Trạng thái: bản nháp chờ chủ dự án review; còn các ca chuyên sâu không đạt, chưa chốt nghiệm thu.**

| Thông tin | Nội dung |
| --- | --- |
| Ngày chạy kiểm thử tự động | 27/11/2026 và chạy bổ sung ngày 28/11/2026 |
| Ngày cập nhật biên bản | 28/11/2026 |
| Repository | [khimbodoi/socket-transport](https://github.com/khimbodoi/socket-transport) |
| Phiên bản đã tích hợp và kiểm thử | `3347aa2e11b07822e8022562755b9005922c1ef3` trên `main`, [PR #1](https://github.com/khimbodoi/socket-transport/pull/1) |
| Phạm vi mạng | Cùng một máy, TCP loopback `127.0.0.1` |
| Người thực hiện kiểm thử tự động | Codex trên máy phát triển của chủ dự án |
| Người kiểm thử thủ công và review | Chủ dự án |

## Nguồn ghi nhận kết quả

- **Tự động ngày 27/11:** 20 bài đạt trong 2,238 giây tại thư mục `đồ án socket`; log gốc lưu cục bộ trong `output/share/chat-tests-20261127.txt`.
- **Tự động ngày 28/11:** chạy lại trên worktree tách riêng từ đúng commit runtime ở trên. Bộ hiện có đạt 20/20 trong 3,134 giây; bộ chuyên sâu có 14 ca, chạy ba lần đều có 3 đạt và 11 không đạt. Xem [log bộ hiện có](kiem-thu/baseline-20261128.txt) và [tổng hợp lần chạy, mã ca, hash runtime/log](kiem-thu/2026-11-28-summary.json).
- **Thủ công:** ngày 28/11/2026, chủ dự án cho biết đã làm các bước đầu của hướng dẫn, không gặp lỗi và kết quả giống phần kiểm thử của Codex. Chưa xác định cụ thể bước cuối đã thực hiện, nên biên bản chưa đánh dấu toàn bộ ca thủ công là đạt.
- Bộ chuyên sâu và log chi tiết giữ tại `tmp/socket-audit-20261128/audit/` trên máy phát triển, chưa đưa vào repository. Tài liệu công khai gồm kết quả tổng hợp và kế hoạch sửa; không thay thế mã kiểm thử/log chi tiết khi nhóm cần tái hiện.
- Dữ liệu kiểm thử là dữ liệu giả, SQLite/thư mục tạm và TCP loopback thật. Một số ca dùng điểm đồng bộ để điều khiển thứ tự thread, nhằm kiểm tra tình huống tranh chấp cụ thể; không suy ra tần suất xảy ra ngoài thực tế. **Đợt này chưa sửa code ứng dụng.**

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

**Bộ hiện có: 20/20 bài đạt**, gồm 4 bài ban đầu và 16 bài bổ sung. Các assertion bao gồm:

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

## Kiểm thử chuyên sâu ngày 28/11/2026

| Lần chạy | Số ca | Đạt | Không đạt | Thời gian | Exit code |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 14 | 3 | 11 | 2,289 giây | 1 |
| 2 | 14 | 3 | 11 | 2,243 giây | 1 |
| 3 | 14 | 3 | 11 | 2,362 giây | 1 |

Cùng 11 ca không đạt ở cả ba lần. Các bài đặt kỳ vọng cho hành vi mong muốn; thất bại thể hiện mục cần sửa hoặc giới hạn thiết kế đang tồn tại. **Không cộng ba lần chạy thành 42 chức năng khác nhau; không quy đổi số ca đạt thành phần trăm hoàn thành đồ án.**

| Nhóm | Kết quả quan sát | Mã ca |
| --- | --- | --- |
| Danh tính và phiên | Tên offline vẫn gắn quyền cũ; đăng nhập đồng thời có thể trùng tên; worker kết nối cũ có thể ghi đè phiên mới | D01–D03 không đạt |
| Phòng và thứ tự dữ liệu | Kick chưa nhất quán với bước lưu file; chat chờ ghi có thể tạo lại phòng Private vừa xóa; lịch sử đến muộn có thể hiển thị lại nội dung thu hồi | D04–D06 không đạt |
| GUI | Gửi chat phải đợi khóa gửi file trên luồng Tk. Trong bài giữ khóa 250 ms, lời gọi gửi chặn khoảng 250 ms; đây không phải đo độ trễ mạng | D07 không đạt |
| Dữ liệu và tài nguyên | Metadata file/phản hồi danh sách có thể vượt ngân sách frame; bộ đệm file vượt số byte khai báo; ID ngoài miền SQLite gây exception ở worker client | D08–D11 không đạt |
| Chat đồng thời | 10 client, tổng 100 tin, kiểm tra đủ 1.000 lượt nhận, không mất/trùng trong bài thử | C01 đạt |
| File và chat song song | Hai file tổng 524.288 byte đúng nội dung/hash, chat vẫn đến | C02 đạt |
| Dọn GUI | Không còn widget file thừa sau thao tác xóa khung trong bài thử; không ghi thành bug | D12 đạt |

Xem [kế hoạch sửa và tiêu chí nghiệm thu từng mục](KE_HOACH_FIX.md). D04/D05 và D08/D11 có vấn đề nền liên quan nhau; 11 ca không đạt không tương đương 11 lỗ hổng độc lập. D10 làm kết thúc worker của phiên liên quan trong bài thử, **không làm dừng toàn bộ tiến trình server**.

## Ghi nhận kiểm thử thủ công để chủ dự án review

Đối chiếu với [hướng dẫn kiểm thử](KIEM_THU.md). Kết quả tự động trong bảng trên vẫn có giá trị độc lập; cột thủ công dưới đây dành để chủ dự án bổ sung đúng những gì mình đã trực tiếp thao tác.

| Mục hướng dẫn | Nội dung | Ghi nhận thủ công hiện tại |
| --- | --- | --- |
| Các bước đầu | Khởi động server/GUI và các thao tác đầu tiên trong hướng dẫn | Chủ dự án báo không gặp lỗi; chờ xác nhận đã thực hiện đến mục nào |
| 2–3 | Chat thời gian thực, tiếng Việt, nhiều dòng, phòng Public và tách biệt phòng | Chưa xác nhận riêng từng ca |
| 4–6 | Private, duyệt/từ chối, thành viên, kick, rời/xóa phòng | Chưa xác nhận riêng từng ca |
| 7 | Gửi/lưu file, so hash, xem PNG, từ chối file quá lớn | Chưa xác nhận riêng từng ca |
| 8 | Thu hồi chat và file/ảnh trên cả hai cửa sổ | Chưa xác nhận riêng từng ca |
| 9 | Khởi động lại với cùng DB, khôi phục phòng và lịch sử | Chưa xác nhận riêng từng ca |
| 10–11 | Mất kết nối, sai cổng và kiểm tra echo cũ | Chưa xác nhận riêng từng ca |

Chỉ đổi một ca thành “Đạt” khi đã trực tiếp thực hiện và quan sát đúng kết quả. Nếu có ảnh/log thủ công, bổ sung đường dẫn ở ca tương ứng; hiện biên bản chưa gắn bằng chứng ảnh cho các ca này.

## Lỗi phát hiện và sửa trước merge

1. Chat yêu cầu vừa 64 KiB nhưng phản hồi thêm sender/room/timestamp/id bị quá giới hạn, làm đóng phiên và lưu lịch sử không phát lại được. Đã kiểm tra kích thước phản hồi trước khi lưu và trả `MESSAGE_TOO_LARGE`; phiên tiếp tục dùng được.
2. Người bị kick có thể hoàn tất file đang gửi vào phòng cũ. Đã thêm kiểm tra phòng còn tồn tại, phòng hiện tại và tư cách thành viên trước khi lưu file. **Kiểm thử mới D04 cho thấy còn khoảng tranh chấp giữa kiểm tra và ghi; chưa coi mục này đã xử lý đầy đủ khi có đồng thời.**
3. Chọn tạo Private trước khi kết nối làm tạo Public vì `hello` không mang chế độ riêng tư. Đã yêu cầu kết nối trước khi tạo Private.
4. `type` JSON là list/dict gây `TypeError`. Đã chuyển thành `ProtocolError` đúng luồng xử lý.
5. Chat/file trước `hello` và gửi lại `hello` để đổi danh tính trong phiên chưa được chặn. Đã thêm kiểm tra đầu phiên.
6. Thêm kiểm tra tổng số chunk theo kích thước khai báo, miền chỉ số binary và kích thước file nhận ở GUI.

## Giới hạn kết luận

- Chưa xác minh LAN hai máy thật, Internet/NAT, tải lớn, tình huống tranh chấp đồng thời ở mọi điểm hoặc tất cả thời điểm ngắt giữa truyền file. Ca 10 client là phép kiểm tra giới hạn, không phải benchmark tải lớn.
- PNG được kiểm thử không cần Pillow; JPEG/WebP và nhánh Pillow chưa thử.
- Hộp thoại chọn/lưu file được thay đường dẫn tạm trong test, chưa phải thao tác chuột thủ công toàn bộ GUI. Người dùng kiểm lại theo `KIEM_THU.md`.
- Định danh/quyền được lưu theo tên hiển thị, chưa có mật khẩu hay xác thực. Người dùng khác có thể dùng lại tên khi chủ tên offline; đây là demo nhóm tin cậy.
- Thu hồi không xóa file đã được người nhận lưu; file/DB không được mã hóa. Không tuyên bố bản này đủ an toàn để triển khai công khai.

## Kết luận tạm thời

Phiên bản tích hợp **đạt 20/20 bài của bộ hiện có**, nhưng **11/14 ca chuyên sâu không đạt**, lặp lại ở cả ba lần chạy. Vì vậy chưa thể kết luận bản chat đã hết lỗi hoặc chốt nghiệm thu toàn bộ. Chủ dự án chưa ghi nhận lỗi trong những bước thủ công đã thử; điều đó không phủ định các lỗi về thứ tự thực thi được tái hiện tự động.

Ưu tiên sửa theo [kế hoạch](KE_HOACH_FIX.md), chạy lại ca liên quan trên commit mới và tiếp tục thủ công/LAN hai máy khi có thiết bị. Đối chiếu yêu cầu đề và phân công nhóm vẫn cần chủ dự án xác nhận. Xem thêm [trạng thái GitHub và quy trình nhóm](KIEM_TRA_GITHUB.md); branch giao diện cũ đã được tích hợp, không cần merge lại.

## Danh sách chủ dự án cần review

- [ ] Xác nhận thông tin phiên bản, môi trường và kết quả tự động trong biên bản.
- [ ] Ghi rõ đã thực hiện thủ công đến mục nào trong `KIEM_THU.md` và cập nhật các dòng tương ứng.
- [ ] Bổ sung lỗi hoặc khác biệt nếu có; nếu không có, ghi “Chưa ghi nhận trong phạm vi đã thử”.
- [ ] Giữ các mục chưa thử (đặc biệt LAN hai máy) ở trạng thái chưa kiểm thử.
- [ ] Review các mục D01–D11, chốt phạm vi xác thực và phân công sửa; chỉ đóng mục khi có kết quả chạy lại.
- [ ] Chốt biên bản sau khi review.

**Ý kiến của chủ dự án:** Chờ review.
