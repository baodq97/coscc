# Plan: A chat-only web app over the Agent SDK
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

> **Đọc đường dẫn trong file này theo bảng sau.** `0002` đổi tên gói `app/` thành
> `cos_baodo/` (commit `81295b9`), nên 13 trích dẫn dưới đây trỏ vào đường dẫn không còn
> tồn tại. Chúng **không được sửa**: file này ghi lại việc đã làm vào lúc đã làm, và viết
> lại nó thành `cos_baodo/` sẽ thành một bản ghi sai theo kiểu khác — nói rằng các file được
> tạo ở chỗ mà lúc ấy chúng không ở.
>
> | Viết trong file này | Nay nằm ở |
> |---|---|
> | `app/__init__.py` | `cos_baodo/__init__.py` |
> | `app/config.py` | `cos_baodo/config.py` |
> | `app/config_test.py` | `cos_baodo/config_test.py` |
> | `app/sessions.py` | `cos_baodo/sessions.py` |
> | `app/sessions_test.py` | `cos_baodo/sessions_test.py` |
> | `app/web.py` | `cos_baodo/api.py` (đổi framework ở `0002` bước 5) |
> | `app/web_test.py` | `cos_baodo/api_test.py` |
> | `app/public/index.html` | đã xoá ở `0002` bước 9 — trang nay là `cos_baodo/cos_baodo.py` |
>
> Đây là câu trả lời cho `spec.md` C14 của `0002`.

Hồ sơ agent đầu tiên là **chat thuần, không tool nào** (`spec.md` C2). Kết quả trong
`intent.md` không cần tool, nên mặc định an toàn nhất không tốn phạm vi.

## Files that change

| Path | |
|---|---|
| `pyproject.toml` | (new) dự án Python ở gốc repo, quản lý bằng `uv` |
| `app/__init__.py` | (new) |
| `app/config.py` | (new) **một chỗ nối duy nhất** đọc cấu hình — `spec.md` C8 |
| `app/config_test.py` | (new) |
| `app/sessions.py` | (new) liệt kê, tạo, mở lại session qua Agent SDK |
| `app/sessions_test.py` | (new) |
| `app/web.py` | (new) HTTP trên loopback, luồng phản hồi |
| `app/public/index.html` | (new) trang chat |
| `scripts/verify_0001.py` | (new) lệnh phán đạt/trượt, dùng ở `## Proof` |
| `package.json` | có thật, 13 dòng — `test` phải chạy cả Python, `spec.md` C5 |
| `.gitignore` | có thật, 8 dòng — thêm `.venv/`, `__pycache__/` |
| `.claude/CLAUDE.md` | có thật — cập nhật `## Commands` |

**Không đụng tới** `channel/`, `evidence/`, `scripts/verify-0001.mjs`. `intent.md` cấm gỡ
chúng và `scripts/verify-0001.mjs:9` vẫn phải chạy được.

Web framework: **aiohttp**, một dependency, asyncio đúng như SDK. Stdlib `http.server` không
hợp vì SDK là asyncio và phản hồi cần chảy theo luồng. Test dùng `unittest` của stdlib để
không thêm dependency thứ hai chỉ để chạy test.

## Order of work

1. **Chỗ đứng cho Python.** `pyproject.toml`, `uv sync`, thêm `.venv/` và `__pycache__/` vào
   `.gitignore`.
   Kiểm: `uv run python -c "import claude_agent_sdk, aiohttp; print('ok')"` in ra `ok`, và
   `git status --short` không thấy `.venv` hay `__pycache__`.

2. **Chỗ nối cấu hình, trước mọi thứ khác.** `app/config.py` trả về bốn knob ở `spec.md` C2
   với mặc định chặt, đọc từ **một** hàm duy nhất. Không knob nào đổi được qua HTTP.
   Kiểm: `app/config_test.py` xanh, trong đó có một test khẳng định mặc định là **không tool
   nào** — để việc nới lỏng sau này phải sửa một test có tên rõ ràng, không lặng lẽ trôi.

3. **Lớp đọc.** `app/sessions.py`: liệt kê session theo thư mục, đọc lịch sử. Thao tác đĩa
   thuần, không tạo session nào.
   Kiểm: chạy trên chính repo này, trả về ≥1 session kèm `cwd` và tóm tắt; `unittest` xanh.

4. **Spike: tạo rồi mở lại, định danh phải khớp. Có lệnh dừng gắn vào.** Tạo một session
   chat-only, gửi một prompt ngắn, đóng client, mở lại, so `session_id`.
   Kiểm: script in ra hai định danh và chúng **khớp chính xác**.
   **Đây là toàn bộ kết quả của `intent.md` thu nhỏ lại.** `spec.md` C7 cảnh báo chế độ mở
   lại có nhánh **tách sang định danh mới**; bật nhầm thì mọi thứ vẫn chạy và chỉ có kết quả
   là sai, sai im lặng. **Không khớp thì dừng, sửa `spec.md`, đừng đi tiếp.**

5. **Lớp HTTP.** `app/web.py`, bind `127.0.0.1`. Các đường: liệt kê workspace và session,
   tạo session, gửi prompt và nhận luồng, đọc lịch sử. Cùng bề mặt này phục vụ cả lệnh kiểm
   ở bước 7 — không có đường riêng cho test.
   Kiểm: `ss -ltn` cho thấy `127.0.0.1`, không phải `0.0.0.0`; mỗi đường trả lời được bằng
   một lệnh dòng lệnh.

