# Intent: an installed copy runs no stage of the loop it was published to run
Author: Bao Do. Type: fix. Status: accepted.

## Problem

`0011` đóng lại với một bản cài chạy được: một dòng lệnh, một systemd user service, sống
lại sau reboot. Ngày 2026-09-22, lần đầu tiên bản phát hành đó được cài như một người
ngoài cài nó — `curl ... install.sh | sh -s -- --working-dir /home/bd/coscc-wp`, trên máy
này, không dùng checkout — rồi clone chính repo này vào làm workspace qua
`POST /api/workspaces`. Cả hai bước đều xanh. Sau đó không còn gì chạy.

**Nguyên nhân là một, không phải hai.** App đi mượn `.claude/` của cây checkout bằng cách
đi ngược hai cấp từ chính nó:

- `coscc/board.py:31` — `SCRIPT = Path(__file__).resolve().parent.parent / ".claude" / "scripts" / "cos.mjs"`
- `coscc/runner.py:39` — `SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"`

Công thức đó chỉ đúng khi `coscc/` nằm cạnh `.claude/`, tức là khi app chạy từ repo của
chính nó. Trong wheel thì `parent.parent` là `site-packages/`, và `.claude/` không được
đóng gói: `pyproject.toml:26-31` khai `module-root = ""` với `module-name = "coscc"`, nên
thứ duy nhất đi theo bản phát hành là thư mục `coscc/`. Đo trên bản cài `v0.2.2`, ngày
2026-09-22:

```
SKILLS  exists=False  .../site-packages/.claude/skills
SCRIPT  exists=False  .../site-packages/.claude/scripts/cos.mjs
```

`coscc/board.py:11-12` đã viết sẵn điều lẽ ra phải đúng: module này chạy *"**the copy
that ships with the app**"*. Không có bản nào đi theo app cả. Câu đó mô tả một quyết định
đã ra, và việc đóng gói chưa bao giờ thực hiện nó — nên đây là một thiếu sót lúc phát hành,
không phải một câu hỏi thiết kế còn mở.

Hai triệu chứng, và chúng **hỏng theo hai kiểu khác nhau** — đây mới là chỗ đáng lo:

**Board tắt thành tiếng.** `coscc/board.py:87-88` kiểm tra file trước khi gọi `node`, nên
`GET /api/board?cwd=/home/bd/coscc-wp/coscc` trả 400 với nguyên văn
`the harness script is missing: /home/bd/.local/share/uv/tools/coscc/lib/python3.14/site-packages/.claude/scripts/cos.mjs`.
Hỏng rõ ràng, có thể sửa được vì nó tự khai.

**Run tắt không thành tiếng.** `coscc/runner.py:57-63` trả chuỗi rỗng khi không tìm thấy
skill, và docstring của nó nói thẳng lựa chọn đó: *"Missing is not fatal."*
`coscc/runner.py:90-92` chỉ chèn khối rules khi chuỗi ấy khác rỗng. Hệ quả là một step vẫn
chạy, vẫn tiêu quota thật, chỉ là chạy **không có luật của stage** trong prompt. Đo cùng
ngày, cùng một unit và stage `spec`, chạy `build_prompt` trên hai interpreter:

| | bản cài | checkout |
|---|---|---|
| `skill_for("intent")` | 0 ký tự | 4.250 |
| `skill_for("spec")` | 0 | 4.534 |
| `skill_for("plan")` | 0 | 4.157 |
| prompt của step `spec` | 14.313 ký tự | 18.882 |
| `included` mà step ghi lại | `['intent.md']` | `['intent.md']` |

Dòng cuối là cái tệ nhất. `coscc/runner.py` mở đầu bằng lý do `included` tồn tại — theo
`spec.md` R4, để *"a reader can check that it happened rather than take it on trust"*. Nó
giống hệt nhau ở hai cột. Nghĩa là bản ghi của một step không phân biệt được step chạy đủ
luật với step chạy thiếu 4.569 ký tự luật, và không có chỗ nào khác trong journal nói hộ.

**Proof của `0011` xanh trong khi không có gì chạy.**
`scripts/verify_0011.py:308-318` duyệt sáu màn hình bằng cách click `#nav-<key>` rồi chờ
`aria-current='page'`. Nó khẳng định màn Board *có mặt và active được*, không khẳng định gì
về nội dung. Một proof đi tới tận mức dựng VM, reboot hai lần và đo update — mà vẫn để lọt
trạng thái "cài xong, không dùng được". Đó là một lớp lỗi, không phải một lần sót.

