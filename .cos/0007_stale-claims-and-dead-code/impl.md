# Impl: Correct what the repository says about itself, and stand it on current versions
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Mười hai commit, mỗi bước một commit, `npm test` xanh ở cuối từng cái (R16).

| Commit | Bước | Việc |
|---|---|---|
| `1a2be0b` | 1 | `scripts/verify_0007.py` — mười một claim, chạy lần đầu **đỏ 11/11** |
| `31b2929` | 2 | Một phép đo một chiều; ba citation trỏ đúng (R1, R2) |
| `22650f0` | 3 | `README.md` tám stage; tên test bỏ số đếm unit (R3, R4) |
| `66273cc` | 4 | Xoá tầng object store (R6, R7) |
| `7f1fe07` | 5 | Bỏ `BREAKPOINTS` (R5) |
| `f1f48df` | 6 | Bỏ `@modelcontextprotocol/sdk` (R8) |
| `4cc0128` | 7 | `on_event("shutdown")` → `lifespan=`, kèm phép kiểm (R10, R12) |
| `f9c2e47` | 8 | Đóng handle stdout bị rò (R9) |
| `c571a65` | 9 | `.gitignore` bỏ entry trùng (R11) |
| `36e797e` | 10 | Python 3.14, Reflex 0.9.12, sửa ba floor (R13, R14) |
| `7ad00f7` | 11 | Chạy lại `verify_0004.py` trên 3.14 (`spec.md` C2) |
| `76822f9` | 12 | Dựng lại bundle, `verify_0003.py`, `.gitignore` thành fixed point (R15) |

Mười ba mục của `intent.md` đóng hết. Ba hình dạng đáng nói:

**Negative control là chính cây đang hỏng.** `verify_0007.py` được viết và chạy **trước** khi
sửa gì: exit 1, 11 trên 11 claim đỏ (`1a2be0b`). Không phải một cảnh hỏng dựng tay.

**C1 không được phép chứa chuỗi nó đi tìm.** Chuỗi đọc ngược phép đo `0004` được ghép từ
`KEPT` và `TOTAL` lúc chạy, nên nó không tồn tại literal ở đâu trong cây — kể cả trong chính
file checker. Nhờ vậy C1 quét được cả cây mà không cần loại trừ thư mục nào.

**Shutdown giờ có một phép kiểm nằm trong `npm test`.** `cos_baodo/api_test.py`
`ShutdownClosesSessions` drive `app.router.lifespan_context` trực tiếp: không cổng, không
trình duyệt, không session thật, không quota, và **không thêm dependency** — `spec.md` skip
assessment mục 3 loại trừ điều đó.

## Where the plan was departed from

Năm mục, đều đã ghi ở `plan.md ## Departures` trong cùng commit gây ra chúng.

1. **Bốn quyết định của tác giả, 2026-09-22.** `spec.md` OQ1 → sửa `0006 spec.md:144` tại chỗ.
   OQ4 → **chạy** `verify_0004.py` sau bước version. C11 → dạng mềm của R13. Chuỗi đọc ngược
   nằm trong chính `intent.md`/`plan.md` của unit → viết lại câu trích bằng chữ.
2. **`ensure_dir` chưa bao giờ tạo `objects/`.** `spec.md:11-12` nói "app thôi tạo
   `~/.cos/objects/`"; đúng ra app **chưa từng** tạo. `cos_baodo/data.py:161-171` chỉ `mkdir`
   `self.root`; `mkdir` duy nhất của thư mục ấy nằm ở `objects.py:74` bên trong `Objects.put`
   mà không ai gọi. Hệ quả: nửa đầu C7 đã xanh trước khi sửa, nên negative control thật là
   `not hasattr(Data(tmp), "objects_dir")`. Bỏ `OBJECTS_DIRNAME` và `objects_dir` **không đổi
   hành vi đĩa một chút nào** — nó là thuộc tính chết, không phải thư mục app đang tạo.
3. **C6 phải đếm cả entrypoint gọi bằng chuỗi.** Một phép kiểm chỉ đọc import báo
   `cos_baodo/run.py` và `cos_baodo/cos_baodo.py` là chết, trong khi xoá cái nào cũng làm app
   không chạy: `cos-baodo = "cos_baodo.run:main"` ở `pyproject.toml`, và
   `"cos_baodo.cos_baodo:app"` ở `cos_baodo/run.py:61`.
