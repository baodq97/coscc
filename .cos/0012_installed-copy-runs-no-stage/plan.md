# Plan: one place answers where the rules are, and the release refuses to ship without them
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

## Files that change

| Path | Gì | Requirement |
|---|---|---|
| `coscc/harness.py` | **(new)** chỗ duy nhất trả lời "luật nằm đâu", cộng hàm đọc một wheel xem có thiếu gì | R1, R5, R7 |
| `coscc/harness_test.py` | **(new)** | R1, R5 |
| `coscc/board.py` | `SCRIPT` ở dòng 31 chuyển sang hỏi `harness` | R1, R2 |
| `coscc/board_test.py` | test đang vá `board.SCRIPT` phải đổi theo | R2 |
| `coscc/runner.py` | `SKILLS` ở dòng 39 chuyển sang hỏi `harness`; `skill_for` (57-63) báo hỏng thay vì trả rỗng | R1, R3, R4 |
| `coscc/runner_test.py` | test cho lối từ chối mới | R3, R4 |
| `.gitignore` | thêm cây đóng gói, cạnh `coscc/_web/` ở dòng 2 | R7 |
| `.github/workflows/release.yml` | thêm bước copy; bước chặn (114-128) gọi script thay vì `grep` tại chỗ | R5, R6 |
| `scripts/check_wheel.py` | **(new)** CLI mỏng bọc hàm trong `harness.py`, chạy được cả ở CI lẫn trên máy | R5, C1 |
| `scripts/verify_0012.py` | **(new)** proof | R9 |
| `docs/install.md` | `## Prerequisites` khai `node` | R8 |
| `.claude/rules/coscc-app.md` | bảng proof ở `## The proofs, and what each one costs` dừng ở `verify_0006` | R9 |

Mọi đường dẫn "không (new)" đã kiểm tra là có thật, ngày 2026-09-22.

**Không đổi, và đây là cố ý:** `coscc/policy.py`, `coscc/service.py`, `coscc/api.py`,
`coscc/screens.py`, `coscc/state.py`. Ràng buộc 2 của `intent.md` cấm đụng bảng grant và
stage list; và `spec.md` R2 nói Board đổi *nơi lấy đường dẫn*, không đổi dữ liệu đi qua
service. Nếu một trong năm file này phải sửa thì giả định của plan sai và plan phải được
viết lại trước, không phải sửa kèm.

## Order of work

1. **`coscc/harness.py` + test.** Hằng số đường dẫn, quy tắc chọn (bản đóng gói thắng khi
   có mặt, ngược lại lùi về `.claude/` của checkout — cùng hình với `coscc/frontend.py:106-113`),
   một exception cho "không tìm thấy luật", và `missing_from_wheel(path)` trả danh sách thứ
   thiếu trong một wheel. Chưa ai gọi. **Kiểm được:** `npm test` xanh, không hành vi nào đổi.
2. **`coscc/board.py` dùng nó.** Xoá công thức `parent.parent` ở dòng 31. Không đụng
   `read()` ngoài chỗ lấy `SCRIPT`. **Kiểm được:** `npm test` xanh.
3. **`coscc/runner.py` dùng nó, và `skill_for` báo hỏng.** Lỗi ném ra từ trong
   `build_prompt`, tức trước `journal.started` (runner.py:238) và trước
   `self.sessions.stream` (runner.py:249) — đó là cách R4 đạt "0 request tới SDK" bằng cấu
   trúc chứ không bằng lời hứa. **Kiểm được:** `npm test` xanh, có test khẳng định thứ tự đó.
4. **`.gitignore` + bước copy trong `release.yml`.** Copy cả thư mục `scripts/` và
   `skills/`, không liệt kê tên file (C4). **Kiểm được:** `git status` sạch sau khi copy tay.
5. **`scripts/check_wheel.py`, và chạy nó hai lần.** Dựng wheel *không* copy → script phải
   báo thiếu, exit khác 0. Copy rồi dựng lại → exit 0. **Kiểm được:** hai lần chạy cho hai
   kết quả khác nhau; một script chỉ từng xanh thì chưa chứng minh được gì.
6. **`docs/install.md` và bảng proof trong `.claude/rules/coscc-app.md`.** **Kiểm được:** đọc.
7. **`scripts/verify_0012.py`, chạy khi bản cài vẫn là `v0.2.2` từ release.** Phải **exit 1**.
   Đây là bước quan trọng nhất của plan: một proof chưa từng đỏ là một proof chưa ai biết nó
   đo được gì (`scripts/verify_0003.py:8-14` giữ exit 1 tách khỏi exit 2 đúng vì lý do này).
8. **Dựng wheel đầy đủ, cài đè lên bản cài, chạy lại proof.** Phải **exit 0**.
   Cài bằng `uv tool install --force dist/coscc-0.2.2-py3-none-any.whl` — **giữ nguyên số
   version**. Bump version là việc của ship stage, và `cos.mjs check-version` canh năm chỗ
   phải khớp nhau, nên bump ở đây là tự mở một lỗi khác.
