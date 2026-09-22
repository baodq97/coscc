# Plan: Prove the loss first, then close it
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

Thứ tự ở đây có một ý: **lệnh chứng minh được viết trước bản sửa, và nó phải đỏ.** Một phép
kiểm cho cuộc đua mà chưa từng thấy cuộc đua thua thì không ai biết nó có đo đúng thứ không.
`0003` vừa mất một vòng vì đúng chuyện này.

## Files that change

| Path | |
|---|---|
| `scripts/verify_0004.py` | (new) lệnh ở `## Proof`; viết ở bước 1, phải đỏ trước khi có bản sửa |
| `cos_baodo/store.py` | có thật, 179 dòng — khoá trong bộ nhớ ở dòng 75; `add`/`set_label`/`remove` ở 125/133/145 |
| `cos_baodo/store_test.py` | có thật — thêm test cho hết hạn chờ và nhả khoá |
| `cos_baodo/sessions.py` | có thật, 224 dòng — `_live` ở dòng 143; thêm một câu hỏi, không đổi vòng đời |
| `cos_baodo/service.py` | có thật — `pull_workspace` ở dòng 171 |
| `cos_baodo/service_test.py` | có thật — thêm test cho từ chối và cho phép lại |
| `.claude/CLAUDE.md` | có thật, 104 dòng — thêm `verify_0004.py` |

**Không đụng tới** `cos_baodo/gitops.py` (nó vẫn chỉ chạy `git` khi được gọi),
`cos_baodo/api.py`, `cos_baodo/cos_baodo.py`, `channel/`, `evidence/`, và ba lệnh kiểm cũ.

`cos_baodo/cos_baodo.py` không đổi vì trang đã hiện lỗi `pull` ở một chỗ chung; lý do từ
chối mới đi qua đúng đường ấy (`spec.md` R8) mà không cần sửa gì.

## Order of work

1. **Lệnh chứng minh trước, và xác nhận nó đỏ.** `scripts/verify_0004.py` với hai mệnh đề
   của `spec.md` R5 và R6/R7, in riêng từng cái.
   Kiểm: chạy **trên cây chưa sửa** thì mệnh đề 1 **đỏ** — 4 tiến trình × 5 workspace để
   lại ít hơn 20 mục. Nếu nó xanh ngay từ đầu thì phép đo sai, không phải app đúng: dừng và
   sửa phép đo. Mệnh đề 2 lúc này cũng đỏ, vì `pull` chưa từ chối gì.

2. **Khoá liên tiến trình cho store.** `cos_baodo/store.py`: một file khoá cạnh store, khoá
   bao trọn đọc-sửa-ghi của `add`, `set_label`, `remove`; hạn chờ 10 giây rồi ném lỗi; nhả
   khoá kể cả khi thân lệnh ném.
   Kiểm: mệnh đề 1 của bước 1 chuyển **xanh**; `store_test.py` xanh, gồm một test giữ khoá
   từ tiến trình khác rồi xác nhận `add` ném lỗi trong hạn chứ không treo, và một test ép
   lỗi giữa lúc giữ khoá rồi lấy lại khoá ngay.

3. **Lớp phiên trả lời được câu hỏi.** `cos_baodo/sessions.py`: một hàm nói workspace nào
   đang có client sống. Không đổi vòng đời client, không đổi `created_here`.
   Kiểm: test khẳng định nó đúng khi có client và khi không; `verify_0001.py` vẫn xanh.

4. **`pull` hỏi trước khi chạy.** `cos_baodo/service.py:171`: có session sống thì ném
   `Invalid` nêu tên workspace, **trước** khi gọi `gitops`.
   Kiểm: mệnh đề 2 chuyển xanh; `service_test.py` có một test khẳng định **không tiến trình
   `git` nào được sinh ra** khi bị từ chối — từ chối sau khi đã chạy `git` là không từ chối.

5. **Đóng unit.** `.claude/CLAUDE.md` thêm `verify_0004.py`. Chạy `## Proof`. Đặt
   `Status: done` chỉ sau khi nó xanh.

## Risks

