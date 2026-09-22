# Spec: transitions are the record, and the state set is configuration
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Mọi requirement truy về một outcome duy nhất của `intent.md`: liệt kê được đúng **39** lần
một artifact bị sửa sau khi đã chốt, cho 12 unit đang có, đối chiếu với git.

**R1 — Bản ghi là *chuyển trạng thái*, và "đang ở đâu" là phép chiếu của nó.** Không tồn tại
cột nào giữ trạng thái hiện tại của một unit hay một stage. Kiểm bằng: xoá dòng chuyển trạng
thái cuối của một unit thì trạng thái hiện tại của nó lùi về giá trị trước đó, không cần
cập nhật thêm chỗ nào.

Đây là toàn bộ vấn đề `intent.md` đo. Một thiết kế có cả log lẫn cột trạng thái sẽ có hai sự
thật, và cái bị sửa tay sẽ là cái kia.

**R2 — Lịch sử có sẵn nhập được.** Đọc lịch sử git của `.cos/` trong repo này và sinh ra
**≥ 39** chuyển trạng thái loại "đã chốt rồi bị sửa tiếp", trên đúng **22** artifact mà
`intent.md` liệt kê. Kiểm bằng `scripts/verify_0013.py`, so trực tiếp với git.

**R3 — Mỗi chuyển trạng thái mang đủ chừng này, hoặc nói rõ là không biết.** Unit; artifact;
trạng thái nguồn; trạng thái đích; thời điểm; **actor**; **session** (có thể rỗng); và
**nguồn suy ra** — commit nào, hay lần chạy nào. Không trường nào được để `NULL` ngầm: không
biết thì phải là một giá trị nói rằng không biết, vì `intent.md` tồn tại chính vì thứ "không
biết" hiện đang trông giống thứ "không xảy ra".

**R4 — Một unit mang một *chuỗi* session, và chuỗi đó khác với một session dài nhiều lượt.**
`coscc/runner.py:271-274` truyền `session_id=None` mỗi bước nên tám stage sinh tám session;
`coscc/sessions.py:184` đặt `fork_session=False` nên chat giữ nguyên một id qua nhiều lượt.
Kiểm bằng: một unit có N chuyển trạng thái do agent thực hiện thì tra ra được N session
riêng biệt theo thứ tự, và một session nhiều lượt vẫn là **một** hàng.

**R5 — Hỏi được "sinh ra bao nhiêu file, ở đâu", và phân biệt được hai loại.**
`intent.md` `## Open questions` mục 2 để mở; spec này chốt: **một chỗ ghi, một trường phân
loại**. Deliverable (artifact của chính unit) và thay đổi trong cây code là hai loại, đếm
riêng được và đếm chung được. Hai bảng riêng thì mọi câu hỏi "tổng cộng bao nhiêu" đều phải
hợp nhất ở chỗ gọi, và một trong hai chỗ gọi sẽ quên.

**R6 — Tập trạng thái là cấu hình, không phải code.** Hai vế, cả hai đều phải kiểm được:

- **Mặc định là đúng tập đang dùng:** tám stage (`idea` … `ship`) với bốn `Status`
  (`draft`, `accepted`, `rejected`, `skipped`, cộng `done` cho `plan`), y như `cos.mjs`
  hiện có. Người khởi xướng chốt 2026-09-22: *"mình hard theo cái mình đang quen để chứng
  minh value trước"*.
- **Đổi được mà không sửa code:** có một test nạp một tập trạng thái **khác** — ít stage hơn,
  tên khác — và chạy một unit đi hết tập đó, không sửa một dòng Python nào. Test đó là bằng
  chứng của vế "flex"; thiếu nó thì "configuration" chỉ là một từ.

**R7 — Không ghi gì vào cây của repo đích.** Sau khi một unit chạy, `git status` trong
workspace chỉ chứa thay đổi mã do chính bước đó tạo ra — không có file nào của coscc. Người
khởi xướng chốt 2026-09-22 (`intent.md`, khối đính chính đầu file).

**R8 — Có proof, và hôm nay nó đỏ.** `scripts/verify_0013.py`, theo quy ước exit của
`scripts/verify_0003.py:8-14`: `0` mọi claim đúng, `1` ít nhất một sai, `2` môi trường không
trả lời được.

