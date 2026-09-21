# Spec: Make the promise true, with the one lever that does not cost CLAUDE.md
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

### A. Lời hứa thành đúng

**R1 — Session mặc định không có tool nào.** Tạo bằng `Config()` mặc định thì thông điệp
`init` báo `tools` **rỗng** và `mcp_servers` **rỗng**. Kiểm được: R7.

**R2 — Chặn phải phủ cả ba nguồn đã đo.** Đo ngày 2026-09-21 thấy tool tới từ `source: user`,
`source: project` (file `.mcp.json` trong chính workspace) và `source: claudeai`. Không nguồn
nào được sót. Kiểm được: dựng một workspace có `.mcp.json`, tạo session, `mcp_servers` rỗng —
không có cả mục `failed`, vì một mục `failed` nghĩa là cấu hình vẫn được đọc và vẫn được thử.

**R3 — Knob 1 vẫn mở được.** Khai một built-in tool thì session có nó. Kiểm được: `Config`
khai một tool, `init` báo đúng tool đó.

**R4 — `CLAUDE.md` của workspace vẫn được nạp.** Đây là ràng buộc chứ không phải tiện ích:
`.cos/0006_sessions-invisible-across-processes/spec.md` C1 vừa đo được rằng session đọc file
này, và toàn bộ lý do tồn tại của `0006` đứng trên đó. Một bản sửa làm `CLAUDE.md` thôi được
nạp sẽ lặng lẽ rút chân `0006`. Kiểm được: workspace có `CLAUDE.md` chứa một từ khoá, session
hỏi ra đúng từ khoá đó.

**R5 — Câu ở `cos_baodo/config.py:44` được làm cho đúng, không được viết lại cho khớp.**
`.cos/0002_no-session-management/spec.md:95-99` chốt chat-only là **quyết định**. Quyết định
ấy không sai; phần chưa làm được là code. Nên `0002` **không sửa gì** — nó ghi đúng thứ đã
quyết. Kiểm được: không commit nào trong unit này đổi file dưới `.cos/0002_*`.

**R6 — `effective_tools()` nói đúng phạm vi của nó.** `cos_baodo/config.py:64-72` trừ
`WRITE_AND_EXEC_TOOLS` khỏi danh sách tool, và phép trừ ấy chỉ có nghĩa trong **tập
built-in**. Hôm nay docstring không nói vậy, nên nó đọc như một hàng rào tổng quát. Kiểm
được: docstring nêu rõ nó không chạm MCP, và nói chỗ nào chạm.

### B. Phép kiểm phải tự đứng được

**R7 — Một lệnh chứng minh, và nó có đối chứng dương không phụ thuộc máy.** Thoát 0 chỉ khi
cả hai đúng, in cả hai kể cả khi hỏng:

1. Session mặc định: `tools` rỗng, `mcp_servers` rỗng.
2. Một session **cố ý mở** trong cùng lệnh: có ít nhất **1** tool. Server dùng cho vế này do
   chính lệnh kiểm cung cấp, không lấy từ cấu hình của máy.

Vế 2 là bắt buộc, không phải trang trí. Trên máy không có MCP server nào, vế 1 đúng sẵn và
lệnh kiểm sẽ xanh mà không chứng minh gì — đúng cái bẫy `0004` đã dựng ra để tránh.

**R8 — Không khẳng định con số "trước khi sửa".** Đo ba lần trong cùng một ngày ra **11**,
**3** và **8** tool, vì trạng thái kết nối của từng server đổi giữa các lần. Bằng chứng của
lỗi là **khác rỗng**, không phải một con số. Kiểm được: không phép kiểm nào so với một số cố
định lớn hơn 0.

### C. Không làm đổ thứ có sẵn

**R9 — Bốn lệnh chứng minh cũ còn xanh** nguyên trạng: `scripts/verify_0002.py`,
`scripts/verify_0003.py`, `scripts/verify_0004.py`, `scripts/verify_0005.py`.

**R10 — `npm test` không cần trình duyệt.**

**R11 — `.claude/CLAUDE.md` nói đúng điều app làm**, gồm cả việc MCP bị chặn và bằng cách
nào — vì câu "chat only" ở đó là thứ người dùng đọc trước khi mở một cổng.

