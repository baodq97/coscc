# Plan: Widen the harness to eight stages, move state, then let two steps run themselves
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

> **`done` ở đây là một bypass, đặt tay ngày 2026-09-22 theo lệnh của người khởi xướng.**
> Ba stage `pr`, `review`, `ship` của unit này **chưa bao giờ chạy** — các ô tương ứng trong
> `cos.mjs status` trống, và chúng trống vì đúng như vậy. Unit không đi qua chúng; nó được
> tuyên bố đóng.
>
> Lối tắt là `.claude/scripts/cos.mjs:123`: `nextAction` thấy `plan.md: done` thì trả
> `finished` ngay và không đọc ba stage sau. Đó **chính là** lối tắt mà `0005` mở rộng vòng
> lặp từ ba lên tám stage để bịt, và `.claude/CLAUDE.md` gọi tên nó — *"`plan.md: done` is
> terminal, which is what kept the five units closed under the old three-stage loop reading
> as finished"*. Nó được dùng lại ở đây một cách có ý thức, không do nhầm.
>
> **Vì sao không đi qua vòng lặp cho đúng:** `cos.mjs:31` cho stage `pr` đúng ba status —
> `draft`, `accepted`, `rejected` — và **không có `skipped`**, trong khi `spec` thì có. Bốn
> unit này hoàn thành trước khi repo có remote, nên không có pull request nào để ghi, và
> `write-pr` invariant 4 bắt ghi `draft` khi không mở được PR. `draft` thì không mở được
> `gate review`. Không có đường ra nào khác ngoài sửa `cos.mjs`, và `0009 spec.md`
> `## Out of scope` đã để việc đó ra ngoài phạm vi.
>
> **Điều này không đúng với `write-plan` invariant 9**, vốn đòi `done` chỉ được đặt sau khi
> công việc đã ship và lệnh ở `## Proof` đã pass. Công việc **đã** ship — code của cả bốn
> unit nằm trên `main` — nhưng `ship.md` thì không tồn tại, và proof của unit này không được
> chạy lại vào ngày đặt `done`.
>
> `0009` là unit đầu tiên đi hết tám stage thật. Từ `0010` trở đi có pull request thật, nên
> bức tường này không gặp lại.


> Tác giả chốt giữa chừng: **"đây là state moving thôi."** Board trước hết là một máy
> chuyển trạng thái trên tám giai đoạn, không phải một hệ thống mới. Thứ tự dưới đây theo
> đúng câu đó: phần dịch chuyển trạng thái lên trước và đứng được một mình; autonomous và
> PR — hai thứ đắt nhất và rủi ro nhất — xuống cuối, nơi chúng hỏng mà không kéo theo gì.
>
> Rồi tác giả thêm: **"lưu ý làm ui/ux nữa", "tôi muốn có ux/ui đẹp xin mịn"**. Yêu cầu
> ấy đến sau khi spec đã accepted, và `spec.md ## Out of scope` đang loại trừ đúng nó —
> nên nó **không** được nhét thẳng vào plan. `intent.md` nhận một ràng buộc mới và
> `spec.md` mọc R22–R25 trước, ra mặt; bước 5 dưới đây mới là chỗ plan thực thi chúng.

## Files that change

**Có sẵn:**

- `.claude/scripts/cos.mjs` — `ARTIFACTS` (`:14`) 3 → 8, `VALID` (`:16-20`), `checkGate`
  (`:118-142`), `nextAction` (`:85-115`), bảng của `cmdStatus` (`:154-181`).
- `.claude/scripts/cos.test.mjs` — 102 dòng, kiểm đúng những hàm trên.
- `cos_baodo/sessions.py` — `_options` (`:110-127`, bỏ `max_turns=1` cứng ở `:125`),
  `stream` (`:190-244`), payload `done` (`:244`).
- `cos_baodo/service.py` — thêm lối gọi board; không route nào và không handler nào được
  tự quyết (`service.py:1-13`).
- `cos_baodo/api.py` — thêm route board, theo đúng khuôn NDJSON của `/api/send`
  (`:131-168`).
- `cos_baodo/cos_baodo.py` — trang: theme và style toàn cục ở `rx.App` (`:266`), nút đổi
  light/dark, khung responsive, rồi board, timeline, danh sách tin nhắn.
- `cos_baodo/config.py` — **chỉ** docstring/chú thích knob 1 (`:44`) nói rõ nó là mặc định
  của app chứ không phải của board. Không thêm knob.
- `.claude/CLAUDE.md` — mục "The loop" và "Invariants" đang mô tả ba artifact.
- `.claude/harness.md` — bảng loop (`:30-34`), phạm vi ba giai đoạn (`:12-13`), và câu
  "driven by hand" (`:16`).
