# Intent: The repository says things about itself that are no longer true
Author: Bao Do. Status: accepted.

> Khung vấn đề do agent viết. Lời của người khởi xướng chỉ có hai câu — "giúp tôi kiểm tra
> xem còn technical dept nào không?" và "P0 sẽ thay đổi nên bỏ qua. Tiến hành lên plan
> clean-up P1 P2 và P3" — nên phần mô tả dưới đây là kết quả một lần dò, không phải lời họ.
> Ghi ra vì invariant 1 của `write-intent` đòi lời người khởi xướng và nó không được thoả.

## Problem

Trong hai ngày 2026-09-21 và 2026-09-22, repo đổi bốn thứ lớn: `0005` mở vòng lặp từ ba
stage lên tám, `0006` đổi kho dữ liệu sang SQLite và thay cả trang, năm unit bị retire, và
số của chúng bị dùng lại. Không có gì trong repo kiểm lại rằng các file **đang chạy** còn
nói đúng về chính nó sau bốn lần đó. `npm test` xanh và `cos.mjs status` không báo Problem,
vì cả hai không đo loại trôi này.

Mười ba chỗ đã trôi. Mỗi mục dưới đây là một `path:line` kiểm được, không phải một cảm giác.

**Nói sai về một phép đo:**

1. `.cos/0004_silent-concurrent-loss/plan.md:115` ghi phép đo gốc: 4 tiến trình × 5
   workspace **để lại 8 trên 20** mục, tức mất 12.
   `.cos/0006_demo-data-and-no-durable-store/spec.md:144` đọc ngược nó thành "**mất 8/20**
   entry". Một trong hai con số sai và cả hai đang được trích.
2. `cos_baodo/journal.py:14` nêu "12 of 20" mà không dẫn nguồn nào.

**Citation trỏ vào dòng không còn đỡ được câu nó đỡ:**

3. `rxconfig.py:11` trỏ `cos_baodo/config.py:60` cho mặc định host của `0001`; dòng đó giờ
   là chú thích về `data_dir`, còn host nằm ở `cos_baodo/config.py:66`.
4. `cos_baodo/journal_test.py:5` trỏ `cos_baodo/store.py:16-18` cho phép đo mất entry; dòng
   đó giờ mô tả row SQLite.
5. `scripts/verify_0005.py:8` trỏ `scripts/verify_0003.py:49` cho bảng exit code; ba mã số
   giờ ở `scripts/proof_harness.py:36`.

**Tài liệu tả một repo khác:**

6. `README.md:3` tả vòng lặp là `intent.md` → `spec.md` → `plan.md`. Nó tám stage từ
   `0005`, và `.claude/harness.md` đã nói tám.
7. `.claude/scripts/cos.test.mjs:123` đặt tên test là "the eight units on disk"; `.cos/` có
   sáu unit.
8. `cos_baodo/ui.py:14` nói ba bề rộng của R24 nằm trong `BREAKPOINTS`. Không component nào
   đọc `cos_baodo/ui.py:30`; `screens.py` viết responsive bằng literal rời.

**Giữ thứ không ai dùng:**

9. `cos_baodo/objects.py` (123 dòng) và `cos_baodo/objects_test.py` (115 dòng): không đường
   nào của app gọi `put`. `data.py:54` và `data.py:153` vẫn tạo thư mục cho nó, và
   `data_test.py:53` vẫn assert về nó. `0006 impl.md ## What is still open` đã nêu.
10. `package.json:12` khai `@modelcontextprotocol/sdk`; không file `.mjs` nào import. Nó
    thuộc `channel/`, đã bị xoá ở `b70eee8`.

**Rác chạy ra màn hình mỗi lần test:**

11. `cos_baodo/api.py:252` dùng `@api.on_event("shutdown")`, FastAPI đã deprecate. Nó in
    warning ở mọi lần `npm test`, và `scripts/verify_0001.py:153` ghi rằng không phép kiểm
    nào chạy hook này — nên nó vừa ồn vừa không được đo.
12. `cos_baodo/store_test.py:263` `TheTransactionIsAcrossProcesses` rò một handle stdout của
    tiến trình con, in `ResourceWarning` mỗi lần chạy.
13. `.gitignore:1` trùng `:17` (`.web`, `.web/`); `:5` trùng `:16` (`*.py[cod]`, `*.pyc`).

Cái làm nó đáng sửa không phải mười ba chỗ này. Là chỗ thứ mười bốn, chưa biết ở đâu: không
gì trong repo phân biệt được một citation còn đúng với một citation đã trôi, nên một người
đọc `rxconfig.py:11` hôm nay sẽ đi tới dòng sai và tin nó. Repo này dựng cả một harness để
mọi câu đều có nguồn; một nguồn trỏ sai còn tệ hơn không có nguồn, vì nó trông như đã kiểm.