Phần còn lại của app thì sống: `/api/health`, `/api/workspaces`, `/api/sessions`,
`/api/timeline` và `GET /` (200, 34 KB) đều trả lời đúng trên bản cài.

Trước 2026-09-22 vấn đề này không tồn tại được, vì trước `0011` không có bản cài nào.

## Proposed outcome

Trước 2026-10-06: trên một máy **không có checkout của repo này**, chỉ có bản cài từ
release, mọi năng lực của app cho ra kết quả không phân biệt được với chính app chạy từ
checkout, trên cùng một workspace. Đo bằng `scripts/verify_0012.py`, chạy hai phía và so
sánh:

1. `GET /api/board?cwd=<workspace>` trả về danh sách unit và stage **bằng đúng** thứ
   `node .claude/scripts/cos.mjs --root <workspace> status --json` in ra trong chính
   workspace đó;
2. prompt của một step ở stage `spec` dài ≥ 18.000 ký tự — hôm nay bản cài cho 14.313.

Outcome này sai được, và hôm nay nó đang sai ở cả hai điểm.

## Affected users and systems

- **Người cài `coscc` từ release.** Hôm nay là 0 người ngoài người viết nó; đó là lý do
  đây vẫn còn sửa được rẻ.
- **`coscc/board.py`, `coscc/runner.py`** — hai chỗ giữ công thức `parent.parent`.
- **`pyproject.toml`** — thứ quyết định cái gì đi theo wheel.
- **`scripts/verify_0011.py`** và bất kỳ proof nào sau nó: lớp lỗi "cài xong nhưng không
  dùng được" hiện không ai đo.
- **`docs/install.md`** — mục `## Prerequisites` viết *"You do not need Node, npm or bun"*,
  đúng cho việc build frontend và sai cho Board, vì `coscc/board.py:90` spawn `node` mỗi
  lần đọc. Đo trên máy này bằng cách cắt `node` khỏi `PATH`:
  `Unavailable: could not run node: [Errno 2] No such file or directory: 'node'`.

## Constraints

1. **Không được chạy bản `.claude/` của workspace.** `coscc/board.py:8-14` và
   `coscc/runner.py:15-17` đã từ chối điều đó có lý do ghi lại: workspace là repo clone từ
   URL người dùng gõ, thực thi file của nó là trao cho repo đó mọi thứ tiến trình này có.
   Ràng buộc này không mở lại trong unit này.
2. **Chỉ sửa cách app mang theo luật của chính nó.** Không đổi nội dung luật, không đổi
   stage list, không đổi `policy.GRANTS`.
3. Proof phải chạy trên một máy không có checkout, nếu không nó đo lại đúng cái nó bỏ sót.

## Open questions

1. **Unit này có bị thay thế bởi hướng đi dài hơn không?** Người khởi xướng nói ngày
   2026-09-22 rằng muốn Board thành thành phần độc lập trong repo, tự quản intents bằng
   Python, để project không còn phụ thuộc harness — lúc đó `cos.mjs` và cả `node` biến mất
   khỏi runtime. Nếu làm ngay thì phần đóng gói ở đây là việc sẽ bị xoá. Chọn làm trước vì
   hướng kia là nhiều unit, còn sản phẩm đã publish thì đang chết hai tính năng chính.
   Chưa có unit nào cho hướng kia; đây chỉ ghi lại rằng nó đã được nói ra.
2. **`node` có ở lại làm prerequisite không?** Nếu unit này chỉ vá đường dẫn thì có, và
   `docs/install.md` đang nói ngược. Nếu hướng ở câu 1 chạy trước thì không. Spec phải
   chọn một.
3. **`included` có phải ghi thêm việc rules có mặt hay không?** Bảng trên cho thấy bản ghi
   hiện không phân biệt được. Sửa nó nằm trong phạm vi ràng buộc 2 hay là unit khác, chưa
   quyết.
4. **Câu chuyện template còn đúng không?** `.claude/CLAUDE.md`, mục
   `## Copying this into another repository`, nói copy `.claude/` là xong. Nếu wheel bắt
   đầu mang theo `.claude/` thì repo này có hai đường phân phối cho cùng một bộ luật.
