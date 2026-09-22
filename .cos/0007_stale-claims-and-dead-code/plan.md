# Plan: Correct the repository's claims about itself, then move it onto current versions
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

## Files that change

`spec.md` ước "mười lăm file đang có". Đếm lại khi đọc từng file thì ra **hai mươi**, cộng
một file có điều kiện. Năm file spec không tính tới: hai lock file được sinh lại, hai file
tài liệu có nhắc `objects/`, và `.python-version`. Ghi ra vì con số ở `spec.md` là con số
sai chứ không phải con số cũ.

**Sửa — citation và figure (R1, R2):**

| File | Chỗ | Việc |
|---|---|---|
| `cos_baodo/journal.py` | `:13-14` | "12 of 20" đang không có nguồn; dẫn về `.cos/0004_silent-concurrent-loss/plan.md:115` |
| `cos_baodo/journal_test.py` | `:4-5` | con số ở `:4`, citation `cos_baodo/store.py:16-18` ở `:5` → cùng dòng `0004` như trên |
| `rxconfig.py` | `:11` | `cos_baodo/config.py:60` → `:66`, nơi `host` thật sự được mặc định |
| `scripts/verify_0005.py` | `:8` | `scripts/verify_0003.py:49` → `scripts/proof_harness.py:36` |
| `cos_baodo/store_test.py` | `:264` | docstring nêu "12 of 20" không nguồn; dẫn về cùng dòng `0004` |

**Sửa — tài liệu tả một repo khác (R3, R4, R5):**

| File | Chỗ | Việc |
|---|---|---|
| `README.md` | `:3-4` | vòng lặp ba stage → tám, khớp `STAGES` ở `.claude/scripts/cos.mjs:24-33` |
| `.claude/scripts/cos.test.mjs` | `:123` | tên test bỏ số đếm unit; không đổi thân test |
| `cos_baodo/ui.py` | `:14`, `:30` | bỏ `BREAKPOINTS`; câu về R23/R24 trỏ `scripts/verify_0003.py:180` |

**Sửa — bỏ tầng object store (R6, R7):**

| File | Chỗ | Việc |
|---|---|---|
| `cos_baodo/data.py` | `:54`, `:153` | bỏ `OBJECTS_DIRNAME` và `self.objects_dir` |
| `cos_baodo/data_test.py` | `:49-53` | bỏ assert về `objects_dir`; đổi tên `test_the_database_and_objects_sit_inside_it` |
| `.claude/CLAUDE.md` | `:52`, `:68` | bỏ `objects/` khỏi mô tả data root và bỏ câu về `cos_baodo/objects.py` |
| `docs/studio.md` | `:22` | bỏ `objects/` khỏi bảng hai gốc dữ liệu |

**Sửa — deprecation, rác và dependency (R8, R9, R10, R11, R12):**

| File | Chỗ | Việc |
|---|---|---|
| `cos_baodo/api.py` | `:50`, `:252-256` | `@api.on_event("shutdown")` → `lifespan=` truyền vào `FastAPI(...)`; `sessions` đã có ở `:47` |
| `cos_baodo/api_test.py` | thêm vào cuối | phép kiểm mới drive lifespan và khẳng định session bị đóng |
| `cos_baodo/store_test.py` | `:280-292` | `_holder` mở `Popen(stdout=PIPE)` và không đóng; thêm `self.addCleanup(child.stdout.close)` |
| `package.json` | `:12` | bỏ `@modelcontextprotocol/sdk` |
| `package-lock.json` | cả file | sinh lại bằng `npm install` |
| `.gitignore` | `:1`/`:17`, `:5`/`:16` | bỏ entry trùng, giữ dạng có dấu `/` cho thư mục |

**Sửa — version (R13, R14):**

| File | Chỗ | Việc |
|---|---|---|
| `pyproject.toml` | `:5`, `:6-10` | `requires-python` → `>=3.14`; floor của `claude-agent-sdk` từ `>=0.1.0` lên version đang cài; floor `reflex` lên `0.9.12` |
| `.python-version` | `:1` | `3.12` → `3.14` |
| `uv.lock` | cả file | sinh lại bằng `uv sync --upgrade` |

