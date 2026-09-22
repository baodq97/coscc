# Impl: the app carries its rules, and the release refuses to ship a wheel without them
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Chín commit trên `fix/installed-copy-runs-no-stage`, theo đúng thứ tự plan đặt ra.

**`432dc9c` — `coscc/harness.py`, chỗ duy nhất trả lời "luật nằm đâu".** `root()` chép hình
của `coscc/frontend.py:106-113`: bản đóng gói thắng khi có mặt, ngược lại lùi về `.claude/`
của checkout. Kèm `MissingRules` và `wheel_complaints()` — hàm đọc một wheel và trả về danh
sách thứ nó thiếu. Chưa ai gọi trong commit này; 13 test đi cùng.

**`09d5615` — `board.py` và `runner.py` hỏi nó.** Hai công thức `parent.parent` ở
`board.py:31` và `runner.py:39` biến mất; đó là hai bản sao của cùng một phép tính, và là lý
do một thiếu sót đóng gói hiện ra thành hai triệu chứng trông không liên quan. `skill_for`
đổi từ trả `""` sang ném `MissingRules`. Chỗ ném nằm trong `build_prompt`, mà `Runner.run`
gọi **trước** `journal.started` và **trước** `Sessions.stream` — nên "0 request tới SDK" của
`spec.md` R4 đúng do cấu trúc, không do lời hứa. Test đếm cả hai: `sessions.streams == 0` và
journal rỗng.

**`d221f50` — đường phát hành.** `release.yml` có thêm bước copy `.claude/scripts` và
`.claude/skills` vào `coscc/_harness/`; `.gitignore` thêm một dòng cạnh `coscc/_web/`. Bước
chặn wheel trước đây là hai lệnh `grep` viết thẳng trong YAML và **chỉ canh frontend** — đó
là lý do một wheel không có harness đi qua nó ba lần. Giờ nó gọi
`scripts/check_wheel.py`, mỏng, bọc `wheel_complaints()`. `docs/install.md` sửa câu
"You do not need Node"; bảng proof trong `.claude/rules/coscc-app.md` dừng ở `verify_0006`,
nay có thêm `verify_0011` và `verify_0012`.

**`7049771` — cái bẫy mà lần chạy test đầu tiên tự tìm ra.** Xem `## What was measured`.

**`b0471a7` — `scripts/verify_0012.py`.** Đo bản **đã cài**, không đo checkout. Chạy
interpreter của bản cài với `cwd=/`: Python đặt thư mục làm việc lên `sys.path` cho `-c`,
nên chạy từ gốc repo sẽ import `coscc` của checkout và đo đúng thứ chưa bao giờ hỏng.

**`f896e7b` — nguyên nhân thứ hai, và nó không phải lỗi đóng gói.** Xem dưới.

## Where the plan was departed from

**Một chỗ, ghi vào `plan.md` ngay lúc nó xảy ra** (bước `8b`, commit `f896e7b`).

Sau bước 8, packaging đã đúng: claim 1, 3, 4 xanh. Board vẫn đỏ, với
`could not run node: [Errno 2] No such file or directory: 'node'`. `node` **có** trên máy —
`/home/bd/.nvm/versions/node/v24.20.0/bin/node`, `command -v node` trả lời ngay trong
terminal — nhưng service systemd chạy với PATH mặc định của systemd user, và nvm nằm dưới
`$HOME`, được đưa vào PATH bởi file khởi động shell mà một user service không đọc. Đo
2026-09-22: PATH của tiến trình service là
`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin`.

Ba việc thêm, đều nằm trong ràng buộc 2 của `intent.md` (không đụng nội dung luật, stage
list hay `policy.GRANTS`):

1. `coscc/board.py` nêu luôn PATH đã tìm trong lời từ chối. Thông báo cũ chỉ nói "could not
   run node", và nó gửi người đọc đi tìm một chương trình không hề thiếu.
2. `docs/install.md` ghi cái bẫy nvm cùng một dòng sửa trong file env.
3. `scripts/verify_0012.py` coi "service không chạy được node" là **exit 2**, không phải một
   claim đỏ. Đó là một cái máy proof không đo được, không phải một bản phát hành hỏng —
   `scripts/verify_0003.py:8-14`.

Rồi làm đúng thứ tài liệu vừa viết, trên chính máy này. Đó là lần đầu dòng hướng dẫn ấy được
thử, và nó là thứ khiến claim 2 chuyển xanh.

**Không đi lệch ở đâu khác.** Năm file `plan.md` hứa không đụng — `policy.py`, `service.py`,
`api.py`, `screens.py`, `state.py` — không file nào bị sửa.

## What was measured

Mọi số dưới đây đo ngày 2026-09-22, trên máy này, WSL2 Linux 6.18.33.2.

**`npm test`** — `297 tests ... OK` cho Python, `pass 60 / fail 0` cho node. Chạy lại sau
mỗi bước; đỏ đúng một lần, xem mục dưới.

**`check_wheel.py` trên wheel thật, hai lần khác nhau.** Trước bước copy:

```
coscc-0.2.2-py3-none-any.whl: no coscc/_harness/scripts/cos.mjs — the Board would answer 400 on every read
coscc-0.2.2-py3-none-any.whl: no coscc/_harness/skills/*/SKILL.md — every step would run without its rules
exit=1
```

