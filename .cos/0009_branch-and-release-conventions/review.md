# Review: A branch grammar, a tag grammar, and the three places that check them
PR: pr.md. Author: Bao Do. Concluded by: **agent — the same session that wrote the code.**
Status: accepted.

**Không ai đọc thay.** Kết luận này do chính phiên đã viết code đưa ra, nên nó không phải
một lần duyệt độc lập và không được đọc như vậy. `.claude/skills/write-review/SKILL.md` gọi
ô `review` xanh trong tình huống này là "a green cell over an empty chair"; dòng
`Concluded by:` ở trên tồn tại để người đọc thấy cái ghế trống đó chứ không đoán.

Điều mới ở `0009`: pull request **thật sự tồn tại** —
https://github.com/baodq97/coscc/pull/1 — nên lần đầu có một chỗ để người thứ hai đọc, nếu
có người thứ hai. `required_approving_review_count` của ruleset là **0**, đúng vì không có
ai. Đó là quyết định, không phải sơ suất.

## Findings

**1. C12 đạt rỗng và không ai bắt được — nghiêm trọng, đã sửa.**
`scripts/verify_0009.py:462` (bản cũ). Claim in ra *"all **0** commits on main since ...
came through a merged PR"* và **PASS**, ngay sau khi PR #1 vừa merge. Nguyên nhân:
`since={since}` nội suy thẳng vào query string, trong khi mốc thời gian của ruleset mang
offset `+07:00` và dấu `+` trong query string giải mã thành dấu cách. GitHub nhận một mốc
hỏng và trả về rỗng; `all([])` là `True`.

Đây là claim **duy nhất** đo nguyên văn outcome của `intent.md`, và nó đã báo xanh cho một
câu hỏi chưa bao giờ được hỏi. Sửa: dùng `-f since=...` để `gh` tự mã hoá, **và** bắt đỏ khi
số commit bằng 0 — cổng bật trước lần merge đầu tiên nên luôn phải có ít nhất một. Sau khi
sửa: *"all 1 commits ... came through a merged PR"*. Sửa ở commit của branch
`fix/proof-0009-vacuous-claim`.

**2. `intent.md` `## Proposed outcome` mô tả sai chuyện đã xảy ra — trung bình, đã sửa.**
`.cos/0009_branch-and-release-conventions/intent.md:68-73`. Nó viết ba artifact của unit này
"vào thẳng `main` và được miễn". Chúng không. `main` được commit **cục bộ** và không bao giờ
được push, nên khi branch cắt ra và PR mở với base `origin/main`, cả ba nằm trong pull
request. `origin/main` đi từ `89e7ed1` thẳng tới `82d599d`. Kết quả tốt hơn dự định — không
gì vòng qua cổng — nhưng vì một tai nạn thứ tự push, không vì thiết kế. Ghi lại nguyên nhân
thật ở cùng file.

**3. `spec.md` C9 nói quá chi phí của squash — thấp, đã sửa.**
`.cos/0009_branch-and-release-conventions/spec.md` C9 viết lịch sử từng bước "chỉ còn trong
pull request". Đo: `squash_merge_commit_message` là `COMMIT_MESSAGES`, nên `82d599d` mang cả
**13 thông điệp, 235 dòng**, đọc được bằng `git log` không cần mạng. Thứ mất là các **cây
trung gian** — không `git show` một bước, không `git bisect` giữa chúng. Đúng là "mất trạng
thái", không phải "mất văn bản".

**4. Ruleset có năm rule, `plan.md` kể ba — thấp, cố ý.**
`deletion` và `non_fast_forward` thêm lúc viết payload. Không có chúng, `main` vẫn xoá được
và vẫn force-push được: cổng chặn lối đi thẳng và để ngỏ hai lối vòng. Ghi ở `impl.md`
departure 11.

**5. Quy tắc "rebase lên `main` mới nhất" chưa bao giờ được thử — thấp, còn mở.**
`spec.md` R15 dựng `required_status_checks` ở chế độ `strict`, và C10 đọc được cờ đó trong
API. Nhưng branch chưa một lần nào đi sau `main` (`git rev-list --count HEAD..origin/main`
trả **0** ở mọi lần đo), nên `gh pr update-branch --rebase` **không chạy lần nào**. Cờ được
kiểm; hành vi thì chưa.

**6. Hai file rỗng lọt vào một commit — thấp, đã sửa.**
`25-34` và `123-136`, do một thông điệp commit truyền bằng `-m` có dấu nháy đơn làm bash
chạy phần còn lại như lệnh. Gỡ ở commit ngay sau. Cùng hình dạng với departure 7 của `0008`,
tức là lần thứ hai `git add -A` quét vào thứ không ai đặt ở đó.

## What was not reviewed

- **`release.yml` chỉ chạy đúng con đường thành công.** Hai lần chạy, cả hai với tag hợp lệ
  trên một cây có version khớp. Nhánh `check-tag` từ chối và nhánh `check-version` lệch
  **chưa bao giờ chạy trên runner** — chúng chỉ được kiểm cục bộ.
- **Không có PR nào mở từ một branch tên sai**, nên job `branch-name` chưa từng đỏ.
  `spec.md` R7 nêu phép kiểm đó; nó không được chạy.
- **Bảy proof cũ** (`verify_0001`–`verify_0008`) không chạy lại. Unit này không chạm
  `coscc/`, nhưng nó bump version ở `pyproject.toml` và sinh lại hai lockfile.
- **Ba SHA action bên thứ ba** được xác minh là phân giải tới commit thật vào 2026-09-22;
  **nội dung** của ba action đó không ai đọc.
- **Prose tiếng Việt trong `.cos/`** — không test nào kiểm được, như mọi unit trước.
- **`verify_0009.py` chạy trên một bản clone mới** — `0008` làm phép này, `0009` thì không.

## Verdict

**Accepted**, với một dè dặt được nói thẳng: bằng chứng của unit này **đã từng báo xanh sai
một lần** ở đúng claim đo outcome, và nó chỉ bị bắt vì con số `0` in ra trông vô lý. Không
gì trong repo bắt được lớp lỗi đó — bốn lần khác trong cùng unit cũng là nó: một file đo một
thứ mà bản thân nó ảnh hưởng tới. Ba trong bốn lần làm claim **đỏ sai** và tự lộ; hai lần
làm claim **đạt sai** và chỉ lộ vì có người nhìn kỹ con số.

Verdict không được nâng cao hơn thế: mọi claim đều xanh, và một trong số chúng đã chứng minh
là xanh không đủ để tin.
