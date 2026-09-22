# Ship: Hai tầng tài liệu, một trường header có máy đọc, và một proof thôi mục
Review: review.md. Author: Bao Do. Status: accepted.

## What shipped

`93175b5` trên `main`, qua PR #4, squash. Unit thứ hai đi đủ tám stage.

| | Trước | Sau |
|---|---|---|
| `.claude/harness.md` | 295 dòng | **xoá** |
| `.claude/CLAUDE.md` | 197 dòng | **117** |
| Cộng hai file | **492** | **117** |
| `.claude/rules/coscc-app.md` | — | 85 dòng, nạp theo `paths:` |
| `intent.md` thiếu `Type:` | 8 | **0** |
| File nói cách cắt branch | **0** | 1 |
| Lệnh `cos.mjs` được liệt kê | 3 | **7**, khớp bảng `run` |
| `npm test` | 55 node + 256 python | **60** + 256 |
| `verify_0008.py` | exit 1, **12/14** | exit 0, **14/14** |

Không có release mới. Unit này không đổi hành vi của app, nên `pyproject.toml` vẫn `0.1.0`
và không tag nào được đẩy.

## Outcome, đo lại nguyên văn

`intent.md` đòi: trước **2026-10-06**, `wc -l .claude/harness.md .claude/CLAUDE.md` cộng lại
**≤ 200 dòng**. Đo ngày **2026-09-22**, 14 ngày trước hạn: **117**. Giảm từ 492.

Năm điều kiện làm kết quả này **sai**, đo từng điều:

| Điều kiện sai | Đo |
|---|---|
| Tổng vượt 200 dòng | 117 |
| Bất kỳ chỗ nào trong bảy chỗ còn mở | 0 — xem bảng dưới |
| Hai file còn giải thích một quy tắc đã có máy cưỡng chế | không đo được bằng lệnh; xem `## Limits` |
| `CLAUDE.md` còn mô tả app hoặc trỏ tới nơi mô tả nó | `grep -c "coscc/"` → **0** |
| Danh sách lệnh không khớp bảng `run` | 7 lệnh, khớp |

Bảy chỗ thiếu, đo lại đúng bằng cột "Đo" của `intent.md`:

| # | Chỗ | Hôm nay |
|---|---|---|
| 1 | Không nơi nào nói khi nào cắt branch | `git grep -liE "git switch\|gh pr create" -- .claude/` → **1** file |
| 2 | `Type:` không gì kiểm | thiếu → problem; `nonsense` → problem khác; 5 test |
| 3 | `unit-branch` cần `intent.md` đã có | `write-intent` nói ra, `CLAUDE.md` bước 2–3 nói ra |
| 4 | `write-pr` nói sai hiện trạng | câu *"may not have one"* đã gỡ |
| 5 | Hai danh sách lệnh thiếu | một danh sách, **7** lệnh |
| 6 | `## Work units` không nhắc `Type:` | `CLAUDE.md` bước 2 |
| 7 | `plan.md: done` là terminal, chỉ cảnh báo ở `CLAUDE.md` | vẫn ở `CLAUDE.md` — nhưng `harness.md`, thứ từng được copy, không còn tồn tại để mâu thuẫn |

## Limits

**Điều kiện thứ ba không có phép đo.** *"Còn giải thích một quy tắc đã có máy cưỡng chế"* —
không `grep` nào phân biệt câu **nêu tên** một quy tắc với câu **giải thích** nó. Ngưỡng 200
dòng là đại lượng thay thế và `intent.md` đã nói vậy. Người đọc muốn kiểm điều kiện này phải
đọc 117 dòng bằng mắt.

**`.claude/rules/` chưa ai chứng minh là nạp được.** Chỗ hở lớn nhất còn lại, ghi ở
`review.md` `## What was not reviewed` và Risk 3 của plan. 85 dòng kiến thức app có thể đã
biến mất khỏi mọi phiên thay vì nạp có điều kiện, và nếu vậy thì **không có triệu chứng nào**
xuất hiện trong unit này.

**Không có `verify_0010.py`.** Người khởi xướng chốt *"Không — sửa một lần"*. Cái giá đã ghi
ở plan `## Chọn không làm` và nó bắt đầu chạy từ hôm nay: không gì ngăn `CLAUDE.md` phình
lại, và không gì phát hiện nếu nó phình.

**Ba lớp lỗi bằng chứng, không lớp nào có lệnh canh.** `0009` gặp "claim đỏ sai" và "claim
xanh sai". `0010` thêm "claim đúng lúc viết, mục theo thời gian" — `verify_0008` đỏ trên
`main` một ngày và chỉ lộ vì unit này chạy lại nó. Năm proof còn lại vẫn chưa chạy lại.

## Follow-ups, không mở unit

- Chạy `verify_0001`–`verify_0006` một lượt, và xem còn cái nào đo `HEAD` hay cây làm việc ở
  chỗ đáng lẽ phải ghim.
- Quy tắc "rebase lên `main` mới nhất" đã hai unit liên tiếp không được thử.
- `coscc/state.py:252` trả `"Complete"` trước khi đọc `problems`, nên một unit `finished`
  mang problem vẫn ở lane Complete. Đọc được lúc kiểm bước 3; ngoài phạm vi unit này.
