# Spec: Workspace management on a Reflex/FastAPI stack with no hand-written HTML
Intent: intent.md. Author: Bao Do. Status: accepted.

> **Viết lại ngày 2026-09-21.** Bản đầu (`06f2e6e`) chỉ nói về workspace trên nền aiohttp.
> `intent.md` đã được tác giả mở rộng sang nền và giao diện; file này thay bản đó.

## Requirements

Ba nhóm, đúng ba mệnh đề của `intent.md:49-59`. Nhóm A là mệnh đề 1 (nền đã chuyển), nhóm B
là mệnh đề 2 (không còn HTML viết tay), nhóm C là mệnh đề 3 (workspace quản lý được).

### A. Nền

**R1 — Layout phẳng, một gói.** Gói Python tên `cos_baodo` nằm ở gốc repo, cạnh
`rxconfig.py` với `app_name="cos_baodo"`. Thư mục `app/` không còn tồn tại. Kiểm được:
`app/` vắng mặt trong `git ls-files`, và `import cos_baodo` chạy.

**R2 — uv theo chuẩn hiện hành.** `pyproject.toml` có `[dependency-groups]` (không dùng
`[tool.uv.dev-dependencies]`), có `[project.scripts]` cho lệnh chạy app, và repo có
`.python-version`. `uv.lock` và lockfile frontend của Reflex được commit; `.web/` nằm trong
`.gitignore`. Kiểm được: `uv sync --locked` chạy sạch trên cây vừa clone.

**R3 — `npm test` vẫn là một lệnh chạy hết.** `test:python` không còn khoá cứng `-s app`.
Sau unit này `.claude/CLAUDE.md:20-21` phải mô tả đúng lệnh thật. Kiểm được: `npm test`
xanh, và thêm một file `*_test.py` mới vào gói vẫn được nhặt.

**R4 — JSON API và trang nằm cùng một tiến trình.** API mount vào Reflex qua
`api_transformer`. Không route nào của app dùng `/ping/`, `/_event` hay `/_upload` — Reflex
giữ ba đường đó. Kiểm được: một tiến trình duy nhất phục vụ cả `/api/*` lẫn trang; gọi
`/api/workspaces` và `/ping/` trên cùng cổng backend đều trả lời.

**R5 — Cả hai cổng của Reflex chỉ loopback.** Reflex phục vụ frontend và backend trên hai
cổng khác nhau. `0001` R5 chỉ nói về một. Cả hai phải bind `127.0.0.1`. Kiểm được: `ss -ltn`
không thấy `0.0.0.0` hay `::` trên cổng nào của app.

**R6 — Lệnh kiểm của `0001` giữ nguyên mệnh đề.** `scripts/verify_0001.py` được đổi thư
viện client và đường import; **các mệnh đề nó khẳng định không đổi, không nới, không bớt**.
Kiểm được: đọc diff — mọi thay đổi phải là client hoặc import; và nó xanh.

**R7 — `terminal-only-access` không bị đụng.** `channel/`, `evidence/0001_terminal-only-access/` và
`scripts/verify-0001.mjs` giữ nguyên, kể cả `channel/public/index.html` — nó là HTML viết
tay nhưng **không** phải trang của app, nên R8 không áp lên nó.

### B. Giao diện

**R8 — Không còn HTML hay CSS viết tay cho trang của app.** `app/public/index.html` (151
dòng) bị xoá và không có file cùng loại thay thế. Kiểm được: sau unit này, không file
`.html` hay `.css` nào trong repo phục vụ giao diện của app; file `.html` duy nhất còn lại
là `channel/public/index.html` của `terminal-only-access` (R7). Tài nguyên tĩnh dạng ảnh/font trong
`assets/` không tính.

**R9 — Giao diện có đủ phần để dùng.** Dựng bằng component Python: danh sách workspace kèm
label và cờ `missing`; chỗ nhập để thêm hoặc clone; chỗ xoá; chỗ pull; và khung hội thoại
của `0001`. Kiểm được: mỗi phần gọi xuống đúng lớp dịch vụ ở `## Design`, và không phần nào
gọi thẳng `git` hay đĩa.

**R10 — Một lớp dịch vụ, hai lối vào.** Trang (event handler của Reflex) và JSON API
**không** được hiện thực song song. Cả hai gọi cùng một lớp dịch vụ; route HTTP là vỏ mỏng.
Kiểm được: không có logic nghiệp vụ nào nằm trong route hay trong event handler — thấy một
nhánh xử lý ở một bên mà bên kia không có là hỏng.