8b. **Đi lệch khỏi plan, ghi lúc nó xảy ra (2026-09-22).** Bước 8 chạy xong thì packaging
   đúng — claim 1, 3, 4 xanh — nhưng Board vẫn đỏ với
   `could not run node: No such file or directory: 'node'`. Không phải lỗi đóng gói: `node`
   **có** trên máy, ở `~/.nvm/versions/node/v24.20.0/bin/node`, còn service systemd chạy với
   PATH mặc định của systemd user, nơi không bao giờ có nvm. Một cái bẫy thật cho bất kỳ ai
   cài node bằng nvm, và không thứ gì trong repo này nói tới nó.

   Ba việc thêm, đều nằm trong ràng buộc của `spec.md` (không đụng nội dung luật, stage list
   hay `GRANTS`):
   - `coscc/board.py` nêu luôn PATH đã tìm trong lời từ chối. Thông báo cũ gửi người đọc đi
     tìm một chương trình không hề thiếu.
   - `docs/install.md` ghi cái bẫy nvm và một dòng sửa.
   - `scripts/verify_0012.py` phân biệt "service không chạy được node" là **exit 2**, không
     phải một claim đỏ — đó là một cái máy proof không đo được, không phải một bản phát hành
     hỏng (`scripts/verify_0003.py:8-14`).

   Rồi làm đúng thứ tài liệu vừa viết, trên chính máy này, và đó là lần đầu dòng hướng dẫn
   đó được thử.

9. **`impl.md`**, ghi số đo thật của bước 7, 8 và 8b.

## Risks

Xếp theo bán kính, và mục 1 là mục muốn không phải viết ra.

1. **Bước copy sống trong CI, nên unit này không sửa được lỗi cho một wheel dựng bằng tay.**
   `uv build` trên máy cá nhân vẫn ra một wheel cài được, thiếu luật, im lặng — đúng thứ
   `v0.2.2` đang là. `spec.md` C1 đã ghi. Điều plan này làm được: đưa phép kiểm ra thành
   script chạy được ở cả hai nơi. Điều nó **không** làm được: ép ai đó chạy. Thứ duy nhất
   thật sự chặn là bước trong `release.yml`, và nó chỉ canh đường release. **Thấy nó hỏng
   khi:** ai đó phát hành bằng tay.
2. **`uv_build` có thể không đưa `coscc/_harness/` vào wheel vì lý do mình chưa biết.** Nó
   đã từ chối symlink thư mục (đo 2026-09-22: `failed to read ...: Is a directory`). Bằng
   chứng ngược lại: `coscc/_web/` cũng gitignore và vẫn vào wheel được (`release.yml:81-94`).
   **Thấy nó hỏng khi:** bước 5 chạy `check_wheel.py` trên wheel vừa dựng.
3. **`skill_for` báo hỏng có thể làm đỏ test cũ mình chưa đọc hết.** Hôm nay nó trả rỗng và
   `build_prompt` bỏ qua — một test dựa vào đường im lặng đó sẽ đỏ. Đó là tín hiệu đúng, không
   phải hỏng. **Thấy nó hỏng khi:** bước 3, `npm test`.
4. **Proof đòi `node` và một service đang chạy.** Thiếu một trong hai thì đúng quy ước phải
   là exit 2, không phải exit 1. Nhầm hai cái này biến "chưa có máy để đo" thành "bản phát
   hành hỏng" — `scripts/verify_0003.py:8-14`.
5. **Sau bước 8 máy này không còn chạy artifact đã publish.** Bản cài thành wheel dựng tại
   chỗ, cùng số `0.2.2`, khác nội dung. Ghi vào `impl.md`, và ship stage phải phát hành bản
   thật để hai thứ khớp lại.
6. **`.claude/skills/` đi vào một wheel công khai.** Chúng đã công khai trong repo này nên
   không lộ gì mới, nhưng bước copy phải copy đúng `scripts/` và `skills/` — không phải cả
   `.claude/`, vì `settings.local.json` sống ở đó (`.gitignore:19`). **Thấy nó hỏng khi:**
   `check_wheel.py` kiểm luôn rằng wheel *không* chứa `settings`.

## Proof

```sh
npm test
COS_URL=http://127.0.0.1:8790 uv run python scripts/verify_0012.py
```

Đạt khi: `npm test` xanh cả hai runtime, **và** `verify_0012.py` exit 0 — sau khi cùng file
đó đã exit 1 ở bước 7 trên bản cài `v0.2.2`. Hai lần chạy đó là bằng chứng; một lần xanh thì
không.

`verify_0012.py` khẳng định, trên interpreter và service của **bản đã cài**, không phải của
checkout:

1. `harness` giải được `cos.mjs` và ≥1 skill, và đường dẫn giải ra **không nằm trong** cây
   checkout này (R1);
2. `GET /api/board?cwd=<ws>` khớp `node .claude/scripts/cos.mjs --root <ws> status --json`
   trên tập `(unit, stage, status)` (R2);
3. `build_prompt` cho stage `spec` ≥ 18.000 ký tự và có khối `# The rules for this stage` (R3);
4. luật không giải được thì `build_prompt` ném lỗi, và lỗi nêu đường dẫn đã tìm (R4);
5. `docs/install.md` khai `node` (R8).

Exit 2 — không phải 1 — khi: không tìm thấy bản cài, không có service ở `COS_URL`, không có
`node`, hoặc không có workspace nào để đo.

**Proof này không chứng minh được R8 trên máy sạch**, và nó phải in ra câu đó. Máy chạy nó
có `node` vì checkout dùng `npm test`; một máy cài theo `docs/install.md` thì không.
Chứng minh đầy đủ cần một target qua SSH như `scripts/verify_0011.py` dùng
(`COS_PROOF_TARGET`). `spec.md` C5 ghi nguyên văn ranh giới này.
