# PR: one function decides where a unit lives, then git, then the two routes
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: accepted.

## Where

https://github.com/baodq97/coscc/pull/18 — nhánh `feat/product-cannot-start-a-work-unit`,
base `main`.

**Body của PR tự đủ, tiếp tục mẫu `0013` đặt ra.** Nó không trỏ vào `.cos/` một lần nào: tự
nêu vấn đề, bảng sáu bước, số đo đỏ-trước và xanh-sau, tiền đã tiêu, và ba chỗ đáng soi
trước. `spec.md` C4 của `0013` vẫn mở — đây là mẫu thứ hai, chưa phải quy tắc.

**Unit này là lần đầu chuyện đó thành bắt buộc chứ không chỉ là mẫu.** `spec.md` R2 chốt
`.cos/` không bao giờ được tạo trong cây repo đích, nên với mọi repo không phải repo này,
mô tả PR là thứ duy nhất reviewer đọc được.

## Scope of the diff

`git diff --stat main...HEAD`: **27 file, +2413 −84**.

| Nhóm | File |
|---|---|
| Mới, mã | `coscc/units.py` |
| Mới, test | `coscc/units_test.py` |
| Mới, proof | `scripts/verify_0014.py` |
| Sửa, mã | `coscc/api.py`, `coscc/board.py`, `coscc/gitops.py`, `coscc/harness.py`, `coscc/journal.py`, `coscc/policy.py`, `coscc/runner.py`, `coscc/screens.py`, `coscc/service.py`, `coscc/state.py` |
| Sửa, test | `coscc/api_test.py`, `coscc/board_api_test.py`, `coscc/gitops_test.py`, `coscc/policy_test.py`, `coscc/runner_test.py`, `coscc/service_test.py`, `coscc/state_test.py` |
| Công cụ | `scripts/proof_harness.py` |
| Harness | `.claude/skills/write-pr/SKILL.md`, `.claude/rules/coscc-app.md` |
| Artifact | năm file trong `.cos/0014_product-cannot-start-a-work-unit/` |

Test Python tăng từ **368** lên **419**; node giữ nguyên **60**.

**Không đổi, và là cố ý:** `.claude/scripts/cos.mjs` (`intent.md` ràng buộc 4),
`coscc/data.py`, `coscc/states.py`, `coscc/backfill.py`, `coscc/config.py`. Store nằm dưới
`data_dir` đã có; không thêm knob thứ năm.

## What a reviewer should look at first

**`coscc/policy.py` (`a6ff136`).** Không phải phần lớn nhất — 38 dòng — nhưng là chỗ duy
nhất trong PR này nới một ranh giới an ninh. Trước nhánh này nó là một câu: ghi ngoài
workspace thì từ chối. Nay là hai, và câu thứ hai nhận một đường dẫn do `units.root()` sinh
ra. Nó chỉ an toàn vì `units.root()` lấy đầu vào từ môi trường và từ một tên workspace đã
qua `_workspace_or_refuse`, **không bao giờ** từ request. `plan.md` Risk 1 là mục tôi không
muốn phải viết ra, và đây là nó.

Ba chỗ tiếp theo, theo thứ tự hậu quả nếu sai:

1. **`.claude/skills/write-pr/SKILL.md` (`80b0894`) giờ merge.** Cùng một bên mở và merge.
   Không gate nào bị gỡ — chưa từng có gate — nhưng năng lực của một bước `pr` đến từ `gh`
   của máy này, nên nó chạm tới **mọi** repo tài khoản đó chạm được, và nay việc nó làm kết
   thúc bằng một lần merge chứ không phải một lời đề nghị. Hazard đã ghi vào
   `.claude/rules/coscc-app.md`.
2. **`/api/timeline` giờ trả về câu trả lời của một bước đã trả tiền mà hỏng**
   (`coscc/runner.py:150`, `coscc/journal.py:372`). Service này không có login trên bất kỳ
   route nào và mặc định bind `0.0.0.0`. Đúng mối nguy `0013` `spec.md` C6 đã nêu, nay rộng
   hơn một bậc. Ghi nhận, chưa sửa.
3. **`6/6` không phải sáu bước `intent.md` viết nguyên văn.** Bước 5 ở `intent.md` là *"chạy
   các stage, mỗi artifact một commit"*; `spec.md` R2 bỏ vế sau khi chuyển artifact ra khỏi
   cây repo, và `scripts/verify_0014.py:115` chấm bước 5 là *"work the stages"*. Cố ý, có
   ghi, nhưng ai đọc con số cần biết định nghĩa đã dịch.

`impl.md` `## Where the plan was departed from` liệt ba departure, và mục 2 là một lần vi
phạm `write-plan` bất biến 8 do chính tôi gây ra — bảng file trong `plan.md` được sửa muộn
tám commit với `coscc/harness.py` và ba commit với phần còn lại. Nó nằm trong hồ sơ vì nó
đã xảy ra, không phải vì nó đã được xử lý.
