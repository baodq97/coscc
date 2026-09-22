# Plan: one function decides where a unit lives, then git, then the two routes
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

## OQ1, settled before planning

`spec.md` `## Open questions` mục 1 chặn outcome: một unit mới tinh không có gì cho stage
`intent` bám vào. Đọc `coscc/runner.py:112-121` thì câu trả lời đã có sẵn trong mã: với
stage `intent`, `stages[:position]` là `['idea']`, nên `build_prompt` **đã** đọc `idea.md`
và đưa vào prompt.

Vậy: `POST /api/units` nhận thêm `brief`, và ghi nó thành `idea.md` với
`Status: accepted.`. Không cơ chế mới, không tham số prompt mới, và nó trùng đúng điều
`write-intent` bất biến 1 đòi — *"The originator states the problem in their own words
first"*. `idea` là stage tuỳ chọn đầu tiên và đây chính là việc của nó.

Kiểm bằng: `brief` xuất hiện nguyên văn trong prompt mà bản ghi journal của bước `intent`
liệt kê là đã gửi.

## Files that change

| Path | Gì | Requirement |
|---|---|---|
| `coscc/units.py` | **(new)** `root()` — một câu trả lời cho "unit ở đâu"; `create()` bọc `cos.mjs new-path`; ghi `idea.md` | R1, R2, R3 |
| `coscc/units_test.py` | **(new)** gồm test đổi data root và thấy cả ba đường đi theo | R3 |
| `coscc/board.py` | `--root` trỏ vào store, không phải cây workspace | R2, R3 |
| `coscc/board_test.py` | 6 chỗ gọi `board.read(REPO)` phải dựng store riêng | R3 |
| `coscc/runner.py` | `unit_dir` nhận store root thay vì tự tính từ workspace | R3 |
| `coscc/runner_test.py` | | R3 |
| `coscc/gitops.py` | `current_branch` (đọc), `create_branch` (ghi, hẹp) | R4, R5 |
| `coscc/gitops_test.py` | một test cho **mỗi** điều R5 cấm | R5 |
| `coscc/policy.py` | ranh giới ghi mở thêm đúng thư mục unit của bước đó | C2 |
| `coscc/policy_test.py` | cả hai phía của ranh giới | C2 |
| `coscc/service.py` | `create_unit`, `start_branch`, truyền root, ghi chuyển trạng thái | R1, R4, R6 |
| `coscc/service_test.py` | | R1, R4, R6 |
| `coscc/api.py` | `POST /api/units`, `POST /api/units/{unit}/branch` | R1, R4 |
| `coscc/api_test.py` | | R1, R4 |
| `coscc/screens.py` | ô nhập slug + brief, nút tạo; nút cắt nhánh; câu giải thích C1 | R8 |
| `coscc/state.py` | handler cho hai nút đó | R8 |
| `scripts/verify_0014.py` | **(new)** proof, chỉ gọi HTTP | R9 |
| `.claude/rules/coscc-app.md` | dòng proof + hazard | R9 |

Mọi đường dẫn không đánh `(new)` đã kiểm là có thật, 2026-09-22.

**Không đổi, cố ý:** `.claude/scripts/cos.mjs` (`intent.md` ràng buộc 4), `coscc/data.py`,
`coscc/states.py`, `coscc/backfill.py`, `coscc/config.py`. Không thêm knob thứ năm: store
nằm dưới `data_dir` đã có.

## Order of work

1. **`units.py` + test.** Một hàm trả lời "unit của workspace này ở đâu", một hàm tạo unit
   bằng `cos.mjs new-path` rồi ghi `idea.md`. **Kiểm được:** `npm test` xanh, chưa ai gọi.
2. **Trỏ ba chỗ vào nó** (`board.py:95`, `runner.py:49-54`, `service.py:721`) và sửa test
   theo. **Kiểm được:** `npm test` xanh; `GET /api/board` cho workspace này trả **0 unit**,
   và đó là C1 xảy ra đúng như đã báo, không phải hỏng.
3. **`gitops`: `current_branch` và `create_branch`.** **Kiểm được:** bốn test từ chối của
   R5 — không push, không merge, không commit, không đụng `main` — mỗi cái đỏ nếu gỡ guard.
4. **`policy`: nới ranh giới ghi đúng một thư mục.** **Kiểm được:** một test ghi vào thư mục
   unit thì cho, một test ghi ra ngoài cả hai thì vẫn từ chối.
5. **`service` + `api`: hai route.** **Kiểm được:** `npm test`, rồi `curl` tay tạo một unit
   trong một repo tạm và thấy nó hiện trên board.
6. **`screens` + `state`.** **Kiểm được:** mở trang thật, thấy ô nhập và hai nút.
7. **`scripts/verify_0014.py`, chạy khi chưa có gì.** Phải **exit 1** ở bước 1 của bảng sáu
   bước.