## Design

**Bốn thành phần và ranh giới giữa chúng.**

1. **Định nghĩa state machine — dữ liệu, nạp từ cấu hình.** Danh sách trạng thái, chuyển
   nào hợp lệ, trạng thái nào là kết thúc. Không thành phần nào khác được hardcode một tên
   trạng thái. Đây là chỗ duy nhất R6 sống, và là lý do R6 kiểm được bằng một test thay vì
   bằng lời hứa.

2. **Log chuyển trạng thái — chỉ ghi thêm.** Không `UPDATE`, không `DELETE` trong đường đi
   bình thường. Trạng thái hiện tại là truy vấn trên log, không phải một giá trị được lưu.
   Nó sống cùng chỗ với dữ liệu hiện có: `coscc/data.py:75-118` đã có cơ chế schema theo
   từng câu lệnh và version nằm ở `PRAGMA user_version`, nên thêm bảng đi theo đường đã có
   chứ không dựng đường mới.

3. **Bộ nhập từ git.** Đọc lịch sử một thư mục `.cos/`, suy ra chuyển trạng thái từ chỗ dòng
   `Status:` đổi giữa hai commit, và ghi vào log với `nguồn suy ra = commit`. Chỉ đọc; không
   bao giờ ghi vào repo nguồn. Nó là **một cách sinh dữ liệu cho log**, không phải một cách
   đọc trạng thái — nếu nó thành đường đọc thì R1 đã thua.

4. **Đường đọc.** Lịch sử của một unit, và phép chiếu "đang ở đâu". Board hiện tại **không
   đổi** và vẫn đọc `cos.mjs` (`intent.md` ràng buộc 3); đường đọc mới đặt cạnh, không thay
   thế. Hai nguồn cùng tồn tại là có chủ ý và có hạn — xem `## Concerns` C2 và C5.

**Dữ liệu đi qua ranh giới:** bộ nhập → log (một chiều); log → đường đọc (một chiều); định
nghĩa state machine → cả hai (chỉ đọc). Không thành phần nào ghi ngược lên thành phần trước.

## Out of scope

- **Agent, và mọi thứ gắn vào agent.** Feed bình luận trên ticket, người nhảy vào session,
  agent trả lời ngắn vào feed — `intent.md` ràng buộc 4 để cả ba ra ngoài. Chúng gắn vào
  thứ unit này xây; xây cùng lúc là một unit không đóng được.
- **`git worktree` cho mỗi ticket.** Cùng lý do.
- **Xoá `cos.mjs`, và bỏ `node` khỏi runtime.** `intent.md` ràng buộc 3.
- **Màn hình mới.** Đường đọc là JSON; Board giữ nguyên. Một màn hình dựng trên một schema
  chưa ai dùng là một màn hình phải vẽ lại.
- **Nhập lịch sử của repo khác repo này.** Nơi khác không có `.cos/` để nhập, theo đúng R7.
- **Làm `pr.md` tự đủ.** Xem `## Concerns` C4 — cần thiết, nhưng không phải thứ outcome này
  cho phép.

## Concerns

**C1 — Bộ nhập không khôi phục được "agent nào, session nào", và đó chính là một nửa lời
than của `intent.md`.** Git biết tác giả commit; nó không biết session. Nên **39 sự kiện lịch
sử sẽ vào log với actor và session là "không biết"**. Outcome vẫn đạt được — nó chỉ đòi liệt
kê chuyển trạng thái kèm thời điểm — nhưng người đọc dễ tưởng rằng nhập xong là có provenance.
Không có. Provenance chỉ đúng với việc làm **sau** khi thứ này chạy. R3 bắt phải ghi "không
biết" thành một giá trị rõ ràng chính là để chỗ này không im lặng.

**C2 — Nguồn đối chiếu co lại còn đúng repo này.** `intent.md` ràng buộc 3 muốn `cos.mjs`
chạy song song để đối chiếu, nhưng R7 nói workspace khác sẽ không có `.cos/`. Vậy đối chiếu
chỉ thực hiện được ở đây. Với công việc mới ở repo khác, **không có nguồn thứ hai nào**, và
độ tin phải đến từ chỗ khác: log là append-only và mọi hàng đều nêu nguồn suy ra.

