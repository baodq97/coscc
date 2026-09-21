# Plan: Move the stack, then manage workspaces on it
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

Thứ tự ở đây có một ý duy nhất: **đổi nền và thêm năng lực không được trộn vào nhau.** Bước
1 hỏi nền mới có sống được trong repo này không, và có quyền dừng cả unit. Bước 3 đổi tên
gói mà không đổi hành vi. Bước 5 đổi framework mà không đổi đường. Chỉ từ bước 6 mới có
năng lực mới. Mỗi bước để repo ở trạng thái chạy được lệnh kiểm.

## Files that change

| Path | |
|---|---|
| `rxconfig.py` | (new) `app_name="cos_baodo"`, ở gốc repo — layout phẳng của Reflex |
| `.python-version` | (new) `spec.md` R2 |
| `pyproject.toml` | có thật, 12 dòng — `[dependency-groups]`, `[project.scripts]`, thêm `reflex`, `fastapi`, `httpx` |
| `.gitignore` | có thật, 11 dòng — thêm `.web/` |
| `package.json` | có thật, 14 dòng — `test:python` đang khoá cứng `-s app` |
| `.claude/CLAUDE.md` | có thật, 70 dòng — dòng 17 nói "no build step", dòng 20-21 nói `test:python` chạy trên `app/` |
| `app/__init__.py` | có thật, 0 dòng — **đổi tên** sang `cos_baodo/` |
| `app/config.py` | có thật, 119 dòng — đổi tên, rồi thêm `COS_WORKING_DIR` và hợp hai nguồn |
| `app/config_test.py` | có thật, 106 dòng — đổi tên, thêm test ranh giới |
| `app/sessions.py` | có thật, 223 dòng — đổi tên, không đổi hành vi |
| `app/sessions_test.py` | có thật, 160 dòng — đổi tên |
| `app/web.py` | có thật, 155 dòng — **bị thay** bởi `cos_baodo/api.py` |
| `app/web_test.py` | có thật, 104 dòng — theo `web.py` |
| `app/public/index.html` | có thật, 151 dòng — **bị xoá**, `spec.md` R8 |
| `cos_baodo/service.py` | (new) nơi duy nhất có logic nghiệp vụ — `spec.md` R10 |
| `cos_baodo/store.py` | (new) danh sách workspace, ghi tạm rồi `rename` |
| `cos_baodo/gitops.py` | (new) `git` bằng argv, môi trường dựng mới |
| `cos_baodo/api.py` | (new) FastAPI, mount vào Reflex qua `api_transformer` |
| `cos_baodo/cos_baodo.py` | (new) trang Reflex, component Python |
| `cos_baodo/service_test.py` | (new) |
| `cos_baodo/store_test.py` | (new) |
| `cos_baodo/gitops_test.py` | (new) |
| `cos_baodo/api_test.py` | (new) |
| `scripts/verify_0002.py` | có thật, 171 dòng — **chỉ** đổi client và import, `spec.md` R6 |
| `scripts/verify_0003.py` | (new) lệnh ở `## Proof` |
| lockfile frontend của Reflex | (new) phải commit, `spec.md` R2 |

**Không đụng tới** `channel/`, `evidence/0001_terminal-only-access/`,
`scripts/verify-0001.mjs`. `channel/public/index.html` là HTML viết tay nhưng không phải
trang của app, nên `spec.md` R8 không áp lên nó (`spec.md` R7).

**Lệnh kiểm không cần biên dịch frontend.** `cos_baodo/api.py` dựng một app FastAPI đứng
được một mình; Reflex mount đúng app đó. Nên `verify_0002.py` và `verify_0003.py` gọi thẳng
app FastAPI trong tiến trình qua `httpx.ASGITransport`, không cần `.web/`, không cần Node.
Đây là thứ giữ cho `npm test` không phải kéo theo một toolchain JavaScript — và nó chỉ đúng
nếu bước 1 xác nhận được.

## Order of work

