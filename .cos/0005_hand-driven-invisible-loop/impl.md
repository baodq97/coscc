# Impl: hand-driven invisible loop
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Mười một bước của `plan.md`, mỗi bước một commit, từ `dc47a9e` tới `029926b`.

**1. Vòng lặp biết tám giai đoạn** (`dc47a9e`). `STAGES` trong
`.claude/scripts/cos.mjs:24-33` trở thành nơi duy nhất định nghĩa vòng lặp: tên artifact,
các status hợp lệ, điều gate đòi, và hành động kế tiếp đều đọc từ mảng đó. `checkGate` và
`nextAction` thành vòng lặp thay cho ba nhánh viết tay. `idea` là `optional` nên nó không
gate gì; `plan.md: done` là điểm dừng. Hai điều đó là lý do năm unit đã đóng dưới vòng ba
giai đoạn vẫn đọc ra `finished` sau khi mở rộng. Thêm cờ `--root <dir>` để đọc `.cos/` của
repo khác bằng chính luật của repo này. Năm skill mới: `write-idea`, `write-impl`,
`write-pr`, `write-review`, `write-ship`.

**2. Board đọc unit mà không đẻ thêm parser thứ hai** (`4585d68`). `cos_baodo/board.py`
chạy **`cos.mjs` của chính app** với `--root`, không bao giờ chạy bản nằm trong workspace —
workspace là repo ai đó clone về, file trong đó là code của người khác. Cùng lý do ấy,
`runner.py` lấy luật giai đoạn từ `.claude/skills/` của app. Môi trường con dựng tay, chỉ
`PATH`/`HOME`/`LC_ALL`/`NO_COLOR`.

**3. Journal** (`2dafc9f`). `cos_baodo/journal.py`, JSONL chỉ ghi thêm, nằm cạnh store.
Khoá bằng `flock` và mở bằng `O_APPEND`. Ghi mode, start, finish, denial, cost. Tổng của
một unit được **cộng lại từ các bước**, không lưu sẵn — một con số lưu sẵn là con số thứ hai
có thể cãi nhau với con số thứ nhất.

**4. API** (`c65d432`). `GET /api/board`, `POST /api/board/mode`, `POST /api/board/run`
(NDJSON), `GET /api/timeline`. Mode là thứ duy nhất board ghi.

**5. Sàn thẩm mỹ** (`02c50b9`). Theme khai báo tường minh qua
`rx.plugins.RadixThemesPlugin` trong `rxconfig.py` (0.9 đã bỏ `App(theme=)`), `cos_baodo/ui.py`
giữ token và primitive, nút đổi light/dark bọc trong `rx.box(id="color-mode")` làm mốc cho
phép đo.

**6. Board lên trang** (`90857c9`). Tám ô mỗi unit, chấm cho chưa bắt đầu, tia chớp cho
autonomous, chìa khoá liệt kê tool được cấp, timeline và bảng điều khiển chọn mode.

**7. Giữ lại con số vẫn đang bị vứt** (`7d13f5a`). `sessions.py` trước đây đọc mỗi
`session_id` từ `ResultMessage` rồi bỏ phần còn lại. Nay lấy usage và `total_cost_usd`,
trừ ra phần của lượt hiện tại, và trả kèm `terminal_reason`.

**8. Sáu giai đoạn chữ tự chạy, tay không** (`bc15639`). `cos_baodo/policy.py` là bảng
grant, khoá theo `(stage, mode)`, **đặt ngoài `Config`** để bốn knob vẫn nói đúng một thứ.
Mặc định là vị trí khoá: không tool, không lệnh, một lượt, không ngân sách.
`cos_baodo/runner.py` dựng prompt gồm `intent.md` cộng artifact của giai đoạn liền trước,
kiểm câu trả lời trước khi nó thành file, và **app cầm bút** cho sáu giai đoạn ấy.

**9. `impl` tự chạy, và cổng kiểm nằm trên mọi lời gọi** (`2ba3106`). `can_use_tool` là lớp
thứ hai và là lớp có thật: `--tools` chỉ đặt tên cho tập built-in. Luật lệnh kiểm **từng
đoạn** của chuỗi lệnh, từ chối thay thế (`$( )`, backtick, `${ }`, `<( )`), từ chối chuyển
hướng vào file, và bỏ qua chuyển hướng descriptor. Ghi chép mọi lần bị từ chối.

**10. `pr`** (`a7bba47`). Grant `git` và `gh`, không package manager. Trần thấp hơn `impl`.
Trang nói rõ **trước khi bấm** rằng bước này dùng credential `gh` mức máy.

**11. `verify_0005.py` và tài liệu** (`029926b`). Bảy mệnh đề, mã thoát theo
`scripts/verify_0003.py:49`. `.claude/harness.md` và `.claude/CLAUDE.md` sửa cho khớp.

## Where the plan was departed from

**1. Ai cầm bút cho artifact.** `spec.md ## Design` viết runner không tự ghi artifact, agent
ghi. Nhưng R9 của cùng file cho sáu giai đoạn chữ grant rỗng ở cả hai chế độ, mà session
không tool thì không ghi được file. Chọn: **app ghi**, agent chỉ trả văn bản. Ghi ở
`plan.md` Risk 1 từ trước khi code, không sửa lén một artifact đã `accepted`.

