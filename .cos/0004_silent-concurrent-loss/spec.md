# Spec: Make the losing writer say so
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Hai nhóm, đúng hai điều của `intent.md`.

### A. Ghi đồng thời không mất mục

**R1 — Khoá bao trọn đọc-sửa-ghi, và là khoá liên tiến trình.** Mọi thao tác đổi danh sách
(`cos_baodo/store.py:125`, `:133`, `:145`) phải giữ một khoá **trên file** từ trước lúc đọc
tới sau lúc ghi xong. Khoá trong bộ nhớ hiện tại (`cos_baodo/store.py:75`) không đủ và
không được để lại như thứ duy nhất. Kiểm được: R5.

**R2 — Chờ khoá có hạn, và hết hạn là một lỗi nói ra được.** Chờ tối đa **10 giây**, sau đó
ném lỗi tới người gọi. Kiểm được: giữ khoá bằng một tiến trình khác rồi gọi `add`, nhận lỗi
trong khoảng 10–12 giây, không treo. Con số 10 là **chọn ở đây, không đo**: thao tác dưới
khoá là đọc và ghi một file vài trăm byte, nên 10 giây đã là rất rộng; nó tồn tại để chặn
treo vô hạn chứ không phải để chờ ai.

**R3 — Không bế tắc được.** Khoá luôn được nhả, kể cả khi thân lệnh ném lỗi. Kiểm được: ép
lỗi giữa lúc giữ khoá, rồi một lần gọi khác vẫn lấy được khoá ngay.

**R4 — File khoá không phải file dữ liệu.** Nó nằm cạnh store, tên khác, và không bao giờ
bị đọc như danh sách workspace. Kiểm được: sau một lần ghi, `entries()` không đổi vì sự tồn
tại của nó; xoá nó giữa chừng không làm mất dữ liệu.

**R5 — 20 mục sống sót qua 4 tiến trình.** **4** tiến trình riêng, mỗi cái thêm **5**
workspace vào cùng một working folder, chạy cùng lúc; sau đó store có **đúng 20** mục. Kiểm
được: đây là điều 1 của `intent.md`, và hôm nay nó thất bại.

### B. `pull` không đổi file dưới chân một lượt đang chạy

**R6 — Có session sống thì `pull` bị từ chối, và lý do nêu tên workspace.**
`cos_baodo/service.py:171` hỏi lớp phiên trước khi gọi `git`. Kiểm được: mở một session
trong workspace, gọi `pull`, nhận lỗi có nội dung; không có tiến trình `git` nào được sinh
ra.

**R7 — Không còn session sống thì `pull` chạy bình thường.** Kiểm được: đóng session, gọi
lại, thành công. Chặn vĩnh viễn là hỏng theo kiểu khác.

**R8 — Lý do mới hiện ra như mọi lý do `pull` thất bại khác.** `0002` R20 đòi lỗi `pull`
không được nuốt; điều này không được thành ngoại lệ. Kiểm được: trang hiện nó ở đúng chỗ
đang hiện lỗi git.

### C. Không làm đổ thứ có sẵn

**R9 — Ba lệnh kiểm cũ còn xanh.** `scripts/verify_0001.py`, `scripts/verify_0002.py`,
`scripts/verify_0003.py` chạy nguyên trạng. `verify_0002` có gọi `pull` trên một workspace
không có session, nên R6 không được chạm tới nó.

**R10 — `npm test` giữ nguyên và không cần trình duyệt** (`0003` đã chốt).

**R11 — Một lệnh chứng minh, in từng mệnh đề.** Thoát 0 chỉ khi cả R5 và R6/R7 đúng; hỏng
một mệnh đề vẫn phải in kết quả mệnh đề kia.

## Design

**Khoá là một file riêng, không phải file store.** Ghi store đang dùng ghi-tạm-rồi-`rename`
(`cos_baodo/store.py:165`), tức file store bị thay thế chứ không bị sửa tại chỗ — khoá đặt
trên chính nó sẽ mất theo mỗi lần `rename`. Nên khoá nằm trên một file cạnh đó, chỉ tồn tại
để được khoá, và không bao giờ được đọc như dữ liệu.

**Khoá bao trọn giao dịch, không chỉ lúc ghi.** Thua im lặng hôm nay không phải do hai lần
ghi giẫm nhau, mà do **đọc-rồi-ghi** của hai bên đan vào nhau: cả hai đọc danh sách cũ, cả
hai ghi lại thứ mình vừa tính. Khoá chỉ quanh lúc ghi sẽ vẫn mất mục và sẽ trông như đã
sửa. Ranh giới đúng là cả `entries()` lẫn `_write()` nằm trong cùng một lần giữ khoá.

**Khoá trong bộ nhớ vẫn giữ.** Nó rẻ và nó đúng cho các luồng trong cùng tiến trình; khoá
file thêm vào phần mà nó không thấy. Hai khoá không cạnh tranh nhau vì chúng luôn được lấy
theo cùng một thứ tự.

**Lớp phiên trả lời "workspace này có ai đang nói không".** `cos_baodo/sessions.py:143` đã
giữ từng client sống kèm `cwd`; thứ còn thiếu là một câu hỏi. Lớp dịch vụ hỏi, và lớp
`git` không biết gì về session — nó vẫn chỉ chạy `git` khi được gọi.

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| Lớp dịch vụ → store | thao tác đổi danh sách | xong, hoặc lỗi hết hạn chờ |
| store → file khoá | giữ / nhả | quyền đọc-sửa-ghi |
| Lớp dịch vụ → lớp phiên | một thư mục | có session sống hay không |
| Lớp dịch vụ → lớp git | tên workspace | chỉ khi không có session sống |