8. **Chạy thật, trọn vòng, trên repo dùng một lần.** Phải **exit 0**, 6/6.
9. **`impl.md`**, ghi số đo thật của bước 7 và 8, gồm cả tiền đã tiêu.

## Risks

Xếp theo bán kính. Mục 1 là mục muốn không phải viết ra.

1. **Bước 4 nới một guard an ninh.** `coscc/policy.py:206-216` hôm nay là một câu:
   ghi ngoài workspace thì từ chối. Sau bước 4 nó là hai câu, và câu thứ hai nhận một đường
   dẫn do `units.root()` sinh ra. Nếu `units.root()` từng nhận dữ liệu từ request thì đó là
   đường từ HTTP tới một chỗ ghi bất kỳ. Giảm nhẹ: `units.root()` chỉ nhận `data_dir` (từ
   môi trường, `coscc/config.py`) và tên workspace đã qua cổng `_workspace_or_refuse`; và
   có test cho phía từ chối, không chỉ phía cho phép.
2. **Board của repo này trống sau bước 2.** C1. Không phải lỗi, nhưng nó sẽ trông y như lỗi,
   và nó là màn hình tôi dùng để kiểm mọi thứ khác. Giảm nhẹ: bước 2 kết thúc bằng câu giải
   thích trên trang (bước 6), và `0013` `intent.md` đã ghi sẵn lý do — `.cos/` ở đây là hồ
   sơ đóng băng.
3. **Bước 8 tiêu tiền thật và không hoàn lại được.** Tám session, `impl` trần $5, `pr` trần
   $3 (`coscc/policy.py:101-115`). Nếu vòng chạy hỏng giữa chừng thì tiền đã tiêu vẫn mất.
   Giảm nhẹ: bước 7 chạy proof **trước** khi tiêu đồng nào, trên một repo rỗng, để mọi lỗi
   đường ống lộ ra ở chỗ rẻ.
4. **`unit_dir` đổi chữ ký, và nó nằm trên đường tiêu tiền.** `coscc/runner.py:243` dựng
   `directory` từ nó, và `coscc/runner.py:292` ghi artifact vào đó. Sai đường dẫn là một
   bước chạy xong, tốn quota, rồi ghi file vào chỗ không ai đọc. Giảm nhẹ: bước 2 đứng riêng
   và `npm test` phải xanh trước khi có bất cứ session nào chạy.
5. **Repo dùng một lần vẫn là một repo trên GitHub thật.** C4: `gh` của máy chạm mọi repo mà
   tài khoản chạm được, và bước 8 để một session dùng nó. Giảm nhẹ: repo riêng, tạo mới cho
   việc này, và proof in ra tên repo nó đang làm việc trước khi làm gì.
6. **`new-path` in đường dẫn tương đối** (`.cos/0002_...`, đo 2026-09-22 với `--root`). Ghép
   sai là tạo thư mục lệch chỗ. Giảm nhẹ: `units.create()` ghép với root và **kiểm lại**
   rằng thư mục vừa tạo nằm dưới root đó.

## Proof

```sh
npm test
uv run python scripts/verify_0014.py
```

Đạt khi: `npm test` xanh cả hai runtime, **và** `verify_0014.py` exit 0 — sau khi chính file
đó đã exit 1 ở bước 7.

`verify_0014.py` khẳng định, theo bảng sáu bước của `intent.md`, và **chỉ bằng HTTP**:

1. tạo được unit (`POST /api/units`) và nó hiện trên `GET /api/board`;
2. chạy được stage `intent`, và prompt đã gửi có chứa nguyên văn `brief`;
3. cắt được nhánh, và `git branch --show-current` trong workspace trả đúng tên
   `cos.mjs unit-branch` sinh ra;
4. chạy hết các stage còn lại, mỗi stage để lại một chuyển trạng thái có `actor` và
   `session` **không** phải `unknown` (R6);
5. `git status --porcelain` trong workspace không có dòng nào dưới `.cos/` (R2), và thư mục
   `.cos/` không tồn tại ở đó;
6. pull request tồn tại và đã merge, đọc bằng `gh pr view --json state`.

Nó dùng `git` và `gh` **chỉ để đọc**. Nó không gọi `git switch`, `git commit`, `gh pr
create` hay `gh pr merge` — mỗi lệnh đó là một việc sản phẩm phải tự làm, và một proof làm
hộ là một proof đo chính nó (`intent.md` ràng buộc 1).

Exit 2 khi: không có `gh` đã đăng nhập, không có `node`, không có service đang chạy, hoặc
biến chỉ repo dùng một lần chưa đặt.

**Proof này tốn tiền.** Tám session, trần $8 tổng theo `coscc/policy.py`. Nó không thuộc về
một vòng lặp không người trông (`.claude/rules/coscc-app.md`), và bước 7 tồn tại để mọi lỗi
rẻ lộ ra trước bước 8.
