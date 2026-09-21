# Intent: Workspaces must be declared by hand before the app starts
Author: Bao Do. Status: accepted.

## Problem

Danh sách workspace là một biến môi trường, đọc đúng một lần lúc tiến trình khởi động
(`app/config.py:110`), và không có đường nào sửa nó khi app đang chạy — `app/web.py:135-140`
chỉ mở `GET /api/workspaces`, không có route ghi nào cho danh sách ấy. Thêm một project
nghĩa là tắt app, sửa `COS_WORKSPACES` (`.claude/CLAUDE.md:33`), bật lại.

Hệ quả thứ nhất: app không biết tôi có bao nhiêu workspace. Nó chỉ biết tôi đã gõ bao nhiêu
cái vào dòng lệnh lần này. Việc dò session làm theo từng thư mục một
(`app/sessions.py:55`); không chỗ nào liệt kê project. Nên câu hỏi "tôi đang có bao nhiêu
chỗ làm việc" không có ai trả lời ngoài trí nhớ của tôi, và trí nhớ ấy chính là thứ phải gõ
lại đúng vào lần khởi động sau.

Hệ quả thứ hai: đem code mới về vẫn là việc của terminal. `0002` bỏ được terminal khỏi việc
mở session, nhưng bước ngay trước đó — clone một repo rồi khai báo nó — thì chưa. Vòng lặp
vẫn gãy ở cùng một chỗ, chỉ là sớm hơn một bước so với `0001`.

Đây đúng là chỗ `0002` cố ý hoãn, chứ không phải chỗ nó bỏ sót. `spec.md:61` chốt trạng thái
cục bộ của app chỉ gồm danh sách thư mục và bốn knob; `app/config.py:1-11` ghi rõ lý do
hoãn là "bốn knob chưa đủ để biện minh cho một schema". Lý do đó hết hiệu lực đúng lúc danh
sách trở thành thứ sửa được và phải sống qua lần khởi động sau.

Một dấu hiệu nhỏ của cùng chỗ trống ấy: `with_workspaces` (`app/config.py:117-119`) là
mutator duy nhất của danh sách, docstring nói nó phục vụ lệnh kiểm chứng — nhưng lệnh đó
dựng `Config` thẳng (`scripts/verify_0002.py:96`) và không gọi nó. Chỉ còn test của chính
nó gọi (`app/config_test.py:59`). Đường sửa workspace đã được dự trù và chưa từng được dùng
thật.

## Proposed outcome

Đến hết ngày **2026-09-24**, một lệnh chạy được **không cần trình duyệt** chứng minh rằng,
khởi động với working folder trống và **không** khai báo `COS_WORKSPACES` nào, chuỗi sau đi
hết từ đầu đến cuối qua web app:

clone **2** repo vào working folder → app báo đúng **2** workspace → đặt label cho một cái →
tắt app, bật lại, không đổi một biến môi trường nào → vẫn đúng **2**, label còn nguyên →
`pull latest` một cái, xong, không lỗi → tạo session mới trong **mỗi** workspace và nhận
được phản hồi → xoá một workspace → app báo còn đúng **1** → tắt bật lại → vẫn đúng **1**.

Kết quả này sai nếu đến hết ngày đó lệnh ấy không tồn tại, hoặc nó chạy nhưng gãy ở bất kỳ
mắt nào: clone không ra, số đếm không đi đúng 2 → 2 → 1 → 1, label mất sau khi khởi động
lại, `pull latest` lỗi, session nào đó không phản hồi, hoặc xoá rồi mà workspace vẫn còn.

Số đếm nằm trong phép đo là cố ý: nó là đúng cái app hiện không trả lời được, nên nó là chỗ
kết quả này dễ chết nhất. Và phép đo **không cần trình duyệt** vì cùng lý do đã ghi ở
`.cos/0002_no-session-management/intent.md:33-34` — bài học rằng bằng chứng cần người ngồi
đó thì làm chậm mọi vòng lặp.

## Affected users and systems

- **Người dùng:** duy nhất tác giả, một máy, loopback. Không phân phối cho ai khác.
- **`app/config.py`:** đang là nơi duy nhất đọc cấu hình và **cố ý không có setter**
  (`app/config.py:97-103`). Unit này đặt một nguồn cấu hình ghi được cạnh env, nên nó chạm
  vào chính tính chất làm nên giá trị của module đó.
