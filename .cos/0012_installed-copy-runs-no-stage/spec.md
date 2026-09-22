# Spec: the app carries the rules it runs, and refuses to run without them
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Mọi requirement dưới đây truy về một outcome duy nhất trong `intent.md`: trên máy không có
checkout, app cho kết quả không phân biệt được với app chạy từ checkout.

**R1 — App tìm được luật của chính nó mà không cần checkout.** Cả `cos.mjs` lẫn 9 skill
(`.claude/skills/*/SKILL.md`, đếm ngày 2026-09-22) phải giải được từ bên trong package đã
cài. Kiểm bằng: trên interpreter của bản cài, hai đường dẫn đó `exists() == True`.

**R2 — Board trên bản cài khớp với gate trong workspace.** `GET /api/board?cwd=<ws>` trả
về tập tên unit và trạng thái từng stage **bằng đúng** thứ
`node .claude/scripts/cos.mjs --root <ws> status --json` in ra trong chính workspace đó.

*So khớp trên tập `(unit, stage, status)`, không phải trên toàn payload.* `intent.md` viết
"bằng đúng thứ ... in ra" là nói quá: `coscc/service.py:276-280` gắn thêm `mode` từ journal
và `coscc/service.py:270-272` gắn thêm cost, hai trường mà `cos.mjs` không biết. Một
requirement đòi payload giống hệt là một requirement không bao giờ xanh được. Đây là chỗ
spec sửa lại intent chứ không phải chỗ intent bị bỏ qua.

**R3 — Prompt của một step mang đủ luật.** `build_prompt` cho stage `spec` trên bản cài trả
≥ 18.000 ký tự và chứa khối `# The rules for this stage`. Hôm nay: 14.313 ký tự, không có
khối đó (`intent.md`, bảng đo).

**R4 — Một step không tìm thấy luật thì phải dừng trước khi tiêu tiền.** Khi luật của stage
không giải được, app từ chối **trước khi tạo session**, và lời từ chối nêu tên stage cùng
đường dẫn đã tìm. Kiểm bằng: 0 request tới SDK, và thông báo chứa đường dẫn.

Đây là requirement duy nhất không nằm sẵn trong outcome của `intent.md` — nó trả lời
`## Open questions` mục 3 của intent. Người khởi xướng chốt ngày 2026-09-22: vá luôn cái im
lặng. Lý do ghi ở `## Concerns` C2, vì nó lật một quyết định đã có chữ trong code.

**R5 — Release từ chối wheel không mang luật.** Một wheel thiếu `cos.mjs` hoặc thiếu skill
phải làm hỏng bước release, không phải làm hỏng máy người cài. Đây là bản tổng quát của
`.github/workflows/release.yml:114-128`, vốn chỉ canh frontend.

**R6 — Chỉ hai thứ app thật sự đọc mới được đóng gói.** `scripts/` và `skills/`. Không
`settings.json`, không `settings.local.json` (`.gitignore` đã cấm commit file này),
không `CLAUDE.md`, không `rules/` — app không đọc chúng, và wheel là thứ công khai.

**R7 — Repo vẫn giữ một bản duy nhất của luật.** Cây đóng gói được **sinh ra**, gitignore,
không bao giờ commit — hệt `coscc/_web/` (`.gitignore:2`). `.claude/CLAUDE.md` viết
`cos.mjs` là "the one place the loop is defined; nothing may hold a second copy of it", và
requirement này là cách duy nhất thoả nó mà vẫn ship được.

**R8 — `node` được khai là prerequisite.** `coscc/board.py:90` spawn `node` mỗi lần đọc
Board. `docs/install.md` mục `## Prerequisites` hiện viết *"You do not need Node, npm or
bun"*, đúng cho việc build frontend và sai cho Board. Phải nói rõ: không cần Node để build,
cần `node` để Board chạy.

**R9 — Có proof chạy được, và hôm nay nó đỏ.** `scripts/verify_0012.py`, theo quy ước exit
của `scripts/verify_0003.py:8-14`: `0` mọi claim đúng, `1` ít nhất một sai, `2` môi trường
không trả lời được (không có bản cài để đo).

## Design

