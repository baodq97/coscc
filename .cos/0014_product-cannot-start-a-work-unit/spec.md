# Spec: the units live in the product, and the repo only ever receives code
Intent: intent.md. Author: Bao Do. Status: accepted.

## Skip assessment

Năm tiêu chí, phán từng cái: **(1) sai** — hơn hai file. **(2) sai** — thêm route và đổi
chỗ artifact nằm. **(3) đúng** — không thêm dependency. **(4) sai** — thêm hành vi git mà
`intent.md` chỉ nêu tên. **(5) sai** — app tự chạy `git` là bề mặt mới.
Bốn trên năm hỏng, nên viết spec. Tiêu chí ép mạnh nhất là **(5)**.

## Requirements

Mọi requirement truy về outcome duy nhất: một unit đi từ chưa tồn tại tới pull request đã
merge, **chỉ bằng HTTP API**, đạt 6/6 trên bảng sáu bước của `intent.md`.

**R1 — Tạo được unit qua HTTP.** `POST /api/units` nhận `cwd` và `slug`, trả về tên unit.
Số và việc kiểm slug lấy từ `cos.mjs new-path`, không phải một bản Python thứ hai
(`intent.md` ràng buộc 4). Kiểm bằng: gọi với một slug hợp lệ thì unit hiện trên
`GET /api/board`; gọi với `Slug_Sai` thì trả 400 kèm đúng lời `cos.mjs` nói.

**R2 — Artifact của unit không nằm trong cây của workspace.** Chúng nằm dưới data root của
sản phẩm. `0013` `intent.md` chốt điều này ngày 2026-09-22 bằng lời người khởi xướng, và
unit này là lần đầu quyết định ấy phải được *thi hành* thay vì chỉ được ghi. Kiểm bằng: sau
khi chạy hết mọi stage, `git status --porcelain` trong workspace **không** có dòng nào dưới
`.cos/`, và thư mục `.cos/` không được tạo ra ở đó.

**R3 — Có đúng một hàm trả lời "unit của workspace này nằm ở đâu".** Hôm nay câu hỏi đó
được trả lời độc lập ở ba chỗ: `coscc/board.py:95`, `coscc/runner.py:49-54`,
`coscc/service.py:721`. Ba công thức giống nhau ở ba chỗ chính là hình dạng lỗi mà `0012`
tốn một unit để sửa (`coscc/harness.py:45-48`). Kiểm bằng: một test đổi data root và thấy
cả ba đường đi theo.

**R4 — Cắt được nhánh qua HTTP.** Tên nhánh lấy từ `cos.mjs unit-branch`, tức đọc `Type:`
trong `intent.md` — nên route phải từ chối khi `intent.md` chưa có, và nói ra điều đó. Kiểm
bằng: gọi trước khi có intent thì 400; gọi sau thì `git branch --show-current` trong
workspace trả đúng `<type>/<slug>`.

**R5 — Năng lực git của *app* bị chặn ở bốn điều, và nó không đi qua bảng grant.**
`coscc/policy.py:1-20` nói bảng grant là thứ duy nhất nói một *session* được làm gì; app tự
chạy git nằm ngoài bảng đó, nên giới hạn phải ở chỗ khác và phải kiểm được. App chỉ được:
tạo nhánh, trong một workspace đã đăng ký, với tên do `cos.mjs` sinh ra. App **không** được
`push`, **không** `merge`, **không** commit, và **không** đụng `main`. Kiểm bằng: một test
cho mỗi điều cấm.

**R6 — Mỗi lần chạy stage ghi một chuyển trạng thái vào log của `0013`.** Đây là lần đầu
bảng `transitions` có dòng do việc đang diễn ra sinh ra, chứ không phải nhập lại từ git —
đúng chỗ `0013` `ship.md` `## What this ships that the next unit will be written from` nói
vòng lặp quay lại. Kiểm bằng: sau khi chạy một stage, `GET /api/unit-history` trả về một
chuyển trạng thái có `actor` và `session` **không phải** `unknown`.

**R7 — PR và merge do session của stage `pr` làm, không phải do một route mới.**
`coscc/policy.py` đã cấp `git` và `gh` cho `("pr", "autonomous")` kèm cảnh báo. Dùng lại nó
giữ nguyên bề mặt của app; thêm một route merge là mở một bề mặt mới trên một app không có
xác thực. Kiểm bằng: proof không gọi route nào tên là merge.