**Bước 1 xanh ngay, và không ai nhận ra phép đo hỏng.** Đây là rủi ro tôi muốn không phải
viết ra, vì nó làm cả unit thành vô nghĩa mà vẫn trông như thành công: nếu bốn tiến trình
không thực sự chồng nhau — khởi động lệch nhau, hoặc mỗi tiến trình quá nhanh — thì 20 mục
sống sót **không phải vì có khoá**. Dấu hiệu: bước 1 xanh trên cây chưa sửa. Giảm thiểu: các
tiến trình phải được đồng bộ để cùng bắt đầu, và bước 1 chỉ được đi tiếp khi đã thấy nó đỏ
ít nhất một lần.

**Khoá không nhả và app treo** (`spec.md` R3). Một app không thêm được workspace vì một
tiến trình đã chết vẫn giữ khoá thì tệ hơn mất một mục, vì nó chặn cả việc chẳng liên quan.
Dấu hiệu: `add` hết hạn chờ dù không có ai chạy. Giảm thiểu: khoá lấy bằng ngữ cảnh có `try`
bao ngoài, và `flock` tự nhả khi tiến trình chết.

**`verify_0002.py` đỏ vì một lý do chẳng liên quan tới `0002`** (`spec.md` C6). Nó gọi
`pull`, và hiện gọi **trước** khi tạo session — một thứ tự tình cờ, không phải ràng buộc ai
viết ra. Dấu hiệu: `0002` đỏ ở đúng bước pull sau khi bước 4 xong. Giảm thiểu: chạy
`verify_0002.py` ngay sau bước 4, trước khi làm gì khác.

**Hết hạn chờ đọc ra như app hỏng** (`spec.md` C7). Dấu hiệu: người dùng báo "không thêm
được workspace". Giảm thiểu: thông báo phải nói có tiến trình khác đang giữ.

**Lệnh chứng minh tiêu hạn mức** (`spec.md` C5). Một session, một prompt ngắn. Không vòng
lặp.

## Proof

```
npm test \
  && uv run python scripts/verify_0001.py \
  && uv run python scripts/verify_0002.py \
  && uv run python scripts/verify_0004.py
```

`verify_0003.py` **không** nằm trong chuỗi này: nó đòi cổng mặc định trống và một bản build
khớp, nên nó là lệnh chạy riêng (`0003` `spec.md` C1). Đổi lại, `spec.md` R9 vẫn đòi nó
xanh, nên nó phải được chạy tay một lần trước khi đóng unit.

`verify_0004.py` thoát 0 **chỉ khi** cả hai đúng, và in cả hai kể cả khi hỏng:

1. **Không mất mục.** 4 tiến trình riêng, mỗi cái thêm 5 workspace vào cùng một working
   folder, khởi động đồng bộ để chúng thật sự chồng nhau. Sau đó store có **đúng 20** mục.
2. **`pull` từ chối rồi lại cho phép.** Mở một session thật trong một workspace, gọi `pull`,
   nhận lỗi nêu tên workspace; đóng session, gọi lại, thành công.

Con số **4** và **5** lấy từ `intent.md`. Muốn đổi thì sửa ở đó.

## What this plan does not do

- **Không khoá session, chỉ khoá store** (`spec.md` C1, C2). `pull` vẫn đổi file dưới chân
  một session của **tiến trình khác**; câu hỏi chỉ trả lời được trong tiến trình này.
- **Không xếp hàng `pull`.** `intent.md` chốt từ chối.
- **Không thêm cách đóng session từ trang** (`spec.md` open question 5), nên một lần từ chối
  vẫn là ngõ cụt nếu session kia không tự đóng.
- **Không khoá lúc đọc danh sách** (`spec.md` open question 6). `rename` là nguyên tử nên
  người đọc thấy bản cũ hoặc bản mới, không thấy nửa vời.
- **Không lo máy không POSIX** (`spec.md` C4).

## What actually happened

Năm bước chạy đúng thứ tự đã viết, không bước nào phải đảo.

