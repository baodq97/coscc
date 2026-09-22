# Plan: Backfill, then enforce, then collapse three documents into two tiers
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

Mười ba bước. Thứ tự bị ép bởi hai chỗ, và cả hai là lý do các bước không hoán vị được:

- **Backfill trước khi cưỡng chế.** Bước 1 đặt `Type:` vào tám `intent.md`; bước 2 mới dạy
  `cos.mjs` báo problem. Đảo lại thì có một khoảng repo mang tám badge đỏ trên board thật.
- **`CLAUDE.md` mới phải viết xong trước khi `harness.md` bị xoá.** Bước 7 viết, bước 8 xoá
  và sửa hai proof trong **cùng một commit** — xoá trước thì hai proof đã đóng đỏ và không có
  chỗ nào cho chúng trỏ tới.

Branch `fix/harness-restates-rules-and-omits-steps` đã cắt và `intent.md`, `spec.md` đã nằm
trên đó. `main` khoá từ `0009`, nên không bước nào ở đây có ngoại lệ.

## Files that change

### Mới

- `.claude/rules/coscc-app.md` **(new)** — path-scoped rule. `.claude/rules/` chưa tồn tại;
  bước 6 tạo thư mục.

### Xoá

- `.claude/harness.md` — 295 dòng. Spec R1, C1.

### Sửa

- `.claude/CLAUDE.md` (197 dòng) — viết lại, ≤ 120. Spec R2, R5.
- `.claude/scripts/cos.mjs` (517 dòng) — `readUnit` (`:61-89`) đọc `Type:`; `parseType`
  (`:246`) và `BRANCH_TYPES` (`:180`) đã có và được dùng lại, không tạo bản sao.
- `.claude/scripts/cos.test.mjs` (335 dòng, **55** test) — test cho R8.
- `.claude/skills/write-intent/SKILL.md` (101 dòng) — spec R6.
- `.claude/skills/write-pr/SKILL.md` (60 dòng) — spec R7; chuỗi cần bỏ ở `:21-22`.
- **Tám** `.cos/NNNN_*/intent.md` — `0001`…`0008`, mỗi file **đúng một dòng header**.
  Spec R10, C2.
- `scripts/verify_0008.py:325` và `scripts/verify_0009.py:304` — một hằng đường dẫn mỗi
  file. Spec R12.
- `README.md:17` và `:24` — hai dòng trỏ tới `harness.md`.
- `.cos/RENAMES.md` — một mục cho 16 trích dẫn số dòng và 71 lần nhắc tên sắp chết. Spec
  OQ5; `RENAMES.md` không nằm trong unit dir nào nên harness bỏ qua nó
  (`cos.mjs:98-99`).

### Không đổi

Bảy `SKILL.md` còn lại — spec R4. `coscc/` — unit này không chạm code app, chỉ chạm thứ
`cos.mjs` **báo cáo** cho nó. `.github/workflows/` — không đổi.

## Order of work

**1. Backfill `Type:` vào tám `intent.md`.** *(Sửa artifact đã ký — spec C2.)*
Một dòng header mỗi file, không chạm câu nào khác. Type đọc từ nội dung từng unit, không
đoán từ tiêu đề: `0001`–`0006` là `feat` (đều thêm năng lực mới), `0007` là `chore` (sửa
tuyên bố và nâng phiên bản), `0008` là `chore` (đổi tên và publish).

> **Departure, 2026-09-22.** `0004` được gán `fix`, không phải `feat`. Risk 2 dưới đây bảo
> đọc `## Problem` trước khi gõ, và lần đọc đó cho câu trả lời khác câu đoán ở trên: *"Two
> writers at once lose work without saying so"* là một khuyết tật có sẵn, và `plan.md` của
> nó (`## Files that change`) sửa `store.py`, `sessions.py`, `service.py` — không thêm màn
> hình hay lệnh nào cho người dùng. `0003` giữ `feat` sau cùng phép đọc: nó thêm
> `cos_baodo/build.py` mà `run.py` phụ thuộc vào, tức một năng lực mới, chứ không chỉ thêm
> một proof. Bảy type còn lại đúng như đã viết.
*Kiểm:* `grep -L "Type:" .cos/*/intent.md` trả **0 file**; `git diff --stat` cho **8 file,
+8 −8**; mỗi `unit-branch` in ra một tên mà `check-branch` nhận.

