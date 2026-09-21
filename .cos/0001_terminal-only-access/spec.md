# Spec: A localhost web channel into a running Claude Code session
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Mỗi yêu cầu dưới đây truy về đúng một câu trong `intent.md`: transcript của một session,
chứa ít nhất 10 lượt prompt gửi từ web page ở localhost và phản hồi tương ứng.

**R1 — Đường vào.** Prompt gõ trên web page tới được session Claude Code đang chạy và xuất
hiện trong ngữ cảnh của nó. Kiểm được: gửi một chuỗi duy nhất từ trang, chuỗi đó xuất hiện
trong session dưới dạng thẻ `<channel>`.

**R2 — Đường ra.** Phản hồi của Claude hiện lại trên chính trang đó mà không cần reload.
Kiểm được: chuỗi trả lời xuất hiện trên trang trong vòng 5 giây kể từ khi Claude gọi tool.

**R3 — Transcript.** Mỗi lượt vào và ra được ghi nối tiếp vào một file, kèm timestamp
ISO-8601 và định danh session. File sống qua việc reload trang và qua việc đóng trình duyệt.
Kiểm được: sau 10 lượt, `grep -c` trên file đếm ra ít nhất 10 bản ghi hướng vào, và file đó
là thứ duy nhất cần để xác minh kết quả trong `intent.md`.

**R4 — Chỉ loopback.** Cổng HTTP chỉ bind `127.0.0.1`. Kiểm được: `ss -ltn` cho thấy địa
chỉ nghe là `127.0.0.1:<port>`, không phải `0.0.0.0`.

**R5 — Một session, không nhầm lẫn.** Transcript ghi rõ 10 lượt thuộc cùng một session.
Kiểm được: mọi bản ghi trong dải đo mang cùng một định danh session.

**R6 — Phân biệt gửi với tới nơi.** Bản ghi hướng vào phân biệt "đã ghi ra transport" với
"session đã nhận". Xem `## Concerns` C4: nếu thiếu, con số 10 không bác bỏ được.

## Design

Trước khi mô tả: toàn bộ hợp đồng channel dưới đây **unverifiable** theo invariant 7. Nó
được xác minh từ CLI `2.1.278` cài trên máy tác giả và từ tài liệu `channels-reference` của
Claude Code — cả hai đều nằm ngoài repo này, nên không file nào ở đây trích dẫn được. Bước
đầu tiên của plan phải là một spike chứng minh hợp đồng bằng cách chạy thật, trước khi xây
bất cứ thứ gì lên trên nó.

**Hình dạng.** Một tiến trình duy nhất, do Claude Code sinh ra như subprocess, mang hai mặt:

- **Mặt MCP** nói stdio với Claude Code. Nó khai báo mình là channel; sự có mặt của khai báo
  đó là thứ khiến Claude Code đăng ký listener. Đường vào là notification đẩy một chuỗi vào
  ngữ cảnh session, bọc trong thẻ `<channel>`. Đường ra là một MCP tool mà Claude gọi để gửi
  chữ trở lại.
- **Mặt HTTP** nghe trên loopback. Trình duyệt gửi prompt lên bằng một request thường, và
  nhận phản hồi xuống bằng một luồng đẩy một chiều còn mở. Hai mặt nằm chung tiến trình vì
  mặt HTTP cần chạm vào đối tượng MCP để đẩy notification.

**Ranh giới và dữ liệu đi qua chúng.**

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| Trình duyệt → HTTP | text người dùng gõ | biên nhận |
| HTTP → MCP | text + meta định tuyến | không có; notification không được ack |
| MCP → Claude Code | `<channel>` chứa text | lời gọi reply tool |
| Bất kỳ hướng nào → Transcript | một bản ghi một dòng | không có |

**Transcript là thành phần, không phải sản phẩm phụ.** Kết quả trong `intent.md` đứng hoặc
đổ theo đúng file này, nên nó được ghi bởi tiến trình ở trên — nơi duy nhất nhìn thấy cả hai
hướng — chứ không phải bởi trình duyệt, vì trạng thái trình duyệt mất khi reload.

**Chỉ dẫn cho Claude.** Server gửi kèm một chuỗi instructions lúc kết nối, nói cho Claude
biết sự kiện đến dưới hình dạng nào và phải gọi tool nào để trả lời. Không có nó, đường ra
không tồn tại trên thực tế dù tool đã đăng ký.

**Cổng vào.** Tài liệu channel nói thẳng rằng một channel không gác là một đường prompt
injection: ai chạm được endpoint thì đặt được chữ trước mặt Claude. Ở đây cổng gác là R4 —
chỉ loopback — chứ không phải allowlist người gửi, vì theo `intent.md` chỉ có một người dùng
trên một máy. Điều này chỉ đúng chừng nào R4 còn đúng; xem C6.

## Out of scope

- **Nhiều workspace.** Chọn giữa các project trong web — `intent.md` đẩy sang intent sau.
- **Permission relay.** Hợp đồng channel có hỗ trợ chuyển prompt duyệt tool ra channel, và
  bỏ qua nó là một lựa chọn chứ không phải thiếu sót: nó mở một đường *ghi* từ trang web vào
  quyết định của session, trong khi lát cắt này cố ý chỉ mở đường chữ.
- **File, ảnh, render diff/output dài.** `intent.md` liệt kê chúng là intent sau.
- **Lịch sử và tìm kiếm trong giao diện.** Transcript ở R3 là file để xác minh, không phải
  tính năng cho người dùng đọc.
