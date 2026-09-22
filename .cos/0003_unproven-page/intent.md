# Intent: The page can be broken while every proof stays green
Author: Bao Do. Type: feat. Status: accepted.

## Problem

`0002` đóng với ba mệnh đề xanh và một trang hỏng.

`spec.md` C11 của `0002` (`.cos/0002_no-workspace-management/spec.md:227-233`) đã nói trước:
sau khi đổi sang Reflex, lệnh kiểm đi qua `/api/*` còn người dùng bấm vào `/_event`, nên
**thứ không được chứng minh lại chính là thứ người dùng dùng**. Nó ghi cả lý do không đóng
được: `spec.md` R10 — một lớp dịch vụ, hai vỏ mỏng — là quy ước về cấu trúc, không phải
phép kiểm tự động.

Ngày 2026-09-21, điều đó xảy ra đúng như mô tả
(`.cos/0002_no-workspace-management/plan.md:183-206`). Trang tải, render đủ mọi thành phần,
rồi đứng im với "Connection Error". Bản biên dịch của Reflex nhúng cứng địa chỉ mở WebSocket
`/_event`, mặc định `http://localhost:8000`, nên phục vụ app ở bất kỳ cổng nào khác là trang
không bao giờ nối được backend: `on_mount` không chạy, danh sách workspace không bao giờ
tải. Đã sửa ở commit `62db370` — `rxconfig.py:34` lấy địa chỉ từ `cos_baodo/config.py`, và
`cos_baodo/run.py:66-67` từ chối khởi động khi bản build được làm cho cổng khác.

Vấn đề còn lại không phải cái lỗi đó. Là **cách nó được tìm ra**: một lần chạy tay, vì có
người bảo thử. Trong lúc nó đang hỏng, `scripts/verify_0002.py` vẫn xanh cả ba mệnh đề và
`npm test` vẫn xanh 111 test Python cùng 23 test Node — vì API không sai gì cả. Không có
lệnh nào trong repo nhìn thấy trang.

Hệ quả: lần sau ai đổi `cos_baodo/cos_baodo.py`, đổi cổng, hay nâng Reflex, thì đúng chế độ
hỏng này quay lại **và mọi bằng chứng vẫn xanh**. Cái chốt ở `cos_baodo/run.py:66-67` chỉ
chặn được một nguyên nhân đã biết; nó không nói được rằng trang còn chạy.

## Proposed outcome

Đến hết ngày **2026-09-24**, một lệnh chạy được mở trang bằng một trình duyệt thật và thoát
0 **chỉ khi** cả ba điều sau đúng, với một working folder chứa đúng **2** workspace:

1. Trang tải và **nối được** backend — không còn ở trạng thái "Connection Error".
2. Trang hiện **đúng** working folder và **đúng số đếm 2** mà `/api/workspaces` trả về, tức
   dữ liệu đi từ backend lên tới chỗ người dùng nhìn, chứ không chỉ là khung tĩnh render
   được.
3. Cùng lệnh ấy, chạy trên cấu hình hỏng của 2026-09-21 — trang được phục vụ từ một bản
   build làm cho cổng khác — **thoát khác 0**.

Điều 3 là phần quan trọng nhất. Một lệnh không bao giờ đỏ thì không phải bằng chứng, và
`0002` vừa cho thấy chính xác một bộ ba mệnh đề xanh trên một trang chết trông như thế nào.

Kết quả này sai nếu đến hết ngày đó lệnh ấy không tồn tại, hoặc nó chạy nhưng: không mở được
trang, không phân biệt được trang nối được với trang không nối được, số đếm nó đọc không
phải số backend trả về, hoặc nó vẫn xanh trên cấu hình hỏng.

## Affected users and systems

- **Người dùng:** duy nhất tác giả, một máy, loopback. Không phân phối.
- **`npm test`:** `.claude/CLAUDE.md` gọi đây là lệnh chạy hết mọi test, và unit này
  **không** đưa trình duyệt vào đó — xem `## Constraints`. Câu "tests must be green" vì thế
  vẫn không bao gồm trang.