1. **Spike: Reflex có sống được trong repo này không. Có lệnh dừng gắn vào.** Thêm `reflex`
   và `fastapi`, viết `rxconfig.py` với `app_name="cos_baodo"`, và `cos_baodo/cos_baodo.py`
   tối thiểu: một trang rỗng, cộng một app FastAPI có `/api/health` mount qua
   `api_transformer`.
   Kiểm, cả bốn:
   (a) `reflex run` dựng được trong repo đã có `package.json`, `node_modules/` và `channel/`;
   (b) `/ping/` trả `pong` và `/api/health` trả 200 **trên cùng cổng backend**;
   (c) `ss -ltn` cho thấy **cả hai** cổng của Reflex đều `127.0.0.1` (`spec.md` R5);
   (d) gọi được `/api/health` qua `httpx.ASGITransport` **mà không** chạy `reflex run` và
   không có `.web/`.
   **Bất kỳ ý nào đỏ thì dừng.** (d) đỏ nghĩa là mọi lệnh kiểm phải biên dịch frontend; (a)
   đỏ nghĩa là layout phẳng không sống chung được với repo này. Cả hai đều phải quay lại sửa
   `spec.md`, và có thể phải quay lại `intent.md` — đây là `spec.md` open question 12, và
   nó chưa được kiểm lần nào.

   **Đã chạy 2026-09-21. Ba ý xanh, một ý buộc đổi cách chạy app.**
   **(a) Xanh.** `reflex run` biên dịch xong trong repo đã có `package.json`,
   `node_modules/` và `channel/`; log in `App running at: http://localhost:3000/`.
   **(b) Xanh.** `/ping/` trả `"pong"` (sau một 307 sang `/ping`) và `/api/health` trả
   `{"ok":true}`, cùng cổng backend.
   **(d) Xanh.** `httpx.ASGITransport` gọi được `/api/health` khi **chưa có** `.web/` và
   **chưa** chạy `reflex run`. Giả định lớn nhất của plan này đứng vững: lệnh kiểm không
   cần toolchain JavaScript.
   **(c) Đỏ như đã viết, và đây là thứ đáng giá nhất bước này tìm ra.**
   `Config.__dataclass_fields__["backend_host"].default` là `'0.0.0.0'`; `ss -ltn` xác nhận
   `0.0.0.0:8000` trước khi `rxconfig.py` ghim lại. `0002` mặc định ngược lại
   (`app/config.py:55`), nên **nhận Reflex vào là tự động lật tư thế mạng của `0002`**, mà
   không dòng code nào của app trông có vẻ sai.
   Ghim được backend, **không ghim được frontend**: chế độ dev chạy vite bằng `run dev` và
   chỉ truyền `PORT`, không truyền host — `ss -ltn` cho `*:3000`. Reflex 0.9.11 không có
   trường cấu hình nào cho host của frontend.
   Lối đi đã kiểm: `__REFLEX_MOUNT_FRONTEND_COMPILED_APP=1` gắn bản build tĩnh vào **cùng
   một app ASGI** với backend. Trong tiến trình, `/`, `/api/health` và `/ping` đều trả 200
   trên **một** app, và cổng duy nhất là `127.0.0.1:8000`.
   **Hệ quả: chế độ dev hai cổng không phải cách chạy được của app này, và `spec.md` R5
   phải đổi từ "cả hai cổng loopback" sang "đúng một cổng, loopback".** Đổi một spec đã
   accepted sau khi đã có code là thứ `intent.md` gọi là ngoại lệ — tác giả quyết.
   **Chưa giải:** `reflex run --env prod --backend-only` kèm biến đó vẫn trả 404 ở `/`.
   Đường chạy đã kiểm được là app ASGI dạng factory, không phải lệnh `reflex run`; bước 11
   phải ghi đúng lệnh chạy thật.

2. **uv đúng chuẩn, trước khi có gì để mất.** `[dependency-groups]` cho `httpx` và test,
   `[project.scripts]`, `.python-version`, `.gitignore` thêm `.web/`, commit lockfile
   frontend.
   **Lệch khỏi plan, đã xảy ra ở bước 1:** `.gitignore` thêm `.web/` và `httpx` vào
   `[dependency-groups]` đều phải làm sớm, vì bước 1 chạy Reflex (sinh `.web/`) và ý (d)
   cần `httpx`. Phần còn lại của bước 2 — `[project.scripts]`, `.python-version`, commit
   `reflex.lock/` — vẫn chưa làm.
   Kiểm: `uv sync --locked` chạy sạch; `git status --short` không thấy `.web/`.

