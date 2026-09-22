# Impl: one function decides where a unit lives, then git, then the two routes
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Mười ba commit trên `feat/product-cannot-start-a-work-unit`, theo đúng thứ tự `plan.md`
`## Order of work`. Mỗi dòng dưới đây là một commit có thật trong repo này.

| Commit | Bước | Gì |
|---|---|---|
| `ec0a829` | 1 | `coscc/units.py` **(new)** — `root()` (`:90`) là một câu trả lời duy nhất cho "unit của workspace này ở đâu"; `create()` (`:178`) bọc `cos.mjs new-path` rồi ghi `brief` thành `idea.md` (`_idea`, `:218`) |
| `72ab39a` | 2 | `board.py`, `runner.py`, `service.py` cùng trỏ vào `units.root()`. Board của repo này thành rỗng — C1 xảy ra đúng như đã báo trước |
| `050eb7f` | 3 | `gitops.current_branch` và `gitops.create_branch`. Bốn test từ chối của R5: không push, không merge, không commit, không đụng `main` |
| `a6ff136` | 4 | `coscc/policy.py` nới ranh giới ghi đúng một thư mục — thư mục unit của bước đang chạy, không hơn |
| `3f7d614` | 5 | `POST /api/units`, `POST /api/units/{unit}/branch`, và bảng `runs` có dòng thật đầu tiên |
| `3ff74b3` | 6 | Ô nhập slug + brief, hai nút, câu giải thích C1 trên trang |
| `4a6983e` | 7 | `scripts/verify_0014.py` **(new)**, 337 dòng, chỉ nói HTTP |
| `80b0894` `1ef1630` `265930b` | 8 | Ba thứ phải thêm mới chạy nổi vòng thật — xem `## Where the plan was departed from` |

Ba commit của bước 8 là phần không có trong kế hoạch, và chúng là phần đáng đọc nhất:

1. **Một bước trả tiền rồi hỏng thì giữ lại thứ nó đã mua.** `coscc/runner.py:150-153`
   (`REPLY_KEPT`, `_with_reply`) và hai nhánh `except` tại `:327` và `:334`. Trước đó một
   stage văn xuôi trả lời thiếu dòng `Status:` là mất trắng: tiền đã tiêu, artifact không
   được ghi, và bản ghi duy nhất là lý do từ chối.
2. **Trang nói vì sao hỏng, không chỉ nói là hỏng.** `coscc/journal.py:372` — `_fold` đã
   **bỏ rơi** `detail` từ `0005`, nên mọi thất bại từ trước tới nay tới bảng dưới dạng một
   chữ `failed` không kèm gì. `coscc/state.py:151` mang nó vào `Run`, `coscc/screens.py:935`
   hiện nó.
3. **Stage `pr` merge cái PR của chính nó.** `.claude/skills/write-pr/SKILL.md:27-50` — đây
   là nửa còn thiếu của bước 6, và không skill nào từng nói nó. Đoạn đó nói thẳng rằng cùng
   một bên mở và merge, và trỏ vào `.claude/CLAUDE.md` `## What is deliberately not built`,
   nơi đã ghi sẵn rằng việc đó *"changes the route, not the reviewer"*.

Cộng một sửa công cụ: `scripts/proof_harness.py:48` bật line-buffering cho stdout, vì một
proof chạy nửa tiếng mà ghi ra file rỗng từ đầu tới cuối thì không phân biệt được "đang
chạy" với "treo".

## Where the plan was departed from

1. **Bước 7 đo thứ tốt hơn kế hoạch đòi.** Kế hoạch muốn proof chạy đỏ *trước khi* có
   implementation. Thay vào đó nó được chĩa vào bản `0.3.0` đã phát hành đang chạy trên máy
   này: `POST /api/units -> 405: Method Not Allowed`, **0 of 6**. Đo sản phẩm đã ship, không
   phải đo một khoảnh khắc trong nhánh. Ghi trong `4a6983e`.

