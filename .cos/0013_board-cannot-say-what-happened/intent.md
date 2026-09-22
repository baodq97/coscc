# Intent: the board cannot say what happened to a unit, only where it is now
Author: Bao Do. Type: feat. Status: accepted.

> **Sửa ngày 2026-09-22, sau khi bản đầu đã accepted và commit (`d41ba8e`), trước khi viết
> spec.** Người khởi xướng nói thêm: *"lúc mở pr chỉ cần output của write pr là được...
> còn phần coscc này là giúp tôi hoàn thành các công việc thôi. không nên đưa vào các repos
> nhỉ. vì repos là cả team cùng làm."*
>
> Bản đầu ghi rằng artifact được **xuất vào repo đích** lúc mở PR. Sai, và sai theo hướng
> mình đề nghị chứ không phải hướng người khởi xướng muốn. Lý do bác bỏ mạnh hơn lý do
> đề nghị: repo là của cả team, còn `.cos/` là quy trình riêng của một người; commit tám
> file markdown vào đó là bắt những người không chọn quy trình ấy phải mang nó. Thứ reviewer
> cần nằm ở **mô tả PR**, và `write-pr` đã sinh sẵn đúng thứ đó.
>
> **Không có gì của coscc đi vào cây của repo đích.** `pr.md` trở thành body của PR. Sửa
> ở đây vì đây là chỗ cuối cùng một thay đổi còn tốn một đoạn văn thay vì một lần viết lại.

## Problem

Người khởi xướng nói ngày 2026-09-22, sau khi lần đầu cài bản phát hành và dùng thử:
*"khi làm board là tôi đã muốn bỏ phần quản lý unit ở repos rồi, thay vào đó sản phẩm này
sẽ managed quản lý thông qua db, và fs... mỗi task trên board là một unit, sẽ biết được bao
nhiêu output file được sinh ra, ở đâu, chuyển state ra sao, back lại lúc nào, agent làm, và
session chat của agent đó"*.

Không câu nào trong đó trả lời được hôm nay, và lý do là một: **trạng thái của công việc
được suy ngược ra từ chữ trong file, nên thứ duy nhất tồn tại là hiện tại.**

`.claude/scripts/cos.mjs` mở artifact và đọc dòng `Status:`; `coscc/board.py:63-81` biến kết
quả đó thành tám dòng một unit. Một artifact đã chốt rồi bị viết lại thì dòng `Status:` cũ
biến mất cùng nội dung cũ. Không có chỗ nào ghi rằng nó đã từng ở đâu.

**Đo được bao nhiêu thứ đã mất như vậy.** Chạy trên chính repo này ngày 2026-09-22, đọc
lịch sử git của 57 artifact trong `.cos/`:

| | |
|---|---|
| artifact từng đạt một `Status` đã chốt (`accepted`, `done`, `skipped`) | **56** |
| trong số đó, bị sửa tiếp sau khi đã chốt | **22** |
| tổng số lần sửa sau khi đã chốt | **39** |
| số lần board hiển thị được | **0** |

Bốn trường hợp nặng nhất: `0008_personal-name-blocks-publishing/plan.md` 6 lần,
`0007_stale-claims-and-dead-code/plan.md` 5 lần, `0008.../intent.md` và
`0009_branch-and-release-conventions/intent.md` mỗi cái 3 lần. Hai trong số đó tự mô tả lý
do ở đầu file — `0011_no-install-path-on-a-clean-machine/intent.md` mở đầu bằng một khối
`> **Sửa ngày 2026-09-22, sau khi bản đầu đã accepted và commit (`d2f55fd`)**`. Nghĩa là
cách duy nhất hiện có để ghi lại một lần quay lui là **viết nó bằng văn xuôi vào chính file
bị quay lui**, và nó chỉ ở đó khi người viết nhớ làm.

**Provenance thì trống hẳn.** `~/.cos/cos.db` có bảng `runs`
(`coscc/journal.py:176-180`, các cột `at, root, workspace, unit, stage, kind, record`), và
hôm nay nó có **0 dòng**. Cả 57 artifact đều được tạo ngoài app, nên với mọi unit trong repo
này, câu hỏi "agent nào làm, session nào" không có nguồn nào trả lời được ngoài git — mà git
chỉ biết tác giả commit, không biết session.

**Một chi tiết sẽ làm hỏng mọi thiết kế bỏ qua nó.** `coscc/runner.py:271-274` truyền
`None` làm `session_id` cho mọi bước, nên **mỗi bước mở một session mới**: một unit chạy
đủ tám stage sinh ra tám session rời nhau. Không phải vì resume phân nhánh —
`coscc/sessions.py:184` đặt `fork_session=False` với ghi chú *"R3 needs the same id back,
not a branch"*, tức resume giữ nguyên id — mà vì bước board không bao giờ resume. Kết quả
vẫn là: một unit không có "session của nó", nó có một chuỗi. Chỗ nào giữ `session_id` số ít
sẽ sai từ bước thứ hai.

Hai cơ chế này khác nhau và phải giữ tách: *một session kéo dài qua nhiều lượt* (chat, dùng
resume) và *nhiều session nối nhau qua nhiều bước* (board). Trộn hai thứ vào một trường là
cách "session chat của agent đó" trỏ sai chỗ.

