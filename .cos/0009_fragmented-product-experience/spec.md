# Spec: A coherent local product prototype
Intent: intent.md. Author: GitHub Copilot. Status: accepted.

## Skip assessment

1. Không đạt: thay đổi nhiều hơn hai file hiện có, gồm đăng ký trang, fingerprint,
   test fingerprint và hướng dẫn sử dụng. Đây là tiêu chí buộc viết spec.
2. Không đạt: thêm một route giao diện công khai trong app local; không đổi HTTP API
   nghiệp vụ hoặc schema lưu workspace.
3. Đạt: không thêm dependency.
4. Đạt: hành vi nằm trong prototype mà intent đã mô tả.
5. Đạt: không đổi auth, dữ liệu cá nhân hay chính sách cấp quyền.

## Requirements

- **R1 — Cách ly.** `/prototype` hiển thị prototype; `/` giữ nguyên app thật. State
  prototype không gọi Service, SDK, git hoặc ghi artifact/workspace. Mọi màn có nhãn
  demo; dữ liệu chỉ sống trong phiên UI, reload có thể khôi phục mẫu.
- **R2 — Điều hướng.** Có sidebar, workspace switcher và đủ sáu khu vực từ intent.
  Chuyển workspace đổi board, phiên chat và activity tương ứng; không rò nội dung
  workspace trước vào workspace vừa chọn.
- **R3 — Workspace.** Có tìm kiếm, tạo workspace mô phỏng, đổi tên và gỡ khỏi demo
  qua xác nhận. Tên rỗng và trùng phải báo rõ. Workspace mới có empty state.
- **R4 — Board.** Có tìm kiếm/lọc công việc, chuyển board/list, mở chi tiết, đọc
  artifact và timeline mẫu, đổi manual/auto trong mô phỏng, chạy demo với tiến trình
  nhìn thấy được. Không kéo thả để vượt gate; không có nút ship thật.
- **R5 — Session.** Chọn/chuyển phiên chat, tạo phiên mẫu và gửi tin nhắn nhận phản
  hồi có nhãn mô phỏng. Lịch sử không bị mất khi chuyển màn hoặc chuyển phiên.
- **R6 — Settings.** Light/dark có hiệu lực thật trên prototype; mật độ hiển thị đổi
  được. Thông tin môi trường và quyền AI hiện rõ là baseline minh họa, không phải
  trạng thái máy đã kiểm. Không có công tắc cấp quyền backend.
- **R7 — Trạng thái và accessibility.** Có empty/loading/error cùng đường quay lại.
  Nút chỉ icon có tên truy cập; dialog đóng bằng Escape, quản lý focus theo Radix.
  Không tràn document tại 390, 768 và 1440 CSS pixel. Ba kích thước là tiêu chí thiết
  kế được chọn cho unit, không phải số đo người dùng.
- **R8 — Chứng minh.** Kiểm tra năm luồng của intent bằng trình duyệt trên app build
  thật; kiểm tra state độc lập không cần AI hoặc mạng. Test cũ còn xanh.
- **R9 — Không nhận vơ.** Không ghi người dùng đã duyệt, skill đã sửa hoặc sản phẩm đã
  ship. `accepted` của artifact không được diễn giải thành người dùng duyệt thiết kế.

## Design

**COS Studio** là tên concept cho prototype, không đổi tên package. Ngôn ngữ thị giác:
nền trung tính, mặt phẳng phân lớp, accent iris, typography rõ và nhiều khoảng nghỉ.
Sidebar nhỏ, vùng công việc rộng; desktop có drawer chi tiết bên phải, mobile có
navigation dialog. Bố cục giữ ngữ cảnh thay vì dồn mọi tính năng vào một trang dài.

Overview dẫn vào việc cần làm, không dùng dashboard toàn biểu đồ giả. Workspaces dùng
thẻ dự án, trạng thái và menu hành động. Board nhóm theo trạng thái làm việc, trong khi
giai đoạn artifact vẫn được ghi riêng trên thẻ. Các trạng thái và stage trong fixture
chỉ là ví dụ, không thay thế bảng luật trong `cos.mjs`.

Chi tiết công việc chứa tab Overview, Artifacts, Timeline và một vùng chạy demo.
Lượt chạy mô phỏng ghi rõ không sửa file, không tiêu token thật, không đánh dấu đã
duyệt/ship. Sessions có danh sách hội thoại và transcript riêng theo workspace.
Activity & Usage lấy số từ cùng fixture thay vì hardcode tổng không khớp.

Tách dữ liệu mẫu, state và primitive trình bày. Dùng Radix/Reflex sẵn có, style bằng
Python props và semantic colors; không thêm font từ bên ngoài hoặc ảnh qua mạng.
Component không mang nghiệp vụ backend. State cục bộ UI là ranh giới tạm cho lượt
prototype; giai đoạn nối backend cần thiết kế adapter riêng.

Fingerprint phải bao phủ mọi module đóng góp vào giao diện, bao gồm theme đang có.
Không dùng `reflex run` hoặc mở server ra interface công cộng.

## Out of scope

Auth, billing, collaboration, invitation, backend mới, persistence dữ liệu demo,
thực thi AI thật, sửa skill/gate/runner, deploy, PR và ship. Không giả vờ các chức
năng này đã chạy bằng cách đặt nút không hoạt động.

## Concerns

- Prototype không chứng minh backend hỗ trợ mọi tương tác. Giữ nhãn demo rõ và
  tài liệu chỉ ra ranh giới; người dùng quyết định phạm vi nối thật sau.
- Chữ "accepted" trong artifact hiện dễ bị hiểu là duyệt của người dùng. Prototype
  phân biệt readiness của artifact và phản hồi người dùng, không sửa harness ở đây.
- Light/dark sử dụng cơ chế browser của Reflex đang có, nên đổi appearance cũng
  có thể ảnh hưởng tab app cũ cùng origin. Đây là tùy chọn trình bày, không đổi dữ liệu.
- Nghĩa của ship chưa được người dùng chốt. Không thực hiện stage ship cho unit này.

## Open questions

- Người dùng có duyệt concept, mật độ và bố cục sau khi bấm thử không?
- Phần nào sẽ được nối backend thật ở unit kế tiếp?
- Hợp đồng impl/review/ship còn mở và sẽ được ghi thành quan sát riêng, không tự sửa
  trong prototype.
