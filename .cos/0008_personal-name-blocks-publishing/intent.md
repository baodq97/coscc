# Intent: publish under a name that is not a person's
Author: Bao Do. Type: chore. Status: accepted.

> **Sửa ngày 2026-09-22, sau khi bản đầu đã accepted và commit (`9280d33`).** Người khởi
> xướng cho biết `cos` **không** phải một chữ viết tắt vô nghĩa: nó là **Chief of Staff**,
> và `cc` là Claude Code. Thông tin đó không có ở đâu trong repo, nên bản đầu kết luận rằng
> stem `cos` vứt được và ghi phạm vi là "đổi triệt để, kể cả `.cos/`". Kết luận đó sai:
> `baodo` là phần thừa, `cos` là phần duy nhất mang ý nghĩa, và nó ở lại. Bản đầu đọc được
> ở `9280d33`; file này thay nó. Harness không có bước sửa đổi, nên ghi đè một file đã
> `accepted` là một lựa chọn, không phải một quy trình — xem `## Constraints` mục cuối.

## Problem

Người khởi xướng nói, nguyên văn:

> "Tôi muốn chuẩn hóa branch naming lại, để publish lên github. tôi cần chọn tên phù hợp
> hơn"

Sau khi hỏi lại, "tên" ở đây là **tên của repository**, không phải tên branch. Quy ước
branch được tách thành một unit riêng và không thuộc file này.

### Cái tên mang tên một người

Repo tên `cos-baodo`. Đo trên HEAD `9280d33` ngày 2026-09-22: chuỗi `baodo` xuất hiện ở
**472 dòng trên 69 file**, và ở **0** chỗ nó đứng một mình — luôn nằm trong `cos_baodo`
(package Python) hoặc `cos-baodo` (tên repo, console script, tên workspace).

### Và phần còn lại của cái tên thì không ai đọc ra được

`cos` là **Chief of Staff**; người khởi xướng nói ngày 2026-09-22. Repo dùng cái tên đó ở
81 file và **không file nào giải nghĩa nó**. Chỗ duy nhất nó xuất hiện dưới dạng người đọc
được là "COS Studio" — `README.md:31` và `docs/studio.md:1` — cả hai đều không nói COS là
gì. Người lạ clone về không có đường nào biết. Đây là cùng loại vấn đề `0007` sinh ra để
dọn: repo mô tả chính nó sai, hoặc không mô tả.

Người khởi xướng cũng xác nhận chức năng Chief of Staff **vẫn là idea, chưa impl**. Hiện
`pyproject.toml:4` mô tả đúng cái nó đang là: *"Web app that lists, creates and resumes
Claude Code sessions across projects"*.

### Chưa publish, và điều đó chặn đuôi vòng lặp

`git remote -v` rỗng — đo 2026-09-22, đã ghi trước ở
`.cos/0005_hand-driven-invisible-loop/pr.md:6-12`. Có **121** commit, tất cả nằm thẳng trên
`main`, đúng như `.claude/harness.md:88` mô tả.

Việc không có remote không chỉ là "code chưa lên mạng". `cos.mjs status` ngày 2026-09-22
cho thấy ba trong tám unit đứng ở giai đoạn `pr`:

| Unit | Trạng thái `pr` | Việc kế tiếp |
|---|---|---|
| `0005_hand-driven-invisible-loop` | `draft` | accept `pr.md` |
| `0006_demo-data-and-no-durable-store` | chưa bắt đầu | `write-pr` |
| `0007_stale-claims-and-dead-code` | chưa bắt đầu | `write-pr` |

Và `0005 pr.md` nói thẳng lý do nó còn là `draft`: không có remote thì không có URL thật để
ghi, nên nó từ chối bịa một URL. Ba unit này không đóng được chừng nào chưa có nơi để đẩy.

## Proposed outcome

Trước **2026-10-06**, một người không có quyền gì đặc biệt clone được repo này từ
`https://github.com/baodq97/coscc`, **công khai**, và trên đúng bản clone đó lệnh sau trả
về **0 dòng trên 0 file**:

```
git grep -in baodo -- . ':!.cos'
```

Hôm nay, 2026-09-22, trên HEAD `9280d33`, cùng lệnh đó trả về **193 dòng trên 45 file**.

Kết quả này sai nếu: không có URL công khai nào trước ngày đó, hoặc có URL nhưng lệnh trên
còn trả về bất kỳ dòng nào.

