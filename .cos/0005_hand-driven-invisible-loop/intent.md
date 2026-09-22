# Intent: The loop is driven by hand and shows nothing of itself
Author: Bao Do. Type: feat. Status: accepted.

> Lời của tác giả, nguyên văn, ngày 2026-09-21 — invariant 1 của `write-intent` đòi người
> khởi xướng nói trước, và đây là chỗ ghi lại:
>
> "Hiện tại đã spike sơ bộ giờ tôi muốn build thành một sản phẩm hoàn chỉnh mà UI như là
> SaaS vậy á. tạm thời noauth trước, có manage workspaces, rồi mỗi workspaces thì có thể
> chat được, có tracking token, ngoài ra, tôi muốn làm một task board, theo quy trình AI
> Native SDLC, idea -> intent -> spec -> plan -> impl -> PR -> review -> Ship, mỗi mục có
> thể là manual hoặc autonomous, và đi theo nguyên combo giống như mình đang làm, bước sau
> sử dụng thông tin bước trước. như hiện tại ui/ux rất tệ tôi thà dùng terminal còn hơn.
> nên là phần task board thì con người và ai cùng làm việc và có thể xem live và xem các
> intent đó trả qua timeline như thế nào? mọi thứ đều được tracking lại."
>
> Bốn lựa chọn phạm vi dưới đây do tác giả chốt khi được hỏi, không do agent suy ra: gộp ba
> mệnh đề vào một unit và chịu giá; đưa autonomous vào ngay unit này; đo bằng một unit công
> việc thật chạy trọn trong app; hạn 2026-09-28.

> **Bổ sung ngày 2026-09-21, sau khi intent và spec đã accepted và commit (`60d24df`,
> `22cf939`), và trước khi có dòng code nào.** Tác giả thêm một ràng buộc về giao diện:
> "lưu ý làm ui/ux nữa", "tôi muốn có ux/ui đẹp xin mịn". Nó nằm ở `## Constraints`, mục
> cuối. **Kết quả ở `## Proposed outcome` không đổi** — vẫn ba mệnh đề cũ, vẫn hạn
> 2026-09-28. Ghi ở đây vì harness không có bước sửa đổi: `0002` đã đặt tiền lệ rằng ghi đè
> một artifact đã `accepted` là một lựa chọn chứ không phải quy trình, và nó chấp nhận được
> lần này đúng vì chưa có code nào đứng trên nó.

## Problem

Repo này là một harness cho AI-native SDLC, và nó không chạy được ở đâu ngoài terminal.
`.claude/harness.md:16` tự nói về mình: "It is driven by hand. There are no hooks, no CI and
no scheduled jobs." Playbook mà nó triển khai đã viết sẵn trạng thái đích ở
`docs/ai-native-sdlc-playbook.md:63` — "the end state being a loop in which each accepted
artifact fires the next gate". Trạng thái đích đó chưa được dựng, và không có gì trong repo
đang đi về phía nó.

**Thiếu hơn một nửa quy trình.** Tác giả kể tám bước: idea, intent, spec, plan, impl, PR,
review, ship. `checkGate` (`.claude/scripts/cos.mjs:133-139`) biết đúng **ba** tên giai
đoạn: `spec`, `plan`, `implement`; tên nào khác thì nó trả về "unknown stage". Thư mục
`.claude/skills/` có bốn skill, **ba** trong số đó viết artifact. `.claude/harness.md:12-13`
nói thẳng phạm vi: "covering the first three stages: Plan, Design, Build". Nên **bốn trong
tám bước — idea, PR, review, ship — không có skill, không có gate, không có artifact.** Một
bước không có artifact thì bước sau nó không có gì để đọc, và "bước sau dùng thông tin bước
trước" đứt ở đúng chỗ đó.

**App không biết harness tồn tại.** `.cos/` đang có **7** unit công việc. Không dòng nào
trong `cos_baodo/` đọc một unit nào: mọi chuỗi `.cos` trong gói là tên file của thứ khác —
dấu build (`cos_baodo/build.py:32`), tên store và lock (`cos_baodo/store.py:42-43`), tiền tố
thư mục clone tạm (`cos_baodo/service.py:138`). App quản lý được workspace và session, và
hoàn toàn mù với công việc đang chạy bên trong chúng. Không có board, nên cũng không có gì
để xem live, và không có timeline.

