# Spec: Let the operating system answer who is working here
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

### A. Câu hỏi trả lời được cho cả máy

**R1 — Một session sống đặt một dấu hiệu mà tiến trình khác đọc được.** Khi app mở một
client trong một workspace của store, nó giữ một dấu hiệu tồn tại ngoài bộ nhớ của nó. Kiểm
được: R6.

**R2 — Dấu hiệu sống đúng bằng đời của client.** Đặt lúc client được tạo, bỏ lúc client
đóng. Không phải đời của một lượt. Kiểm được: mở session, dấu hiệu có; `close`, dấu hiệu
hết; giữa hai lượt vẫn có.

**R3 — `pull` hỏi dấu hiệu, không hỏi bộ nhớ.** `cos_baodo/service.py:189` hiện hỏi
`cos_baodo/sessions.py:163`, chỉ thấy tiến trình này. Sau unit này chỉ còn **một** cách trả
lời câu hỏi, và nó là cách cả máy thấy. Hai cách trả lời một câu hỏi đúng là thứ
`.cos/0003_no-workspace-management/spec.md:57` R10 dựng lên để chặn, và `0003` đã dính một lần
rồi. Kiểm được: không còn chỗ nào trong app đọc `_live` để quyết định `pull`.

**R4 — `pull` giữ dấu hiệu độc quyền suốt thời gian `git` chạy.** Không chỉ hỏi rồi thả.
Kiểm được: trong lúc `pull` chạy, một tiến trình khác mở session trong cùng workspace bị từ
chối.

**R5 — Nhiều session cùng workspace sống chung được.** Dấu hiệu là "có người", không phải
"có một người". Kiểm được: hai client trong cùng workspace, đóng một cái, `pull` vẫn bị từ
chối; đóng nốt thì được.

**R6 — Hai tiến trình, và đây là kết quả của `intent.md`.** Bản A mở session trong workspace
`W`; bản B gọi `pull W` và **bị từ chối**, lý do nêu tên `W`; A đóng session; B gọi lại và
**thành công**. Con số **2** lấy từ `intent.md`.

**R7 — Từ chối xảy ra trước khi `git` chạy.** Từ chối sau khi đã fetch là đã đổi file rồi.
Kiểm được: không tiến trình `git` nào được sinh ra khi bị từ chối — cùng phép kiểm
`.cos/0005_silent-concurrent-loss/spec.md:34` R6 đã dùng, nay bắc qua hai tiến trình.

### B. Không tự dựng một cái treo mới

**R8 — Dấu hiệu chết theo tiến trình giữ nó.** `kill -9` một app đang mở session thì
workspace đó phải `pull` được **ngay**, không cần ai xoá gì. Kiểm được: giết bản A, gọi
`pull` ở bản B trong vòng **5 giây**, thành công. Con số 5 là chọn, không đo: nó chỉ để phép
kiểm không chờ mãi; cơ chế đúng thì độ trễ là 0.

**R9 — Mở session lúc đang có `pull` thì hỏng ngay, không chờ.** `cos_baodo/gitops.py:48`
đặt hạn `pull` là 60 giây; chờ tới đó là một cái treo dưới mắt người dùng. Lỗi nêu tên
workspace và nói đang có `pull`. Kiểm được: giữ `pull` rồi gọi mở session, nhận lỗi trong
**2 giây**. Xem C4 — đây là hành vi `intent.md` không đòi.

**R10 — Không có workspace nào bị chặn vĩnh viễn bởi một tiến trình đã chết.** Là R8 nói
theo hướng ngược, và là ràng buộc "không ai phải dọn tay" của `intent.md`.

### C. Không làm đổ thứ có sẵn

**R11 — Dấu hiệu nằm ngoài workspace.** Nó không được xuất hiện trong `git status` của repo
người dùng. Kiểm được: sau khi mở session trong một workspace là git repo,
`git status --porcelain` trong workspace đó **rỗng**.

**R12 — Thư mục không phải workspace của store thì không có dấu hiệu.** Workspace khai bằng
`COS_WORKSPACES` không có tên và không nằm dưới working folder; `pull` cũng không chạm tới
chúng (`cos_baodo/service.py` chỉ `pull` mục trong store). Kiểm được: không có
`COS_WORKING_DIR` thì app cư xử đúng như `0002` — không file nào được tạo thêm.

