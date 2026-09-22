# How to read paths in artifacts written before 0008

`0008_personal-name-blocks-publishing` đổi tên gói Python của repo. Các artifact viết trước
nó trỏ vào tên cũ, và **chúng không được sửa**. File này là bảng tra.

## The mapping

| Viết trong artifact | Nay là |
|---|---|
| `cos_baodo/<file>` | `coscc/<file>` |
| `cos_baodo/cos_baodo.py` | `coscc/coscc.py` |
| `cos_baodo.cos_baodo:app` | `coscc.coscc:app` |
| lệnh `cos-baodo` | lệnh `coscc` |
| lệnh `cos-build` | lệnh `coscc-build` |
| tên phân phối `cos-baodo` | `coscc` |

Đo ngày 2026-09-22: **272** trích dẫn đường dẫn dạng `cos_baodo/...` nằm trong `.cos/`, trong
đó **97** trích dẫn trên **20 file** có kèm số dòng. Con số này tăng mỗi lần một artifact mới
nhắc tới tên cũ, nên phải đo lại chứ không dùng lại.

## Two things the table cannot fix

**`cos_baodo/store.py:<N>` lệch cả số dòng.** `0008` bước 2 xoá đường import legacy khỏi
`Store` — 52 dòng. Nên với `store.py`, đổi `cos_baodo/` thành `coscc/` là **chưa đủ**: mọi
trích dẫn có số dòng lớn hơn 41 nay trỏ vào nội dung khác. Những chỗ trích
`STORE_FILENAME`, `_import_legacy`, `_load_legacy`, `_needs_import` hoặc `legacy_path` trỏ
vào thứ **không còn tồn tại**; đó là điều `0008 spec.md` R3 cố ý làm, không phải hỏng.

**Tên cũ vẫn nằm trong history.** Việc viết lại history ở cuối `0008` chỉ **bỏ hai đường
dẫn**, không đụng nội dung, nên `git log -p` vẫn đọc ra tên cũ. Outcome của unit đó đo trên
tree, không trên history.

## Every seven-character hash quoted before 0008 is dead

Sát lúc publish, `0008` viết lại toàn bộ history để gỡ hai thứ khỏi mọi commit:

- `docs/ai-native-sdlc-playbook.md` — 611 dòng văn bản của Anthropic giữ nguyên văn, không
  ghi nguồn, hot-link bốn ảnh từ CDN của họ. Nó nằm ở **commit đầu tiên**. **Đường dẫn đó nay
  không tồn tại**: repo giữ một bản đọc cục bộ ở `.raws/`, và `.raws/` nằm trong
  `.gitignore`. Bản gốc ở <https://claude.com/blog/the-ai-native-sdlc-playbook>.
- `.claude/settings.local.json.tmp.*` — cấu hình máy lọt vào commit do `git add -A`.

Cả 134 commit giữ nguyên thứ tự và thông điệp nhưng **đổi hash**. Nên **44 trích dẫn SHA**
nằm rải trong `.cos/` — ví dụ `0001 plan.md:5` trích `81295b9`, `0002 intent.md:4` trích
`c34adfc` — nay không phân giải được nữa.

Chúng **không được vá tại chỗ**, cùng lý do với phần trên: vá nghĩa là sửa artifact đã ký, và
`0008 spec.md` C5 đã đo rằng làm thế còn hỏng thêm các trích dẫn số dòng trỏ vào chính những
file ấy. Một hash không phân giải được là một bản ghi *thiếu*, còn một artifact bị sửa sau khi
ký là một bản ghi *sai*. Unit này chọn cái thứ nhất.

Bản history trước khi viết lại được giữ trong một git bundle ngoài repo, trên máy tác giả. Nó
không có ở đây và không ai ngoài đó tra được — đó là cái giá của việc gỡ nội dung của người
khác khỏi một repo công khai.

## Three citations to a path that no longer exists

Ba artifact đã đóng của `0005` trích dẫn `docs/ai-native-sdlc-playbook.md`, một trong số đó
kèm số dòng:

| Chỗ trích | Trích cái gì |
|---|---|
| `.cos/0005_hand-driven-invisible-loop/intent.md:33` | `docs/ai-native-sdlc-playbook.md:63` |
| `.cos/0005_hand-driven-invisible-loop/plan.md:418` | `docs/ai-native-sdlc-playbook.md:63` |
| `.cos/0005_hand-driven-invisible-loop/spec.md:216-217` | file đó, hai lần |

Đường dẫn ấy đã biến mất khỏi repo. Đoạn cả ba trỏ tới là đoạn nói về vòng lặp trong đó mỗi
artifact được chấp nhận sẽ khởi động giai đoạn kế tiếp; nó nằm trong bản gốc, ở phần bàn về
*the committed artifact*.

