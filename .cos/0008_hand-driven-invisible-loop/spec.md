# Spec: A board over eight stages, with tools opened per step and every turn counted
Intent: intent.md. Author: Bao Do. Status: accepted.

> **Skip assessment** (bắt buộc bởi `write-spec`). Năm tiêu chí, năm verdict:
> (1) ≤2 file đã có — **fail**, `intent.md` đã kể ít nhất 6; (2) không đổi interface hay dữ
> liệu lưu — **fail**, thêm route, thêm state trên đĩa, payload `done` mọc thêm trường;
> (3) không thêm dependency — **fail**, bước PR gọi một tiến trình ngoài chưa từng gọi;
> (4) không có hành vi ngoài intent — **fail**, intent để ngỏ bốn bước ghi artifact gì;
> (5) không chạm bề mặt an toàn — **fail**, và đây là cái ép mạnh nhất: unit này mở tool ghi,
> tool exec, và đường tới credential git. Không skip.

> **Bổ sung ngày 2026-09-21, sau khi file này đã accepted và commit (`22cf939`), trước khi
> có dòng code nào.** `intent.md` nhận thêm một ràng buộc của tác giả về giao diện. Spec
> này lớn thêm đúng phần đó: `## Requirements` có mục **Giao diện** với R22–R25,
> `## Out of scope` bỏ dòng loại trừ thẩm mỹ, và C7 viết lại cho khớp. Requirement khác
> không đổi; Design không đổi.

## Requirements

Mỗi requirement dưới đây truy về đúng một mệnh đề của `intent.md ## Proposed outcome`,
ghi trong ngoặc. Requirement nào không truy được thì đã bị cắt.

### Tám giai đoạn (P1)

- **R1.** Tên tám giai đoạn cố định và viết thường: `idea`, `intent`, `spec`, `plan`,
  `impl`, `pr`, `review`, `ship`. Board hiện đủ tám cho mọi unit, kể cả unit chưa có bước
  nào chạy.
- **R2.** Mỗi giai đoạn có **đúng một** artifact trong thư mục unit, tên tiếng Anh:
  `idea.md`, `intent.md`, `spec.md`, `plan.md`, `impl.md`, `pr.md`, `review.md`, `ship.md`.
  `ARTIFACTS` trong `.claude/scripts/cos.mjs:14` đi từ **3** lên **8**, `VALID` (`:16-20`)
  có danh sách status cho cả tám, và phép kiểm file lạ (`:59-60`) không được báo năm file
  mới là `unexpected file(s)`.
- **R3.** `checkGate` (`.claude/scripts/cos.mjs:118-142`) nhận cả tám tên. Giai đoạn `N`
  chỉ mở khi mọi giai đoạn trước nó đã `settled` (`accepted` hoặc `skipped`, theo
  `:78`). Tên ngoài tám cái vẫn trả `unknown stage`.
- **R4.** Prompt của một bước chứa **nguyên văn** artifact của bước liền trước cộng
  `intent.md`. Đo được: bản ghi run của bước lưu độ dài prompt và danh sách artifact đã
  nhúng; nếu bước `N` chạy mà danh sách ấy không chứa artifact của `N-1`, R4 hỏng.
- **R5.** Mỗi bước của mỗi unit mang một chế độ, `manual` hoặc `autonomous`, đổi được từ
  trang và sống qua restart của app.
- **R6.** Mọi bước chạy qua board ghi lại `session_id`, và `Sessions.created_here`
  (`cos_baodo/sessions.py:160`) trả `True` cho id đó. Bước manual do người gõ ngoài app
  không có session id, và board hiện nó là `unattributed` chứ không hiện nó là xong.
- **R7.** `impl` và `pr` chạy được ở chế độ `autonomous`: agent sửa file trong workspace,
  và mở được một pull request, không ai gõ lệnh trong terminal.

### Tool, mở đúng chỗ (P1, và ràng buộc "không làm 0007 vô nghĩa")