## Design

**Một cần gạt, và nó được chọn bằng phép đo chứ không bằng lý lẽ.** Bốn biến thể của đúng
`_options` mà app đang dùng, mỗi cái chạy trong một workspace tạm có `CLAUDE.md` mang từ
khoá, ngày 2026-09-21:

| Biến thể | `tools` | `CLAUDE.md` được đọc |
|---|---|---|
| như đang ship | 3 | có |
| `strict_mcp_config=True` | **0** | **có** |
| `setting_sources=[]` | 0 | **không** |
| cả hai | 0 | không |

`strict_mcp_config=True` là hàng duy nhất đạt cả R1 lẫn R4. `setting_sources=[]` cũng đóng
được lỗ, nhưng nó tắt luôn `CLAUDE.md` — tức trả tiền bằng đúng thứ `0006` vừa dựa vào.
`intent.md` open question 2 lo rằng hai unit này sẽ đá nhau; phép đo cho thấy chúng **không**,
miễn là chọn đúng cần gạt.

**Vì sao `tools=[]` không đủ, nói một lần cho rõ.** `--tools` của CLI mô tả chính nó là "the
list of available tools **from the built-in set**". MCP tool không nằm trong tập đó, nên
`tools=[]` là một phép trừ trên tập không chứa thứ cần trừ. Đây không phải lỗi logic ở
`cos_baodo/config.py:64-72` — phép trừ ấy đúng với thứ nó nhắm tới; lỗi là không ai kiểm xem
tập nền có phải tập duy nhất không.

**Hai cổng vào năng lực, và unit này chỉ đóng một.**

| Cổng | Đi vào | Sau unit này |
|---|---|---|
| MCP server | user config, `.mcp.json` của workspace, claudeai | đóng — `mcp_servers` rỗng |
| settings và `CLAUDE.md` của workspace | chữ, vào thẳng ngữ cảnh | **vẫn mở**, cố ý — xem C5 |

**Ranh giới:** chỉ nơi dựng options cho SDK đổi. Lớp dịch vụ, lớp web, store, git không biết
gì về chuyện này và không được biết. Cấu hình vẫn chỉ đọc ở một chỗ
(`.cos/0002_no-session-management/spec.md:146` C8).

**Không thêm knob cho MCP.** Bật MCP lại là một quyết định về năng lực, không phải một ô
cấu hình; nó cần intent riêng. Hệ quả là hôm nay muốn bật phải sửa code — cố ý, và xem C4.

## Out of scope

- **Chặn `CLAUDE.md` hay `.claude/settings.json` của workspace.** R4 đòi ngược lại. Xem C5.
- **Knob bật/tắt MCP.** Cần intent riêng.
- **Liệt kê đủ mọi nguồn năng lực.** Ba nguồn đã đo thì đóng; chưa ai chứng minh là hết. C7.
- **Sửa `.cos/0002_*`.** R5.
- **`0006`.** Nó đỗ ở `write-plan` và unit này không đụng vào, ngoài việc giữ R4 cho nó.
- **Duyệt quyền từng lần gọi tool** (`can_use_tool`, hook `PreToolUse`). Hàng rào khác, đáng
  xét khi nào MCP được bật lại.

## Concerns

**C1 — Chặn đúng cũng là mất thật.** `claude.ai Claude Docs` và `microsoft-learn` là những
thứ người dùng có thể đang muốn. Sau unit này session của app không gọi được chúng, kể cả khi
người dùng thấy chúng hoạt động bình thường ở terminal. Đây là mặc định đúng theo
`.cos/0002_no-session-management/spec.md:95-99`, nhưng nó **lấy đi một năng lực đang có**,
không phải chỉ bịt một lỗ lý thuyết. **Người quyết là tác giả** nếu muốn một knob thay vì
một bức tường.

**C2 — Con số trong `intent.md` không lặp lại được, và `intent.md` sẽ đứng nguyên như thế.**
Nó ghi 11 tool. Đo lại trong cùng ngày ra 3 rồi 8. Không cái nào sai: số phụ thuộc server nào
đang `connected` lúc ấy. Bài học là con số ấy lẽ ra phải được ghi kèm điều kiện ngay từ đầu.
R8 tồn tại để phép kiểm không thừa hưởng lỗi đó; `intent.md` không sửa, vì nó là bản ghi của
lúc ấy và đã tự cảnh báo rằng con số là của máy này.

