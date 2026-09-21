# Spec: A browser proof that fails when the page cannot reach its backend
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Truy về ba điều của `intent.md`: trang nối được, trang hiện dữ liệu sống khớp API, và lệnh
**đỏ được** trên cấu hình hỏng.

**R1 — Một lệnh, ba mã thoát phân biệt được.** `0` là đạt. `1` là **trang hỏng** — thứ duy
nhất được phép đọc là thất bại thật. `2` là **môi trường chưa sẵn sàng**: không có trình
duyệt, chưa build frontend, hoặc cổng đang bận. Kiểm được: dựng từng tình huống, xem mã
thoát. Trộn `1` với `2` là biến "chưa cài chromium" thành "trang chết", và unit này tồn tại
vì một thứ xanh nhầm.

**R2 — Trình duyệt thật, headless, không cần người.** Không có bước nào đợi ai bấm. Kiểm
được: chạy trong một shell không có màn hình và nó vẫn kết thúc.

**R3 — Khẳng định dữ liệu sống, không khẳng định khung.** Lệnh đọc `/api/workspaces` lấy
`working_dir` và `count`, rồi đòi **đúng hai giá trị đó** xuất hiện trên trang. Kiểm được:
working folder có đúng **2** workspace, và trang phải hiện đúng đường dẫn ấy cùng số `2`.
Đây vừa là điều 2 của `intent.md` vừa là cách chứng minh điều 1: hai giá trị đó chỉ tới được
trang nếu `on_mount` đã chạy, và `on_mount` chỉ chạy nếu WebSocket đã nối.

**R4 — Không bám vào chuỗi nội bộ của Reflex.** Không được kết luận "nối được" bằng cách dò
chữ `Connection Error`. Kiểm được: đọc mã lệnh — không có phép so chuỗi nào với văn bản do
Reflex sinh ra. `intent.md` OQ3 nêu lý do: chuỗi ấy đổi được khi nâng phiên bản mà không báo.

**R5 — Phải đỏ được, và điều đó được chứng minh trong cùng lần chạy.** Lệnh tự dựng một
trang **không có backend nào với tới được**, chạy đúng bộ khẳng định ở R3 lên nó, và đòi
chúng **thất bại**. Nếu bộ khẳng định ấy lại đậu, cả lệnh thoát khác 0. Kiểm được: đây là
điều 3 của `intent.md`, và nó là mệnh đề duy nhất nói rằng lệnh này không phải một con dấu
luôn xanh.

**R6 — Không tạo session, không tiêu hạn mức.** Lệnh không được gọi `/api/send`. Kiểm được:
đọc mã lệnh — không có tham chiếu nào tới đường đó; và số session trên đĩa không đổi qua một
lần chạy.

**R7 — Không tự tải trình duyệt.** Thiếu trình duyệt là `2` kèm câu lệnh cần chạy, không
phải một lần tải vài trăm MB không ai yêu cầu. Kiểm được: chạy khi chưa có trình duyệt, xem
mã thoát và thông báo.

**R8 — `npm test` không đổi và không có trình duyệt.** `package.json:7-9` giữ nguyên.
Kiểm được: `npm test` vẫn xanh trên một máy không có chromium.

**R9 — Không đụng `0002` và `0003`.** `scripts/verify_0002.py` và `scripts/verify_0003.py`
chạy nguyên trạng và vẫn xanh.

**R10 — Working folder của lệnh là tạm và tự dọn.** Hai workspace được tạo bằng cách nhận
thư mục có sẵn, **không clone**, nên lệnh không cần mạng. Kiểm được: sau khi chạy, không còn
thư mục tạm nào.

## Design

**Bằng chứng ở đây là một cặp, không phải một phép kiểm.** Chạy khẳng định lên trang tốt chỉ
nói rằng trang tốt. Chạy đúng khẳng định ấy lên trang hỏng và thấy nó **gãy** mới nói rằng
phép kiểm còn sống. `0003` vừa cho thấy một bộ ba mệnh đề xanh nằm trên một trang chết, nên
unit này đo cả hai chiều trong một lần chạy. Mọi thứ khác chảy ra từ đó.

**"Trang hỏng" được dựng bằng hiện tượng, không bằng nguyên nhân.** Lỗi ngày 2026-09-21 quan
sát được là: trang tải đủ, rồi không bao giờ nối được backend. Cách tái tạo rẻ và trung thực
nhất là **phục vụ đúng bản build đã có bằng một máy chủ tĩnh, không có backend nào phía
sau**. Trang vẫn render, vẫn mở WebSocket tới địa chỉ nhúng sẵn, và không ai trả lời — đúng
hiện tượng cũ.