- `scripts/verify_0003.py` — thêm mệnh đề live/không-reload (spec R20).
- `package.json` — không đổi. Ghi ra để không ai phải đoán.

**Mới:**

- `cos_baodo/board.py` (new) — đọc `.cos/` của một workspace qua `cos.mjs status --json`.
- `cos_baodo/board_test.py` (new)
- `cos_baodo/policy.py` (new) — `StagePolicy`, bảng tra (giai đoạn, chế độ) → quyền.
- `cos_baodo/policy_test.py` (new)
- `cos_baodo/journal.py` (new) — bản ghi append-only, khoá như `store.py:16-22`.
- `cos_baodo/journal_test.py` (new)
- `cos_baodo/runner.py` (new) — chạy một bước, phát sự kiện, trả số.
- `cos_baodo/runner_test.py` (new)
- `scripts/verify_0005.py` (new)
- `.claude/skills/write-idea/SKILL.md` (new)
- `.claude/skills/write-impl/SKILL.md` (new)
- `.claude/skills/write-pr/SKILL.md` (new)
- `.claude/skills/write-review/SKILL.md` (new)
- `.claude/skills/write-ship/SKILL.md` (new)

## Order of work

Mỗi bước để lại repo ở trạng thái kiểm được. Không bước nào cần bước sau nó mới có nghĩa.

**1. Mở harness từ ba giai đoạn lên tám. Chỉ Node, không chạm Python.**
`ARTIFACTS` thành tám tên theo spec R2; `VALID` có danh sách status cho năm file mới
(`draft`/`accepted`/`rejected` cho cả năm, `impl.md` thêm `done`); `checkGate` nhận tám tên
và bắt mọi bước trước phải `settled`; `nextAction` đi hết tám; bảng của `cmdStatus` không
còn ba cột cố định. `cos.test.mjs` mở rộng cùng lúc.
*Kiểm:* `npm test` xanh; `cos.mjs status` in đủ 8 unit và không báo `unexpected file(s)`
cho năm tên mới; `cos.mjs gate 0005_hand-driven-invisible-loop pr` trả lời thay vì
`unknown stage`.

*Đã làm, và ba chỗ đi khác plan — ghi theo invariant 8:*
(a) **`implement` giữ làm alias của `impl`.** `.claude/skills/write-plan/SKILL.md` còn viết
tên cũ; bỏ nó là khoá gate của `sessions-invisible-across-processes` và `chat-only-sessions-have-tools`, đúng Risk 5. Một dòng bảng tra, có test.
(b) **`idea` là tuỳ chọn và không gác gì.** Bắt nó thành bắt buộc sẽ đánh dấu cả 8 unit
trên đĩa là dang dở, vì `readUnit` xưa nay vẫn đòi mỗi unit mở bằng một intent. Nó là chỗ
ghi một ý nghĩ có trước intent, và vắng mặt chỉ có nghĩa là không ai ghi.
(c) **`settled` nhận thêm `done`.** Trước đây chỉ `accepted`/`skipped`. Một `plan.md` đã
`done` là đi xa hơn `accepted`, nên nó không được chặn giai đoạn sau. Đổi lại,
`nextAction` giữ `plan.md: done` là **tận cùng**, để năm unit đã đóng không bị mở lại —
chúng vẫn đọc là `finished`.

**2. `Board` — đọc trạng thái vào Python, không chép luật.**
`board.py` gọi `cos.mjs status --json` bằng tiến trình con và dựng mô hình tám bước. Không
parse Markdown ở Python. Workspace không có `.claude/scripts/cos.mjs` thì trả board rỗng
kèm lý do, không ném lỗi.
*Kiểm:* `board_test.py` chạy trên chính `.cos/` của repo này, khẳng định 8 unit và 8 bước
mỗi unit, và khẳng định một thư mục không có harness trả rỗng có lý do.

*Đã làm, và một chỗ đi khác plan — ghi theo invariant 8:* **app không bao giờ chạy
`cos.mjs` của workspace.** Plan viết "workspace không có `.claude/scripts/cos.mjs` thì trả
board rỗng", tức là ngầm định workspace **có** thì chạy cái đó. Đó là lỗ hổng: workspace là
repo `clone` từ một URL người ta gõ (`0002`), nên file ấy là code của repo đó, và chạy nó
trao cho một repo lạ mọi thứ tiến trình này có — vượt qua toàn bộ knob ở
`cos_baodo/config.py`. Thay vào đó `cos.mjs` nhận cờ `--root <dir>`, và app chạy **bản của
chính nó** trỏ vào `.cos/` của workspace. Cái giá, ghi ra: một workspace dùng phiên bản
harness khác sẽ được đọc bằng danh sách giai đoạn của app này, không phải của nó. Có test
trồng một `cos.mjs` độc trong workspace và khẳng định nó không hề chạy.