`requires-python` lên `>=3.14` chứ không giữ `>=3.11`: sau unit này không phép đo nào trong
repo được lấy trên 3.11 hay 3.12, nên một floor nói 3.11 là đúng loại câu không đúng mà unit
này đang đi dọn. Đây là app một người dùng local, không phải library, nên không có ai ở dưới
bị chặn.

**Xoá:**

- `cos_baodo/objects.py` — 123 dòng, không đường nào của app gọi tới.
- `cos_baodo/objects_test.py` — 115 dòng, **13 test**. Đo 2026-09-22, nên sau bước 4 số test
  Python đi từ 278 xuống 265, và đó là con số phải thấy chứ không phải dấu hiệu mất test.

**Thêm:**

- `scripts/verify_0007.py` **(new)** — lệnh quyết định pass/fail của unit này. Xem `## Proof`.

**Sửa có điều kiện — quyết định cho `spec.md` open question 1:**

- `.cos/0006_demo-data-and-no-durable-store/spec.md:144` — **sửa tại chỗ.** Con số ở đó sai
  chiều, không phải cũ: `0004` đo được **8 trên 20 còn lại**, dòng ấy lấy tám mục sống sót
  làm tám mục bị mất.
  Ba chỗ trong code đang nói chiều đúng, nên để nguyên là để một người đối chiếu bốn chỗ gặp
  một mâu thuẫn không có đường giải. Sửa gọn trong phạm vi con số cộng một citation về
  `.cos/0004_silent-concurrent-loss/plan.md:115`; không viết lại lập luận của C2, không thêm
  bình luận vào artifact cũ. Lần sửa được ghi ở đây và ở `impl.md`, nên dấu vết audit **thêm**
  một mục chứ không mất một mục, và git giữ bản gốc. Nếu tác giả không đồng ý thì lối còn lại
  là thêm một dòng ở `spec.md` của unit này nói dòng đó sai — và khi đó R1 phải được viết lại,
  vì nó đòi "không chỗ nào nói mất 8".

## Order of work

Mười một bước. Mỗi bước là một commit, và `npm test` phải xanh ở cuối mỗi bước (R16). Version
đi **sau** mười mục kia, vì `spec.md` C6 đòi một mục hỏng không được chặn mười lăm mục còn
lại — và nếu 3.14 hoặc `0.9.12` vỡ thì mọi thứ khác đã nằm trên `main` và xanh.

1. **Dựng `scripts/verify_0007.py` và chạy nó khi chưa sửa gì.** Nó phải thoát **1** và liệt
   kê đủ mười một claim đang đỏ. Đây là negative control của unit này, và nó mạnh hơn một cảnh
   hỏng dựng tay vì nó đỏ trên đúng cái cây đang có vấn đề. Kiểm được ngay: chạy, đọc, thấy
   `FAIL` ở C1–C11 trừ những claim tình cờ đã đạt.
2. **Figure và citation** (R1, R2). Năm file ở bảng đầu, cộng
   `.cos/0006_demo-data-and-no-durable-store/spec.md:144`. Kiểm: C1 và C2 của
   `verify_0007.py` chuyển `PASS`; `npm test` xanh.
3. **Tài liệu** (R3, R4). `README.md` và `.claude/scripts/cos.test.mjs`. Kiểm: C3 và C4
   `PASS`; `npm run test:node` xanh với 23 test.
4. **Bỏ tầng object store** (R6, R7). Xoá hai file, sửa `data.py`, `data_test.py`,
   `.claude/CLAUDE.md`, `docs/studio.md`. Kiểm: C6 và C7 `PASS`; `npm test` xanh với **265**
   test Python.
5. **`BREAKPOINTS`** (R5). Kiểm: C5 `PASS`.
6. **Dependency mồ côi** (R8). `package.json` và `npm install` để sinh lại
   `package-lock.json`. Kiểm: C8 `PASS`; `npm run test:node` xanh — nó chỉ dùng builtin của
   Node nên việc bỏ dependency không được làm nó đỏ.
7. **Shutdown sang lifespan, kèm phép kiểm của nó** (R10, và phần lớn R9). Kiểm: `npm test`
   xanh, và `npm test 2>&1 | grep -c DeprecationWarning` bằng **0** — hôm nay là 68.