- **R8.** Mặc định của mọi session app tạo vẫn là **0 tool**, đúng như
  `cos_baodo/config.py:44`. Session chat (không thuộc board) không đổi hành vi. Phép kiểm
  của `0007` phải còn xanh nguyên trạng sau unit này.
- **R9.** Quyền tool gắn vào **cặp (giai đoạn, chế độ)**, không phải vào `Config`. Sáu giai
  đoạn `idea`, `intent`, `spec`, `plan`, `review`, `ship` có grant rỗng ở **cả hai** chế độ.
  Chỉ `impl` và `pr`, và chỉ khi chế độ là `autonomous`, mang grant khác rỗng.
- **R10.** Grant được cưỡng chế tại thời điểm gọi tool, không chỉ ở lúc dựng options. Mọi
  lần từ chối được ghi vào bản ghi run của bước và đếm được.
- **R11.** Một bước autonomous có trần cứng: số lượt và ngân sách. Vượt trần thì bước dừng
  với trạng thái `exhausted`, không phải treo. Trần mặc định: **50** lượt và **5.00** USD
  cho một bước. *Hai số này là chọn, không phải đo* — cùng kiểu với `LOCK_TIMEOUT` ở
  `cos_baodo/store.py:45-49`, và chúng tồn tại để biến một vòng lặp vô hạn thành một lỗi,
  không phải để phản ánh chi phí thật của một bước.
- **R12.** Knob 3 `bypass_permissions` (`cos_baodo/config.py:49`) vẫn tắt và vẫn không đặt
  được qua HTTP. Không bước nào của board bật nó.

### Live và timeline (P2)

- **R13.** Trong lúc một bước autonomous chạy, trang nhận cập nhật mới **trong vòng 2 giây**
  kể từ khi agent phát ra một khối text hoặc gọi một tool, và **không cần reload**. *Con số
  2 giây là chọn, không đo* — nó là ngưỡng để "live" có nghĩa khác "polling chậm".
- **R14.** Một bước autonomous đang chạy **không chặn** thao tác khác trên trang: trong lúc
  nó chạy, liệt kê workspace và đổi chế độ của bước khác vẫn trả lời.
- **R15.** Timeline của một unit liệt kê các bước theo thứ tự thời gian, mỗi mốc có: tên
  giai đoạn, chế độ, mốc bắt đầu, mốc kết thúc, artifact sinh ra, session id (hoặc
  `unattributed`), và kết cục (`done`, `failed`, `exhausted`, `cancelled`).

### Đếm (P3)

- **R16.** Payload `done` của `Sessions.stream` (`cos_baodo/sessions.py:244`, hôm nay 3
  trường) mang thêm số của lượt: input token, output token, cache read, cache creation,
  chi phí USD, số lượt, thời lượng ms.
- **R17.** Board hiện số token của **mỗi** bước và tổng của **mỗi** unit. Tổng của unit bằng
  tổng các bước của nó, kiểm bằng phép cộng chứ không bằng một trường lưu sẵn thứ hai.
- **R18.** Với một unit đã chạy ít nhất một bước autonomous, tổng token khác **0**.

### Bằng chứng (cả ba mệnh đề)

- **R19.** `scripts/verify_0008.py` dựng lại mệnh đề 1 và 3 **không cần trình duyệt**, và
  trả non-zero nếu bất kỳ mắt nào hỏng. Nó dùng mã thoát cùng quy ước với
  `scripts/verify_0004.py`: `0` đạt, `1` hỏng, `2` môi trường chưa sẵn sàng.
- **R20.** Mệnh đề 2 (live, không reload) chỉ chứng minh được bằng trình duyệt thật, nên nó
  đi vào `verify_0004.py` hoặc một lệnh cùng loại. Không mệnh đề nào của P2 được tính là
  đạt bằng một phép kiểm mức HTTP.
- **R21.** `npm test` vẫn xanh, vẫn không cần trình duyệt, vẫn không cần toolchain
  JavaScript. Năm lệnh chứng minh cũ vẫn xanh với đúng những mệnh đề chúng đang khẳng định.

### Giao diện (ràng buộc tác giả, thêm 2026-09-21)