- **Một phụ thuộc mới ngoài Python và Node:** một trình duyệt. Máy này đã có chromium sẵn —
  **nằm ngoài repo, nên không trích dẫn được**; quan sát ngày 2026-09-21 bằng
  `ls ~/.cache/ms-playwright`. Một lần clone mới trên máy khác thì chưa chắc có.
- **Bước build:** lệnh mới cần một bản build của frontend, tức nó phụ thuộc vào bước build
  mà `0002` đã đưa vào repo.
- **Hạn mức:** nếu lệnh này không tạo session thì nó không tiêu hạn mức — đó là một phần lý
  do chọn phạm vi hẹp ở `## Constraints`.
- **`scripts/verify_0001.py` và `scripts/verify_0002.py`:** không bị đụng.

## Constraints

- **`npm test` giữ nguyên, không có trình duyệt.** Lệnh mới đứng riêng, như
  `scripts/verify_0001.py` và `scripts/verify_0002.py` đang đứng riêng. Cái giá phải nói
  thẳng: không gì bắt ai chạy nó, đúng như không gì bắt ai chạy hai lệnh kia.
- **Phạm vi hẹp: đúng loại lỗi đã cắn.** Trang tải, nối được, và đọc được dữ liệu sống từ
  backend. **Không** dựng lại toàn bộ vòng thao tác mà tay đã đi qua ngày 2026-09-21 — nhận
  thư mục, clone, pull, xoá, chat. Lý do: mỗi lần chạy sẽ tốn một lần clone thật và một
  session thật, tức tiêu hạn mức và cần mạng, cho phần mà `scripts/verify_0002.py` đã chứng
  minh ở tầng API rồi.
- **Không tạo session.** Kéo theo từ trên: lệnh này không được tiêu hạn mức tài khoản.
- **Phải đỏ được.** Lệnh phải chứng minh nó đỏ trên cấu hình hỏng đã biết, không chỉ xanh
  trên cấu hình tốt.
- **Không sửa `0002`.** `intent.md` của `0002` đặt "không cần trình duyệt" thành một phần
  kết quả của nó, và kết quả đó **đã đạt**. Unit này không lật điều đó và không viết lại
  artifact nào của `0002`; nó thêm một bằng chứng thứ hai cho một thứ khác.
- **Session vẫn chat only, không tool nào.** Bốn knob của `0001` giữ nguyên mặc định.
- **Không xoá `channel/`, `evidence/0001_terminal-only-access/`.**

## Open questions

1. Trình duyệt lấy từ đâu trên một máy chưa có? Lệnh tự cài là một lệnh tải vài trăm MB mà
   không ai yêu cầu; không tự cài thì nó đỏ vì lý do chẳng liên quan gì tới trang.
2. Làm sao dựng được "cấu hình hỏng" ở điều 3 khi `cos_baodo/run.py:66-67` đã từ chối khởi
   động đúng trường hợp đó? Đi vòng qua chốt ấy nghĩa là lệnh kiểm chạy app bằng một đường
   mà người dùng không dùng — đúng thứ `cos_baodo/api.py` phản đối.
3. "Nối được backend" đọc bằng dấu hiệu nào? Đọc chữ "Connection Error" trên trang là bám
   vào chuỗi do Reflex sinh ra, thứ có thể đổi khi nâng phiên bản mà không báo.
4. Lệnh này có nên chạy cả khi `0002` chưa build frontend không, hay cứ đỏ? Đỏ vì chưa build
   là đỏ đúng, nhưng dễ bị đọc nhầm thành trang hỏng.
5. Một lệnh kiểm cần trình duyệt thì có tự mâu thuẫn với bài học của `terminal-only-access` — rằng bằng
   chứng cần người ngồi đó thì làm chậm mọi vòng lặp
   (`.cos/0001_no-session-management/intent.md:33-34`)? Ở đây trình duyệt chạy headless nên
   không cần người, nhưng nó vẫn nặng hơn mọi lệnh khác trong repo.
