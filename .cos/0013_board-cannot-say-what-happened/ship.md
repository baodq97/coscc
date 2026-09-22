# Ship: transitions are the record, and the state set is configuration
Review: review.md. Author: Bao Do. Status: accepted.

## What went out

**`v0.3.0`**, tag `v0.3.0` trên `main` tại `0f53615`, release có
`coscc-0.3.0-py3-none-any.whl`, `install.sh`, `SHA256SUMS`. Đã cài trên máy này bằng đúng
một dòng trong `docs/install.md`; `coscc --version` in `coscc 0.3.0`.

Ba pull request:

| | |
|---|---|
| #13 | `feat(0013)` — log chuyển trạng thái, tập trạng thái là cấu hình, bộ nhập từ git, đường đọc |
| #14 | `chore` — `0.3.0`. Minor chứ không phải patch, vì `SCHEMA_VERSION` lên 2 là cửa một chiều |
| #15 | `fix(test)` — test suite ghi vào `~/.cos` thật. Xem `## What to do if it breaks` |

## Did the outcome hold

`intent.md` hứa: *"sản phẩm liệt kê được, cho 12 unit đang có trong `.cos/`, **đúng 39 lần
một artifact bị sửa sau khi đã chốt**"*, hạn 2026-10-20, và hôm viết intent con số liệt kê
được là **0**.

Đo trên `main` ngày 2026-09-22, `uv run python scripts/verify_0013.py --import`, **exit 0**:

```
PASS  the product lists every post-settlement edit git knows about (39 of 39, over 22 artifacts)
```

**39 và 22, đúng từng con số `intent.md` viết ra.** Hạn còn 28 ngày.

**Nhưng nó đúng vì một lý do không ai lường, và lý do đó quan trọng hơn con số.**

Trong lúc làm, con số đi **39 → 41 → 42 → 43 → 44**, mỗi lần tăng đều do artifact của chính
unit này bị sửa sau khi đã accepted. `plan.md` Risk 2 ghi chuyện đó và bắt proof tính lại từ
git mỗi lần chạy thay vì mang hằng số — nếu không, proof đã đỏ bốn lần và bị sửa bốn lần.

Rồi PR #13 được **squash-merge**, và con số rơi từ 44 về 39.

```
$ git log --oneline <branch> -- .cos/0013.../spec.md
70429fd 0013 spec: _SCHEMA closes at 118, not 119
0b5a33b 0013 spec: transitions are the record, and the state set is configuration

$ git log --oneline main -- .cos/0013.../spec.md
415a97a feat(0013): ... (#13)
```

Năm sự kiện biến mất, vì squash-merge **giữ lại trạng thái cuối và bỏ đường đi tới đó** —
đúng nguyên văn cơ chế `intent.md` được viết ra để chống. Vòng lặp này gặp lại vấn đề của
chính nó, ở một tầng nó chưa nhìn tới.

Nên câu trả lời trung thực là hai vế, không phải một:

- **Vế đạt:** sản phẩm liệt kê được mọi sự kiện mà git còn giữ, kèm artifact, trạng thái
  nguồn, trạng thái đích, thời điểm và commit suy ra, đối chiếu bằng một cài đặt độc lập.
  Từ 0 lên đủ.
- **Vế không đạt, và không được đọc thành đạt:** "39" bây giờ **không phải bằng chứng con số
  ổn định**. Nó bằng 39 vì 44 đã bị xoá bớt 5. Bộ nhập từ git chỉ tốt bằng lịch sử git còn
  lại, và quy trình của repo này (`.claude/CLAUDE.md`, mỗi unit một PR squash) chủ động vứt
  bỏ đúng loại sự kiện unit này đếm.

Bằng chứng mạnh nhất không phải con số tổng mà là một trường hợp `intent.md` gọi đích danh:
`0008_personal-name-blocks-publishing/plan.md`, "6 lần". Bản **đã cài** in ra qua API của nó:

```
2026-09-22T06:57:03+00:00 not started -> accepted commit:3da1008
2026-09-22T07:21:59+00:00 accepted    -> accepted commit:f9caae1
2026-09-22T07:27:19+00:00 accepted    -> accepted commit:cd21b51
2026-09-22T07:36:23+00:00 accepted    -> accepted commit:9233f90
2026-09-22T07:52:38+00:00 accepted    -> accepted commit:96647cb
2026-09-22T08:12:44+00:00 accepted    -> accepted commit:f3f0a84
2026-09-22T09:49:00+00:00 accepted    -> done     commit:31ea2fc
```

