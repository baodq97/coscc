# Spec: One loaded document, path-scoped rules, and the procedure written down
Intent: intent.md. Author: Bao Do. Status: accepted.

Skip assessment, ngày 2026-09-22:

| # | Tiêu chí | Verdict |
|---|---|---|
| 1 | ≤2 file đã tồn tại | **Fail** — `harness.md`, `CLAUDE.md`, `cos.mjs`, `cos.test.mjs`, hai `SKILL.md`, cộng 8 `intent.md` và 2 proof |
| 2 | Không đổi public interface / stored data | **Fail** — `readUnit` thêm một loại problem, và `problems` chảy tới UI: `coscc/board.py:130` → `coscc/state.py:254` → `coscc/screens.py:443` |
| 3 | Không thêm dependency | Pass |
| 4 | Không có hành vi ngoài `intent.md` | Pass |
| 5 | Không chạm bề mặt bảo mật | Pass — `.claude/rules/` là cơ chế nạp ngữ cảnh, không phải quyền |

Tiêu chí 2 là cái ép mạnh nhất: một problem mới không dừng ở terminal, nó thành badge đỏ
trên board của app.

## Requirements

Mọi số đo ngày 2026-09-22.

**R1 — `.claude/harness.md` bị xoá.** 295 dòng. Đây là phép dedup lớn nhất và là quyết định
nặng nhất của spec này — xem C1. Nội dung không lặp lại ở đâu khác được chia về ba chỗ theo
R2, R3, R4. Kiểm: file không còn tồn tại; `grep -rl "harness\.md"` trong `.claude/` trả 0.

**R2 — `.claude/CLAUDE.md` là tài liệu duy nhất nạp ở mọi phiên, ≤ 120 dòng.** Từ 197.
Ngưỡng này chặt hơn `intent.md` `## Proposed outcome` (200 cho **cả hai** file) vì sau R1
chỉ còn một file để đếm. Tài liệu chính thức của Claude Code đặt ngưỡng "under 200 lines per
CLAUDE.md"; 120 là con số spec này chọn, không phải con số họ nêu.

Nó mang **đúng** những gì global: quy trình chung, hành vi chung, và các cảnh báo không ai
cưỡng chế. Nó **không** mang: kiến thức app, giải thích thứ `cos.mjs`/CI/ruleset đã kiểm, và
bất kỳ trỏ dẫn nào tới nơi chứa kiến thức app (`intent.md` constraint 3).

**R3 — Kiến thức app thành một path-scoped rule.** `.claude/rules/coscc-app.md`, frontmatter
`paths:` khớp `coscc/**`, `rxconfig.py`, `scripts/verify_*.py`. Nó nạp **chỉ khi** phiên chạm
file khớp, nên nó không tốn ngữ cảnh của một phiên chỉ viết artifact. Đây là cơ chế người
khởi xướng nêu — *"chỉ apply special thì có thể sử dụng rule"*.

134 dòng hiện tại (`CLAUDE.md:18-151`) **không** chuyển nguyên. Giữ lại thứ không lệnh nào
giữ hộ; bỏ thứ một lệnh đã chứng minh. Ranh giới: một câu tồn tại được vì có số đo có nguồn
mà chạy lệnh không ra — `PRAGMA journal_mode=WAL` trước `busy_timeout` hỏng một lần trong
mười, bundle nướng cứng port, `reflex run` bind `*:3000`. Một câu mô tả thứ `npm test`,
`coscc-build` hay một proof script đã kiểm thì bỏ.

**R4 — Quy tắc từng stage ở trong `SKILL.md` của stage đó, không ở đâu khác.** Chín file đó
đã là khuôn mẫu phong cách (`intent.md` constraint 1) và chúng không đổi, trừ hai chỗ ở R6
và R7.

**R5 — Thủ tục branch được viết ra, đúng một chỗ.** `intent.md` chỗ 1: hiện 0 kết quả cho
`cut a branch|git switch|gh pr create` trong cả `.claude/`. Nó thuộc `CLAUDE.md` chứ không
thuộc một `SKILL.md`: cắt branch xảy ra **một lần cho cả unit**, còn skill là per-stage, nên
đặt vào một skill là đặt sai tầng và đặt vào chín skill là chín bản sao trôi độc lập —
đúng thứ `harness.md:113-118` cấm với tiêu chí spec skip. Trả lời `intent.md` OQ2.

Thủ tục phải nêu đủ: cắt branch **trước** commit đầu tiên, lấy tên từ đâu, và rằng
`intent.md` viết xong mới có tên để lấy (chỗ 3). Kiểm: `grep -riE "git switch|gh pr create"`
trên `.claude/` trả về **đúng một** file.