### C. Workspace

**R11 — Working folder là một gốc, khai báo bằng env.** `COS_WORKING_DIR` đọc trong
`from_env` và không ở đâu khác. Không route, event handler hay component nào đặt được nó.
Kiểm được: không có setter ngoài `from_env`; một request gửi kèm `working_dir` bị bỏ qua
hoàn toàn, không phải bị ghi đè.

**R12 — Workspace định danh bằng `name`, không bằng đường dẫn.** `name` là **một đoạn
đường dẫn duy nhất**: chỉ `[A-Za-z0-9._-]`, dài 1–64 ký tự, không phải `.` hay `..`. Đường
dẫn thật luôn là `working_dir / name` và **không bao giờ** đến từ request. Kiểm được:
`../x`, `/etc`, `a/b`, chuỗi rỗng, tên 65 ký tự — tất cả bị từ chối 400, và không input nào
tạo ra được đường dẫn ngoài `working_dir`.

**R13 — Đếm và liệt kê.** Một đường đọc trả về số workspace và danh sách, mỗi mục gồm
`name`, đường dẫn, `label`, nguồn (`env` hay `store`), và cờ `missing`. Kiểm được: số trả
về khớp đúng 2 → 1 trong chuỗi đo.

**R14 — Thêm.** Hai cách, cùng một đường: kèm `repo_url` thì clone; không kèm thì nhận một
thư mục đã có sẵn dưới `working_dir`. Cả hai được `intent.md` cho phép — "thêm" và "clone"
là hai mục riêng trên dòng phạm vi.

**R15 — Clone chỉ `https://`, không tương tác, môi trường tối thiểu.** `git` gọi bằng argv
(không qua shell), subcommand cố định, không nhận cờ từ người dùng, `repo_url` phải bắt đầu
bằng `https://` và không bắt đầu bằng `-`. Tiến trình `git` nhận môi trường **dựng mới**:
`PATH`, `HOME`, `GIT_TERMINAL_PROMPT=0`, và một `GIT_ASKPASS` luôn thất bại. Kiểm được:
repo riêng tư trả lỗi trong hạn thời gian thay vì treo; `CLAUDE_CODE_OAUTH_TOKEN` và mọi
biến `COS_*` không có trong môi trường tiến trình con.

**R16 — Clone hoặc trọn vẹn, hoặc không để lại gì.** Clone vào thư mục tạm dưới
`working_dir` rồi mới đổi tên sang `name`; thất bại thì dọn tạm và không ghi store. Kiểm
được: ép clone hỏng, sau đó R13 trả về số cũ và `working_dir` không có thư mục thừa.

**R17 — Sửa label.** Chuỗi ≤ 200 ký tự, không ảnh hưởng đường dẫn hay định danh.

**R18 — Xoá là gỡ khỏi danh sách, không xoá đĩa.** Kiểm được: sau khi xoá, R13 còn 1 mục và
`working_dir / name` vẫn tồn tại.

**R19 — Trạng thái sống qua khởi động lại, không cần biến môi trường nào đổi.** Store là
thứ duy nhất mang trạng thái đó.

**R20 — `pull latest` chạy được và báo thất bại ra ngoài.** `git -C <path> pull --ff-only`,
cùng ràng buộc môi trường như R15. Không fast-forward được là **lỗi trả về**, không im
lặng.

**R21 — Ranh giới workspace không nới ra.** `is_workspace` vẫn là cổng gác của mọi đường
làm việc. Nó nhận một path khi và chỉ khi path đó nằm trong danh sách env **hoặc** giải ra
đúng `working_dir / name` của một mục trong store. Kiểm **lúc đọc**, mỗi lần. Kiểm được:
sửa tay file store để trỏ ra ngoài `working_dir`, mọi đường làm việc vẫn từ chối.

**R22 — Session không đổi tư thế.** Vẫn chat only, không tool nào. Không knob nào của
`0001` bị lật. Kiểm được: `effective_tools()` rỗng trong suốt chuỗi đo.

**R23 — Một lệnh, ba mệnh đề, không cần trình duyệt.** Toàn bộ `intent.md:49-59` chạy trong
một lệnh trả non-zero khi bất kỳ mệnh đề nào hỏng.

## Design