4. **`.gitignore` phải là fixed point của `reflex init`.** Bước 9 bỏ `.web` giữ `.web/`; bước
   12 cho thấy `uv run cos-build` chạy `reflex init` và **thêm lại `.web`** — bản dedup sống
   đúng tới lần build kế tiếp. Đảo chiều: giữ `.web`, bỏ `.web/`. Đo bằng md5 trước và sau một
   lần `cos-build`: không đổi.
5. **Một phép kiểm phải đổi hình để không tự thêm noise.** Trên 3.14, event loop của
   `IsolatedAsyncioTestCase` chạy ở debug mode và in một dòng slow-callback cho phép kiểm
   lifespan. App được dựng ở `setUp` thay vì trong coroutine; ba lần chạy liên tiếp sạch.

`plan.md` cũng được sửa ở ba chỗ con số: dòng `journal_test.py` là `:4-5` chứ không phải `:5`,
hàng C1 và hàng C11 của bảng `## Proof` viết lại theo hai quyết định ở trên.

## What was measured

Tất cả trên **Python 3.14.4**, `reflex 0.9.12`, ngày 2026-09-22.

| Lệnh | Kết quả |
|---|---|
| `uv run python scripts/verify_0007.py` (trước khi sửa, `1a2be0b`) | exit **1**, 11/11 claim đỏ |
| `uv run python scripts/verify_0007.py` (sau bước 12) | exit **0**, 11/11 claim xanh |
| `npm test` | xanh — **23** test Node, **266** test Python |
| `npm test 2>&1 \| grep -c Warning` | **0** (đầu unit: **70**) |
| `npm test 2>&1 \| grep -c DeprecationWarning` | **0** (đầu unit: **68**) |
| `uv run cos-build` | exit 0, **0** dòng chứa `Deprecat`, 6 source file |
| `uv run python scripts/verify_0003.py` | exit **0**, 5/5 claim, kể cả negative control |
| `uv run python scripts/verify_0004.py` | exit **0**, **20 trên 20** entry sống sót |
| `uv run python scripts/verify_0001.py` | exit **0**, 5/5 claim, 2 project |
| `uv run python scripts/verify_0002.py` | exit **0**, 3/3 claim, 2 workspace, có clone thật |
| `npm outdated` | rỗng |
| `uv pip list --outdated` | **4** dòng, từ 15 (xem dưới) |

Số test Python đi **278 → 265 → 266**: mất 13 khi xoá `objects_test.py` (`66273cc`), được lại
1 khi thêm phép kiểm lifespan (`4cc0128`). 265 là con số phải thấy sau bước 4, không phải dấu
hiệu mất test.

**Bốn dependency còn cũ, và cái gì đang chặn từng cái** — đây là dạng mềm mà R13 cho phép.
Không cái nào là dependency trực tiếp của repo này; cả bốn bị upper bound của một package
khác ghim:

| Package | Đang cài | Mới nhất | Bị chặn bởi |
|---|---|---|---|
| `pydantic-core` | 2.46.5 | 2.49.0 | `pydantic` đòi `pydantic-core==2.46.5` |
| `pyee` | 13.0.1 | 14.0.0 | `playwright` đòi `pyee<14,>=13` |
| `redis` | 7.4.1 | 8.1.0 | `reflex` đòi `redis<8.0,>=6.4` |
| `wrapt` | 2.3.0 | 2.4.1 | `reflex` đòi `wrapt<2.4,>=1.17.0` |

Ba floor ở `pyproject.toml` được sửa: `requires-python` `>=3.11` → `>=3.14`,
`claude-agent-sdk` `>=0.1.0` → `>=0.2.157` (bản cũ là một dòng thư viện khác, nên `uv sync`
trên máy mới có quyền cài thứ code này không chạy được), `reflex` `>=0.9.11.post1` → `>=0.9.12`.

**Luật tên dành riêng của Reflex 0.9.12 không đụng vào đây.** `spec.md` open question 5 là câu
duy nhất chỉ trả lời được lúc chạy: `StudioState` có **48** state var, app factory dựng và
compile xong trên 0.9.12. Ba breaking change còn lại đã đo là không áp dụng trước khi nâng —
`cos_baodo/` không dùng `router`, không gọi `.dict()`, không dùng `deps=`.