Sáu lần sửa sau khi chốt, đúng như `intent.md` đếm, giờ đọc được từ sản phẩm thay vì từ git.
Cùng lời gọi đó trả `"sessions": []` và `"unknown_transitions": 16` — `spec.md` C1 hiện ra
thành số chứ không im lặng.

## How it is watched

Bốn tín hiệu, mỗi cái là một lệnh.

1. **`uv run python scripts/verify_0013.py --import`** — băng kiểm soát chính. Nó tính kỳ
   vọng **từ git lúc chạy**, nên nó không đo một con số mà đo **một quan hệ**: hai cài đặt
   độc lập phải ra cùng một kết quả. Exit 0 là đạt. Exit 1 nghĩa là sản phẩm và git đã lệch.
2. **`uv run python scripts/verify_0013.py`** (không cờ) — phải **exit 1**. Một proof mất
   khả năng đỏ là một proof đã hỏng; dòng này kiểm lại điều đó trên mọi máy, mãi mãi.
3. **`npm test`** — `60` node + `371` Python. Trong đó có guard mới ở `coscc/data_test.py`
   chặn mọi test dựng `Journal`, `Store` hay `History` thiếu data root.
4. **`curl -sS http://127.0.0.1:8790/api/workspaces`** trên máy đã cài. `{"ok":true}` từ
   `/api/health` **không** đủ: hôm nay app trả `Internal Server Error` ở route này trong khi
   `/api/health` vẫn xanh và `systemctl --user is-active` vẫn nói `active`. Route nào đọc DB
   mới là route nói thật.

Cái không được canh, và phải nói ra: **không màn hình nào hiển thị thứ này.**
`coscc/screens.py` không đổi một dòng. 207 chuyển trạng thái đang nằm trong `~/.cos/cos.db`
của máy này và chỉ tới được qua JSON.

## What to do if it breaks

**Lùi mã:** revert `415a97a` (#13), `454d97c` (#14), `0f53615` (#15) trên `main`.

**Lùi mã thôi là không đủ, và đây là phần đắt.** `SCHEMA_VERSION = 2`
(`coscc/data.py:48-56`) khiến `v0.2.3` trở về trước **từ chối mở** DB mà bản này đã chạm, và
`uv tool uninstall` không xoá `~/.cos/cos.db`. Đường lùi đầy đủ:

```sh
systemctl --user stop coscc
rm ~/.cos/cos.db          # hoặc mv đi chỗ khác
curl -LsSf https://github.com/baodq97/coscc/releases/download/v0.2.3/install.sh | sh
```

Cái mất khi xoá file đó, đo 2026-09-22 ngay trước khi ship: `runs` 0 dòng, `prefs` 0 dòng,
`migrations` 0 dòng, `workspaces` **1** dòng (`/home/bd/coscc-wp` · `coscc`) — thêm lại bằng
màn Workspaces trong một phút. Toàn bộ `transitions` nhập được **dựng lại được** từ git bằng
bộ nhập, nên nó không phải dữ liệu độc bản.

**Nếu triệu chứng là app đã cài trả 500 trên mọi route đọc DB trong khi `/api/health` xanh:**
gần như chắc là DB mới hơn app. Ba nguyên nhân, theo thứ tự đã gặp thật:
`npm test` trên một checkout mới hơn (đã sửa ở #15 và có guard), cài bản mới rồi lùi app,
hoặc hai bản coscc khác version cùng dùng một `COS_DATA_DIR`.

## What this ships that the next unit will be written from

Không phải một cái hỏng — một chỗ trống mà ship này vừa làm lộ ra, và là chỗ `write-ship`
nói vòng lặp quay lại:

**Băng kiểm soát bị vi phạm ngay hôm ship: 44 sự kiện trên nhánh, 39 trên `main`.** Squash
xoá 5. Chừng nào đường ghi duy nhất còn là "nhập lại từ git", sản phẩm còn thừa hưởng nguyên
điểm mù của git — và quy trình của chính repo này là thứ tạo ra điểm mù đó. Lời đáp của unit
này ("log là bản ghi") chỉ đúng với việc làm **sau khi** log tồn tại, và hôm nay chưa có
đường nào ghi vào log lúc việc đang diễn ra. `spec.md` `## Out of scope` đẩy agent ra ngoài,
nên đó là unit tiếp theo, không phải một lỗi của unit này.