**Trang không dùng được, kể cả cho thứ nó đã làm được.** `cos_baodo/cos_baodo.py` dài 267
dòng và gồm một bảng workspace cộng một ô chat. `State` (`:27-42`) có **11** biến và không
biến nào là session id. `send` (`:137-149`) gọi `_service.stream(self.cwd, self.prompt)` với
hai tham số, nên `session_id` luôn là `None`, và `cos_baodo/sessions.py:198` nói rõ điều đó
nghĩa là gì: "Creates the session when `session_id` is None". **Mỗi lần bấm send là một
session mới.** Trong trình duyệt không có cách nào nói câu thứ hai với cùng một session.
Tệ hơn, `:138` xoá `self.reply` mỗi lần gửi, nên câu trả lời trước biến mất — không có danh
sách tin nhắn, chỉ có một ô chứa lượt cuối.

Cái đó không phải vì backend thiếu. `cos_baodo/api.py` mở **8** route, trong đó có
`/api/sessions` (`:109`) và `/api/history` (`:120`). Grep hai chữ `sessions` và `history`
trong `cos_baodo/cos_baodo.py` ra **0** kết quả. Liệt kê session và đọc lại lịch sử là đúng
cái `0001` được mở ra để làm, đã làm xong ở tầng HTTP, và trang không hiện cái nào. Câu "thà
dùng terminal còn hơn" là đánh giá của tác giả, không phải số đo — không có phép đo nào về
trang này trong repo — nhưng ba điều trên thì đo được, và chúng đủ giải thích câu ấy.

**Chi phí đi qua tay app rồi bị vứt.** `.claude/CLAUDE.md` cảnh báo mỗi session app tạo ra
đều tiêu quota tài khoản. Grep `token`/`usage` trong `cos_baodo/*.py` (trừ test) ra đúng
**1** kết quả, và nó là `cos_baodo/gitops.py:4` nói về token đăng nhập git — **không có một
dòng kế toán nào.** Điều đáng nói là app *đã chạm* vào chỗ có số: `cos_baodo/sessions.py:228`
bắt `sdk.ResultMessage` — thông điệp duy nhất mang usage và cost — và lấy khỏi nó đúng một
trường, `session_id`. Payload `done` ở `:244` có **3** trường: `session_id`, `text`, `cwd`.
Số tiền đi qua đúng dòng code đó mỗi lượt và không được giữ lại.

**Và bước autonomous thì đâm vào hàng rào.** `cos_baodo/config.py:44` chốt knob 1: "Empty
means chat only — no tools at all, not even read." Một session không tool thì không ghi được
`spec.md`, không sửa được code, không mở được PR. `chat-only-sessions-have-tools` đang đi làm cho câu đó thành đúng
sự thật — nó đo được session mặc định vẫn báo về **11** MCP tool
(`chat-only-sessions-have-tools/intent.md`) — và plan của `chat-only-sessions-have-tools` đã accepted ở
commit `2d6e0dc` mà chưa có dòng code nào. Nên unit này mở đúng cánh cửa mà unit ngay trước
nó đang đóng.

## Proposed outcome

Đến hết ngày **2026-09-28**, unit công việc kế tiếp — `fragmented-product-experience_*`, số do `cos.mjs new-path` cấp
— đi trọn **tám** bước idea → intent → spec → plan → impl → PR → review → ship **hoàn toàn
trong trình duyệt ở `127.0.0.1`, không gõ một lệnh nào sau khi app đã chạy**, và cả ba mệnh
đề dưới đây cùng đúng trong một lần chạy:

1. **Tám bước, mỗi bước đọc bước trước, ít nhất hai bước tự chạy.** Board hiện đủ 8 bước của
   `fragmented-product-experience_*`. Mỗi bước ghi lại session id đã sinh ra nó, và mọi session id ấy đều do app tạo
   (`Sessions.created_here`, `cos_baodo/sessions.py:160`). Mỗi bước chọn được manual hoặc
   autonomous. Ít nhất **2** bước — `impl` và `PR` — chạy autonomous thật: agent sửa file
   trong workspace và mở được một PR, không ai gõ lệnh.
