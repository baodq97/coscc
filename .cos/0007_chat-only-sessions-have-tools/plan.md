# Plan: Set the one flag, then take away the test that said it was already fine
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

Bản sửa là **một tham số**. Phần còn lại của unit này là dựng thứ lẽ ra phải tồn tại để lỗi
không sống được ba unit: một phép đo chạy được, và việc gỡ một test đang khẳng định điều
không đúng. Thứ tự vì thế đặt phép đo trước, giống `0005`.

## Files that change

| Path | |
|---|---|
| `scripts/verify_0007.py` | (new) hai mệnh đề của `spec.md` R7 |
| `cos_baodo/sessions.py` | có thật, 264 dòng — `_options` ở `:109-127`; thêm cần gạt, sửa chú thích sai ở `:126` |
| `cos_baodo/sessions_test.py` | có thật, 223 dòng — `:128-131` là test phải gỡ |
| `cos_baodo/config.py` | có thật, 132 dòng — `:44` và docstring `effective_tools` `:64-72` |
| `.claude/CLAUDE.md` | có thật, 117 dòng — `spec.md` R11 |

**Không đụng tới** `.cos/0002_*` (`spec.md` R5), `cos_baodo/store.py`, `cos_baodo/gitops.py`,
`cos_baodo/service.py`, `cos_baodo/api.py`, lớp trang. Không file nào trong số đó biết tới
tool, và không file nào cần biết.

`package.json` không đổi: `npm test` đã quét `cos_baodo/*_test.py`, nên test mới tự được
nhặt.

## Order of work

1. **Phép đo trước, và xác nhận nó đỏ.** `scripts/verify_0007.py` với hai mệnh đề:
   (1) session tạo bằng `Config()` mặc định báo `tools` rỗng **và** `mcp_servers` rỗng;
   (2) đối chứng dương — cùng options ấy cộng một SDK MCP server **do chính lệnh kiểm tạo
   trong tiến trình** — báo đúng **1** tool và đó là tool của nó.
   Kiểm: chạy **trên cây chưa sửa** thì mệnh đề 1 **đỏ** và mệnh đề 2 **xanh**.
   **Nếu mệnh đề 1 xanh ngay từ đầu:** máy đang chạy không có MCP server nào, nên phép đo
   không đo được gì (`spec.md` C7). Dừng, chạy lại trên máy có, hoặc ghi rõ vào
   `## Proof result` rằng vế sống của nó chưa từng đỏ. **Không đi tiếp và gọi là xong.**
   Mệnh đề 2 dùng `create_sdk_mcp_server` nên không cần tiến trình con và không phụ thuộc
   cấu hình máy — đó là nửa duy nhất của phép kiểm đúng ở mọi nơi (`spec.md` R7).

2. **Cần gạt.** `cos_baodo/sessions.py:118` thêm `strict_mcp_config=True` vào
   `ClaudeAgentOptions`, viết ra kèm lý do, giống cách `fork_session=False` được viết ra để
   xoá nó là một sửa đổi nhìn thấy được.
   Kiểm: mệnh đề 1 chuyển **xanh**, mệnh đề 2 **vẫn xanh** — vế sau quan trọng ngang vế
   trước, vì một cần gạt chặn luôn cả server được truyền vào là chặn quá tay. Thêm một test
   khẳng định `_options` đặt cờ ấy; test này đúng trên mọi máy, kể cả máy không MCP.

3. **Gỡ test đang chứng nhận cái lỗi.** `cos_baodo/sessions_test.py:128-131` tên là
   `test_project_settings_cannot_widen_the_tool_list` và nó **xanh** — trong khi điều nó
   khẳng định thì sai, và chú thích của nó nói `setting_sources=None` chặn settings. Cùng
   niềm tin ấy nằm ở `cos_baodo/sessions.py:126`. Thay bằng một test có tên đúng với thứ
   thật sự được bảo đảm, và sửa chú thích thành điều `claude-agent-sdk` 0.2.157 (`uv.lock`
   `:179-180`) thực sự nói: `None` là **nạp hết**.
   Kiểm: không còn chuỗi `cannot_widen` trong `cos_baodo/`; `npm test` xanh.

4. **Phạm vi nói đúng phạm vi** (`spec.md` R6). `cos_baodo/config.py:44` và docstring
   `effective_tools` `:64-72` nêu rõ phép trừ chỉ có nghĩa trong tập built-in, và chỉ chỗ
   nào chặn MCP.
   Kiểm: đọc hai chỗ đó mà không cần mở file khác vẫn biết cái gì chặn cái gì.

5. **Một chỗ dựng options** (`spec.md` C8). Hôm nay `ClaudeAgentOptions(` xuất hiện đúng
   **1** lần ngoài test (`cos_baodo/sessions.py:118`). Thêm một test giữ nó ở đó, cùng kiểu
   với `NoWebFrameworkLeaksIn` trong `cos_baodo/service_test.py`.
   Kiểm: test đỏ khi thêm một lời gọi thứ hai.

6. **Đóng unit.** `.claude/CLAUDE.md` sửa câu "chat only — no tools" thành điều đúng và nói
   MCP bị chặn bằng gì. Chạy `## Proof`. Chạy `verify_0004.py` tay một lần (`spec.md` R9,
   nó ngoài chuỗi). Đặt `Status: done` chỉ sau khi tất cả xanh.

## Risks