2. **Bốn file bị sửa mà bảng `## Files that change` không có:** `coscc/harness.py`
   (`child_env()`, từ bước 1 — `ec0a829`), rồi `coscc/journal.py`,
   `scripts/proof_harness.py` và `.claude/skills/write-pr/SKILL.md` ở bước 8.
   `write-plan` bất biến 8 đòi sửa `plan.md` **trong cùng commit** với chỗ lệch. **Tôi đã
   không làm** — bảng chỉ được bổ sung ở commit chứa chính file này, tức là muộn tám commit
   với `harness.py` và ba commit với phần còn lại. Ghi ra đây vì nó là một lần vi phạm,
   không phải một lựa chọn.

   Thêm hai chỗ bảng gốc nói sai: `coscc/board_test.py` không tồn tại (file thật là
   `coscc/board_api_test.py`, và `plan.md` bất biến 1 đòi kiểm mọi đường dẫn trước khi
   viết xuống — dòng đó không được kiểm), còn `.claude/rules/coscc-app.md` được liệt cho R9
   nhưng mãi tới commit này mới sửa, tức là bước 7 đã đóng lại khi còn thiếu một nửa.

3. **Bước 5 của bảng sáu bước bị đọc hẹp đi, và điều đó là cố ý.** `intent.md` viết bước 5
   là *"Chạy các stage, **mỗi artifact một commit**"*. `spec.md` R2 quyết định artifact sống
   trong store của sản phẩm chứ không vào cây repo, nên vế "mỗi artifact một commit" **không
   còn áp dụng cho repo đích** — và `scripts/verify_0014.py:115` chấm bước 5 là *"work the
   stages"*, đúng như thế. Nghĩa là **6/6 không phải là sáu bước mà `intent.md` viết
   nguyên văn**: bước 5 đã được định nghĩa lại giữa intent và spec. Người đọc `ship.md` cần
   biết điều này trước khi tin con số.

## What was measured

**`npm test`, 2026-09-23:** 60 test node + 419 test python, `OK`, xanh cả hai runtime.

**Bước 7 — đỏ trước khi có gì.** Chĩa vào `0.3.0` đang chạy: `POST /api/units` trả `405`,
bảng ghi **0 of 6**, và bước 1 chặn năm bước còn lại — đúng thứ `intent.md` đã đo. Với
checkout, `--dry` xanh ở **1 of 6** rồi dừng trước bước trả tiền đầu tiên.

**Bước 8 — vòng thật, `exit 0`.** Chạy 2026-09-22, repo dùng một lần
`https://github.com/baodq97/coscc-proof.git`, log đầy đủ ở `/tmp/proof0014.log` (file trên
máy này, không nằm trong repo). Tám claim `PASS`, không claim nào `FAIL`:

```
PASS  the product created 0001_a-problem-this-proof-invented and lists it on its own board
PASS  creating the unit put nothing into the repository's tree
PASS  the intent step ran and wrote intent.md from the brief
PASS  the product named this unit's branch and the workspace is on it (docs/a-problem-this-proof-invented)
PASS  every stage ran: spec, plan, impl, pr
PASS  all 5 transitions name the stage and the session that made them
PASS  after every stage, the repository's tree still holds nothing of coscc's
PASS  a pull request for docs/a-problem-this-proof-invented exists and is merged (.../pull/1)
    steps performed by the product: 6 of 6
```

Pull request: `https://github.com/baodq97/coscc-proof/pull/1`, `MERGED` lúc
`2026-09-22T17:01:08Z`, merge commit `a2cfb0d`, nội dung `README.md` `+1 -0` — đọc bằng
`gh pr view 1 --json state,mergedAt,mergeCommit,files`.

**Tiền, và nó là số đo chứ không phải ước lượng.** Năm session của vòng xanh:

| Stage | Bắt đầu → kết thúc | Thời gian | USD |
|---|---|---|---|
| `intent` | 16:50:26 → 16:50:46 | 20s | 0.1161 |
| `spec` | 16:50:48 → 16:52:21 | 1m33s | 0.2542 |
| `plan` | 16:52:23 → 16:54:41 | 2m18s | 0.3705 |
| `impl` | 16:54:43 → 16:59:38 | 4m55s | 1.8460 |
| `pr` | 16:59:40 → 17:02:15 | 2m35s | 0.6968 |
| | **16:50:26 → 17:02:15** | **11m49s** | **3.2836** |

Cộng hai lần tiêu trước đó trong cùng ngày cho cùng unit này: một vòng đầy đủ **hỏng** ở
`spec` (2 session, **$0.1630**, không ra artifact nào) và một lần dò riêng stage `spec` cho
kết quả **đạt** (1 session, **$0.0635**). **Tổng tiền `0014` đã tiêu: $3.5101.**

Trần theo `coscc/policy.py`: `impl` $5 và `pr` $3. Không trần nào bị chạm — `impl` dùng
$1.85/$5, `pr` dùng $0.70/$3.

**Nguồn của các con số tiền:** transcript của chính Claude Code dưới
`~/.claude/projects/-tmp-tmpxbzetiqe-work-proof-1790095823/`, trường `totalCostUSD`, năm
file `.jsonl`. **Không phải từ coscc** — xem `## What is still open` mục 1.

**Bundle:** dựng lại 2026-09-23 cho `http://127.0.0.1:8791`, reflex 0.9.12, 6 source file;
fingerprint ở `.web/build/client/.coscc-build.json`. Cần dựng lại vì `screens.py` và
`state.py` đổi ở `1ef1630`, nếu không thì ô `detail` mới không hiện trên trang thật.

## What is still open

1. **Sản phẩm không nói được một unit tốn bao nhiêu.** Journal có tiền từng run, nhưng
   `COS_DATA_DIR` của vòng proof là thư mục tạm và đã bị dọn khi app tắt, nên con số $3.2836
   ở trên phải lấy từ transcript của Claude Code chứ không phải từ coscc. Một cái bảng tiêu
   tiền mà không tổng được tiền theo unit là lỗ hổng, và nó nối thẳng với trần chi tiêu
   toàn-bảng còn nợ từ `0013`.
2. **`detail` giờ chảy ra `/api/timeline`.** Nội dung câu trả lời của một session đã trả tiền
   nay đọc được qua HTTP, và app này **không có login, không token, không password trên mọi
   route**, mặc định bind `0.0.0.0` (`0011`, cố ý). Đúng mối nguy `0013` `spec.md` C6 đã
   nêu, nay rộng hơn một bậc.
3. **Stage văn xuôi không tất định.** `spec` hỏng một lần vì thiếu dòng `Status:`, rồi lần dò
   riêng ngay sau đó lại đạt với cùng prompt. Chưa sửa — mới chỉ làm cho nó **chẩn đoán
   được** (mục 1 của `## What was built`).
4. **Cùng một bên mở và merge.** `write-pr` giờ chạy cả `gh pr create` lẫn `gh pr merge`.
   Không gate nào bị gỡ vì chưa từng có gate, nhưng đường đi ngắn lại một bậc và
   `.claude/CLAUDE.md` là nơi duy nhất nói ra điều đó.
5. **`REPLY_KEPT = 2000` là chọn, không phải đo.** Ghi ngay trong comment ở
   `coscc/runner.py:147-150`.
6. **Repo dùng một lần `baodq97/coscc-proof` vẫn còn trên GitHub.** Nó chứa một PR đã merge
   do agent viết. Xoá hay giữ là việc của `ship.md`.
7. **Nợ từ `0013`, chưa trả:** `pr.md` phải tự đứng được (C4), backup `pr.md` phòng mất DB,
   trần chi tiêu toàn bảng.