Bản thân `.claude/` **không** còn trỏ ra ngoài nó nữa: `harness.md` liên kết bằng URL, và
`write-ship` đã bỏ hẳn trích dẫn. Đó là điều kiện để copy `.claude/` sang repo khác mà không
mang theo một đường dẫn chết — chính `.claude/harness.md:168-170` đòi như vậy.

## Why the artifacts were not rewritten

Repo đã gặp đúng tình huống này một lần. `0002` đổi `app/` thành tên gói cũ, và
`.cos/0002_no-workspace-management/spec.md:245-250` kết luận:

> Sửa nghĩa là viết lại một artifact đã ký; không sửa nghĩa là harness có trích dẫn hỏng,
> trong khi chính nó đòi "cite only a file committed in this repository". Không lối nào
> sạch. Unit này chọn không sửa và ghi lại ở đây.

Kết quả là bảng tra ở `.cos/0001_no-session-management/plan.md:4-21`, đặt ở đầu chính file bị
ảnh hưởng, với lý do rằng viết lại thân file sẽ thành *"một bản ghi sai theo kiểu khác"*.

`0008` không đặt được bảng theo cách đó. Lúc `0002` làm, chỉ có **1** file bị ảnh hưởng.
Ở `0008` có **20**, và `0008 spec.md` C5 đo được rằng **5** trong số đó đang bị nơi khác trích
dẫn kèm số dòng — thêm một header vào chúng sẽ đẩy mọi dòng xuống và làm **19** trích dẫn
đang đúng thành sai. Tức là chữa 272 trích dẫn chết bằng cách tạo ra 19 trích dẫn chết kiểu
khác. Nên bảng nằm một chỗ, ở đây.

## Why this file is here and not in .claude/

Bảng tra buộc phải viết ra tên cũ. `.claude/harness.md` nằm **ngoài** `.cos/`, và outcome của
`0008` là `git grep -in <tên cũ> -- . ':!.cos'` bằng 0 — nên đặt bảng ở đó thì outcome không
bao giờ đạt. `.cos/` là vùng duy nhất vừa được loại khỏi phép đo vừa đúng về ngữ nghĩa.

File này nằm ở tầng `.cos/`, không trong unit dir nào, nên harness bỏ qua nó:
`.claude/scripts/cos.mjs:97-98` chỉ liệt kê thư mục, và kiểm tra file lạ ở `:83-84` chỉ chạy
bên trong một unit dir. `.claude/harness.md` trỏ tới đây.

## `.claude/harness.md`, deleted by 0010 (2026-09-22)

`0010` xoá `.claude/harness.md`. Thứ nó giữ mà không ai giữ hộ chuyển sang
`.claude/CLAUDE.md`; thứ nó giải thích về một quy tắc đã có `cos.mjs`, CI hoặc ruleset cưỡng
chế thì không chuyển đi đâu cả. Kiến thức riêng của app `coscc` sang
`.claude/rules/coscc-app.md`, nạp có điều kiện theo `paths:`.

**Trích dẫn tới file đã chết, đo ngày xoá** bằng `grep -ro` trên `.cos/`:

| Unit | Lần nhắc tên | Trong đó có số dòng |
|---|---|---|
| `0005_hand-driven-invisible-loop` | 19 | 6 |
| `0007_stale-claims-and-dead-code` | 2 | 0 |
| `0008_personal-name-blocks-publishing` | 27 | 5 |
| `0009_branch-and-release-conventions` | 19 | 5 |
| **Cộng, bốn unit đã ký** | **67** | **16** |
| `0010_…` (unit xoá nó) và file này | 40 | 8 |

Không cái nào được sửa, cùng lý do đã ghi ở `## Why the artifacts were not rewritten`: sửa
là viết lại artifact đã ký, và một header chèn thêm sẽ đẩy mọi số dòng khác xuống. `0010
intent.md` constraint 5 chốt điều đó. Đây là lần thứ hai repo trả giá này — lần đầu là 44
trích dẫn SHA chết ở `0008`.

**Tra ở đâu:** nội dung cuối cùng của file nằm ở `git show <commit trước 0010>:.claude/harness.md`.
Số dòng trong mọi trích dẫn trên đọc theo bản 295 dòng đó, không theo bản nào khác.

Hai lần nhắc `harness.md` còn lại trong chính file này (`## Why this file is here and not in
.claude/`) **cố ý không sửa**: chúng kể lý do lịch sử của một quyết định ở `0008`, và câu đó
đúng vào lúc nó được viết. Con trỏ tới bảng tra bây giờ ở `.claude/CLAUDE.md`.
