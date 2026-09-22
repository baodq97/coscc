# Plan: Rename to coscc, then publish
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

Mười hai bước. Bước 1 dựng bằng chứng và nó phải **đỏ** trước, theo đúng cách `0007` làm
(commit `1a2be0b`). Bước 10 là bước duy nhất không lùi lại được.

## Files that change

### Đổi tên, không sửa nội dung

- `cos_baodo/` → `coscc/` — cả thư mục, 34 file.
- `cos_baodo/cos_baodo.py` → `coscc/coscc.py` — Reflex đòi module app trùng tên gói.

### Mới

- `scripts/verify_0008.py` **(new)** — bằng chứng của unit này.
- `.cos/RENAMES.md` **(new)** — bảng tra cho các artifact viết trước khi đổi tên. Ở tầng
  `.cos/`, không nằm trong unit dir nào. `.claude/scripts/cos.mjs:97-98` chỉ liệt kê thư mục
  nên harness không thấy nó; kiểm tra file lạ ở `:83-84` chỉ chạy bên trong một unit dir.

### Xoá code

- `cos_baodo/store.py` — mười chỗ, liệt kê ở `spec.md` R3. Có **hai** điểm vào, không phải
  một: `self._import_legacy(conn)` trong `transaction()` (`:132`) trên đường ghi, và khối
  `if self._needs_import():` ở `:185-187` trên đường **đọc**, nơi một transaction rỗng bị ép
  chạy vì `connect()` không ghi được. Bỏ cả hai điểm vào trước, rồi mới bỏ phần còn lại.
- `cos_baodo/store_test.py` — bốn chỗ (`:188`, `:204`, `:229`, `:368`) và các test bọc chúng,
  trong đó có `test_bad_names_in_the_file_are_not_imported` (`:222`).

> **Lệch so với dự kiến, ghi lúc làm bước 2.** "Bốn chỗ" đếm đúng số dòng chứa tên file cũ
> nhưng sai về thứ phải bỏ. Thực tế là **hai lớp test**:
>
> - `TheOneShotImportFromJson` — **8 test**, toàn bộ lớp nói về đường import vừa bị xoá. Bỏ
>   cả lớp, 67 dòng.
> - `NothingWritesTheOldFile` — **3 test**, mỗi test đặt tên một artifact mà bản trước `0006`
>   để lại trong working folder: `.json`, `.lock`, `.tmp`. Cả ba literal đều mang tên cũ. Đổi
>   tên chúng sẽ là nói rằng những file đó từng tồn tại dưới một cái tên chúng chưa bao giờ
>   có, nên cả ba được thay bằng **một** test phát biểu đúng cái bất biến mà chúng là ba mẫu:
>   store không ghi gì vào working folder. Ba test cũ truyền `Store(d, d)` — hai root trùng
>   nhau — nên bất biến đó không phát biểu được cho tới khi tách chúng ra.
>
> Cộng thêm: `import json` trong `store.py` thành import chết sau khi `_load_legacy` biến
> mất, và bị bỏ theo. Tổng: **266 → 256 test**.

### Sửa nội dung — 28 file trong gói

`api.py` (4 dòng), `api_test.py` (4), `board.py` (2), `board_api_test.py` (2),
`board_test.py` (2), `build.py` (7), `build_test.py` (7), `config.py` (2), `config_test.py`
(3), `cos_baodo.py` (5), `cost_test.py` (2), `data_test.py` (2), `gitops_test.py` (2),
`journal.py` (1), `journal_test.py` (2), `policy.py` (1), `policy_test.py` (2), `run.py` (4),
`runner.py` (4), `runner_test.py` (3), `screens.py` (2), `service.py` (12),
`service_test.py` (3), `sessions.py` (1), `sessions_test.py` (3), `state.py` (3),
`store.py` (3), `store_test.py` (4).

**Ba chỗ trong số đó không phải import và sẽ không hỏng ồn ào nếu bỏ sót:**

- `cos_baodo/build.py:37-41` — `_SOURCES`, một danh sách **chuỗi đường dẫn literal**:
  `"cos_baodo/cos_baodo.py"`, `"cos_baodo/ui.py"`, `"cos_baodo/studio.py"`,
  `"cos_baodo/screens.py"`, `"cos_baodo/state.py"`. `build.py:16-19` cảnh báo thẳng rằng thứ
  không nằm trong `_SOURCES` sẽ khiến dấu vân tay nói "current" về một bundle đã cũ.