8. **Đóng handle bị rò trong `store_test.py`** (phần còn lại của R9). Kiểm: C9 `PASS`, tức
   `npm test` in **0** dòng chứa `Warning`.
9. **`.gitignore`** (R11). Kiểm: C10 `PASS`.
10. **Version** (R13, R14). `.python-version` → 3.14, floor ở `pyproject.toml`,
    `uv sync --upgrade`, `npm outdated` rỗng. Kiểm: `npm test` xanh trên interpreter mới,
    C11 `PASS`, `uv pip list --outdated` rỗng.
11. **Dựng lại bundle và chứng minh trang** (R15). `uv run cos-build`, rồi
    `uv run python scripts/verify_0003.py` — cần cổng `COS_PORT` trống, app phải được tắt
    trước. Kiểm: build xong không in dòng nào chứa `Deprecat` (R12), và `verify_0003.py`
    thoát **0**.

Sau bước 11: `write-impl`, rồi `plan.md` chuyển `done`.

**Về `verify_0004.py`** (`spec.md` C2 và open question 4). Bước 10 đổi interpreter, nên
module `sqlite3` dưới chân khẳng định "20 trên 20" cũng đổi. Phép kiểm ấy tốn **một** session
thật với một prompt ngắn. Nó **không** nằm trong ba proof mà người khởi xướng để ngoài scope,
nên plan này đề nghị chạy nó ngay sau bước 10 — nhưng nó tiêu quota, nên nó chỉ chạy khi tác
giả nói chạy. Nếu họ không nói, `impl.md` phải ghi thẳng rằng con số 20/20 chưa được lấy lại
trên 3.14, và `.claude/CLAUDE.md:85` phải nói ngày đo cùng interpreter của lần đo đó.

## What this plan does not do

- **Không dựng máy dò citation.** `verify_0007.py` kiểm **ba** citation được nêu tên, không
  kiểm mọi citation trong repo. `intent.md` open question 2 để việc đó cho một unit riêng, nên
  sau unit này cái citation thứ tư vẫn trôi được mà không ai biết.
- **Không sửa `setting_sources`.** Người khởi xướng nói nó sẽ đổi. Nó vẫn là món lớn nhất còn
  mở.
- **Không chạy `verify_0001.py`, `verify_0002.py`, `verify_0005.py`.** Quyết định của tác giả
  ngày 2026-09-22. Bước 10 làm khoảng trống này rộng thêm chứ không hẹp lại: ba lệnh ấy giờ
  chưa được chạy trên cả cơ chế mới **và** interpreter mới.
- **Không tạo remote**, nên unit này cũng sẽ dừng ở `impl`, giống `0006`.
- **Không tách `screens.py` và `state.py`.**
- **Không lên Python 3.15.** Còn là release candidate.
- **Không xoá `~/.cos/objects/` của ai.** App thôi tạo, app không dọn — cùng lựa chọn mà
  `0002` C6 đã chốt cho workspace bị bỏ khỏi danh sách.

## Risks

**Risk 1 — Python 3.14 làm `uv sync` hỏng hoặc một wheel không có bản cho nó, và nó là bước
đứng trước bằng chứng của cả repo.** Blast radius lớn nhất vì nó đổi nền của mọi phép đo, và
vì nó chạm mọi dependency một lúc. Dấu hiệu: `uv sync --upgrade` lỗi, hoặc `npm test` đỏ ở
chỗ chẳng liên quan gì tới mười mục kia. Lối lùi: `.python-version` về `3.12` và `uv sync` —
một file một dòng, và vì nó là bước 10 nên chín bước trước vẫn còn nguyên trên `main`.

