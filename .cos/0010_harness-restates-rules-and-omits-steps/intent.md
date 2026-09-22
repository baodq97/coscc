# Intent: The harness restates what scripts enforce and never says what to do
Author: Bao Do. Type: fix. Status: accepted.

## Problem

Người khởi xướng yêu cầu hai việc cùng lúc, sau khi đọc một lần rà soát harness:

> "đồng ý với mục fix này, ngoài ra tôi cần optimize harness.md và CLAUDE.md theo phong cách
> tương tự skills để nó là các hướng dẫn general, clean và compact đảm bảo minimal content
> nhưng maximize impact, những gì đã có thể sử dụng hard verify, hard rule rồi thì không cần
> mentions giải thích hướng dẫn gì nữa"

Hai việc, một nguyên nhân: **harness mô tả cơ chế, không mô tả thủ tục.** Nó giải thích dài
những thứ `cos.mjs`, CI và ruleset đã cưỡng chế, rồi im lặng ở đúng chỗ không ai cưỡng chế.

### Bảy chỗ thiếu, đo ngày 2026-09-22

| # | Chỗ | Đo |
|---|---|---|
| 1 | Không nơi nào nói khi nào cắt branch | `grep -riE "cut (a\|the) branch\|git switch\|gh pr create"` trên `.claude/` → **0** kết quả |
| 2 | `Type:` bắt buộc nhưng không gì kiểm | unit thiếu `Type:` → `problems: []`; `Type: nonsense` → `problems: []` |
| 3 | `unit-branch` cần `intent.md` đã có | `cos.mjs unit-branch 0010_…` → exit **2**, `No such work unit` |
| 4 | `write-pr` nói sai hiện trạng | `SKILL.md:21-22` viết *"this repository may not have one"*; `git remote -v` có `origin` từ `0008` |
| 5 | Hai danh sách lệnh thiếu | `harness.md:119-130` và `CLAUDE.md:6-16` kể **3** lệnh `cos.mjs`; bảng `run` có **7** |
| 6 | `## Work units` không nhắc `Type:` | `harness.md:70-82` |
| 7 | `plan.md: done` là terminal, chỉ cảnh báo ở `CLAUDE.md` | `harness.md:86` liệt kê `done` như status thường; `harness.md` mới là thứ được copy |

Chỗ 3 vừa được sống lại khi mở chính unit này: `new-path` in ra đường dẫn, nhưng `main` đã
khoá nên phải cắt branch trước khi commit, mà tên branch lại suy ra từ một file chưa tồn tại.
Lối ra có thật — viết `intent.md` rồi mới cắt — và không ai ghi nó xuống.

### Và hai file dài gấp đôi chín skill cộng lại

Đo cùng ngày:

| File | Dòng |
|---|---|
| `.claude/harness.md` | **295** |
| `.claude/CLAUDE.md` | **197** |
| **Cộng** | **492** |
| Chín `SKILL.md` cộng lại | 657 |
| — dài nhất trong số đó, `write-spec` | 112 |

Hai file mang **4738 từ**. `harness.md` có **64** dòng nhắc tới thứ đã có máy kiểm — `gate`,
`status`, ngữ pháp slug, các status hợp lệ, `check-*`, ruleset, squash, `permissions` — và
phần lớn số đó là giải thích chứ không phải quy tắc.

`CLAUDE.md` thì **134 trong 197 dòng** (`:18-151`) là kiến thức riêng của app `coscc`:
build fingerprint, hai root, WAL và `busy_timeout`, board, port nướng vào bundle, sáu proof
script. Chỉ **46** dòng cuối (`:152-197`) là quy tắc chung. Điều này mâu thuẫn với chính
`harness.md:283` — *"Copy `.claude/`. That is the whole harness"* — vì bản copy mang theo
134 dòng về một app mà repo đích không có.

### Vì sao hai việc này là một unit

Nén mà không lấp bảy chỗ kia thì được một tài liệu ngắn vẫn không đi theo được. Lấp mà không
nén thì thêm chữ vào một file đã dài gấp ba `write-spec`. Cả hai đều là cùng một phép sửa:
bỏ phần giải thích thứ có máy kiểm, viết vào phần thủ tục không ai kiểm.

## Proposed outcome

Trước **2026-10-06**, `wc -l .claude/harness.md .claude/CLAUDE.md` cộng lại **≤ 200 dòng**,
giảm từ 492.

Kết quả này **sai** nếu bất kỳ điều nào sau đây đúng tại ngày đó:

- Tổng vượt 200 dòng.
- Bất kỳ chỗ nào trong bảy chỗ trên còn mở — đo lại đúng bằng các lệnh ở cột "Đo", cộng
  `cos.mjs status` báo problem cho một unit thiếu `Type:` hoặc mang type ngoài tập mười.
- Hai file còn giải thích một quy tắc mà `cos.mjs`, CI hoặc ruleset đã cưỡng chế. "Giải
  thích" nghĩa là văn xuôi nói **vì sao** hoặc **làm thế nào** một quy tắc đã có máy kiểm
  hoạt động; nêu tên quy tắc và lệnh kiểm nó thì không phải.
- `CLAUDE.md` còn mô tả app `coscc`, hoặc trỏ tới nơi mô tả nó.
- Danh sách lệnh trong tài liệu không khớp bảng `run` của `cos.mjs`.