Bốn requirement này **không** truy về ba mệnh đề của kết quả — chúng truy về ràng buộc tác
giả vừa thêm ở `intent.md ## Constraints`. Đây là một lần nới invariant 1 của `write-spec`,
vốn đòi mọi requirement truy về `## Proposed outcome`. Ghi ra vì nó là lựa chọn, không phải
sơ suất: hỏng R22–R25 thì unit vẫn đạt kết quả đã hứa, và tác giả vẫn không có thứ họ muốn.

- **R22.** App khai báo theme tường minh. `rx.App` nhận `theme=rx.theme(...)` với
  `accent_color`, `gray_color`, `radius`, `scaling` đặt rõ, không để mặc định thư viện.
  Hôm nay con số là **0** lần dùng `rx.theme` trong `cos_baodo/*.py`.
- **R23.** Đổi được light/dark từ trang, và lựa chọn ấy sống qua việc tải lại trang. Màu
  không hardcode hex: chỗ nào cần một sắc độ thì đi qua `rx.color(...)` để nó theo mode.
- **R24.** Không tràn ngang ở **ba** bề rộng viewport: **390px**, **768px**, **1280px**.
  *Ba số này là chọn, không đo* — chúng là mốc điện thoại, tablet và laptop thường gặp, và
  tồn tại để biến "responsive" thành một phép kiểm chứ không phải một tính từ.
- **R25.** Tương phản chữ thân bài đạt **4.5:1** ở cả light và dark. Nguồn: WCAG 2.1 AA.
  ***Unverifiable trong repo*** — chuẩn nằm ngoài, và con số lấy từ đó.

## Design

### Ranh giới: bốn thành phần, và thứ đi qua giữa chúng

**1. `Board` — đọc `.cos/` của một workspace thành mô hình tám bước.**
Nó thuần đĩa, không giữ client, không gọi SDK. Vào: đường dẫn workspace. Ra: danh sách unit,
mỗi unit có tám bước với `status` đọc từ dòng `Status:` của artifact tương ứng. Đây là cùng
phép đọc mà `.claude/scripts/cos.mjs:36-62` đang làm, và **logic phải nằm ở đúng một chỗ**:
`cos.mjs` là bản có thẩm quyền vì nó là thứ gate đọc, nên `Board` gọi nó qua tiến trình con
và đọc `status --json`, thay vì chép lại luật parse sang Python. Hai bản parse sẽ lệch, và
lệch âm thầm.

Hệ quả: **board là board của workspace đang chọn, không phải của repo chạy app.** Harness là
template (`.claude/harness.md:13-14`), nên một workspace có `.claude/scripts/cos.mjs` thì có
board; workspace không có thì board hiện rỗng kèm lý do. `cos-baodo` tự nó là một workspace
như mọi workspace khác.

**2. `StagePolicy` — cặp (giai đoạn, chế độ) → quyền.**
Đây là chỗ R8 và R9 sống, và nó **không phải** một knob mới trên `Config`. `Config` giữ
nguyên bốn knob và giữ nguyên nghĩa "mặc định của app". `StagePolicy` là một bảng tra thuần
hàm, không đọc env, không sửa được qua HTTP, trả về ba thứ cho một bước: danh sách tool được
phép, trần lượt, trần ngân sách. Sáu giai đoạn trả về danh sách rỗng ở cả hai chế độ; chỉ
`impl` và `pr` ở chế độ `autonomous` trả về khác rỗng.

Lý do tách khỏi `Config`: `0007` tồn tại vì câu ở `cos_baodo/config.py:44` không đúng. Nếu
quyền của board đi vào `Config` thì câu ấy lại phải nói về hai thứ cùng lúc và lại sẽ sai với
một trong hai. Tách ra thì `Config` vẫn nói đúng một điều — mặc định — và `StagePolicy` nói
điều còn lại, ở một chỗ đọc riêng.

