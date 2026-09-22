# Review: Three tiers into two, and a proof that had been red for a day
PR: pr.md. Author: Bao Do. Concluded by: **agent — the same session that wrote the code.**
Status: accepted.

**Không ai đọc thay.** Kết luận này do chính phiên đã viết code đưa ra. Dòng
`Concluded by:` tồn tại để người đọc thấy cái ghế trống chứ không phải đoán ra nó.
`required_approving_review_count` của ruleset là **0**, đúng vì không có ai.

PR #4 merge thành `93175b5`. Đây là unit thứ hai đi đủ tám stage.

## Findings

**1. `verify_0008` đã đỏ trên `main` một ngày mà không ai biết — nghiêm trọng, đã sửa.**
Đo trên `main` tại `31ea2fc`, trước khi branch này tồn tại: **12 trên 14 claim**. C2 (qua
`kept()`), C11 và C12 đo `BASE..HEAD` hoặc `BASE..cây làm việc` — đầu trên chạy theo thời
gian. Chúng được viết ngày `HEAD` *là* `08b863d`, nên vô tình thành tuyên bố về mọi commit
tương lai của repo, và `0009` làm chúng đỏ chỉ bằng việc tồn tại.

Đây là **lớp lỗi thứ ba** repo gặp, sau "claim đỏ sai" và "claim xanh sai" của `0009`:
một bằng chứng **đúng lúc viết và hỏng theo thời gian**, không do ai sửa gì. Không phép đo
nào trong repo bắt được nó — `npm test` không chạy proof, và CI cũng không.

Sửa: ghim `TIP = 08b863d`. C1 cố ý không ghim. Sau khi sửa: 14/14.

**2. Sửa ở finding 1 không phân biệt được với nới bằng chứng — trung bình, còn mở.**
Nói thẳng vì nó đúng: sửa một file bằng chứng của unit đã đóng cho tới khi nó xanh lại là
đúng hình dạng của việc nới claim. Khác biệt duy nhất là diff, `impl.md` departure 3, `pr.md`
và đoạn này đều nói ra. **Không có gì trong repo bắt được khác biệt đó** — cùng kết luận
`review.md` của `0009` đã rút ra về một lớp lỗi khác.

Chỗ để đảo nếu người khởi xướng thấy đây là nới lỏng: `TIP`, một hằng, một dòng.

**3. `0009` bỏ sót đúng thứ nó tự ghi là đã bỏ sót — thấp, đã sửa.**
`review.md` của `0009`, mục `## What was not reviewed`: *"Bảy proof cũ không chạy lại."*
Finding 1 chính là thứ câu đó che. Ghi một lần ở đây vì nó là bằng chứng rằng một mục
"chưa kiểm" viết ra rồi **vẫn** không ai quay lại kiểm.

**4. Plan đoán sai một type trong tám — thấp, đã sửa.**
`0004` là `fix`, không phải `feat`. Risk 2 của plan dự đoán đúng cơ chế hỏng (không lệnh nào
biết một unit "đáng lẽ" là gì) và cách duy nhất nó nêu — đọc `## Problem` trước khi gõ — là
cách nó bị bắt. Bảy type còn lại đúng.

**5. Một chỗ trùng được tạo ra rồi gỡ trong cùng unit — thấp, đã sửa.**
Thủ tục cắt branch sáu bước nằm ở **cả** `CLAUDE.md` **và** `write-intent` sau bước 9, trong
khi spec R5 đòi đúng một file. Mệnh đề 4 của `## Proof` bắt được: nó đếm **3** file thay vì
1. Đáng chú ý là phép đếm đó chỉ bắt được vì có một con số cố định; một mệnh đề dạng "có mặt"
đã xanh cả hai lần.

**6. Mệnh đề 4 của `## Proof` từng đo máy đang chạy — thấp, đã sửa.**
`grep -rliE` đi theo hệ tệp nên bắt `.claude/settings.local.json`, file **gitignore**. Hai
trong ba file khớp không có trong repo. Đổi sang `git grep`. Một proof đọc file không theo
dõi là một proof cho kết quả khác nhau trên hai máy.

**7. Push một commit vào PR đặt lại required check — thấp, thủ tục, chưa ghi.**
`gh pr merge` bị ruleset từ chối với *"2 of 2 required status checks are expected"* ngay sau
khi commit `pr.md` được push. Các check chạy lại từ đầu. Không phải lỗi, nhưng nó là một bước
thật trong thủ tục merge mà `CLAUDE.md` không nói. Thêm một câu ở bước 6, trong branch này.

## What was not reviewed

- **`.claude/rules/coscc-app.md` có nạp không.** Frontmatter khớp tài liệu
  (`code.claude.com/docs/en/memory`, đọc 2026-09-22) và file parse được. Không phép đo nào
  cho thấy Claude Code đọc nó khi chạm `coscc/`. Risk 3 của plan, còn nguyên, và hỏng thì im
  lặng. **Đây là chỗ hở lớn nhất còn lại của unit này** — 85 dòng kiến thức app có thể đã
  biến mất khỏi mọi phiên thay vì nạp có điều kiện, và triệu chứng chỉ xuất hiện ở một phiên
  khác sau này.
- **Năm proof `verify_0001`–`verify_0006`.** Cần browser, port rảnh, hoặc tiêu quota. Finding
  1 vừa chứng minh một proof có thể đỏ nhiều ngày mà không ai biết, nên câu này nặng hơn lời
  rào thông thường.
- **295 dòng bị xoá** chỉ đi qua hai bước đọc của cùng phiên đã viết phần lớn chúng. Risk 1
  của plan. Hai bảng ở `impl.md` là bản ghi duy nhất.
- **Quy tắc "rebase lên `main` mới nhất"** vẫn chưa chạy lần nào — `HEAD..origin/main` trả
  **0** ở cả hai lần đo. Lần thứ hai liên tiếp.
- **Nhánh từ chối của `release.yml`** và một job `branch-name` đỏ: vẫn chưa chạy trên runner.
  Không đổi so với `0009`.
- **Bốn `SKILL.md` bị gỡ con trỏ** — không lệnh nào kiểm được rằng câu nói còn nguyên nghĩa
  sau khi bỏ chỗ nó trỏ tới. Đọc bằng mắt.
- **Prose tiếng Việt trong `.cos/`** — như mọi unit trước.

## Verdict

**Accepted**, với một dè dặt khác `0009`.

`0009` kết thúc với lời cảnh báo rằng một claim xanh không đủ để tin. `0010` thêm một bậc:
một claim xanh **hôm qua** cũng không đủ, vì phép đo có thể mục theo thời gian mà không ai
chạm vào nó. Repo bây giờ có ba lớp lỗi đã gặp thật, và không lớp nào có lệnh canh.

Và unit này vừa xoá 295 dòng văn xuôi với lập luận rằng văn xuôi không cưỡng chế được gì —
rồi đặt ba thứ quan trọng nhất của chính nó vào văn xuôi: bảng "bỏ cái gì" ở `impl.md`,
finding 2 ở đây, và thủ tục cắt branch ở `CLAUDE.md`. Lập luận đó đúng; nó chỉ không áp dụng
cho thứ không có máy nào kiểm được, và đó chính là thứ còn lại sau khi nén.