Sau bước copy: `carries a frontend and a harness`, exit 0. Hai kết quả khác nhau từ cùng một
script là bằng chứng; một lần xanh thì không. Điều này cũng loại `plan.md` Risk 2:
`uv_build` **có** đưa `coscc/_harness/` vào wheel dù thư mục bị gitignore, y như `coscc/_web/`.

**`verify_0012.py`, cùng một file, cùng một máy, hai bản cài.**

Trên `v0.2.2` tải từ GitHub release — **exit 1**, 4 trên 5 claim đỏ:

| Claim | Kết quả |
|---|---|
| resolves cos.mjs + skills | `ImportError: cannot import name 'harness'` |
| board khớp gate | `400 the harness script is missing: .../site-packages/.claude/scripts/cos.mjs` |
| prompt ≥ 18.000 ký tự | **14.320 ký tự**, không có khối rules |
| thiếu luật thì dừng | `ImportError` |
| `docs/install.md` khai node | PASS |

Sau khi cài wheel vá — **exit 0**, cả 5 xanh: 9 skill giải được ngoài checkout, **88 dòng
`(unit, stage, status)`** của Board khớp gate của workspace, prompt qua ngưỡng, lời từ chối
nêu đủ đường dẫn đã tìm.

Con số `14.320` ở đây lệch 7 ký tự so với `14.313` trong `intent.md`: bảng của intent gọi
`build_prompt` với `artifact=""`, proof gọi với `artifact="spec.md"`. Cùng một hiện tượng,
khác một tham số — ghi ra để không ai đi tìm một hồi quy không tồn tại.

**Cái bẫy mà lần chạy test đầu tiên tự tìm ra** (`7049771`). `npm test` đỏ đúng một lần,
ở `test_the_resolved_script_is_the_one_the_repo_commits`:

```
AssertionError: PosixPath('.../coscc/_harness/scripts/cos.mjs')
            != PosixPath('.../.claude/scripts/cos.mjs')
```

Không phải lỗi code. `coscc/_harness/` còn sót lại từ lần dựng wheel trước đó, và nó **thắng**
đúng như thiết kế — nghĩa là một checkout có cây đóng gói cũ sẽ đọc luật cũ trong khi
`git status` vẫn sạch, vì cả hai cây đều gitignore. Sửa cả ba mặt: test tự xoá
`PACKAGE_HARNESS` thay vì tin vào trạng thái thư mục; bước copy trong `release.yml` bắt đầu
bằng `rm -rf`; và cái bẫy được viết vào `.claude/rules/coscc-app.md`, mục `## Hazards`, nơi
một session sẽ đọc.

**Chưa chạy:** `scripts/verify_0011.py`. Nó cần `COS_PROOF_TARGET` — một máy khác qua SSH mà
nó reboot hai lần — và không có máy nào được cấp cho lần này. Unit này sửa `docs/install.md`
và `release.yml`, hai thứ `0011` đo, nên nói rằng bản cài vẫn sống sau reboot là chuyện chưa
ai kiểm trong unit này.

## What is still open

1. **`spec.md` C1 vẫn nguyên.** Bước copy sống trong `release.yml`. `uv build` gõ tay trên
   máy cá nhân vẫn ra một wheel cài được, thiếu luật, im lặng. `check_wheel.py` giờ chạy được
   ở cả hai nơi, nhưng không gì ép ai chạy nó.
2. **Proof không chứng minh được nửa "máy sạch" của R8.** Máy này có `node` vì `npm test` cần.
   `spec.md` C5; proof in ra câu đó thay vì để exit 0 hàm ý nhiều hơn nó đo.
3. **Máy này đang chạy một wheel dựng tại chỗ, vẫn mang số `0.2.2`, khác nội dung với bản
   `v0.2.2` trên GitHub.** `plan.md` Risk 5. Chỉ khớp lại khi ship stage phát hành bản thật.
4. **File env trên máy này đã bị sửa tay** để thêm PATH có nvm. Đó là làm theo tài liệu, không
   phải lách, nhưng nó là trạng thái của một máy chứ không phải của repo.
5. **Bản cài cũ không tự sửa.** Người đang chạy `v0.2.2` phải chạy lại dòng install; đó là
   đường cập nhật duy nhất (`docs/install.md` mục `## Update`). Fix này tới tay họ khi có
   release mới.
6. **`intent.md` OQ1 và OQ4 chưa trả lời.** Hướng engine Python thay `cos.mjs` sẽ xoá phần
   đóng gói ở đây; và sau unit này repo có hai đường phân phối cùng một bộ luật, nên câu
   "copy `.claude/` là xong" trong `.claude/CLAUDE.md` cần xem lại bằng một unit `docs`.
7. **Mới nảy 2026-09-22, chưa có unit:** chạy nhiều intent song song bằng `git worktree`, mỗi
   unit một cây theo đúng branch mà `unit-branch` đã suy ra. Thứ phải quyết trước tiên là
   việc cấp số: `cos.mjs new-path` đọc thư mục `.cos/` để cấp, nên hai worktree ở hai commit
   sẽ cùng cấp một số.
