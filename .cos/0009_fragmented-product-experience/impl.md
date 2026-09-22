# Impl: COS Studio prototype verified locally
Intent: intent.md. Plan: plan.md. Author: GitHub Copilot. Status: accepted.

## What was built

Commit `b249ee0` chứa mã nguồn prototype Reflex ở `/prototype`, giữ trang cũ ở `/`:
shell theo workspace, các màn theo spec, detail dialog, dữ liệu mẫu typed, handler
quản lý workspace/công việc/hội thoại trong state UI, chạy mô phỏng và appearance.
Commit này ban đầu được lưu ở trạng thái chưa chạy được do thiếu quyền thực thi.

Cùng commit thêm unit test, browser proof `scripts/verify_0009.py`, tài liệu sử dụng,
và fingerprint cho các module UI.

Commit `e322f46` sửa lỗi được phát hiện khi người dùng cho phép build/run: signature
event của segmented control, khoảng cách CSS không có đơn vị, breakpoint desktop
và icon/label bị xuống hàng. Thêm regression test dựng component tree và từ chối
multi-select; browser proof kiểm số cột cùng gutter thực tế thay vì chỉ kiểm không
tràn màn hình. Prototype đã build và chạy local được.

Không nối Service, SDK, git hoặc nghiệp vụ backend vào state prototype.

Quan sát về skill được lưu riêng trong commit `ca67343`, tại
`.cos/0010_stage-records-without-actions/idea.md`. Không sửa skill hoặc gate.

## Where the plan was departed from

Code được lưu ở trạng thái draft trước khi kiểm chứng do quyền thực thi không có;
`plan.md` ghi lại trong commit `b249ee0`. Người dùng sau đó yêu cầu "build và run đi".
Việc kiểm chứng, screenshot và mở preview đã thực hiện trong lượt tiếp theo.

Browser proof mở rộng thêm 1024px và kiểm số cột/gutter sau khi screenshot phát hiện
layout chưa đúng; `plan.md` được cập nhật cùng commit sửa `e322f46`.

## What was measured

Ngày 2026-09-22, sau khi người dùng cho phép chạy:

- `npm test`: exit 0, **31 Node + 239 Python** test, số lấy từ output runner.
- `uv run cos-build`: exit 0, fingerprint bao phủ **6** file nguồn, số lấy từ output
  wrapper. Không dùng `reflex run`.
- `uv run python scripts/verify_0009.py --screenshots <session-files>/prototype`:
  exit 0, **5/5** luồng của intent; sáu màn, light/dark, empty/loading/error, không
  gọi business API; không tràn document ở **390/768/1024/1440px**, số cột và gutter
  đúng. Các ngưỡng là assertion trong script, không phải số đo về người dùng.
- Đã xem screenshot Overview và Board trên desktop/mobile; không coi việc agent
  xem ảnh là người dùng duyệt thiết kế.
- `uv run python scripts/verify_0004.py`: exit 0; trang gốc còn hiển thị dữ liệu,
  giữ sàn theme/responsive/contrast và negative control vẫn phát hiện backend chết.
- `uv run cos-baodo`: server preview đã khởi động. `curl --location` nhận HTTP 200
  tại `http://127.0.0.1:8790/prototype/`; `/api/health` HTTP 200; `ss` xác nhận bind
  `127.0.0.1:8790`, không phải interface công cộng.
- `git diff --check`: exit 0 trước commit sửa.

Lượt đầu bị tool từ chối build/test là sự kiện lịch sử, đã ghi trong `2d7677d`;
không phải blocker hiện tại. Không chạy lệnh tạo session AI thật hoặc push PR.

## What is still open

- Người dùng chưa xem hoặc duyệt thiết kế. Không có kết luận thẩm mỹ thay người dùng.
- Nối backend là phần việc sau khi người dùng duyệt, không thuộc unit này.
- Nghĩa của ship chưa chốt; không PR, review record hoặc ship. Plan vẫn accepted,
  không done. `accepted` ở impl chỉ ghi prototype đã được agent kiểm và sẵn sàng cho
  người dùng xem; không có nghĩa là đã được người dùng duyệt hoặc đã ship.