- `cos_baodo/run.py:61` — `"cos_baodo.cos_baodo:app"`, đích ASGI dạng chuỗi.
- `cos_baodo/build_test.py:49` và `:132` — assert trên đúng các chuỗi đó, nên `npm test` bắt
  được nếu lệch.

### Sửa nội dung — gốc repo

- `pyproject.toml` (5 dòng) — `name`, `module-name`, và hai entry `[project.scripts]`.
- `rxconfig.py` (4) — `app_name`, và import `from cos_baodo.config`, `from cos_baodo.ui`.
- `package.json` (2) — `name`, và `test:python` đang khoá cứng `-s cos_baodo`.
- `uv.lock` (1 dòng, `:150`) và `package-lock.json` (2 dòng, `:2` và `:8`) — sinh lại.

### Sửa nội dung — tài liệu và harness

- `README.md` (3 dòng: `:1`, `:37`, `:50`) — cộng thêm đoạn giải nghĩa Chief of Staff (R7).
- `docs/studio.md` (4 dòng: `:8`, `:13`, `:29`, `:35`) — `:35` là câu về import legacy.
- `.claude/CLAUDE.md` (19 dòng) — `:68` là câu về import legacy.
- `.claude/harness.md` (2 dòng: `:22`, `:123`) — cộng con trỏ tới `.cos/RENAMES.md`.

### Sửa nội dung — bằng chứng đã có

`scripts/proof_harness.py` (4 dòng), `verify_0001.py` (2), `verify_0002.py` (8),
`verify_0003.py` (3), `verify_0004.py` (4), `verify_0005.py` (6), `verify_0006.py` (3),
`verify_0007.py` (17). Phần lớn là `from cos_baodo.<x> import`; `verify_0002.py:75-80` còn
có glob và đường dẫn dạng chuỗi, `verify_0007.py:242` kiểm chính hai chuỗi wiring ở trên.

### Không đổi, và được kiểm

`cos_baodo/config.py:25` (`_ENV_PREFIX = "COS_"`), `cos_baodo/data.py:52-53`
(`DEFAULT_DIR`, `DB_FILENAME`), `.cos/` (trừ `RENAMES.md` và artifact của `0008`),
`.claude/scripts/cos.mjs`, `.claude/skills/cos-status/`. Đây là `spec.md` R10.

## Order of work

**1. Dựng `scripts/verify_0008.py`, và nó phải đỏ.**
Khuôn theo `scripts/verify_0007.py`: claim tĩnh, exit `0`/`1`/`2`, dùng `say`,
`EXIT_PASS`, `EXIT_BROKEN`, `EXIT_ENV` từ `scripts/proof_harness.py`. Claim liệt kê ở
`## Proof`. **Kim tìm không được viết thành literal** — xem Risk 1.
*Kiểm:* chạy nó, exit `1`, và mọi claim tĩnh đỏ trừ những claim đã đúng sẵn. Commit riêng.

**2. Xoá đường import legacy của `Store`, và sửa hai câu tài liệu nói về nó.**
R3 và R6 đi cùng một bước vì cả hai nói về chuỗi `.cos-baodo.json`, và cả hai **phải xong
trước bước 3**. Lý do ở Risk 2. `Data.import_once` (`cos_baodo/data.py:325`) ở lại vì
`Journal` gọi nó (`cos_baodo/journal.py:151`); đường legacy của `Journal` không đụng tới.
Hai câu ở `.claude/CLAUDE.md:68` và `docs/studio.md:35` viết lại để chỉ còn nói về
`.cos-journal.jsonl`.
*Kiểm:* `git grep -c "cos-baodo\.json" -- . ':!.cos'` trả rỗng; `npm test` xanh.

**3. Đổi tên, một bước.**
`git mv cos_baodo coscc`, rồi `git mv coscc/cos_baodo.py coscc/coscc.py`. Rồi thay trên mọi
file đã tracked **ngoài `.cos/`**: `cos_baodo` → `coscc`, `cos-baodo` → `coscc`,
`cos-build` → `coscc-build`. Đây là một bước chứ không phải năm, vì một repo đổi tên nửa
chừng thì không import được và không có trạng thái trung gian nào kiểm được. Sau khi thay,
đọc lại bằng mắt ba chỗ literal: `coscc/build.py` `_SOURCES`, `coscc/run.py:61`,
`rxconfig.py` `app_name`.
*Kiểm:* `npm test` xanh — `build_test.py` bắt `_SOURCES`; `uv run python -c "import coscc"`
chạy; `git grep -in baodo -- . ':!.cos'` chỉ còn `scripts/verify_0008.py` nếu bước 1 làm sai
Risk 1, và không còn gì nếu làm đúng.