- **LAN, internet, TLS, tài khoản.** `intent.md` khoá phạm vi ở localhost một người.
- **Gộp lệnh build/test/lint vào `.claude/CLAUDE.md`.** Việc này `intent.md` giao cho đơn vị
  công việc này, nhưng nó thuộc về plan chứ không phải thiết kế.

## Concerns

**C1 — Có thể thứ này đã tồn tại, và đây là chỗ rẻ nhất để phát hiện.** Trong preview có sẵn
một channel tên `fakechat`: giao diện chat chạy trên trình duyệt ở localhost, hai chiều, có
reply tool. Nó **nằm trong allowlist**, nên chạy bằng cờ `--channels` thường và **không dính
hộp thoại xác nhận** mà `intent.md` lo ở open question 4. Nó cho bạn 10 lượt trao đổi gần
như ngay lập tức, với 0 dòng code.

Nhưng nó **không có persistence** — trạng thái mất mỗi lần reload — nên nó không sinh ra
được transcript ở R3, tức không xác minh được kết quả đã ghi trong `intent.md`. Nó cũng tự
mô tả là công cụ dev, không phải cầu nối, nên nó không phải nền để mở rộng dần như
`intent.md` mong muốn.

Vậy lựa chọn thật là: **dùng `fakechat` để chạm hạn ngày mai nhưng không có thước đo**, hay
**tự viết để có thước đo và có nền, nhưng gánh cờ dev cộng hộp thoại mỗi lần khởi động**.
Đây là mâu thuẫn giữa hai ràng buộc trong `intent.md` — "hạn là thứ được nới, phạm vi thì
không" và "bám cơ chế `--channels`". **Tác giả quyết, không phải spec này.** Nguồn về
`fakechat` nằm ngoài repo nên **unverifiable**; plan phải tự chạy thử nó trước khi loại.

**C2 — Cờ dev là chi phí thường trực.** Channel tự viết không nằm trong allowlist của
preview, nên mỗi lần mở session đều phải qua cờ `--dangerously-load-development-channels` và
một hộp thoại toàn màn hình. `intent.md` open question 4 đã nêu khả năng chi phí này lớn hơn
cái tiện mà web page mang lại. Spec này không giải được điều đó; nó chỉ ghi rằng vấn đề ở
đầu `intent.md` có thể vẫn chưa được giải kể cả khi con số 10 đạt.

**C3 — Hợp đồng đang ở research preview.** Nó có thể đổi, và tổ chức có thể chặn channel
bằng policy. Mọi thứ xây lên trên nó thừa hưởng rủi ro đó.

**C4 — Notification không có ack.** Việc gửi hoàn tất khi chữ được ghi ra transport, không
phải khi Claude đã xử lý. Nếu session không được khởi động với cờ channel, sự kiện **bị bỏ
im lặng và server không nhận lỗi nào**. Đây là lý do R6 tồn tại: một transcript chỉ đếm số
lần gửi sẽ đếm cả những lần không tới nơi, và khi đó con số 10 không còn bác bỏ được điều gì.

**C5 — "Một lượt" cần định nghĩa.** Nhiều sự kiện đến trong lúc Claude đang bận sẽ được giao
cùng nhau ở lượt kế tiếp và xử lý như một nhóm. Nếu lượt được đếm theo phía Claude, mười lần
gõ có thể thành ít hơn mười lượt. Spec này định nghĩa **một lượt = một bản ghi hướng vào
trong transcript**, tức đếm theo phía web page. Nếu tác giả muốn nghĩa khác, sửa `intent.md`
chứ đừng sửa cách đếm.

**C6 — Cổng gác mỏng hơn vẻ ngoài.** R4 là toàn bộ lớp bảo vệ. Bất cứ tiến trình nào trên
máy, kể cả một tab trình duyệt đang mở trang khác, đều có thể POST vào loopback và đặt chữ
trước mặt một agent đang có quyền chạy lệnh và sửa file. Đây là mức chấp nhận được cho một
máy cá nhân một người dùng, nhưng nó là *quyết định*, không phải *sự an toàn*, và nó hết
hiệu lực ngay khi ai đó nới R4.

## Open questions

1. **Đã trả lời** (`intent.md` OQ1): cơ chế cho phép channel tự viết, dưới dạng MCP server
   nói stdio, khai báo capability channel. Xác minh từ CLI và tài liệu chính thức — cả hai
   ngoài repo, nên plan vẫn phải chứng minh bằng một spike chạy được.
2. **Đã trả lời** (`intent.md` OQ2): không còn phụ thuộc plugin Telegram. Hợp đồng được lấy
   từ tài liệu `channels-reference` và kiểm chéo với CLI cài trên máy.
3. **Đã trả lời** (`intent.md` OQ3): transcript do tiến trình channel ghi, là R3.
4. **Còn mở** (`intent.md` OQ4): hộp thoại xác nhận lúc khởi động đắt tới mức nào trong dùng
   hằng ngày? Câu trả lời quyết định C1 — nếu nó thực sự phiền, `fakechat` thắng ở chỗ nó
   không dính hộp thoại, và lát cắt này nên đổi hình dạng.
5. **Mới:** port nào? Cả hai ví dụ trong tài liệu đều chiếm cổng cố định, và `fakechat` mặc
   định 8787. Nếu chọn trùng thì hai thứ không chạy cùng lúc được, đúng lúc C1 đang cần so
   sánh chúng cạnh nhau.
6. **Mới:** transcript sống ở đâu? Nó là bằng chứng cho `intent.md`, nhưng `.cos/NNNN_*/`
   theo `.claude/CLAUDE.md:20-21` chỉ được chứa `intent.md`, `spec.md`, `plan.md` và không gì
   khác. Nên nó không được nằm đó, và plan phải chọn chỗ khác.
