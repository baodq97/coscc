# Impl: One data root under ~/.cos, and one page on top of it
Intent: intent.md. Plan: plan.md. Author: Claude Opus 5. Status: accepted.

## What was built

**Tầng dữ liệu** — commit `2f8f746`. `cos_baodo/data.py` là nơi duy nhất biết thư mục dữ
liệu ở đâu: mặc định `~/.cos`, tạo với quyền `0o700`, chứa `cos.db` và `objects/`. Mở
database ở WAL; mọi read-modify-write đi qua `Data.write`, tức `BEGIN IMMEDIATE`.
`cos_baodo/objects.py` lưu blob theo digest SHA-256 của chính nội dung, ghi ra file tạm rồi
`rename`.

**`COS_DATA_DIR`, `Store` và `Journal`** — commit `f3d1d07`. `Config` có thêm `data_dir`,
đọc trong `from_env` và không ở đâu khác. `Store` và `Journal` giữ nguyên interface, đổi
chỗ chứa sang SQLite. Bảng `workspaces` là `(root, name, label, added_at)` — **không có cột
nào chứa đường dẫn workspace**, nên một database sửa tay không có chỗ để đặt `/etc`. Bảng
`runs` giữ nguyên bản ghi dưới dạng JSON đúng như journal soạn ra; các cột bên cạnh chỉ để
truy vấn và được đọc *từ* JSON lúc insert. Một `.cos-baodo.json` hoặc `.cos-journal.jsonl`
có từ trước `0006` được nhập một lần rồi ghi vào bảng `migrations`; **file gốc không bị
xóa**.

Cùng commit đó, mọi test và mọi proof phải nêu data root của nó một cách tường minh. Lý do
là một sự cố thật: chạy bộ test lần đầu sau khi đổi đã tạo ra `~/.cos` thật trên máy này.

**Sửa khoá** — commit `ce60485`. Xem `## What was measured`.

**Trang** — commit `03c1a61`. `cos_baodo/state.py` là toàn bộ state của trang và không
quyết định gì: mỗi handler gọi `Service`, dịch `Invalid` thành một dòng chữ, rồi dừng.
`cos_baodo/screens.py` là sáu màn của `fragmented-product-experience`, mọi giá trị lấy từ state đó.
`cos_baodo/prototype.py`, `prototype_data.py`, `prototype_test.py` và trang cũ trong
`cos_baodo/cos_baodo.py` bị xóa; `cos_baodo/ui.py` cắt còn `THEME` và `GLOBAL_STYLE`.
`cos_baodo/studio.py` **không đổi một dòng nào** — đó là phát biểu rõ nhất về thứ mà `fragmented-product-experience`
đã chốt: phần nhìn.

**Bằng chứng** — commit `0fac3c4`. `scripts/verify_0003.py` trỏ sang trang mới, giữ nguyên
bốn kiểm tra sàn và negative control. `scripts/verify_0006.py` là mới.
`scripts/verify_fragmented-product-experience.py` bị xóa.

**Tài liệu** — commit `9affe9d`. `README.md`, `.claude/CLAUDE.md`, và `docs/prototype.md`
thành `docs/studio.md`.

## Where the plan was departed from

Cả năm đã ghi trong `plan.md` `## Departures from this plan`, tóm tắt ở đây:

1. **`Service` có thêm sáu method chỉ-đọc** — `activity`, `usage`, `settings`, `artifact`,
   `preferences`, `set_preference`. `spec.md` viết "`Service` không đổi". Cách khác là để
   trang đọc thẳng `Journal`, `policy` và file artifact, tức phá đúng quy tắc mà
   `cos_baodo/service.py` tồn tại để giữ. `set_preference` có ghi và chỉ nhận ba khóa
   trong danh sách trắng.
2. **`journal.py` cũng nhập một lần từ JSONL.** `spec.md` R8 chỉ nói tới file store.
3. **Lane trên board không dùng `blocked` của harness** — xem `## What was measured`.
4. **Phiên bản schema nằm ở `PRAGMA user_version`**, không phải bảng `schema_version`.
5. **`ui.py` bị cắt** còn hai hằng số.

