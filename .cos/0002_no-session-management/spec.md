# Spec: A web app that lists, creates and resumes sessions across projects
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Mỗi yêu cầu truy về đúng câu trong `intent.md`: qua web app, với 2 project khác nhau, tạo
được session, nhận được phản hồi, mở lại đúng session đó với lịch sử còn nguyên.

**R1 — Liệt kê theo project.** App liệt kê được session của **ít nhất 2 thư mục project
khác nhau**, mỗi session kèm định danh, tóm tắt và thư mục của nó. Kiểm được: một lệnh trả
về danh sách cho hai `cwd` khác nhau, và không có mục nào của project này lẫn sang project
kia.

**R2 — Tạo và trả lời.** App tạo được session mới trong một project được chọn, gửi một
prompt và nhận lại phản hồi. Kiểm được: lệnh trả về `session_id` mới và phần chữ phản hồi
khác rỗng.

**R3 — Mở lại đúng session đó.** Sau khi client đã đóng, app mở lại session ở R2 và
`session_id` **khớp chính xác** cái đã tạo, với lịch sử trước đó còn nguyên. Kiểm được: so
sánh hai chuỗi định danh, và số message đọc được sau khi mở lại **không nhỏ hơn** số trước
đó. Đây là mệnh đề trung tâm của `intent.md`; trượt cái này là trượt cả unit.

**R4 — Chứng minh không cần trình duyệt.** Mọi điều trên phải kiểm được bằng một lệnh chạy
tới bề mặt HTTP của app, không cần người mở tab. `intent.md` đặt điều này thành một phần
của kết quả, không phải tiện nghi.

**R5 — Chỉ loopback.** Bề mặt HTTP chỉ bind `127.0.0.1`. Kiểm được: `ss -ltn` cho thấy địa
chỉ nghe là loopback.

**R6 — Một nguồn sự thật.** Nội dung hội thoại **không** được app lưu bản sao. Lịch sử đọc
từ session store của SDK. Kiểm được: xoá mọi trạng thái cục bộ của app rồi mở lại session ở
R3, lịch sử vẫn đầy đủ.

## Design

**Session là transcript, không phải tiến trình.** Đây là điều quan trọng nhất trong file
này và nó được xác minh bằng chạy thật, không bằng tài liệu: hàm liệt kê của SDK trả về cả
những session đã tắt từ lâu, đọc thẳng từ đĩa. Do đó "mở lại" nghĩa là **dựng lại từ bản ghi
trên đĩa** và nối tiếp vào đó, chứ không phải gắn vào một tiến trình còn sống. Mọi thứ khác
trong thiết kế chảy ra từ câu này.

**Hình dạng.** Một tiến trình Python, ba lớp:

- **Lớp phiên.** Với mỗi session đang hoạt động, app giữ một client của SDK. Client đó tự
  sinh tiến trình CLI riêng; vòng đời của nó do app quyết, không do terminal nào.
- **Lớp đọc.** Liệt kê session theo thư mục và đọc lịch sử — đều là thao tác đọc đĩa, không
  cần client nào đang sống. Đây là thứ làm được R1 và R6.
- **Lớp HTTP.** Loopback. Trình duyệt lấy danh sách và lịch sử, gửi prompt, nhận phản hồi
  theo luồng. Cùng bề mặt đó phục vụ luôn lệnh kiểm ở R4 — không có đường riêng cho test,
  vì một đường riêng là một đường không ai dùng thật.

**Ranh giới và dữ liệu đi qua.**

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| Trình duyệt → HTTP | project, session (nếu có), text | định danh session, luồng phản hồi |
| HTTP → lớp phiên | prompt + thư mục làm việc | phản hồi theo từng phần |
| HTTP → lớp đọc | thư mục, hoặc định danh session | danh sách session, hoặc lịch sử |
| Lớp phiên → đĩa | không do app ghi | SDK tự ghi transcript |

**Trạng thái cục bộ của app chỉ có một thứ:** danh sách thư mục nào được coi là workspace.
Đó là vài dòng, không phải cơ sở dữ liệu. Nội dung hội thoại không nằm trong đó (R6).

**Không có kho dữ liệu thứ hai.** Session store của SDK là nguồn sự thật. Xem C6.

## Out of scope

- **Nối vào session đang mở trong terminal.** Không làm được bằng đường này, và `intent.md`
  đã ghi đó là đánh đổi đã chấp nhận. Xem C1 — nó sẽ *có cảm giác* như lỗi.
- **Chạy nhiều session song song trong một màn hình.** Tạo và mở lại là phạm vi; bố cục
  nhiều khung thì không.
- **Render diff, file, output dài.** Vẫn là intent sau, như ở `0001`.
- **Xoá, đổi tên, gắn nhãn session** — SDK có sẵn, nhưng `intent.md` không cho phép.
- **Gỡ `channel/`.** `intent.md` cấm; `scripts/verify-0001.mjs:9` phụ thuộc vào nó.
- **Đa người dùng, đăng nhập, TLS.** Một người, loopback.

## Concerns