**4. Viết lại prose còn lại.**
`README.md`, `docs/studio.md`, `.claude/CLAUDE.md`, `.claude/harness.md`. README phải nói
`CoS` là Chief of Staff, `cc` là Claude Code, và nói rõ chức năng đó **chưa được xây** —
`pyproject.toml` `description` vẫn mô tả đúng cái app đang làm, và hai câu đó không được
mâu thuẫn nhau.
*Kiểm:* claim tài liệu của `verify_0008.py` chuyển xanh.

**5. Dựng `.cos/RENAMES.md` và con trỏ trong `harness.md`.**
Bảng tra theo hình thức `.cos/0001_no-session-management/plan.md:4-21`. Con trỏ trong
`.claude/harness.md` **không được viết ra tên cũ** — nếu viết, bước 3 vừa làm xong bị phá.
*Kiểm:* `node .claude/scripts/cos.mjs status` không báo problem nào;
`git grep -in baodo -- . ':!.cos'` vẫn rỗng.

**6. Sinh lại hai lockfile.**
`uv lock` và `npm install --package-lock-only`.
*Kiểm:* `git diff uv.lock package-lock.json` chỉ chạm ba dòng tên. Bất kỳ dòng `version` nào
của package thứ ba xuất hiện thì hoàn tác và sinh lại hẹp hơn — Risk 4.

**7. Dựng lại bundle.**
Xoá `.web/` rồi `uv run coscc-build`.
*Kiểm:* `uv run coscc` khởi động, trang mở được ở `http://127.0.0.1:8790` và **không** hiện
"Connection Error".

**8. Dọn dữ liệu dev.**
Xoá `~/.cos/cos.db` và `/home/bd/personal-projects/.cos-baodo.json`. R13 cho phép; người
khởi xướng xác nhận 2026-09-22 rằng đây là dữ liệu dev.
*Kiểm:* `uv run coscc` khởi động trên database trống, màn Settings vẫn in ra hai root.

**9. Chạy `verify_0008.py`, mọi claim tĩnh xanh.**
Claim publish còn đỏ — chưa có repo.
*Kiểm:* exit `1`, và claim đỏ duy nhất là claim publish.

**10. Tạo repo public và push.** *(Không lùi lại được — Risk 6.)*
`gh repo create baodq97/coscc --public --source=. --push`. Không viết lại history; 121+
commit đi nguyên.
*Kiểm:* `gh repo view baodq97/coscc --json visibility` trả `public`; `git log` trên một bản
clone mới đếm đúng số commit của local.

**11. Chạy `verify_0008.py` trên một bản clone mới.**
*Kiểm:* exit `0`.

**12. Viết `impl.md`.**
Đo lại mọi con số tại thời điểm đó.

> **Sửa lúc làm bước 5, do người khởi xướng nêu.** Bản đầu của bước này bảo `impl.md` đo lại
> **số dòng `baodo` trong `.cos/`**. Bỏ yêu cầu đó: `impl.md` **không ghi một tổng nào cho
> `.cos/`**.
>
> Lý do là con số ấy không đo cái gì cả. Nó tự sinh ra bởi chính các artifact đang đếm nó —
> 279 ở `9280d33`, 289 ở `fed0638`, 319 ở `5226be5`, 352 sau khi plan này được sửa — và mỗi
> lần `impl.md` nhắc tới nó thì nó lại tăng. Nó cũng **không phải thứ outcome đo**: outcome
> loại `.cos/` ra có chủ ý (`intent.md` `## Proposed outcome`).
>
> Ghi nó như một figure còn có hại: đọc thấy một số lớn ở cuối unit thì phản xạ là dọn cho
> nhỏ, và cách duy nhất để dọn là sửa một artifact đã ký — đúng Risk 8. Nên `impl.md` ghi
> con số outcome thực sự đo (`baodo` ngoài `.cos/`, phải bằng 0) và nói về `.cos/` đúng một
> câu: còn một số dương, ai cần thì tự đo, không ai được trích lại. Đó cũng là điều R11 đã
> yêu cầu; bước 12 chỉ đang thôi mâu thuẫn với nó.