**C3 — SQLite ở đây có một thứ tự đã đo và không test nào bắt được.**
`.claude/rules/coscc-app.md:53-57`: `busy_timeout` phải là câu lệnh **đầu tiên** trên mọi
connection, trước `PRAGMA journal_mode=WAL`; đảo lại thì hỏng khoảng một lần trên mười với
`database is locked`, đo 2026-09-22. Mọi read-modify-write bọc trong `BEGIN IMMEDIATE`. Bảng
mới phải đi qua đúng cơ chế đang có chứ không mở connection riêng.

**C4 — `pr.md` phải tự đủ, và requirement đó không truy về outcome này.** Đo 2026-09-22:
**44** chỗ trong code, script, docs và config của repo này trỏ vào `.cos/` (20 `scripts/`,
9 `coscc/`, 7 `docs/`, 5 `.claude/`, 3 `.github/`). Khi không có gì của coscc vào repo đích
nữa, lý lẽ của một thay đổi chỉ còn chỗ duy nhất là mô tả PR — trong khi
`.claude/skills/write-pr/SKILL.md` lại được thiết kế dựa đúng vào chỗ vừa mất: *"It is short
on purpose: the argument for the change is in `intent.md` and `spec.md`"*. **Người quyết:
người khởi xướng.** Gần như chắc là một unit riêng; ghi ở đây để nó không rơi mất.

**C5 — R6 và ràng buộc đối chiếu kéo ngược nhau.** Nếu ai đó cấu hình một tập trạng thái
khác, `cos.mjs` vẫn đọc tám stage cứng, và phép đối chiếu sẽ **so hai thứ khác nhau mà vẫn
chạy**. Giảm nhẹ: đối chiếu phải từ chối khi cấu hình không khớp tập mà `cos.mjs` biết, chứ
không so tiếp. Không xoá được mâu thuẫn — nó là cái giá của việc giữ hai nguồn trong lúc
chuyển tiếp.

**C6 — Đường đọc mới mở rộng thứ một caller không xác thực đọc được.** `coscc/data.py:62-65`
nói DB này giữ *"a record of every workspace on this machine and every prompt-shaped thing
the app has run"*, và thư mục là `0o700` vì lý do đó. App không có xác thực trên route nào
và mặc định bind `0.0.0.0` (`docs/install.md`). Thêm một route trả lịch sử unit là thêm thứ
đọc được từ mạng. Không phải lỗ hổng mới — nó là lỗ hổng cũ được mở rộng — nhưng nói ra ở
đây vì `0011` đã chọn `0.0.0.0` có chủ ý và lựa chọn đó nên được nhắc mỗi lần bề mặt lớn lên.

## Open questions

1. **Nếu DB mất thì sao?** `intent.md` OQ1 còn lại một nửa: thứ duy nhất còn trong repo dùng
   chung là mô tả PR trên GitHub. Chấp nhận đó là bản sao lưu duy nhất, hay `pr.md` cần chỗ
   thứ hai. Một câu trả lời "cần" sẽ thêm một đường xuất, không đổi gì trong schema.
2. **Trần chi cho cả board** (`intent.md` OQ4). `coscc/policy.py` chặn theo từng step. Chưa
   cần cho unit này vì không có agent nào chạy; sẽ chặn unit có agent.
3. **`verify_0011` và `verify_0012` đo cái gì sau này** (`intent.md` OQ5). Cả hai đo một sản
   phẩm còn `node` và `cos.mjs`. Chưa quyết; không chặn unit này.
4. **Ai ghi chuyển trạng thái khi con người tự làm?** Nếu một người sửa tay một artifact
   trong repo này, log không biết. Với việc làm mới thì R7 khiến chuyện này không xảy ra nữa
   — nhưng trong giai đoạn chuyển tiếp, repo này vừa có log vừa có người sửa tay, và hai thứ
   sẽ lệch. Một câu trả lời sẽ đổi việc bộ nhập chạy một lần hay chạy lại được nhiều lần.
