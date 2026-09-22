# Spec: Rename the package to coscc and publish it public
Intent: intent.md. Author: Bao Do. Status: accepted.

> **Sửa ngày 2026-09-22, sau khi bản đầu đã accepted và commit (`5226be5`).** Hai concern
> được giao cho người khởi xướng đã có câu trả lời, và cả hai được ghi tại chỗ thay vì đẩy
> sang `plan.md`: **C2** — dùng `github.com/baodq97/coscc`, chấp nhận handle cá nhân trong
> URL; **C5** — ghi chú tra cứu đặt một chỗ, trong `.claude/harness.md`, sau một phép đo
> mới ghi ở chính C5. R11 đổi theo. Bản đầu đọc được ở `5226be5`. Trước khi thêm khối này,
> đã kiểm: **0** chỗ trong repo trích dẫn file này kèm số dòng, nên việc đẩy số dòng xuống
> không làm hỏng gì — đúng cái bẫy mà C5 vừa đo được ở 19 artifact khác.

Skip assessment, ngày 2026-09-22, cả năm tiêu chí:

| # | Tiêu chí | Verdict |
|---|---|---|
| 1 | ≤2 file đã tồn tại | **Fail** — 45 file ngoài `.cos/` chứa cái tên |
| 2 | Không đổi public interface / stored data | **Fail** — console script, tên package, tên phân phối, và một đường đọc file trên đĩa |
| 3 | Không thêm dependency | Pass |
| 4 | Không có hành vi ngoài `intent.md` | Pass |
| 5 | Không chạm auth/PII/bề mặt bảo mật | **Fail** — public hoá 121 commit, một email, 9 dòng `/home/bd` |

Ba tiêu chí fail nên spec phải viết. Tiêu chí 1 là cái ép mạnh nhất.

## Requirements

Mọi số đo ngày 2026-09-22, trên working tree sau khi `intent.md` đã commit ở `fed0638` và
trước khi file này được commit. Hai loại số phải phân biệt, và C9 giải thích tại sao: số đo
**ngoài `.cos/`** là ổn định và mọi requirement dùng loại đó; số đo **trên cả repo** tự trôi
mỗi lần unit này viết thêm một artifact, nên không requirement nào pin loại đó.

**R1 — Package đổi tên.** `cos_baodo/` → `coscc/`. Ba chỗ phải khớp nhau hoặc app không
chạy: tên thư mục, `app_name` trong `rxconfig.py`, và `module-name` trong
`pyproject.toml` (`[tool.uv.build-backend]`). Kiểm: `import coscc` thành công, và
`git grep -c cos_baodo -- . ':!.cos'` trả 0.

**R2 — Tên phân phối và tên lệnh đổi.** `pyproject.toml` `name = "coscc"`;
`[project.scripts]` thành `coscc` và `coscc-build`. `package.json` `"name": "coscc"`, và
`test:python` đổi `-s cos_baodo` thành `-s coscc`. Kiểm: `uv run coscc-build` phân giải
được, `npm test` chạy được cả hai runtime.

**R3 — Đường import legacy của `Store` bị xóa hẳn.** Chín định danh phải mất, đo trên
`cos_baodo/store.py`: `STORE_FILENAME` (`:45`), mục của nó trong `__all__` (`:63`),
`self.legacy_path` (`:118`), `self._imported` (`:120`), lời gọi `self._import_legacy(conn)`
nằm trong `transaction()` (`:133`), `_migration_key` (`:137`), `_needs_import` (`:141`),
`_import_legacy` (`:143`), `_load_legacy` (`:155`). Cùng với bốn chỗ trong
`cos_baodo/store_test.py` (`:188`, `:204`, `:229`, `:368`) và các test bọc chúng — một trong
số đó là `test_bad_names_in_the_file_are_not_imported` (`:222`). Kiểm:
`git grep -in "cos-baodo.json" -- . ':!.cos'` trả 0, và `npm test` xanh.