2. **Live và timeline.** Trong lúc một bước autonomous đang chạy, board hiện tiến trình của
   nó **không cần reload trang**. Timeline của `fragmented-product-experience_*` liệt kê đủ **8** bước theo thứ tự
   thời gian, mỗi bước có mốc bắt đầu, mốc kết thúc và artifact nó sinh ra.
3. **Đếm được.** Mỗi bước hiện số token của nó, lấy từ `sdk.ResultMessage` chứ không ước
   lượng; tổng của `fragmented-product-experience_*` bằng tổng các bước; và tổng ấy khác **0**.

Kết quả này **sai** nếu đến hết ngày đó: unit `fragmented-product-experience_*` không tồn tại; hoặc artifact nào của
nó không có session id đứng sau trên board (nghĩa là nó được gõ ở terminal); hoặc `impl` và
`PR` vẫn phải gõ tay; hoặc board thiếu bước nào trong 8; hoặc phải F5 mới thấy tiến trình;
hoặc timeline thiếu mốc; hoặc bước nào không có số token; hoặc tổng lệch với các phần; hoặc
tổng bằng 0.

Số **8** lấy từ chính lời tác giả trích ở đầu file. Số **2** là mức tối thiểu để chữ
"autonomous" có nghĩa: `impl` và `PR` là hai bước duy nhất trong tám bước buộc phải ghi ra
ngoài `.cos/`, nên chúng là chỗ hàng rào chat-only thật sự chặn. Hạn **2026-09-28** là lựa
chọn của tác giả; nó có cơ sở trong repo chứ không phải lạc quan suông — toàn bộ **62**
commit của repo đều mang ngày `2026-09-21` (`git log --format=%ad --date=short`), tức 7 unit
dựng trong một ngày, và `0002` đặt hạn 2026-09-28 rồi đóng ngay hôm mở.

**Ba mệnh đề là một lần kéo giãn có chủ ý của invariant 3**, vốn đòi đúng một kết quả. Tác
giả được hỏi và chọn gộp. `0002` đã trả giá này một lần và ghi lại: hỏng một mắt là hỏng cả
ba, và không có cách nào đọc kết quả để biết phần nào đứng được. Lần này giá ấy cao hơn, vì
mệnh đề 1 còn đòi lật một quyết định an toàn — xem `## Constraints`. Ghi ra để người đọc sau
biết đây là lựa chọn, không phải sơ suất.

## Affected users and systems

- **Người dùng:** duy nhất tác giả, một máy, loopback, không đăng nhập. Không phân phối.
- **`cos_baodo/cos_baodo.py` (267 dòng)** — trang hiện tại. Board, timeline và danh sách tin
  nhắn không nhét vừa hình dạng này; đây là phần bị viết lại nhiều nhất.
- **`cos_baodo/sessions.py:190-244`** — `stream` phải giữ lại `ResultMessage` thay vì lấy
  mỗi `session_id`, và payload `done` (3 trường hôm nay) phải mang số.
- **`cos_baodo/config.py:38-52`** — bốn knob. Knob 1 là câu chặn bước autonomous.
- **`.claude/scripts/cos.mjs:117-140`** — `checkGate` biết 3 tên giai đoạn; tám bước thì nó
  trả "unknown stage" cho bốn cái.
- **`.claude/skills/` và `.claude/harness.md`** — bốn bước mới cần luật của chúng, và
  `harness.md:12-13` đang tự giới hạn ở ba giai đoạn đầu. Dòng đó sẽ thành sai.
- **`.cos/` trở thành thứ app ghi vào, không chỉ thứ người gõ.** Đây là lần đầu một tiến
  trình khác `git` và trình soạn thảo viết artifact. Luật ngôn ngữ của `.claude/CLAUDE.md`
  (tên file và heading tiếng Anh, prose tiếng Việt) áp cho cả artifact do board sinh ra.