**2. Mệnh đề 5 hỏi thêm một vế.** `plan.md ## Proof` viết "báo 0 tool trong `init`".
`verify_0005.py` hỏi **0 tool và 0 MCP server**, vì đo riêng danh sách tool cho kết quả
không ổn định (xem `## What was measured`). Ghi ở `plan.md` bước 11.

**3. Thiếu remote không làm cả lệnh im.** `plan.md` ngụ ý một lệnh một kết quả; bản chạy
chia phần: 1, 5, 7 vẫn chạy khi không có remote, chỉ 2, 3, 4, 6 bị bỏ qua. Cũng ghi ở
`plan.md ## Proof`.

Ngoài ba chỗ đó, không đi khác plan.

## What was measured

Ngày 2026-09-22, trên máy này.

```
npm test                              257 xanh (31 node, 226 python)
uv run cos-build                      reflex 0.9.11.post1, 2 source files
uv run python scripts/verify_0001.py  exit 0 — cả 5 claim, 2 project
uv run python scripts/verify_0002.py  exit 0 — cả 3 claim
uv run python scripts/verify_0003.py  exit 0 — cả 5 check, kể cả sàn thẩm mỹ
uv run python scripts/verify_0004.py  exit 0 — 20/20 entry, pull bị chặn đúng lúc
uv run python scripts/verify_0005.py  exit 1 — xem bên dưới
```

`verify_0005.py`, chạy không đặt `COS_PROOF_REPO`:

- **Mệnh đề 1 đạt** — gate trả lời đủ tám tên, từ chối `deploy`, `rollback`, tên rỗng, và
  trước đó chứng minh nó biết chặn (unit rỗng bị chặn ở `spec`).
- **Mệnh đề 7 đạt** — bước `impl` với `max_turns=1` trả `exhausted` kèm lý do, không treo.
- **Mệnh đề 5 KHÔNG đạt** — session giai đoạn chữ, grant rỗng, vẫn có `microsoft-learn` và
  `claude.ai Claude Docs` gắn vào, cùng ba tool `mcp__microsoft-learn__*`. Đây đúng là
  `chat-only-sessions-have-tools`, unit có plan accepted và chưa một dòng code.
- **Mệnh đề 2, 3, 4, 6 bỏ qua** — `git remote -v` rỗng, đo lại 2026-09-22.

**Đo `tools` trong `init` không đáng tin một mình.** Hai lần chạy liên tiếp, cùng máy cùng
tham số: lần đầu ba tool, lần sau không tool nào, trong khi cả hai lần đều có đúng hai MCP
server gắn vào. Danh sách tool chạy đua với lúc server kết nối xong. Nếu mệnh đề 5 chỉ hỏi
số tool thì `0005` đã tự cấp cho mình một dấu xanh.

**`ResultMessage.usage` là cộng dồn cả session, không phải của lượt** — `plan.md` Risk 4,
đo ở bước 7: hai lượt trong một session cho cache-read 1608 rồi 5512, và
`total_cost_usd` 0.016909 rồi 0.036336. Cộng thẳng hai lượt ra 7120 trong khi thật là 5512.
Nên `sessions.py` trừ ra phần chênh.

**Một lần chạy `impl` autonomous thật** (bước 9): sửa được file trong workspace, tự ghi
`impl.md`, 19 lượt, $0.447198, và **4 lần bị từ chối**, trong đó có một `Write` ra
`/tmp/step9-…/proof.py` — ngoài workspace.

**Board đọc mất ~0.05s**, đo 5 lần; `board.py` để timeout 10s.

## What is still open

1. **Mệnh đề 1 của `intent.md` không đạt, nên cả unit không đạt.** Ba mệnh đề đi chung —
   đó là cái giá `intent.md` ghi khi chọn gộp. Repo không có remote nên `pr` không có chỗ
   đi. Tôi không tự tạo remote: việc đó với ra ngoài máy và là quyết định của tác giả.
   Hạn `2026-09-28` vẫn còn.
2. **`chat-only-sessions-have-tools` chưa làm, nên mệnh đề "0 tool" chỉ đúng một nửa.** `spec.md` C1 khuyên làm
   `chat-only-sessions-have-tools` trước `0005`; đã không làm vậy. Giờ `verify_0005.py` là chỗ nêu tên vấn đề đó
   mỗi lần chạy.
3. **`review` là ô xanh trên một chiếc ghế trống** (`spec.md` C5). Skill có, gate có,
   nhưng người review là chính agent đã viết code, và `accepted` là tự cấp.
4. **Luật lệnh chặn theo tên nhị phân, không theo lệnh con** (`plan.md` Risk 3).
   `git push --force` và `gh api` vẫn lọt. Biên thật là `cwd`, luật đường ghi, và hai trần.
   `policy_test.py:148-158` ghi giới hạn này thành một test xanh thay vì để nó ngầm.
5. **`0004` C2 rộng thêm.** Journal ghi thường xuyên hơn hẳn danh sách workspace, mà hai
   bản app trên một working folder vẫn nhìn xuyên nhau.
6. **Không có gì tự châm bước kế tiếp.** Đúng như `spec.md ## Out of scope` chốt.
