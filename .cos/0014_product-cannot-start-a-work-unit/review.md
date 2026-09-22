# Review: one function decides where a unit lives, then git, then the two routes
PR: pr.md. Author: Bao Do. Concluded by: agent — Claude Opus 5, cùng agent đã viết mã. Status: accepted.

**Không có tách vai ở đây, và file này không giả vờ có.** Người viết mã và người viết
review là một. Ô này là chỗ để ghi lại một kết luận, không phải một người phê duyệt. Bốn
mục dưới là thứ đọc lại code đã merge tìm ra, không phải thứ ai khác chỉ ra.

## Findings

**1. `coscc/service.py:441` + `coscc/gitops.py:209` — nhánh được cắt từ `main` **cũ**, và
không gì trong sáu bước làm nó mới.** Mức: **trung bình**, gặp thật trên workspace sống.

`create_branch` chạy `git switch -c <name> main`, dùng `main` **local**. `gitops.pull` tồn
tại nhưng là một route riêng người dùng phải tự bấm (`coscc/service.py:223`), và
`start_branch` không gọi nó. Proof không thấy lỗi này vì nó clone mới mỗi lần, nên `main`
luôn đúng. Một workspace đã nằm đó vài ngày thì không: unit mới cắt từ trunk cũ, và PR mở
ra trên một base nó chưa từng thấy. `.claude/CLAUDE.md` nói *"rebased onto the latest
`main` first"* — sản phẩm chưa làm phần "latest".

Chưa sửa trong unit này. Sửa đúng chỗ là `start_branch` fetch trước khi cắt, và điều đó
chạm vào cùng cái guard `live_in` mà route `pull` phải tôn trọng — đủ lớn để là unit riêng,
không phải một dòng nhét vào lúc đang đóng sổ.

**2. `coscc/policy.py:244` — thông báo lỗi nói sai chỗ hỏng.** Mức: **thấp**.

Vòng lặp resolve hai gốc, và khi `unit_dir` là cái resolve hỏng, nó vẫn trả
`"the workspace path could not be resolved"`. Người đọc sẽ đi kiểm workspace, mà workspace
không sao. Không ảnh hưởng an toàn — phía từ chối vẫn từ chối — chỉ tốn thời gian của người
duy nhất có thể sửa nó, đúng thứ `0014` vừa bỏ ba commit để chống ở chỗ khác.

**3. `coscc/journal.py:372` → `coscc/api.py:236` — nội dung câu trả lời đã trả tiền đọc
được không cần đăng nhập.** Mức: **trung bình**, có chủ ý, đã ghi, chưa sửa.

`/api/timeline` nay trả `detail`, và `detail` của một bước hỏng chứa tối đa 2000 ký tự
session đã nói. App không có login trên bất kỳ route nào và mặc định bind `0.0.0.0`
(`0011`, cố ý). Đã ghi vào `.claude/rules/coscc-app.md` `## Hazards` trong `28d7dd3`. Đánh
đổi là có thật và tôi chọn phía chẩn đoán được: một thất bại không ai đọc được lý do là
thất bại sẽ lặp lại.

**4. `coscc/units.py:90` — store không có git, nên artifact nay **không có lịch sử nội
dung** ở đâu cả.** Mức: **trung bình**, hệ quả trực tiếp của R2 và không được nêu ở
`spec.md`.

Trước `0014`, artifact nằm trong cây repo và mỗi cái là một commit — `.claude/CLAUDE.md`
bước 5 viết đúng như thế. Nay chúng nằm dưới `data_dir/units/<slot>/.cos/`, và không có gì
version hoá thư mục đó: ghi đè một `spec.md` là mất bản cũ vĩnh viễn. `0013` dựng
`transitions` vì status bị huỷ khi file bị viết lại; log đó vẫn ghi **trạng thái** đã đổi,
nhưng **nội dung** thì nay không còn bản nào. Đây là một bước lùi so với trước unit này, và
nó đổi lấy điều người khởi xướng yêu cầu — không có gì của coscc trong repo cả team dùng.
Ghi vào `impl.md` `## What is still open` là việc của unit sau.

## What was not reviewed

- **Ô `detail` chưa ai nhìn thấy có dữ liệu.** Bundle đã dựng lại cho `127.0.0.1:8791`
  (fingerprint ở `.web/build/client/.coscc-build.json`), và `scripts/verify_0003.py` chạy
  trình duyệt thật trên bundle đó: **exit 0**, năm claim, gồm cả claim rằng trang vẫn
  render khi cảnh dữ liệu hỏng. Vậy `rx.cond` mới không làm vỡ trang. Nhưng `~/.cos/cos.db`
  không có dòng `end` nào, nên **không có run hỏng thật nào để ô đó hiện ra** — hình dạng
  của nó khi có chữ trong đó chưa ai thấy, và thấy được thì phải tiêu tiền.
- **Chính 479 test đó.** Chúng xanh; không ai đọc lại chúng để hỏi chúng có đo đúng thứ
  chúng nói không. `0013` review tìm ra hai defect nằm **trong** proof chứ không trong sản
  phẩm — cùng rủi ro, lần này không kiểm.
- **Workspace thứ hai.** Mọi thứ đo trên một workspace. `units.slot()` phân biệt hai
  workspace trùng basename bằng digest, có test, nhưng chưa ai chạy hai workspace thật song
  song.
- **Đường hỏng của `verify_0014.py`.** Nó exit 0 một lần và exit 1 một lần. Các nhánh exit 2
  (không `gh`, không `node`, không service) chưa lần nào chạy.

## Verdict

**Accepted.** Outcome đã đo được và đạt: một unit đi từ chưa tồn tại tới pull request đã
merge, chỉ bằng HTTP, **6/6**, `exit 0`, $3.2836.

Bốn finding trên không chặn: ba cái là nợ đã ghi, một cái (mục 2) là câu chữ. Finding 1 là
cái đáng làm trước nhất ở unit sau, vì nó hỏng **im lặng** — nhánh vẫn cắt, PR vẫn mở, chỉ
là base sai.

Verdict này do agent tự cấp cho việc của chính nó. Không ai khác đã đọc mã này.