**R13 — Ba lệnh chứng minh cũ còn xanh** nguyên trạng: `scripts/verify_0002.py`,
`scripts/verify_0003.py`, `scripts/verify_0005.py`.

**R14 — `npm test` không cần trình duyệt và không tạo session nào.**

**R15 — Một lệnh chứng minh, in từng mệnh đề**, thoát 0 chỉ khi tất cả đúng, và in hết kể cả
khi hỏng.

## Design

**Câu hỏi được giao cho hệ điều hành, vì nó là bên duy nhất thấy cả hai tiến trình.**
`_live` (`cos_baodo/sessions.py:156`) là `dict` trong bộ nhớ; không có cách nào làm nó liên
tiến trình mà không dựng một kho trạng thái. Nhưng câu hỏi thật ra không cần một kho — nó
cần một thứ **chết theo tiến trình**, và `flock` đã là đúng thứ đó. `0005` dựng nó cho danh
sách workspace (`cos_baodo/store.py:43`) và đã đo rằng nó bắc qua tiến trình.

**Khoá chia sẻ cho người làm việc, khoá độc quyền cho `pull`.** Đây là chỗ hình dạng khớp
đúng bài toán thay vì bị ép vào:

| Ai | Giữ gì | Nghĩa là |
|---|---|---|
| mỗi client sống | `LOCK_SH` trên file của workspace đó, suốt đời client | "tôi đang ở đây"; nhiều người cùng giữ được (R5) |
| `pull` | `LOCK_EX`, không chờ, suốt thời gian `git` chạy | "không ai được vào"; trượt nghĩa là có người (R4, R7) |

Không cần đếm ai, không cần dọn gì, không cần heartbeat. Hai tính chất cần thiết rơi ra từ
chính cơ chế: `LOCK_EX` trượt **khi và chỉ khi** có ít nhất một `LOCK_SH` đang giữ, và nhân
hệ điều hành nhả mọi khoá khi tiến trình chết (R8, R10). `flock` xung đột theo **file
description** chứ không theo tiến trình, nên cùng một tiến trình vừa giữ `LOCK_SH` vừa thử
`LOCK_EX` trên descriptor khác vẫn trượt — tức **một cách trả lời duy nhất** phục vụ cả
trường hợp cùng tiến trình lẫn khác tiến trình (R3). Đây là lý do `live_in` biến mất chứ
không được giữ lại làm đường tắt.

**Một file cho mỗi workspace, đặt trong working folder** — trả lời `intent.md` open question
2. Đặt trong workspace thì nó vào `git status` của người ta (R11 cấm). Working folder đã giữ
`.cos-baodo.json` và `.cos-baodo.lock`, nên nó là chỗ những file này đã sống. Tên file suy từ
**tên workspace**, mà tên workspace đã bị ép về một đoạn đường dẫn an toàn ở
`cos_baodo/store.py:54` — nên tên file an toàn **theo cấu trúc**, không phải nhờ một phép
kiểm thêm. Đó cũng là lý do R12 tồn tại: thứ không có tên thì không có file, và cũng không
`pull` được.

**Ranh giới:**

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| lớp phiên → lớp dấu hiệu | một thư mục, lúc tạo client | giữ `LOCK_SH`, hoặc không gì nếu thư mục không phải workspace của store |
| lớp phiên → lớp dấu hiệu | cùng thư mục, lúc đóng client | nhả |
| lớp dịch vụ (`pull`) → lớp dấu hiệu | một tên workspace | giữ `LOCK_EX` suốt `git`, hoặc từ chối ngay |
| lớp dấu hiệu → hệ điều hành | descriptor trong working folder | khoá cố vấn, chết theo tiến trình |

Lớp `git` không đổi và vẫn không biết gì về session.

## Out of scope

- **Session `claude` chạy ở terminal.** `intent.md` đã loại. Người dùng sẽ không phân biệt
  được hai thứ; xem C2.
- **Chặn `remove` hay đổi label.** `.cos/0003_no-workspace-management/spec.md:96` R18: xoá
  không đụng thư mục, nên không cắt file dưới chân ai.
