# Intent: publish under a name that is not a person's
Author: Bao Do. Status: accepted.

## Problem

Người khởi xướng nói, nguyên văn:

> "Tôi muốn chuẩn hóa branch naming lại, để publish lên github. tôi cần chọn tên phù hợp
> hơn"

Sau khi hỏi lại, "tên" ở đây là **tên của repository**, không phải tên branch. Quy ước
branch được tách thành một unit riêng và không thuộc file này.

Repo chưa từng được publish. `git remote -v` rỗng — đo 2026-09-22, và đã được ghi trước đó
ở `.cos/0005_hand-driven-invisible-loop/pr.md:6-12`. Có **120** commit, tất cả nằm thẳng
trên `main`, đúng như `.claude/harness.md:88` mô tả.

Việc không có remote không chỉ là "code chưa lên mạng". Nó **chặn đuôi vòng lặp**.
`cos.mjs status` ngày 2026-09-22 cho thấy ba trong bảy unit đứng ở giai đoạn `pr`:

| Unit | Trạng thái `pr` | Việc kế tiếp |
|---|---|---|
| `0005_hand-driven-invisible-loop` | `draft` | accept `pr.md` |
| `0006_demo-data-and-no-durable-store` | chưa bắt đầu | `write-pr` |
| `0007_stale-claims-and-dead-code` | chưa bắt đầu | `write-pr` |

Và `0005 pr.md` nói thẳng lý do nó còn là `draft`: không có remote thì không có URL thật để
ghi, nên nó từ chối bịa một URL. Ba unit này không đóng được chừng nào chưa có nơi để đẩy.

Về cái tên. Repo hiện tên `cos-baodo`, mang tên riêng của tác giả. Đo trên tracked files
ngày 2026-09-22: chuỗi `baodo` xuất hiện ở **68 file / 463 dòng**, và ở **0** chỗ nó đứng
một mình — luôn nằm trong `cos_baodo` (package Python, 64 file) hoặc `cos-baodo` (tên repo
và console script, 25 file). Tức là tên riêng chỉ dính vào định danh của sản phẩm, không
dính vào chỗ nào khác.

Repo cũng đã tự gọi sản phẩm bằng một cái tên thứ hai: **"COS Studio"** — `README.md:31`,
`docs/studio.md`, `cos_baodo/screens.py`. Nên hiện có hai tên cùng tồn tại cho một thứ.

## Proposed outcome

Trước **2026-10-06**, một người không có quyền gì đặc biệt clone được repo này từ một URL
GitHub **công khai**, và trên đúng bản clone đó, lệnh sau trả về **0 dòng trên 0 file**:

```
git grep -Ein 'baodo|COS_|\.cos/|cos\.mjs|cos-status|COS Studio'
```

Hôm nay, 2026-09-22, cùng lệnh đó trên tree hiện tại trả về **782 dòng trên 81 file**, tổng
cộng repo có 92 tracked file.

Kết quả này sai nếu: không có URL công khai nào trước ngày đó, hoặc có URL nhưng lệnh trên
còn trả về bất kỳ dòng nào.

Ngày **2026-10-06 là do file này đặt**, không phải do người khởi xướng nêu. Nó sửa được, và
sửa thì sửa ở đây chứ không phải ở spec.

## Affected users and systems

- **Người khởi xướng** — hiện là người duy nhất chạy thứ này, và là người duy nhất có dữ
  liệu thật dưới `COS_DATA_DIR`.
- **Bất kỳ ai clone repo sau khi public** — 120 commit và toàn bộ `.cos/` (prose tiếng
  Việt) trở thành công khai vĩnh viễn.
- **Harness.** `.cos/` được tham chiếu ở 43 file / 113 dòng và chứa 25 tracked file thuộc 7
  unit; `cos.mjs` được tham chiếu ở 32 file. Bảng giai đoạn ở
  `.claude/scripts/cos.mjs:24-33` là nơi duy nhất định nghĩa vòng lặp.
- **App.** Package `cos_baodo/` (64 file), console script `cos-baodo`, lệnh build
  `cos-build`, và dấu vân tay bundle mà `cos-baodo` dùng để từ chối chạy trên bundle lệch
  nguồn.