**2. `readUnit` báo problem khi `Type:` thiếu hoặc sai, cùng test.**
Dùng lại `parseType` và `BRANCH_TYPES`. Thông điệp problem nói **thiếu** khác **sai**, cùng
lối `missing()` (`cos.mjs:116`) đã phân biệt file vắng với file không có Status.
`checkGate` **không** đổi — spec R9.
*Kiểm:* unit dựng tạm thiếu `Type:` → có problem; `Type: nonsense` → có problem; tám unit
thật → `cos.mjs status` không có mục Problems; `npm run test:node` xanh với số test > 55.

**3. Đo lại `cos.mjs status --json` qua đường app đi.**
Spec tiêu chí 2: `problems` tới UI qua `coscc/board.py:130` → `coscc/state.py:254` →
`coscc/screens.py:443`. Bước 1 và 2 cộng lại phải để kênh đó sạch.
*Kiểm:* `cos.mjs status --json` cho **0** problem trên cả chín unit; `npm test` xanh cả hai
runtime.

**4. Liệt kê từng câu của `CLAUDE.md:18-151` và quyết giữ hay bỏ.**
Phép thử ở spec `## Design`: có script, CI hay ruleset nào làm câu này sai được không? Có →
bỏ. Không → giữ. Đây là bước **đọc**, không sửa file, và nó có sản phẩm: một danh sách viết
vào `impl.md` ở bước 11.
*Kiểm:* mỗi câu được giữ có một số đo mà chạy lệnh không ra — `WAL` trước `busy_timeout`,
bundle nướng port, `reflex run` bind `*:3000`.

**5. Làm cùng thế với `harness.md`.**
295 dòng, trong đó **64** dòng nhắc tới thứ đã có máy kiểm. Phần còn lại chia ba: trùng với
`CLAUDE.md` (bỏ), thuộc một stage (đã có trong `SKILL.md`, bỏ), hoặc là cảnh báo không ai
cưỡng chế (giữ, sang `CLAUDE.md`).
*Kiểm:* danh sách thứ hai, cũng vào `impl.md`.

**6. Viết `.claude/rules/coscc-app.md`.**
Frontmatter `paths:` khớp `coscc/**`, `rxconfig.py`, `scripts/verify_*.py`. Nội dung là danh
sách giữ lại của bước 4.
*Kiểm:* file parse được như YAML frontmatter + markdown; `npm test` xanh (nó không đọc file
này, nên đây chỉ là kiểm không làm hỏng gì).
**Không kiểm được ở đây:** rule có thực sự nạp hay không — spec C5, Risk 3.

**7. Viết lại `.claude/CLAUDE.md`.**
Phong cách `SKILL.md`: mở đầu ngắn, mục có tiêu đề, quy tắc đánh số, không đoạn kể lịch sử.
Mang: quy trình chung, thủ tục branch (spec R5), hành vi chung, và các cảnh báo giữ lại từ
bước 5 — kể cả bypass `plan.md: done` của bốn unit. **Không** mang kiến thức app và **không**
trỏ tới `rules/coscc-app.md` (`intent.md` constraint 3).
*Kiểm:* `wc -l` ≤ **120**; `grep -riE "git switch|gh pr create" .claude/` trả **đúng một**
file; `grep -c "coscc/" .claude/CLAUDE.md` trả **0** ngoài phần `## Commands`.

**8. Xoá `harness.md`, sửa hai proof, `README.md`, `RENAMES.md` — một commit.**
Không tách được: giây phút file biến mất, `verify_0008` C10 và `verify_0009` C6 đỏ.
*Kiểm:* `uv run python scripts/verify_0008.py` exit **0**; `verify_0009.py` exit **0**;
`grep -rl "harness\.md" .claude/ README.md` trả **rỗng**; `npm test` xanh.

**9. `write-intent`: thứ tự `new-path` → viết `intent.md` → cắt branch.** Spec R6.
*Kiểm:* skill nêu đủ ba bước và nói rõ `unit-branch` cần file đã tồn tại.

**10. `write-pr`: bỏ câu nói repo có thể không có remote.** Spec R7.
*Kiểm:* `grep -r "may not have one" .claude/` trả **rỗng**.

**11. Chạy `## Proof`.** Toàn bộ chuỗi, một lần.
*Kiểm:* exit **0**.