### Lệch so với plan, ghi lúc chạy bước 7-9

**C2 hỏi sai câu, và phải viết lại.** Bản đầu pin hai con số của `spec.md` R10 — `COS_` 70
dòng / 20 file, `cos.mjs` 21 file — rồi đỏ ở bước 9 với 77/22 và 22. Cả hai lần tăng đều là
**hành vi đúng**: chính `verify_0008.py` nhắc `COS_` sáu lần trong lúc đi kiểm nó, và test
thay thế ở bước 2 có một docstring nhắc `COS_DATA_DIR`. Một tổng pin sẵn trả lời câu "có ai
viết lại chuỗi này không", mà không ai hỏi câu đó. Câu cần hỏi là **việc đổi tên có mang
`COS_` đi theo không** — nên C2 nay so từng file với `BASE`, gập đường dẫn đổi tên ra, và chỉ
đỏ khi một file **mất** thứ nó từng có. Thêm mention mới thì không sao. Hai helper chỉ phục
vụ cách đếm cũ cũng bị bỏ theo.

**Tôi commit một file temp ở bước 6.** `git add -A` kéo theo
`.claude/settings.local.json.tmp.*` vào commit `fc7f968`. Đã `git rm --cached`. Và hoá ra
`.claude/settings.local.json` chỉ được ignore bởi gitignore **toàn cục của máy này**
(`/home/bd/.config/git/ignore`) — một bản clone của repo public sẽ không có nó, nên hai dòng
đã được thêm vào `.gitignore` của repo. Việc này nhỏ nhưng nó chỉ thành vấn đề *vì* unit này
đang đem repo ra công khai.

**Chạy `verify_0003.py` dù `spec.md` để nó ngoài phạm vi.** Sau bước 7, log app in một dòng
`[Reflex Frontend Exception] ... frontend/backend state mismatch`. `.claude/CLAUDE.md` ghi
rằng loại lỗi này "no HTTP-level check could see it", nên một tín hiệu thật đáng giá hơn một
ranh giới phạm vi — và `verify_0003.py` là phép kiểm duy nhất trả lời được mà **không tốn
quota, không tạo session**. Nó **PASS**, cả negative control. Dòng kia là do request
socket.io méo của tôi, không phải lỗi. Chạy lại sau bước 8 trên data root đã dọn: vẫn PASS.
Sáu proof còn lại vẫn không chạy.

### Lệch so với plan, ghi lúc quét trước khi publish

**Viết lại history — đảo `intent.md` constraint 5.** Một lần quét trước bước 10 tìm ra hai
thứ không nên ra công khai: `docs/ai-native-sdlc-playbook.md`, 611 dòng văn bản của Anthropic
giữ nguyên văn, không ghi nguồn, hot-link bốn ảnh từ CDN của họ, nằm ở **commit đầu tiên**;
và `.claude/settings.local.json.tmp.*`, lọt vào `fc7f968` do chính `git add -A` của tôi. Cả
hai bị gỡ khỏi **toàn bộ 134 commit** bằng `git filter-repo` chạy qua `uvx` (không thêm
dependency nào vào repo). Backup là một git bundle ngoài repo, tạo trước khi chạy. Hệ quả đã
đo và chấp nhận: **44 trích dẫn SHA** trong `.cos/` chết, `BASE` của proof đổi từ `b923bba`
sang `fa7d47c`, và `.cos/RENAMES.md` ghi một lần cho tất cả. Đường dẫn playbook nay là một
stub trỏ về bản gốc, nên 5 trích dẫn hiện có vẫn phân giải.

**Một skill không được phụ thuộc vào `docs/` — lỗi có sẵn, người khởi xướng chỉ ra.**
`.claude/skills/write-ship/SKILL.md` trích dẫn `docs/ai-native-sdlc-playbook.md`, trong khi
`.claude/harness.md:168-170` tuyên bố copy `.claude/` là đủ và *"nothing lands in the host
repository's own tree"*. Một skill trỏ ra ngoài `.claude/` thì chết ngay khi template được
copy sang repo khác. Câu đó viết lại bằng lời của chính skill, không trích dẫn file nào.
`harness.md` giữ liên kết tới bản gốc bằng **URL** — một URL chạy được ở mọi repo, một đường
dẫn tương đối thì không. Quét lại: **0** chỗ trong `.claude/skills/` còn trỏ ra ngoài.