**R4 — `Data.import_once` ở lại.** Nó ở `cos_baodo/data.py:325` và **`Journal` gọi nó**
(`cos_baodo/journal.py:151`). R3 không được kéo nó theo. Kiểm: `Journal` vẫn import được
`.cos-journal.jsonl` và test của nó vẫn xanh.

**R5 — Đường import legacy của `Journal` không đổi.** `.cos-journal.jsonl` không chứa tên
riêng, nên `intent.md` không cho phép bỏ nó. Kiểm: `git grep -c "cos-journal" cos_baodo/journal.py`
giữ nguyên giá trị hiện tại.

**R6 — Hai câu mô tả import legacy phải đúng lại.** `.claude/CLAUDE.md:68` và
`docs/studio.md:35` hiện đều nói một `.cos-baodo.json` **hoặc** `.cos-journal.jsonl` được
import một lần và không bao giờ bị xóa. Sau R3 chỉ còn đúng với cái thứ hai. Cả hai dòng
phải viết lại để nói đúng điều đó. Kiểm: không dòng nào còn nhắc `.cos-baodo.json`.

**R7 — Cái tên phải được giải nghĩa.** `README.md` phải nói `CoS` là **Chief of Staff**,
`cc` là Claude Code, và nói rõ chức năng Chief of Staff **chưa được xây**. Hiện **57 file
ngoài `.cos/`** dùng cái tên ở một dạng nào đó, và **0 file nào trong số đó giải nghĩa nó** —
chuỗi "Chief of Staff" xuất hiện đúng một lần trong toàn repo, ở `intent.md` của chính unit
này. Kiểm: `git grep -ci "chief of staff" -- . ':!.cos'` trả về ít nhất một file, và file đó
là `README.md`; mô tả ở `pyproject.toml` vẫn nói đúng cái app đang làm.

**R8 — Tám file trong `scripts/` đổi tên theo** — `proof_harness.py` (4 dòng),
`verify_0001.py` (2), `verify_0002.py` (8), `verify_0003.py` (3), `verify_0004.py` (4),
`verify_0005.py` (6), `verify_0006.py` (3), `verify_0007.py` (17). Kiểm: mỗi script còn
import được; chạy lại từng cái **không** thuộc requirement này (xem `## Out of scope`).

**R9 — Lockfile mang tên mới và không mang gì khác.** `uv.lock:150` và
`package-lock.json:2,8` là ba dòng duy nhất được phép đổi trong hai file đó. Kiểm: `git diff`
trên hai lockfile không chứa dòng `version` nào của bất kỳ package thứ ba.

**R10 — Tập không được động tới.** Đây là requirement dạng phủ định, và nó là điểm chính của
bản sửa `intent.md`. Mọi con số dưới đây đo **ngoài `.cos/`** — cố ý, vì đó là vùng duy nhất
`impl` chạm tới, và vì một phép đo trên cả repo sẽ tự trôi mỗi lần unit này viết thêm một
artifact (C9). Đo case-sensitive; `grep -i "COS_"` khớp luôn `cos_baodo` và cho số vô nghĩa.

| Token | Phải giữ đúng | Nguồn |
|---|---|---|
| `COS_` | **70 dòng / 20 file** | tiền tố định nghĩa một chỗ, `cos_baodo/config.py:25` |
| `cos.mjs` | **21 file** | vòng lặp định nghĩa ở `.claude/scripts/cos.mjs:24-33` |
| `DEFAULT_DIR = "~/.cos"` | giữ nguyên giá trị | `cos_baodo/data.py:52` |
| `DB_FILENAME = "cos.db"` | giữ nguyên giá trị | `cos_baodo/data.py:53` |
| `.claude/skills/cos-status/` | giữ nguyên tên thư mục | — |

Lệch khỏi bảng này không tự động là lỗi, nhưng phải được ghi vào `impl.md` kèm lý do. Một con
số đổi mà không ai nói tại sao là đúng thứ `0007` đi dọn.