**R8 — Làm được từ trang, không chỉ bằng `curl`.** "Người dùng thông thường" bấm nút. Tối
thiểu: một chỗ nhập slug và tạo unit, và một chỗ cắt nhánh, trên màn Board. Kiểm bằng
`scripts/verify_0003.py`-style: trình duyệt thật mở trang và thấy control đó.

**R9 — Proof không được làm thay sản phẩm.** `scripts/verify_0014.py` chỉ gọi HTTP. Nó được
gọi `git` **chỉ để đọc** (`status`, `branch --show-current`, `log`), và không bao giờ để
tạo, commit hay chuyển nhánh. `intent.md` ràng buộc 1. Chạy trên một repo dùng một lần
(ràng buộc 2), không phải `cos-baodo`.

## Design

**Một quyết định quyết cả phần còn lại: unit sống trong sản phẩm, repo chỉ nhận mã.**

Hôm nay `.cos/` nằm trong cây của workspace, và `cos.mjs --root <dir>` đã được thiết kế để
đọc `.cos/` ở bất cứ đâu — `.claude/CLAUDE.md` nói `--root` tồn tại để app đọc `.cos/` của
repo khác bằng luật của repo này. Unit này dùng đúng cơ chế ấy, chỉ đổi `<dir>`: từ cây của
workspace sang **`<data_dir>/units/<workspace>/`** của sản phẩm.

Hệ quả, và đó là toàn bộ lý do chọn thế:

- Repo đích nhận **nhánh**, **commit mã do stage `impl` tạo**, và **mô tả PR từ `pr.md`**.
  Không file nào của coscc vào cây của nó. `0013` được thi hành thay vì chỉ được ghi.
- **"Mỗi artifact một commit" tan biến chứ không bị vi phạm.** Artifact không còn ở trong
  repo, nên không có gì để commit. Thứ thay thế nó là log chuyển trạng thái của `0013` —
  mỗi artifact vẫn có một bản ghi riêng, chỉ là trong DB chứ không trong git. Bước 5 của
  bảng sáu bước vì thế đọc lại thành: chạy stage, và mỗi stage để lại một chuyển trạng thái.

**Bốn thành phần, và ranh giới giữa chúng.**

1. **`units_root(workspace)` — một hàm, một câu trả lời.** R3. Ba chỗ hiện tự tính lấy đều
   gọi nó. Đây là chỗ duy nhất biết artifact nằm đâu.
2. **Cấp phát — vỏ mỏng quanh `cos.mjs new-path`.** Chạy script, lấy đường dẫn nó in ra,
   tạo thư mục. Không có logic đánh số ở Python. Lỗi của script trở thành `Invalid`.
3. **Nhánh — `gitops`, ghi, có giới hạn hẹp.** `gitops` hôm nay chỉ `clone` và `pull`;
   thêm `current_branch` (đọc) và `create_branch` (ghi). `create_branch` nhận tên đã do
   `cos.mjs` sinh và cắt từ `main`. R5 là danh sách những gì nó không làm.
4. **Ghi chuyển trạng thái — `history`, gọi từ `service.run_step`.** R6. `actor` là stage
   đang chạy, `session` là id session thật của bước đó, `source` là `run:<journal id>`.

**Dữ liệu qua ranh giới:** route → service → (units_root | cos.mjs | gitops | history).
Không thành phần nào ở dưới gọi ngược lên, và không route nào quyết định gì
(`.cos/0001.../spec.md` R10).

## Out of scope

- **Chuyển 13 unit của repo này sang store mới.** `0013` `intent.md` đã nói `.cos/` ở đây ở
  lại như **hồ sơ lịch sử đóng băng**. Board đọc store mới cho công việc mới; `.cos/` cũ
  vẫn đọc được bằng `cos.mjs --root` trỏ tay. Một unit sau quyết có nhập nó vào hay không.
- **`git worktree` cho mỗi ticket.** `intent.md` OQ4. Nhiều unit song song cần nó; unit này
  làm một unit chạy trọn, chưa làm nhiều unit chạy cùng lúc.
- **Agent tự bấm "accept".** `intent.md` OQ2. Ở đây `accepted` vẫn do session tự viết vào
  artifact như hôm nay; không thêm nút duyệt, và không giả vờ có người duyệt.