Cưỡng chế (R10) nằm ở callback `can_use_tool` của SDK, chứ không chỉ ở danh sách truyền vào
lúc dựng options. Lý do là bài học của chính `0007`: một danh sách truyền vào có thể không
bao phủ hết nguồn năng lực, và `0007` đo được đúng điều đó — 11 MCP tool đi vòng qua
`tools=[]`. Một callback thì đứng ở đường mọi tool phải đi qua, bất kể nó đến từ nguồn nào.
Danh sách vẫn được truyền, làm lớp thứ nhất; callback là lớp quyết định.

**3. `Runner` — chạy một bước, phát sự kiện, trả số.**
Vào: unit, giai đoạn, chế độ, và prompt đã lắp (R4). Ra: một dòng sự kiện
`(kind, payload)` cùng hình dạng `Sessions.stream` đang dùng (`cos_baodo/sessions.py:190`),
mở rộng thêm loại `tool` và `denied`, và `done` mang số (R16).

`Runner` **không** tự viết artifact. Agent trong session viết, bằng tool ghi, vào đúng thư
mục unit. Board chỉ đọc lại sau đó. Đây là điều giữ ràng buộc "không có kho sự thật thứ hai
cho artifact" của `intent.md`: nội dung artifact chỉ có một bản, trên đĩa, trong git.

**4. `Journal` — thứ không phải artifact.**
Chế độ mỗi bước (R5), session id (R6), mốc thời gian (R15), số token (R17), số lần từ chối
tool (R10): không cái nào là artifact, và không cái nào nên nằm trong file mà người đọc để
hiểu công việc. Chúng đi vào một bản ghi **append-only** cạnh store workspace, dùng lại đúng
cơ chế đã trả giá ở `0005`: ghi qua temp rồi `rename`, khoá `flock` trên file riêng, chờ có
trần (`cos_baodo/store.py:16-22`, `:42-49`).

Ranh giới giữa 3 và 4 là câu trả lời cho `intent.md` open question 5: **artifact suy ra từ
file, telemetry lưu trong journal.** Trạng thái của một bước không bao giờ đọc từ journal —
nó luôn đọc từ dòng `Status:` của artifact — nên journal không thể nói dối về tiến độ. Nó chỉ
kể ai chạy, lúc nào, tốn bao nhiêu.

### Dữ liệu đi qua ranh giới

- `Board → trang`: unit, tám bước, status, chế độ, tổng token. Không nội dung artifact.
- `Board → Runner`: unit, giai đoạn, và nguyên văn artifact của bước trước (R4).
- `StagePolicy → Runner`: danh sách tool, trần lượt, trần ngân sách.
- `Runner → trang`: `chunk`, `tool`, `denied`, `done`. `done` mang số của R16.
- `Runner → Journal`: một bản ghi khi bắt đầu, một khi kết thúc. Không ghi mỗi chunk —
  journal là bản ghi của bước, không phải bản sao của transcript, vốn đã có ở session store.

### Live, và vì sao nó phải là background

Handler hiện tại (`cos_baodo/cos_baodo.py:137-149`) là generator event thường, nên nó giữ
khoá state suốt lượt. Một bước autonomous kéo dài nhiều phút sẽ khoá cả trang, và R14 hỏng.
Reflex 0.9.11.post1 có `rx.event(background=True)` — *đo ngày 2026-09-21 từ gói đã cài,
nguồn ngoài repo, nên đánh dấu unverifiable theo invariant 7 của `write-spec`*. Bước
autonomous chạy trong background event, nhận khoá từng đoạn ngắn để đẩy cập nhật; thao tác
khác trên trang vẫn vào được giữa các đoạn.

### Bước `pr`

`cos_baodo/gitops.py` hôm nay chỉ có `clone` và `pull`, và nó **không mở rộng** trong unit
này. Branch, commit, push và mở PR là việc agent làm trong bước `pr` bằng grant exec giới
hạn ở `git` và `gh`. Hai lý do: app không phải học một giao thức mới, và credential không
phải đi qua app — nó ở chỗ `gh` đã để sẵn. *Đo ngày 2026-09-21: `gh` 2.93.0, đã đăng nhập
tài khoản `baodq97`. Nguồn ngoài repo.*