**R11 — `.cos/` không bị viết lại, và người đọc được bảo cách đọc nó ở đúng một chỗ.** Các
artifact đã `accepted` giữ nguyên chữ `cos_baodo` trong thân file; **không file nào trong
`.cos/` được sửa, kể cả thêm header**. Ghi chú tra cứu — nội dung theo hình thức
`.cos/0001_no-session-management/plan.md:4-21`, nói `cos_baodo/` nay là `coscc/` — đặt trong
`.claude/harness.md`, là nơi file đó đã tự nhận là chỗ đọc khi thiết lập hoặc thay đổi
harness (`.claude/harness.md:7`). Lý do không theo hình thức đặt-trong-file của tiền lệ nằm ở
C5. Kiểm: `git diff` của unit này không chạm file nào dưới `.cos/` ngoài các artifact của
chính `0008`; `.claude/harness.md` chứa cả `cos_baodo/` lẫn `coscc/`; và
`git grep -in baodo -- .cos` vẫn trả một số dương. **Không pin con số đó ở đây**: nó là 279 ở
`9280d33`, 289 ở `fed0638`, 319 ở `5226be5`, và tăng nữa khi `impl.md` được commit. `impl.md`
đo lại tại thời điểm của nó.

**R12 — Bundle dựng lại và test xanh.** `uv run coscc-build` rồi `npm test`. Không có gì tự
chạy build (`.claude/CLAUDE.md`), và `coscc` phải từ chối khởi động nếu bundle không khớp
nguồn — hành vi đó phải sống sót qua việc đổi tên. Kiểm: app khởi động được và trang mở được.

**R13 — Dữ liệu dev được phép dọn sạch.** Người khởi xướng xác nhận ngày 2026-09-22 rằng
`~/.cos/cos.db` và `/home/bd/personal-projects/.cos-baodo.json` là dữ liệu dev và xoá hết
không sao. Điều này cần thiết vì sau R3 file JSON kia thành trơ, và `cos.db` đang giữ một
dòng workspace **tên `cos-baodo`** — đo 2026-09-22:
`('/home/bd/personal-projects', 'cos-baodo', 'cos-baodo', ...)`. Một workspace được lưu là
một *tên*, không phải đường dẫn (`.claude/CLAUDE.md`), nên dòng đó trỏ vào
`<COS_WORKING_DIR>/cos-baodo`. Kiểm: sau khi dọn, app khởi động trên database trống và
Settings vẫn in ra hai root.

**R14 — Repo tồn tại public.** `coscc` dưới `baodq97`, visibility `public`, `main` được push
nguyên **không viết lại history**. Kiểm:
`gh repo view baodq97/coscc --json visibility,defaultBranchRef`, và `git log` trên bản clone
đếm ra đúng số commit của local.

**R15 — Phép đo nghiệm thu.** Trên tree đã push:
`git grep -in baodo -- . ':!.cos'` trả **0 dòng trên 0 file**. Hiện là **193 dòng trên 45
file**. Đây là outcome của `intent.md`, nguyên văn.

## Design

### Ba loại chỗ xuất hiện, và đó là toàn bộ thiết kế

Mọi thứ khác suy ra từ việc phân loại **482 dòng** `baodo` (HEAD `fed0638`) thành ba loại, vì
mỗi loại có một cách xử lý khác nhau:

| Loại | Là gì | Xử lý | Ở đâu |
|---|---|---|---|
| **Định danh máy đọc** | `import cos_baodo`, `app_name`, `module-name`, đường dẫn trong test | Đổi tên | `coscc/`, `rxconfig.py`, `pyproject.toml`, `package.json`, lockfile |
| **Prose mô tả hiện tại** | README, `docs/`, `.claude/`, comment trong code | Viết lại | **193 dòng / 45 file** ngoài `.cos/` |
| **Prose ghi lại quá khứ** | artifact đã `accepted` | **Không động** | phần còn lại, trong `.cos/` — 289 dòng ở `fed0638` và còn tăng |

Loại thứ ba là chỗ duy nhất có tiền lệ bắt buộc. `0002 spec.md:245-250` đã kết luận rằng sửa
một artifact đã ký để cứu trích dẫn là "một bản ghi sai theo kiểu khác", và
`.cos/0001_no-session-management/plan.md:4-21` là hình thức đã chọn: giữ nguyên thân file,
thêm một bảng tra ở đầu. Unit này thừa hưởng kết luận đó chứ không quyết lại.