3. **Đổi tên gói, không đổi một hành vi nào.** `app/` → `cos_baodo/`, sửa mọi `from app.`,
   sửa `test:python` trong `package.json`. Vẫn là aiohttp, chưa đụng framework.
   Kiểm: `npm test` xanh **và** `uv run python scripts/verify_0002.py` xanh. Hai lệnh này
   xanh sau một bước chỉ đổi tên là bằng chứng bước này không mang theo gì khác.

4. **Tách lớp dịch vụ.** `cos_baodo/service.py` nhận toàn bộ logic đang nằm trong route của
   `web.py`; route thành vỏ mỏng. Chưa đổi framework.
   Kiểm: `npm test` và `verify_0002.py` vẫn xanh; đọc `web.py` không còn nhánh nghiệp vụ
   nào. Bước này tồn tại vì `spec.md` R10 chỉ có nghĩa nếu lớp dịch vụ có **trước** khi có
   lối vào thứ hai.

5. **Đổi HTTP sang FastAPI, giữ nguyên đường và hình dạng dữ liệu.** `cos_baodo/api.py`
   thay `web.py`: cùng năm đường, cùng NDJSON, cùng mã lỗi. Viết lại `verify_0002.py`
   **chỉ** ở client (`aiohttp` → `httpx`) và import.
   Kiểm: `verify_0002.py` xanh, và `git diff` của nó không chứa thay đổi nào ngoài client và
   import — `spec.md` R6 và C13. Đây là chỗ dễ nới lỏng một mệnh đề nhất, nên diff phải được
   đọc, không chỉ được chạy.

6. **Store và ranh giới.** `cos_baodo/store.py` (chỉ lưu `name`, ghi tạm rồi `rename`),
   `COS_WORKING_DIR` trong config, `is_workspace` thành hợp của env và store, kiểm lúc đọc.
   Kiểm: `store_test.py` và `config_test.py` xanh, trong đó **bắt buộc** có một test sửa tay
   file store để trỏ ra ngoài `working_dir` và khẳng định mọi đường làm việc vẫn từ chối
   (`spec.md` R21), và một test cho `../x`, `/etc`, `a/b`, rỗng, 65 ký tự (R12).

7. **Lớp git.** `cos_baodo/gitops.py`: clone và pull, argv, môi trường dựng mới, timeout.
   Kiểm: `gitops_test.py` khẳng định môi trường tiến trình con **không** chứa
   `CLAUDE_CODE_OAUTH_TOKEN` và không chứa biến `COS_*` (R15); một URL `https://` không tồn
   tại trả lỗi trong hạn thời gian chứ không treo; `ssh://`, `git@`, `file://` và chuỗi bắt
   đầu bằng `-` đều bị từ chối trước khi `git` được gọi.

8. **Đường API cho workspace.** Thêm vào `api.py` và `service.py`: liệt kê kèm số đếm,
   thêm/clone, sửa label, xoá, pull. Clone vào thư mục tạm rồi `rename` (R16).
   Kiểm: từng đường trả lời bằng một lệnh dòng lệnh; ép clone hỏng rồi xác nhận số đếm không
   đổi và `working_dir` không có thư mục thừa.

9. **Trang Reflex, và xoá HTML.** `cos_baodo/cos_baodo.py`: danh sách workspace kèm label và
   cờ `missing`, chỗ thêm/clone, xoá, pull, và khung hội thoại của `0002`. Mọi event handler
   gọi xuống `service.py`. Xoá `app/public/index.html`.
   Kiểm: không file `.html`/`.css` nào còn phục vụ trang app; file `.html` duy nhất còn lại
   là `channel/public/index.html` (R8). Mở trang và đi hết một vòng bằng tay — đây là phần
   **không** có lệnh nào chứng minh, và đó là `spec.md` C11.

10. **Lệnh chứng minh.** `scripts/verify_0003.py` theo `## Proof`.
    Kiểm: chạy khi chưa xong thì thoát khác 0 kèm mệnh đề nào hỏng.

11. **Đóng unit.** Sửa `.claude/CLAUDE.md`: dòng 17 ("no build step") thành mô tả đúng, kèm
    lệnh build thật và ai chạy nó; dòng 20-21 trỏ đúng gói mới; `## Commands` có lệnh chạy
    app mới. Chạy `## Proof`. Đặt `Status: done` chỉ sau khi nó xanh.
    **Đã làm 2026-09-21.** `cos_baodo/run.py` + `[project.scripts] cos-baodo` là lệnh chạy
    thật, và nó trả lời câu còn treo ở bước 1: không dùng `reflex run`, mà mount bản build
    vào cùng app ASGI rồi để uvicorn bind `config.host`. Đo lại: đúng **một** socket
    `127.0.0.1:8791`, không có gì trên `:3000`, `/` trả 200 và `/api/*` trả 200. `## Proof`
    xanh cả bốn lệnh.