**Bước 1 đỏ đúng như cần.** Trên cây chưa sửa, 4 tiến trình × 5 workspace để lại **8 trên
20** mục, không một dòng lỗi nào ở đâu. Đó là con số `intent.md` nói là chưa đo được; nay
đo được rồi, và nó tệ hơn mức "một vài mục".

**Bước 2 làm mệnh đề 1 xanh.** Khoá `flock` trên `.cos-baodo.lock`, bao trọn
`entries()` + `_write()`. Một chỗ phải đổi so với kế hoạch: hạn chờ chuyển từ tham số mặc
định `LOCK_TIMEOUT` sang đọc tại lúc gọi, vì tham số mặc định bị gắn lúc định nghĩa hàm nên
test không rút ngắn được — một test phải chờ đủ 10 giây là một test không ai chạy.

Test giữ khoá bằng **tiến trình con thật**, không phải descriptor thứ hai trong cùng tiến
trình. Cái thứ hai sẽ xanh kể cả khi khoá quay về `threading.Lock`, tức là kiểm đúng thứ
không cần kiểm. Có thêm một test khẳng định khoá **chết theo tiến trình giữ nó** — đó là
dạng duy nhất của `spec.md` R3 không phụ thuộc vào việc ai đó nhớ nhả khoá.

**Bước 4 suýt xanh vì lý do sai.** Test đầu tiên dùng thư mục trống làm workspace, và nó
xanh — nhưng `gitops.pull` trả về **trước khi** sinh tiến trình nếu thư mục không phải repo,
nên khẳng định "không có `git` nào chạy" đúng dù bỏ hẳn phần kiểm tra session. Đổi sang
`git init` thật thì gỡ phần kiểm tra ra là đỏ ngay với đúng thông báo.

Hai phép sabotage được chạy thật: bỏ lời gọi `fcntl.flock` (test khoá đỏ, "Busy not
raised"), và bỏ câu hỏi về session trong `pull_workspace` (test đỏ, "git ran despite a live
session"). Phép so đường dẫn **không** được sabotage — nó chỉ có test khẳng định, nên độ
tin của nó thấp hơn hai cái kia.

**`spec.md` C6 giữ nguyên.** `verify_0002.py` vẫn `pull` trước khi tạo session nên R6 không
chạm tới nó — xanh, chạy sau bước 4. Thứ tự tình cờ ấy vẫn chưa được ai viết thành ràng
buộc; nó sẽ vỡ vào ngày có người đổi thứ tự trong file đó vì một lý do không liên quan.

## Proof result

Chạy ngày 2026-09-21, trên `main`:

```
npm test                          136 tests, OK
scripts/verify_0001.py            PASS — 5 mệnh đề, 2 project
scripts/verify_0002.py            PASS — 3 mệnh đề, 2 workspace
scripts/verify_0004.py            PASS — 3 dòng, cả hai mệnh đề
```

`verify_0003.py` chạy tay theo `spec.md` R9, ngoài chuỗi trên: cổng mặc định đang bị một
tiến trình khác giữ, nên build và chạy ở `COS_PORT=8795` rồi build lại về 8790. **PASS**, cả
4 dòng, gồm cả dòng nói rằng phép kiểm biết đỏ.

## What is still open

- **`spec.md` C2** — `pull` vẫn cắt dưới chân session của **tiến trình khác**. Khoá store là
  liên tiến trình; câu hỏi về session thì không. Đóng nó cần một dấu hiệu trên đĩa, tức một
  cơ chế nữa, và `intent.md` không cho phép. Người quyết là tác giả.
- **`spec.md` C3** — 10 giây là chọn, chưa đo. Nên xem lại sau lần chạy thật đầu tiên.
- **`spec.md` C4** — `flock` là giả định POSIX, và nó sẽ vỡ lúc khoá chứ không lúc cài đặt.
- **`spec.md` open question 5** — bị từ chối rồi thì trang vẫn chưa có cách đóng session.
  Một lỗi đúng, nhưng là ngõ cụt.
- **`spec.md` C6** — thứ tự trong `verify_0002.py` vẫn là tình cờ, không phải ràng buộc.
