# PR: Delete harness.md, scope the app to a rule, and write down how to cut a branch
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: accepted.

## Where

https://github.com/baodq97/coscc/pull/4

Branch `fix/harness-restates-rules-and-omits-steps`, base `main`. Tên branch do
`cos.mjs unit-branch` sinh ra từ `Type: fix` ở header `intent.md`, không gõ tay.

`git rev-list --count HEAD..origin/main` trả **0** ở lần đo trước khi push, nên
`gh pr update-branch --rebase` không cần chạy. Đây là lần thứ hai quy tắc "rebase lên `main`
mới nhất" không được thử — `review.md` của `0009` đã ghi lần thứ nhất.

## Scope of the diff

`git diff --shortstat main...HEAD -- ':!.cos'`: **13 file, +287 −496**. Kể cả `.cos/`:
25 file, +948 −504.

| File | |
|---|---|
| `.claude/harness.md` | **−295**, xoá |
| `.claude/CLAUDE.md` | 197 → **115** |
| `.claude/rules/coscc-app.md` | **+85**, mới |
| `.claude/scripts/cos.mjs` | +11 |
| `.claude/scripts/cos.test.mjs` | +47 −5, năm test mới |
| `scripts/verify_0008.py` | +32 −3 |
| `scripts/verify_0009.py` | +1 −1 |
| Bốn `SKILL.md` | +15 −14 cộng lại |
| Tám `.cos/*/intent.md` | +8 −8 |
| `.cos/RENAMES.md` | +30 |
| `README.md`, `.github/workflows/pr.yml` | +7 −6 cộng lại |

## What a reviewer should look at first

**`scripts/verify_0008.py`** — departure 3 của `impl.md`, và là chỗ duy nhất trong diff mang
hình dạng của việc nới bằng chứng.

File đó **đã đỏ sẵn trên `main`** trước khi branch này tồn tại: 12 trên 14, và đỏ từ lúc
`0009` ship. Ba claim đo `BASE..HEAD` hoặc `BASE..cây làm việc`, tức đầu trên chạy theo thời
gian, nên chúng vô tình là tuyên bố về mọi commit tương lai của repo. `0009` làm chúng đỏ
chỉ bằng việc tồn tại — mười artifact và sáu dòng lockfile.

Sửa là ghim đầu trên vào `08b863d`, commit `0008` kết thúc. Điều được tuyên bố không đổi;
điều được đo thôi di chuyển. C1 cố ý không ghim.

Phân biệt duy nhất giữa việc này và việc nới một claim cho tới khi nó xanh là: diff nói ra,
`impl.md` nói ra, và đoạn này nói ra. Không có gì trong repo bắt được khác biệt đó. Nếu
người đọc kết luận đây là nới lỏng, chỗ để đảo là `TIP` — một hằng, một dòng.

Hai chỗ nhỏ hơn, theo thứ tự:

- **Bốn `SKILL.md` bị gỡ con trỏ** theo yêu cầu giữa chừng *"trong skills không mention bất
  kỳ item khác nào"*. Va vào spec R4 ở chữ. Câu nói giữ nguyên; kiểm bằng cách đọc diff chứ
  không lệnh nào kiểm được.
- **`.claude/rules/coscc-app.md` chưa ai chứng minh là nạp được.** Frontmatter khớp tài liệu
  và file parse được; không phép đo nào trong repo cho thấy Claude Code đọc nó. Hỏng thì im
  lặng.
