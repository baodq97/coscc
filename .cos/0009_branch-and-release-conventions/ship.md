# Ship: The convention is live, and main is behind a gate
Review: review.md. Author: Bao Do. Status: accepted.

## What went out

**https://github.com/baodq97/coscc/pull/1**, squash-merged 2026-09-22T09:38:29Z thành
commit **`82d599d`** trên `main`. Mười ba commit thành một; commit ấy mang cả 13 thông điệp,
235 dòng. **15 file, +1344 −46.** `coscc/` không đổi một dòng.

**Hai release**, cả hai do `.github/workflows/release.yml` dựng từ một tag được đẩy:

| Tag | Loại | Lúc |
|---|---|---|
| `v0.1.0-rc.1` | Pre-release | 2026-09-22T09:39:30Z |
| `v0.1.0` | Latest | 2026-09-22T09:40:20Z |

**Ba thứ cưỡng chế, không nằm trong file nào:**

- Setting của repo: `allow_squash_merge: true`, `allow_merge_commit: false`,
  `allow_rebase_merge: false`, `delete_branch_on_merge: true`.
- Ruleset `active` id **23815312** trên `main`, năm rule: `pull_request`
  (`allowed_merge_methods: ["squash"]`, `required_approving_review_count: 0`),
  `required_linear_history`, `required_status_checks` (`strict`, hai context `branch-name`
  và `tests`), `deletion`, `non_fast_forward`.
- Hai workflow, chạy thật: `pr` bốn lần xanh, `release` hai lần xanh.

**Còn ở trong file, nên đi theo mọi bản copy `.claude/`:** ngữ pháp branch mười type, ngữ
pháp tag, nguồn version và năm chỗ, bốn lệnh `cos.mjs`, 32 test mới, trường `Type:` trong
`write-intent`, và một mục 100 dòng trong `harness.md` nói thẳng rằng ba thứ ở trên **không**
đi theo.

`.cos/0009_.../review.md` finding 1 cũng đi ra trong branch `fix/proof-0009-vacuous-claim`
cùng file này: bản vá cho một claim từng báo xanh trên một câu hỏi chưa bao giờ được hỏi.

## Did the outcome hold

`intent.md` `## Proposed outcome` đòi, trước **2026-10-13**:

> `gh release list --repo baodq97/coscc` trả về **hai dòng**: một prerelease và một release
> `v0.1.0` sau nó. Tag của `v0.1.0` trỏ vào một commit trên `main`, và **mọi commit vào
> `main` kể từ lúc quy ước có hiệu lực** đều đến qua một pull request, từ một branch có tên
> theo quy ước được ghi trong `.claude/`.

**Đạt, ngày 2026-09-22 — sớm hơn hạn 21 ngày.** Đo từng vế:

| Vế của outcome | Đo | Kết quả |
|---|---|---|
| `gh release list` trả hai dòng | `gh release list` | **2**: `v0.1.0` Latest, `v0.1.0-rc.1` Pre-release |
| prerelease đứng trước release | hai timestamp ở trên | rc.1 lúc 09:39:30Z, v0.1.0 lúc 09:40:20Z |
| tag `v0.1.0` trên `main` | `git branch --contains v0.1.0` | có `main` |
| mọi commit sau mốc cổng đều qua PR | `verify_0009.py` C12 | **1/1**, qua PR đã merge |
| branch có tên theo quy ước trong `.claude/` | `cos.mjs check-branch` | `feat/branch-and-release-conventions`, exit 0 |

Hôm intent được viết: **0** release, **0** tag, **0** pull request, **137** commit đều vào
thẳng `main`. Hôm nay: 2 release, 2 tag, 2 pull request, và `main` không nhận được một
`git push` trực tiếp nào nữa — thử thật, bị từ chối với `GH013`.