Hình dạng này **đã tồn tại trong repo cho một thứ khác**, nên đây là việc tổng quát hoá,
không phải phát minh. `0011` gặp đúng bài toán với frontend và giải xong:

| | frontend (`0011`) | harness (unit này) |
|---|---|---|
| nguồn trong checkout | `.web/build/client` | `.claude/scripts`, `.claude/skills` |
| bản trong package | `coscc/_web/` | cây đóng gói tương đương |
| chọn bản nào | `coscc/frontend.py:106-113` | cùng một hình |
| gitignore | `.gitignore:2` | thêm một dòng |
| copy lúc release | `release.yml:81-94` | thêm một step |
| chặn wheel thiếu | `release.yml:114-128` | mở rộng |

**Bốn thành phần, và ranh giới giữa chúng:**

1. **Một chỗ duy nhất trả lời "luật nằm đâu".** Hiện có hai công thức `parent.parent` độc
   lập ở `coscc/board.py:31` và `coscc/runner.py:39`, và đó là lý do một lỗi thành hai
   triệu chứng. Sau unit này, cả hai hỏi cùng một nơi. Quy tắc chọn giống
   `coscc/frontend.py:106-113`: **bản đóng gói thắng khi nó có mặt**, ngược lại lùi về
   `.claude/` của checkout. Một wheel không có checkout để lùi về; một checkout không có
   cây đóng gói trừ khi ai đó cố ý tạo.

