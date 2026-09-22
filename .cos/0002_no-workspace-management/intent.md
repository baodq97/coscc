# Intent: Workspaces must be declared by hand, on a stack that cannot show them
Author: Bao Do. Status: accepted.

> **Sửa ngày 2026-09-21, sau khi bản đầu đã accepted và commit (`c34adfc`).** Tác giả bổ
> sung ba ràng buộc về nền: cấu trúc uv, Reflex/FastAPI thay aiohttp, và giao diện dựng
> bằng component Python thay HTML viết tay. Bản đầu đọc được ở `c34adfc`; file này thay
> nó. Harness không có bước sửa đổi, nên việc ghi đè một file đã `accepted` là một lựa
> chọn, không phải một quy trình — xem `## Constraints`, mục cuối.

## Problem

Danh sách workspace là một biến môi trường, đọc đúng một lần lúc tiến trình khởi động
(`app/config.py:110`), và không có đường nào sửa nó khi app đang chạy — `app/web.py:135-140`
chỉ mở `GET /api/workspaces`, không có route ghi nào cho danh sách ấy. Thêm một project
nghĩa là tắt app, sửa `COS_WORKSPACES` (`.claude/CLAUDE.md:33`), bật lại.

Hệ quả thứ nhất: app không biết tôi có bao nhiêu workspace. Nó chỉ biết tôi đã gõ bao nhiêu
cái vào dòng lệnh lần này. Việc dò session làm theo từng thư mục một
(`app/sessions.py:55`); không chỗ nào liệt kê project. Nên câu hỏi "tôi đang có bao nhiêu
chỗ làm việc" không có ai trả lời ngoài trí nhớ của tôi, và trí nhớ ấy chính là thứ phải gõ
lại đúng vào lần khởi động sau.

Hệ quả thứ hai: đem code mới về vẫn là việc của terminal. `0001` bỏ được terminal khỏi việc
mở session, nhưng bước ngay trước đó — clone một repo rồi khai báo nó — thì chưa. Vòng lặp
vẫn gãy ở cùng một chỗ, chỉ là sớm hơn một bước so với `terminal-only-access`.

Hệ quả thứ ba, và là lý do phạm vi unit này rộng hơn bản đầu: **chỗ để hiện những thứ đó
không tồn tại.** Trang của app là 151 dòng HTML viết tay (`app/public/index.html`). Danh
sách workspace sửa được, có label, có trạng thái `missing`, có nút clone và pull — là một
giao diện, không phải một ô chat. Viết tay từng dòng cho nó là cách chắc chắn nhất để nó
không bao giờ được viết.

Đây đúng là chỗ `0001` cố ý hoãn, chứ không phải chỗ nó bỏ sót. `spec.md:61` chốt trạng thái
cục bộ của app chỉ gồm danh sách thư mục và bốn knob; `app/config.py:1-11` ghi rõ lý do
hoãn là "bốn knob chưa đủ để biện minh cho một schema". Lý do đó hết hiệu lực đúng lúc danh
sách trở thành thứ sửa được và phải sống qua lần khởi động sau.

Một dấu hiệu nhỏ của cùng chỗ trống ấy: `with_workspaces` (`app/config.py:117-119`) là
mutator duy nhất của danh sách, docstring nói nó phục vụ lệnh kiểm chứng — nhưng lệnh đó
dựng `Config` thẳng (`scripts/verify_0001.py:96`) và không gọi nó. Chỉ còn test của chính
nó gọi (`app/config_test.py:59`). Đường sửa workspace đã được dự trù và chưa từng được dùng
thật.

## Proposed outcome

Đến hết ngày **2026-09-28**, một lệnh chạy được **không cần trình duyệt** chứng minh cả ba
mệnh đề dưới đây trong **một** lần chạy, và trả về non-zero nếu bất kỳ mệnh đề nào hỏng:

1. **Nền đã chuyển.** `npm test` xanh, `scripts/verify-0001.mjs` xanh, và lệnh kiểm của
   `0001` xanh với **đúng những mệnh đề nó đang kiểm hôm nay** — chỉ thư viện client và
   đường import được phép đổi.
2. **Không còn HTML viết tay.** Repo không còn file HTML hay CSS viết tay nào phục vụ trang
   của app; `app/public/index.html` (151 dòng) đã bị xoá và không có file thay thế cùng
   loại. Giao diện dựng hoàn toàn bằng component Python.