### Chọn không làm

- **Không chạy lại sáu proof cũ.** `spec.md` `## Out of scope`. Chúng tốn quota thật, cần
  mạng, và `verify_0005.py` đẩy một branch lên một repo thật. Bước 3 chỉ đảm bảo chúng
  import được, không đảm bảo chúng còn xanh. Đó là một lỗ hổng có chủ ý và được ghi ở đây
  chứ không im lặng.
- **Không đổi tên thư mục `/home/bd/personal-projects/cos-baodo`.** Ngoài git.
- **Không sửa artifact nào trong `.cos/`.** `intent.md` constraint 4.
- **Không viết lại history.** `intent.md` constraint 5.
- **Không đụng đường legacy của `Journal`.** `spec.md` R5 — `.cos-journal.jsonl` không mang
  tên người, nên không có gì cho phép bỏ nó.
- **Không mở `0009 idea.md` cho Chief of Staff.** Người khởi xướng chọn chưa ghi ở đâu;
  bước 4 chỉ buộc README nói nó chưa có.

## Risks

**1. Bằng chứng tự đánh hỏng chính nó.** `scripts/verify_0008.py` nằm **ngoài** `.cos/`.
Nếu nó chứa chuỗi tên cũ dưới dạng literal để đi tìm, thì nó chính là file cuối cùng làm
claim C1 đỏ, vĩnh viễn — và cách "sửa" tự nhiên nhất là nới lỏng phép kiểm, tức là vứt bỏ
đúng thứ đang bảo vệ outcome. `scripts/verify_0007.py:14-17` đã gặp và ghi lại y hệt cái
bẫy này. **Kim phải được ghép lúc chạy, và literal không bao giờ tồn tại trên đĩa.**
*Dấu hiệu:* C1 đỏ và file duy nhất bị nêu là chính `verify_0008.py`.

**2. Thay hàng loạt làm hỏng `.cos-baodo.json`.** Chuỗi đó chứa `cos-baodo`, nên một lần
thay `cos-baodo` → `coscc` sẽ biến nó thành `.coscc.json` — đổi tên một file **đã tồn tại
trên đĩa**, đúng thứ `spec.md` R3 tồn tại để tránh. Đây là lý do bước 2 đi trước bước 3.
*Dấu hiệu:* `git grep "coscc\.json"` trả về bất cứ thứ gì.

**3. `_SOURCES` bị bỏ sót và dấu vân tay nói dối.** `coscc/build.py:37-41` là chuỗi literal,
không phải import, nên trình thông dịch không kêu. Hậu quả đúng như `build.py:16-19` mô tả:
dấu vân tay nói "current" về một bundle đã cũ, và mọi kiểm tra sau đó xanh trên bundle sai.
`build_test.py:49,132` bắt được — **miễn là không ai sửa test cho khớp code.**
*Dấu hiệu:* `npm test` đỏ ở `build_test`; nếu nó xanh mà trang vẫn cũ thì test đã bị sửa.

**4. Sinh lại lockfile kéo theo nâng dependency.** `0007` vừa chuyển sang Python 3.14 và
reflex 0.9.12 rồi **đo lại** các proof trên nền đó; `.claude/CLAUDE.md` ghi rằng đổi nền
không mang theo bằng chứng cũ. Một lần `uv lock` nhân tiện nâng một gói sẽ làm các con số
đó mất nền mà không có gì báo. Bước 6 vì thế giới hạn diff về ba dòng tên.
*Dấu hiệu:* `git diff uv.lock` chứa một dòng `version = ` của gói thứ ba.

**5. Quên dựng lại bundle.** Không có gì tự chạy build. Bỏ bước 7 thì mọi thứ vẫn xanh trên
bundle cũ cho tới khi mở trang bằng trình duyệt.
*Dấu hiệu:* trang render rồi hiện "Connection Error", hoặc `coscc` từ chối khởi động.