6. **Trang chat.** `app/public/index.html`: chọn workspace, chọn hoặc tạo session, gõ, xem
   trả lời. Tải trang thì dựng lại lịch sử từ lớp đọc — `spec.md` open question 5 nói lần
   này phải **cố ý** giải, vì `terminal-only-access` để nó rơi.
   Kiểm: mở trang, F5, lịch sử vẫn còn.

7. **Lệnh chứng minh.** `scripts/verify_0001.py` theo `## Proof`.
   Kiểm: chạy khi chưa có gì thì thoát khác 0 kèm lý do.

8. **Chạy thật và đóng unit.** Chạy `## Proof` trên hai project. Cập nhật `## Commands` trong
   `.claude/CLAUDE.md`, sửa `package.json` để `npm test` chạy cả hai runtime. Đặt
   `Status: done` sau khi `## Proof` xanh.

## Risks

**Mỗi lần chạy đều tiêu hạn mức, và không gì đếm nó.** Đây là rủi ro tôi muốn không phải
viết ra. `terminal-only-access` chạy trong session sẵn có nên không tốn thêm; `0001` **tạo** session, và
bước 4, 7, 8 đều tạo session thật. Một vòng lặp hỏng trong `app/web.py` hoặc trong lệnh kiểm
là một vòng lặp đốt hạn mức của tài khoản, im lặng. Dấu hiệu: cảnh báo hạn mức, hoặc phản
hồi chậm bất thường. Giảm thiểu: lệnh kiểm gửi prompt ngắn nhất có thể và có giới hạn lượt;
không bước nào chạy trong vòng lặp không người trông.

**Định danh không khớp khi mở lại.** Nếu bước 4 đỏ thì bước 5 đến 8 vô nghĩa, vì R3 là mệnh
đề trung tâm. Dấu hiệu: hai chuỗi khác nhau. Đó là lý do bước 4 đứng trước phần HTTP và có
lệnh dừng gắn vào.

**Token đăng nhập nằm trong tiến trình đang nghe HTTP** (`spec.md` C3). Ở `terminal-only-access` lộ cổng là
lộ một ô chat; ở đây là lộ thông tin đăng nhập dài hạn. Dấu hiệu: không có dấu hiệu nào —
đây là loại rủi ro không tự báo. Giảm thiểu: không đường nào trả về biến môi trường, không
đường nào chạy lệnh theo chữ người dùng gửi, và chat-only nghĩa là session không có tool để
đọc chính file token.

**Tiến trình con rò rỉ.** Mỗi client của SDK sinh một tiến trình CLI. Không đóng thì chúng
tích lại. Dấu hiệu: `pgrep -f claude` tăng dần sau mỗi lần dùng.

**Hai runtime, một câu "tests must be green".** Nếu `npm test` không chạy phần Python thì
câu đó mất nghĩa mà không ai nhận ra, vì nó vẫn xanh (`spec.md` C5). Dấu hiệu: thêm một test
Python hỏng mà `npm test` vẫn xanh — đáng thử đúng một lần ở bước 8.

**Chat-only vẫn có thể chạm vào duyệt quyền.** Chưa kiểm. Nếu SDK vẫn hỏi duyệt dù không tool
nào được phép, bước 4 sẽ treo thay vì trả lời. Dấu hiệu: script không in gì và không kết
thúc.

## Proof

```
npm test && uv run python scripts/verify_0001.py
```

`verify_0001.py` tự khởi động app trên một cổng trống, tự tắt khi xong, và thoát 0 **chỉ
khi** cả năm điều đúng:

1. Liệt kê được session cho **2 thư mục project khác nhau**, không mục nào lẫn sang nhau.
2. Tạo được session mới trong mỗi project.
3. Mỗi session trả về phần chữ phản hồi khác rỗng.
4. Mở lại cho `session_id` **khớp chính xác** cái đã tạo.
5. Số message sau khi mở lại **không nhỏ hơn** số trước đó.

Thoát khác 0 thì in ra điều nào trong năm điều đã hỏng. Project thứ hai do chính lệnh này
tạo trong thư mục tạm, để nó tự đứng được mà không cần máy có sẵn repo thứ hai.

Con số 2 lấy từ `intent.md`. Muốn đổi thì sửa ở đó, không sửa ở đây.

## What this plan does not do

- **Không dựng kho cấu hình trung tâm** (`spec.md` C8). Bốn knob chưa biện minh được một
  schema. Bước 2 dựng chỗ nối để đổi nguồn sau là đổi một chỗ.
- **Không dựng cơ chế nhiều hồ sơ agent** (`spec.md` C9). Hồ sơ thứ hai cần intent riêng.
- **Không cho mở lại session app không tạo ra** (`spec.md` C1), cho tới khi open question 3
  được kiểm. Session của terminal sẽ **hiện trong danh sách** vì lớp đọc thấy chúng, nhưng
  gửi prompt vào đó bị chặn.
- **Không đếm hay chặn hạn mức.** Rủi ro đầu tiên ở trên không được giảm thiểu bằng code
  trong unit này, chỉ bằng việc giữ mọi thứ ngắn và có người trông.
- **Không gỡ `channel/`.** Nó retired, không bị xoá.