**3. `Journal` — chỗ của thứ không phải artifact.**
`journal.py` dùng lại nguyên cơ chế đã trả giá ở `0004`: ghi temp rồi `rename`, `flock`
trên file riêng, chờ có trần (`cos_baodo/store.py:16-22`, `:42-49`). Ghi chế độ mỗi bước,
mốc thời gian, session id, số token, số lần từ chối tool.
*Kiểm:* `journal_test.py` gồm một ca nhiều tiến trình cùng ghi — cùng hình dạng phép đo của
`scripts/verify_0004.py` nhưng ở mức unit test — và khẳng định không mất mục.

*Đã làm, và một chỗ đi khác plan — ghi theo invariant 8:* **không dùng temp-rename, dùng
`O_APPEND`.** Plan viết "ghi temp rồi rename" vì đó là cách `store.py` làm. Nhưng store
thay cả file mỗi lần ghi, còn journal chỉ nối thêm một dòng; temp-rename ở đây sẽ chép lại
toàn bộ log mỗi sự kiện. Quan trọng hơn: cái `0004` đo được là mất mát do **đọc-rồi-ghi xen
kẽ**, và một phép nối không có bước đọc, nên lớp lỗi đó vắng mặt về mặt cấu trúc chứ không
phải bị phòng thủ. `flock` vẫn giữ — nó đóng khung một bản ghi để hai người ghi không cài
răng lược nửa dòng, và cho người đọc một ảnh chụp nhất quán — nhưng nó là lớp thứ hai.
Test bốn tiến trình × 5 bản ghi: đủ 20, và mỗi dòng đều parse được.

**4. Board lên API, chỉ đọc cộng đổi chế độ.**
`service.py` thêm `board(cwd)` và `set_mode(cwd, unit, stage, mode)`; `api.py` thêm
`GET /api/board` và `POST /api/board/mode`. Route không quyết gì (`api.py:12-13`).
*Kiểm:* test `httpx.ASGITransport` in-process, cùng kiểu `api_test.py` đang dùng; chế độ
sống qua việc dựng lại `Service`.

**5. Nền giao diện: theme, light/dark, và khung responsive. Trước khi vẽ board.**
Đây là chỗ R22–R25 sống. Hôm nay `cos_baodo/cos_baodo.py:266` là
`rx.App(api_transformer=_api)` — không `theme=`, không `style=`, và cả gói không có
một lần dùng `rx.theme`, `rx.color`, `color_mode` hay `breakpoints` nào. Bước này
khai báo theme tường minh
(`accent_color`, `gray_color`, `radius`, `scaling`), thêm nút đổi light/dark bằng
`toggle_color_mode`, dựng một bộ style toàn cục ở `rx.App(style=...)` cho typography và
khoảng cách thay vì rải prop từng chỗ, và chuyển mọi màu đang hardcode sang `rx.color(...)`
để chúng theo mode. Khung trang dùng `rx.breakpoints` ở ba mốc của R24.
Làm trước bước 6 vì board vẽ trên nền này; làm sau thì phải sửa hai lần.
*Kiểm:* `uv run cos-build` chạy được; trang render ở `390px`, `768px`, `1280px` không tràn
ngang; đổi light/dark được và lựa chọn sống qua reload.

*Đã làm. Bốn chỗ đi khác plan, ghi theo invariant 8:*
(a) **Thêm `cos_baodo/ui.py` (new)**, không có trong `## Files that change`. Theme, type
scale và các khối dựng chung nằm đó để bước 6 dùng lại; `cos_baodo.py` giữ phần hành vi.
(b) **Theme khai báo ở `rxconfig.py`, không ở `rx.App`.** Reflex 0.9.11 trả cảnh báo
deprecation cho `App(theme=...)` và nói nó biến mất ở 1.0, chỉ sang
`rx.plugins.RadixThemesPlugin` — đo ngày 2026-09-21 bằng chính lần build. Tài liệu trên web
vẫn dạy lối cũ; gói đã cài là nguồn thật. `rxconfig.py` cũng chưa có trong danh sách file.
(c) **Bốn phép kiểm R22–R25 vào `verify_0003.py` ngay bây giờ**, không đợi bước 11: chúng
là thứ chứng minh bước này, và để tới cuối thì bước này không có bằng chứng.
(d) **Thêm một "canary" cho phép đo tràn ngang.** Đo ngày 2026-09-21: xoá hẳn khung cuộn
của bảng mà phép kiểm vẫn xanh — cảnh test chỉ có 2 workspace tên ngắn nên không đủ rộng để
tràn. Phép kiểm vì thế tự chèn một khối 3000px và đòi bị bắt, trước khi được tin.

