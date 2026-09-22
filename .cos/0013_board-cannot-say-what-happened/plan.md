# Plan: the state set first, then the log, then the history that fills it
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

## Files that change

| Path | Gì | Requirement |
|---|---|---|
| `coscc/states.json` | **(new)** định nghĩa mặc định: tám stage, các `Status`, chuyển nào hợp lệ | R6 |
| `coscc/states.py` | **(new)** nạp định nghĩa; không nơi nào khác được hardcode tên trạng thái | R6 |
| `coscc/states_test.py` | **(new)** gồm test nạp một tập **khác** và chạy hết, không sửa Python | R6 |
| `coscc/data.py` | `SCHEMA_VERSION` 1 → 2, thêm hai bảng vào `_SCHEMA` (dòng 75-118) | R1, R3, R5 |
| `coscc/data_test.py` | một DB v1 có sẵn mở lên và có thêm bảng mới | R1 |
| `coscc/history.py` | **(new)** ghi thêm-only; và phép chiếu "đang ở đâu" | R1, R3, R4 |
| `coscc/history_test.py` | **(new)** | R1, R3, R4 |
| `coscc/backfill.py` | **(new)** đọc lịch sử git của một `.cos/`, sinh chuyển trạng thái | R2 |
| `coscc/backfill_test.py` | **(new)** | R2 |
| `coscc/service.py` | một phương thức đọc lịch sử unit | R8 |
| `coscc/service_test.py` | | R8 |
| `coscc/api.py` | một route JSON | R8 |
| `coscc/api_test.py` | | R8 |
| `scripts/check_wheel.py` → `coscc/harness.py` | `states.json` phải có trong wheel | R6 |
| `scripts/verify_0013.py` | **(new)** proof | R8 |
| `.claude/rules/coscc-app.md` | thêm dòng vào bảng proof | R8 |

Mọi đường dẫn "không (new)" đã kiểm tra là có thật, 2026-09-22.

**Không đổi, và đây là cố ý:** `coscc/board.py`, `.claude/scripts/cos.mjs`, `coscc/policy.py`,
`coscc/runner.py`, `coscc/screens.py`, `coscc/state.py`, `coscc/config.py`. Board vẫn đọc
`cos.mjs` (`intent.md` ràng buộc 3); đường đọc mới đặt cạnh. `config.py` đặc biệt: định nghĩa
trạng thái nạp từ **file truyền vào**, không phải từ một biến môi trường mới — thêm knob thứ
năm là mở lại một quyết định `coscc/policy.py:3-8` đã đóng.

## Order of work

1. **`states.json` + `states.py` + test.** Định nghĩa là dữ liệu; hàm nạp nhận đường dẫn,
   mặc định là bản đóng gói. **Kiểm được:** `npm test` xanh, chưa ai gọi, và đã có test nạp
   một tập trạng thái khác — vế "flex" của R6 tồn tại **trước** khi có gì phụ thuộc vào tập
   mặc định, chứ không được thêm vào sau cho đủ.
2. **`data.py`: version 2 và hai bảng.** `_create` chạy lại toàn bộ `_SCHEMA` khi
   `user_version` thấp hơn (`coscc/data.py:286-310`), và mọi câu lệnh đều `IF NOT EXISTS`,
   nên không cần viết migration riêng. **Kiểm được:** mở `~/.cos/cos.db` hiện có (có
   `workspaces`, `runs` 0 dòng) và thấy version thành 2 cùng hai bảng mới, không mất dữ liệu.

   **Departure, 2026-09-22 (bước 2).** Đo trên **bản sao** của `~/.cos/cos.db`, không phải
   bản gốc. Lý do chính là Risk 3 xảy ra ngay lúc đo: bản `v0.2.3` đang cài và đang chạy như
   systemd service dùng đúng file đó, và nâng nó lên version 2 là làm app đang chạy ném
   `Incompatible` cho tới khi có bản mới. Bản sao chứng minh đúng cùng một điều — một DB v1
   do bản đã phát hành ghi ra thì nâng được và không mất dòng nào — mà không làm hỏng thứ
   đang chạy. DB thật sẽ được nâng khi bản mang thay đổi này được cài.
3. **`history.py`: ghi thêm-only và phép chiếu.** **Kiểm được:** test xoá dòng chuyển trạng
   thái cuối của một unit và thấy trạng thái hiện tại lùi về giá trị trước — đó là R1, và nó
   chỉ xanh được nếu không tồn tại cột trạng thái nào.
4. **`backfill.py`: từ git ra chuyển trạng thái.** Chỉ đọc. **Kiểm được:** chạy trên repo
   này và ra số ≥ số mà một phép đếm độc lập trên git cho.
