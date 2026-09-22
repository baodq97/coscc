# Plan: One data root under ~/.cos, and one page on top of it
Intent: intent.md. Spec: spec.md. Author: Claude Opus 5. Status: done.

> **`done` ở đây là một bypass, đặt tay ngày 2026-09-22 theo lệnh của người khởi xướng.**
> Ba stage `pr`, `review`, `ship` của unit này **chưa bao giờ chạy** — các ô tương ứng trong
> `cos.mjs status` trống, và chúng trống vì đúng như vậy. Unit không đi qua chúng; nó được
> tuyên bố đóng.
>
> Lối tắt là `.claude/scripts/cos.mjs:123`: `nextAction` thấy `plan.md: done` thì trả
> `finished` ngay và không đọc ba stage sau. Đó **chính là** lối tắt mà `0005` mở rộng vòng
> lặp từ ba lên tám stage để bịt, và `.claude/CLAUDE.md` gọi tên nó — *"`plan.md: done` is
> terminal, which is what kept the five units closed under the old three-stage loop reading
> as finished"*. Nó được dùng lại ở đây một cách có ý thức, không do nhầm.
>
> **Vì sao không đi qua vòng lặp cho đúng:** `cos.mjs:31` cho stage `pr` đúng ba status —
> `draft`, `accepted`, `rejected` — và **không có `skipped`**, trong khi `spec` thì có. Bốn
> unit này hoàn thành trước khi repo có remote, nên không có pull request nào để ghi, và
> `write-pr` invariant 4 bắt ghi `draft` khi không mở được PR. `draft` thì không mở được
> `gate review`. Không có đường ra nào khác ngoài sửa `cos.mjs`, và `0009 spec.md`
> `## Out of scope` đã để việc đó ra ngoài phạm vi.
>
> **Điều này không đúng với `write-plan` invariant 9**, vốn đòi `done` chỉ được đặt sau khi
> công việc đã ship và lệnh ở `## Proof` đã pass. Công việc **đã** ship — code của cả bốn
> unit nằm trên `main` — nhưng `ship.md` thì không tồn tại, và proof của unit này không được
> chạy lại vào ngày đặt `done`.
>
> `0009` là unit đầu tiên đi hết tám stage thật. Từ `0010` trở đi có pull request thật, nên
> bức tường này không gặp lại.


## Files that change

**Tầng dữ liệu**

| Path | Gì xảy ra |
|---|---|
| `cos_baodo/data.py` *(new)* | Thư mục dữ liệu, kết nối SQLite, schema, migration, prefs. Nơi duy nhất biết đường dẫn thật. |
| `cos_baodo/data_test.py` *(new)* | Schema version, từ chối phiên bản cao hơn, tạo thư mục `0o700`, prefs đọc/ghi. |
| `cos_baodo/objects.py` *(new)* | Object store theo digest. Ghi file tạm rồi `rename`. |
| `cos_baodo/objects_test.py` *(new)* | Bất biến, ghi lại là không-op, không để lại file tạm khi lỗi. |
| `cos_baodo/config.py` | Thêm `data_dir`, đọc `COS_DATA_DIR` trong `from_env` và không ở đâu khác. |
| `cos_baodo/config_test.py` | Mặc định `~/.cos`; override; không có setter. |
| `cos_baodo/store.py` | Giữ nguyên interface (`entries`, `add`, `set_label`, `remove`, `path_of`, `is_under`, `resolves_to_entry`, `transaction`, `Busy`, `BadName`). Đổi ruột sang SQLite. |
| `cos_baodo/store_test.py` | Test hiện có phải xanh; thêm test cho lần nhập từ JSON. |
| `cos_baodo/journal.py` | Giữ nguyên interface (`append`, `set_mode`, `started`, `finished`, `records`, `modes`, `timeline`, `totals`). Đổi ruột sang SQLite. |
| `cos_baodo/journal_test.py` | Test hiện có phải xanh. |

**Trang**