- **Xếp hàng `pull`.** Từ chối, như `0005` đã chốt.
- **Đóng session của tiến trình khác từ trang.** Xem C5.
- **Khoá cho `clone`.** Clone tạo thư mục mới; chưa ai ở trong đó được.
- **Máy không POSIX.** `.cos/0005_silent-concurrent-loss/spec.md:114` C4 nặng thêm một bậc sau
  unit này; xem C6.
- **Đổi hình dạng `.cos-baodo.json`.** Dấu hiệu không phải dữ liệu và không được đọc như dữ
  liệu.

## Concerns

**C1 — Session "chat thuần" vẫn đọc file trong workspace, và app đang hiểu sai vì sao.**
Bản đầu của C1 viết rằng cái hại này là giả định: không tool thì không đọc file, nên `pull`
chẳng cắt gì. **Sai.** Kiểm lại trước khi viết plan thì ra ngược lại, và chỗ sai không nằm
trong lập luận mà nằm trong code.

`cos_baodo/sessions.py:126` truyền `setting_sources=None` kèm chú thích "no project/user
settings can widen the tool list". `claude-agent-sdk` 0.2.157 (ghim ở `uv.lock:179-180`)
nói ngược: `None` nghĩa là **nạp hết** — user, project, local — và `[]` mới là tắt. Tài liệu
của chính trường đó còn ghi rằng phải có `"project"` thì `CLAUDE.md` mới được nạp; `None`
bao gồm `"project"`.

Nên một session, dù không tool nào, vẫn đọc `CLAUDE.md` và `.claude/settings.json` **trong
workspace**. `git pull` đổi được cả hai. Cái hại của `intent.md` là thật, không phải chờ ai
bật knob 1.

Hai điều phải tách cho rõ, vì chúng không cùng số phận:

- **Kết luận "không tool nào" vẫn đúng**, nhưng nhờ một trường khác: `tools=[]` là tập nền
  của built-in tool, và SDK nói `allowed_tools` chỉ quyết định có hỏi quyền hay không. Không
  có settings nào thêm built-in tool vào một tập nền rỗng.
- **Lý do ghi trong chú thích thì sai**, và nó sai theo hướng nguy hiểm: người đọc tiếp theo
  sẽ tưởng `setting_sources=None` là một lớp khoá. Nó là mặc định của CLI.

**Chưa kiểm, và nằm ngoài unit này:** `.claude/settings.json` của một workspace có thể khai
MCP server. Nếu settings được nạp thì đó là một đường mở rộng năng lực **không đi qua**
`tools`, tức lập luận "tập nền rỗng" không che nó. Đây là bề mặt an toàn của
`.cos/0002_no-session-management/spec.md` C2, không phải của `0006`. **Người quyết là tác
giả** — nó đáng một unit riêng, và nó không nên bị sửa lẫn vào đây.

**C2 — Sửa xong vẫn không khớp với câu người dùng nói.** Với họ, "tôi đang mở session trong
`foo`" là một câu. Sau unit này nó là hai: session của app thì thấy, session `claude` gõ ở
terminal thì không. Một người vừa chạy app vừa gõ terminal trong cùng workspace sẽ thấy
`pull` bị chặn lúc này và không bị chặn lúc khác, mà không có quy tắc nào đoán được từ bên
ngoài. `intent.md` open question 1, mang nguyên sang đây.

**C3 — Đời của dấu hiệu là đời của client, và không gì đóng client trừ lúc tắt app.** R2
chốt như vậy vì `intent.md` chốt như vậy ("mở một session" là đủ để chặn). Hệ quả: một app đã
từng chat trong `W` sẽ chặn `pull W` của **mọi tiến trình khác** cho tới khi nó thoát. Dưới
`0005` đây là chuyện riêng của một tiến trình; giờ nó là chuyện của cả máy. Cách khác là buộc
dấu hiệu vào đời một **lượt** — đúng hơn với cái hại (chỉ lúc đang đọc mới bị cắt) và gần như
xoá sạch ngõ cụt — nhưng nó đổi hành vi `0005` R6 đã ship và làm kết quả của `intent.md` sai
theo nghĩa đen. **Cần một intent khác**, không sửa lén ở đây.