*Hai hồi quy phép kiểm bắt được:* trang vẽ lại làm mất dòng "N workspace(s)" mà
`scripts/verify_0003.py:248` chờ đúng chữ; và phép đo tương phản đầu tiên đọc `body`, nơi
Reflex không đặt màu gì — ra 1.00:1. Màu của theme nằm trên node `.radix-themes`, nên phép
đo chuyển sang chữ thật trên trang và lấy trường hợp tệ nhất.

**6. Trang: board và timeline. Đây là "state moving" nhìn thấy được.**
Tám cột, mỗi unit một hàng, mỗi ô một trạng thái; timeline của một unit theo spec R15. Vẫn
chưa chạy được bước nào — trang chỉ hiện và đổi chế độ.
*Kiểm:* `uv run cos-build` rồi `uv run python scripts/verify_0003.py` xanh; mắt thấy 8 unit,
8 cột.

*Đã làm.* Board vẽ 8 unit × 8 giai đoạn; bấm một unit mở panel có segmented control
manual/auto cho từng giai đoạn và bảng timeline. Đo bằng trình duyệt thật ngày 2026-09-22:
tràn ngang **0px** ở 390px, chế độ tối đúng.

*Ba chỗ đi khác plan, ghi theo invariant 8:*
(a) **Ô "not started" là một dấu chấm, không phải chữ.** Vẽ đủ chữ tám lần mỗi hàng đẩy cột
`next` — cột nói phải làm gì tiếp — ra khỏi mép thẻ ở 1280px. Chữ đầy đủ nằm trong tooltip.
(b) **Chế độ không nằm trong ô của bảng.** 64 control trong một lưới là một cái form, không
phải một cái board. Chúng ở trong panel mở ra khi chọn một unit; trên bảng chế độ
`autonomous` chỉ là một dấu tia chớp.
(c) **Tắt badge "Built with Reflex"** (`show_built_with_reflex=False`). Nó cố định ở góc
mọi màn hình và đè lên nội dung ở viewport thấp.

**7. Token: giữ lại thứ đang bị vứt.**
`stream` giữ `ResultMessage` thay vì lấy mỗi `session_id` (`sessions.py:228`); payload
`done` mang input/output/cache token, USD, số lượt, thời lượng (spec R16). Journal cộng
dồn, trang hiện tổng theo bước và theo unit.
*Kiểm:* unit test với `ResultMessage` dựng sẵn cho phép cộng; và một session thật qua
`verify_0005.py` cho R18 (tổng khác 0).

*Đã làm, và Risk 4 đóng bằng số đo thật.* Chạy hai lượt trên một client ngày 2026-09-21:

| | lượt 1 | lượt 2 |
|---|---|---|
| `usage.cache_read_input_tokens` | 1608 | 3904 |
| `model_usage.cacheReadInputTokens` | 1608 | **5512** |
| `total_cost_usd` | 0.016909 | **0.036336** |

`model_usage` và `total_cost_usd` **cộng dồn cả session**, không phải của lượt. Cộng chúng
qua từng lượt cho 7120 token cache-read trong khi session dùng 5512 — cao hơn 29%, và không
có gì trong con số ấy trông bất thường. Còn `usage` ở tầng trên **cũng không phải** câu trả
lời: nó chỉ là iteration cuối trong lượt (`input_tokens: 2` trong khi `model_usage` báo
1171). Nên chi phí của một lượt = **hiệu hai lần đọc cộng dồn**, và `Live.spent` giữ lần
đọc trước. `cos_baodo/cost_test.py` phát lại đúng hai con số này, gồm một test khẳng định
phép cộng ngây thơ cho 7120 chứ không phải 5512.

*Hai chỗ đi khác plan:* (a) thêm trường `cost_usd` dạng số thực bên cạnh các trường token
nguyên — một lượt tốn dưới một xu, `int()` sẽ báo mọi lượt như vậy là miễn phí; (b) số token
hiển thị gồm **cả cache read và cache creation** vì chúng đều bị tính tiền, hiển thị mỗi
input+output sẽ báo một session nặng cache là gần như không tốn gì.

**8. `StagePolicy` + `Runner`, sáu giai đoạn chữ, **không tool nào**.**
`policy.py` là bảng tra thuần hàm, không đọc env, không sửa được qua HTTP. Sáu giai đoạn
`idea`, `intent`, `spec`, `plan`, `review`, `ship` trả grant rỗng ở cả hai chế độ.
`runner.py` lắp prompt gồm `intent.md` cộng artifact của bước liền trước (spec R4), chạy
session, và **app ghi artifact từ văn bản agent trả về** — xem Risk 1, đây là chỗ plan này
đi khác một câu trong `spec.md`. Năm `SKILL.md` mới viết ở bước này, cùng khuôn với
`.claude/skills/write-intent/SKILL.md`.
*Kiểm:* chạy bước `spec` từ trang trên một unit nháp; `spec.md` xuất hiện với dòng
`Status:`; thông điệp `init` của session ấy báo **0 tool**.