- **`app/web.py`:** `POST /api/send` hiện là route ghi duy nhất (`app/web.py:135-140`).
  Unit này thêm route ghi nằm ngoài phạm vi hội thoại.
- **`is_workspace` và ba chỗ gọi nó** (`app/config.py:82-94`; `app/web.py:44`, `:60`,
  `:85`): ranh giới này đang đứng được một phần nhờ danh sách bất biến trong suốt đời tiến
  trình. Nó sẽ không còn bất biến.
- **Đĩa:** `spec.md:59` ghi app không tự ghi gì, chỉ SDK ghi transcript. Dòng đó sẽ sai.
- **`git` và mạng:** lần đầu app gọi một tiến trình ngoài và lần đầu nó chạm mạng.
- **`scripts/verify_0002.py`:** bằng chứng của `0002` dựng `Config` thẳng
  (`scripts/verify_0002.py:96`); nó phải còn xanh sau khi cấu hình có thêm một nguồn.

## Constraints

- **Working folder khai báo bằng env, không đặt được qua HTTP.** Giữ nguyên cơ chế đã làm
  nên knob 3: `from_env` không có đường đi tới từ một request (`app/config.py:97-103`). Mọi
  workspace và mọi lần clone phải nằm dưới folder đó.
- **Danh sách sửa được không có nghĩa là ranh giới biến mất.** `is_workspace` đổi câu hỏi từ
  "có trong env không" sang "có nằm dưới working folder không" — đổi, chứ không bỏ. Một
  request không được tự mở đường tới thư mục bất kỳ.
- **App tự chạy `git`, và chỉ `clone` với `pull latest`.** Session vẫn **chat only, không
  tool nào** (`app/config.py:44-47`, `.claude/CLAUDE.md:27`). Đây là ràng buộc chứ không
  phải mặc định để ngỏ: `spec.md:101` ghi vì sao tư thế mặc định chặt đến vậy, và một unit
  về quản lý workspace không được phép lật nó.
- **Các thao tác git khác (branch, commit, push) không thuộc unit này.** Tác giả muốn về sau
  giao chúng cho agent qua bash git CLI; việc đó phải bật tool, tức phải xem lại
  `spec.md:101` trước — nên nó là một intent riêng, không phải một knob lật lên ở đây.
- **Trạng thái workspace phải sống qua restart.** Điều này phá `spec.md:61`. Đây là lúc chỗ
  nối ở `spec.md:146-151` được dùng đến — nhưng chỉ cho workspace và metadata của nó, không
  phải giấy phép dựng kho cấu hình trung tâm cho nhiều hồ sơ agent.
- **Không có kho dữ liệu thứ hai cho session.** Session store của SDK vẫn là nguồn sự thật
  (`spec.md:70`).
- **Ràng buộc xác thực của `0002` còn nguyên**
  (`.cos/0002_no-session-management/intent.md:48-55`): công cụ tác giả tự dùng trên máy
  mình; mở cho người khác là vi phạm.
- **Không xoá `channel/`, không xoá `evidence/0001_terminal-only-access/`.**
- **Phạm vi:** đếm, thêm, xoá, đặt label workspace; clone; pull latest. Ngoài phạm vi: giao
  diện đẹp, nhiều người dùng, đăng nhập, TLS, bật tool cho session.

## Open questions

1. Clone repo riêng tư cần credential thì sao? App chạy loopback, không có terminal để hỏi
   token hay passphrase. Chưa biết lệnh kiểm sẽ tránh bằng repo công khai, hay phải giải
   luôn chuyện này.
2. Metadata của workspace gồm gì ngoài label? "Dễ nhìn hơn" là yêu cầu thật nhưng chưa đo
   được, và mỗi trường thêm vào là một trường phải di trú khi hình dạng đổi.
3. Xoá workspace là gỡ khỏi danh sách, hay xoá luôn thư mục trên đĩa? Hai việc khác hẳn nhau
   về mức hồi phục được, và phép đo ở trên không phân biệt.
4. Workspace trỏ vào thư mục đã biến mất thì app xử sao — ẩn, báo lỗi, hay tự dọn?
5. File cấu hình ghi được nằm ở đâu, và có commit vào repo không? Nếu có thì đường dẫn máy
   cá nhân đi vào git; nếu không thì đây là trạng thái ngoài repo đầu tiên của dự án.
6. Docstring của `with_workspaces` (`app/config.py:117-118`) mô tả sai chỗ dùng so với
   `scripts/verify_0002.py:96`. Sửa trong unit này, hay chỉ ghi nhận?