Thêm một departure phát sinh trong lúc viết proof, chưa có trong `plan.md` lúc lập:
`verify_0006.py` ban đầu chờ 2,4 giây cho danh sách hội thoại làm mới và báo đỏ. Chẩn đoán
đầu tiên của tôi — SDK chưa index phiên đang mở — **sai**, và tôi đã thêm một nhánh phòng
vệ dựa trên chẩn đoán đó. Phép đo trực tiếp bác bỏ nó, nhánh đó đã bị gỡ trong `0fac3c4` và
chỗ nó từng nằm giờ ghi lại phép đo.

## What was measured

Ngày 2026-09-22, trên máy này.

**Lệnh `## Proof` của plan, chạy liền một mạch, cả năm exit 0:**

- `npm test` — **31** Node + **279** Python test, số lấy từ output runner. Mốc của `fragmented-product-experience`
  là 31 + 239. Chênh lệch là **+72 −32**: thêm `data_test.py` (21), `objects_test.py` (11),
  và các test mới cho store/journal/config; bớt `prototype_test.py` (**10** test, đếm bằng
  cách chạy bộ test trước và sau khi xóa: 289 → 279).
- `uv run cos-build` — exit 0, fingerprint phủ **6** file nguồn, số lấy từ output wrapper.
- `uv run python scripts/verify_0004.py` — `20 entries survive 4 processes writing at once`.
  Đây là **R9**, và `spec.md` C2 yêu cầu đo lại chứ không kế thừa. Claim 1 còn được chạy
  riêng **5 lần liên tiếp**, cả 5 đều 20/20.
- `uv run python scripts/verify_0003.py` — exit 0, không phải 2. Năm claim xanh, trong đó
  có negative control: cùng phép kiểm **đỏ** khi không có backend phía sau.
- `uv run python scripts/verify_0006.py` — exit 0, `5/5 flows work on real data and survive
  a restart`. Đây là **outcome của `intent.md`**.

**Lỗi khoá tìm được bằng cách chạy, không bằng cách đọc.** Test bốn-tiến-trình của journal
đỏ khoảng **1 lần trên 10** với `database is locked`, ném ra từ chính
`PRAGMA journal_mode=WAL`. Nguyên nhân: đổi journal mode cần khoá độc quyền, và nó đang chạy
**trước** khi `busy_timeout` được đặt, nên kết nối đó không chờ một chút nào. `CREATE TABLE
IF NOT EXISTS` cũng chạy trên mọi kết nối. Sau `ce60485`: `busy_timeout` là câu lệnh đầu
tiên, WAL chỉ đặt khi file chưa ở WAL, schema tạo một lần trong `BEGIN IMMEDIATE` với phiên
bản đọc lại dưới khoá. **15 lần chạy bộ test liên tiếp, 0 lần đỏ.**

**Test rò vào thư mục dữ liệu thật.** Lần chạy đầu sau khi chuyển store sang SQLite đã tạo
`~/.cos/cos.db` thật. Đã sửa bằng cách buộc mọi test và proof nêu data root; xác nhận bằng
`rm -rf ~/.cos && npm test && ls ~/.cos` → không tồn tại.

**`blocked` của harness không phải "cần xem lại".** Đo bằng `cos.mjs status --json` trên
chính repo này: `blocked` là `true` cho cả **6** unit chưa xong và `false` cho **5** unit đã
xong. Lane "Needs review" ban đầu nuốt cả 6, hai lane kia rỗng. Sau khi đọc lane từ trạng
thái artifact, cùng bàn đó phân thành **0 Planned / 4 In progress / 2 Needs review /
5 Complete**, xác nhận trong trình duyệt.

**Migration chạy thật, không phải trong test.** Chạy app với
`COS_WORKING_DIR=/home/bd/personal-projects`: `/api/workspaces` trả `count: 1` với
`cos-baodo` và nhãn của nó; `~/.cos/cos.db` có `user_version=1`, `journal_mode=wal`, một
dòng `workspaces` và một dòng `migrations`; `/home/bd/personal-projects/.cos-baodo.json`
vẫn còn nguyên nội dung cũ.

