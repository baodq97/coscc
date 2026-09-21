# Intent: "Chat only, no tools" is not true of the sessions the app creates
Author: Bao Do. Status: accepted.

> Khung vấn đề do agent viết. Tác giả chỉ đạo hướng đi và bảo tự chốt phần còn lại. Ghi ra
> vì invariant 1 của `write-intent` đòi lời của người khởi xướng và nó không được thoả.

## Problem

Toàn bộ tư thế an toàn của app đứng trên một câu, viết ở `cos_baodo/config.py:44`:

> `# Knob 1. Empty means chat only — no tools at all, not even read.`

`.cos/0002_no-session-management/spec.md:95-99` gọi đó là quyết định đã chốt và là lý do
"mặc định phải chặt đến vậy". `.claude/CLAUDE.md` nói lại với người dùng rằng session là
"chat only — no tools". Câu đó **sai**.

Đo ngày **2026-09-21**: một session tạo bằng đúng `_options` của app, với `Config()` mặc
định (`effective_tools()` trả về `[]`), báo về **11 tool** trong thông điệp `init`:

```
mcp__claude_ai_Claude_Docs__{batch,create,delete,export,guide,query,read,update}
mcp__microsoft-learn__{microsoft_code_sample_search,microsoft_docs_fetch,microsoft_docs_search}
```

kèm `mcp_servers: [microsoft-learn (source: user), claude.ai Claude Docs (source: claudeai)]`.

Lý do thì đơn giản và nằm ngay trong tài liệu của CLI: `--tools` chỉ nói về **built-in
set**. MCP tool không phải built-in, nên `tools=[]` không hề chạm tới chúng. App đang trừ đi
một tập không chứa thứ nó muốn trừ. `effective_tools()` (`cos_baodo/config.py:64-72`) lọc
`WRITE_AND_EXEC_TOOLS` khỏi một danh sách rỗng — một phép trừ trên đúng tập sai.

Điều làm nó đáng sửa trước những việc khác đang xếp hàng: trong 11 tool đó có
`__create`, `__update`, `__delete`, `__export`. Đây không phải năng lực đọc. Và chúng đến từ
cấu hình **mức user của chính máy**, nên chúng có mặt mà không ai phải khai gì trong app,
không hiện ở đâu trong app, và không có knob nào của app tắt được.

**Con số 11 là của máy này, không phải của phần mềm.** Nó bằng số MCP tool mà cấu hình user
đang bật; máy khác sẽ ra số khác, có thể là 0 — và một máy ra 0 sẽ khiến mọi phép kiểm xanh
mà không chứng minh gì. Phép đo hôm nay chạy bằng một script tạm chưa nằm trong repo; đưa nó
vào và làm nó không phụ thuộc máy là việc đầu tiên, không phải việc cuối.

## Proposed outcome

Ngày **2026-09-21**, một session tạo bằng `Config()` mặc định báo về **0** tool trong
`init` — không built-in, không MCP, không nguồn nào khác — **và** phép kiểm đó tự nó đỏ được
khi đặt cạnh một session cố ý mở.

Hôm nay vế đầu **sai**: nó báo 11.

Số **0** lấy từ `cos_baodo/config.py:44`. Muốn đổi thì sửa câu ở đó trước, rồi mới sửa ở đây.

## Affected users and systems

- **Người chạy app trên máy có MCP server** — tức máy này. Họ đọc `.claude/CLAUDE.md`, thấy
  "chat only — no tools", và mở một cổng loopback tin rằng nó không làm gì được.
- **Những thứ ngoài máy mà MCP server chạm tới.** `claude.ai Claude Docs` có `create`,
  `update`, `delete`, `export`. Một session của app gọi được chúng hôm nay.
- **`cos_baodo/config.py`** — nơi câu sai được viết, và nơi `effective_tools()` trừ nhầm tập.
- **`cos_baodo/sessions.py`** — nơi options được dựng, gồm cả `setting_sources=None` mà chú
  thích đang mô tả ngược (`.cos/0006_sessions-invisible-across-processes/spec.md` C1).
- **`.cos/0002_no-session-management/spec.md:95-99` và `.claude/CLAUDE.md`** — hai chỗ đang
  nói với người đọc một điều không đúng. Chúng không được sửa lén: `0002` là bản ghi của lúc
  ấy, nên chỗ sửa là ở đây.
- **Không chạm:** `cos_baodo/store.py`, `cos_baodo/gitops.py`, lớp web. Không liên quan.

## Constraints

- **Mặc định phải thành đúng như đã hứa, không phải hứa lại cho khớp cái đang có.**
  `.cos/0002_no-session-management/spec.md:95-99` chốt chat-only là **quyết định**, không
  phải mô tả. Sửa câu chữ cho khớp hành vi là lật quyết định đó, và nếu làm thì phải làm ra
  mặt chứ không phải bằng một lần sửa comment.
- **Knob 1 vẫn phải bật được.** Khai tool thì session phải có tool. Một bản sửa khoá chặt tới
  mức không mở lại được là hỏng theo kiểu khác.
- **Phép kiểm không được phụ thuộc cấu hình MCP của máy.** Trên máy không MCP server nào,
  "0 tool" đúng sẵn và mọi thứ xanh mà không chứng minh gì. Phép kiểm phải tự dựng được
  trường hợp có tool rồi cho thấy nó bị chặn — `0004` đã dạy đúng bài này.
- **Bốn lệnh chứng minh cũ phải còn xanh** nguyên trạng: `verify_0002`, `verify_0003`,
  `verify_0004`, `verify_0005`.
- **`npm test` không cần trình duyệt.**
- **Không nới `bypass_permissions` hay knob 2 để bù.** Chúng là hàng rào khác.

## Open questions

1. Chặn bằng gì? `--strict-mcp-config` và `setting_sources=[]` là hai thứ đáng thử, nhưng
   chưa cái nào được kiểm, và chúng chặn hai thứ khác nhau.
2. **Chặn settings thì `CLAUDE.md` cũng thôi được nạp** — và điều đó **kéo lùi `0006`**. Cái
   hại `0006` chặn là `pull` đổi file session đang đọc; nếu session thôi đọc `CLAUDE.md`
   trong workspace thì `0006` mất đúng cái vừa đo được là thật. Hai unit này chạm nhau, và
   thứ tự làm sẽ quyết định cái nào đúng.
3. Session **có nên** đọc `CLAUDE.md` của workspace không? Nó hữu ích — session biết luật của
   repo. Nó cũng là chữ từ một repo vừa `clone` về đi thẳng vào ngữ cảnh. Chưa ai quyết.
4. `.mcp.json` trong workspace có gắn được server vào session không? Đo hôm nay ra
   `status: failed, source: project` — nó **được đọc và được thử**, nhưng server dùng để đo
   không tự chạy được nên không kết luận được. Nếu mở thì một repo `clone` về tự khai được
   năng lực, và đó là một vấn đề khác hẳn.
5. Có nên có knob riêng cho MCP không, hay MCP nằm chung knob 1? Chung thì tên tool MCP dài
   và người dùng phải biết chúng; riêng thì thêm một knob nữa vào bốn cái đã có.
6. Còn nguồn năng lực nào nữa chưa nhìn? Đo hôm nay thấy `source: user` và `source: claudeai`
   — cái thứ hai không đến từ file nào trên máy. Danh sách nguồn chưa ai liệt kê hết.
