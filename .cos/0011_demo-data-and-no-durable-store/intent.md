# Intent: The approved experience runs on invented data and remembers nothing
Author: Claude Opus 5. Status: accepted.

> Người khởi xướng, ngày 2026-09-22: "read commit mới nhất đó là prototype cho project
> này. tôi thấy khá ưng ý rồi. tôi muốn bulk impl để hoàn thành nhanh nhất, với stacks
> chỉ cần theo best practices của reflex, sử dụng db là SQLite và file để lưu trữ meta,
> và object, dữ liệu lưu chính ở /home/bd/.cos đi."

## Problem

`0009` dựng xong sáu màn COS Studio và người khởi xướng nói đã ưng ý. Nhưng thứ họ ưng
là một bản diễn: mọi con số, workspace, công việc, hội thoại và dòng hoạt động trên màn
hình đều do `cos_baodo/prototype_data.py:1-128` bịa ra, và `PrototypeState` giữ tất cả
trong bộ nhớ của một phiên trình duyệt (`cos_baodo/prototype.py:24-57`). Đóng tab là mất.

Hệ quả là hai thứ tách rời nhau. Trang thật ở `/` nối đúng backend nhưng có bố cục mà
người khởi xướng gọi là tệ; trang đẹp ở `/prototype` không đọc một byte nào của dữ liệu
thật (`cos_baodo/cos_baodo.py:867-868`). Người dùng không có một chỗ nào vừa dùng được
vừa muốn dùng.

Cùng lúc, trạng thái app thật đang nằm rải trong thư mục làm việc: danh sách workspace ở
`.cos-baodo.json` (`cos_baodo/store.py:42`), nhật ký ở `.cos-journal.jsonl`
(`cos_baodo/journal.py:37`), và cả hai chỉ tồn tại khi `COS_WORKING_DIR` được đặt
(`cos_baodo/config.py:115-121`). Không đặt biến đó thì app không có chỗ nào để nhớ bất
cứ thứ gì. Đây là lý do "sống qua một lần khởi động lại" chưa phải là tính chất mà app
này có.

## Proposed outcome

Đến hết ngày **2026-09-22**, một người chạy app trên máy này hoàn thành **5/5** luồng
tương tác của `0009` trên **dữ liệu thật** — workspace thật dưới thư mục làm việc, board
đọc từ `.cos/` của workspace đó, phiên chat thật do SDK tạo — rồi tắt app, bật lại, và
cả **5** luồng vẫn thấy đúng dữ liệu vừa tạo.

Năm luồng đó là: quản lý/chọn workspace; tìm và mở một công việc trên board; xem artifact
và timeline của nó; mở/chuyển phiên chat; đổi tùy chọn giao diện.

Một luồng không làm được, hay làm được nhưng mất sau khi khởi động lại, thì kết quả là
**sai**. Số **5**, thước đo "sống qua restart" và ngày trên là tiêu chí người khởi xướng
chọn trong trao đổi ngày 2026-09-22; không suy ra từ đo đạc nào.

## Affected users and systems

- Người dùng cá nhân, local-first, chưa đăng nhập. Không đổi so với `0009`.
- Sáu màn của `0009`: Overview, Workspaces, Board, Sessions, Activity & Usage, Settings.
  Con số **6** lấy từ `.cos/0009_fragmented-product-experience/intent.md`.
- Nơi app cất trạng thái của chính nó. Người khởi xướng chọn `/home/bd/.cos`, và chọn nó
  chứa **dữ liệu app**, không chứa bản clone của workspace.
- `cos_baodo/store.py` và `cos_baodo/journal.py`: hai cơ chế lưu trữ hiện có, cả hai đều
  đã được một proof chứng minh (`scripts/verify_0005.py`).
- Trang `/` hiện tại: người khởi xướng chọn để COS Studio thay thế nó, nên
  `scripts/verify_0004.py` — proof duy nhất mở trình duyệt thật — sẽ không còn đúng đối
  tượng nó đang kiểm.

## Constraints

- Dữ liệu chính ở `/home/bd/.cos`: SQLite cho metadata, file cho object. Đây là lựa chọn
  của người khởi xướng, không phải kết luận từ đo đạc.
- `/home/bd/.cos` chỉ chứa dữ liệu app. Workspace (bản clone git) vẫn nằm dưới
  `COS_WORKING_DIR`. Quy tắc "một entry lưu *tên*, không bao giờ lưu đường dẫn" và
  "`COS_WORKING_DIR` không set được qua HTTP" phải còn nguyên.
- Chuyển `store` và `journal` sang SQLite thì phải chạy lại `scripts/verify_0005.py` với
  4 tiến trình ghi đồng thời. Không được coi tính chất mà `0005` đã chứng minh là hiển
  nhiên đúng với cơ chế mới.
- Theo best practice của Reflex, viết bằng component Python. Không HTML/CSS viết tay.
- Giữ nguyên tư thế an toàn: chỉ bind loopback; phiên mặc định là chat, không tool; bảng
  grant vẫn ở `cos_baodo/policy.py`, ngoài `Config`.
- Build bằng `uv run cos-build`, giữ kiểm tra fingerprint và kiểm tra port khớp.
- Dừng sau `impl`. Không push branch, không mở PR, không review, không ship trong unit
  này. Người khởi xướng chọn điểm dừng này ngày 2026-09-22.
- `npm test` phải xanh. Không xóa hay bỏ qua test nào đang có.

## Open questions

- Thư mục `/home/bd/.cos` và thư mục `.cos/` của mỗi repository trùng tên nhưng khác vai
  trò. Cái tên này có gây nhầm khi dùng thật không — chưa biết, và đổi tên sau sẽ tốn một
  lần migrate.
- Chưa có dữ liệu thật nào trong `.cos-baodo.json` hay `.cos-journal.jsonl` trên máy này
  để migrate hay không: chưa kiểm. Nếu có, phải quyết định đọc tiếp hay bỏ.
- Người khởi xướng nói "ưng ý" sau khi xem prototype, nhưng `0009` để mở việc duyệt thẩm
  mỹ. Unit này không được ngầm coi "ưng ý" là đã duyệt xong thiết kế.
- Sau khi COS Studio thay trang `/`, `scripts/verify_0004.py` kiểm cái gì: viết lại theo
  trang mới, hay giữ nó như proof lịch sử. Spec quyết định.