- **Dữ liệu trên máy.** `COS_DATA_DIR` mặc định `~/.cos` và chứa `cos.db`
  (`.claude/CLAUDE.md:51`). `COS_WORKING_DIR`, `COS_HOST`, `COS_PORT`, `COS_PROOF_REPO` là
  tiền tố `COS_` mà scripts và docs đang đọc. Đổi tiền tố mà không di trú thì dữ liệu đang
  có trở thành không nhìn thấy.
- **Sáu script chứng minh** `scripts/verify_0001.py` … `verify_0006.py`, vì chúng đọc biến
  môi trường `COS_*` và gọi console script.

## Constraints

1. **Repo sẽ là public.** Người khởi xướng chốt. Mọi thứ đã commit — kể cả prose tiếng Việt
   trong `.cos/` — đi theo.
2. **Tiêu chí duy nhất cho tên mới: không chứa `baodo`.** Đó là tiêu chí người khởi xướng
   nêu, và là tiêu chí duy nhất họ nêu. Ba tiêu chí khác được đưa ra và **không** được
   chọn: mô tả đúng sản phẩm, khớp với "COS Studio", và chưa bị chiếm trên GitHub/PyPI.
3. **Phạm vi đổi tên rộng hơn tiêu chí ở mục 2, và đó là lựa chọn có chủ ý của người khởi
   xướng.** Họ chọn đổi triệt để, kể cả `COS_*`, `~/.cos`, `.cos/` và `cos.mjs`. Cần nói
   thẳng: không có chuỗi nào trong số đó chứa tên riêng — đo ở `## Problem`, `baodo` đứng
   một mình 0 lần. Nên tiêu chí ở mục 2 **không** đòi hỏi việc đổi stem `cos`. Việc đổi nó
   là một quyết định thêm, không phải hệ quả. Spec không được suy ngược ra rằng mục 2 biện
   minh cho mục 3.
4. **Tên và email tác giả giữ nguyên.** `Author: Bao Do` trên 21 file artifact và email tác
   giả trên cả 120 commit không đổi. Người khởi xướng chốt là không động tới. Ghi công khác
   với tên sản phẩm.
5. **Luồng làm việc không đổi.** Vẫn commit thẳng `main`, `accepted` vẫn tự cấp
   (`.claude/harness.md:88`). Có remote không mở ra bước phê duyệt nào.
6. **Quy ước branch không thuộc unit này.** Nó là một intent riêng, viết sau khi đã có
   remote thật để thử.
7. **`npm test` phải xanh và `uv run cos-build` phải chạy được sau khi đổi tên**, trên tên
   mới. Đổi tên mà không dựng lại bundle thì mọi kiểm tra vẫn xanh trên bundle cũ.

## Open questions

1. **Tên mới là gì?** Chưa chọn. Đây là việc của spec — intent chỉ ghi tiêu chí ở mục 2.
2. **GitHub owner nào?** Tài khoản/tổ chức sẽ chứa repo chưa được nêu. Không có nó thì URL
   trong `## Proposed outcome` không kiểm được.
3. **Đổi `.cos/` thì 7 unit đã đóng có đổi đường dẫn theo không?** Mọi artifact hiện có
   trích dẫn lẫn nhau theo đường dẫn — invariant của repo là "cite only a file committed in
   this repository, by path and line range". Đổi thư mục mà không sửa trích dẫn thì các
   trích dẫn trỏ sai; sửa chúng thì đang viết lại chính cái sổ ghi chép đang làm bằng
   chứng. Chưa có câu trả lời nào là rõ ràng đúng.
4. **`~/.cos` đang có `cos.db` thật thì xử lý ra sao?** Di trú, hay chấp nhận mất, hay đọc
   cả hai đường dẫn một thời gian. Chưa quyết.
5. **Tên mới có còn trống trên GitHub không?** Chưa tra. Người khởi xướng không chọn tiêu
   chí này, nhưng một tên đã bị chiếm thì `## Proposed outcome` không đạt được.
6. **`/home/bd` xuất hiện ở 9 dòng trong 3 file `.cos/`** — `0005 spec.md` (1),
   `0006 impl.md` (3), `0006 intent.md` (5). Public thì chúng lộ ra. Đây là đường dẫn máy
   cá nhân, không phải bí mật, và chưa được quyết là để yên hay xóa.
7. **Thứ tự: publish trước rồi đổi tên, hay đổi tên rồi publish?** Publish trước thì tên cũ
   có mặt công khai một thời gian và GitHub giữ redirect từ tên cũ. Chưa quyết.