Hệ quả cuối, và là lý do việc này chặn những việc khác: vì trạng thái nằm trong file của một
cây làm việc, **hai unit không chạy song song được**. Mọi thứ người khởi xướng muốn xây
tiếp — feed bình luận trên ticket, người nhảy vào session của agent, worktree cho mỗi
ticket — đều gắn vào một thứ chưa tồn tại.

## Proposed outcome

Trước 2026-10-20: sản phẩm liệt kê được, cho 12 unit đang có trong `.cos/`, **đúng 39 lần
một artifact bị sửa sau khi đã chốt**, mỗi lần kèm artifact nào, từ trạng thái nào sang
trạng thái nào, và thời điểm — đối chiếu với chính lịch sử git bằng
`scripts/verify_0013.py`. Hôm nay con số nó liệt kê được là **0**.

Outcome này sai được, và nó không cần một agent nào để đo: 39 sự kiện là dữ liệu có thật
đã nằm trong repo.

## Affected users and systems

- **Người dùng board** — hôm nay không có ai ngoài người viết nó.
- **`.claude/scripts/cos.mjs`** và **`coscc/board.py`** — hai chỗ hiện quyết định "một stage
  đang ở đâu" bằng cách đọc file.
- **`coscc/journal.py`** — bảng `runs` đã tồn tại và đang trống; nó ghi *lần chạy*, không
  ghi *chuyển trạng thái*, và hai thứ đó không phải một.
- **`.cos/` trong repo đích — biến mất hoàn toàn.** Người khởi xướng chốt 2026-09-22: DB
  là nguồn sự thật, và **không file nào của coscc đi vào cây của repo đích**. Thứ duy nhất
  tới được repo là nội dung `pr.md`, dưới dạng mô tả của pull request. Lý do là repo dùng
  chung cho cả team, còn vòng lặp này là công cụ riêng của một người.
- **Repo này là ngoại lệ, và phải được xử lý có chủ ý.** `.cos/` ở đây có 57 artifact và
  chính là dữ liệu mà outcome đo. Nó ở lại như **hồ sơ lịch sử đóng băng**, không phải như
  nơi công việc mới được ghi tiếp.
- **`0012_installed-copy-runs-no-stage`** — phần đóng gói `cos.mjs` vừa ship sẽ bị hướng này
  xoá. `.cos/0012_installed-copy-runs-no-stage/spec.md` mục `## Out of scope` đã ghi trước
  điều đó.

## Constraints

1. **Trạng thái không được suy ra từ nội dung hiện tại của artifact.** Đó chính là cơ chế
   làm mất 39 sự kiện. Một thiết kế vẫn đọc `Status:` để biết "đang ở đâu" là thiết kế chưa
   giải quyết vấn đề này.
2. **Một unit mang một *chuỗi* session, không phải một session** — `coscc/runner.py:271-274`.
   Và chuỗi đó phải phân biệt được với một session dài nhiều lượt, thứ mà chat dùng.
3. **`cos.mjs` chưa được xoá trong unit này.** Nó là thứ duy nhất hôm nay biết đọc `.cos/`,
   nên nó phải chạy song song làm nguồn đối chiếu cho đến khi có thứ thay được. Xoá nó là
   việc của một unit sau.

   Kèm theo đó là một giới hạn phải nói ra: khi workspace không còn `.cos/`, **đối chiếu chỉ
   còn thực hiện được trên repo này**, nơi lịch sử đã có sẵn. Với công việc mới ở một repo
   khác, không có nguồn thứ hai nào để so.
4. **Không agent nào trong phạm vi unit này.** Feed bình luận, người nhảy vào session,
   worktree cho mỗi ticket — cả ba đều gắn vào thứ unit này xây, và không cái nào được xây ở
   đây. Một unit cố làm cả bốn là một unit không đóng được.
5. **Dữ liệu cũ phải vào được.** Outcome đòi 39 sự kiện lịch sử, nên thứ xây ra phải nhận
   được lịch sử có sẵn, không chỉ ghi được cái mới.

## Open questions

1. ~~Xuất `.cos/` lúc mở PR là xuất cái gì?~~ **Đã trả lời 2026-09-22:** không xuất gì cả;
   `pr.md` thành body của PR. Còn lại một câu con chưa quyết: nếu DB mất, thứ duy nhất còn
   lại trong repo dùng chung là mô tả các PR trên GitHub. Có chấp nhận đó là bản sao lưu duy
   nhất không, hay `pr.md` cũng cần được giữ ở nơi khác.
2. **"Bao nhiêu output file được sinh ra, ở đâu"** — một task vừa sinh artifact vừa sửa code
   trong repo. Hai loại file, hai đích. Đếm chung hay tách, chưa quyết.
3. **Trạng thái nào tồn tại?** Hôm nay là tám stage cố định với bốn `Status`. Người khởi
   xướng nói *"task đi theo state machine rõ ràng"* — chưa rõ state machine đó có giữ đúng
   tám stage ấy hay không. Nếu đổi, `cos.mjs` và bảng đối chiếu ở ràng buộc 3 sẽ đo hai thứ
   khác nhau.
4. **Trần chi cho cả board.** `coscc/policy.py` chặn theo từng step, không theo máy. Khi
   nhiều ticket chạy song song thì trần chung nằm ở đâu, chưa có chỗ nào trả lời.
5. **`0011` và `0012` đo cái gì sau khi hướng này chạy?** Cả hai đo một sản phẩm mà `node` và
   `cos.mjs` còn là bộ phận. Chưa quyết là sửa proof cũ hay để chúng chết cùng thứ chúng đo.