**`.cos/` bị loại khỏi phép đo có chủ ý**, và đó là **289 dòng trên 24 file** sẽ còn `baodo`
sau khi unit này xong — đo trên working tree ngày 2026-09-22, sau bản sửa này. Con số đó
**tự tăng mỗi lần một artifact nhắc tới cái tên**: bản đầu ở `9280d33` đo được 279, và chính
việc viết lại file này thêm 10 dòng. Bất kỳ ai kiểm lại phải đo lại, không được dùng lại con
số. Lý do loại `.cos/` ra nằm ở constraint 4 — nó là quyết định đã có tiền lệ trong repo,
không phải một chỗ trừ cho tiện.

Ngày **2026-10-06 là do file này đặt**, không phải do người khởi xướng nêu. Nó sửa được, và
sửa thì sửa ở đây chứ không phải ở spec.

## Affected users and systems

Phân bố 193 dòng phải đổi, đo 2026-09-22 trên HEAD `9280d33`:

| Nơi | File | Dòng |
|---|---|---|
| `cos_baodo/` (package) | 28 | 104 |
| `scripts/` (sáu proof) | 8 | 47 |
| `.claude/` | 2 | 21 |
| `pyproject.toml` | 1 | 5 |
| `rxconfig.py` | 1 | 4 |
| `docs/studio.md` | 1 | 4 |
| `README.md` | 1 | 3 |
| `package.json` | 1 | 2 |
| `package-lock.json` | 1 | 2 |
| `uv.lock` | 1 | 1 |

- **Người khởi xướng** — hiện là người duy nhất chạy thứ này.
- **Bất kỳ ai clone repo sau khi public** — 121 commit và toàn bộ `.cos/` (prose tiếng
  Việt) trở thành công khai vĩnh viễn.
- **Reflex ràng tên package vào cấu hình.** `rxconfig.py` đặt `app_name="cos_baodo"` và
  Reflex phân giải nó thành một thư mục cùng tên ở gốc repo. Đổi package là đổi cả hai, và
  bundle phải dựng lại — `.claude/CLAUDE.md` ghi rõ cả `cos-baodo` và
  `scripts/verify_0003.py` từ chối chạy trên bundle không khớp nguồn.
- **Đường import legacy, và một file thật trên đĩa.** `cos_baodo/store.py:45` đặt
  `STORE_FILENAME = ".cos-baodo.json"`, và `/home/bd/personal-projects/.cos-baodo.json` **có
  thật** — 108 byte, một workspace, đo 2026-09-22. Tên đó không đổi được: nó gọi một file đã
  tồn tại. Xem constraint 6.
- **Bốn chỗ** trong `cos_baodo/store_test.py` (`:188`, `:204`, `:229`, `:368`) dựng hoặc
  kiểm file legacy theo đúng tên đó, trong tổng số 33 test của file.

**Không bị ảnh hưởng, và đó là điểm chính của bản sửa này:** tiền tố `COS_` (110 dòng,
`cos_baodo/config.py:25`), `~/.cos/cos.db` (`cos_baodo/data.py:52-53`), thư mục `.cos/` (113
dòng trên 43 file), `.claude/scripts/cos.mjs` (32 file tham chiếu), skill `cos-status`. Tất
cả đều đã sạch tên riêng.

## Constraints

1. **Repo sẽ là public, dưới `baodq97`.** `gh` đang đăng nhập hai tài khoản — `baodq97`
   (active, trùng email của cả 121 commit) và `doquocbao-nois`. Người khởi xướng chọn
   `baodq97`. Không tài khoản nào trong 14 repo hiện có của nó tên `coscc`.
2. **Tên mới là `coscc`** — repo `coscc`, package `coscc/`, lệnh `coscc` và `coscc-build`,
   trang "CoS Studio". `cos` ở lại vì nó là Chief of Staff; chỉ `baodo` bị bỏ.
3. **`cos-build` → `coscc-build` dù nó không chứa tên riêng.** Việc này không do tiêu chí
   đòi; người khởi xướng chọn nó để tên lệnh khớp tên package. Ghi ra để spec không suy
   ngược rằng tiêu chí bắt buộc điều đó.
4. **279 dòng `baodo` trong `.cos/` không được viết lại.** Repo đã gặp đúng tình huống này:
   `0002` đổi `app/` → `cos_baodo/`, và `0002 spec.md:245-250` kết luận *"Sửa nghĩa là viết
   lại một artifact đã ký... Không lối nào sạch. Unit này chọn không sửa"*. Kết quả là bảng
   tra ở `.cos/0001_no-session-management/plan.md:4-21`, nơi 13 trích dẫn cũ được giữ
   nguyên vì viết lại sẽ thành *"một bản ghi sai theo kiểu khác"*. Unit này theo tiền lệ đó.