5. **`service.py` + `api.py`: đường đọc.** Route dịch request thành một lời gọi service, và
   không quyết định gì (`coscc/service.py:3-10`). **Kiểm được:** `npm test`.
6. **`scripts/verify_0013.py`, chạy khi chưa backfill.** Phải **exit 1** — nó thấy 0 sự kiện
   trong khi git nói có hơn 40.
7. **Chạy backfill, chạy lại proof.** Phải **exit 0**.
8. **`states.json` vào wheel.** Thêm vào `wheel_complaints` và chạy `check_wheel.py` hai lần:
   wheel thiếu nó phải đỏ. `0012` vừa tốn một unit vì đúng lớp lỗi này; dùng lại thứ nó xây.
9. **`impl.md`**, ghi số đo thật của bước 6, 7 và 8.

## Risks

Xếp theo bán kính. Mục 1 là mục muốn không phải viết ra.

1. **Bộ nhập suy trạng thái từ chữ `Status:` — đúng cơ chế mà R1 bác bỏ.** `spec.md` cho
   phép, vì nó là cách *sinh dữ liệu cho log* chứ không phải cách *đọc trạng thái*. Nhưng nếu
   nó suy sai thì con số ra sai, và không có nguồn thứ ba để biết. Giảm nhẹ: proof tính kỳ
   vọng **độc lập, trực tiếp từ git, lúc chạy** — không dùng lại code của bộ nhập. Hai đường
   tính cùng ra một số là bằng chứng; một đường thì không.
2. **Con số không phải hằng số, và nó đã đổi trong lúc viết unit này.**
   `intent.md` đo **39** ngày 2026-09-22. Đo lại cùng ngày, sau khi `0013` có `intent.md` và
   `spec.md`: **41** — 58 artifact đã chốt, 24 bị viết lại. Hai sự kiện mới **chính là
   `intent.md` và `spec.md` của unit này bị sửa sau khi đã accepted**, tức unit đo việc viết
   lại vừa tự sinh ra hai mẫu của nó. Hệ quả cứng: **proof không được hardcode con số**; nó
   so với git tại thời điểm chạy. Một proof viết `== 39` sẽ đỏ ở commit tiếp theo và người ta
   sẽ sửa con số thay vì đọc nó.
3. **`SCHEMA_VERSION = 2` khiến bản app cũ từ chối mở DB mới.** `coscc/data.py:268-273` ném
   `Incompatible` khi DB mới hơn build. Sau khi cái này ship, quay về `v0.2.3` mà vẫn giữ
   `~/.cos/cos.db` là app không chạy. Có chủ ý (`coscc/data.py:121-127`) nhưng phải nói ra:
   đường lùi ở đây là lùi app **và** dọn DB, không phải chỉ lùi app.
4. **`states.json` có thể không vào wheel.** Đúng lớp lỗi `0012`. Bước 8 tồn tại vì thế, và
   nó phải chạy **đỏ trước** để chứng minh guard biết bắt.
5. **Đường đọc mới đọc được từ mạng.** `spec.md` C6. Không có gì trong unit này làm hẹp lại;
   nó chỉ không được làm rộng hơn mức một route đọc lịch sử unit cần.
6. **`npm test` chạy trên `~/.cos` thật nếu quên truyền `Data`.** `coscc/journal.py:108-109`
   ghi sẵn cảnh báo đó cho `Journal` và `Store`; bảng mới thừa hưởng nguyên rủi ro.

## Proof

```sh
npm test
uv run python scripts/verify_0013.py
```

Đạt khi: `npm test` xanh cả hai runtime, **và** `verify_0013.py` exit 0 — sau khi cùng file
đó đã exit 1 ở bước 6, trước khi backfill chạy. Hai lần chạy đó là bằng chứng.

`verify_0013.py` khẳng định:

1. đếm **độc lập từ git** số lần một artifact trong `.cos/` bị sửa sau khi đã chốt, rồi
   khẳng định đường đọc của sản phẩm trả về **đúng từng ấy** sự kiện, trên đúng những
   artifact ấy (R2);
2. mỗi sự kiện mang unit, artifact, trạng thái nguồn, trạng thái đích, thời điểm và nguồn
   suy ra; những trường không biết được mang giá trị "không biết" tường minh, không phải
   `NULL` (R3);
3. không tồn tại cột trạng thái hiện tại: xoá chuyển trạng thái cuối thì phép chiếu lùi
   theo (R1);
4. nạp được một tập trạng thái khác từ file, không sửa Python (R6);
5. sau khi chạy, `git status` của workspace không có file nào của coscc (R7).

Exit 2 khi: không có `git`, không có DB ghi được, hoặc repo này không phải một git checkout.

**Proof này không đo:** agent, feed, session thật. Không có cái nào tồn tại trong unit này,
và `spec.md` `## Out of scope` nói vì sao.