| Path | Gì xảy ra |
|---|---|
| `cos_baodo/state.py` *(new)* | `StudioState`. Gọi `Service`, không quyết định gì. |
| `cos_baodo/state_test.py` *(new)* | Dataclass hiển thị, ánh xạ `Invalid` → chữ, không import dữ liệu mẫu. |
| `cos_baodo/screens.py` *(new)* | Sáu màn, dựng từ component của `prototype.py` nhưng đọc `StudioState`. |
| `cos_baodo/screens_test.py` *(new)* | Dựng component tree sáu màn; giữ các regression của `prototype_test.py` còn đúng. |
| `cos_baodo/studio.py` | Giữ. Có thể thêm primitive; không bỏ cái nào. |
| `cos_baodo/cos_baodo.py` | Bỏ trang cũ và `State` của nó. Đăng ký `screens.index` ở `/`. |
| `cos_baodo/prototype.py` | **Xóa.** |
| `cos_baodo/prototype_data.py` | **Xóa.** |
| `cos_baodo/prototype_test.py` | **Xóa.** |
| `cos_baodo/build.py` | `_SOURCES` bỏ hai file prototype, thêm `state.py` và `screens.py`. |
| `cos_baodo/build_test.py` | Theo `_SOURCES` mới. |

**Bằng chứng và tài liệu**

| Path | Gì xảy ra |
|---|---|
| `scripts/verify_0006.py` *(new)* | Proof của R13 và R14: 5 luồng trên dữ liệu thật, restart ở giữa. |
| `scripts/verify_0003.py` | Viết lại theo trang mới. Giữ theme, responsive, contrast, negative control. |
| `scripts/verify_0004.py` | Giữ nguyên hai claim và số **20**. Chỉ chạy lại trên cơ chế mới. |
| `scripts/verify_fragmented-product-experience.py` | **Xóa.** Nó lái `/prototype`, thứ không còn tồn tại. Xem Risk 6. |
| `docs/prototype.md` | Viết lại thành tài liệu trang thật. |
| `README.md` | Thư mục dữ liệu, lệnh mới, proof mới. |
| `.claude/CLAUDE.md` | Mô tả nơi lưu dữ liệu và danh sách proof. |

## Order of work

Mỗi bước để lại một trạng thái kiểm được. `npm test` phải xanh ở cuối mỗi bước.

1. **`data.py` + test.** Thư mục dữ liệu, `connect()`, schema v1 (bảng `schema_version`,
   `migrations`, `workspaces`, `runs`, `prefs`), WAL, `busy_timeout` 10s, từ chối schema
   cao hơn. Chưa ai gọi nó. — *kiểm: `npm test`.*
2. **`objects.py` + test.** Ghi theo digest, atomic rename, bất biến. Chưa ai gọi nó.
   — *kiểm: `npm test`.*
3. **`config.py`: `data_dir`.** Mặc định `~/.cos`, đọc `COS_DATA_DIR` trong `from_env`.
   — *kiểm: `npm test`.*
4. **`store.py` sang SQLite.** Interface không đổi. Thêm lần nhập một lần từ
   `<root>/.cos-baodo.json`, ghi vào bảng `migrations` để không chạy lần hai; file JSON
   không bị xóa. — *kiểm: `npm test`, rồi `uv run python scripts/verify_0004.py` claim 1
   xanh với 20/20.*
5. **`journal.py` sang SQLite.** Interface không đổi. `records` vẫn trả oldest-first.
   — *kiểm: `npm test`.*
6. **Cả `verify_0004.py` xanh.** Hai claim. Claim 2 tạo một phiên thật và tiêu quota.
   — *kiểm: exit 0.*
7. **`state.py`.** `StudioState`: workspaces, board, timeline, sessions, history, prefs,
   run một bước. Mọi handler gọi `Service` và chỉ dịch `Invalid` thành chữ.
   — *kiểm: `npm test`.*
8. **`screens.py`.** Sáu màn đọc `StudioState`. Xóa `prototype*.py`. Chuyển những
   regression còn đúng của `prototype_test.py` sang `screens_test.py`.
   — *kiểm: `npm test`.*
9. **Một trang ở `/`.** Bỏ trang cũ trong `cos_baodo.py`, đăng ký `screens.index` ở `/`,
   cập nhật `_SOURCES`. — *kiểm: `uv run cos-build` exit 0 và fingerprint phủ file mới;
   `uv run cos-baodo` khởi động và `curl` trả 200.*
