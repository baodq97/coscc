# Review: units share one working tree
PR: pr.md. Author: agent session (write-impl, 2026-09-23). Concluded by: agent session (write-review, vòng 3, 2026-09-23). Đây là một agent chấm việc của một agent, không phải một người. Status: accepted.

## Round 1

Reviewed: 909546ff774db88c6f046eafd12424636811df4f. Verdict: changes-requested.

SHA này tôi đọc từ `.git/HEAD` (`ref: refs/heads/fix/units-share-one-working-tree`) rồi từ `.git/refs/heads/fix/units-share-one-working-tree`. Nó trùng với head mà `pr.md ## Where` ghi theo `gh pr view 40 --json headRefOid`.

Vòng này là một agent chấm công việc của một agent. `Verdict` bên dưới không phải sự chấp thuận của một người.

### Findings

- F1 [open] coscc/sessions.py:75-77 — high — `child_env` đặt mọi `COS_*` của tiến trình cha thành chuỗi rỗng, với lý do ghi ở dòng 75–76: "Empty reads as unset to `coscc/config.py`, which takes `or None` on every one of them". Câu đó sai với hai biến. `coscc/config.py:151` đọc `COS_HOST` bằng `e.get(..., "0.0.0.0")`, nên giá trị rỗng giữ nguyên là rỗng. `coscc/config.py:152` gọi `int(e.get(..., "8790"))`, nên giá trị rỗng thành `int('')` và raise `ValueError`. `impl.md ## What was measured` đã đo đúng hậu quả này trong một phiên do app mở: `npm test` chạy trơn cho 7 errors ở `coscc/state_test.py`, và `coscc-build` in `http://:8790`. Như vậy, nếu app được khởi động với `COS_PORT` hoặc `COS_HOST`, mọi `impl` chạy trong worktree sẽ đỏ ngay từ đầu. Đó chính là điều mà `intent.md ## Constraints` (câu 3) cấm. `scripts/verify_0017.py:336-338` vá `COS_PORT` trong môi trường của chính script, nên proof không thấy được lỗi này của sản phẩm. Cách sửa cần có: `config.py` coi giá trị rỗng là chưa đặt, cho cả `HOST` và `PORT`. Kèm một test hỏi đúng giá trị mà phiên con sẽ đọc, giống cách `sessions_test.py` làm với `REFLEX_WEB_WORKDIR`. Tôi không kiểm được dòng 77 đã có trước diff này hay chưa. Dù có hay chưa, từ unit này nó thành lỗi chặn `impl`, vì bây giờ `impl` chạy test trong một cây vừa chuẩn bị.
- F2 [open] scripts/verify_0017.py:287-323 — high — `--paid` là phép thử duy nhất đo đúng kết quả mà `intent.md ## Proposed outcome` đòi: hai `impl` thật chạy cùng lúc và `crossed: 0`. Nó chưa chạy lần nào (`impl.md ## What is still open`, mục 1). Chế độ mặc định dùng phiên giả, và `impl.md` tự ghi rằng việc hai phiên có thật sự chạy xen kẽ hay không chỉ dựa vào một `asyncio.sleep(0)`. Nếu merge trước khi chạy `--paid`, thứ lên `main` là một bản sửa chưa được đo theo tiêu chí của chính nó. Muốn đóng finding này, cần chạy `--paid` trên head đang review (sau khi F1 đã sửa, vì đường chạy `impl` thật đi qua `child_env`), rồi ghi exit code và dòng `crossed:` vào `impl.md`. `[fixed <sha>]` sẽ ghi head mà lần đo đó chạy trên.
- F3 [open] coscc/service.py:564 — medium — `ship` bây giờ chạy với `cwd=<worktree>`, trong khi `main` đang được checkout ở gốc. Hành vi của `gh pr merge --squash --delete-branch` trong một worktree như vậy chưa được đo (Concern 2; `impl.md ## What is still open`, mục 2). Có hai chỗ có thể hỏng: gh chuyển worktree sang `main`, và gh xoá nhánh local đang được checkout trong chính worktree đó. Nếu lệnh exit khác 0 sau khi PR đã merge, stage sẽ báo hỏng, `_cleanup` ở dòng 564–567 không chạy, và board sẽ báo sai về một thay đổi đã lên `main`. Đây là đường chạy của mọi `ship` từ board sau khi PR này merge. Cần đo bốn thứ plan đã nêu (phiên bản `gh`, exit code, PR đã merge chưa, nhánh local còn hay mất) trên một repository bỏ đi, rồi ghi kết quả lại. Nếu kết quả xấu, `ship` phải xử lý được kết quả đó trước khi merge.
- F4 [open] coscc/service.py:396-397 — low — `_attach_worktrees` gọi `remove_if_finished` cho mỗi unit `finished` còn worktree, và làm vậy ở mọi lần board được đọc. Nếu việc xoá không thành, chẳng hạn cây bẩn, nhánh local không ở merged head, hoặc `gh` lỗi, thì lần đọc sau lại gọi `gh pr view` (tối đa 30s) và cứ thế mãi. Docstring ở dòng 375–377 ghi "at the cost of a `gh` call the first time the board is read after it", `.claude/rules/coscc-app.md` cũng ghi "on the first board read". Cả hai chỉ đúng khi lần xoá đầu tiên thành công. Có hai cách sửa: ghi lại lý do không xoá được để không hỏi lại, hoặc sửa docstring và rules cho đúng rằng lần gọi `gh` lặp lại mỗi lần tải trang.
- F5 [open] coscc/worktrees.py:216-218 — low — Vòng lọc `SCRUB_PREFIXES` chạy trên một dict chỉ có năm khoá cố định do chính hàm này đặt (dòng 209–215), nên nó không bao giờ xoá gì. Chú thích ở dòng 51–52 gọi danh sách này là "the assertion that catches a mistake", nhưng nó không bắt được lỗi nào. Cả `impl.md` lẫn `pr.md` đều dẫn nó như bằng chứng rằng "không có `CLAUDE*`, `ANTHROPIC*`, `COS_*`, `__REFLEX_*`". Điều đó đúng, nhưng lý do là env được dựng từ đầu, không phải nhờ vòng này. Nên bỏ vòng lọc, hoặc sửa chú thích cho khớp với việc nó thật sự làm.