### Ràng buộc ba chiều của Reflex

Tên package không phải một chuỗi tự do. `rxconfig.py` đặt `app_name` và Reflex phân giải nó
thành **một thư mục cùng tên ở gốc repo** — đó là lý do repo dùng layout phẳng, không có
`src/`. Cộng thêm ràng buộc thứ ba: bundle đã biên dịch mang dấu vân tay của nguồn, và cả
`coscc` lẫn `scripts/verify_0003.py` từ chối chạy khi nó không khớp. Ba thứ — thư mục,
`app_name`, bundle — phải đổi cùng lúc. Đổi hai trong ba thì hoặc Reflex không tìm thấy app,
hoặc mọi kiểm tra chạy xanh trên bundle cũ.

### Bất đối xứng mới giữa `Store` và `Journal`

Sau R3, `Store` không còn đọc file legacy còn `Journal` vẫn đọc. Hai lớp này được
`.claude/CLAUDE.md:68` mô tả như một đôi ("kept their interfaces and changed their backing"),
nên sự lệch nhau này là thật và không tự hiển hiện. Nó là **hệ quả bắt buộc của phạm vi**,
không phải một lựa chọn kiến trúc: `intent.md` chỉ cho phép bỏ một cái tên mang tên người, và
`.cos-journal.jsonl` không mang tên nào. Bỏ luôn cả hai sẽ là việc không có gì cho phép.

Ranh giới đứng yên: `Data` giữ `import_once` vì `Journal` còn cần; `Config` giữ tiền tố
`COS_`; harness giữ `.cos/` và `cos.mjs`.

### Dữ liệu chảy qua đâu khi đổi tên

Không có dữ liệu nào phải di trú, và đó là điểm khác biệt lớn nhất so với bản `intent.md` đầu.
`COS_DATA_DIR` vẫn là `~/.cos`, file vẫn là `cos.db`, schema không đổi, `PRAGMA user_version`
không tăng. Cái duy nhất đi ra là một dòng workspace mang tên cũ, và nó đi ra vì R13 cho phép
dọn sạch dữ liệu dev, không vì cơ chế nào đòi.

### Publish

Tạo repo mới thẳng dưới tên mới, không đổi tên một repo đã có. Ba hệ quả: không có redirect
từ tên cũ, tên cũ không bao giờ là tên công khai của repo, và vì history không bị viết lại
nên **40 trích dẫn SHA** trong `.cos/` vẫn phân giải được.

### Chỗ cái tên được giải nghĩa

`coscc` **không** dễ đọc hơn `cos-baodo`. Nửa thứ hai của vấn đề trong `intent.md` — 81 file
dùng cái tên, 0 file nói nó là gì — chỉ được chữa bởi R7, không bởi cái tên. Nếu R7 bị bỏ thì
unit này đổi một acronym không ai đọc được thành một acronym khác không ai đọc được.

## Out of scope

- **Quy ước branch naming.** Là unit riêng, theo `intent.md` constraint 9 — dù đó là chữ đầu
  tiên người khởi xướng nói.
- **Chức năng Chief of Staff.** Chưa impl, và không có artifact nào giữ nó. R7 chỉ buộc README
  nói rằng nó chưa có.
- **Chạy lại bảy proof script.** R8 chỉ buộc chúng đổi tên và import được. Chạy lại tốn quota
  thật, cần mạng, và `verify_0005.py` còn đẩy một branch lên một repo thật. Việc đó thuộc
  `plan.md` quyết, không thuộc requirement ở đây.
- **Viết lại history.** `intent.md` constraint 5. `git filter-repo` cũng không có trên máy
  (đo 2026-09-22).
- **Tên và email tác giả.** `intent.md` constraint 7.
- **9 dòng `/home/bd` trong `.cos/`.** Constraint 4 nói không sửa `.cos/`, nên chúng ở lại.
- **Đổi tên thư mục `/home/bd/personal-projects/cos-baodo`.** Ngoài git.
- **Publish lên PyPI.** `pyproject.toml` đổi tên phân phối nhưng không có bước publish nào;
  đây là app cục bộ của một người.