5. **History bị viết lại — sửa 2026-09-22, sau `14fad47`.** Bản trước của mục này nói history
   giữ nguyên, và nó đúng cho tới khi một lần quét trước khi publish tìm ra hai thứ không
   nên ra công khai: `docs/ai-native-sdlc-playbook.md`, 611 dòng văn bản của Anthropic giữ
   nguyên văn không ghi nguồn và hot-link bốn ảnh từ CDN của họ, nằm ngay ở **commit đầu
   tiên**; và `.claude/settings.local.json.tmp.*`, một file cấu hình máy lọt vào commit do
   `git add -A`. Tree không xoá được chúng khỏi quá khứ, và public thì quá khứ đọc được.

   Người khởi xướng chọn viết lại. `git filter-repo` chạy qua `uvx` nên không thêm
   dependency nào vào repo. **134 commit giữ nguyên số lượng và nội dung thông điệp, nhưng
   tất cả đổi hash.** Hệ quả đã đo và chấp nhận: **44 trích dẫn SHA** trong `.cos/` đang
   phân giải đúng nay chết hết, gồm `0001 plan.md:5` (`81295b9`), `0002 intent.md:4`
   (`c34adfc`) và các artifact của chính `0008`; constraint 4 cấm vá chúng tại chỗ, nên
   `.cos/RENAMES.md` ghi một lần cho tất cả.

   **Điều này không làm `baodo` biến mất khỏi history.** Việc viết lại chỉ bỏ hai đường dẫn,
   không đụng nội dung, nên tên cũ vẫn tra ra được bằng `git log -p`. Outcome vẫn đo trên
   tree, đúng như đã viết.
6. **Đường import legacy bị xóa hẳn**, không phải đổi tên. `STORE_FILENAME`,
   `_import_legacy` và bốn test đi kèm bị bỏ. Nội dung file legacy đã nằm trong `cos.db` từ
   `0006`, và không bản clone nào trong tương lai có file tiền-`0006`. Tiền lệ: `0007` xóa
   object store không ai gọi (commit `4cc0128`). Đây là cách duy nhất đưa
   `.cos-baodo.json` về 0 mà không đổi tên một file đã tồn tại.
7. **Tên và email tác giả giữ nguyên.** `Author: Bao Do` trên 21 file artifact và email trên
   cả 121 commit không đổi. Ghi công khác với tên sản phẩm.
8. **Luồng làm việc không đổi.** Vẫn commit thẳng `main`, `accepted` vẫn tự cấp
   (`.claude/harness.md:88`). Có remote không mở ra bước phê duyệt nào.
9. **Quy ước branch không thuộc unit này.** Nó là một intent riêng, viết sau khi đã có
   remote thật để thử.
10. **`npm test` phải xanh và bundle phải dựng lại** trên tên mới. Đổi tên mà không dựng lại
    thì mọi kiểm tra vẫn xanh trên bundle cũ.
11. **File này ghi đè một bản đã `accepted`.** Harness không có bước sửa đổi, nên đây là một
    lựa chọn của người khởi xướng, không phải một quy trình. Bản đầu ở `9280d33`.

## Open questions

1. **`/home/bd` xuất hiện ở 9 dòng trong 3 file `.cos/`** — `0005 spec.md` (1),
   `0006 impl.md` (3), `0006 intent.md` (5). Public thì chúng lộ ra. Là đường dẫn máy cá
   nhân, không phải bí mật. Constraint 4 nói không sửa `.cos/`, nên câu trả lời mặc định là
   để nguyên — nhưng chưa ai quyết rõ.
2. **Chưa có artifact nào giữ chức năng Chief of Staff.** Người khởi xướng chọn chưa ghi nó
   ở đâu. Nghĩa là tên repo trỏ tới một đích đến mà repo không mô tả. Nếu để vậy, spec phải
   nói rõ tên mang đích đến chứ không mang hiện trạng.
3. **Thư mục trên máy, `/home/bd/personal-projects/cos-baodo`, có đổi tên không?** Nó nằm
   ngoài git. Đổi thì mọi session đang mở mất cwd. Chưa quyết.

### Đã trả lời, so với bản `9280d33`

- **Tên mới là gì** → `coscc` (constraint 2).
- **Owner nào** → `baodq97` (constraint 1).
- **`.cos/` xử lý sao** → không viết lại, theo tiền lệ (constraint 4).
- **`~/.cos` di trú sao** → không phải di trú gì; `COS_*` và `~/.cos` không đổi.
- **Tên có trống trên GitHub không** → có; 14 repo của `baodq97` không có `coscc`.
- **Publish trước hay đổi tên trước** → tạo repo mới thẳng dưới tên mới, nên không có
  redirect từ tên cũ và tên cũ không bao giờ là tên công khai của repo.
- **40 trích dẫn SHA trong `.cos/`** → vẫn đúng, vì constraint 5 giữ history nguyên. Nếu
  history bị viết lại thì cả 40 sai; đó là một lý do nữa cho constraint 5.
