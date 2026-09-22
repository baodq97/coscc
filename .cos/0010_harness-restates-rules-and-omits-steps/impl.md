# Impl: Ba tầng tài liệu thành hai, và một trường header có máy đọc
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Bốn commit trên `fix/harness-restates-rules-and-omits-steps`.

**`6924a8b` — backfill `Type:` vào tám `intent.md` đã ký.** Một dòng header mỗi file, 8 file,
`+8 −8`. Bảy type đúng như plan đoán; một không — xem `## Where the plan was departed from`.

**`a5dfcff` — `readUnit` đọc `Type:` và báo problem.** Dùng lại `parseType` và
`BRANCH_TYPES` đã có, không tạo bản sao thứ hai của tập mười (`intent.md` constraint 7).
Thông điệp phân biệt **thiếu** với **sai**, cùng lối `missing()` đã phân biệt file vắng với
file không có Status. `checkGate` không đổi một dòng — spec R9. Năm test mới, một trong số
đó khẳng định thẳng rằng problem này **không** đóng cổng nào.

**`cd1304d` — xoá `.claude/harness.md`, viết `.claude/rules/coscc-app.md`, viết lại
`.claude/CLAUDE.md`.** Một commit, vì đúng lúc file biến mất thì `verify_0008` C10 và
`verify_0009` C6 không còn chỗ đọc.

**`3379b6d` — skill thôi trỏ ra ngoài, thủ tục branch về một file.** Hai skill theo plan,
ba skill nữa theo một yêu cầu giữa chừng.

### Bước 4 — `CLAUDE.md:18-151` giữ gì, bỏ gì

Phép thử của spec `## Design`: *có script, CI hay ruleset nào làm câu này sai được không?*
Có → bỏ. Không → giữ. **Đây là nơi duy nhất ghi lại 134 dòng đó đi đâu.**

| Đoạn | Quyết | Vì sao |
|---|---|---|
| Build bằng `coscc-build`, không `reflex export`; dấu vân tay | **giữ lệnh, bỏ giải thích** | `coscc` và `verify_0003` **từ chối chạy** khi bundle lệch nguồn. Sự từ chối tự nói ra |
| `npm test` không tự build | **giữ** | Không lệnh nào nói điều này; quên build là im lặng |
| `npm test` phủ hai runtime, thêm `*_test.py` là được nhặt | **bỏ** | Chạy `npm test` một lần là thấy |
| Một trang, sáu màn, `screens`/`state`/`service` | **giữ**, nén | Không test nào bắt được một handler tự quyết |
| Loopback, chat-only no tools mặc định | **giữ 1 dòng** | Mặc định có test; nhưng đây là tư thế an toàn, mất nó là mất im lặng |
| `policy.py` nằm **ngoài** `Config` | **giữ** | Không gì ngăn ai đó gộp nó vào |
| `("pr","autonomous")` với tới mọi repo `gh` login với tới | **giữ** | Hiểm hoạ, không lệnh nào nêu |
| Mỗi session tiêu quota | **giữ** | Như trên |
| Hai root, backup cái này không backup cái kia | **giữ** | Không lệnh nào nói |
| `config.from_env` là reader duy nhất, không có setter | **bỏ** | Có test; và không có setter thì không có gì để làm sai |
| Workspace là **tên**, không phải path | **giữ 1 dòng** | Lý do store sửa tay không trỏ được app vào `/etc` |
| `busy_timeout` **trước** WAL, đo 1/10 lần hỏng | **giữ** | Sai thứ tự chỉ làm flaky; không test nào bắt |
| `BEGIN IMMEDIATE` quanh read-modify-write | **giữ** | Như trên |
| `PRAGMA user_version`, `Store`/`Journal` đổi backing | **bỏ** | Lịch sử; hành vi có test |
| `Journal` còn đọc file cũ, `Store` thì không | **bỏ** | Có test (`the journal still imports its own legacy file`) |
| Bảng sáu proof và giá của từng cái | **giữ** | Quota, browser, port, push — không cái nào có lệnh cảnh báo |
| `verify_0004` đo lại 20/20 trên SQLite và Python 3.14.4 | **bỏ số, giữ nguyên tắc** | Số nằm ở artifact của unit đó; nguyên tắc "đổi cơ chế thì số không theo" thì không |
| `pull` chỉ từ chối trong process này | **giữ** | Hở đã biết, chưa sửa |
| `verify_0003`/`0006` cần `COS_PORT` rảnh, không chạy cùng lúc | **giữ** | Chạy cùng lúc chỉ hỏng khó hiểu |
| Board đọc lane từ status, **không** từ `blocked` | **giữ** | Một phiên sau này rất dễ "sửa" ngược lại |
| Sáu stage văn xuôi không có tool, app ghi file hộ | **giữ** | Trái một câu trong `## Design` của `0005`; phải nói ra |
| `board.py` chạy `cos.mjs` của **repo này** với `--root` | **bỏ** | `cos.mjs` tự từ chối `--root` ở ba lệnh, và chính file đó mang comment giải thích |
| Build nướng cứng port vào bundle | **bỏ** | `coscc` **không khởi động** khi build và run lệch |
| `reflex run` bind `*:3000` | **giữ** | Không gì ngăn ai đó gõ `reflex run` |

