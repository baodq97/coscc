# PR: an installed copy could run no stage of the loop — the app now carries its rules
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: accepted.

## Where

https://github.com/baodq97/coscc/pull/10

Branch `fix/installed-copy-runs-no-stage`, vào `main`. Tên branch lấy từ
`cos.mjs unit-branch 0012_installed-copy-runs-no-stage`, không gõ tay. URL là thứ
`gh pr create` in ra.

## Scope of the diff

`git diff --stat main...HEAD`, ngày 2026-09-22: **16 file, +1.397 / −51**.

| Nhóm | File | Dòng |
|---|---|---|
| Code mới | `coscc/harness.py` | +165 |
| | `coscc/harness_test.py` | +159 |
| Code sửa | `coscc/board.py`, `coscc/runner.py` | 32, 36 |
| | `coscc/board_test.py`, `coscc/runner_test.py` | 11, 67 |
| Đường phát hành | `.github/workflows/release.yml`, `.gitignore` | 44, 1 |
| | `scripts/check_wheel.py` | +53 |
| Proof | `scripts/verify_0012.py` | +269 |
| Tài liệu | `docs/install.md`, `.claude/rules/coscc-app.md` | 44, 8 |
| Artifact | bốn file trong `.cos/0012_.../` | 559 |

Hơn một phần ba số dòng là artifact của chính unit và proof; phần code thực sự đổi hành vi
là `harness.py`, cộng hai chỗ gọi nó trong `board.py` và `runner.py`.

## What a reviewer should look at first

**`coscc/runner.py`, chỗ `skill_for` ném lỗi.** Đây là chỗ rủi ro nhất, không phải chỗ
nhiều dòng nhất. Nó lật một quyết định có chữ trong code — `"Missing is not fatal"` — và
đổi một đường đi im lặng thành một đường đi dừng lại. Câu hỏi cho reviewer: nếu skill của một
stage biến mất vì lý do nào đó ngoài đóng gói, dừng hẳn có đúng là điều nên xảy ra không?
`spec.md` C2 ghi ai quyết và vì sao; `impl.md` ghi phép đo dẫn tới nó.

**Sau đó là `plan.md` bước `8b`, chỗ đi lệch.** Packaging đúng rồi mà Board vẫn chết vì
`node` của nvm không nằm trong PATH của systemd user service. Ba thay đổi đi kèm — thông báo
lỗi nêu PATH, tài liệu ghi cái bẫy, proof coi đó là exit 2 — không nằm trong plan gốc. Lý do
chúng vẫn ở trong unit này chứ không tách ra: nếu không có chúng thì outcome của `intent.md`
vẫn sai, và một unit đóng lại với outcome sai là thứ `plan.md: done` không được phép nói.

**Điều không kiểm được:** `scripts/verify_0011.py` chưa chạy — nó cần một máy thứ hai qua
SSH. PR này sửa `docs/install.md` và `release.yml`, hai thứ `0011` đo, nên các claim về
reboot không có ai kiểm trong lần này. `impl.md` mục `## What was measured` ghi nguyên văn.