**R6 — `write-intent` nêu thứ tự `new-path` → viết `intent.md` → cắt branch.**
`intent.md` chỗ 3, đo được: `cos.mjs unit-branch 0010_…` trả exit 2 `No such work unit` khi
file chưa có. Vòng lặp gà-trứng có lối ra và chưa ai ghi. Kiểm: skill nêu thứ tự đó.

**R7 — `write-pr` bỏ câu nói repo có thể không có remote.** `SKILL.md:21-22` viết *"this
repository may not have one"*; `git remote -v` có `origin` từ `0008`. Đây là câu đã đẩy
`0005`–`0008` vào ghi `draft`. Kiểm: chuỗi `may not have one` không còn trong `.claude/`.

**R8 — `readUnit` báo problem khi `intent.md` thiếu `Type:` hoặc mang type ngoài tập mười.**
`intent.md` chỗ 2, đo: cả hai trường hợp hiện trả `problems: []`. Tập type đọc từ
`BRANCH_TYPES` đã có, **không** tạo bản sao thứ hai (`intent.md` constraint 7). Có test
trong `cos.test.mjs`.

**R9 — Báo, không chặn.** Problem xuất hiện trong `status`; `checkGate` không đổi. Trả lời
`intent.md` OQ1. Lý do là tiêu chí 2 của bảng skip: `gate` mà đỏ vì thiếu `Type:` sẽ khoá
tám unit đã đóng, còn problem thì chỉ hiện ra. Nhưng "chỉ hiện ra" vẫn tới UI — xem R10.

**R10 — Tám `intent.md` cũ nhận `Type:` trên dòng header.** `grep -L "Type:" .cos/*/intent.md`
trả **8**. Không backfill thì R8 sinh 8 problem vĩnh viễn, và chúng không dừng ở terminal:
`coscc/board.py:130` đưa `problems` sang `coscc/state.py:254`, nơi nó quyết lane, rồi
`coscc/screens.py:443` vẽ badge đỏ `problem` trên thẻ. Tám badge đỏ đứng mãi sẽ dạy người ta
bỏ qua mục problem — hỏng đúng kênh mà R8 vừa dựng.

Sửa **đúng một dòng header** mỗi file, không chạm câu nào khác. Việc này ngược
`intent.md` constraint 5 — xem C2.

**R11 — Không nội dung nào nằm ở hai chỗ.** Đo hiện tại: 2 cặp trùng ý giữa `harness.md` và
`CLAUDE.md` (Jaccard > 0.45) — `cos-status` làm gì, và quy tắc ngôn ngữ trong `.cos/`. Sau
unit này, mỗi sự thật có đúng một nơi giữ, và nơi khác trỏ tới bằng tên chứ không chép lại.

**R12 — Hai proof đã đóng được sửa đường dẫn, không sửa claim.** `scripts/verify_0008.py:325`
và `scripts/verify_0009.py:304` đều `read(".claude/harness.md")`. R1 làm cả hai đỏ. Sửa: đổi
hằng đường dẫn sang nơi nội dung ấy chuyển tới. **Claim không đổi** — `0008` vẫn hỏi "bảng
tra có được trỏ tới, và con trỏ không viết ra tên cũ", `0009` vẫn hỏi "quy ước có đủ mười
type, hai dạng tag, và câu ruleset không đi theo bản copy". Kiểm: cả hai proof exit 0.

**R13 — Phép đo nghiệm thu.** `wc -l .claude/harness.md .claude/CLAUDE.md` cộng lại **≤ 200**
(sau R1: `harness.md` không tồn tại, nên là `CLAUDE.md` một mình, ≤ 120 theo R2). Hôm nay:
**492**. Đây là outcome của `intent.md`, nguyên văn.

## Design

### Ba tầng, phân theo "nạp lúc nào" chứ không theo chủ đề

| Tầng | File | Nạp khi | Chứa |
|---|---|---|---|
| Global | `.claude/CLAUDE.md` | mọi phiên | quy trình chung, hành vi chung, cảnh báo không ai cưỡng chế |
| Scoped | `.claude/rules/coscc-app.md` | khi chạm `coscc/**` | tri thức về app này |
| On demand | `.claude/skills/*/SKILL.md` | khi gọi skill | quy tắc của đúng một stage |

Đây là trục người khởi xướng nêu: *"không phải global, liên quan tới quy trình chung,
behaviours chung, chỉ apply special thì có thể sử dụng rule"*. Nó cũng là trục mà tài liệu
Claude Code khuyên: *"If an entry is a multi-step procedure or only matters for one part of
the codebase, move it to a skill or a path-scoped rule instead."*