3. **Workspace quản lý được.** Khởi động với working folder trống và **không** khai báo
   `COS_WORKSPACES` nào: clone **2** repo → app báo đúng **2** → đặt label cho một cái →
   tắt app, bật lại, không đổi một biến môi trường nào → vẫn đúng **2**, label còn nguyên →
   `pull latest` một cái, xong, không lỗi → tạo session mới trong **mỗi** workspace và nhận
   được phản hồi → xoá một workspace → app báo còn đúng **1** → tắt bật lại → vẫn đúng **1**.

Kết quả này sai nếu đến hết ngày đó lệnh ấy không tồn tại, hoặc nó chạy nhưng gãy ở bất kỳ
mắt nào: test cũ đỏ, còn sót một file HTML viết tay, clone không ra, số đếm không đi đúng
2 → 2 → 1 → 1, label mất sau khi khởi động lại, `pull latest` lỗi, session nào đó không
phản hồi, hoặc xoá rồi mà workspace vẫn còn.

Số đếm nằm trong phép đo là cố ý: nó là đúng cái app hiện không trả lời được, nên nó là chỗ
kết quả này dễ chết nhất. Và phép đo **không cần trình duyệt** vì cùng lý do đã ghi ở
`.cos/0001_no-session-management/intent.md:33-34` — bài học rằng bằng chứng cần người ngồi
đó thì làm chậm mọi vòng lặp.

**Ba mệnh đề là một sự kéo giãn có chủ ý của invariant 3 trong `write-intent`**, vốn đòi
đúng một kết quả. Chúng được giữ chung vì mệnh đề 3 không kiểm được trên nền cũ và mệnh đề
2 không có nghĩa nếu không có gì để hiển thị. Cái giá: unit này hỏng ở một mắt là hỏng cả
ba, và không có cách nào đọc kết quả để biết phần nào đứng được.

## Affected users and systems

- **Người dùng:** duy nhất tác giả, một máy, loopback. Không phân phối cho ai khác.
- **`app/` biến mất.** Gói Python chuyển sang layout phẳng của Reflex, cạnh `rxconfig.py`.
  Trên HEAD `06f2e6e`, các artifact đã commit trong `.cos/` chứa **35** trích dẫn dạng
  `app/<file>`; **13** trong số đó nằm ở `.cos/0001_no-session-management/plan.md`, file mà
  unit này **không** viết lại — đó mới là phần trích dẫn chết thật sự. `terminal-only-access` không có cái
  nào. Đếm ngày 2026-09-21 bằng
  `git show HEAD:<file> | grep -o "app/[a-z_/]*\.\(py\|html\)"`.
- **`package.json`:** `test:python` đang khoá cứng `-s app`. Phải đổi.
- **`.claude/CLAUDE.md:17`:** ghi "There is no build step — the channel runs from source"
  và cấm bịa ra một lệnh build. Reflex biên dịch frontend, nên dòng đó thành sai và file đó
  phải sửa, không phải lách.
- **`scripts/verify_0001.py`:** import `app.web` và dùng `aiohttp`. Nó là **bằng chứng duy
  nhất** `0001` từng đạt, và unit này viết lại nó.
- **`app/web.py`:** toàn bộ lớp HTTP đổi framework.
- **`is_workspace` và ba chỗ gọi nó** (`app/config.py:82-94`; `app/web.py:44`, `:60`,
  `:85`): ranh giới này đang đứng được một phần nhờ danh sách bất biến trong suốt đời tiến
  trình. Nó sẽ không còn bất biến.
- **Đĩa:** `spec.md:59` ghi app không tự ghi gì, chỉ SDK ghi transcript. Dòng đó sẽ sai.
- **`git`, mạng, và `bun`/Node:** lần đầu app gọi tiến trình ngoài, lần đầu chạm mạng, và
  lần đầu repo có một bước biên dịch.

## Constraints

**Ba ràng buộc dưới đây do tác giả đặt, không suy ra từ vấn đề.** Ghi ở đây vì intent không
được chứa thiết kế, nhưng được chứa lựa chọn của người đặt hàng. Một spec không được lật
chúng; nó chỉ được nói chúng tốn gì.

- **Reflex làm web framework, FastAPI là backend của nó.** Reflex chạy trên FastAPI và cho
  mount một app FastAPI qua `api_transformer`, nên JSON API và trang nằm **cùng một tiến
  trình** — đó là điều kiện để lệnh kiểm không cần trình duyệt vẫn đi qua đúng tiến trình mà
  tab đi qua. Streamlit bị loại vì nó chạy Tornado, không phải FastAPI, và không có đường
  cho endpoint JSON riêng. **Unverifiable:** cả hai điều này đọc từ tài liệu của Reflex và
  Streamlit, nằm ngoài repo.