*Đã làm.* Chạy thật ngày 2026-09-22 trên một unit nháp: board đọc 1 unit / 8 giai đoạn →
đặt `spec` thành `autonomous` → bước chạy → `spec.md` ra đời với `Status: accepted.`, prose
tiếng Việt heading tiếng Anh → board đọc lại báo `spec: accepted` → sổ ghi
**$0.154759**, 2901 in / 4488 out / 1608 cache-read / 2725 cache-create. Trả về
`included: ['intent.md']` — đó là R4, đo được chứ không phải tin.

*Ba chỗ đi khác plan, ghi theo invariant 8:*
(a) **`max_turns` thành tham số ngay ở bước này**, không đợi bước 9 — `Runner` cần trần từ
policy trước khi có bước autonomous nào. Mặc định vẫn 1, nên mọi caller cũ không đổi hành vi
(plan C6).
(b) **Thêm route `POST /api/board/run` và nút `run` trên panel**, cùng một ô hiện tiến trình
live. Plan xếp phần live ở bước 6, nhưng không có gì để hiện cho tới khi có thứ chạy được.
Handler dùng `rx.event(background=True)`: một generator event thường giữ khoá state suốt
lượt, nên một bước dài sẽ khoá cả trang — đúng cái R14 cấm.
(c) **`policy.GRANTS` rỗng.** Bước 9 và 10 đổ vào. Tới lúc này mọi giai đoạn ở mọi chế độ
đều là chat không tool, deny-by-default: một cặp (giai đoạn, chế độ) không có tên trong bảng
nhận `Grant()` rỗng, nên một giai đoạn nghĩ ra ngày mai là **khoá**, không phải mở.

*Một điều phải nói thẳng:* grant rỗng là thứ **app** kiểm soát. Session vẫn nhận MCP tool từ
cấu hình mức máy cho tới khi `chat-only-sessions-have-tools` được implement — đó là toàn bộ nội dung của `chat-only-sessions-have-tools`, và là
lý do `spec.md` C1 khuyến nghị làm nó trước. Mệnh đề "0 tool" của bước này chỉ đạt hoàn toàn
sau khi `chat-only-sessions-have-tools` xong.

**9. `impl` autonomous.**
Grant khác rỗng lần đầu: tool ghi và exec, giới hạn trong thư mục workspace. Cưỡng chế ở
`can_use_tool`, không chỉ ở danh sách truyền vào — lý do là phép đo của `chat-only-sessions-have-tools`. Trần 50
lượt và 5.00 USD (spec R11) gắn vào `StagePolicy`, **không** thay giá trị toàn cục.
`max_turns=1` ở `sessions.py:125` chuyển thành giá trị do policy cấp, mặc định vẫn 1.
*Kiểm:* một bước `impl` sửa được một file trong workspace nháp và ghi `impl.md`; một bước
`spec` chạy ngay sau đó vẫn báo 0 tool; mọi lần từ chối tool đếm được trong journal.

*Đã làm, chạy thật ngày 2026-09-22.* Workspace nháp có `greet.py` chỉ chứa `pass`. Bước
`impl` ở chế độ `autonomous` sửa nó thành `return "xin chao"`, ghi `impl.md`, tốn
**$0.447198 trong 19 lượt**, và **bị từ chối 4 lần**:

```
Bash: this step may not run 'echo'
Bash: this step may not run 'assert'
Write: writing outside the workspace is not allowed: /tmp/step9-…/proof.py
Bash: this step may not run 'rm'
```

Lần thứ ba là lần đáng giá: agent thử ghi ra **ngoài** workspace, vào đúng working folder
một cấp trên, và bị chặn. Ranh giới không phải lý thuyết.

*Hai lỗ của chính tôi mà lần chạy ấy lộ ra, đã vá:*
(a) **Chuyển hướng ra file lọt lưới.** `echo` bị cấm oan trong khi `echo x > /etc/foo` sẽ
**qua** được, vì phép tách không nhìn dấu `>`. Giờ mọi chuyển hướng ra file bị từ chối —
bước đã có `Write`/`Edit` để tạo file — và `echo`, `printf`, `test`, `which`, `pwd`,
`sort`, `uniq` vào danh sách cho phép.
(b) **`2>&1` bị chặn nhầm.** Dấu `&` trong nó bị đọc là dấu ngăn lệnh, nên `1` thành một
"lệnh" không được phép. Chuyển hướng giữa các mô tả tệp giờ được gỡ trước khi tách.

