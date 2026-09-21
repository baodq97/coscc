# Plan: A localhost web channel into a running Claude Code session
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

Concern C1 trong `spec.md` đã được tác giả quyết: **tự viết, không dùng `fakechat`**, lý do
là cần chỗ để tích hợp sâu về sau. `fakechat` là một tab chat rồi hết, không có bề mặt để
cắm thêm. Ghi lại ở đây để C1 không phải mở lại.

## Files that change

| Path | |
|---|---|
| `package.json` | (new) khai báo `@modelcontextprotocol/sdk`, và script test |
| `.mcp.json` | (new) đăng ký channel server với Claude Code |
| `channel/server.mjs` | (new) tiến trình hai mặt: MCP qua stdio, HTTP trên loopback |
| `channel/transcript.mjs` | (new) ghi nối tiếp, tách riêng để test được không cần mạng |
| `channel/transcript.test.mjs` | (new) test cho phần trên |
| `channel/public/index.html` | (new) trang web: ô nhập, khung hội thoại, luồng nhận |
| `scripts/verify-0001.mjs` | (new) lệnh quyết định đạt hay không, dùng ở `## Proof` |
| `evidence/0001_terminal-only-access/transcript.jsonl` | (new) sinh ra khi chạy thật, được commit làm bằng chứng |
| `.claude/CLAUDE.md` | có thật, 36 dòng — thêm lệnh build/test/lint vào `## Commands` |

Chọn **Node** chứ không phải Bun, dù ví dụ trong tài liệu dùng Bun: repo này đã chạy
`node --test` (`.claude/CLAUDE.md:9`) và thêm runtime thứ hai là thêm một thứ phải cài trên
mọi máy về sau. Viết `.mjs` thuần, không bước build, khớp với `.claude/scripts/*.mjs`.

Transcript **không** nằm trong `.cos/0001_terminal-only-access/` vì `.claude/CLAUDE.md:20-21`
bắt thư mục đó chỉ chứa ba artifact. Nó nằm trong `evidence/` và được commit, để sau này còn
trích dẫn được theo invariant "chỉ trích file đã commit trong repo".

Cổng **8789**. `fakechat` mặc định 8787 và ví dụ trong tài liệu dùng 8788; tránh cả hai để
còn chạy song song mà so sánh (`spec.md` open question 5).

## Order of work

1. **Dựng nền.** Tạo `package.json`, cài `@modelcontextprotocol/sdk`.
   Kiểm: `node -e "import('@modelcontextprotocol/sdk/server/index.js').then(()=>console.log('ok'))"`
   in ra `ok`.

2. **Spike hợp đồng, một chiều, trước mọi thứ khác.** `spec.md` bắt buộc bước này: toàn bộ
   thiết kế tựa lên một hợp đồng research-preview mà không file nào trong repo xác minh được.
   Viết bản tối thiểu của `channel/server.mjs` chỉ làm một việc — nhận POST, đẩy
   notification — rồi đăng ký trong `.mcp.json` và mở session bằng cờ dev.
   Kiểm: `curl -X POST localhost:8789 -d 'spike'` và thẻ `<channel>` chứa `spike` xuất hiện
   trong session. **Nếu bước này hỏng, dừng lại và sửa `spec.md`, đừng đi tiếp.**

   **Lệch so với dự kiến, ghi lại ngày 2026-09-21:** bước này **không chạy được bằng agent**.
   Ở chế độ `--print`, với cả `--channels` lẫn cờ dev, debug log không hề có dòng đăng ký
   channel nào — không "registered", không "skipped", không cả lỗi allowlist — trong khi
   server vẫn kết nối MCP bình thường và khai báo đúng `experimental: {"claude/channel": {}}`
   (kiểm trực tiếp bằng một lời gọi `initialize`). Channel dường như chỉ sống trong session
   tương tác. Vì vậy bước 2 phải do con người chạy trong một session thật, và mọi bước sau
   phụ thuộc vào nó cũng vậy.

   **Lệch thứ hai, 2026-09-21:** bước 3 và bước 6 được làm trước khi bước 2 xanh. Cả hai
   chỉ đọc và ghi file, không chạm gì vào hợp đồng channel, nên chúng không thể bị bước 2
   làm sai. Lệnh dừng gắn ở bước 2 áp dụng cho trường hợp hợp đồng **hỏng**; hiện nó đang
   **chờ người**, không phải hỏng. Nếu bước 2 hỏng thật thì hai bước này vẫn đúng, chỉ là
   đứng chờ một thiết kế khác.

3. **Transcript trước giao diện.** Viết `channel/transcript.mjs`: nối tiếp một bản ghi JSON
   mỗi dòng, gồm thời điểm ISO-8601, hướng (`in`/`out`), định danh session, nội dung, và
   trạng thái giao (`written` khi đã ghi ra transport, `delivered` khi session đã phản hồi).
   Viết `channel/transcript.test.mjs` cùng lúc.
   Kiểm: `node --test` xanh, và file sống qua nhiều lần mở.

4. **Đường ra.** Thêm `tools` capability, đăng ký tool `reply`, và chuỗi `instructions` nói
   cho Claude biết gọi tool nào. Nối tool vào một luồng đẩy một chiều xuống trình duyệt.
   Kiểm: `curl -N localhost:8789/events` nhận được chữ khi Claude gọi `reply`.