Cái giá của lựa chọn này nằm ở `## Concerns` C3 và C4.

### Bốn artifact mới ghi gì

Đây là câu trả lời cho `intent.md` open question 3. Mỗi file giữ đúng hình dạng đã dùng:
một dòng `Status:` ở đầu, prose tiếng Việt, heading tiếng Anh.

- `idea.md` — câu hỏi hoặc quan sát thô, trước khi nó thành vấn đề. Status: `draft`,
  `accepted`, `rejected`. Đây là chỗ để "mọi thứ đều được tracking" bắt đầu từ trước
  `intent`.
- `impl.md` — không phải code. Nó ghi cái đã làm và cái đã đo: commit nào, test nào chạy,
  kết quả. Code nằm trong git; file này là chỗ bước `pr` đọc để biết phải mô tả gì.
- `pr.md` — URL của pull request, branch, và phạm vi diff.
- `review.md` — các phát hiện, và ai kết luận. Xem C5.
- `ship.md` — cái gì đã lên, lúc nào, và đo bằng gì.

## Out of scope

- **Đăng nhập, nhiều người dùng, TLS, chạy ngoài loopback.** Tác giả chốt "tạm thời noauth".
- **Triển khai ra khỏi máy này**, và mọi giai đoạn SDLC sau `ship` (vận hành, sự cố,
  incident record mà `docs/ai-native-sdlc-playbook.md` có nhắc).
- **Tự động kích hoạt bước sau khi bước trước xong.** `docs/ai-native-sdlc-playbook.md:63`
  mô tả trạng thái đích là "each accepted artifact fires the next gate"; unit này dựng chỗ
  để bấm, không dựng cái bấm hộ. Người chọn chế độ và khởi động từng bước.
- **`gitops.py` mở rộng.** Không có `branch`, `commit`, `push` ở tầng Python.
- **Sửa `0006` hay `0005` C2.** Hai bản app trên cùng working folder vẫn nhìn xuyên qua nhau.
- **Thẩm mỹ vượt quá sàn của R22–R25.** Sàn craft thì đo được và nằm trong phạm vi. Cái
  còn lại — bố cục có đẹp không, có "xịn mịn" không — vẫn là phán đoán, vẫn không có phép
  đo, và unit này không hứa. Xem C7.
- **Hook `PreToolUse` cho harness.** `.claude/harness.md` nói một hook sẽ biến gate từ lời
  khuyên thành luật; unit này không dựng nó.

## Concerns

**C1 — `0007` và `0008` kéo ngược chiều nhau, và thứ tự là của tác giả quyết.**
`0007` có plan accepted (`2d6e0dc`) và chưa có code; nó đi đóng đường tool. `0008` đi mở.
Thiết kế trên cố ý giữ được cả hai — mặc định vẫn 0 tool (R8), quyền nằm ngoài `Config`
(R9) — nhưng nếu `0008` làm trước và `0007` làm sau, thì `0007` phải kiểm một mặc định giờ
có `StagePolicy` đứng cạnh, và phép kiểm của nó phải chứng minh thêm rằng board không mở
đường vòng. **Khuyến nghị: làm `0007` trước.** Quyết định là của tác giả, không phải của
spec này.

**C2 — repo này không có remote, nên mệnh đề 1 hôm nay không thể đạt.**
`git remote -v` ngày 2026-09-21 trả về rỗng. `.claude/harness.md` mô tả tác giả làm một mình
và commit thẳng `main`, nên chưa từng cần remote. R7 đòi mở một pull request; không có remote
thì không có chỗ nào để mở. Đây là **blocker cứng của hạn 2026-09-28**, không phải một chi
tiết. Hai lối, và tác giả chọn: (a) tạo remote cho `cos-baodo`; hoặc (b) unit chứng minh
`0009_*` nằm trong một workspace khác đã có remote — nhưng `intent.md` nói `0009_*` do
`cos.mjs new-path` cấp, và script ấy neo vào repo chứa nó (`.claude/scripts/cos.mjs:9`), nên
lối (b) đòi sửa cả cách hiểu kết quả.