- **`COS_*`, `~/.cos`, `.cos/`, `cos.mjs`, `cos-status`.** R10 là requirement giữ chúng.

## Concerns

**C1 — `baodo` còn trong 121 commit, công khai vĩnh viễn.** R15 đo trên tree, nên outcome vẫn
đạt được trong khi `git log -p` vẫn tra ra tên riêng. Người khởi xướng đã cân nhắc viết lại
history và chọn không (`intent.md` constraint 5). Ghi lại, không giải quyết.

**C2 — URL công khai vẫn chứa tên một người, và đó là phần spec này không chạm tới.**
`https://github.com/baodq97/coscc` — `baodq97` là handle cá nhân, trùng tiền tố email của cả
121 commit. Tiêu chí người khởi xướng nêu là về **tên repo**, và theo đúng tiêu chí đó thì
`coscc` đạt. Nhưng nếu mục đích thật là "không còn tên người trong URL công khai" thì unit
này không đạt, và `doquocbao-nois` là tài khoản thứ hai đang đăng nhập sẵn.

> **Quyết ngày 2026-09-22: dùng `github.com/baodq97/coscc`.** Người khởi xướng chấp nhận
> handle cá nhân trong URL. Concern giữ nguyên ở đây vì nó là một doubt đã được ghi, không
> phải một lỗi đã được sửa: tên riêng vẫn có mặt trong URL công khai, chỉ là không còn trong
> tên repo. R14 vì thế pin đúng URL đó.

**C3 — Đổi một acronym không đọc được thành một acronym không đọc được.** Xem
`## Design`. R7 là chỗ duy nhất gánh nửa thứ hai của vấn đề, và nó là một dòng prose mà không
test nào kiểm được chất lượng.

**C4 — Tên trỏ tới đích đến, mô tả trỏ tới hiện trạng.** Repo vừa đóng nguyên `0007` để dọn
những câu tự mô tả không còn đúng. Đặt tên theo một chức năng chưa xây là cố ý tạo ra khoảng
cách đó. R7 buộc hai thứ phải được nói tách nhau; nếu sau này chức năng vẫn không tới, câu ở
README sẽ thành đúng loại câu `0007` đã đi dọn.

**C5 — Ghi chú tra cứu cho `.cos/` đặt ở đâu; tiền lệ không áp dụng được, và phép đo nói tại
sao.** Đo ngày 2026-09-22: `.cos/` chứa **90 trích dẫn** dạng `cos_baodo/<file>.py:<dòng>`
trải trên **19 file**, và cả 90 sẽ trỏ vào hư không sau R1 — ví dụ
`.cos/0005_hand-driven-invisible-loop/intent.md:71` trỏ vào `cos_baodo/gitops.py:4`.

Tiền lệ đặt ghi chú **trong** file bị ảnh hưởng (`0001 plan.md:4-21`), và lúc đó chỉ có 1
file. Làm vậy ở đây thì hỏng, vì **5 trong 19 file đó đang bị nơi khác trích dẫn kèm số
dòng**:

```
.cos/0002_no-workspace-management/plan.md
.cos/0004_silent-concurrent-loss/plan.md
.cos/0005_hand-driven-invisible-loop/pr.md
.cos/0005_hand-driven-invisible-loop/spec.md
.cos/0006_demo-data-and-no-durable-store/spec.md
```

Thêm một header vào chúng đẩy mọi dòng xuống và làm **19 trích dẫn đang đúng thành sai**.
Tức là cách đó chữa 90 trích dẫn chết bằng cách tạo ra 19 trích dẫn chết kiểu khác — đúng
nguyên văn thứ `.cos/0001_no-session-management/plan.md:6` gọi là *"một bản ghi sai theo kiểu
khác"*. Tiền lệ từ chối viết lại thân file; nó không lường trước trường hợp chính cái header
cũng phá được thứ khác.

