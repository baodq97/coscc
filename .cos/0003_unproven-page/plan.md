# Plan: Build a page check, then prove it can fail
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

Thứ tự ở đây có một ý: **dấu vân tay bản build phải có trước phép đo trang.** Không có nó,
mọi bước sau đo một thứ có thể không phải thứ đang chạy — đúng `spec.md` C3, và đúng hình
dạng tiếp theo của lỗi unit này sinh ra để chống.

## Files that change

| Path | |
|---|---|
| `cos_baodo/build.py` | (new) dựng frontend và ghi dấu vân tay; nơi **duy nhất** trả lời "bản build có khớp nguồn không" |
| `cos_baodo/build_test.py` | (new) |
| `scripts/verify_0003.py` | (new) lệnh ở `## Proof` |
| `pyproject.toml` | có thật, 31 dòng — thêm `playwright` vào `[dependency-groups]`, thêm `cos-build` vào `[project.scripts]` |
| `cos_baodo/run.py` | có thật, 91 dòng — chốt ở dòng 66-67 đang **grep chuỗi trong file JS**; đổi sang dùng `build.py` |
| `.claude/CLAUDE.md` | có thật, 97 dòng — lệnh build đổi thành `uv run cos-build`; thêm `verify_0003.py` |

**Không đụng tới** `cos_baodo/cos_baodo.py`, `cos_baodo/api.py`, `cos_baodo/service.py`,
`cos_baodo/store.py`, `cos_baodo/gitops.py`, `scripts/verify_0001.py`,
`scripts/verify_0002.py`, `channel/`, `evidence/`. Unit này thêm bằng chứng, không đổi thứ
được chứng minh (`spec.md` R9).

**`rxconfig.py` không đổi.** Nó đã đọc địa chỉ từ `cos_baodo/config.py` và đó vẫn đúng; dấu
vân tay chỉ cần *ghi lại* nó đã build với gì.

## Order of work

1. **Dấu vân tay, trước mọi phép đo.** `cos_baodo/build.py`: một hàm băm nguồn quyết định
   nội dung bản build — `cos_baodo/cos_baodo.py`, `rxconfig.py`, và phiên bản Reflex — cộng
   `host:port` đã nhắm; một hàm ghi dấu ấy vào `.web/` sau khi build; một hàm so dấu trên
   đĩa với nguồn hiện tại, trả về **khớp / lệch / chưa build**. Thêm `cos-build` vào
   `[project.scripts]`.
   Kiểm: `uv run cos-build` dựng xong và để lại file dấu; chạy lại ngay thì báo **khớp**;
   `touch` thêm một dòng vào `cos_baodo/cos_baodo.py` thì báo **lệch**; xoá `.web/` thì báo
   **chưa build**. `build_test.py` xanh cho cả ba trạng thái mà **không** chạy build thật.

2. **Một câu hỏi, một chỗ trả lời.** `cos_baodo/run.py` bỏ đoạn grep chuỗi `host:port` trong
   file JS (dòng 66-67 hiện tại) và hỏi `build.py`. Docstring của chính nó đã gọi cách cũ là
   "crude"; quan trọng hơn, hai cơ chế trả lời cùng một câu là đúng thứ `0002` vừa trả giá
   để học (`cos_baodo/sessions.py`, lớp gác riêng).
   Kiểm: `uv run cos-baodo` khởi động sau một bản build khớp; sau khi sửa trang mà chưa
   build lại thì **thoát 2** và thông báo nêu đúng lệnh `uv run cos-build`.

3. **Trình duyệt, và đường thoát khi không có.** Thêm `playwright` vào
   `[dependency-groups]`. Viết phần dò trình duyệt của `scripts/verify_0003.py` trước phần
   đo: không có trình duyệt dùng được thì **thoát 2** kèm câu lệnh cài, tuyệt đối không tự
   tải (`spec.md` R7).
   Kiểm: chạy với một biến môi trường trỏ trình duyệt vào chỗ không tồn tại → thoát 2, chữ
   trong thông báo là hướng dẫn cài, không phải "trang hỏng".

4. **Dựng cảnh và đo cảnh tốt.** `scripts/verify_0003.py`: tạo working folder tạm với đúng
   **2** thư mục con, nhận cả hai làm workspace qua lớp dịch vụ (không clone, không mạng,
   không session), chạy app thật ở **đúng cổng bản build nhắm**, mở trang bằng trình duyệt,
   đọc `working_dir` và số đếm trên màn hình, so với `/api/workspaces`.
   Kiểm: thoát 0 trên cây sạch; cổng bận thì thoát **2**, không phải 1.