Giữ lại thành `.claude/rules/coscc-app.md`, **85 dòng**, nạp theo `paths:` khớp `coscc/**`,
`rxconfig.py`, `scripts/verify_*.py`.

### Bước 5 — `harness.md` 295 dòng đi đâu

| Đoạn | Quyết |
|---|---|
| `:1-8` file tự giới thiệu | chết theo file |
| `:10-13` con trỏ `RENAMES.md` | **sang `CLAUDE.md`**, 2 dòng |
| `:15-31` playbook, lịch sử 3→8 stage, "không hook không CI" | **bỏ** — lịch sử; phần autonomous sang rule file |
| `:33-40` cơ chế vs phán đoán, cổng vẫn advisory | **sang `CLAUDE.md`**, nén còn một gạch đầu dòng ở `## What is deliberately not built` |
| `:42-61` bảng tám stage | **bỏ** — đúng thứ `cos.mjs status` in ra, và là bản sao `STAGES` |
| `:63-68` `cos-status` không ghi gì; chuỗi commit | **bỏ** |
| `:70-81` ngữ pháp `NNNN_<slug>` | **bỏ ngữ pháp** (`new-path` kiểm), **giữ** câu slug đặt theo vấn đề |
| `:83-92` danh sách status hợp lệ | **bỏ** (`cos.mjs` kiểm); **giữ** "accepted không phải phê duyệt" |
| `:94-104` không có bước phê duyệt, và đó là đánh đổi | **sang `CLAUDE.md`** |
| `:106-117` spec skip | **bỏ** — năm tiêu chí đã ở `write-spec` và chỉ được có một bản |
| `:119-128` danh sách lệnh | **sang `CLAUDE.md`**, và **sửa**: 3 lệnh → **7**, khớp bảng `run` |
| `:130-140` `--root` là ranh giới, vì sao `cos.mjs` được test | **bỏ** — `cos.mjs` tự từ chối, và comment của nó đã nói |
| `:142-149` ngôn ngữ đầu ra | **bỏ** — đã là invariant ở `CLAUDE.md`, đây là bản trùng |
| `:151-172` sáu mục không xây | **sang `CLAUDE.md`**, nén còn bốn |
| `:176-180` cảnh báo ruleset không đi theo bản copy | **sang `CLAUDE.md`** nguyên câu |
| `:181-216` ngữ pháp branch, squash, rebase | **giữ tên quy tắc, bỏ giải thích** — `check-branch` kiểm |
| `:218-227` `unit-branch`, `check-branch` | **sang `CLAUDE.md`** thành thủ tục sáu bước |
| `:228-229` *"`Type:` … absent from the eight before it … not backfilled"* | **bỏ, vì bước 1 làm nó sai** |
| `:231-256` ngữ pháp tag, năm chỗ version | **giữ tên, bỏ giải thích** — `check-tag`, `check-version` kiểm |
| `:258-265` lệnh nào nhận `--root` | **sang `CLAUDE.md`**, 2 dòng |
| `:267-280` bảng "cái gì không đi theo bản copy" | **nén còn một đoạn** ở `CLAUDE.md` |
| `:282-295` copy sang repo khác | **sang `CLAUDE.md`**, nén |

## Where the plan was departed from