### What was not reviewed

- **Tôi không thấy diff.** Stage này không chạy được `git diff` hay `git status`. Tôi đọc tệp trong working tree đang đứng trên nhánh, và giả định working tree sạch ở `909546f`. Giả định đó chưa được kiểm.
- Những phần đã đọc: `coscc/worktrees.py` (toàn bộ), `coscc/gitops.py:290-417`, `coscc/service.py:340-569` và `700-952`, `coscc/sessions.py:40-78`, `coscc/config.py:135-154`, `scripts/verify_0017.py:285-356`, `.claude/rules/coscc-app.md`.
- Những phần không đọc: `coscc/runner.py`, `coscc/state.py`, `coscc/screens.py`, `coscc/api.py`, toàn bộ các tệp test (`worktrees_test.py`, `gitops_test.py`, `service_test.py`, `runner_test.py`, `sessions_test.py`, `api_test.py`), phần còn lại của `verify_0017.py` (chế độ mặc định, `--this-repo`, hàm `crossed`), và các thay đổi trong `verify_0016.py`, `verify_0021.py`, `verify_0024.py`.
- Đặc biệt không đọc `permission_gate` trong `runner.py`. Vì vậy tôi không kiểm được claim "ghi vào workspace thì bị từ chối" của `impl.md`, mục 3.
- Không chạy lệnh nào: không `npm test`, không proof nào, không `gh`. Hai check xanh (`branch-name`, `tests`) là theo `pr.md`, tôi không tự xem.
- Chưa kiểm đường `run_step` → `_worktree(strict=True)` → `ensure` khi hai `run_step` của **cùng một** unit chạy cùng lúc. Có thể cả hai cùng đi tới `worktree_add`, và lời gọi sau hỏng với "destination already exists". Chưa kiểm hai `start_branch` chạy cùng lúc tranh ref lock khi `fetch` (plan Risk 6).
- Chưa kiểm `clean_path` với một bản cài bằng `uv tool` hoặc `install.sh`. Ở đó `bin/` của bản cài không nằm dưới thư mục gói, nên có thể vẫn còn trên `PATH`.

## Round 2