**12. `impl.md`, rồi mở PR.**
Hai danh sách của bước 4 và 5 vào `## What was built` — đó là chỗ duy nhất ghi lại **cái gì
bị bỏ và vì sao**, và sau khi `harness.md` biến mất thì không còn chỗ nào khác tra được.
*Kiểm:* `gate 0010_… pr` exit 0; `gh pr checks` cho hai job xanh.

**13. `pr.md`, merge, rồi `review.md` và `ship.md` qua branch thứ hai.**
Như `0009` bước 16: cổng đã bật nên hai artifact cuối cũng đi qua nó.
*Kiểm:* `cos.mjs status` cho `0010` đủ tám cột; `plan.md` này sang `Status: done`.

### Chọn không làm

- **Không viết `verify_0010.py`.** `intent.md` constraint 4. `## Proof` dưới đây dựng từ
  lệnh đã có — xem spec C6. Cái giá: sau unit này không gì ngăn `CLAUDE.md` phình lại, đúng
  như finding 5 hôm nay là tài liệu trôi khỏi `cos.mjs`. Người khởi xướng đã cân và chọn.
- **Không sửa 16 trích dẫn số dòng trong bốn unit.** `intent.md` constraint 5; cùng cách
  `0008` xử 44 trích dẫn SHA chết — ghi một lần ở `RENAMES.md`.
- **Không đụng `checkGate`.** Spec R9.
- **Không viết lại bảy `SKILL.md` còn lại.** Spec R4; chúng là khuôn mẫu, không phải đối
  tượng.
- **Không gỡ bypass `plan.md: done`.** Spec `## Out of scope`. `CLAUDE.md` mới vẫn phải nói
  ra nó.
- **Không thêm `PreToolUse` hook.** Spec `## Out of scope`.

## Risks

**1. Xoá `harness.md` rồi mới phát hiện nó giữ thứ không ai giữ hộ.** 295 dòng đi qua hai
bước đọc (4, 5) do cùng một phiên vừa viết phần lớn chúng, và một phiên bỏ sót chính câu nó
viết là chuyện đã xảy ra năm lần trong `0009`. Sau bước 8 thì `git show` là cách duy nhất lấy
lại.
*Dấu hiệu:* không có. Đó là lý do nó đứng đầu danh sách, và là lý do bước 12 bắt ghi danh
sách **bỏ cái gì** chứ không chỉ **giữ cái gì**.

**2. Backfill tám header sai type.** Không lệnh nào biết một unit "đáng lẽ" là `feat` hay
`chore`; `check-branch` chỉ biết mười chuỗi. Một type sai đi vào artifact đã ký và sẽ không
ai đọc lại.
*Dấu hiệu:* không có lệnh nào; chỉ có việc đọc `## Problem` của từng unit trước khi gõ.

**3. `.claude/rules/` không nạp như mong đợi.** Spec C5. Cơ chế này chưa từng dùng ở repo
này, phụ thuộc phiên bản Claude Code, và nếu hỏng thì 134 dòng kiến thức app **im lặng biến
mất** khỏi mọi phiên thay vì nạp có điều kiện. Không proof nào trong unit này bắt được, vì
`intent.md` constraint 4 cấm viết một cái.
*Dấu hiệu:* một phiên sau này sửa `coscc/` và không biết bundle nướng cứng port — tức triệu
chứng xuất hiện **sau** unit này đóng, ở một phiên khác.

**4. `CLAUDE.md` ≤ 120 dòng mua được bằng cách bỏ đúng thứ đang giữ hành vi.** Spec C4. Ngưỡng
là một con số; thứ nó cắt thì không. Rủi ro cụ thể: bỏ câu giải thích **vì sao** `gate` tồn
tại làm một phiên dễ lý luận vòng qua nó hơn.
*Dấu hiệu:* một phiên sau này đi qua một gate đỏ và giải thích được lý do. Không đo được ở
đây.

**5. R8 đổi thứ app hiển thị cho workspace của người khác.** `cos.mjs` là template và app trỏ
nó vào repo clone (`coscc/board.py:90`). Một repo không dùng quy ước `Type:` sẽ hiện badge đỏ
trên mọi unit.
*Dấu hiệu:* `cos.mjs --root <workspace khác> status` in ra mục Problems cho mọi unit. Đây là
hành vi **đúng** — quy ước thuộc harness và harness đi theo bản copy — nhưng nó phải được
nói ra ở bước 12.