**C3 — grant exec cho bước `pr` là hàng rào rộng nhất unit này mở.**
"Giới hạn ở `git` và `gh`" là một câu dễ viết và khó cưỡng chế: một tool exec nhận chuỗi lệnh,
và `git` có `-c core.pager`, `gh` có `gh api`. Callback `can_use_tool` thấy được lệnh trước
khi nó chạy, nên chỗ cưỡng chế có tồn tại — nhưng luật phân tích chuỗi lệnh thì chưa ai viết,
và một luật sai ở đây là một lỗ, không phải một phiền toái. Chủ sở hữu quyết: tác giả, ở
`plan.md`, bằng cách chốt luật cụ thể thay vì để câu "giới hạn ở git và gh" đi tiếp.

**C4 — agent bước `pr` chạm credential `gh` của cả máy.**
`gh` đã đăng nhập ở mức người dùng (`/home/bd/.config/gh/hosts.yml`, đo 2026-09-21). Một
agent gọi được `gh` thì gọi được tới **mọi** repo tài khoản ấy với tới, không riêng workspace
đang mở. Đây đúng hình dạng cái hại `0007` mô tả — năng lực đến từ cấu hình mức máy, không
khai trong app, không hiện trong app — chỉ lần này app mở nó có chủ ý. Nó phải hiện ra trên
trang trước khi bước chạy, chứ không nằm trong một file thiết kế.

**C5 — cột `review` vẽ lại đúng chỗ trống mà harness đã thừa nhận.**
`.claude/harness.md` viết thẳng: không còn bước duyệt nào, `accepted` là chữ agent tự viết về
việc của chính nó, và cái còn lại "is not separation of duties but three weaker things".
Một ô `review` mà agent tự tick làm chỗ trống ấy **khó thấy hơn** trước, vì nó trông như một
cổng. Spec này không giải quyết nó và không giả vờ giải quyết. Chủ sở hữu quyết: tác giả.
Lối tối thiểu nếu muốn giữ ô ấy trung thực — `review.md` phải ghi ai kết luận, và giá trị
`accepted` do agent tự cấp phải hiện khác với một giá trị do người cấp.

**C6 — `max_turns=1` phải mất, và nó đang đỡ một thứ khác.**
`cos_baodo/sessions.py:125` đặt `max_turns=1`. Một bước `impl` không thể xong trong một lượt,
nên R7 đòi bỏ nó cho bước autonomous. Nhưng `max_turns=1` hiện đang là thứ làm mỗi lượt chat
có biên rõ ràng, và bỏ nó ở nhầm chỗ thì một session chat cũng thành nhiều lượt. Giới hạn mới
(R11) phải gắn vào `StagePolicy`, không phải thay giá trị toàn cục.

**C7 — sàn craft thì đo được; "đẹp" thì vẫn không, và hai thứ đó không thay nhau được.**
R22–R25 đóng được phần cứng của `intent.md` open question 10: theme tường minh, dark/light,
ba bề rộng, tương phản AA. Cả bốn đều có phép kiểm, và cả bốn đều là **điều kiện cần**.
Chúng không phải điều kiện đủ: một trang qua hết R22–R25 vẫn có thể xấu, rối, và vẫn khiến
tác giả thà mở terminal. Không có số đo nào cho phần còn lại, và spec này không bịa một cái
ra. Chủ sở hữu quyết: tác giả, bằng mắt, sau khi nhìn trang thật — và nếu muốn phần ấy cũng
có thể **đỏ** được thì nó cần intent riêng với kết quả riêng, không phải thêm một dòng vào
đây.

**C8 — hai bản parse trạng thái là rủi ro, và thiết kế mới chỉ giảm chứ không xoá.**
`Board` gọi `cos.mjs status --json` thay vì chép luật parse sang Python, nên luật chỉ có một
bản. Cái giá: app gọi Node cho mỗi lần đọc board, và `.claude/scripts/cos.mjs` thành một phụ
thuộc lúc chạy của một app Python. Nếu một workspace không có file đó, board ở đó không chạy.