**C1 — "Mở lại" không phải "nối lại", và người dùng sẽ tưởng là một.** Session mở từ web là
tiến trình của app; session mở trong terminal là tiến trình khác. App **nhìn thấy** transcript
của session terminal qua lớp đọc, nên nó sẽ hiện ra trong danh sách — nhưng gửi prompt vào đó
là **tạo một tiến trình mới nối tiếp bản ghi cũ**, không phải nói chuyện với cái terminal
đang mở. Nếu cả hai cùng chạy trên một session, hai tiến trình cùng ghi vào một transcript.
Điều gì xảy ra thì chưa biết — xem open question 3. **Tác giả quyết** có chặn việc này hay
để mở.

**C2 — Permission prompt không có terminal để hiện ra.** `0001` chỉ *nói chuyện* với session
có sẵn; `0002` **tạo** session chạy được lệnh và sửa được file. Khi Claude cần duyệt một
tool, ở đây không có terminal nào để hỏi. Ba lối, và không lối nào miễn phí: chặn hẳn tool
ghi; mở sẵn một tập tool hẹp; hoặc bật chế độ bỏ qua duyệt, tức trao cho một cổng loopback
quyền chạy lệnh không cần hỏi. **Đây là câu hỏi an toàn thật sự của unit này. Tác giả quyết,
và spec này từ chối chọn hộ.**

**C3 — Token dài hạn nằm trong tiến trình web.** `CLAUDE_CODE_OAUTH_TOKEN` là thông tin xác
thực sống lâu, giờ nằm trong môi trường của một tiến trình đang nghe HTTP. Bất kỳ đường nào
trong app làm lộ biến môi trường, hoặc chạy lệnh theo chữ người dùng gửi, là làm lộ token đó.
Cổng gác vẫn chỉ là loopback như `0001` — và ở `0001` cái bị lộ chỉ là một ô chat, còn ở đây
là một thông tin đăng nhập.

**C4 — Hạn mức bị tiêu mà không ai đếm.** Session tạo từ web tiêu hạn mức tài khoản. Không có
gì trong thiết kế này đếm hay chặn. Một vòng lặp hỏng trong app là một vòng lặp đốt hạn mức.

**C5 — Hai runtime trong một repo.** `.claude/CLAUDE.md:9` ghi `npm test` là lệnh chạy mọi
test. Sau unit này điều đó không còn đúng. Hoặc `npm test` gọi luôn phần Python, hoặc phải có
một lệnh khác đứng trên cả hai — nếu không, câu "tests must be green" mất nghĩa vì không ai
biết "tests" chỉ cái gì.

**C6 — Cám dỗ dựng kho dữ liệu thứ hai.** Đã kiểm: hàm đọc lịch sử của SDK trả về đủ nội dung
để dựng lại hội thoại. Thêm một kho riêng cho message là tạo nguồn sự thật thứ hai, và khi
hai nguồn lệch thì cái đúng là cái Claude thực sự đọc, không phải cái app lưu. Tiền lệ nằm
ngay trong repo này: `0001` đã ghi một transcript song song với thứ Claude Code vốn đã ghi.
Nó không vô ích — nó ghi cái file chính thức không có — nhưng nó là thứ phải biện minh, không
phải mặc định. **Muốn thêm kho, phải nêu được thứ session store không lưu.**

**C7 — `fork` và `resume` trông giống nhau và cho kết quả khác nhau.** SDK có chế độ mở lại
mà **tách nhánh sang định danh mới**. R3 đòi định danh khớp chính xác, nên nhánh đó phải
tắt. Bật nhầm thì mọi thứ vẫn chạy, chỉ có kết quả của unit là sai — và sai im lặng.

## Open questions

1. **Đã trả lời** (`intent.md` OQ2): lịch sử lấy từ session store của SDK, đã kiểm bằng dữ
   liệu thật của repo này. Không cần DB.
2. **Đã trả lời** (`intent.md` OQ4): session không sống sót theo nghĩa tiến trình; nó là
   transcript trên đĩa. "Mở lại" là dựng lại. Xem C1 cho hệ quả.
3. **Mới, và chặn C1:** hai tiến trình cùng mở lại một session thì transcript ra sao — hỏng,
   xen kẽ, hay cái sau ghi đè? Chưa kiểm. Plan phải kiểm trước khi cho phép mở lại bất kỳ
   session nào không do app tạo ra.
4. **Còn mở** (`intent.md` OQ1): lệnh nào chạy hết mọi test sau khi có Python? Xem C5.
5. **Còn mở** (`intent.md` OQ3): app giữ gì giữa các lần tải trang? Nếu trang lại chỉ là
   khung nhìn thì câu hỏi "reload mất sạch" quay lại nguyên vẹn ở tầng khác — khác `0001` ở
   chỗ lần này lịch sử đọc lại được từ đĩa, nên nó giải được, nhưng phải cố ý giải.
6. **Mới:** chọn chế độ duyệt tool nào (C2)? Đây là câu tác giả phải trả lời trước khi plan
   được viết, vì nó quyết định app có quyền gì trên máy.