5. **Trang web.** `channel/public/index.html`: ô nhập gửi POST, khung hội thoại nghe luồng
   ở bước 4.
   Kiểm: mở `http://127.0.0.1:8789`, gõ một câu, thấy câu trả lời hiện ra mà không reload;
   `ss -ltn` cho thấy `127.0.0.1:8789`, không phải `0.0.0.0`.

6. **Lệnh chứng minh.** Viết `scripts/verify-0001.mjs` theo `## Proof`.
   Kiểm: chạy trên transcript rỗng thì thoát khác 0, kèm lý do.

7. **Phiên đo thật.** Chạy một session, trao đổi ít nhất 10 lượt qua trang web, commit
   `evidence/0001_terminal-only-access/transcript.jsonl`.
   Kiểm: `## Proof` thoát 0.

8. **Đóng đơn vị công việc.** Thêm lệnh build/test/lint vào `## Commands` trong
   `.claude/CLAUDE.md` — `intent.md` giao việc này cho unit 0001. Đặt `Status: done` trong
   file này, chỉ sau khi bước 7 đã xanh.

## Risks

**Hợp đồng có thể không đúng như đã đọc.** Đây là rủi ro tôi muốn không phải viết ra, và nó
là rủi ro thật: bước 3 đến 8 đều tựa lên bước 2, và bước 2 tựa lên tài liệu cùng một binary
nằm ngoài repo. Hỏng ở đây thì hỏng toàn bộ, đúng vào ngày hạn. Dấu hiệu: không thẻ
`<channel>` nào xuất hiện, hoặc `/mcp` trong session báo server `failed`. Đây là lý do bước 2
đứng trước mọi thứ và có lệnh dừng gắn vào nó.

**Loopback là toàn bộ lớp gác.** Mọi tiến trình trên máy, kể cả một tab trình duyệt đang mở
trang khác, đều POST được vào cổng này và đặt chữ trước mặt một agent có quyền chạy lệnh và
sửa file (`spec.md` C6). Dấu hiệu: bản ghi hướng vào trong transcript mà bạn không gõ. Không
có biện pháp nào trong lát cắt này ngoài việc cổng chỉ nghe loopback.

**Sự kiện rơi im lặng.** Notification không được ack; nếu session khởi động thiếu cờ channel
thì sự kiện bị bỏ và server không nhận lỗi (`spec.md` C4). Dấu hiệu: bản ghi đứng mãi ở
`written` mà không bao giờ lên `delivered`. Đây là lý do trạng thái giao nằm trong transcript
ngay từ bước 3, không phải thêm vào sau.

**Cờ dev mỗi lần khởi động.** Channel tự viết không nằm trong allowlist, nên mỗi session đều
qua một hộp thoại toàn màn hình. Dấu hiệu: bạn ngừng dùng nó sau vài ngày — tức vấn đề trong
`intent.md` chưa được giải dù con số 10 đã đạt.

**Không tự kiểm được từ trong session.** Channel không đăng ký ở chế độ headless (xem lệch
ghi ở bước 2), nên không có cách nào chạy `## Proof` đầu-cuối bằng một lệnh không người
trông. Dấu hiệu: đã thấy rồi — hai lần chạy `-p` đều im lặng. Hệ quả: phần chứng minh của
đơn vị công việc này buộc phải có người ngồi đó, và điều đó cần được nhớ khi sau này muốn tự
động hoá.

**Node đi đường ít người đi hơn.** Ví dụ chính thức viết bằng Bun. SDK không phụ thuộc
runtime, nhưng phần HTTP và luồng đẩy thì có. Dấu hiệu: bước 4 hoặc 5 sa lầy quá lâu. Lối
thoát: Bun đã có sẵn trên máy (1.3.14), đổi runtime cho riêng `channel/` và ghi lại vì sao.

## Proof

```
node --test 'channel/*.test.mjs' '.claude/scripts/*.test.mjs' \
  && node scripts/verify-0001.mjs evidence/0001_terminal-only-access/transcript.jsonl
```

Đạt khi cả hai cùng thoát 0. Vế sau thoát 0 **chỉ khi** transcript chứa ít nhất 10 bản ghi
hướng vào, tất cả mang cùng một định danh session, và mỗi bản ghi đó có trạng thái
`delivered` chứ không phải chỉ `written`. Thoát khác 0 thì in ra thiếu điều kiện nào.

Con số 10 và cách đếm lấy nguyên từ `intent.md` và `spec.md` C5: một lượt là một bản ghi
hướng vào. Muốn đổi cách đếm thì sửa `intent.md`, không sửa lệnh này.

## What this plan does not do

- **Không dùng `fakechat`**, kể cả để so sánh — bước 6 của `spec.md` open question 5 chỉ giữ
  cổng khác biệt để sau này còn chạy song song nếu cần.
- **Không làm permission relay**, dù hợp đồng có hỗ trợ. Nó mở một đường ghi từ trang web vào
  quyết định của session; `spec.md` để nó ngoài phạm vi có chủ ý.
- **Không xác thực, không phiên đăng nhập, không TLS.** `intent.md` khoá ở localhost một
  người dùng.
- **Không sửa `README.md`**, dù dòng 3-4 của nó vẫn mô tả luật cũ "accepted by a human". Đó
  là drift do việc đổi luật sinh ra, không thuộc đơn vị công việc này, và sửa nó ở đây sẽ
  trộn hai thay đổi không liên quan vào một commit.