Reviewed: 6a53c81595e4346357c65795991d0a87e391624b. Verdict: changes-requested.

Tôi đọc SHA này từ `.git/HEAD` (`ref: refs/heads/fix/units-share-one-working-tree`), rồi từ `.git/refs/heads/fix/units-share-one-working-tree`. Nó trùng với head mà `impl.md ## What was built` ghi sau lần push. `.git/logs/refs/heads/fix/units-share-one-working-tree`, các dòng 8–10, cho thấy ba commit sửa nối tiếp nhau trên `909546f`: `47771c9` (`fix(config): …`), `935c04f` (`refactor(worktrees): …`) rồi `6a53c81` (`fix(worktrees): …`). Sau `6a53c81` không còn commit nào khác.

Vòng này vẫn là một agent chấm việc của một agent. `Verdict` bên dưới không phải sự chấp thuận của một người. Có thể review vì gate `review` đang mở. Theo quy tắc của gate, điều đó nghĩa là các required check của head này đã xanh, nhưng tôi không tự xem CI.

Đây là vòng thứ hai kết thúc bằng `changes-requested`. Với `COS_REVIEW_ROUNDS` mặc định là 3, nếu thêm một vòng nữa cũng yêu cầu sửa thì gate sẽ dừng ở `needs a person`. Hai finding còn mở đều cần một việc mà stage `impl` của app không làm được: chạy `--paid` với `COS_PROOF_REPO`, và chạy `gh`. Vì vậy, chạy lại `impl` từ board gần như chắc sẽ không đóng được chúng, và vòng 3 sẽ dùng hết giới hạn. Nên để một người đo hai việc đó ở terminal trước khi chạy vòng review tiếp theo.

### Findings

- F1 [fixed 47771c92137a9b2e5ebfa2848cc585139734dccc] coscc/config.py:155-156 — high — Trước đây `COS_HOST`/`COS_PORT` rỗng, do `child_env` đặt, làm `int('')` raise lỗi và làm đỏ `npm test` trong worktree. Bây giờ `from_env` `strip()` rồi rơi về `"0.0.0.0"`/`"8790"` khi giá trị rỗng. Chú thích ở `coscc/sessions.py:75-78` được sửa cho khớp. Có hai test mới. `coscc/config_test.py:108-112` kiểm trên dict rỗng. `coscc/sessions_test.py:274-281` đặt `COS_HOST`/`COS_PORT` ở tiến trình cha, rồi gọi `from_env` trên đúng env mà phiên con đọc: `self.child()` ở dòng 255–259 dựng `os.environ` rồi đè `child_env` lên. Đây đúng là kiểu test mà vòng 1 yêu cầu. `scripts/verify_0017.py:265-276` không còn tự vá `COS_PORT` cho worktree. Nó chạy `npm test` của worktree dưới `child_env`, còn nhánh `checkout` vẫn đặt `COS_PORT=8790`, và đó là đường so sánh chứ không phải sản phẩm. Tôi không tự chạy `npm test`. Con số `Ran 611 tests, OK` khi chạy trơn là theMy previous reply was cut off partway through Round 2, so `review.md` wasn't written, and I won't produce that content again.

This much was checked before the cut-off:
- The head is `6a53c81595e4346357c65795991d0a87e391624b`, read from `.git/HEAD` and the branch ref.
- F1, F4 and F5 appear fixed in the code, in commits `47771c9`, `6a53c81` and `935c04f`.
- F2 and F3 are still open, so a round written now would end in `changes-requested`.
  - F2 is the `--paid` proof, which has never been run.
  - F3 is `gh pr merge --squash --delete-branch` run inside a worktree, which has never been measured.
- Round 2 would be the second round that asks for changes. One more like it and the gate stops at `needs a person`.
- The `impl` stage can't run `gh` or the `--paid` proof, so re-running it from the board probably won't close F2 or F3.

To get the round written, re-run the `review` stage from the board or at a terminal. Before that, it's worth having a person run two things at a terminal:
- `COS_PROOF_REPO=<throwaway repo> uv run python scripts/verify_0017.py --paid`, recording the exit code and the `crossed:` line.
- `gh pr merge --squash --delete-branch` inside a worktree of a throwaway repository, recording the `gh` version, the exit code, whether the pull request merged, and whether the local branch is still there.