## What actually happened

Bốn thứ đáng ghi, vì không cái nào đoán được trước khi chạy.

**Reflex lật tư thế mạng của `0002` mà không ai thấy.** `backend_host` mặc định
`'0.0.0.0'`. Ghim được backend, nhưng chế độ dev của Reflex chạy vite không nhận host, nên
cách duy nhất giữ được `spec.md` R5 là bỏ hẳn chế độ hai cổng: mount bản build vào cùng app
ASGI, một cổng, loopback. R5 vẫn đậu **đúng như đã viết** — tiêu chí của nó là "không cổng
nào của app bind ra ngoài loopback", và chạy một cổng thì tiêu chí đó đúng. Chỉ phần văn
xuôi "Reflex phục vụ hai cổng" là không còn mô tả đúng cách chạy.

**Bước 4 đổi hành vi, và test của `0002` bắt được.** Gộp validation vào async generator
biến một `Refused` từ dòng lỗi NDJSON sau 200 thành 400 — xoá đúng cái ranh giới mà
docstring của `post_send` viết ra. Tách thành `check_send` và `stream`.

**Hai lớp cùng hỏi một câu và trả lời khác nhau.** `sessions.py:159` giữ cổng gác riêng gọi
thẳng `config.is_workspace`, nên workspace trong store qua được cổng của service rồi bị từ
chối ở lớp dưới. Đây đúng là thứ `spec.md` R10 sinh ra để chặn, và nó có sẵn từ `0002`.
Không test nào thấy; chỉ một lần clone thật rồi mở session mới lòi ra. Cổng gác giữ lại,
nhưng câu hỏi thì dùng chung.

**`from_env` lặng lẽ thêm cwd vào danh sách.** Chạy với working folder và không khai báo
`COS_WORKSPACES` thì repo tự thành workspace, và số đếm 2 đọc ra 3. Fallback giờ chỉ áp
dụng khi không có working folder — `0002` không đổi.

Hai lỗi sau không unit test nào bắt được ở hình dạng cũ. Cả hai giờ có test hồi quy.

## Risks

**Bước 1 đỏ, và nền đã nằm trong một intent đã accepted.** Đây là rủi ro tôi muốn không phải
viết ra. `spec.md` open question 12 chưa được kiểm lần nào: chưa ai biết Reflex có chịu được
một repo đã có `package.json`, `node_modules/` và `channel/` không, hay có chạy được mà
không biên dịch frontend không. Nếu đỏ, việc phải làm không phải là lách, mà là quay lại sửa
`intent.md` — **lần thứ hai**, sau `d6ae280`. Dấu hiệu: `reflex run` không dựng, hoặc
`httpx.ASGITransport` không gọi được app khi chưa có `.web/`. Giảm thiểu: bước 1 đứng trước
mọi thứ và không có bước nào chạy trước nó.

**Ba mệnh đề đi chung một kết quả.** `intent.md` đã ghi cái giá này. Hệ quả cụ thể cho plan:
nếu bước 1 đỏ thì phần workspace — thứ chẳng liên quan gì tới Reflex — cũng trượt theo, và
`## Proof` không nói được phần nào đứng vững. Dấu hiệu: một mệnh đề đỏ, hai mệnh đề kia
không được báo cáo riêng. Giảm thiểu: `verify_0003.py` in kết quả **từng mệnh đề** trước khi
thoát, kể cả khi thoát khác 0.

**Bước 5 nới lỏng một mệnh đề của `0002` mà không ai thấy.** `verify_0002.py` là bằng chứng
duy nhất `0002` từng đạt (`spec.md` C13). Viết lại nó là viết lại bằng chứng. Dấu hiệu: diff
chứa thay đổi ở phần `f.check(...)` chứ không chỉ ở client. Giảm thiểu: bước 5 đòi đọc diff
như một điều kiện, không chỉ chạy lệnh — và không có gì tự động ép điều đó.

