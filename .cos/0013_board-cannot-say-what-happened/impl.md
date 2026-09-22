# Impl: the state set first, then the log, then the history that fills it
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Bốn thành phần mới, theo đúng thứ tự `plan.md` đặt ra, mỗi bước một commit.

**`coscc/states.json` + `coscc/states.py` (`aea113a`).** Tập trạng thái là dữ liệu. Mặc định
là tám stage với đúng các `Status` mà `.claude/scripts/cos.mjs:25-34` đang enforce; không
module nào khác được viết tên một trạng thái ra. Vế "mặc định đúng" được kiểm bằng cách
**chạy `cos.mjs status --json`** và so từng trường — một kỳ vọng viết tay sẽ vẫn xanh sau khi
hai bên lệch nhau, đúng lý lẽ `coscc/board_test.py:1-7` đã viết cho chính nó. Vế "flex" được
kiểm bằng một tập không chung một cái tên nào với mặc định.

Bốn kiểu định nghĩa hỏng bị từ chối, và cái đáng sợ nhất là `settled` gọi tên một status
không stage nào mang được: nó nạp được, khiến **không gì là settled**, và con số của unit này
sẽ về 0 trông như tin tốt.

**`coscc/data.py` lên schema 2 (`85e537e`).** Hai bảng: `transitions` và `outputs`.
Không cần migration riêng — `_create` chạy lại toàn bộ `_SCHEMA` khi `user_version` thấp hơn
và mọi câu lệnh đều `IF NOT EXISTS`. Mọi cột đều `NOT NULL` **không default**, nên người ghi
buộc phải nói "không biết" thành một giá trị chứ không để trống (R3). Thêm một `once_key` với
unique index **từng phần** (`WHERE once_key <> ''`): bộ nhập đặt khoá nên chạy lại không nhân
đôi, còn ghi thật không đặt khoá nên hai lần chuyển giống hệt nhau vẫn là hai sự kiện.

**`coscc/history.py` (`1292e5c`).** `state()` là **phép gấp trên log**, không phải giá trị
lưu. `from_state` được suy từ chính log bên trong một `BEGIN IMMEDIATE`, nên chuỗi luôn tự
nhất quán. `sessions_of()` giữ hai cơ chế tách nhau: một chat nhiều lượt là **một** hàng,
tám bước là tám hàng.

**`coscc/backfill.py` (`55f6f04`).** Một lượt `git log --reverse --no-renames --name-status`
trên cả thư mục, rồi **một** `cat-file --batch` cho mọi blob — 103 blob trong một tiến trình
con thay vì 103 tiến trình. Chỉ đọc.

**Đường đọc (`d914aaf`)** đặt cạnh Board, không thay thế. Board vẫn chạy `cos.mjs`
(`intent.md` ràng buộc 3).

**`scripts/verify_0013.py` (`0334adc`)**, và **`states.json` vào guard của wheel**
(`5b3ef02`).

## Where the plan was departed from

Ba chỗ, cả ba đã ghi vào `plan.md` trong cùng commit gây ra chúng.

1. **Bước 2 đo trên *bản sao* của `~/.cos/cos.db`, không phải bản gốc** (`85e537e`). Risk 3
   xảy ra ngay tại lúc đo: bản `v0.2.3` đang cài và đang chạy dùng đúng file đó, nâng nó lên
   version 2 là làm app đang chạy ném `Incompatible`. Bản sao chứng minh đúng cùng một điều.

2. **Bước 6 và 7 thành một cờ `--import`, ghi vào data root tạm** (`0334adc`). Kế hoạch viết
   hai bước này như hai thời điểm. Cả hai đã diễn ra đúng thứ tự, nhưng một proof chỉ đỏ được
   **một lần trong đời** thì sau đó không ai kiểm lại được nó có biết đỏ hay không. Với cờ
   này mọi máy chạy được cả hai chiều.

3. **Thêm `machines_in()` và trường `mixed_state_sets`** (`94b0db4`), không có trong bảng file
   của `plan.md` nhưng nằm trong các file plan đã liệt kê. Lý do: tự soát lại thì `spec.md`
   C5 là requirement duy nhất tôi chưa xây gì cho nó.

## What was measured

**`npm test`** — `60` test node (không đổi) và **`368`** test Python, xanh cả hai runtime.
Ngay sau bước 1, lần chạy đầy đủ đầu tiên đo `310` test Python; `coscc/states_test.py` đóng
góp `11` trong số đó, nên con số trước unit này là **`299`**. Tức unit này thêm `69` test.

**`scripts/verify_0013.py`**, chạy thật hai chiều:

```
$ uv run python scripts/verify_0013.py
FAIL  the product lists every post-settlement edit git knows about (0 of 43, over 0 artifacts)
FAIL  and each one carries the state it came from and the state it went to
FAIL  every one of the 0 transitions carries all 8 fields: no transitions at all
FAIL  the projection follows the log: no transition that changed a state, so the check
      could not be made to fail
PASS  a state set loaded from a file ... drives a unit end to end with no Python changed
PASS  reading this repository's history added nothing to its git status
exit=1

$ uv run python scripts/verify_0013.py --import
imported 209 of 209 transitions over 24 units
PASS  the product lists every post-settlement edit git knows about (44 of 44, over 25 artifacts)
PASS  and each one carries the state it came from and the state it went to
PASS  every one of the 104 transitions carries all 8 fields
    note: 104 of 104 say their actor is 'unknown'
PASS  deleting the last state-changing transition of 0012.../ship.md moves it back from
      accepted to not started, so nothing else stores it
PASS  a state set loaded from a file ... drives a unit end to end with no Python changed
PASS  reading this repository's history added nothing to its git status
exit=0
```

**Con số đã đổi bốn lần trong lúc làm unit này: 39 → 41 → 42 → 43 → 44.** `intent.md` đo 39,
`plan.md` đo 41, bước 4 đo 43, và lần chạy cuối đo 44. Mỗi lần tăng đều do **artifact của
chính unit này bị sửa sau khi đã accepted** — tức unit đo việc viết lại liên tục tự sinh thêm
mẫu cho phép đo của nó. `plan.md` Risk 2 dự đoán đúng chuyện này, và đó là lý do proof tính
kỳ vọng từ git **mỗi lần chạy**.

**Hai đường tính độc lập cho cùng một số.** `coscc/backfill.py` quét cả thư mục một lượt và
đọc blob qua `cat-file --batch`; `scripts/verify_0013.py` đi **từng file một** với
`git log -- <path>` và `git show <sha>:<path>`, có biểu thức đọc `Status:` riêng và định
nghĩa "settled" riêng. Hai chương trình cùng ra 44 là bằng chứng; một chương trình tự đồng ý
với mình thì không (`plan.md` Risk 1).

**Bước 8, đỏ rồi xanh trên wheel thật**, không phải zip dựng tay — câu hỏi thật là `uv_build`
có đóng gói một file không phải `.py` dưới `coscc/` hay không:

```
$ uv build --wheel --out-dir /tmp/wheelout        # mang coscc/states.json, không bị kêu
$ python3 scripts/check_wheel.py <wheel đã gỡ states.json>
  no coscc/states.json — no transition could be read or written
```

**Nâng schema, đo trên bản sao của `~/.cos/cos.db` do `v0.2.3` ghi ra:** `user_version`
1 → 2, có đủ `transitions` và `outputs`, dòng `workspaces` duy nhất
(`/home/bd/coscc-wp` · `coscc`) còn nguyên.

**Hai lỗi của chính tôi trong proof, sửa chứ không lách.** Claim 3 ban đầu xoá một hàng còn
hàng khác chồng lên sau, nên phép chiếu **không** lùi — và một cột trạng thái lưu sẵn sẽ cho
đúng câu trả lời ấy, tức claim không có khả năng đỏ. Nay nó chọn hàng cuối **của chính
artifact đó** và đòi `from_state <> to_state`. Claim 5 ban đầu đòi `git status` sạch, tức đo
người đang chạy chứ không đo sản phẩm; nay nó so với ảnh chụp lấy trước khi nhập.

## What is still open

1. **`coscc/service.py:51` vẫn hardcode tám tên stage** trong `STAGE_FILES`. Có trước unit
   này và không phải tên *trạng thái*, nên R6 không bị vi phạm theo chữ. Nhưng nó là nguồn
   thứ hai cho danh sách stage, và tinh thần R6 muốn nó hỏi `states.py`. Chưa làm: đổi nó
   đụng vào `artifact()` và `run_step`, ngoài phạm vi outcome này.
2. **Nửa còn lại của C5 chưa có chỗ để từ chối.** Không thành phần nào hiện so Board với
   đường đọc mới, nên chưa có phép đối chiếu nào để chặn. `coscc/states_test.py` chặn ở mức
   test: mặc định phải khớp `cos.mjs`. Khi có component đối chiếu thật, nó phải hỏi
   `machines_in()` trước khi so.
3. **Không có route nào chạy bộ nhập.** Proof gọi `backfill.run` trực tiếp. Thêm một route
   *ghi* là mở rộng bề mặt mà `spec.md` C6 vừa cảnh báo, và repo này là repo duy nhất có
   `.cos/` để nhập, nên chưa đáng.
4. **`SCHEMA_VERSION = 2` khiến `v0.2.3` không mở được DB đã nâng.** Đường lùi là lùi app
   **và** dọn `~/.cos/cos.db`. Ghi ở `coscc/data.py:48-56`.
5. **Bốn câu hỏi mở của `spec.md` còn nguyên**: sao lưu `pr.md` nếu mất DB; trần chi cả
   board; `verify_0011`/`verify_0012` đo gì khi `node` và `cos.mjs` biến mất; ai ghi chuyển
   trạng thái khi con người sửa tay. Câu cuối nay đã có một nửa câu trả lời: bộ nhập
   **chạy lại được**, nên sửa tay rồi nhập lại thì sự kiện mới vào log với nguồn là commit.