*Một chỗ đi khác plan:* `Runner` **không** tự ghi artifact cho `impl`, vì bước này có tool
và tự ghi được; `Runner` kiểm lại file có tồn tại và có dòng `Status:` không. Tin rằng nó đã
ghi mà không nhìn là cách một bước báo thành công cho một file không có thật.

*Giới hạn còn nguyên (Risk 3):* danh sách theo từ đầu không bó được `git` hay `npm` bị sai
khiến. `cos_baodo/policy_test.py` có một test **khẳng định** `git push --force` qua được
danh sách — viết ra thành test đang xanh chứ không để ngầm.

**10. `pr` autonomous. Bước duy nhất chạm ra ngoài máy.**
Grant exec giới hạn ở `git` và `gh`, luật trên chuỗi lệnh chốt tại đây (spec C3). Trang
hiện rõ, **trước khi bước chạy**, rằng bước này dùng credential `gh` mức máy (spec C4).
`gitops.py` không đổi.
*Kiểm:* mở được một PR thật. **Bước này không kiểm được cho tới khi repo có remote** — xem
Risk 2.

*Đã dựng, chưa chứng minh được.* `git remote -v` đo lại ngày 2026-09-22: **vẫn rỗng**. Nên
R7 không đạt, mệnh đề 1 không đạt, và vì ba mệnh đề đi chung nên **cả unit không đạt** — đúng
cái giá `intent.md` đã ghi khi chọn gộp. Tôi không tự tạo remote: đó là một hành động ra
ngoài máy và là quyết định của tác giả, không phải của bước này.

*Những gì đã dựng và kiểm được không cần remote:*
- Grant `("pr", "autonomous")`: `git` và `gh`, **không** `npm`/`uv` — bước này đề xuất một
  thay đổi đã có sẵn, nó không có lý do gì để build hay cài. Trần thấp hơn `impl`:
  30 lượt, $3.00.
- **Cảnh báo hiện ra trước khi bấm** (`spec.md` C4): panel hiện một callout nói bước này
  chạy `git`/`gh` bằng đăng nhập GitHub sẵn có của máy, và nó **với tới mọi repo tài khoản
  đó với tới, không riêng workspace này**. Mỗi ô có grant cũng có một biểu tượng chìa khoá
  liệt kê tool được cấp. Năng lực đến từ cấu hình mức máy phải nhìn thấy được **trong app** —
  đó là nguyên văn bài học của `chat-only-sessions-have-tools`.
- Test khẳng định `manual` không mang tool lẫn cảnh báo, và `impl` không mang cảnh báo của
  `pr`.

*Việc còn lại cho tác giả:* cấp một remote cho `cos-baodo`, rồi chạy bước `pr` một lần.

**11. `verify_0005.py`, rồi tài liệu.**
Lệnh chứng minh viết sau cùng vì nó khẳng định kết quả của cả chín bước trên. Rồi sửa
`.claude/CLAUDE.md` và `.claude/harness.md` cho khớp thực tế — tám giai đoạn, và câu
"driven by hand" (`harness.md:16`) không còn đúng với hai bước.
*Kiểm:* mục `## Proof` dưới đây.

*Đã dựng. Chạy ngày 2026-09-22, kết quả: **exit 1**.* Chi tiết, vì con số gộp giấu mất
chỗ nào đứng được:

- **Mệnh đề 1 — đạt.** `gate` trả lời đủ tám tên, từ chối `deploy`, `rollback` và tên rỗng.
  Trước đó nó phải chứng minh mình **biết nói không**: một unit rỗng bị chặn ở `spec`. Đây
  là bài học của `0003`, viết thành một dòng.
- **Mệnh đề 7 — đạt.** Một bước `impl` với `max_turns=1` chạm trần và trả `exhausted`, kèm
  lý do, không treo. Hạ trần là **sửa số trong bảng grant**, không phải nhánh code thứ hai.
- **Mệnh đề 5 — KHÔNG đạt, và đây là `chat-only-sessions-have-tools`.** Session của một giai đoạn chữ, grant rỗng,
  vẫn nhận `microsoft-learn` và `claude.ai Claude Docs` cùng ba tool `mcp__…`. `--tools`
  chỉ đặt tên cho tập built-in nên không trừ được MCP. `chat-only-sessions-have-tools` có plan accepted, chưa có
  dòng code nào. Mệnh đề này còn đỏ tới khi `chat-only-sessions-have-tools` xong.