**C3 — Đường `project` được chứng minh là *được đọc*, không phải là *chạy được*.** Đo thấy
`probe(project,failed)`: workspace tự khai được server và CLI có thử. Nhưng server dùng để đo
không tự khởi động được khi chạy riêng, nên **chưa ai thấy** một server của workspace gắn
thành công vào session. R2 chặn cả trường hợp ấy, nên bản sửa không phụ thuộc câu trả lời —
nhưng đừng đọc unit này thành "đã chứng minh repo clone về cướp được quyền".

**C4 — Bức tường không có cửa.** Không knob nghĩa là người muốn MCP phải sửa code, và người
sửa code sẽ xoá đúng một dòng — đúng cái dòng đang là hàng rào. Không có gì ngăn điều đó, và
không có test nào đỏ lên nếu ai đó xoá nó trừ lệnh chứng minh của unit này. Đó là lý do R7 vế
1 phải nằm trong một lệnh có người chạy, chứ không chỉ là một dòng comment.

**C5 — Cổng còn lại rộng hơn cổng vừa đóng, và unit này không đóng nó.** `CLAUDE.md` và
`.claude/settings.json` của workspace vẫn đi thẳng vào ngữ cảnh session — đã đo, bằng từ
khoá. Một repo vừa `clone` về (`0003` cho phép clone từ URL bất kỳ) mang theo chữ của người
lạ, và chữ ấy được model đọc như hướng dẫn. Không tool nào bị gọi, nên nó không phải cùng một
lỗ; nhưng nó là bề mặt lớn hơn. **Người quyết là tác giả** — nó đáng một unit riêng và R4 cố
ý giữ cửa ấy mở cho tới lúc đó.

**C6 — Lệnh chứng minh tạo session thật.** Hai session mỗi lần chạy, prompt ngắn nhất có
thể. Không vòng lặp không người trông.

**C7 — Ba nguồn là ba nguồn đã thấy, không phải mọi nguồn.** `source: claudeai` không đến từ
file nào trên máy này, nên danh sách nguồn không đoán được từ đĩa. `strict_mcp_config` chặn
theo nguyên tắc "chỉ nhận cái được truyền vào", nên nó **nên** phủ cả nguồn chưa biết — đó là
lý lẽ, không phải phép đo, và nó chỉ đo được bằng cách gặp một nguồn mới.

**C8 — Một chỗ dựng options, và không gì bắt nó ở yên như vậy.** Hàng rào là một tham số tại
một lời gọi. Một lời gọi thứ hai ở đâu đó sẽ không có nó và sẽ không làm gì đỏ. Cùng họ với
`.cos/0003_no-workspace-management/spec.md:57` R10, và cùng cách chữa: một phép kiểm khẳng
định chỉ có một chỗ.

## Open questions

1. **Đã trả lời** (`intent.md` OQ1): `strict_mcp_config=True`. Bảng đo ở `## Design`.
2. **Đã trả lời** (OQ2): không có mâu thuẫn. Đo cho thấy `strict_mcp_config` giữ nguyên
   `CLAUDE.md`, nên `0006` không bị rút chân. Đây là lý do duy nhất để không chọn
   `setting_sources=[]`.
3. **Còn mở** (OQ3, và là C5): session **có nên** đọc `CLAUDE.md` của workspace không.
4. **Trả lời một nửa** (OQ4, và là C3): `.mcp.json` của workspace được đọc và được thử; chưa
   thấy nó gắn thành công. Bản sửa phủ cả hai khả năng.
5. **Đã trả lời** (OQ5): không knob. Cần intent riêng; hệ quả ở C4.
6. **Còn mở** (OQ6, và là C7): danh sách nguồn năng lực chưa ai liệt kê hết.
7. **Mới:** sau unit này, có gì cho người dùng **thấy** session đang có tool nào không? Hôm
   nay câu trả lời nằm trong `init` mà không ai hiển thị. Một hàng rào không nhìn thấy được
   là một hàng rào không ai kiểm.