**Cách chặn đường thoát ra ngoài gốc là hình dạng dữ liệu, không phải một hàm kiểm.** Store
chỉ lưu `name` — một đoạn đường dẫn. Không có đường dẫn tuyệt đối nào trong store, nên
không có gì để một file bị sửa tay trỏ ra ngoài `working_dir`. Đường dẫn được **dựng ra** từ
gốc mỗi lần đọc. Kiểm containment vẫn còn (R21) như lớp thứ hai, nhưng lớp thứ nhất là việc
không tồn tại một chỗ nào để đặt `/etc` vào.

**Cách chặn trang và API trôi khỏi nhau cũng là hình dạng, không phải kỷ luật.** Lớp dịch
vụ là nơi duy nhất có logic; route HTTP và event handler của Reflex đều là vỏ. R10 tồn tại
vì hai lối vào cùng một năng lực là cách chắc chắn nhất để một lối được sửa còn lối kia
không.

**Sáu lớp.**

- **Lớp cấu hình.** Nơi duy nhất đọc môi trường. Trả lời `is_workspace` bằng hợp của danh
  sách env bất biến và store.
- **Lớp store.** Đọc/ghi danh sách workspace. Ghi bằng file tạm rồi `rename`, nối tiếp bằng
  một khoá trong tiến trình.
- **Lớp git.** Gọi `git` bằng argv với môi trường dựng mới. Chỉ clone và pull.
- **Lớp phiên.** Giữ nguyên từ `0001`: mỗi session một client SDK, vòng đời do app quyết.
- **Lớp dịch vụ.** Nơi duy nhất có logic nghiệp vụ. Bốn lớp trên chỉ được gọi từ đây.
- **Lớp trình bày.** Hai lối vào, không logic: route JSON của FastAPI mount qua
  `api_transformer`, và các event handler của Reflex vẽ trang.

**Ranh giới và dữ liệu đi qua.**

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| Trình duyệt → Reflex `/_event` | thao tác của người dùng | cập nhật state, trang vẽ lại |
| Lệnh kiểm → `/api/*` | `name`, `label`, `repo_url`, prompt | số đếm, danh sách, luồng phản hồi |
| Lớp trình bày → lớp dịch vụ | tham số đã hợp lệ hoá | kết quả, hoặc lỗi có nội dung |
| Lớp dịch vụ → git | `name`, `repo_url` | thành công, hoặc lỗi |
| git → mạng | `repo_url` | nội dung repo |
| Lớp dịch vụ → store | mục | file JSON dưới `working_dir` |
| Lớp phiên → đĩa | không do app ghi | SDK tự ghi transcript |

**Store nằm ngoài repo, dưới chính working folder.** Trả lời `intent.md` OQ5: không commit,
vì nó chứa những gì người dùng đã clone về máy mình. File mang một số `version`.

**Hai nguồn workspace, thứ tự rõ ràng.** `COS_WORKSPACES` của `0001` không bị bỏ và không
được tự động chuyển vào store. Khi `COS_WORKING_DIR` không đặt, lớp store tắt hẳn và app cư
xử đúng như `0001` — đó là cách R6 được giữ.

**"Khởi động lại" trong lệnh kiểm** là dựng lại app từ đúng môi trường cũ. Nếu trạng thái
sống sót qua đó thì nó đến từ đĩa, và đó là toàn bộ điều R19 cần chứng minh.

## Out of scope

- **Đăng ký thư mục bất kỳ bằng đường dẫn tuyệt đối.** `intent.md` cấm. Project ngoài gốc
  vẫn đi qua `COS_WORKSPACES`. Xem C4.
- **Clone repo riêng tư.** R15 chọn thất bại nhanh thay vì treo.
- **`git` ngoài clone và pull.**
- **Bật tool cho session.** Bốn knob của `0001` giữ nguyên mặc định
  (`.cos/0001_no-session-management/spec.md:112-115`).
- **Hiện branch, trạng thái sạch/bẩn, số commit đi sau.**
- **Xoá thư mục khỏi đĩa.** Xem R18 và C6.
- **Tự động dọn workspace trỏ vào thư mục đã mất.**
- **Sửa 13 trích dẫn chết trong `.cos/0001_no-session-management/plan.md`.** Xem C14.
- **Giao diện đẹp như một mục tiêu riêng.** Thứ đo được là R8 và R9; "đẹp" không đo được và
  không nằm trong kết quả.
- **Nhiều người dùng, đăng nhập, TLS.**

## Concerns