**Lấy mất một năng lực người dùng đang có, và họ sẽ gặp nó trước khi đọc unit này.** Sau
bước 2, session của app không gọi được `claude.ai Claude Docs` hay `microsoft-learn` nữa,
trong khi cùng những server ấy vẫn chạy bình thường ở terminal. Đây là rủi ro tôi muốn không
phải viết ra, vì bản sửa thì đúng còn cảm giác thì là mất. Dấu hiệu: người dùng bảo "trước nó
làm được". Giảm thiểu: `.claude/CLAUDE.md` ở bước 6 phải nói ra, không giấu trong changelog;
và `spec.md` C1 đã ghi rằng chọn bức tường thay vì cái knob là một lựa chọn, không phải một
tất yếu.

**Phép kiểm xanh ở nơi không có gì để kiểm.** Mệnh đề 1 chỉ đỏ trên máy có MCP server. Trên
CI sạch hoặc máy đồng nghiệp, nó xanh trước và sau khi sửa, và unit này trông như đã được
chứng minh trong khi chưa. Dấu hiệu: `verify_0007.py` xanh trên cây **chưa** sửa. Giảm
thiểu: bước 1 có lệnh dừng gắn vào; mệnh đề 2 và test ở bước 2 là hai thứ đúng ở mọi máy.

**Bức tường không có cửa, và cửa duy nhất là một dòng** (`spec.md` C4). Ai đó cần MCP sẽ xoá
đúng `strict_mcp_config=True`. `npm test` sẽ đỏ nhờ test ở bước 2 — nhưng chỉ vì test ấy tồn
tại, và người xoá cờ sẽ thấy ngay một test chặn mình và có thể xoá nốt. Dấu hiệu: không có.
Giảm thiểu: không có cơ chế; chỉ có việc bước 2 viết lý do ra ngay tại chỗ.

**Nguồn chưa từng thấy** (`spec.md` C7). `strict_mcp_config` chặn theo nguyên tắc "chỉ nhận
cái được truyền vào", nên **nên** phủ cả nguồn chưa biết. Đó là lý lẽ. Dấu hiệu: một mục lạ
trong `mcp_servers` của `init`. Giảm thiểu: mệnh đề 1 kiểm `mcp_servers` **rỗng**, không chỉ
kiểm `tools` rỗng — một nguồn mới gắn được sẽ hiện ra ở đó kể cả khi nó chưa cấp tool nào.

**Ba lệnh chứng minh cũ tạo session thật.** Nếu cần gạt làm hỏng việc tạo session thì chúng
đỏ. Chưa có lý do để tin vậy — đo hôm nay tạo session thành công với cờ bật — nhưng chúng
nằm trong `## Proof` nên sẽ nói ngay.

**Hạn mức.** `verify_0007.py` tạo **2** session mỗi lần chạy, prompt một từ. Cộng với
`verify_0002` (4), `verify_0003` (2), `verify_0005` (1). Không vòng lặp không người trông.

## Proof

```
npm test \
  && uv run python scripts/verify_0002.py \
  && uv run python scripts/verify_0003.py \
  && uv run python scripts/verify_0005.py \
  && uv run python scripts/verify_0007.py
```

`verify_0004.py` **không** nằm trong chuỗi: nó đòi cổng mặc định trống và một bản build khớp
(`.cos/0004_unproven-page/spec.md:116` C1). `spec.md` R9 vẫn đòi nó xanh, nên chạy tay một lần
trước khi đóng unit.

`verify_0007.py` thoát 0 **chỉ khi** cả hai đúng, và in cả hai kể cả khi hỏng:

1. **Mặc định không có gì.** Session tạo bằng `Config()`: `tools` rỗng **và** `mcp_servers`
   rỗng.
2. **Đối chứng dương.** Cùng options cộng một SDK MCP server do lệnh kiểm tạo: đúng **1**
   tool, và tên nó là tool của server ấy.

Số **0** ở mệnh đề 1 lấy từ `cos_baodo/config.py:44`. Số **1** ở mệnh đề 2 là số tool lệnh
kiểm tự khai, nên nó do chính file ấy định nghĩa.

**Không có mệnh đề nào so với con số "trước khi sửa"** (`spec.md` R8). Đo ba lần trong ngày
2026-09-21 ra 11, 3 rồi 8 tool tuỳ server nào đang `connected`. Bằng chứng của lỗi là *khác
rỗng*, và nó thuộc về `## Proof result` của unit này, không thuộc về một phép so trong code.

## What this plan does not do

- **Không thêm knob bật/tắt MCP** (`spec.md` C4, open question 5). Bật lại là một quyết định
  về năng lực và cần intent riêng.
- **Không chặn `CLAUDE.md` hay `.claude/settings.json` của workspace** (`spec.md` R4, C5).
  `0006` đang đứng trên việc chúng **được** nạp, và đóng chúng là một bề mặt khác hẳn.
- **Không đụng `.cos/0002_*`** (`spec.md` R5). Quyết định ở đó đúng; code mới là phần thiếu.
- **Không thêm duyệt quyền từng lần gọi tool.** Hàng rào khác, chỉ đáng xét khi MCP được bật
  lại.
- **Không liệt kê đủ mọi nguồn năng lực** (`spec.md` C7). Ba nguồn đã thấy thì chặn; chưa ai
  chứng minh là hết.
- **Không sửa `0006`.** Nó đỗ ở `write-plan` và unit này giữ R4 để nó còn đúng.
