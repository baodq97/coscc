# Intent: Two writers at once lose work without saying so
Author: Bao Do. Status: accepted.

## Problem

Hai chỗ trong app đang để hai bên cùng ghi một thứ, và bên thua **không được báo gì**.

**Chỗ thứ nhất — store.** `cos_baodo/store.py:75` nối tiếp các lần ghi bằng một khoá
**trong tiến trình**. Hai tiến trình app cùng trỏ vào một working folder thì khoá ấy không
thấy nhau: cả hai đọc danh sách, cả hai ghi đè, và mục của bên ghi trước biến mất. Không lỗi,
không cảnh báo — workspace vừa thêm xong đơn giản là không còn ở đó.
`.cos/0003_no-workspace-management/spec.md:281-283` đã ghi đây là chỗ hở, và ghi cả lý do
đáng lo: `0002` đã **đo được** chính xác kiểu hỏng này ở tầng khác — hai tiến trình cùng mở
lại một session, cùng nhận `session_id`, không tiến trình nào nhận lỗi, và một lượt biến mất
hoàn toàn (`.cos/0002_no-session-management/spec.md:165-176`).

**Chỗ thứ hai — `pull` giữa lượt.** `cos_baodo/service.py:171` chạy `git pull --ff-only`
trên một workspace bất kể workspace ấy có session đang sống hay không. App **biết** session
nào đang sống — `cos_baodo/sessions.py:143` giữ đúng danh sách đó — nhưng không hỏi.
Đổi file dưới chân Claude giữa một lượt thì thứ nó đọc xong không còn là thứ nó đang trả lời
về. `.cos/0003_no-workspace-management/spec.md:210-214` ghi nhận và nói thẳng unit ấy không
dựng khoá nào.

Điểm chung, và là lý do hai việc này nằm chung một unit: **không cái nào tự báo.** Không
ngoại lệ, không dòng log, không dấu vết. Đúng loại hỏng mà cả `0003` lẫn `0004` đã cho thấy
là loại tốn nhất — một thứ xanh trong khi nó sai.

## Proposed outcome

Đến hết ngày **2026-09-22**, một lệnh chạy được thoát 0 **chỉ khi** cả hai điều sau đúng:

1. **Không mất mục nào khi ghi đồng thời.** **4** tiến trình riêng biệt, mỗi tiến trình thêm
   **5** workspace vào **cùng** một working folder, chạy cùng lúc. Sau khi cả bốn xong,
   store phải chứa **đủ 20** mục. Hôm nay phép đo này sẽ thất bại.
2. **`pull` bị từ chối khi workspace có session đang sống**, và lý do nói ra được. Đóng
   session rồi thì `pull` chạy lại bình thường.

Kết quả này sai nếu đến hết ngày đó lệnh ấy không tồn tại, hoặc nó chạy nhưng: số mục sau
cuộc đua nhỏ hơn 20, `pull` vẫn chạy khi có session sống, hoặc `pull` bị chặn vĩnh viễn kể
cả khi không còn session nào.

Con số 20 là để cuộc đua đủ dày mà vẫn chạy trong vài giây; 4 tiến trình vì khoá cần chứng
minh là **liên tiến trình**, mà một tiến trình thì không chứng minh được gì.

## Affected users and systems

- **Người dùng:** duy nhất tác giả, một máy, loopback.
- **`cos_baodo/store.py`:** mọi đường ghi danh sách workspace.
- **`cos_baodo/service.py`:** `pull_workspace` nhận thêm một điều kiện từ chối.
- **`cos_baodo/sessions.py`:** đang giữ danh sách client sống; nay có người hỏi nó.
- **Trang Reflex:** `pull` sẽ có thêm một lý do thất bại, và nó phải hiện ra như các lý do
  khác — `0003` đã đòi lỗi `pull` không được nuốt.
- **`scripts/verify_0003.py`:** pull trên một workspace không có session, nên không bị chặn;
  phải còn xanh.

## Constraints

- **Khoá phải liên tiến trình.** Một khoá trong bộ nhớ không giải được vấn đề đang nêu.
- **Không có tiến trình thứ hai làm trọng tài.** Không daemon, không server khoá. Đây là
  công cụ một người trên một máy.
- **Bế tắc phải không xảy ra được.** Một app treo vì chờ khoá còn tệ hơn một mục bị mất, vì
  nó chặn cả những việc chẳng liên quan.
- **`pull` từ chối, không xếp hàng.** Tác giả chọn ngày 2026-09-21: có session sống thì
  `pull` trả lỗi nói rõ lý do. Hàng đợi là cơ chế chưa ai đòi.
- **Session vẫn chat only, không tool nào.** Bốn knob của `0002` giữ nguyên mặc định.
- **Không đổi hình dạng file store.** Nó đã có `version`; unit này không dùng tới.
- **Không đụng `channel/`, `evidence/0001_terminal-only-access/`, và hai lệnh kiểm của
  `0002`, `0003`.**
- **`npm test` giữ nguyên, không trình duyệt** — như `0004` đã chốt.

## Open questions

1. Khoá bằng gì? `flock` là của POSIX; repo này chạy trên Linux qua WSL. Nếu về sau có máy
   khác thì đó là một giả định đang nằm im.
2. Chờ khoá bao lâu rồi bỏ cuộc? Chờ vô hạn là bế tắc; bỏ cuộc sớm là dựng lại đúng cái
   thua im lặng, chỉ khác là lần này có lỗi.
3. "Session đang sống" nghĩa là gì cho một app vừa khởi động lại? Client sống nằm trong bộ
   nhớ, nên sau khi tắt bật lại app không biết tiến trình khác đang mở session nào — cùng
   một lỗ hổng liên tiến trình, ở chỗ khác.
4. Lệnh chứng minh có nên tạo một session thật để kiểm điều 2 không? Tạo thì tốn hạn mức;
   không tạo thì điều 2 chỉ kiểm được bằng cách giả lập trạng thái, tức kiểm một đường không
   ai đi.
5. Một lần `pull` bị từ chối rồi thì người dùng làm gì? Chưa có cách đóng một session từ
   trang.