**Mỗi lần chạy `## Proof` đều tiêu hạn mức.** `verify_0002.py` tạo session thật, và
`verify_0003.py` tạo thêm hai. Một vòng lặp hỏng là một vòng lặp đốt hạn mức, im lặng
(`spec.md` C10). Dấu hiệu: cảnh báo hạn mức, phản hồi chậm bất thường. Giảm thiểu: prompt
ngắn nhất có thể, `max_turns=1` như `0002` đã đặt, và không bước nào chạy không người trông.

**Clone và pull chạm mạng và chạy tiến trình ngoài** (`spec.md` C2). Dấu hiệu của hỏng an
toàn: **không có dấu hiệu nào** — đây là loại rủi ro không tự báo. Giảm thiểu là bước 7, và
test của nó phải khẳng định điều không xảy ra (token vắng mặt), không chỉ điều xảy ra.

**Trang là thứ không có bằng chứng nào chạy qua** (`spec.md` C11). Sau unit này, đường người
dùng bấm là `/_event` của Reflex, còn lệnh kiểm đi `/api/*`. Dấu hiệu: một lỗi chỉ xuất hiện
khi bấm bằng tay và `## Proof` vẫn xanh. Giảm thiểu duy nhất là `spec.md` R10 — một quy ước
về cấu trúc, không phải một phép kiểm.

**`.web/` và lockfile frontend làm bẩn cây.** Dấu hiệu: `git status --short` có rác sau mỗi
lần chạy. Bước 2 đứng trước mọi lần chạy Reflex vì lý do đó.

## Proof

```
npm test \
  && node scripts/verify-0001.mjs evidence/0001_terminal-only-access/transcript.jsonl \
  && uv run python scripts/verify_0002.py \
  && uv run python scripts/verify_0003.py
```

Ba lệnh đầu là mệnh đề 1 của `intent.md` — nền đã chuyển mà không làm đổ thứ có sẵn. Lệnh
`verify-0001.mjs` chạy không cần người: nó đọc transcript đã commit và hiện thoát 0 với
`PASS: session e539fd53 delivered 12 turns`, kiểm ngày 2026-09-21.

`verify_0003.py` thoát 0 **chỉ khi** cả ba đúng, và in kết quả từng mệnh đề kể cả khi hỏng:

1. **Không còn HTML viết tay.** `app/public/index.html` không tồn tại, và file `.html` hay
   `.css` duy nhất còn trong repo là của `channel/`.
2. **Ranh giới không nới.** Ghi một `name` hợp lệ vào store, sửa tay file store để mục đó
   trỏ ra ngoài `working_dir`, rồi khẳng định mọi đường làm việc từ chối.
3. **Chuỗi workspace.** Working folder trống, không `COS_WORKSPACES`: clone 2 repo công khai
   → đếm đúng 2 → đặt label → dựng lại app từ đúng môi trường cũ → vẫn 2 và label còn → pull
   latest một cái → tạo session trong mỗi workspace và nhận phản hồi khác rỗng → xoá một →
   còn đúng 1 → dựng lại → vẫn 1.

Con số 2 lấy từ `intent.md`. Muốn đổi thì sửa ở đó, không sửa ở đây.

## What this plan does not do

- **Không sửa 13 trích dẫn chết trong `.cos/0002_no-session-management/plan.md`**
  (`spec.md` C14). Sửa là viết lại một artifact đã ký. Tác giả quyết; plan này không tự
  quyết hộ.
- **Không giải `spec.md` C4** — một gốc hay nhiều gốc. Plan dựng một gốc, vì đó là thứ
  `intent.md` cho phép. Project cũ nằm ngoài gốc vẫn đi qua `COS_WORKSPACES`.
- **Không dựng khoá giữa hai tiến trình** cho store (`spec.md` open question 11). Khoá trong
  tiến trình là tất cả những gì bước 6 làm.
- **Không dựng khoá giữa pull và session đang chạy** (`spec.md` C7). Pull vẫn đổi file dưới
  chân Claude giữa lượt.
- **Không dọn thư mục tạm bị bỏ quên** sau một clone hỏng (`spec.md` C5).
- **Không chứng minh trang bằng công cụ điều khiển trình duyệt** (`spec.md` C11). `intent.md`
  đặt "không cần trình duyệt" thành một phần của kết quả, nên việc đó cần intent riêng.
- **Không đếm hạn mức.** Vẫn như `0002`.
- **Không bật tool nào cho session.** Bốn knob giữ nguyên mặc định.