**Trang chạy thật.** `uv run cos-baodo` bind `127.0.0.1:8790` (`ss -ltn`), `/api/health`
200, `/` 200. Lái bằng Chromium ở 1440×900: **11** work unit thật của repo này, **2** dòng
grant đọc từ `policy.py`, `#data-dir` hiện `/home/bd/.cos`, **0** lỗi JavaScript.
Screenshot sáu màn ở light và dark, một ở 390px, và ngăn kéo work unit đã được chụp.

**Lỗi thứ ba, chỉ nhìn thấy trong ảnh chụp.** Ngăn kéo work unit ghi "Run idea" trong khi
thẻ của chính unit đó ghi "Next: write-pr": nút chạy lấy stage trống *đầu tiên*, mà `idea`
là stage tuỳ chọn và không gate gì. Hai câu trả lời cho một câu hỏi, trên cùng một màn
hình. Sửa bằng cách bỏ qua stage tuỳ chọn; xác nhận lại trong trình duyệt: nút giờ đọc
"Run pr — spends quota". Không một proof nào bắt được lỗi này — nó chỉ lộ ra khi nhìn ảnh.

**Hai lỗi thật do chính việc viết proof tìm ra**, cả hai đã sửa trong `0fac3c4`: tab
Overview của ngăn kéo là nơi chứa phần hiển thị grant, và Reflex chỉ render tab đang mở —
nên R17 phải được đo trước khi chuyển tab; và một lần làm mới danh sách hội thoại trông như
hỏng thật ra là proof chờ chưa đủ lâu.

**Không chạy:** `scripts/verify_0001.py`, `verify_0002.py`, `verify_0005.py`. Hai cái đầu
tốn quota và clone qua mạng; `verify_0005.py` cần `COS_PROOF_REPO` và đẩy một branch. Cả ba
đều đã được sửa để nêu data root, nhưng **chưa có ai chạy chúng sau thay đổi này** — đó là
một khoảng trống thật, không phải một chi tiết.

## What is still open

- **Ba proof chưa chạy lại** — xem trên. `verify_0002.py` đáng lo nhất trong ba, vì claim 2
  của nó là claim đã bị sửa tay (chuyển từ sửa file JSON sang sửa bảng SQLite).
- **Người khởi xướng chưa duyệt thiết kế.** Họ nói "khá ưng ý" về prototype của `fragmented-product-experience` và
  điều đó đã cho phép unit này chạy; nó không phải là một lần duyệt. Việc agent xem
  screenshot cũng không phải.
- **Hai gốc dữ liệu, một bản backup** (`spec.md` C1). Chưa làm gì để giảm nhẹ ngoài việc in
  cả hai lên màn Settings.
- **`~/.cos` trùng tên với `.cos/` của mỗi repository** (`spec.md` C4). Chưa dùng đủ lâu để
  biết có gây nhầm không.
- **Object store chưa có ai ghi vào.** `cos_baodo/objects.py` được xây theo `spec.md` R3 và
  R11, có test riêng, nhưng chưa một đường chạy nào của app gọi `put`. `spec.md` open
  question 1 đã nêu đúng khả năng này; giờ nó là sự thật chứ không còn là câu hỏi. Nối reply
  của một lần chạy bước vào đó, hoặc bỏ tầng này, là phần việc sau.
- **Chưa có số đo về hiệu năng.** Không biết bao nhiêu bản ghi run thì truy vấn board chậm
  thấy được; index `runs_scope` được đặt theo hình dạng truy vấn, không theo số đo.
- **`verify_0003.py` và `verify_0006.py` chồng nhau một phần** (`spec.md` open question 4).
  Giữ hai: cái trước giữ sàn thẩm mỹ và có negative control, không tốn quota; cái sau chứng
  minh năm luồng và tính bền, có tốn. Chưa gộp.
- Unit dừng ở đây theo `intent.md`. Không PR, không review, không ship.