5. **Cảnh hỏng, và nó phải làm phép đo gãy.** Vẫn trong cùng lần chạy: **tắt app thật**, rồi
   phục vụ đúng thư mục build bằng một máy chủ tĩnh ở một cổng khác, không backend. Chạy
   **đúng bộ khẳng định ở bước 4** lên trang đó và đòi chúng **thất bại**. Chúng đậu thì cả
   lệnh thoát khác 0.
   Kiểm: cố tình để bộ khẳng định luôn đậu (sửa tạm) và xác nhận lệnh đỏ.
   **Thứ tự bắt buộc, không phải tuỳ chọn:** app thật phải tắt trước. Bản build nhúng cứng
   địa chỉ backend, nên nếu app thật còn nghe ở cổng ấy, trang tĩnh sẽ nối được và cảnh
   "hỏng" hoá ra vẫn tốt — negative control tự vô hiệu mà không báo gì.

6. **Đóng unit.** `.claude/CLAUDE.md`: lệnh build đổi thành `uv run cos-build` và nói vì sao
   (dấu vân tay), thêm `verify_0003.py` vào danh sách lệnh kiểm kèm ghi chú rằng nó cần một
   cổng thật. Chạy `## Proof`. Đặt `Status: done` chỉ sau khi nó xanh.

## What actually happened

**Bước 3, 4, 5 về chung một commit.** Chúng cùng nằm trong một file, và tách ra sẽ là một
script chỉ dò trình duyệt rồi thoát — kiểm được nhưng vô nghĩa. Khả năng kiểm từng phần
không mất: lệnh in kết quả từng mệnh đề.

**Thêm một mệnh đề không có trong plan: "cảnh hỏng vẫn render trang thật".** Viết xong bước
5 thì thấy nó có thể xanh vì lý do sai — thư mục build rỗng thì máy chủ tĩnh trả 404, dữ
liệu sống vắng mặt vì chẳng có trang nào, và negative control báo đạt trong khi không đo gì.
Đó đúng là hình dạng lời nói dối unit này sinh ra để bắt, nên lệnh phải tự kiểm trang có
render không trước khi kết luận. Đã kiểm tay một lần (máy chủ tĩnh trả 200, 10KB, đúng trang
của app) rồi mới đưa thành mệnh đề để lần sau không cần tay nữa.

**Đã chứng minh lệnh đỏ được, bằng một trang hỏng thật.** Gỡ `on_mount=State.load` khỏi
trang, build lại, chạy: thoát **1**, và nó nói `the working folder … never appeared on the
page`. Phục hồi thì xanh lại. Đây là thứ phân biệt lệnh này với một con dấu.

**`spec.md` C1 cắn ngay lần chạy đầu.** Cổng 8790 đang bị một tiến trình khác của tác giả
giữ — `python -m app.web`, bản trước khi đổi tên ở `0002`, vẫn sống kèm hai tiến trình
`claude` con. Lệnh thoát **2** với đúng lý do, không phải 1. Không tắt tiến trình ấy; build
và chạy proof ở cổng 8799 thay thế. Ma sát này là thật, và `spec.md` open question 7 vẫn
mở.

## Risks

**Cảnh hỏng không thật sự hỏng, và lệnh xanh vì lý do sai.** Đây là rủi ro tôi muốn không
phải viết ra, vì nó làm hỏng đúng thứ unit này bán: nếu app thật chưa tắt hẳn khi máy chủ
tĩnh chạy, trang tĩnh nối được backend ở cổng nhúng và bộ khẳng định **đậu**, nên bước 5
báo "phép đo không gãy" — hoặc tệ hơn, nếu ai đó viết ngược điều kiện thì nó im lặng thành
xanh. Dấu hiệu: bước 5 báo cảnh hỏng vẫn đậu, hoặc `ss -ltn` còn thấy cổng cũ sau khi app
đã được yêu cầu tắt. Giảm thiểu: bước 5 phải xác nhận cổng đã đóng **trước** khi mở trình
duyệt, và phải coi "không đóng được cổng" là lỗi chứ không phải chuyện nhỏ.

**Trình duyệt của `playwright` Python không phải bản đang có trong cache.** Máy này có
chromium sẵn từ một cài đặt khác (quan sát ngoài repo, ngày 2026-09-21), nhưng gói Python có
thể đòi một bản khác. Dấu hiệu: bước 3 thoát 2 trên chính máy đã có chromium. Giảm thiểu:
thông báo ở bước 3 phải in ra **đường dẫn nó đã tìm** và lệnh cài, chứ không chỉ "không tìm
thấy trình duyệt".

