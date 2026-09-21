# Intent: Terminal-only access to Claude Code sessions
Author: Bao Do. Status: accepted.

## Problem

Tác giả làm việc song song trên nhiều project, và đường duy nhất để nói chuyện với một
session Claude Code đang chạy là cái terminal đã khởi động nó, trên đúng máy đó. Rời khỏi
terminal là mất session: không xem được nó đang làm gì, không gửi thêm được prompt, không
tiếp tục được mạch việc đang dở.

Hệ quả là mọi việc phải diễn ra trong terminal, kể cả những việc mà terminal không phải chỗ
tốt để làm. Và vì không có bề mặt nào khác ngoài terminal, cũng không có chỗ để về sau gắn
thêm bất cứ thứ gì — chuyển qua lại giữa các workspace, xem output dài, duyệt một tool call.

Repo này hiện chưa có application code (`.claude/CLAUDE.md:16`), nên đây là đơn vị công việc
đầu tiên sinh ra code chạy được.

## Proposed outcome

Đến hết ngày **2026-09-22**, tồn tại transcript của **một** session Claude Code duy nhất,
chứa **ít nhất 10 lượt** prompt được gửi từ một web page chạy ở localhost và phản hồi tương
ứng của chính session đó.

Kết quả này sai nếu đến hết ngày đó không có transcript nào như vậy, nếu số lượt dưới 10,
hoặc nếu 10 lượt phải trải trên nhiều session mới đủ.

Hai điều được cố ý để ngoài phần đo:

- **Việc khởi động session trong terminal không tính là một lượt.** Khởi động là thiết lập,
  không phải vòng làm việc.
- **"Không phải quay lại terminal" không nằm trong phần đo.** Đó là ý định đằng sau con số,
  nhưng một transcript chỉ chứng minh được cái đã xảy ra qua web page; nó không chứng minh
  được cái không xảy ra ở nơi khác. Đưa một mệnh đề không kiểm chứng được vào thước đo thì
  mất luôn thước đo.

## Affected users and systems

- **Người dùng:** duy nhất tác giả. Không có người dùng thứ hai, không có khái niệm tài
  khoản hay phân quyền trong phạm vi này.
- **Máy:** một máy cá nhân. Web page và session Claude Code nằm trên cùng máy đó.
- **Session Claude Code đang chạy:** thứ bị tác động trực tiếp — nó phải nhận được prompt
  từ một nguồn không phải terminal của chính nó.
- **Repo `cos-baodo`:** chưa có build, test, lint command cho application code
  (`.claude/CLAUDE.md:15-16`); việc bổ sung chúng thuộc về đơn vị công việc này khi code
  xuất hiện.
- **Không bị tác động:** mạng LAN, internet, bất kỳ hạ tầng hosted nào.

## Constraints

- **Không dùng `claude-agent-sdk`.** Ràng buộc cứng do tác giả đặt. Lý do: phải đi bằng cơ
  chế channel/plugin sẵn có của Claude Code, tức nói chuyện với đúng session đang chạy, chứ
  không phải tạo ra một phiên riêng bên cạnh nó.
- **Chỉ localhost.** Không bind ra LAN, không ra internet. Mọi thứ về TLS, auth mạng, tài
  khoản đều nằm ngoài phạm vi.
- **Một lát cắt, không hơn:** text hai chiều với đúng một session. Chọn giữa nhiều
  workspace, trả lời permission prompt, render diff/file/output dài — đều không thuộc intent
  này. Chúng là intent sau.
- **Hạn 2026-09-22**, một ngày kể từ khi intent được viết. Tác giả đã được cảnh báo rằng hạn
  này chật và vẫn giữ nó.
- **Nếu đến hạn mà kết quả sai thì hạn là thứ được nới, không phải phạm vi.** Chốt trước để
  sau này không nới phạm vi nhằm cứu con số — nới phạm vi là mất thước đo.
- **Channel tự viết không nằm trong allowlist của Claude Code**, nên phải nạp bằng cờ dành
  cho phát triển và chịu một hộp thoại xác nhận mỗi lần khởi động session. Điều này chấp
  nhận được với một người dùng trên localhost, nhưng nó là chi phí thường trực chứ không
  phải chuyện một lần. **Unverifiable:** thông tin này đọc từ CLI đã cài trên máy tác giả,
  không phải từ file commit trong repo, nên theo invariant 5 nó không được trích dẫn làm
  nguồn. Spec phải tự xác minh lại bằng cách chạy thật.

## Open questions

1. Cơ chế channel của Claude Code cho phép một transport tự viết tới mức nào? Chưa có nguồn
   nào trong repo này xác minh được điều đó; spec phải tự xác minh trước khi thiết kế.
2. Plugin Telegram chính thức được nêu làm tiền lệ trong lúc thảo luận. Nội dung của nó nằm
   ngoài repo này, nên theo invariant 5 nó **unverifiable** ở đây và không được trích dẫn như
   một nguồn. Mọi điều rút ra từ nó phải được kiểm lại độc lập.
3. Transcript được sinh ra từ đâu và ai giữ nó? Kết quả ở trên đứng hoặc đổ theo đúng file
   này, nên spec phải chỉ định nó là gì và nó được ghi lúc nào, trước khi viết dòng code
   đầu tiên.
4. Hộp thoại xác nhận lúc khởi động có chặn việc dùng hằng ngày tới mức nào? Nếu mỗi lần mở
   máy đều phải bấm qua nó, chi phí đó có thể lớn hơn cái tiện mà web page mang lại — và
   nếu vậy thì vấn đề ở đầu file này chưa được giải, dù con số 10 có đạt.