10. **`verify_0003.py` viết lại.** — *kiểm: exit 0.*
11. **`verify_0006.py`.** Năm luồng, rồi restart tiến trình app, rồi kiểm lại.
    — *kiểm: exit 0.*
12. **Tài liệu.** `README.md`, `docs/prototype.md`, `.claude/CLAUDE.md`. Xóa
    `verify_fragmented-product-experience.py`. — *kiểm: lệnh trong README chạy được đúng như viết.*

Bước 1–6 là tầng dữ liệu và độc lập với trang: nếu phải dừng giữa chừng, dừng sau bước 6
để lại một repository chạy được với trang cũ còn nguyên.

## Risks

1. **SQLite mất ghi ở chỗ `flock` không mất.** Blast radius lớn nhất: đây là đúng thứ
   `0004` đã đo thấy hỏng — **8/20** entry sống sót trước khi có khóa
   (`.cos/0004_silent-concurrent-loss/`). `BEGIN IMMEDIATE` phải bao cả read-modify-write,
   không phải chỉ câu `INSERT`. **Thấy được khi:** `verify_0004.py` claim 1 báo dưới 20.
   Không suy luận; chạy nó.
2. **Trang mới là trang duy nhất.** Sau bước 9, sai ở đâu thì không còn mặt trước nào để
   đối chiếu trong cùng build (spec C3). **Thấy được khi:** `verify_0003.py` hoặc
   `verify_0006.py` đỏ — nhưng một cái sai tinh vi về bố cục thì chỉ người dùng thấy.
   Giảm nhẹ: bước 9 là commit riêng, revert được.
3. **`StudioState` gánh cả sáu màn.** `prototype.py` dài 1372 dòng với state trong bộ nhớ;
   bản thật phải gọi `Service` bất đồng bộ và chịu lỗi. Reflex yêu cầu var có kiểu dựng
   được, nên mọi dict từ `Service` phải đổ vào dataclass trước khi vào state — đúng cách
   `cos_baodo/cos_baodo.py:36-71` đang làm. **Thấy được khi:** `uv run cos-build` đỏ, hoặc
   trang render rỗng.
4. **Mỗi lần mở màn Board là một tiến trình con `cos.mjs`,** timeout 10s
   (`cos_baodo/board.py:38`). Một workspace không có `.cos/` trả `Unavailable`.
   **Thấy được khi:** màn Board treo tới 10s, hoặc hiện lỗi thay vì trạng thái rỗng — R15
   nói nó phải là trạng thái rỗng có chữ.
5. **Nút chạy bước tiêu quota thật** (spec C8). `verify_0006.py` **không được** bấm nó;
   luồng (c) chỉ xem artifact và timeline. **Thấy được khi:** hóa đơn, tức là quá muộn —
   nên đây là ràng buộc lên proof, không phải thứ để đo sau.
6. **Xóa `verify_fragmented-product-experience.py` là bỏ khả năng chạy lại bằng chứng của `fragmented-product-experience`.** Nội dung của nó
   — sáu màn, light/dark, empty/loading/error, không tràn ở 390/768/1024/1440px — chuyển
   sang `verify_0006.py` nên không mất về bản chất. Nhưng `.cos/fragmented-product-experience_.../impl.md` trích nó,
   và sau bước 12 câu trích đó chỉ còn đúng với commit `e5d6048`. Ghi ở đây thay vì sửa
   artifact của `fragmented-product-experience`.
7. **Nhập từ JSON hai lần** thành entry trùng. **Thấy được khi:** test ở bước 4 đếm entry
   sau hai lần mở store.
8. **Thứ không muốn viết ra.** Unit này xóa mặt trước duy nhất đã được chứng minh, và viết
   lại cả hai cơ chế lưu trữ mà một unit trước đã đo ra lỗi mất dữ liệu thật — trong một
   lần, vì người khởi xướng yêu cầu nhanh. Bốn proof ở `## Proof` là toàn bộ thứ đứng giữa
   việc đó và một app hỏng lặng lẽ. Nếu chúng yếu hơn bộ cũ ở chỗ nào tôi không nhận ra,
   không còn gì bắt được.

