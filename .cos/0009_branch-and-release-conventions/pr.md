# PR: A branch grammar, a tag grammar, and the three places that check them
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: accepted.

## Where

**https://github.com/baodq97/coscc/pull/1**
Branch `feat/branch-and-release-conventions` → `main`.

Đây là **pull request đầu tiên** của repo. Trước nó, `gh pr list --state all` trả 0 dòng và
137 commit vào thẳng `main` (`intent.md` `## Problem`). Tên branch không do ai gõ: nó ra từ
`node .claude/scripts/cos.mjs unit-branch 0009_branch-and-release-conventions`, đọc
`Type: feat` trên header `intent.md:2`.

## Scope of the diff

`git diff --stat main...HEAD`, ngày 2026-09-22: **15 file, +1344 −46**, tám commit.

| File | Dòng |
|---|---|
| `scripts/verify_0009.py` **(new)** | +522 |
| `.cos/0009_branch-and-release-conventions/impl.md` **(new)** | +150 |
| `.claude/scripts/cos.mjs` | +244 −? (275 → 517 dòng) |
| `.claude/scripts/cos.test.mjs` | +162 (23 → 55 test) |
| `.claude/harness.md` | +100 (180 → 268 dòng) |
| `.github/workflows/pr.yml` **(new)** | +55 |
| `.github/workflows/release.yml` **(new)** | +48 |
| `.claude/skills/write-intent/SKILL.md` | +25 |
| `.cos/0009_.../plan.md`, `spec.md` | +39, +31 |
| `.claude/CLAUDE.md` | +4 −4 |
| `pyproject.toml`, `package.json`, `uv.lock`, `package-lock.json` | 5 dòng, chỉ version |

`coscc/` **không đổi một dòng nào**.

## What a reviewer should look at first

**`.claude/scripts/cos.mjs`, phần dispatcher ở cuối file.** Rào `--root` nằm ở đó chứ không
ở trong từng lệnh, và đó là chỗ dễ làm sai mà không ai thấy: `--root` bị tước khỏi `argv`
**trước** khi lệnh được đọc, nên "lệnh này không đụng `cosDir`" là một mệnh đề yếu hơn "cờ đó
đã bị từ chối". Chỉ mệnh đề thứ hai ngăn được `coscc/board.py:90` trỏ một câu hỏi về git vào
bản checkout của repo người khác — nơi `coscc/board.py:48` ghi rằng script "needs no secret,
so it is given none". Năm test gọi CLI bằng subprocess để giữ rào đó, vì mọi test khác trong
file import hàm thuần và không chạm tới dispatcher.

Sau đó là **`.github/workflows/pr.yml`**. Đây là workflow đầu tiên của một repo **public**,
và ba thứ trong đó không có check nào bắt được: `permissions` khai tường minh chứ không thừa
kế mặc định của repo; ba action bên thứ ba ghim theo **commit SHA** chứ không theo tag; và
trigger là `pull_request`, không phải `pull_request_target`.

`impl.md` `## Where the plan was departed from` có tám mục; mục 5 là mục đáng đọc: **bằng
chứng tự sai bốn lần, và cả bốn là cùng một lỗi** — một file đo một chuỗi trong khi bản thân
nó chứa chuỗi đó. Hai lần trong số đó làm claim **đạt** một cách sai, không phải đỏ sai:
negative control của C3 đạt bằng cách không làm gì, và C6 đo nhầm đoạn văn.

## What the checks say, and what they do not

`gh pr checks 1`, 2026-09-22: **`branch-name` pass (4s), `tests` pass (17s)**. Log của job
`tests` cho **55** test node và **256** test python trên runner — nghĩa là cả hai file YAML
parse được, điều mà `verify_0009.py` C7 **không** kiểm được (nó kiểm cấu trúc, không kiểm cú
pháp; `plan.md` Risk 1).

Không job nào là cổng. Không gì chặn merge dựa trên chúng, và theo `spec.md` C3 runner mang
bản vá Python khác máy đã đo mọi proof trong `scripts/`.

`verify_0009.py` hiện **exit 1, xanh 10/13**. Ba claim đỏ — ruleset, hai release, mọi commit
vào `main` qua PR — cần chính PR này được merge, rồi bước 12–14 của `plan.md`.