- **`chat-only-sessions-have-tools`** — plan đã accepted (`2d6e0dc`), chưa có code. Unit này mở tool cho session; đó
  là hướng ngược với cái `chat-only-sessions-have-tools` vừa được cho phép làm. Hai unit gặp nhau, và thứ tự làm
  quyết định cái nào đúng.
- **`sessions-invisible-across-processes`** — chưa có `plan.md`. Nó chặn `pull` khi có session sống, và chỉ thấy tiến trình
  của chính nó. Một bước autonomous chạy dài làm cửa sổ ấy rộng ra.
- **`git` và mạng, ở mức mới.** Hôm nay app chỉ `clone` và `pull` (`cos_baodo/gitops.py`).
  `PR` cần branch, commit, push và một remote. `.claude/harness.md` ghi tác giả làm một mình
  và commit thẳng `main`; bước PR không có chỗ đứng trong mô tả đó.
- **Năm lệnh chứng minh** — `verify_0001`, `verify_0002`, `verify_0003`, `verify_0004`, và
  lệnh của `chat-only-sessions-have-tools` nếu nó làm xong trước. `verify_0003` còn là lệnh duy nhất mở trình duyệt
  và là lệnh duy nhất cần `COS_PORT` trống.
- **`uv run cos-build`** — dấu vân tay bundle. Trang đổi nhiều thì đây là bước dễ quên nhất,
  và `.claude/CLAUDE.md` đã ghi vì sao quên nó thì mọi phép kiểm xanh vô nghĩa.

## Constraints

**Những ràng buộc đánh dấu (tác giả) là lựa chọn của người đặt hàng, không suy ra từ vấn đề.**
Spec không được lật chúng; spec chỉ được nói chúng tốn gì.

- **(tác giả) Autonomous nằm trong unit này.** Session của board được phép có tool ghi và
  chạy `git`. Đây là lật tư thế `cos_baodo/config.py:44`, và phải lật ra mặt.
- **(tác giả) Ba mệnh đề đi chung, hạn 2026-09-28.** Không tách thành ba unit.
- **(tác giả) Chưa có auth.** Loopback, một người dùng, không TLS, không đăng nhập.
- **(tác giả) Giao diện phải đạt một sàn craft đo được.** Thêm ngày 2026-09-21. Hôm nay
  app **chưa khai báo theme bao giờ**: `cos_baodo/cos_baodo.py:266` là
  `rx.App(api_transformer=_api)`, không `theme=`, không `style=`, và trong cả gói không có
  một lần dùng `rx.theme`, `rx.color`, `color_mode` hay `breakpoints` nào. Trang đang chạy
  trên mặc định của thư viện, chỉ light, không dark, không responsive. Sàn tối thiểu: theme
  khai báo tường minh; đổi được light/dark; không tràn ngang ở ba bề rộng màn hình; tương
  phản chữ thân bài đạt **4.5:1** (WCAG 2.1 AA — *nguồn ngoài repo, unverifiable ở đây*).
  **"Đẹp" thì vẫn không đo được, và ràng buộc này không giả vờ đo nó** — xem
  `## Open questions` mục 10, vẫn mở.
- **Mở tool không được làm `chat-only-sessions-have-tools` thành vô nghĩa.** `chat-only-sessions-have-tools` tồn tại vì mặc định đang nói dối.
  Việc mở phải là một lựa chọn hiện ra ở từng bước, không phải một công tắc toàn cục bật sẵn,
  và mặc định của session chat vẫn phải là không tool. Một bản sửa làm `chat-only-sessions-have-tools` không còn kiểm
  được gì là hỏng theo kiểu khác.
- **Knob 3 `bypass_permissions` không được bật để bù.** Nó là hàng rào khác, và
  `cos_baodo/config.py:49` ghi nó không đặt được qua HTTP.
- **`COS_WORKING_DIR` vẫn không đặt được qua HTTP**, và workspace vẫn là *tên*, không phải
  đường dẫn. Board không được mở đường vòng tới cái root đó.