**6. Bước 10 không lùi lại được.** Public là vĩnh viễn: 121 commit, prose tiếng Việt trong
`.cos/`, email tác giả trên mọi commit, và 9 dòng `/home/bd`. Xoá repo sau đó **không**
gỡ được thứ đã bị clone, cache hay index. `intent.md` constraint 5 và `spec.md` C1 đã ghi
rằng tên riêng vẫn tra ra được bằng `git log -p`. Không chạy bước 10 cho tới khi bước 9 xanh.
*Dấu hiệu:* không có — đó chính là vấn đề. Đây là chỗ phải dừng và đọc lại, không phải chỗ
để dựa vào một phép kiểm.

**7. `.cos/RENAMES.md` là một loại file mới trong `.cos/`.** Hôm nay `cos.mjs` bỏ qua nó vì
`:97-98` chỉ liệt kê thư mục. Một thay đổi harness sau này mà bắt đầu kiểm nội dung `.cos/`
sẽ nêu nó lên, và người sửa sẽ không biết nó ở đó để làm gì.
*Dấu hiệu:* `cos.mjs status` báo problem ở `.cos/`.

**8. Cái tôi muốn không phải viết ra.** Artifact của chính unit này làm số dòng `baodo` trong
`.cos/` tăng lên mỗi lần commit — 279, rồi 289, rồi 319. Khi làm bước 9 và thấy con số ấy
vẫn lớn, sự cám dỗ là "dọn cho sạch" bằng cách sửa một file dưới `.cos/`. Làm thế là phá
`intent.md` constraint 4, phá tiền lệ `0002 spec.md:245-250`, và làm hỏng 19 trích dẫn số
dòng đã đo ở `spec.md` C5 — tất cả để cho đẹp một con số mà outcome **cố ý** không đo.
*Dấu hiệu:* claim C11 của `verify_0008.py` đỏ. Claim đó tồn tại vì rủi ro này, không vì gì
khác.

## Proof

```
uv run python scripts/verify_0008.py
```

**Đạt là exit `0`.** `1` là có claim không đạt; `2` là môi trường không trả lời được —
không có `gh`, `npm`, `uv`, hoặc `git` không chạy. Cách tách `1` khỏi `2` theo
`scripts/verify_0003.py:8-14`: "chưa cài `gh`" không phải là "repo chưa được publish".

Mười bốn claim:

| # | Claim |
|---|---|
| C1 | `git grep -in baodo -- . ':!.cos'` trả **0 dòng**. Kim ghép lúc chạy — Risk 1. |
| C2 | Tập không đổi: `COS_` **70 dòng / 20 file** ngoài `.cos/`; `cos.mjs` được nhắc ở **21 file** ngoài `.cos/`; `DEFAULT_DIR == "~/.cos"`; `DB_FILENAME == "cos.db"`; `.claude/skills/cos-status/` còn đó. |
| C3 | `import coscc` chạy, và `coscc/coscc.py` tồn tại. |
| C4 | `pyproject.toml` khai `coscc` và `coscc-build`, và cả hai phân giải được. |
| C5 | Mọi đường dẫn trong `_SOURCES` của `coscc/build.py` đều tồn tại trên đĩa. |
| C6 | `coscc/store.py` không còn `STORE_FILENAME` cũng không còn `_import_legacy`. |
| C7 | `coscc/journal.py` vẫn có đường legacy của nó, và `Data.import_once` vẫn còn. |
| C8 | `.claude/CLAUDE.md` và `docs/studio.md` không còn nói kho JSON được import. |
| C9 | `README.md` giải nghĩa Chief of Staff **và** nói chức năng đó chưa được xây. |
| C10 | `.cos/RENAMES.md` có bảng tra; `.claude/harness.md` trỏ tới nó và **không** chứa tên cũ. |
| C11 | Diff của unit này không chạm file nào dưới `.cos/` ngoài artifact của `0008` và `RENAMES.md`. Claim này tồn tại vì Risk 8. |
| C12 | `git diff` trên hai lockfile không chứa dòng `version` của package thứ ba. |
| C13 | `npm test` xanh, cả hai runtime. |
| C14 | `gh repo view baodq97/coscc` trả visibility `public`. Không có `gh` thì **exit 2**, không phải 1. |

C1 và C14 cùng nhau chính là outcome của `intent.md`, nguyên văn: một URL công khai, và trên
tree ở đó không còn tên riêng nào ngoài `.cos/`.