**`spec.md` nói 112 state var; đo lại là 48.** 112 là tổng annotated attribute của cả
`state.py`, trong đó 64 thuộc về 9 dataclass thường mà luật tên của Reflex không chạm tới.

**Tắt app bằng SIGTERM kéo theo tiến trình CLI con** (đo lúc dọn cổng cho bước 12): app
`2725742` và tiến trình `claude` con `2727599` cùng biến mất, cổng 8790 trống. Đây là thứ
đường shutdown tồn tại để làm, và nó được quan sát trên tiến trình thật — nhưng trên **code
cũ**, vì app ấy đã chạy từ trước bước 7.

## What is still open

**Phép kiểm lifespan không bắt được lần đảo chiều mount.** Nó drive app FastAPI một mình. Ở
production, Reflex mount app của nó **vào trong** app FastAPI (`reflex/app.py:815`, đo
2026-09-22), nên uvicorn chạy lifespan của app ta — hôm nay. Nếu một bản Reflex sau đảo chiều
thì phép kiểm vẫn xanh trong khi session không được đóng, và cái bị rò là tiến trình CLI giữ
`CLAUDE_CODE_OAUTH_TOKEN` (`.cos/0001_no-session-management/spec.md:121`). Lần SIGTERM ở trên
là quan sát duy nhất trên tiến trình thật, và nó chạy code trước bước 7.

**Citation trôi thứ tư, đã biết chỗ, không sửa.**
`.cos/0005_hand-driven-invisible-loop/spec.md:291` trỏ `cos_baodo/store.py:16-18` cho phép đo
mất entry; dòng đó giờ tả row SQLite. Đây là hiện thân cụ thể của "mục thứ mười bốn" mà
`intent.md` open question 2 đoán trước. Tác giả chọn ghi lại chứ không chạm artifact accepted
thứ hai. Nó là đích đầu tiên đã biết cho unit citation-checker.

**Không có máy dò citation.** `verify_0007.py` C2 kiểm **ba** citation được nêu tên, không
kiểm mọi citation trong repo. Sau unit này cái thứ năm vẫn trôi được mà không ai biết.

**`setting_sources` vẫn mở.** `cos_baodo/sessions.py:188` đặt `setting_sources=None` và tuyên
bố nó chặn settings của user/project; SDK nói `None` là nạp mọi nguồn. Người khởi xướng để
ngoài scope. Nó vẫn là món lớn nhất còn mở trong repo.

**Còn một proof chưa chạy lại, không phải ba.** Tác giả đổi ý ngày 2026-09-22 sau khi thấy
bước 10 làm khoảng trống rộng ra, nên `verify_0001.py` và `verify_0002.py` được chạy: cả hai
exit **0** trên Python 3.14.4 + Reflex 0.9.12, và đó cũng là lần đầu chúng chạy sau khi `0006`
đổi kho dữ liệu sang SQLite — hai lần đổi nền được đóng bằng một lần chạy.

Còn `verify_0005.py`. Nó **không** bị chặn bởi quota mà bởi một quyết định chưa ai ra: nó cần
`COS_PROOF_REPO` và một remote để push, và repo này không có remote. Cùng một lý do khiến `pr`,
`review`, `ship` không unit nào đạt được. Nên board chạy một step thật vẫn là đường duy nhất
trong repo đi qua cả lần đổi SQLite lẫn lần đổi interpreter mà không ai chứng minh.

**Một dòng noise mới từ build.** `reflex 0.9.12` báo `SitemapPlugin` được bật mặc định mà
không khai trong `rxconfig.py`. Nó không phải deprecation, nên R12 vẫn đạt; tắt nó hay khai nó
đều đổi thứ build sinh ra, nên nó được ghi lại chứ không bị làm im. `spec.md` C4 đã nói trước
rằng ngưỡng "0 warning" là mong manh theo version — đây là lần đầu nó nhúc nhích.

**Unit dừng ở `impl`.** Repo không có remote, nên `pr`, `review`, `ship` không đạt được, giống
`0005` và `0006`.