- **Mệnh đề 2, 3, 4, 6 — bỏ qua, vì repo không có remote.** `git remote -v` vẫn rỗng
  (Risk 2). Script đòi `COS_PROOF_REPO` trỏ tới một repo mà tác giả **đồng ý** cho nó push
  và mở PR; không có mặc định, vì với ra ngoài máy là quyết định của tác giả.

*Một phát hiện đáng ghi hơn cả kết quả:* đo `tools` trong message `init` **không đáng tin
một mình**. Hai lần chạy liên tiếp, cùng máy cùng tham số: lần đầu ba tool, lần sau **không
tool nào**, trong khi cả hai lần đều có đúng hai MCP server gắn vào. Danh sách tool chạy đua
với lúc server kết nối xong, nên một lần đọc sớm sẽ báo "0 tool" cho một session không hề
rỗng — đúng kiểu xanh giả mà `0003` đã dạy. Mệnh đề 5 vì vậy hỏi **cả hai**: 0 tool **và**
0 server. Nếu chỉ giữ vế đầu thì `0005` đã tự cấp cho mình một dấu xanh.

## Risks

Xếp theo bán kính, rộng nhất trước.

**1. Plan này đi khác `spec.md` ở một câu, và câu đó tự mâu thuẫn.**
`spec.md ## Design` viết "Runner không tự viết artifact. Agent trong session viết, bằng tool
ghi." Nhưng R9 của cùng file cho sáu giai đoạn chữ grant **rỗng ở cả hai chế độ** — một
session không tool thì không ghi được file. Hai câu không cùng đúng được. Plan này chọn:
**app cầm bút cho artifact trong `.cos/`, agent chỉ trả về văn bản**, vì nó giữ được R9,
giữ được mặc định 0 tool, và giữ cho `chat-only-sessions-have-tools` còn kiểm được. Câu trong `## Design` là sai và
được ghi nhận sai **ở đây**, không phải bằng cách sửa lén một artifact đã `accepted`.
*Dấu hiệu nó vỡ:* bước `spec` chạy mà session báo khác 0 tool.

**2. Repo không có remote, nên bước 10 không có chỗ đi.** `git remote -v` rỗng ngày
2026-09-21 (`spec.md` C2). Bước 1–9 và 11 không phụ thuộc điều này; **chỉ bước 10 chết**. Nếu
đến lúc làm bước 10 mà vẫn chưa có remote, thì R7 không đạt, mệnh đề 1 không đạt, và vì ba
mệnh đề đi chung nên **cả unit không đạt** dù chín phần mười đã chạy. Đây là cái giá của
việc gộp mà `intent.md` đã ghi. *Cần tác giả cấp một remote trước bước 10.*

**3. Grant exec của bước 10 rộng hơn ý định.** "Giới hạn ở `git` và `gh`" là một câu, không
phải một luật. `gh api` gọi được toàn bộ REST API của tài khoản `baodq97`, tới mọi repo tài
khoản ấy với tới. *Dấu hiệu nó vỡ:* journal ghi một lệnh `gh` chạm repo không phải workspace
đang mở. Luật phải chặn theo danh sách lệnh con cho phép, không theo tên nhị phân.

**4. Số token có thể cộng sai.** Chưa đo `ResultMessage.usage` là của lượt hay cộng dồn cả
session (`spec.md` open question 6). Cộng nhầm thì R17 xanh trong khi số sai — kiểu hỏng tệ
nhất, vì nó trông như đã đo. *Dấu hiệu:* chạy hai lượt trong một session, tổng phải bằng
tổng hai lượt chứ không phải gấp đôi lượt sau. Kiểm ở bước 7, không để tới bước 11.

**5. Bước 1 đổi `cos.mjs`, và `cos.mjs` là thứ gate của mọi unit khác đang đọc.** Làm hỏng
nó là khoá cả `sessions-invisible-across-processes` và `chat-only-sessions-have-tools` lại. *Dấu hiệu:* `cos.mjs status` báo problem cho một unit
vốn sạch.

**6. Board gọi Node cho mỗi lần đọc (spec C8).** App Python có phụ thuộc lúc chạy vào
`.claude/scripts/cos.mjs`. Nếu chậm hoặc thiếu thì trang trống. *Dấu hiệu:* board rỗng trên
một workspace có `.cos/`.

**7. Bước 9 và 10 cùng mở tool trong lúc `chat-only-sessions-have-tools` chưa implement.** `chat-only-sessions-have-tools` có plan accepted và
chưa có dòng code nào. `spec.md` C1 khuyến nghị làm `chat-only-sessions-have-tools` trước; plan này không ép, nhưng
nếu `0005` xong trước thì phép kiểm của `chat-only-sessions-have-tools` phải chứng minh thêm rằng board không mở
đường vòng.