**Risk 2 — luật tên dành riêng của Reflex `0.9.12` (#7136) đụng vào một trong 112 state var.**
Ba trong bốn breaking change của bản đó không chạm tới đây: đo 2026-09-22, `cos_baodo/` không
dùng `router`, không gọi `.dict()`, không dùng `deps=`. Cái thứ tư chỉ lộ ra lúc chạy. Dấu
hiệu: `uv run cos-build` hoặc chỉ riêng việc import app đã raise. **Nếu nó đụng thì dừng, và
báo** — đổi tên state var là đổi trang, không phải clean-up, và nó cần một unit riêng. Không
tự đổi tên rồi đi tiếp.

**Risk 3 — phép kiểm lifespan mới xanh mà server thật vẫn không chạy lifespan.** Đây là risk
tôi không muốn viết ra, vì nó làm R10 trông chắc hơn thực tế. Phép kiểm drive app FastAPI
**một mình**; ở production Reflex mount app của nó **vào trong** app FastAPI
(`reflex/app.py:815`, đo 2026-09-22), nên uvicorn chạy lifespan của app ngoài cùng là app của
ta — hôm nay. Nếu một bản Reflex sau này đảo chiều mount thì phép kiểm vẫn xanh trong khi
session không được đóng, và cái bị rò là tiến trình CLI giữ `CLAUDE_CODE_OAUTH_TOKEN`
(`.cos/0001_no-session-management/spec.md:121`). Không có gì trong unit này bắt được lần đảo
đó. Cái bắt được là bước 11 cộng một lần tắt app bằng tay và xem tiến trình con có còn không
— và bước 11 không làm việc ấy.

**Risk 4 — xoá `objects.py` bỏ một tầng mà `0006` R3 và R11 đòi, và không gì báo khi cần nó
lại.** Dấu hiệu: không có. Chỉ git giữ. Đã ghi ở `spec.md` C5; người khởi xướng chọn lối này
biết cả hai lối.

**Risk 5 — sửa `.cos/0006.../spec.md:144` là sửa một artifact đã `accepted`.** Dấu hiệu:
không có; một người đọc lịch sử sẽ thấy artifact khác bản đã commit lúc accept. Cái làm nó
chịu được là lần sửa được ghi ở plan này và ở `impl.md`, và phạm vi bị giới hạn ở con số cộng
một citation. Nếu tác giả không đồng ý, xem mục có điều kiện ở `## Files that change`.

**Risk 6 — số test tụt 13 và ai đó đọc thành mất test.** Dấu hiệu: `npm test` báo 265 thay vì
278 sau bước 4. Lối phòng: con số đã viết ra ở đây và phải được nhắc lại trong thông điệp
commit của bước 4.

**Risk 7 — bước 11 không chạy được vì cổng bận.** `verify_0003.py` thoát `2` khi
`COS_PORT` đang có app, và nó không di chuyển được sang cổng khác vì bundle nhúng địa chỉ.
Dấu hiệu: exit 2 kèm câu bảo tắt app. Hậu quả nếu bỏ qua: trang là thứ duy nhất đi qua một
lần đổi framework mà không ai chứng minh — `spec.md` C3.

## Proof

```
uv run python scripts/verify_0007.py     # mười một claim tĩnh, không quota, không trình duyệt
npm test                                 # cả hai runtime
uv run cos-build                         # phải in 0 dòng chứa "Deprecat"
uv run python scripts/verify_0003.py     # cần COS_PORT trống; app phải tắt trước
```

Đạt khi: `verify_0007.py` thoát **0**, `npm test` xanh với **23** test Node và **265** test
Python **và không in dòng nào chứa `Warning`**, `cos-build` chạy xong không có dòng
`Deprecat`, `verify_0003.py` thoát **0**.

`scripts/verify_0007.py` **(new)** dựng theo `scripts/proof_harness.py` — dùng `say`,
`EXIT_PASS`, `EXIT_BROKEN`, `EXIT_ENV` từ đó và không dùng phần khởi động app, vì không claim
nào cần một server. Mười một claim:

| # | Claim | Đạt khi |
|---|---|---|
| C1 | Phép đo của `0004` chỉ có một chiều | `.cos/0004_silent-concurrent-loss/plan.md:115` chứa `8 trên`, và không file nào trong cây chứa chuỗi đọc ngược — `verify_0007.py` ghép chuỗi ấy từ `KEPT` và `TOTAL` nên chính nó cũng không chứa literal |
| C2 | Ba citation được nêu tên trỏ đúng | với mỗi cặp (file, `path:line` nó trích), dòng được trích chứa chuỗi neo khai trong script |
| C3 | `README.md` nêu tám stage | số stage trong `README.md` bằng số entry `stages` của `cos.mjs status --json` |
| C4 | Không tên test nào mang số đếm unit | `.claude/scripts/cos.test.mjs` không chứa `eight units` |
| C5 | Không hằng số bề rộng nào không ai đọc | không file nào trong `cos_baodo/` chứa `BREAKPOINTS` |
| C6 | Không module nào chỉ được import bởi test của chính nó | với mỗi `cos_baodo/X.py` không phải `_test`, có ít nhất một file khác `cos_baodo/X_test.py` nhắc `X` |
| C7 | Data root không còn `objects` | `Data(tmp).ensure_dir()` rồi liệt kê `tmp` không có `objects`, và `Data` không có thuộc tính `objects_dir` |
| C8 | Không dependency npm mồ côi | mỗi key trong `dependencies` của `package.json` xuất hiện trong một `import`/`require` dưới `.claude/scripts/` |
| C9 | `npm test` im | `npm test` thoát 0 và in **0** dòng chứa `Warning` |
| C10 | `.gitignore` không trùng | sau khi bỏ dấu `/` ở cuối, không entry nào xuất hiện hai lần |
| C11 | Version là bản mới nhất | `.python-version` là `3.14`, và mỗi dòng còn lại của `uv pip list --outdated` được nêu tên trong `impl.md` kèm ràng buộc chặn nó — dạng mềm mà `spec.md` R13 cho phép, vì 13 trong 15 dòng hôm nay là transitive bị chính `reflex` ghim |

Mã thoát: `0` mọi claim đạt, `1` có claim đỏ, `2` môi trường không trả lời được — thiếu `npm`
hoặc `uv`, hoặc `cos.mjs status --json` không chạy. C9 gọi `npm test` bên trong, nên lệnh này
mất khoảng thời gian của một lần `npm test` cộng vài giây; nó vẫn không cần cổng trống, không
cần trình duyệt và không tiêu quota.

`verify_0003.py` nằm ngoài `verify_0007.py` vì nó cần trình duyệt và một cổng trống, và
`.claude/CLAUDE.md` giữ `npm test` không có trình duyệt là điều cố ý. Ranh giới ấy là lý do
R15 phải được chạy bằng tay, và `spec.md` C3 ghi cái giá của nó.

## Departures

Ghi khi chúng xảy ra, theo `write-plan` invariant 8. `impl.md` gom lại.

1. **Bốn quyết định của tác giả, 2026-09-22.** `spec.md` OQ1 → sửa `0006 spec.md:144` tại
   chỗ. OQ4 → **chạy** `verify_0004.py` sau bước version, tốn một session. C11 → dạng mềm của
   R13 (bảng `## Proof` đã sửa). Chuỗi đọc ngược nằm trong chính `intent.md` và `plan.md` của
   unit này → **viết lại các câu trích bằng chữ**, nên C1 quét được cả cây mà không cần loại
   trừ ai; `intent.md:23` và hai dòng ở đây đổi theo.
2. **`ensure_dir` chưa bao giờ tạo `objects/`.** `cos_baodo/data.py:161-171` chỉ `mkdir`
   `self.root`; `objects_dir` là đường dẫn tính sẵn, và `mkdir` duy nhất nằm ở
   `cos_baodo/objects.py:74` bên trong `Objects.put` mà không ai gọi. Nửa đầu C7 vì vậy đã
   xanh trước khi sửa — negative control thật là `not hasattr(Data(tmp), "objects_dir")`.
   `spec.md:11-12` nói "app thôi tạo" là nói sai chiều.
3. **C6 đếm cả entrypoint gọi bằng chuỗi.** Một phép kiểm chỉ đọc import báo
   `cos_baodo/run.py` và `cos_baodo/cos_baodo.py` là chết, trong khi xoá cái nào cũng làm app
   không chạy: chúng được gọi qua `cos-baodo = "cos_baodo.run:main"` ở `pyproject.toml` và
   `"cos_baodo.cos_baodo:app"` ở `cos_baodo/run.py:61`.
4. **Citation trôi thứ tư, không sửa.** `.cos/0005_hand-driven-invisible-loop/spec.md:291`
   trỏ `cos_baodo/store.py:16-18` — cùng đích cũ như mục 4 của `intent.md`, và dòng đó giờ tả
   row SQLite. Nó là hiện thân cụ thể của "mục thứ mười bốn" mà `intent.md` OQ2 đoán trước.
   Tác giả chọn ghi lại chứ không chạm artifact accepted thứ hai.