**C4 — R9 là hành vi `intent.md` không đòi.** Intent chỉ nói về `pull` bị từ chối. Nhưng khoá
có hai đầu: khi `pull` giữ `LOCK_EX`, việc mở session phải làm gì đó, và "không quyết" vẫn là
một quyết định — nó sẽ thành chờ tới 60 giây rồi mới hỏng. Spec chọn hỏng ngay. Ghi ở đây vì
nó vượt phạm vi intent chứ không phải vì nó sai.

**C5 — Ngõ cụt rộng ra, không hẹp lại.** `0005` open question 5: bị từ chối rồi thì không có
cách đóng session từ trang. Nay session nằm ở **tiến trình khác**, nên trang của B không có
đường nào chạm tới nó kể cả khi ai đó viết nút ấy. Lối thoát duy nhất là tắt bản app kia.
Không có gì trong unit này làm nó tốt hơn.

**C6 — `flock` từ chỗ tiện thành chỗ chịu lực.** Ở `0005` mất `flock` nghĩa là mất khoá
danh sách. Ở đây nó còn là cách duy nhất trả lời "ai đang làm việc", nên trên máy không POSIX
câu trả lời sẽ là "không ai" — tức `pull` chạy luôn, im lặng, đúng cái lỗi đang sửa. Hỏng lúc
khoá, không hỏng lúc cài. Nặng hơn `.cos/0005_silent-concurrent-loss/spec.md:114` C4 một bậc.

**C7 — `flock` trên NFS và trên ổ mount từ Windows là chỗ nó hay không đúng.** Repo chạy
Linux qua WSL; một working folder đặt trên `/mnt/c` là tình huống rất dễ xảy ra ở đúng máy
này và **chưa kiểm**. Nếu khoá im lặng không có tác dụng ở đó thì mọi phép kiểm vẫn xanh trên
`/home` và sai ở chỗ người ta thật sự để code. Đáng kiểm một lần trong lúc làm.

**C8 — `CLAUDE.md` của chính repo này nằm trong một workspace người ta sẽ `pull`.** Hệ quả
trực tiếp của C1, và đáng nói riêng vì nó là trường hợp gần nhất: `cos-baodo` tự nó là một
workspace hợp lệ. Một `pull` trên nó trong lúc có session sống đổi đúng file mà session đang
đọc để biết luật của repo.

**C9 — Phép kiểm dễ xanh vì sai lý do.** `intent.md` open question 5 lo phải dàn cuộc đua
đúng lúc A ở giữa một lượt. Hình dạng ở `## Design` **xoá** lo đó: dấu hiệu sống bằng đời
client nên không cần bắt đúng khoảnh khắc. Đổi lại là một cái bẫy khác — nếu lệnh kiểm chạy
hai tiến trình mà chúng không thật sự dùng chung working folder, mọi mệnh đề sẽ xanh mà
không chứng minh gì. Lệnh kiểm phải cho thấy **file dấu hiệu là cùng một file**, không chỉ
cho thấy kết quả đúng.

## Open questions

1. **Còn mở** (`intent.md` OQ1, và là C2): session terminal.
2. **Đã trả lời** (OQ2): một file cho mỗi workspace, trong working folder. Lý do ở
   `## Design`; R11 là phép kiểm.
3. **Còn mở** (OQ3, và là C5): bị từ chối rồi thì làm gì. Rộng hơn `0005` OQ5 một bậc.
4. **Đã trả lời** (OQ4, và là C6): có, `flock` nặng thêm một bậc. C7 là phần chưa kiểm.
5. **Đã trả lời** (OQ5): không cần dàn cuộc đua nữa — xem C9 cho cái bẫy thay thế.
6. **Mới:** hai app chạy với **hai** `COS_WORKING_DIR` khác nhau nhưng trỏ vào cùng một
   workspace qua symlink thì sao? Dấu hiệu nằm theo working folder, nên chúng sẽ là hai file
   khác nhau và không thấy nhau. Hẹp, nhưng nó là đúng lỗ này ở một hình dạng khác.
7. **Mới:** nên có cách nào đọc được "ai đang giữ dấu hiệu" không? Hiện lỗi chỉ nói *có*
   người. Trên một máy có hai app, biết là bản nào sẽ là khác biệt giữa sửa được và đoán.