`harness.md` không có tầng nào. Nó không được nạp tự động, không có phạm vi, và không gắn
với một stage — nên nó là tài liệu người ta phải **nhớ** mà đọc, và hai proof phải **trỏ
tới**. Đó là lý do R1 xoá nó chứ không phải vì nó dài.

### Quy tắc cắt: cái gì ở lại văn xuôi

Sau `intent.md` constraint 2, thử một câu bằng một câu hỏi: **có script, CI hay ruleset nào
làm câu này sai được không?**

- Có → nêu tên quy tắc và lệnh kiểm nó. Không giải thích vì sao, không hướng dẫn cách.
- Không → giữ nguyên văn xuôi, vì đó là thứ duy nhất đang giữ nó.

Hệ quả đã nói ở `intent.md` constraint 2 và nó khó chịu: `CLAUDE.md` còn lại sẽ **toàn là**
cảnh báo, đánh đổi và chỗ hở — không có chỗ nào đọc thấy dễ chịu.

### Ranh giới `cos.mjs` không được vượt

R8 thêm một phép đọc vào `readUnit`, nơi app trỏ vào `.cos/` của repo khác qua `--root`
(`coscc/board.py:90`). Phép đọc ấy là đọc file, cùng loại việc `readUnit` vẫn làm, nên nó
không vượt ranh giới `0009` R6 đã kẻ. Nhưng nó **đổi thứ app hiển thị** cho một workspace
người khác: một repo clone về mà artifact không khai `Type:` sẽ hiện badge đỏ. Đó là đúng
hành vi — quy ước này thuộc harness và harness đi theo bản copy — nhưng nó phải được nói ra.

## Out of scope

- **Hook chặn.** `harness.md:157-158` hiện nêu một `PreToolUse` hook như việc cố ý không
  xây; unit này không xây nó. Tài liệu Claude Code cũng nói rõ CLAUDE.md và rule là ngữ cảnh
  chứ không phải cấu hình cưỡng chế.
- **Thêm status `skipped` cho stage `pr`.** Vẫn là bức tường đã bypass ở `0005`–`0008`; vẫn
  ngoài phạm vi, như `0009` đã để.
- **Gỡ bypass `plan.md: done` của bốn unit.** Chúng giữ nguyên; `CLAUDE.md` mới vẫn phải nói
  ra nó (chỗ 7 của `intent.md`).
- **Viết lại chín `SKILL.md`.** Chúng đã đúng phong cách; chỉ hai file đổi theo R6, R7.
- **Proof script mới.** `intent.md` constraint 4.
- **Đổi `README.md`.** Nó trỏ tới `harness.md` và sẽ cần sửa một dòng, nhưng nội dung của nó
  không thuộc unit này.

## Concerns

**C1 — Xoá `harness.md` là quyết định của spec này, không phải của người khởi xướng.** Họ
nói *"optimize harness.md và CLAUDE.md"* và *"nhớ dedup"*. Xoá một trong hai là phép dedup
mạnh nhất, và lập luận cho nó là: hai tài liệu cùng mô tả một quy trình thì trùng lặp không
phải rủi ro mà là kết quả — đã đo được 2 cặp hôm nay, và cả hai file mới chỉ sống ba tuần.
Giá phải trả, đo được: hai proof đã đóng phải sửa đường dẫn (R12), `README.md` hai dòng
(`:17`, `:24`), và **16** trích dẫn kèm số dòng chết hẳn cộng **71** lần nhắc tên, trải trên
**bốn** unit — `0005`, `0007`, `0008`, `0009` — và `.cos/RENAMES.md`.

> **Sửa cùng ngày, ngay sau khi người khởi xướng chọn.** Bản đầu của đoạn này viết *"một cái
> tên mà bốn tài liệu trong `.cos/` trích dẫn sẽ chết"*. Đếm đúng: **bốn unit**, không phải
> bốn trích dẫn — 16 trích dẫn có số dòng và 71 lần nhắc tên. Con số cũ nhỏ hơn gần hai mươi
> lần và nó được nêu ra **trong lúc hỏi** người khởi xướng, tức nó đã bóp méo ô chi phí của
> chính lựa chọn họ cân. Cùng loại với 44 trích dẫn SHA chết mà `0008` đã chấp nhận và ghi
> một lần ở `.cos/RENAMES.md`, nên hướng không đổi — nhưng lượng thì sai, và người khởi xướng
> được nói lại con số đúng trước khi plan bắt đầu.

**Người khởi xướng quyết** —
nếu muốn giữ `harness.md`, R1 đổi thành "slim còn ≤ 80 dòng" và R2 nới lên, tổng vẫn ≤ 200.