Bốn, tất cả đã ghi vào `plan.md` ở đúng commit gây ra chúng.

**1. `0004` là `fix`, không phải `feat`.** Plan đoán `0001`–`0006` đều `feat`. Risk 2 bảo đọc
`## Problem` trước khi gõ; lần đọc đó cho câu khác: *"Two writers at once lose work without
saying so"* là khuyết tật, và `plan.md` của nó sửa `store.py`, `sessions.py`, `service.py`
mà không thêm gì người dùng thấy. `0003` giữ `feat` sau cùng phép đọc — nó thêm
`cos_baodo/build.py` mà `run.py` phụ thuộc vào.

**2. Hai file plan nói "không đổi" phải đổi.** `.github/workflows/pr.yml` (một comment và
một tên job) và `.claude/skills/write-review/SKILL.md` (một câu) đều trỏ tới `harness.md`.
Bỏ qua là để lại bốn trích dẫn chết ngay trong file còn sống.

**3. `verify_0008` đã đỏ sẵn trên `main` trước khi branch này tồn tại — và không ai biết.**
Đây là departure lớn nhất và nó không phải lỗi của unit này. C2 (qua `kept()`), C11 và C12
đo `BASE..HEAD` và `BASE..cây làm việc`, tức đầu trên **chạy theo thời gian**. Chúng được
viết ngày `HEAD` *là* `08b863d`, nên vô tình thành tuyên bố về mọi commit tương lai của repo.
Đo trên `main` tại `31ea2fc`: **2 trên 14 claim đỏ** — C11 kể mười artifact `0009` viết, C12
kể sáu dòng lockfile mà lần bump version của `0009` làm dịch. Không cái nào là một phép đổi
tên mang theo thứ khác. `review.md` của `0009` đã ghi *"bảy proof cũ không chạy lại"*; đây
chính là thứ lần bỏ sót đó che.

Sửa: ghim đầu trên vào `TIP = 08b863d`, commit `0008` kết thúc. **Điều được tuyên bố không
đổi; điều được đo thôi di chuyển.** C1 cố ý **không** ghim — *"cái tên đã biến mất"* là
tuyên bố về hiện tại. Sau khi ghim: 14/14.

Và nói thẳng phần khó chịu: việc này làm C11 thôi nhìn thấy bước 1 của chính unit này, vốn
**có** viết lại tám artifact đã ký. Quyền làm việc đó đến từ spec R10 và C2 của `0010`, và
nó được ghi ở `.cos/RENAMES.md` cùng ở đây — không phải ở file bằng chứng của `0008`.

**4. Bước 9 và 10 nở ra, theo yêu cầu giữa chừng.** *"trong skills không mention bất kỳ item
khác nào"*. Ngoài hai skill plan liệt, gỡ con trỏ ở `write-review` (tới `CLAUDE.md` và tới
`.cos/0005.../spec.md` C5), `write-impl` (tới `write-plan/SKILL.md`), `write-pr` (tới
`0005`–`0008`). Câu nói giữ nguyên, con trỏ thì không. Giữ lại đường dẫn `cos.mjs` và mục
`## Next` — cái đầu là lệnh skill tự chạy, cái sau là vòng lặp. Va vào spec R4 ở chữ, không
ở ý: bảy skill không bị viết lại, chỉ bị gỡ con trỏ.

Yêu cầu đó cũng giải quyết hộ một chỗ trùng tôi vừa tạo ra: thủ tục sáu bước nằm ở **cả**
`CLAUDE.md` **và** `write-intent`, trong khi spec R5 đòi đúng một file. Skill không còn được
trỏ sang bản kia, nên bản ở `write-intent` còn một câu.

**Kèm theo: mệnh đề 4 của `## Proof` đo nhầm máy tôi.** `grep -rliE` đi theo hệ tệp nên bắt
`.claude/settings.local.json` — file **gitignore**, chứa `"Bash(git switch *)"` như một mục
cấp quyền. Ba file khớp thay vì một, hai trong đó không có trong repo. Đổi sang `git grep`.

## What was measured

Chuỗi `## Proof`, chạy nguyên vẹn ngày 2026-09-22: **exit 0**.

