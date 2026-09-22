# Intent: The product experience is fragmented
Author: GitHub Copilot. Status: accepted.

> Người khởi xướng, ngày 2026-09-22: "hiện tôi muốn chuẩn hóa ui theo stacks hiện tại
> làm prototype trước rồi bằng code thật thông qua reflex python luôn.
> hiện tại ui/ux khá tệ, cần có đầy đủ khung, các chức năng như một Saas hoàn chỉnh.
> managed workspaces, Board, settings, ...
> bạn giúp tôi với, với phần này tôi cần sáng tạo là chính"

## Problem

Các chức năng đã có nhưng chưa tạo thành trải nghiệm sản phẩm thống nhất. Trang ghép
Workspaces, Board và Chat theo chiều dọc, dùng một route
(`cos_baodo/cos_baodo.py:845-867`). Người dùng phải tìm từng khối thay vì có ngữ cảnh
workspace và điều hướng rõ ràng. Chat mới hiện phản hồi cuối cùng
(`cos_baodo/cos_baodo.py:512-559`); board hiện dạng bảng và chi tiết bên dưới
(`cos_baodo/cos_baodo.py:790-842`). Đánh giá "ui/ux khá tệ" là nhận xét của người dùng,
không phải số đo khách quan.

Người dùng muốn duyệt trải nghiệm trước khi đầu tư nối lại toàn bộ backend. Đổi màu
trang hiện tại không giải quyết được việc thiếu cấu trúc và thiếu luồng thao tác.

## Proposed outcome

Đến hết ngày **2026-09-22**, bản prototype chạy bằng Reflex cho phép hoàn thành
**5/5 luồng tương tác mô phỏng** trong trình duyệt: quản lý/chọn workspace; tìm và mở
công việc trên board; xem artifact, timeline và chạy thử một bước; mở/chuyển phiên chat;
đổi tùy chọn giao diện. Mỗi luồng có phản hồi nhìn thấy được, mọi dữ liệu mẫu được nhận
diện rõ, không tạo phiên AI thật hoặc thay đổi workspace thật.

Số **5** và ngày trên là tiêu chí người dùng xác nhận trong cuộc trao đổi ngày
2026-09-22. Một luồng không thực hiện được, chỉ là nút trang trí, hoặc tác động lên dữ
liệu thật thì kết quả không đạt. Việc người dùng duyệt thẩm mỹ là bước riêng sau bàn
giao; không suy ra sự đồng ý từ kết quả kiểm tra.

## Affected users and systems

- Người dùng cá nhân, local-first, chưa đăng nhập; đây là phạm vi người dùng đã chọn.
- Giao diện Reflex, cấu hình đăng ký trang và fingerprint bundle.
- Phần trình bày gồm **6** khu vực do người dùng chọn: Overview, Workspaces, Board cùng
  chi tiết công việc, Sessions/chat, Activity & Usage, Settings.
- Backend và giao diện đang chạy vẫn phải sử dụng được như trước.

## Constraints

- Prototype tương tác bằng Reflex Python, dữ liệu mẫu; dừng để người dùng duyệt trước
  khi nối backend. Không dùng HTML/CSS viết tay thay cho component Python.
- Settings chỉ thể hiện giao diện, thông tin môi trường và chính sách AI hiện có.
  Không mở quyền sửa working root hoặc bật quyền tool qua prototype.
- Không gọi SDK AI, clone/pull, push, tạo PR, deploy hoặc tự ship trong unit này.
- Không đổi ngữ nghĩa gate hoặc cơ chế an toàn của app thật.
- Build theo `uv run cos-build`; giữ kiểm tra fingerprint và loopback.
- Người dùng đã yêu cầu tiếp tục prototype trước, ghi việc sửa `write-impl`,
  `write-review`, `write-ship` thành phần việc riêng. Chưa chốt nghĩa của ship,
  chưa cho phép tự thực hiện ship.

## Open questions

- Phản hồi trực tiếp của người dùng về bố cục, mật độ thông tin và phong cách sau khi
  xem prototype. Chưa có phản hồi thì không ghi là đã duyệt.
- Những tương tác mô phỏng nào sẽ được nối backend ở unit tiếp theo?
- Hợp đồng thực thi thật của các skill impl/review/ship thuộc phần việc riêng;
  prototype không được ngầm xác nhận rằng chúng đã được sửa.