**C1 — `is_workspace` mất chỗ dựa cũ.** Ở `0001` nó đứng được một phần nhờ danh sách bất
biến suốt đời tiến trình (`app/config.py:82-94`). Câu trả lời của thiết kế là R12 và R21.
Chỗ này hỏng thì mọi thứ khác trong `0001` hỏng theo.

**C2 — App lần đầu chạy tiến trình ngoài và lần đầu chạm mạng.**
`.cos/0001_no-session-management/spec.md:121-125` (C3) nói rõ: đường nào chạy lệnh theo chữ
người dùng gửi là đường làm lộ token. R15 là câu trả lời trực tiếp — argv chứ không shell,
subcommand cố định, môi trường dựng mới chứ không kế thừa.

**C3 — Thời gian chờ là quyết định, không phải phép đo.** Đề xuất **120 giây cho clone, 60
giây cho pull**. **Không có nguồn** — chọn ở đây, chưa đo lần nào, nên chỉnh sau lần chạy
đầu chứ đừng tin.

**C4 — Unit này không giải hết vấn đề mà `intent.md` nêu.** Với gốc-bằng-env, mọi project
đang nằm ngoài `working_dir` **vẫn** phải sửa env và khởi động lại — tức tất cả project hiện
có. Ba lối: (a) chấp nhận và chuyển dần project về dưới gốc; (b) cho `COS_WORKING_DIR` nhận
nhiều gốc, vẫn env; (c) cho đăng ký đường dẫn tuyệt đối. **Người quyết là tác giả**, vì (b)
và (c) sửa constraint trong intent chứ không sửa thiết kế. Không ai quyết thì mặc định là
(a), và khi đó phải nói thẳng rằng vấn đề chỉ được giải một nửa.

**C5 — Clone dở dang.** R16 chọn thứ tự clone-vào-tạm → rename → ghi store, nên trạng thái
xấu nhất là một thư mục tạm bị bỏ quên, không phải một workspace hỏng. Thư mục tạm bỏ quên
vẫn là rác **chưa ai dọn**.

**C6 — Xoá không đụng đĩa là quyết định, và nó sẽ gây khó chịu.** App không có undo, còn
một thư mục clone có thể chứa công việc chưa commit. Hậu quả: `working_dir` đầy dần thư mục
không còn trong danh sách. Chọn giữa rác và mất việc; unit này chọn rác.

**C7 — `pull --ff-only` sẽ thất bại thường xuyên, và đó là hành vi đúng.** Rủi ro nằm ở
việc nó thất bại **im lặng**; R20 đòi lỗi ra tới người gọi. Thêm một chỗ chưa ai nghĩ hết:
pull vào workspace đang có session chạy dở sẽ đổi file dưới chân Claude giữa lượt. Không có
khoá nào ngăn, và unit này không dựng.

**C8 — Store là trạng thái đầu tiên app tự sở hữu.** `0001` cố ý không có
(`.cos/0001_no-session-management/spec.md:61`). Một khi có file để ghi, cám dỗ ghi thêm
message vào đó là có thật, và
`.cos/0001_no-session-management/spec.md:135-141` (C6) đã viết sẵn lý do không được. Store
này chỉ được chứa workspace và label.

**C9 — Đây chưa phải kho cấu hình trung tâm ở `0001` C8.** Store lưu workspace, không lưu
knob, không lưu hồ sơ agent. Bốn knob vẫn chỉ đọc từ env.

**C10 — Hạn mức vẫn không được đếm.** `.cos/0001_no-session-management/spec.md:127-128`
còn nguyên hiệu lực, và unit này thêm băng thông, đĩa, và một toolchain Node.

**C11 — Đường người dùng thật sự bấm vẫn là đường không ai chứng minh, chỉ là lật ngược.**
`app/web.py:3-5` phản đối route chỉ dùng cho test, vì đó là route không ai chạy thật. Sau
unit này, lệnh kiểm chạy `/api/*` còn người dùng chạy `/_event` của Reflex — nên cái không
được chứng minh lại chính là **cái người dùng dùng**. R10 là thứ duy nhất đang giữ hai lối
khỏi trôi khỏi nhau, và R10 là một quy ước về cấu trúc, **không phải một phép kiểm tự
động**. Đây là món nợ rõ ràng nhất mà unit này tạo ra. Người quyết có chấp nhận nó không là
tác giả; spec này không tự đóng.

**C12 — Repo có bước biên dịch, và `.claude/CLAUDE.md:17` nói là không có.** Dòng đó còn
cấm bịa ra lệnh build. Reflex biên dịch frontend sang JavaScript. File đó phải được sửa cho
đúng — lách bằng cách không gọi nó là build step thì chỉ là nói dối chậm hơn.