- **Không có kho sự thật thứ hai cho artifact.** `.claude/harness.md` chốt chuỗi commit là
  audit trail. Board đọc và ghi `.cos/`; nó không được giữ một bản sao song song của nội dung
  artifact mà git không thấy.
- **Năm lệnh chứng minh cũ phải còn xanh**, giữ nguyên những mệnh đề chúng đang khẳng định.
- **`npm test` không cần trình duyệt và không cần toolchain JavaScript.**
  `.claude/CLAUDE.md` nói rõ đó là chủ ý.
- **`uv run cos-build` là cách build duy nhất**, và cổng vẫn bị nướng vào bundle.
- **Ngoài phạm vi:** nhiều người dùng, đăng nhập, TLS, triển khai ra khỏi máy này, mobile,
  và mọi bước SDLC sau `ship`.

## Open questions

1. **PR đi đâu?** Repo commit thẳng `main` (`.claude/harness.md`), chưa có branch flow, chưa
   có remote nào trong luật. "Mở PR" cần cả ba thứ đó, và cần credential mà app loopback
   không có đường hỏi — đúng câu hỏi `0002` mở ra cho clone repo riêng tư và chưa ai trả lời.
2. **Làm `0005` trước hay `chat-only-sessions-have-tools` trước?** `chat-only-sessions-have-tools` đóng cửa, `0005` mở cửa, và cả hai đều đã
   được cho phép. Làm sai thứ tự thì một trong hai unit được ghi là xong trong khi thứ nó
   khẳng định không còn đúng.
3. **`idea`, `PR`, `review`, `ship` ghi ra artifact gì?** Bốn bước này không có file nào hôm
   nay. Không có artifact thì bước sau không có gì để đọc, và cả chuỗi "bước sau dùng thông
   tin bước trước" hỏng ở đó. Chúng có thuộc `.cos/NNNN_*/` không — trong khi
   `.claude/harness.md` nói thư mục đó chỉ chứa artifact của unit và không gì khác?
4. **`review` là ai review?** `.claude/harness.md` nói thẳng không còn bước duyệt nào, và
   `accepted` là chữ agent tự viết về việc của chính nó. Một cột `review` trên board mà agent
   tự tick là đang vẽ lại đúng chỗ trống ấy dưới dạng một ô xanh.
5. **Trạng thái board ở đâu?** Status suy ra từ file trong `.cos/` mỗi lần đọc (như
   `cos.mjs status` đang làm), hay lưu riêng? Lưu riêng thì có hai nguồn sự thật và chúng sẽ
   lệch; suy ra thì các bước không có artifact (câu 3) không có trạng thái nào để hiện.
6. **`ResultMessage` cho đúng những gì?** Có `usage` và `cost` không, đơn vị gì, cache token
   tính ra sao, và số ấy có cộng dồn qua các lượt của cùng một session không.
   **Unverifiable:** chưa đo; `cos_baodo/sessions.py:228` mới chỉ chạm vào nó.
7. **Live view chạy qua đường nào?** Trang do Reflex vẽ đi qua WebSocket `/_event`. Đây vẫn
   là open question 8 của `0002`, chưa đóng: đường người dùng thật sự bấm là đường ít bằng
   chứng nhất, và `verify_0003` mới chỉ chạm một lần bằng trình duyệt thật.
8. **Một bước autonomous chạy bao lâu, và bỏ dở thì sao?** Không có timeout, không có huỷ,
   không có phục hồi sau khi app tắt giữa chừng trong bất cứ chỗ nào của repo hôm nay.
9. **Hai bản app trên cùng working folder vẫn nhìn xuyên qua nhau** — `0004` C2, và
   `sessions-invisible-across-processes` chưa có plan. Board ghi vào `.cos/` làm vùng va chạm rộng hơn hẳn so với lúc chỉ có
   danh sách workspace.
10. **"UI như SaaS" đo bằng gì?** Câu "thà dùng terminal" là đánh giá, không phải phép đo, và
    ba mệnh đề của kết quả không có mệnh đề nào bắt trang phải đẹp hay dễ dùng. Một bản đủ
    xấu vẫn có thể làm kết quả này xanh.