> **Quyết ngày 2026-09-22: một ghi chú, trong `.claude/harness.md`.** Không artifact nào bị
> chạm, 19 trích dẫn số dòng giữ nguyên. Giá phải trả không biến mất và phải nói rõ: ai mở
> thẳng một artifact sẽ thấy đường dẫn chết và không có gì tại chỗ chỉ họ đi đâu. Đó là chỗ
> concern này vẫn hở, và R11 chỉ làm nó rẻ hơn chứ không đóng nó.

**C6 — Bất đối xứng `Store`/`Journal` sau R3.** Xem `## Design`. Nó đúng theo phạm vi và vẫn
là một chỗ lệch mà người đọc code sẽ phải hỏi tại sao. R6 là chỗ nó được nói ra.

**C7 — Sinh lại lockfile có thể kéo theo thay đổi version mà không ai định.** `0007` vừa
chuyển sang Python 3.14 và reflex 0.9.12 rồi **đo lại** các proof trên nền đó — `.claude/CLAUDE.md`
ghi rằng đổi interpreter không mang theo bằng chứng cũ. Nếu `uv lock` nhân cơ hội nâng một
dependency, các con số `0007` vừa đo mất nền mà không có gì báo. R9 vì thế giới hạn diff của
lockfile về ba dòng tên.

**C8 — Bundle cũ bị từ chối sau khi đổi tên, và đó là hành vi đúng.** Dấu vân tay không khớp
nữa, nên lần chạy đầu sau đổi tên sẽ thất bại cho tới khi `coscc-build` chạy. Mọi lệnh trong
README và trong sáu proof cũng đổi tên. Không phải defect, nhưng là chỗ dễ mất nửa giờ nếu
`plan.md` không đặt build đúng vị trí.

**C9 — Baseline của chính unit này tự dịch chuyển.** `.cos/` đo được 279 dòng ở `9280d33` và
289 ở `fed0638`, chỉ vì artifact của unit này nhắc tới cái tên. `impl.md` và các file sau sẽ
đẩy nó lên nữa. R11 vì thế yêu cầu đo lại, và không con số nào trong `.cos/` được dùng lại.

## Open questions

1. **9 dòng `/home/bd` trong `.cos/` để nguyên hay xóa?** Constraint 4 nói không sửa `.cos/`,
   nên mặc định là để nguyên và spec này theo mặc định đó. Nếu người khởi xướng muốn khác,
   điều đổi là: `0005 spec.md` (1 dòng), `0006 impl.md` (3), `0006 intent.md` (5) bị sửa, và
   tiền lệ constraint 4 bị phá cho một lý do khác với lý do nó được lập ra.
2. **Chức năng Chief of Staff không có artifact nào giữ.** Người khởi xướng chọn chưa ghi ở
   đâu. R7 buộc README nói nó chưa có, nhưng không có `idea.md`. Điều một câu trả lời sẽ đổi:
   có `0009 idea.md` thì R7 trỏ được vào một file thay vì tự mô tả; không có thì câu ở README
   là chỗ duy nhất nó tồn tại.
3. **Thư mục `/home/bd/personal-projects/cos-baodo` có đổi tên không?** Ngoài git, nên
   `## Out of scope`. Điều một câu trả lời sẽ đổi: R13 đang dọn một dòng workspace tên
   `cos-baodo`; nếu thư mục cũng đổi tên thì `COS_WORKING_DIR` của người khởi xướng và mọi
   session đang mở mất đường dẫn cùng lúc.
### Đã trả lời, so với bản `5226be5`

4. **Ghi chú tra cứu của R11 đặt ở đâu?** → Một chỗ, trong `.claude/harness.md`. Xem C5;
   quyết định đến từ phép đo 5 file / 19 trích dẫn, không từ khẩu vị.
5. **`doquocbao-nois` có vai gì về sau?** → Không vai gì trong unit này. Repo tạo dưới
   `baodq97`. Nếu về sau chuyển sang một org thì đó là một bước riêng, và GitHub để lại
   redirect — điều đó vẫn đúng và vẫn là một chi phí chưa ai trả.