**Một vế đạt theo cách khác với dự định.** Mốc "kể từ lúc quy ước có hiệu lực" được viết kèm
một phần miễn trừ cho `intent.md`, `spec.md`, `plan.md`. Phần miễn trừ **không cần dùng**:
ba file đó cũng đi qua pull request, vì `main` chưa bao giờ được push trước khi branch cắt
ra. Outcome đạt **mạnh hơn** chữ của nó — `review.md` finding 2, và nguyên nhân là một tai
nạn thứ tự push chứ không phải thiết kế.

**`verify_0009.py`: exit 0, 13/13 claim**, 2026-09-22. Ba trong số đó hỏi GitHub, nên trên
một máy không mạng nó trả exit 2 chứ không phải 0 — bằng chứng này yếu hơn `verify_0008.py`
ở đúng chỗ đó.

## How it is watched

```
uv run python scripts/verify_0009.py     # 13 claim; 0 đạt, 1 hỏng, 2 môi trường
node .claude/scripts/cos.mjs check-version
npm test                                 # 55 node + 256 python
```

`verify_0009.py` là thứ duy nhất đo được cả ba chân cùng lúc, vì hai trong ba không nằm
trong repo. Cụ thể nó sẽ đỏ nếu: ai đó bật lại `allow_merge_commit`, ruleset bị tắt hoặc mất
một rule, `strict` bị gỡ khỏi `required_status_checks`, năm chỗ khai version lệch nhau, hoặc
một commit vào `main` không qua pull request.

**Chạy nó sau mỗi lần đụng vào cài đặt repo trên giao diện web.** Đó là con đường duy nhất
thay đổi được hai chân ngoài file mà không để lại commit nào.

CI chạy `check-version` và `npm test` trên mỗi pull request. **Không job nào là cổng** theo
nghĩa chặn merge vì nội dung — `required_status_checks` bắt cả hai phải xanh, nhưng chúng đo
trên một bản vá Python khác máy đã đo mọi proof trong `scripts/` (`spec.md` C3), nên badge
xanh không thay `npm test` cục bộ.

**Không có gì tự chạy bất kỳ thứ nào ở trên.** Giống mọi thứ khác trong repo này, việc theo
dõi là một lệnh ai đó gõ.

## What to do if it breaks

**Cổng chặn nhầm và cần vào `main` gấp.** Tắt ruleset thay vì xoá:

```
gh api -X PUT repos/baodq97/coscc/rulesets/23815312 -f enforcement=disabled
```

Bật lại bằng `-f enforcement=active`. Xoá ruleset thì mất luôn cấu hình năm rule và phải
dựng lại từ `.cos/0009_branch-and-release-conventions/ship.md` này.

**`required_status_checks` khoá mọi pull request** — triệu chứng: `mergeStateStatus` là
`BLOCKED` trong khi `gh pr checks` xanh hết. Nghĩa là một context không khớp tên job nào và
nằm `pending` vĩnh viễn (`plan.md` Risk 9). Sửa bằng cách `PUT` lại ruleset với đúng tên lấy
từ `gh pr checks --json name`.

**Quy ước sai chứ không phải cổng sai.** Commit cần revert là **`82d599d`** — toàn bộ unit
nằm trong một commit, đó là cái giá và cũng là cái tiện của squash. Revert nó bỏ đi cả bốn
lệnh, hai workflow, mục harness và bump version; ruleset với setting repo thì **không** bị
revert theo, phải tắt tay bằng lệnh ở trên.

**Một type bị tập đóng chặn** (`spec.md` C7): sửa `BRANCH_TYPES` trong
`.claude/scripts/cos.mjs`, thêm dòng vào `harness.md` và `write-intent`, và mở một pull
request — không có đường tắt, đó là chủ ý.

**Ba SHA action cũ đi.** Không gì nhắc nâng. Nâng bằng cách phân giải tag mới qua
`gh api repos/<owner>/<repo>/git/ref/tags/<tag>` và thay SHA; nhớ dereference tag có chú
thích, `astral-sh/setup-uv` là loại đó.