| Mệnh đề | Trước | Sau |
|---|---|---|
| `CLAUDE.md` + `harness.md` ≤ 200 dòng | 492 | **115** |
| `harness.md` không tồn tại | 295 dòng | xoá |
| không file nào trong `.claude/`, `README.md` trỏ tới nó | 4 file | **0** |
| đúng một file (đã theo dõi) nói cách cắt branch | **0** | **1** — `.claude/CLAUDE.md` |
| câu *"may not have one"* | còn | gỡ |
| `intent.md` thiếu `Type:` | **8** | **0** |
| `.claude/rules/coscc-app.md` | chưa có | 85 dòng |
| problem trên toàn bộ unit | 0, nhưng vì chưa kiểm gì | **0**, sau khi kiểm |
| `npm test` | 55 node + 256 python | **60** node + 256 python |
| `verify_0008.py` | **exit 1, 12/14** trên `main` | exit 0, **14/14** |
| `verify_0009.py` | exit 0, 13/13 | exit 0, 13/13 |

Số khác, mỗi số một lệnh:

- `.claude/CLAUDE.md` **115** dòng (ngưỡng plan đặt: ≤ 120).
- `.claude/rules/coscc-app.md` **85** dòng; frontmatter parse ra
  `{'paths': ['coscc/**', 'coscc/**/*', 'rxconfig.py', 'scripts/verify_*.py']}`.
- `grep -c "coscc/" .claude/CLAUDE.md` → **0**.
- Diff cả branch, trừ `.cos/`: **13 file**, `+287 −496`
  (`git diff --shortstat main...HEAD -- ':!.cos'`).
- `harness.md` còn được nhắc **67** lần trong bốn unit đã ký, **16** lần kèm số dòng. Không
  lần nào được sửa; ghi một mục ở `.cos/RENAMES.md`.
- Mười unit đều sinh ra một tên branch mà `check-branch` nhận.

**Kênh tới board còn sống, kiểm bằng mắt chứ không bằng lệnh:** `coscc/board.py:130` →
`coscc/state.py:254` → `coscc/screens.py:443`. Ba dòng đó vẫn đúng như spec tiêu chí 2 mô tả.
Một điều đọc được lúc kiểm và không ai hỏi: `state.py:252` trả `"Complete"` **trước** khi đọc
`problems`, nên một unit đã `finished` mang problem vẫn nằm ở lane Complete và chỉ hiện badge
đỏ. Không sửa ở đây — `coscc/` ngoài phạm vi unit này.

## What is still open

- **Rule file có nạp không thì unit này không biết.** `paths:` đúng theo tài liệu
  (`code.claude.com/docs/en/memory`, đọc 2026-09-22) và file parse được, nhưng không phép đo
  nào trong repo chứng minh Claude Code thực sự nạp nó khi chạm `coscc/`. `intent.md`
  constraint 4 cấm viết proof mới. Đây là Risk 3, còn nguyên: hỏng thì **im lặng**, và triệu
  chứng xuất hiện ở một phiên khác, sau khi unit này đóng.
- **295 dòng đi qua hai bước đọc của cùng phiên đã viết phần lớn chúng.** Risk 1. Hai bảng ở
  trên là bản ghi duy nhất; sau `cd1304d` thì `git show` là cách duy nhất lấy lại.
- **Tám type là phán đoán, không phép đo.** Risk 2. `check-branch` chỉ biết mười chuỗi, không
  biết unit nào "đáng lẽ" là gì.
- **R8 báo nhưng không chặn.** Risk 7, và unit này không làm nó nhẹ đi: `Type:` giờ có script
  kiểm, kiểm xong thì in ra. Ai bỏ qua mục Problems thì nó không mạnh hơn một câu văn xuôi —
  đúng thứ unit này vừa xoá 295 dòng với lập luận rằng văn xuôi không cưỡng chế được gì.
- **Năm proof còn lại chưa chạy lại.** `verify_0001`–`verify_0006` cần browser, port, hoặc
  tiêu quota. Departure 3 vừa cho thấy một proof có thể đỏ nhiều ngày mà không ai biết, nên
  câu này là một tuyên bố có trọng lượng chứ không phải lời rào.
- **`.claude/rules/` chưa ai copy đi đâu.** Câu *"copy `.claude/` là cả harness"* bây giờ
  mang theo một thư mục nữa; chưa lần nào được thử ở repo khác.