Cách còn lại là tái tạo **nguyên nhân** — build cho một cổng rồi phục vụ ở cổng khác — và
spec này **không** chọn nó, vì `cos_baodo/run.py:66-67` nay từ chối khởi động đúng trường
hợp đó. Muốn dựng lại nguyên nhân thì lệnh kiểm phải khởi động app bằng một đường người dùng
không dùng. Đó là câu `intent.md` OQ2 hỏi, và câu trả lời là: đừng. Xem C2 cho phần còn
thiếu của lựa chọn này.

**Ba thành phần, ranh giới rõ.**

- **Người dựng cảnh.** Tạo working folder tạm, hai thư mục con, nhận chúng làm workspace qua
  lớp dịch vụ. Không mạng, không session.
- **Người phục vụ.** Hai chế độ: app thật (trang + API cùng một tiến trình), và máy chủ tĩnh
  chỉ trả file build, không backend. Chế độ hai là cảnh hỏng ở R5.
- **Người quan sát.** Mở trình duyệt, đọc trang, so với những gì API nói. Nó không biết gì
  về cách cảnh được dựng — cùng một bộ khẳng định chạy lên cả hai chế độ.

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| Người dựng cảnh → đĩa | thư mục tạm, hai tên | working folder có 2 workspace |
| Người phục vụ (app) → mạng loopback | cấu hình | trang + `/api/*` trên một cổng |
| Người phục vụ (tĩnh) → mạng loopback | thư mục build | chỉ trang, không backend |
| Người quan sát → trang | không | `working_dir`, `count` đọc được trên màn hình |
| Người quan sát → `/api/workspaces` | không | `working_dir`, `count` đúng của backend |

**Cổng không được chọn tự do.** `0003` phát hiện bản build nhúng cứng địa chỉ backend
(`rxconfig.py:13-21`), nên app thật **phải** chạy đúng cổng mà bản build đã nhắm — lấy từ
`cos_baodo/config.py:61` và biến môi trường của nó. Đây là chỗ lệnh này khác hẳn
`scripts/verify_0002.py` và `scripts/verify_0003.py`, vốn chạy app trong tiến trình qua ASGI
và không cần cổng nào. Cổng bận là `2`, không phải `1`.

**Python, và là dev dependency.** Cùng ngôn ngữ với hai lệnh kiểm kia. Nằm trong
`[dependency-groups]` chứ không phải `dependencies`, để `uv sync` của người chạy app không
kéo theo một trình duyệt. Và vì nó nằm trong `scripts/`, `package.json:9` — vốn chỉ quét
`cos_baodo/` — không nhặt nó, nên R8 đúng theo cấu trúc chứ không nhờ ai nhớ.

## Out of scope

- **Dựng lại toàn bộ vòng thao tác** — nhận thư mục, clone, pull, xoá, chat. `intent.md`
  cắt, vì mỗi lần chạy sẽ tốn một clone thật và một session thật cho phần
  `scripts/verify_0003.py` đã chứng minh ở tầng API.
- **Tạo session, gửi prompt.** R6.
- **Đưa trình duyệt vào `npm test`.** R8, và `intent.md` chốt.
- **Tự cài trình duyệt.** R7.
- **Kiểm giao diện đẹp hay không** — bố cục, màu, khoảng cách. Không đo được, và `0003` đã
  cắt "đẹp" khỏi mọi kết quả.
- **Chụp ảnh so sánh từng pixel.** Một phép kiểm đỏ mỗi lần đổi chữ là một phép kiểm sẽ bị
  tắt.
- **Chạy trên nhiều trình duyệt.** Một là đủ cho câu hỏi đang hỏi.
- **Đóng C4, C7, OQ11 của `0003`.** Không liên quan; vẫn treo.

## Concerns

**C1 — Lệnh này phải chiếm một cổng cố định, và đó là một khác biệt thật so với hai lệnh
kiểm kia.** `verify_0002` và `verify_0003` chạy app trong tiến trình và không đụng mạng, nên
chúng chạy được bất cứ lúc nào. Lệnh này không: nếu tác giả đang mở app để dùng, cổng bận và
lệnh trả `2`. Có thể sống chung được, nhưng nó là ma sát mới và sẽ làm người ta ngại chạy —
đúng loại ma sát khiến một bằng chứng không ai chạy.