**C9 — journal làm vùng va chạm của `0005` C2 rộng ra.**
`0005` đo được bốn tiến trình ghi một working folder chỉ còn **8/20** mục khi chưa
khoá (`cos_baodo/store.py:16-18`), và khoá hiện chỉ phủ tiến trình này. Journal thêm
một file ghi thường xuyên hơn hẳn danh sách workspace. Dùng lại `flock` của
`store.py` là bắt buộc, không phải tuỳ chọn — nhưng nó vẫn không đóng `0005` C2.

## Open questions

Mười câu của `intent.md`, trả lời hoặc nêu lại kèm thứ một câu trả lời sẽ đổi.

1. **PR đi đâu** — *chưa trả lời, thành C2.* Đây là câu duy nhất chặn hạn.
2. **`0007` trước hay `0008` trước** — *chưa trả lời, thành C1.* Spec khuyến nghị `0007`
   trước; quyết định là của tác giả.
3. **Bốn bước mới ghi artifact gì** — **trả lời rồi**, ở `## Design`, và R2 mở
   `ARTIFACTS` từ 3 lên 8 để `cos.mjs` thôi gọi chúng là file lạ.
4. **`review` là ai review** — *chưa trả lời, thành C5.*
5. **Trạng thái board ở đâu** — **trả lời rồi**: status suy ra từ artifact, telemetry vào
   journal. Cái còn hở là bước chưa có artifact thì không có status nào để hiện; R1 bắt hiện
   đủ tám bước, nên một bước chưa bắt đầu hiện là `not started`, và đó là một giá trị suy ra
   từ sự vắng mặt của file chứ không phải một giá trị lưu.
6. **`ResultMessage` cho gì** — **trả lời rồi, đo ngoài repo.** Ngày 2026-09-21,
   `claude-agent-sdk` 0.2.157: `ResultMessage` có `usage`, `total_cost_usd`, `num_turns`,
   `duration_ms`, `permission_denials`, và `model_usage` với `inputTokens`, `outputTokens`,
   `cacheReadInputTokens`, `cacheCreationInputTokens`, `costUSD`. **Unverifiable trong
   repo** — nguồn là gói đã cài. Còn hở: chưa đo số ấy có cộng dồn qua các lượt của cùng một
   session hay chỉ là của lượt cuối. Một câu trả lời sai ở đây làm R17 cộng sai.
7. **Live view chạy qua đường nào** — *vẫn hở, và là lý do R20 tồn tại.* Đường
   `/_event` vẫn là đường ít bằng chứng nhất; `verify_0004.py` chạm nó một lần bằng trình
   duyệt thật, và mệnh đề 2 không có cách nào khác để đạt.
8. **Bước autonomous chạy bao lâu, bỏ dở thì sao** — **trả lời một nửa.** Trần lượt và trần
   ngân sách (R11) biến vô hạn thành `exhausted`. Huỷ có đường: SDK có
   `ClaudeSDKClient.interrupt` (*đo 2026-09-21, nguồn ngoài repo*). **Chưa trả lời:** app tắt
   giữa một bước autonomous thì bước ấy để lại gì — journal có mốc bắt đầu, không có mốc
   kết thúc, và không ai dọn.
9. **Hai bản app nhìn xuyên nhau** — *vẫn hở, thành C9.*
10. **"UI như SaaS" đo bằng gì** — *vẫn hở, thành C7.*

Hai câu mới, sinh ra từ thiết kế này:

11. Grant exec của bước `pr` cưỡng chế bằng luật nào trên chuỗi lệnh? C3 nói chỗ cưỡng chế
    tồn tại; nó không nói luật. `plan.md` phải chốt.
12. Một workspace không có `.claude/` thì board ở đó là rỗng (C8). Vậy app có nên giúp khởi
    tạo harness vào một workspace không? Đó là đúng nghĩa "template" mà
    `.claude/harness.md:13-14` hứa, và nó không nằm trong `intent.md`, nên unit này không
    làm — nhưng một câu trả lời "có" sẽ là unit kế tiếp.