**6. Hai proof được sửa để giữ màu xanh.** Bước 8 đổi đường dẫn trong `verify_0008` và
`verify_0009`. Claim không đổi, nhưng hình dạng của việc này — sửa bằng chứng của một unit đã
đóng cho tới khi nó xanh lại — giống hệt hình dạng của việc nới bằng chứng, và chỉ khác nhau
ở chỗ tôi nói mình đổi gì.
*Dấu hiệu:* diff của hai file vượt quá một hằng đường dẫn mỗi file.

**7. Cái tôi muốn không phải viết ra.** Unit này để `cos.mjs` **báo** một thứ mà
`cos.mjs` **không chặn** (spec R9), ngay sau khi vừa kết luận ở `0009` rằng "mọi invariant
đều advisory trừ thứ có script kiểm". `Type:` bây giờ có script kiểm — nhưng kiểm xong thì
chỉ in ra. Nếu ai đó bỏ qua mục Problems thì R8 không mạnh hơn một câu trong tài liệu, và
unit này vừa xoá 295 dòng tài liệu với lập luận rằng văn xuôi không cưỡng chế được gì.
*Dấu hiệu:* một unit mới ra đời không có `Type:`, `status` báo, và không ai sửa.

## Proof

Một chuỗi lệnh, chạy từ gốc repo. **Đạt là exit `0`.** Không có script mới —
`intent.md` constraint 4, và spec C6 nói rõ đây là cách dung hoà với `write-plan`
invariant 4.

```sh
test "$(cat .claude/CLAUDE.md .claude/harness.md 2>/dev/null | wc -l)" -le 200 \
&& test ! -e .claude/harness.md \
&& test -z "$(grep -rl 'harness\.md' .claude/ README.md)" \
&& test "$(grep -rliE 'git switch|gh pr create' .claude/ | wc -l)" -eq 1 \
&& ! grep -rq 'may not have one' .claude/ \
&& test "$(grep -L 'Type:' .cos/*/intent.md | wc -l)" -eq 0 \
&& test -f .claude/rules/coscc-app.md \
&& node .claude/scripts/cos.mjs status --json \
   | python3 -c 'import json,sys; u=json.load(sys.stdin)["units"]; sys.exit(sum(len(x["problems"]) for x in u))' \
&& npm test \
&& uv run python scripts/verify_0008.py \
&& uv run python scripts/verify_0009.py
```

Mười một mệnh đề, và mỗi mệnh đề là một requirement:

| Mệnh đề | Requirement | Hôm nay |
|---|---|---|
| tổng ≤ 200 dòng | R13 — outcome của `intent.md` | 492 |
| `harness.md` không tồn tại | R1 | tồn tại, 295 dòng |
| không gì trong `.claude/` hay `README.md` trỏ tới nó | R1, R11 | 4 file trỏ |
| đúng **một** file nói cách cắt branch | R5 — `intent.md` chỗ 1 | **0** file |
| không còn câu "may not have one" | R7 — chỗ 4 | còn |
| **0** `intent.md` thiếu `Type:` | R10 — chỗ 2 | **8** thiếu |
| rule file tồn tại | R3 | chưa |
| **0** problem trên cả chín unit | R8 + R10 cộng lại | 0, nhưng vì chưa kiểm gì |
| `npm test` | R8 có test; nền của repo | 55 + 256 xanh |
| `verify_0008.py` exit 0 | R12 — claim không đổi | xanh |
| `verify_0009.py` exit 0 | R12 — claim không đổi | xanh |

**Ba chỗ chuỗi này không với tới, và không chỗ nào giả vờ ngược lại.**

- **Chỗ 5, 6, 7 của `intent.md`** — danh sách lệnh khớp `cos.mjs`, `Type:` được nhắc ở tài
  liệu, `plan.md: done` là terminal được cảnh báo. Cả ba là "văn bản có nói điều đó không",
  và một `grep` cho chuỗi cố định chỉ chứng minh chuỗi có mặt. Chúng được đọc bằng mắt ở
  bước 7 và ghi vào `impl.md`.
- **"Không giải thích lại thứ đã có hard rule"** (`intent.md` constraint 2) — không
  `grep` nào phân biệt được một câu nêu tên quy tắc với một câu giải thích nó. Ngưỡng 120
  dòng là **đại lượng thay thế**, không phải phép đo.
- **Rule có nạp không** — Risk 3.