Hôm nay, 2026-09-22: **492** dòng, bảy chỗ đều mở, `CLAUDE.md` mang 134 dòng về app.

**200 là con số file này đặt**, không phải do người khởi xướng nêu. Nó chọn theo `write-spec`
(112 dòng) — skill dài nhất — cộng chỗ cho một `CLAUDE.md` chỉ còn quy tắc chung. Sửa được,
và sửa ở đây.

## Affected users and systems

- **Mọi phiên làm việc trong repo này.** `CLAUDE.md` được nạp làm project instruction ở mỗi
  phiên; `harness.md` là tài liệu tham chiếu mà mọi skill trỏ về.
- **Bất kỳ ai copy `.claude/`.** Sau unit này họ nhận một harness không kèm 134 dòng về một
  app họ không có.
- **`.claude/scripts/cos.mjs`** — chỗ 2 là thay đổi hành vi: `readUnit` phải đọc `Type:` và
  báo problem. Đây là file duy nhất định nghĩa vòng lặp, nên sửa nó là sửa oracle.
- **`.claude/skills/write-pr/SKILL.md`** — chỗ 4, và nó là câu đã đẩy `0005`–`0008` vào ghi
  `draft`.
- **`.claude/skills/write-intent/SKILL.md`** — chỗ 3 sống ở đây: thứ tự giữa `new-path`,
  viết `intent.md`, và cắt branch.
- **`docs/`** — hiện chỉ có `studio.md`. Kiến thức app của `CLAUDE.md` chuyển sang đây nếu
  còn giá trị; người khởi xướng chốt **không** trỏ từ `CLAUDE.md` sang.
- **Chín `SKILL.md`** — chúng là khuôn mẫu phong cách cho lần viết lại này, và chỗ 1 phải rơi
  vào đúng một trong số chúng chứ không rơi vào cả chín.

## Constraints

1. **Phong cách theo `SKILL.md`.** Người khởi xướng chốt. Nghĩa là: mở đầu ngắn, invariant
   đánh số, `## Done when`, và không có đoạn kể lịch sử.
2. **Không giải thích lại thứ đã có hard rule.** Người khởi xướng chốt, nguyên văn. Hệ quả
   phải nói thẳng: văn xuôi còn lại sẽ **toàn là** thứ không ai cưỡng chế — cảnh báo,
   đánh đổi, chỗ hở. Đó là chủ ý, và nó làm hai file đọc khó chịu hơn hiện tại.
3. **Kiến thức app ra khỏi `CLAUDE.md`, và `CLAUDE.md` không nhắc tới nó.** Người khởi xướng:
   *"tách docs nếu thật sự giá trị, nhưng cũng không cần mentions ở CLAUDE.md"*. Cùng hình
   dạng với `0008` departure 11 — một skill không được phụ thuộc `docs/`.
4. **Không thêm proof script.** Người khởi xướng chốt *"Không — sửa một lần"*. Điều này va
   vào `write-plan` invariant 4, vốn đòi `## Proof` là một lệnh quyết định pass/fail. Cách
   dung hoà là việc của plan; nó **không** được biến thành "verify manually".
5. **Không sửa artifact đã `accepted` của unit khác.** Như `0008` và `0009`.
6. **Mọi thay đổi đi qua branch và pull request.** `main` đã khoá từ `0009`; unit này không
   có ngoại lệ nào, kể cả `intent.md` này.
7. **`cos.mjs` là nơi duy nhất định nghĩa vòng lặp.** Chỗ 2 thêm một phép kiểm vào đó; nó
   không được tạo ra một bản sao thứ hai của tập type.

## Open questions

1. **Chỗ 2 chặn hay chỉ báo?** `readUnit` báo problem thì `cos.mjs status` in ra nhưng `gate`
   vẫn mở. Điều một câu trả lời sẽ đổi: nếu `Type:` sai làm `gate spec` đỏ thì tám unit cũ —
   không unit nào khai `Type:` — sẽ chặn ngay, và phải backfill hoặc miễn trừ chúng; nếu chỉ
   báo thì bảng `status` mang tám dòng problem vĩnh viễn.

2. **Chỗ 1 rơi vào skill nào?** Cắt branch xảy ra một lần cho cả unit, nhưng vòng lặp có tám
   stage và mỗi stage là một skill. Điều một câu trả lời sẽ đổi: đặt ở `write-intent` thì nó
   được đọc sớm nhất nhưng cách `impl` bảy bước; đặt ở cả chín thì nó là chín bản sao trôi
   độc lập, đúng thứ `harness.md:113-118` cấm với tiêu chí spec skip.

3. **134 dòng app có bao nhiêu "thật sự giá trị"?** Người khởi xướng đặt điều kiện nhưng
   không cắt hộ. Điều một câu trả lời sẽ đổi: giữ hết thì `docs/` nhận một file 134 dòng
   không ai đọc; cắt về các số đo có nguồn mà không lệnh nào giữ — WAL trước `busy_timeout`,
   bundle nướng port — thì còn khoảng một phần ba và phần còn lại mất.

4. **`harness.md` còn lý do tồn tại không?** Sau khi bỏ hết phần giải thích thứ có máy kiểm,
   phần còn lại của nó có thể vừa trong chín `SKILL.md` cộng `CLAUDE.md`. Điều một câu trả
   lời sẽ đổi: nếu gộp được thì outcome đạt bằng cách xoá một file, và `.claude/` còn hai
   tầng tài liệu thay vì ba.