2. **Board.** Không đổi gì ngoài chỗ lấy đường dẫn. Quyết định ở `coscc/board.py:8-14` —
   không bao giờ chạy `cos.mjs` của workspace — giữ nguyên, và ràng buộc 1 của `intent.md`
   cấm mở lại. Điều unit này làm là khiến câu `coscc/board.py:11-12` tự khai ("the copy that
   ships with the app") trở thành đúng.

3. **Runner.** Hai thay đổi, và chỉ hai. Chỗ lấy đường dẫn; và `skill_for` từ "trả rỗng"
   thành "báo hỏng", đủ sớm để không có session nào được tạo. Nội dung skill, thứ tự stage
   và `policy.GRANTS` không đụng tới — ràng buộc 2 của `intent.md`.

4. **Đường phát hành.** Một bước copy hai thư mục vào package, và một bước đọc lại wheel để
   từ chối nếu chúng không có trong đó. Bước thứ hai là bước thật sự quan trọng: bước copy
   nào cũng có thể quên chạy, và `release.yml:111-113` đã ghi sẵn nhận xét đó cho frontend.

**Dữ liệu đi qua ranh giới** không đổi: Board vẫn là JSON của `cos.mjs` đi vào service rồi
ra HTTP; Runner vẫn là văn bản skill đi vào prompt. Unit này chỉ đổi **nơi hai thứ đó được
lấy về**, cộng một lối từ chối mới khi lấy không được.

## Out of scope

- **Engine Python thay `cos.mjs`.** Hướng người khởi xướng nói ngày 2026-09-22 (Board độc
  lập, tự quản intents, bỏ `node`) là nhiều unit và chưa có unit nào. Nếu nó chạy, nó xoá
  phần đóng gói ở đây. Làm trước vì sản phẩm đã publish đang chết hai tính năng, không phải
  vì hướng kia sai.
- **Bỏ `node` khỏi runtime.** Hệ quả của điều trên. Unit này khai báo `node`, không loại bỏ nó.
- **Ghi vào `included` việc rules có mặt hay không** (`intent.md` OQ3). R4 làm câu hỏi này
  thành thừa: nếu thiếu luật là dừng, thì không tồn tại bản ghi nào của một step đã chạy mà
  thiếu luật. Ghi thêm một trường để mô tả trạng thái không còn xảy ra được là thêm chỗ để
  lệch.
- **Uninstall, macOS, Windows.** `docs/install.md` đã để ngoài phạm vi từ `0011`.
- **Xác thực.** App vẫn không có auth trên route nào; unit này không đổi điều đó.

## Concerns

**C1 — Bước copy sống trong CI, nên một wheel dựng bằng tay vẫn sai.** `uv build` chạy trên
máy cá nhân không gọi `release.yml`, và nó vẫn ra một wheel cài được, thiếu luật, im lặng.
Đây đúng là lỗ hổng `0011` để lại cho frontend và unit này thừa hưởng. Giảm nhẹ: bước kiểm
tra ở R5 phải nằm trong một thứ **chạy được ở cả hai nơi**, không phải chỉ vài dòng `run:`
trong YAML. Không xoá được hẳn lỗ hổng: không gì ép một người phải chạy nó.

**C2 — R4 lật một quyết định đã ghi trong code.** `coscc/runner.py:57-63` viết
*"Missing is not fatal."* Câu đó là một lựa chọn có chủ ý, và unit này đảo nó. Lý do:
`intent.md` đo được rằng hệ quả của "không fatal" là một step tiêu quota thật với prompt
thiếu 4.569 ký tự luật, và bản ghi của nó không phân biệt được với một step chạy đủ. Không
fatal chỉ an toàn khi thiếu luật là chuyện nhỏ; bảng đo nói nó không nhỏ. **Người quyết:
người khởi xướng**, đã chốt 2026-09-22. Ghi ở đây vì lật một quyết định có chữ mà không ghi
là cách nó quay lại lần sau.

**C3 — R7 và R5 kéo ngược nhau, và spec này chọn một bên.** R7 nói repo chỉ giữ một bản, nên
cây đóng gói phải gitignore. R5 nói release phải chặn wheel thiếu nó. Hệ quả: thứ quyết định
một wheel đúng hay sai **không nằm trong repo dưới dạng file có thể đọc bằng mắt** — nó là
một bước dựng. Bên còn lại (commit bản sao vào repo) thì `.claude/CLAUDE.md` cấm thẳng.
Chọn R7. Cái giá: mọi bảo đảm ở đây dựa vào một bước có thể không chạy — xem C1.

**C4 — 9 skill là số đo hôm nay, không phải hằng số.** Một skill thêm vào mai sau không tự
động được đóng gói nếu bước copy liệt kê tên. Bước copy phải copy cả thư mục, và bước chặn
phải kiểm thứ nó biết chắc (ví dụ số skill khác 0 và `cos.mjs` có mặt), chứ không phải một
danh sách chép tay.

**C5 — Proof này đo được packaging, không đo được "máy sạch".** Bản cài trên máy này thoả
điều kiện của R1–R4: `parent.parent` của nó là `site-packages/`, không phải repo, nên
checkout nằm cùng đĩa không rò gì vào. Nhưng **`node` thì có**: máy này có `node` v24.20.0
vì checkout chạy `npm test`, còn máy sạch theo `docs/install.md` thì không. Nghĩa là R8
không thể được chứng minh bởi proof chạy ở đây — nó chỉ được chứng minh bởi một target như
`scripts/verify_0011.py` dùng (`COS_PROOF_TARGET`, một máy qua SSH). Proof phải nói rõ nó
đang đo cái nào, thay vì để người đọc tưởng exit 0 bao luôn R8.

## Open questions

1. **`intent.md` OQ1 — unit này có bị thay thế không?** Giữ nguyên là mở. Một câu trả lời
   "có, và làm ngay" sẽ đổi quyết định *làm gì* chứ không đổi gì trong spec này; spec này
   vẫn là thứ đúng để làm trước.
2. **`intent.md` OQ2 — `node` ở lại không?** Spec này trả lời: **ở lại**, và R8 buộc phải nói
   ra. Nếu engine Python thay `cos.mjs`, R8 trở thành sai và phải bị gỡ, không phải bị quên.
3. **`intent.md` OQ4 — câu chuyện template còn đúng không?** `.claude/CLAUDE.md` mục
   `## Copying this into another repository` nói copy `.claude/` là xong. Sau unit này,
   wheel cũng mang cùng bộ luật, nên có hai đường phân phối. Chưa trả lời. Một câu trả lời
   "không còn đúng" sẽ buộc viết lại `README.md` và mục đó — việc của một unit `docs`, không
   phải của unit này.
4. **Bản cài cũ có tự sửa không?** Không. Người đang chạy `v0.2.2` phải chạy lại dòng install
   để lấy bản mới; `docs/install.md` mục `## Update` đã nói đó là đường duy nhất. Nghĩa là
   fix này chỉ tới tay người ta khi có release mới, và ship stage phải tạo nó.