- **Layout phẳng theo Reflex:** gói Python nằm cạnh `rxconfig.py` ở gốc repo, không dùng
  `src/`. Tài liệu Reflex chỉ mô tả dạng phẳng. **Unverifiable:** nguồn ngoài repo.
- **uv đúng chuẩn hiện hành:** `[dependency-groups]` thay cho dạng cũ, có
  `[project.scripts]`, có `.python-version`, commit `uv.lock` và lockfile frontend của
  Reflex; `.web/` không commit. **Unverifiable:** nguồn ngoài repo.
- **Không còn HTML hay CSS viết tay cho trang của app.** Đây là mệnh đề 2 của kết quả, nên
  nó vừa là ràng buộc vừa là thứ đo được.
- **Working folder khai báo bằng env, không đặt được qua HTTP.** Giữ nguyên cơ chế đã làm
  nên knob 3: `from_env` không có đường đi tới từ một request (`app/config.py:97-103`). Mọi
  workspace và mọi lần clone phải nằm dưới folder đó.
- **Danh sách sửa được không có nghĩa là ranh giới biến mất.** `is_workspace` đổi câu hỏi từ
  "có trong env không" sang "có nằm dưới working folder không" — đổi, chứ không bỏ.
- **App tự chạy `git`, và chỉ `clone` với `pull latest`.** Session vẫn **chat only, không
  tool nào** (`app/config.py:44-47`, `.claude/CLAUDE.md:27`). `spec.md:101` ghi vì sao tư
  thế mặc định chặt đến vậy, và một unit về workspace và giao diện không được phép lật nó.
- **Các thao tác git khác (branch, commit, push) không thuộc unit này.**
- **Trạng thái workspace phải sống qua restart.** Điều này phá `spec.md:61`. Đây là lúc chỗ
  nối ở `spec.md:146-151` được dùng đến — nhưng chỉ cho workspace và metadata của nó.
- **Không có kho dữ liệu thứ hai cho session.** Session store của SDK vẫn là nguồn sự thật
  (`spec.md:70`).
- **Lệnh kiểm của `0001` giữ nguyên các mệnh đề nó kiểm.** Được đổi client và import; không
  được đổi, nới, hay bỏ bớt điều nó khẳng định. Nó là bằng chứng duy nhất của một unit đã
  đóng.
- **Ràng buộc xác thực của `0001` còn nguyên**
  (`.cos/0001_no-session-management/intent.md:48-55`).
- **Không xoá `channel/`, không xoá `evidence/0001_terminal-only-access/`.**
- **Ghi đè một artifact đã `accepted` là ngoại lệ, không phải lối đi.** Harness không có
  bước sửa đổi. Lần này chấp nhận được vì `0002` chưa có `plan.md` và chưa có dòng code nào;
  một lần sửa sau khi đã có code thì phải là unit mới.
- **Phạm vi:** đếm, thêm, xoá, đặt label workspace; clone; pull latest; chuyển nền; giao
  diện bằng component Python. Ngoài phạm vi: nhiều người dùng, đăng nhập, TLS, bật tool cho
  session, git ngoài clone/pull.

## Open questions

1. Clone repo riêng tư cần credential thì sao? App chạy loopback, không có terminal để hỏi
   token hay passphrase.
2. Metadata của workspace gồm gì ngoài label?
3. Xoá workspace là gỡ khỏi danh sách, hay xoá luôn thư mục trên đĩa?
4. Workspace trỏ vào thư mục đã biến mất thì app xử sao — ẩn, báo lỗi, hay tự dọn?
5. File cấu hình ghi được nằm ở đâu, và có commit vào repo không?
6. Docstring của `with_workspaces` (`app/config.py:117-118`) mô tả sai chỗ dùng so với
   `scripts/verify_0001.py:96`. Sửa, hay bỏ hẳn hàm đó?
7. **Mới:** 13 trích dẫn trong `.cos/0001_no-session-management/plan.md` sẽ chết. Để
   nguyên và coi artifact là bản ghi lịch sử đọc qua git, hay sửa hết? Sửa thì đang
   viết lại thứ người ta đã ký.
8. **Mới:** trang do Reflex vẽ đi qua WebSocket `/_event`, còn lệnh kiểm đi qua JSON API.
   Vậy đường mà người dùng thật sự bấm vẫn là đường không có bằng chứng nào chạy qua. Đó là
   đúng cái `app/web.py:3-5` phản đối, chỉ là lật ngược. Chưa biết trả bằng gì.
9. **Mới:** `.claude/CLAUDE.md:17` cấm bịa ra lệnh build. Reflex có bước biên dịch. Sửa dòng
   đó thành gì, và ai kiểm rằng nó vẫn đúng?
