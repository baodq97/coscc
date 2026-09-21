# Intent: No way to manage sessions from the web page
Author: Bao Do. Status: accepted.

## Problem

Trang web dựng ở `0001` chỉ nói được với **một** session Claude Code đã được mở sẵn trong
terminal, và chỉ session đó. Nó không liệt kê được những session nào đang tồn tại, không mở
được session mới, không mở lại được session cũ. Định danh session được sinh ra trong chính
tiến trình channel (`channel/server.mjs:22`), nên nó chỉ biết đúng session đã spawn ra nó và
không có khái niệm nào về những session khác.

Hệ quả: nó là một ô chat gắn vào một terminal, không phải chỗ làm việc. Muốn đổi sang
project khác thì phải quay lại terminal, mở session mới, khởi động lại channel — tức đúng
cái việc mà `0001` sinh ra để khỏi phải làm. `0001` giải được "với tới session đang chạy";
nó không giải được "làm việc với nhiều project", và đó vẫn là vấn đề gốc.

Đây cũng là chỗ ràng buộc cứng của `0001` — không dùng `claude-agent-sdk`
(`.cos/0001_terminal-only-access/intent.md:50`) — hết tác dụng. Ràng buộc ấy đúng cho việc
nối vào một session có sẵn, vì SDK không làm được điều đó. Nhưng nó cũng chính là thứ chặn
việc quản lý session, vì cơ chế channel không có API nào cho việc đó.

## Proposed outcome

Đến hết ngày **2026-09-24**, một lệnh chạy được **không cần trình duyệt** chứng minh rằng,
qua web app, với **2 project khác nhau**: mỗi project tạo được một session mới, gửi được một
prompt và nhận được phản hồi, rồi **mở lại đúng session đó** với lịch sử còn nguyên —
`session_id` ở lần mở lại khớp `session_id` lúc tạo.

Kết quả này sai nếu đến hết ngày đó lệnh ấy không tồn tại, hoặc nó chạy nhưng thất bại ở bất
kỳ mắt nào: không liệt kê được 2 project, không tạo được session, không nhận được phản hồi,
hoặc mở lại ra một `session_id` khác.

Phép đo cố ý **không cần trình duyệt**. Bài học đắt nhất của `0001` là phần chứng minh của nó
buộc phải có người ngồi đó, và điều đó làm mọi vòng lặp chậm lại.

## Affected users and systems

- **Người dùng:** duy nhất tác giả. Không phân phối cho ai khác — xem `## Constraints`, ranh
  giới này có hệ quả pháp lý chứ không chỉ là phạm vi.
- **Máy:** một máy cá nhân. Web app và các session nằm cùng máy.
- **Nhiều project:** ít nhất hai thư mục repo khác nhau, mỗi cái là một workspace.
- **`channel/` của `0001`:** bị ngừng sử dụng, **không bị xoá**. Xem `## Constraints`.
- **Repo `cos-baodo`:** hiện thuần Node (`.claude/CLAUDE.md:9`). Đơn vị công việc này đưa
  Python vào.

## Constraints

- **Xác thực bằng `CLAUDE_CODE_OAUTH_TOKEN`**, sinh bởi `claude setup-token`, để chạy trong
  hạn mức của tài khoản sẵn có thay vì tính token theo API. Tài liệu Agent SDK ghi nguyên
  văn: *"Unless previously approved, Anthropic does not allow third party developers to offer
  claude.ai login or rate limits for their products, including agents built on the Claude
  Agent SDK."* Cơ sở để vẫn đi đường này: câu đó cấm **phân phối sản phẩm cho người khác**,
  còn đây là công cụ tác giả tự dùng trên máy mình. **Ranh giới đó là một phần của intent
  này:** khoảnh khắc thứ này được mở cho người khác dùng, ràng buộc bị vi phạm và phải
  chuyển sang API key. **Unverifiable:** nguồn nằm ngoài repo.
- **Mất đường nối vào session terminal.** Agent SDK tự spawn tiến trình CLI của nó; nó không
  gắn vào session bạn đang mở sẵn. Đây là đánh đổi đã biết và đã chấp nhận, không phải thiếu
  sót phát hiện sau.
- **Không xoá `channel/`.** `scripts/verify-0001.mjs:9` import `channel/transcript.mjs`; xoá
  là mất lệnh duy nhất chứng minh `0001` từng đạt. Đánh dấu retired thì được, gỡ thì không.
- **Không xoá `evidence/0001_terminal-only-access/`** vì cùng lý do.
- **Phạm vi:** liệt kê, tạo, mở lại session qua web. Giao diện đẹp, render diff, duyệt
  permission, chạy nhiều session song song trong một màn hình — đều là intent sau.

## Open questions

1. Python vào một repo thuần Node thì `npm test` ở `.claude/CLAUDE.md:9` còn là một lệnh
   chạy hết mọi thứ nữa không? Nếu không, phải có một lệnh khác làm việc đó, nếu không thì
   "tests must be green" mất nghĩa vì không ai biết "tests" là gì.
2. Lịch sử lấy từ `get_session_messages` của SDK hay từ file session trên đĩa? Cái đầu là API
   có tài liệu; chưa kiểm nó trả về gì và có đủ để dựng lại hội thoại không.
3. Web app giữ trạng thái gì giữa các lần tải? Nếu nó cũng chỉ là khung nhìn như `0001` thì
   câu hỏi "reload mất sạch" quay lại nguyên vẹn, chỉ là ở tầng khác.
4. Một session do web app tạo ra có sống sót khi tắt web app không, hay chết theo tiến trình
   cha? Câu trả lời quyết định "mở lại" nghĩa là gì — nối lại vào thứ còn sống, hay dựng lại
   từ đĩa.