## Proposed outcome

Đến hết ngày **2026-09-29**, mười ba mục ở `## Problem` còn **0** mục mở, mỗi mục xác nhận
được bằng `path:line` nêu tại chính mục đó hoặc bằng một lệnh in ra trong `plan.md`.

Hôm nay **2026-09-22** con số là 13 mở trên 13. Nguồn: phép dò ghi trong `## Problem`, mọi
citation đã đọc từng dòng.

Mệnh đề này sai nếu còn bất kỳ mục nào mở, hoặc nếu `npm test` đỏ, hoặc nếu `npm test` vẫn
in warning sau khi mục 11 và 12 đóng.

## Affected users and systems

- **Người đọc repo để hiểu nó** — tác giả sau vài tuần, hoặc ai copy `.claude/` sang repo
  khác. Họ là đối tượng duy nhất của mọi citation trong này, và là người mà một citation
  trỏ sai làm hại.
- **`cos_baodo/data.py`, `data_test.py`** — chúng đỡ một tầng object store không ai gọi.
- **`cos_baodo/api.py`** — chỗ duy nhất đóng session khi tiến trình dừng, và chỗ duy nhất
  không có phép kiểm nào chạy qua.
- **`npm test`** — lệnh mà `.claude/CLAUDE.md` nói phải xanh trước khi báo xong việc. Hai
  mục làm nó in warning, nên "xanh" hiện không có nghĩa là "im".
- **Không ảnh hưởng người dùng app.** Không mục nào trong mười ba đổi hành vi trang, trừ
  mục 11 đổi cách shutdown được nối.

## Constraints

- **P0 nằm ngoài unit này, theo chỉ đạo của người khởi xướng.** `cos_baodo/sessions.py:188`
  đặt `setting_sources=None` và tuyên bố nó chặn settings của user/project; SDK
  0.2.157 nói `None` là nạp mọi nguồn. Người khởi xướng nói "P0 sẽ thay đổi nên bỏ qua".
  Ghi ở đây để nó không biến mất khỏi tầm nhìn: nó vẫn mở, và nó lớn hơn cả mười ba mục này
  cộng lại.
- **`cos_baodo/objects.py` bị xoá, không nối vào.** Người khởi xướng chọn ngày 2026-09-22
  giữa hai lối mà `0006 impl.md` để mở. Git giữ lại toàn bộ nếu sau cần.
- **Ba proof không chạy lại trong unit này.** `verify_0001.py`, `verify_0002.py`,
  `verify_0005.py` chưa ai chạy sau khi đổi sang SQLite. Người khởi xướng chọn để ngoài
  scope ngày 2026-09-22 vì cả ba tiêu quota thật. Khoảng trống bằng chứng đó còn nguyên sau
  unit này.
- **Không tạo remote.** Nên `0005 pr.md` còn `draft` và bốn stage cuối của vòng lặp vẫn
  không thể đạt cho bất kỳ unit nào. Quyết định của người khởi xướng, cùng ngày.
- **Không refactor.** `screens.py` 1175 dòng và `state.py` 1024 dòng là hai file lớn nhất
  repo; tách chúng là việc khác và không nằm trong mười ba mục.
- **`npm test` phải xanh sau mỗi commit**, không chỉ ở commit cuối.

## Open questions

1. Mục 1 sửa một figure trong `spec.md` **đã accepted** của `0006`. Harness coi chuỗi commit
   là dấu vết audit, nên sửa file cũ là sửa dấu vết — nhưng để nguyên là để một con số sai
   tiếp tục được trích. `plan.md` phải chốt lối nào và nói rõ lý do; người quyết là tác giả.
2. Mười ba mục này tìm ra bằng một lần dò tay. Không có lệnh nào đếm lại được chúng, nên
   mệnh đề ở `## Proposed outcome` kiểm được từng mục nhưng không kiểm được rằng **không còn
   mục thứ mười bốn**. Dựng một phép kiểm cho citation là một unit riêng, không phải một
   dòng thêm vào đây.
3. Mục 11 chuyển `on_event` sang `lifespan`. Đo ngày 2026-09-22:
   `.venv/lib/python3.12/site-packages/reflex/app.py:815` mount app của Reflex **vào trong**
   app FastAPI, nên app FastAPI là app ngoài cùng và lifespan của nó sẽ chạy. Chưa kiểm:
   liệu có dựng được một phép kiểm cho hook ấy mà không cần cổng trống và quota, vì
   `scripts/verify_0001.py:153` nói `ASGITransport` không chạy lifespan.