**8. `0004` C2 vẫn mở, và journal làm nó rộng ra.** Hai bản app trên một working folder vẫn
nhìn xuyên nhau. Journal ghi thường xuyên hơn hẳn danh sách workspace.

## Proof

Một lệnh, chạy sau bước 11:

```
uv run cos-build && uv run python scripts/verify_0005.py
```

Đạt là **exit 0**. Mã thoát theo đúng quy ước `scripts/verify_0003.py:49`: `0` đạt, `1`
hỏng, `2` môi trường chưa sẵn sàng (không có remote, không có `gh`, cổng bận).

`verify_0005.py` phải trả non-zero nếu bất kỳ mệnh đề nào dưới đây hỏng:

1. `cos.mjs gate` trả lời đủ tám tên giai đoạn, và từ chối tên thứ chín.
2. Một unit nháp đi từ `idea` tới `ship`, mọi bước chạy qua board, mọi bước có session id
   và `created_here` trả `True`.
3. Prompt của mỗi bước có chứa artifact của bước liền trước (spec R4).
4. Bước `impl` sửa được file trong workspace nháp; bước `pr` mở được PR.
5. Một bước bất kỳ trong sáu giai đoạn chữ báo **0 tool** trong `init`.
6. Tổng token của unit nháp khác 0, và bằng tổng các bước.
7. Vượt trần lượt cho ra trạng thái `exhausted`, không phải treo.

Thiếu remote **không** làm cả lệnh im. Mệnh đề 1, 5 và 7 không cần remote nên vẫn chạy và
vẫn in phán quyết; chỉ 2, 3, 4, 6 bị bỏ qua. Đó là câu trong Risk 2 — "chín phần mười vẫn
chạy" — viết thành cơ chế thay vì để làm lời hứa.

Bốn mệnh đề giao diện (R22–R25) **không** nằm trong `verify_0005.py`, vì chúng cần một
trình duyệt thật. Chúng vào `scripts/verify_0003.py`, cùng chỗ với mệnh đề live của R20:

8. `rx.App` có `theme=` khai báo tường minh (đọc được từ DOM: thuộc tính của node theme).
9. Render ở **390px**, **768px**, **1280px**: `scrollWidth` không vượt `clientWidth` của
   `documentElement` ở bề rộng nào.
10. Bấm nút đổi mode: `appearance` đổi, và sau một lần reload nó vẫn giữ giá trị mới.
11. Tương phản chữ thân bài ≥ **4.5:1** ở cả hai mode, tính từ màu nền và màu chữ đọc
    được qua `getComputedStyle`.

Ngoài lệnh trên, phải cùng xanh:

```
npm test
uv run python scripts/verify_0001.py
uv run python scripts/verify_0002.py
uv run python scripts/verify_0003.py
uv run python scripts/verify_0004.py
```

`verify_0003.py` là chỗ duy nhất kiểm được mệnh đề 2 của intent (live, không reload), vì nó
là lệnh duy nhất mở trình duyệt thật. Mệnh đề ấy **không** được tính là đạt bằng bất kỳ phép
kiểm mức HTTP nào (spec R20).

## What this plan does not do

- **Không tự kích hoạt bước sau khi bước trước xong.** `docs/ai-native-sdlc-playbook.md:63`
  mô tả trạng thái đích là mỗi artifact accepted tự châm gate kế tiếp. Plan này dựng chỗ để
  bấm, không dựng cái bấm hộ — và `spec.md ## Out of scope` đã chốt vậy. Người vẫn chọn chế
  độ và khởi động từng bước.
- **Không mở `gitops.py`.** Không `branch`, `commit`, `push` ở tầng Python. Việc đó nằm
  trong grant của bước 9, nơi nó nhìn thấy được, thay vì thành một năng lực thường trực của
  app.
- **Không thêm knob thứ năm.** Quyền nằm trong `policy.py`, tách khỏi `Config`, đúng lý do
  `spec.md ## Design` nêu: knob 1 phải tiếp tục chỉ nói về một thứ.
- **Không sửa `0004` C2 và không đụng `sessions-invisible-across-processes`.** Chúng là unit khác.
- **Không hứa trang sẽ đẹp.** Bước 5 dựng *sàn* — theme, dark/light, ba bề rộng, tương
  phản AA — và bốn thứ đó kiểm được. Phần còn lại của chữ "xịn mịn" thì không: cả mười một
  bước trên xanh được với một trang vẫn rối. `spec.md` C7 ghi ai quyết phần ấy, và ghi rằng
  muốn nó **đỏ** được thì phải có intent riêng. Bịa một số đo cho thẩm mỹ ở đây sẽ tệ hơn
  là nói thẳng chỗ nào không đo.