**Dấu vân tay băm thiếu thứ ảnh hưởng tới bản build.** Bước 1 băm trang, `rxconfig.py` và
phiên bản Reflex. Nếu nội dung bản build còn phụ thuộc thứ khác — một plugin, một biến môi
trường lúc build — thì dấu báo "khớp" trong khi thực ra đã lệch, và `spec.md` C3 quay lại
nguyên vẹn chỉ khó thấy hơn. Dấu hiệu: đổi một thứ, thấy trang đổi, mà dấu vẫn khớp. Giảm
thiểu: không có cách tự động; ghi rõ trong `build.py` nó băm gì và vì sao.

**Lệnh chiếm cổng thật, nên nó không chạy được lúc đang dùng app** (`spec.md` C1). Dấu hiệu:
thoát 2 với lý do cổng bận. Không giảm thiểu trong unit này; đó là `spec.md` open question 7.

**Bộ khẳng định bám vào bố cục trang** (`spec.md` C4). Đổi cách hiển thị working folder hay
số đếm là lệnh đỏ trong khi trang vẫn tốt. Dấu hiệu: đỏ ngay sau một thay đổi thuần trình
bày. Đây là đánh đổi đã chọn, không phải sơ suất.

**Lệnh này vẫn không ai bị bắt phải chạy** (`spec.md` C5). Không có dấu hiệu nào; đó là bản
chất của vấn đề.

## Proof

```
npm test \
  && node scripts/verify-0001.mjs evidence/0001_terminal-only-access/transcript.jsonl \
  && uv run python scripts/verify_0001.py \
  && uv run python scripts/verify_0002.py \
  && uv run cos-build \
  && uv run python scripts/verify_0003.py
```

Hai lệnh cuối cần `COS_PORT` trống. Ngày 2026-09-21 cổng mặc định đang bị chiếm, nên chúng
được chạy với `COS_PORT=8799` — cùng giá trị cho cả build lẫn proof, vì bản build nhúng
cổng. Đặt biến ấy cho cả hai là cách hợp lệ để chạy `## Proof` trên một máy đang mở app.

Bốn lệnh đầu là `spec.md` R8 và R9: thêm một bằng chứng mà không làm đổ thứ có sẵn, và
`npm test` vẫn không cần trình duyệt. `uv run cos-build` đứng ngay trước lệnh cuối vì lệnh
cuối **từ chối chạy trên bản build lệch** — đó là điểm của bước 1.

`verify_0003.py` thoát **0** chỉ khi cả ba đúng, và in kết quả từng phần kể cả khi hỏng:

1. **Bản build khớp nguồn.** Lệch hoặc chưa build là thoát **2**, không phải 1.
2. **Cảnh tốt đậu.** Trang mở được, và `working_dir` cùng số đếm **2** hiện trên màn hình
   khớp đúng những gì `/api/workspaces` trả về.
3. **Cảnh hỏng làm phép đo gãy.** Cùng bộ khẳng định, chạy lên bản build phục vụ tĩnh không
   backend, phải **thất bại**. Đậu là cả lệnh thoát khác 0.

Mã thoát: `0` đạt, `1` trang hỏng hoặc phép đo không gãy được, `2` môi trường chưa sẵn sàng
(không trình duyệt, chưa build, build lệch, cổng bận). Con số **2** workspace lấy từ
`intent.md`; muốn đổi thì sửa ở đó.

## What this plan does not do

- **Không đưa trình duyệt vào `npm test`** (`spec.md` R8). Cái giá: không gì bắt ai chạy
  lệnh mới, đúng như hai lệnh kiểm kia.
- **Không tái tạo nguyên nhân gốc của lỗi 2026-09-21** — build nhắm cổng khác. `spec.md` C2
  ghi rõ phần này để lại cho chốt ở `cos_baodo/run.py`, và ghi rõ nó hổng ở đâu.
- **Không dựng lại vòng adopt/clone/pull/remove/chat trên trình duyệt.** `intent.md` cắt;
  `scripts/verify_0002.py` đã phủ ở tầng API.
- **Không chụp ảnh so từng pixel**, không chạy nhiều trình duyệt.
- **Không gỡ ma sát cổng cố định** (`spec.md` C1 và open question 7).
- **Không giải C4, C7, OQ11 của `0002`.** Vẫn treo, vẫn là việc của tác giả.