## Proof

```
npm test \
  && uv run cos-build \
  && uv run python scripts/verify_0004.py \
  && uv run python scripts/verify_0003.py \
  && uv run python scripts/verify_0006.py
```

Đạt khi cả năm lệnh exit 0, và:

- `npm test` không ít test hơn lần đo ngày 2026-09-22 ở `fragmented-product-experience` (**31** Node + **239**
  Python), trừ phần trừ đi đúng bằng số test của `prototype_test.py` bị xóa. Con số bị trừ
  phải nêu rõ trong `impl.md`.
- `verify_0004.py` in `20 of 20` cho claim 1.
- `verify_0006.py` in `5/5` và báo đạt cả phần sau khi khởi động lại.
- `verify_0003.py` exit 0, không phải 2. Exit 2 nghĩa là môi trường chưa sẵn sàng và
  **không tính là đạt**.

`verify_0003.py` và `verify_0006.py` cần `COS_PORT` trống và một browser; dừng app trước
khi chạy. Cả hai không được chạy đồng thời.

## Departures from this plan

Ghi tại thời điểm xảy ra, theo `write-plan` invariant 8.

1. **`Service` có thêm sáu method chỉ-đọc** (`activity`, `usage`, `settings`, `artifact`,
   `preferences`, `set_preference`). `spec.md` `## Design` viết "`Service` không đổi". Cách
   khác là để trang đọc thẳng `Journal`, `policy` và file artifact — tức là phá đúng quy
   tắc mà `cos_baodo/service.py:1-13` tồn tại để giữ. Sáu method chỉ đọc là cái giá rẻ hơn.
   `set_preference` có ghi, và nó chỉ nhận các khóa trong một danh sách trắng.

2. **`journal.py` cũng nhập một lần từ JSONL.** `spec.md` R8 chỉ nói tới
   `.cos-baodo.json`. Bỏ qua nhật ký cũ sẽ mất lịch sử trên một máy đã dùng trước `0006`;
   cơ chế y hệt, một dòng migration key khác.

3. **Lane trên board không dùng `blocked` của harness.** Đo ngày 2026-09-22 bằng
   `cos.mjs status --json` trên chính repo này: `blocked` là `true` cho **mọi** unit chưa
   xong (`.claude/scripts/cos.mjs:122-135`), nên lane "Needs review" nuốt cả 6 unit và hai
   lane kia rỗng vĩnh viễn. Lane giờ đọc từ trạng thái artifact: có `draft` hoặc có
   `problems` thì mới là cần xem lại. Ghi lại vì đây là một hiểu nhầm dễ lặp.

4. **Phiên bản schema nằm ở `PRAGMA user_version`, không phải bảng `schema_version`.**
   Bảng buộc mỗi lần mở kết nối phải đọc bảng, và `CREATE TABLE IF NOT EXISTS` chạy mỗi
   lần mở là một lần xin khoá ghi. Xem Risk 1 và commit `ce60485`.

5. **`ui.py` bị cắt còn `THEME` + `GLOBAL_STYLE`.** Các helper còn lại chỉ trang cũ dùng.

## What was chosen against

- **Không** giữ trang cũ song song. Người khởi xướng chọn thay thế ngày 2026-09-22; giữ
  hai trang là giữ hai thứ phải sửa mỗi lần.
- **Không** đưa workspace vào `~/.cos`. Lựa chọn của người khởi xướng; giữ quy tắc đường
  dẫn dựng từ `COS_WORKING_DIR` nguyên vẹn.
- **Không** dùng ORM hay migration framework. Schema có năm bảng và một số phiên bản;
  thêm một dependency để quản lý chừng đó là đắt hơn thứ nó quản.
- **Không** đổi `Service`, `Sessions`, `Runner`, `policy.py` hay API JSON. Đó là lý do unit
  này làm được trong một lần.
- **Không** xóa `<root>/.cos-baodo.json` sau khi nhập. Một lần nhập sai mà file gốc còn thì
  sửa được; xóa rồi thì không.