**Cái này không giải được, và thiết kế không giả vờ.** Câu hỏi "có session sống không" chỉ
trả lời được cho **tiến trình này**. Một app thứ hai đang mở session trong cùng workspace
thì tiến trình này không thấy, và `pull` sẽ chạy. Xem C2.

## Out of scope

- **Xếp hàng `pull` cho tới khi session đóng.** `intent.md` chốt: từ chối, không xếp hàng.
- **Đóng session từ trang.** `intent.md` OQ5 nêu; không có gì trong kết quả đòi nó.
- **Khoá liên tiến trình cho *session*.** Chỉ store được khoá. Xem C2.
- **Đổi hình dạng file store.** Trường `version` vẫn không dùng tới.
- **Khoá trên máy không POSIX.** Xem C4.
- **Giải mất-lượt khi mở lại session đồng thời** (`.cos/0001_no-session-management/spec.md:165-176`).
  Cùng họ, khác chỗ, và vẫn bị chặn bằng knob 4 chứ không bằng khoá.

## Concerns

**C1 — Sửa đúng chỗ đang bị đo, không phải chỗ hay bị nhắc.** Chỉ store được khoá. Điều đó
đúng với `intent.md`, nhưng phải nói rõ để không ai đọc unit này thành "app đã an toàn khi
chạy hai bản".

**C2 — `pull` vẫn đổi file dưới chân một session của tiến trình khác.** R6 hỏi lớp phiên
trong bộ nhớ, nên nó chỉ thấy session của chính app này. Hai app cùng working folder thì
đúng cái lỗ `intent.md` mở đầu vẫn còn, chỉ hẹp hơn. Đóng nó cần một dấu hiệu trên đĩa cho
"workspace đang bận", tức một cơ chế nữa, và `intent.md` không cho phép. **Người quyết là
tác giả** nếu muốn đi tiếp.

**C3 — Chờ 10 giây là một lựa chọn, không phải phép đo.** Khác với `0002` C3 — ở đó con số
bịa rồi mới đo (`cos_baodo/gitops.py`). Ở đây không có gì để đo trước khi có khoá; con số
nên được xem lại sau lần chạy thật đầu tiên chứ không phải được tin.

**C4 — `flock` là giả định về hệ điều hành đang nằm im.** Repo chạy Linux qua WSL nên nó
đúng hôm nay. Một máy không POSIX sẽ hỏng ở chỗ này, và sẽ hỏng lúc khoá chứ không lúc cài
đặt — tức muộn. Ghi ở đây vì `intent.md` OQ1 hỏi và không có gì trong unit này bắt nó lộ ra
sớm hơn.

**C5 — Lệnh chứng minh tiêu hạn mức, và đó là lựa chọn.** `intent.md` OQ4 hỏi: giả lập
trạng thái session hay tạo session thật? Spec chọn **thật**, một session, một prompt ngắn.
Giả lập sẽ kiểm một đường không ai đi, và `0003` vừa cho thấy đúng loại lỗi chỉ lộ ra khi đi
đường thật. Cái giá là mỗi lần chạy tốn một ít hạn mức.

**C6 — Khoá làm hỏng một giả định của lệnh chứng minh cũ.** `scripts/verify_0002.py` gọi
`pull`. Nếu nó chạy khi có session sống trong cùng workspace thì R6 sẽ chặn và `0002` đỏ.
Hiện nó `pull` trước khi tạo session, nên không sao — nhưng đó là **thứ tự tình cờ**, không
phải một ràng buộc ai viết ra. Đổi thứ tự trong file đó là làm `0002` đỏ vì một lý do chẳng
liên quan gì tới `0002`.

**C7 — Một lỗi hết hạn chờ trông giống hỏng, nhưng là hệ thống đang làm đúng.** Người dùng
sẽ thấy "không thêm được workspace" và nghĩ app hỏng. Thông báo phải nói rằng có tiến trình
khác đang giữ, nếu không thì đổi một lỗi im lặng lấy một lỗi khó hiểu.

## Open questions

1. **Đã trả lời** (`intent.md` OQ1): `flock`. Giới hạn ở C4.
2. **Đã trả lời** (OQ2): 10 giây rồi lỗi (R2). Lý do con số ở C3.
3. **Còn mở** (OQ3, và là C2): session của tiến trình khác. Tác giả quyết nếu muốn đóng.
4. **Đã trả lời** (OQ4): tạo session thật (C5).
5. **Còn mở** (OQ5): sau khi bị từ chối, người dùng chưa có cách đóng session từ trang. Nhỏ,
   nhưng nó biến một lỗi đúng thành một ngõ cụt.
6. **Mới:** khoá có nên bao cả lúc **đọc** danh sách không? Hiện chỉ bao lúc đổi. Một lần
   đọc trùng lúc ai đó ghi sẽ thấy danh sách cũ hoặc mới, không thấy nửa vời — vì `rename` là
   nguyên tử. Nên chưa cần; ghi lại để người sau không tưởng là bỏ sót.
