# PR: transitions are the record, and the state set is configuration
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: accepted.

## Where

https://github.com/baodq97/coscc/pull/13 — nhánh `feat/board-cannot-say-what-happened`,
base `main`.

**File này là lần đầu `pr.md` phải tự đủ.** `intent.md` (khối đính chính đầu file) chốt rằng
không gì của coscc đi vào cây repo dùng chung, nên với mọi repo khác, thứ duy nhất reviewer
đọc được là **mô tả PR**. Body của PR #13 vì thế không trỏ vào `.cos/` một lần nào: nó tự nêu
vấn đề, số đo, bốn thành phần, chỗ đáng soi trước, proof và các giới hạn đã biết. Ở repo này
thì `.cos/` vẫn còn nên còn đối chiếu được — chỗ khác thì không, và đó chính là bài kiểm tra.
`spec.md` C4 vẫn mở: đây là một mẫu, chưa phải một quy tắc.

## Scope of the diff

`git diff --stat main...HEAD`: **21 file, +3082 −7**.

| Nhóm | File |
|---|---|
| Mới, mã | `coscc/states.py`, `coscc/states.json`, `coscc/history.py`, `coscc/backfill.py` |
| Mới, test | `coscc/states_test.py`, `coscc/history_test.py`, `coscc/backfill_test.py` |
| Mới, proof | `scripts/verify_0013.py` |
| Sửa | `coscc/data.py`, `coscc/service.py`, `coscc/api.py`, `coscc/harness.py` |
| Sửa, test | `coscc/data_test.py`, `coscc/service_test.py`, `coscc/api_test.py`, `coscc/harness_test.py` |
| Tài liệu | `.claude/rules/coscc-app.md` |
| Artifact | bốn file trong `.cos/0013_board-cannot-say-what-happened/` |

Test tăng từ **299** lên **368** (Python); node giữ nguyên **60**.

**Không đổi, và là cố ý:** `coscc/board.py`, `.claude/scripts/cos.mjs`, `coscc/policy.py`,
`coscc/runner.py`, `coscc/screens.py`, `coscc/state.py`, `coscc/config.py`.

## What a reviewer should look at first

**`scripts/verify_0013.py`, `claim_3` (`0334adc`).** Không phải phần lớn nhất, nhưng là phần
rủi ro nhất, và nó là một departure khỏi bản đầu của chính tôi. Bản đầu xoá một chuyển trạng
thái còn hàng khác chồng lên sau, nên phép chiếu **không** lùi — mà một cột trạng thái lưu
sẵn cũng sẽ cho đúng câu trả lời ấy. Claim đó không có khả năng đỏ, tức nó vô dụng đúng với
requirement duy nhất nó tồn tại để kiểm (R1). Nay nó chọn hàng cuối **của chính artifact đó**
và đòi `from_state <> to_state`. `claim_5` cùng loại lỗi: đòi `git status` sạch là đo người
đang chạy chứ không đo sản phẩm.

Hai chỗ tiếp theo, theo thứ tự hậu quả nếu sai:

1. **`coscc/data.py:48-56` là cửa một chiều.** `v0.2.3` trở về trước **từ chối mở** DB mà bản
   này đã chạm. Lùi app phải kèm dọn `~/.cos/cos.db`. `plan.md` Risk 3.
2. **`coscc/backfill.py` vứt bỏ tác giả commit.** Cố ý (`spec.md` C1): điền `actor` từ commit
   là khẳng định một con người làm việc mà một agent đã làm. Cả 104 sự kiện nhập vào đều mang
   `actor: unknown`, và proof in con số đó ra thay vì giấu.

`impl.md` `## Where the plan was departed from` liệt kê ba departure; cả ba đã ghi vào
`plan.md` trong cùng commit gây ra chúng.
