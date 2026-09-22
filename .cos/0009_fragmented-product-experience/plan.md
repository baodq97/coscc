# Plan: Build the isolated COS Studio prototype
Intent: intent.md. Spec: spec.md. Author: GitHub Copilot. Status: accepted.

## Files that change

- `scripts/verify_0009.py` (new): browser proof cho năm luồng và responsive.
- `cos_baodo/prototype_data.py` (new): fixture có kiểu, chỉ trong bộ nhớ.
- `cos_baodo/prototype.py` (new): state mô phỏng và sáu màn.
- `cos_baodo/studio.py` (new): primitive, màu và khung trình bày dùng lại.
- `cos_baodo/prototype_test.py` (new): tính nhất quán fixture, state, cách ly.
- `cos_baodo/cos_baodo.py`: đăng ký `/prototype`, không thay route `/`.
- `cos_baodo/build.py`, `cos_baodo/build_test.py`: fingerprint các module UI mới và
  theme `ui.py` mà trang đang dùng.
- `README.md`: đường dẫn, cách chạy và giới hạn demo.
- `docs/prototype.md` (new): bản đồ màn hình, nguyên tắc thị giác và ranh giới backend.
- Một `idea.md` ở unit riêng do `cos.mjs new-path` cấp: ghi quan sát về ba skill,
  chưa thiết kế hoặc sửa chúng.

## Order of work

1. Tạo browser proof với năm luồng kiểm được. Khi prototype chưa có, lệnh phải đỏ,
   không được báo thành công do chỉ nhận HTTP 200.
2. Thêm fixture typed và primitive; triển khai state cục bộ, shell và các màn trong
   một thay đổi có thể compile. Kiểm fixture và handler bằng unit test.
3. Đăng ký route riêng, cập nhật fingerprint cùng test và hướng dẫn. Giữ nguyên mọi
   handler thật. Build để kiểm component/typing của Reflex.
4. Chạy `npm test`, build và browser proof; sửa lỗi được phát hiện. Chụp hình để
   kiểm trực tiếp composition desktop/mobile, không chỉ đếm component.
5. Ghi kết quả thực tế vào impl record sau commit code; giữ plan ở accepted, không
   tự chạy PR/review/ship. Ghi quan sát skill vào unit riêng, không sửa harness.

## Risks

1. **Chạm dữ liệu thật từ demo.** Không import Service/SDK vào module prototype,
   kiểm nguồn và browser request; demo không gửi `/api/*` nghiệp vụ. Preview chỉ
   cập nhật state Reflex riêng.
2. **Bundle cũ hoặc sai cổng.** Mọi module UI mới nằm trong fingerprint; build và
   server dùng cùng cổng loopback. Không giết server của người khác nếu cổng bận.
3. **Mất ngữ cảnh hoặc dữ liệu chéo workspace.** Đổi workspace đóng detail, reset
   bộ lọc và chọn session thuộc workspace mới. Lượt chạy mô phỏng giữ ID gốc,
   không cập nhật nhầm workspace đang được chọn.
4. **Prototype đẹp nhưng nút vô dụng.** Browser proof bấm đủ năm luồng, kiểm nội
   dung thay đổi, không chỉ screenshot. Các chức năng chưa có không hiện như đã có.
5. **Hiểu nhầm shipped/approved.** Luôn có nhãn prototype và dữ liệu mẫu. Không
   chạy ship, không đánh dấu người dùng duyệt. Công việc skill chỉ là quan sát mở.

## Proof

```
npm test
uv run cos-build
uv run python scripts/verify_0009.py
```

Pass khi tất cả lệnh exit 0; browser proof xác nhận năm luồng của intent, đủ sáu màn,
empty/loading/error và không tràn document ở 390/768/1440 CSS pixel trong light/dark.
Lệnh tạo app với working folder tạm, không tạo session AI hoặc clone repo.
Thiếu browser/build/cổng bận phải exit 2, không lẫn với UI lỗi exit 1.

Không chạy proof tạo phiên AI thật hoặc push PR: chúng không kiểm phần prototype và
có side effect ngoài phạm vi. Không viết lại giao diện chính trước khi người dùng
duyệt. Screenshot hỗ trợ đánh giá thẩm mỹ, không thay cho quyết định của người dùng.