**C13 — Unit này viết lại bằng chứng duy nhất của một unit đã đóng.**
`scripts/verify_0001.py` là thứ duy nhất chứng minh `0001` từng đạt. R6 giới hạn thay đổi ở
client và import, nhưng không có gì **ép** điều đó ngoài người đọc diff. Cùng loại rủi ro mà
`.claude/CLAUDE.md` chặn cho `terminal-only-access` bằng cách cấm xoá `channel/` — chỉ là ở đây không có
lệnh cấm nào tương đương.

**C14 — 13 trích dẫn sẽ chết và unit này không sửa.**
`.cos/0001_no-session-management/plan.md` trỏ vào `app/*.py` ở 13 chỗ; sau R1 chúng trỏ vào
hư không. Sửa nghĩa là viết lại một artifact đã ký; không sửa nghĩa là harness có trích dẫn
hỏng, trong khi chính nó đòi "cite only a file committed in this repository". Không lối nào
sạch. Unit này chọn không sửa và ghi lại ở đây; **tác giả quyết** nếu muốn khác.

**C15 — Reflex kéo theo một toolchain không phải Python.** Node/bun, thư mục `.web/`, một
lockfile frontend phải commit, và lần chạy đầu cần mạng để tải. Trước unit này repo chạy
được từ source không cần gì ngoài `uv sync` và `npm test`. Sau nó thì không.

**C16 — Streaming có hai đường và chúng dễ lệch.** `0001` stream NDJSON qua `/api/send`
(`app/web.py:90-107`). Trang Reflex sẽ stream bằng state update, không bằng NDJSON. R10 đòi
cả hai đi qua cùng lớp dịch vụ, nhưng hình dạng dữ liệu hai bên vẫn khác nhau — và chỗ lệch
sẽ xuất hiện ở xử lý lỗi giữa chừng, đúng chỗ `app/web.py:70-74` cảnh báo rằng status đã
gửi trước khi lỗi xảy ra.

## Open questions

1. **Đã trả lời** (`intent.md` OQ1): clone repo riêng tư ngoài phạm vi. R15 chọn thất bại
   nhanh và rõ thay vì treo chờ một mật khẩu không ai gõ được.
2. **Đã trả lời** (OQ2): metadata chỉ có `label`. Nguồn, cờ `missing` và đường dẫn là thứ
   tính lúc đọc, không lưu.
3. **Đã trả lời** (OQ3): xoá là gỡ khỏi danh sách. Xem R18 và C6.
4. **Đã trả lời** (OQ4): hiện cờ `missing`, không tự dọn, và từ chối tạo session trong đó.
5. **Đã trả lời** (OQ5): file JSON dưới `working_dir`, không commit.
6. **Còn mở** (OQ6): `with_workspaces` — sửa docstring hay bỏ hẳn? Store đã làm thay việc
   của nó. Plan quyết.
7. **Còn mở** (OQ7, và là C14): 13 trích dẫn chết. Tác giả quyết.
8. **Còn mở** (OQ8, và là C11): lấy gì chứng minh đường người dùng thật sự bấm? Một lựa
   chọn là kiểm giao diện bằng công cụ điều khiển trình duyệt — nhưng `intent.md` đã đặt
   "không cần trình duyệt" thành một phần của kết quả, nên nó sẽ là một unit riêng với một
   lý do riêng.
9. **Còn mở** (OQ9, và là C12): `.claude/CLAUDE.md:17` sửa thành gì? Nếu có lệnh build thì
   nó phải được ghi cạnh `npm test` và phải có ai đó chạy nó — nếu không, nó là một lệnh
   trong tài liệu mà không ai gọi.
10. **Còn mở, và là câu ở C4:** một gốc hay nhiều gốc?
11. **Còn mở:** hai app cùng chạy trên một `working_dir` thì store bị hai tiến trình ghi.
    Khoá trong tiến trình không đủ. `0001` OQ3 đã cho thấy chính xác kiểu hỏng này mất dữ
    liệu im lặng (`.cos/0001_no-session-management/spec.md:165-176`).
12. **Còn mở:** Reflex có chạy được với layout phẳng mà gói tên khác `app_name` không, và có
    chịu được việc `rxconfig.py` sống cạnh một repo đã có `package.json` và `channel/`
    không? Chưa kiểm. Plan phải kiểm trước khi xây gì lên trên.