**C2 — R10 sửa artifact đã `accepted` của tám unit khác, trái `intent.md` constraint 5.**
Đây là mâu thuẫn thật giữa hai ràng buộc, không phải một chỗ mơ hồ. Một bên: tiền lệ
`0008`/`0009` là không chạm artifact đã ký. Bên kia: R8 mà không có R10 thì đặt tám badge đỏ
vĩnh viễn lên board và làm hỏng kênh problem. `0009` R13 đã quyết **không** backfill, với lý
do *"chúng không có branch nào để đối chiếu"* — lý do đó nói về **giá trị**, và cái hại thì
lúc ấy chưa biết. **Người khởi xướng quyết.** Nếu giữ constraint 5 thì R8 phải đổi: chỉ báo
khi `Type:` **có mặt nhưng sai**, và "thiếu `Type:`" quay lại không ai kiểm.

**C3 — Một phương án thứ ba cho C2 đã bị loại, và lý do đáng ghi.** Có thể cho `readUnit`
miễn trừ unit số < 0009. Loại, vì `cos.mjs` là **template**: một bản copy sang repo khác sẽ
mang theo một mốc số không có nghĩa gì ở đó. Một hằng số ngày tháng trong oracle chỉ đúng ở
đúng một repo.

**C4 — Sau khi nén, `CLAUDE.md` mất phần giải thích, và giải thích là thứ giúp không đi vòng
quy tắc.** `intent.md` constraint 2 chốt bỏ chúng. Rủi ro cụ thể: một phiên đọc "chạy
`cos.mjs gate` trước mỗi stage, dừng khi khác 0" mà không đọc *vì sao*, sẽ dễ lý luận vòng
qua nó hơn — đúng thứ `.claude/CLAUDE.md` `## Invariants` hiện đang cấm bằng một câu giải
thích. Đánh đổi này là chủ ý và không đo được bằng gì trong unit này.

**C5 — `.claude/rules/` là cơ chế chưa từng dùng ở repo này.** Nó phụ thuộc phiên bản Claude
Code: tài liệu ghi rule path-scoped trong `.claude/rules/` lồng nhau từng nạp cả khi
`project` bị loại khỏi `--setting-sources`, sửa ở v2.1.211. Nếu cơ chế không hoạt động như
mong đợi thì 134 dòng kiến thức app im lặng biến mất khỏi mọi phiên thay vì nạp có điều
kiện — **hỏng lặng**, và không proof nào trong unit này bắt được, vì `intent.md`
constraint 4 cấm viết một cái.

**C6 — `## Proof` của plan không được là "verify manually".** `intent.md` constraint 4 cấm
proof script mới; `write-plan` invariant 4 đòi một lệnh quyết định pass/fail. Hai thứ này
dung hoà được bằng một chuỗi lệnh có sẵn — `wc -l`, `grep`, `cos.mjs status --json`,
`npm test`, hai proof cũ — với kết quả kỳ vọng viết rõ. Nếu plan không dựng được chuỗi đó
thì mâu thuẫn là thật và constraint 4 phải nới. **Người khởi xướng quyết** nếu tới đó.

## Open questions

1. **`intent.md` OQ1 — đã trả lời ở R9:** báo, không chặn. Điều một câu trả lời khác sẽ đổi:
   nếu `gate` đỏ vì `Type:` thì tám unit cũ khoá ngay kể cả sau R10, vì `gate` đọc status chứ
   không đọc problem — và lúc đó R10 thành bắt buộc chứ không còn là lựa chọn.

2. **`intent.md` OQ2 — đã trả lời ở R5:** `CLAUDE.md`, một chỗ.

3. **`intent.md` OQ3 — trả lời một nửa.** R3 đặt ranh giới ("số đo có nguồn mà chạy lệnh
   không ra") nhưng không nói còn lại bao nhiêu dòng. Điều một câu trả lời sẽ đổi: nếu người
   khởi xướng muốn một ngưỡng cho rule file thì nó thành một requirement đếm được; hiện nó
   là một phép thử định tính và plan sẽ phải liệt kê từng câu giữ lại.

4. **`intent.md` OQ4 — đã trả lời ở R1:** xoá. Xem C1; đây là câu người khởi xướng nên đọc
   kỹ nhất trong file này.

5. **16 trích dẫn kèm số dòng và 71 lần nhắc tên `harness.md` trong `.cos/` sẽ chết**, trải
   trên bốn unit. Cùng loại với 44 trích dẫn SHA chết của `0008`, đã ghi một lần ở
   `.cos/RENAMES.md`. Điều một câu trả lời sẽ đổi: ghi thêm một mục vào `RENAMES.md` thì có
   một chỗ tra; không ghi thì 87 chỗ im lặng trỏ vào hư không.