**C2 — Cảnh hỏng chứng minh máy dò còn sống, không chứng minh nguyên nhân cũ bị bắt.** R5
dựng "không có backend". Nguyên nhân thật ngày 2026-09-21 là "build nhắm cổng khác", và thứ
chặn nguyên nhân ấy là `cos_baodo/run.py:66-67`, một chốt riêng với lý lẽ riêng. Nếu ai đó
gỡ chốt đó, lệnh này **sẽ không bắt được** — nó chỉ bắt được khi cấu hình hỏng thực sự được
đem ra chạy. Đây là lỗ hổng đã biết của lựa chọn ở `## Design`, và cái giá phải trả để không
khởi động app bằng đường người dùng không dùng.

**C3 — Bản build cũ vẫn làm lệnh này xanh.** Bước build là thủ công
(`.claude/CLAUDE.md`, mục Build step). Sửa `cos_baodo/cos_baodo.py` mà quên build lại thì
lệnh này mở **bản cũ**, thấy mọi thứ ổn, và trả `0` — trong khi mã nguồn của trang đã khác.
Đây **đúng là hình dạng tiếp theo của chính lỗi unit này đang chữa**: một bằng chứng xanh
trên một thứ không phải thứ đang chạy. Spec này không giải, và nó là thứ đáng giải sớm.
**Người quyết là tác giả:** hoặc lệnh tự build trước khi đo (chậm, nhưng hết nghi), hoặc nó
so dấu vân tay nguồn với dấu của bản build và trả `2` khi lệch, hoặc chấp nhận và ghi rõ.

**C4 — Khẳng định vẫn bám vào cấu trúc trang, chỉ là bám chỗ tốt hơn.** R4 cấm dò chuỗi của
Reflex, nhưng đọc `working_dir` và `count` vẫn cần biết chúng nằm ở đâu trên màn hình. Đổi
bố cục là lệnh đỏ mà trang không hỏng. Đổi lấy: nếu bám lỏng hơn nữa thì nó không còn phân
biệt được trang sống với trang chết, tức mất đúng thứ cần đo.

**C5 — Không gì bắt ai chạy lệnh này.** Giống hệt `verify_0002` và `verify_0003`.
`intent.md` đã nhận cái giá này khi chọn giữ `npm test` không có trình duyệt. Nhắc lại ở đây
vì nó là lý do một unit về bằng chứng vẫn có thể kết thúc bằng việc chẳng ai xem.

**C6 — Chromium headless không phải trình duyệt tác giả dùng.** Một lỗi chỉ xuất hiện trên
trình duyệt thật vẫn lọt. Phạm vi hẹp ở `intent.md` chấp nhận điều này.

**C7 — Thêm một phụ thuộc nặng vào một repo đến giờ chỉ cần `uv sync` và `npm test`.**
`0003` đã thêm một toolchain JavaScript; unit này thêm một trình duyệt. Mỗi cái đều có lý
do, và cộng lại thì "clone về rồi chạy" không còn là một câu ngắn nữa.

## Open questions

1. **Đã trả lời** (`intent.md` OQ1): không tự tải. Thiếu trình duyệt là mã `2` kèm câu lệnh
   cần chạy (R7).
2. **Đã trả lời** (`intent.md` OQ2): không tái tạo nguyên nhân, tái tạo hiện tượng — phục vụ
   bản build tĩnh không có backend. Phần còn thiếu nằm ở C2.
3. **Đã trả lời** (`intent.md` OQ3): không dò chuỗi của Reflex; "nối được" suy ra từ việc dữ
   liệu sống có mặt trên trang (R3, R4).
4. **Đã trả lời** (`intent.md` OQ4): chưa build là `2`, không phải `1` (R1).
5. **Đã trả lời** (`intent.md` OQ5): headless nên không cần người ngồi, nên bài học ở
   `.cos/0002_no-session-management/intent.md:33-34` không bị vi phạm. Nó vẫn là lệnh nặng
   nhất repo — xem C1 và C7.
6. **Còn mở, và là C3:** làm gì với bản build cũ? Tác giả quyết. Đây là câu hỏi sắc nhất còn
   lại, vì trả lời sai thì unit này tạo ra đúng loại bằng chứng mà nó sinh ra để chống.
7. **Còn mở:** lệnh này có nên chạy được khi app đang mở sẵn không? C1 nói hiện tại là
   không. Một cổng riêng cho lúc kiểm sẽ gỡ được ma sát, nhưng bản build nhúng cổng nên phải
   có bản build riêng cho cổng ấy — tức lại một bước build nữa.