- **Xác thực.** `spec.md` C5 dưới đây nói bề mặt vừa lớn ra; nó không được thu lại ở đây.
- **Xoá `cos.mjs` hoặc bỏ `node`.** Unit này phụ thuộc nhiều hơn vào nó, không ít hơn.

## Concerns

**C1 — Board của repo này sẽ trống sau thay đổi, và đó là một bất ngờ chứ không phải một
lỗi.** Khi `units_root` trỏ vào data root, workspace `coscc` không còn hiện 13 unit dưới
`.cos/` của nó. Đúng theo thiết kế và đúng theo `0013`, nhưng người mở Board sẽ thấy nó rỗng
và nghĩ là hỏng. Phải nói ra trên trang, không chỉ ở đây.

**C2 — Session của `impl` sẽ bị chặn khi tự ghi artifact của nó.** `coscc/policy.py:206-216`
từ chối mọi `Write` ra ngoài workspace, và artifact vừa chuyển ra ngoài workspace. Với
`("impl","autonomous")` và `("pr","autonomous")`, `app_writes_artifact=False` — session tự
ghi. Vậy ranh giới ghi phải mở thêm **đúng thư mục unit của chính bước đó**, không hơn. Đây
là nới một guard an ninh, nên nó phải là một dòng đọc được và có test cho cả hai phía.

**C3 — App tự chạy `git` là năng lực mới, và bảng grant không nói gì về nó.**
`coscc/policy.py` nói về *session*. Một route cắt nhánh chạy bằng quyền của tiến trình app,
không của session nào. R5 là toàn bộ giới hạn, và nó là **một danh sách viết tay**, không
phải một cơ chế — cùng loại yếu như `check_command` mà `coscc/policy.py:160-170` đã tự nhận.
Ai đọc lại nên soi đúng chỗ này trước.

**C4 — Merge đi qua `gh` của máy, tức chạm mọi repo mà tài khoản đó chạm được.**
`coscc/policy.py:96-99` đã ghi cảnh báo này từ `0005`. Khác biệt ở đây: trước nó là một nút
người ta có thể không bấm; sau unit này nó nằm trên **đường đi chính** của vòng lặp. Cảnh
báo không được gỡ, và `verify_0014` chạy trên repo dùng một lần chính vì điều này.

**C5 — Tạo unit và cắt nhánh giờ gọi được từ mạng.** App không có xác thực trên route nào và
mặc định bind `0.0.0.0` (`docs/install.md`). `0013` `spec.md` C6 đã nói một route *đọc* làm
bề mặt lớn lên; đây là hai route **ghi**, một trong đó ghi vào git của người khác. Quyết
định giữ `0.0.0.0` là của người khởi xướng ngày 2026-09-22 và nên được nhắc lại mỗi lần bề
mặt lớn lên — lần này nó lớn theo hướng khác chất.

**C6 — Proof chạy thật sẽ tiêu quota thật.** Tám stage, trong đó `impl` có trần $5 và `pr`
có $3 (`coscc/policy.py:101-115`). `.claude/rules/coscc-app.md` nói thẳng: thứ gì nói chuyện
với app thì không thuộc về một vòng lặp không người trông. Proof này phải chạy có chủ ý, và
`## Proof` của plan phải nói nó tốn gì.

## Open questions

1. **`intent.md` viết bởi ai, lần đầu?** Stage `intent` chạy được, nhưng nó cần một câu đề
   bài. Hôm nay `run_step` không nhận văn bản từ người dùng; prompt dựng từ artifact có sẵn
   (`coscc/runner.py:90-150`). Một unit mới thì chưa có gì. Hoặc `POST /api/units` nhận thêm
   một đoạn mô tả, hoặc stage `intent` phải hỏi. **Câu này chặn outcome** và plan phải chốt.
2. **`0013` ship.md nói log cần đường ghi lúc việc đang diễn ra; R6 làm một nửa.** Nó ghi
   chuyển trạng thái do app gây ra. Người sửa tay artifact trong store thì vẫn không ai ghi.
3. **Merge rồi thì `ship` đo gì?** `write-ship` đòi đối chiếu outcome. Với unit chạy bằng
   máy, ai chạy proof của unit đó, và chạy ở đâu.
4. **Nhiều workspace cùng một slug.** `units_root` tách theo workspace, nên hai workspace có
   thể cùng có `0001_x`. `journal` đã dùng đường dẫn đã resolve làm khoá; store phải dùng
   cùng một quy ước chứ không phát minh cái thứ hai.
