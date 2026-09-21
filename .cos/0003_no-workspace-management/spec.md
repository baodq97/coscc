# Spec: Workspaces managed under one env-declared working folder
Intent: intent.md. Author: Bao Do. Status: accepted.

## Requirements

Mỗi yêu cầu truy về chuỗi đo ở `intent.md:38-41`: clone 2 → đếm 2 → label → khởi động lại →
vẫn 2 và label còn → pull latest → session trong mỗi workspace → xoá một → còn 1 → khởi
động lại → vẫn 1.

**R1 — Working folder là một gốc, khai báo bằng env.** `COS_WORKING_DIR` đọc trong
`from_env` và không ở đâu khác. Không route nào đặt, trả về đường dẫn tuyệt đối của nó để
sửa, hay nhận nó làm tham số. Kiểm được: không có setter nào ngoài `from_env`; một request
gửi kèm `working_dir` bị bỏ qua hoàn toàn, không phải bị ghi đè.

**R2 — Workspace được định danh bằng `name`, không bằng đường dẫn.** `name` là **một đoạn
đường dẫn duy nhất**: chỉ `[A-Za-z0-9._-]`, dài 1–64 ký tự, không phải `.` hay `..`. Đường
dẫn thật luôn là `working_dir / name` và **không bao giờ** đến từ request. Kiểm được:
`../x`, `/etc`, `a/b`, chuỗi rỗng, tên 65 ký tự — tất cả bị từ chối 400, và không có input
nào tạo ra được một đường dẫn nằm ngoài `working_dir`.

**R3 — Đếm và liệt kê.** Một đường đọc trả về số workspace và danh sách, mỗi mục gồm `name`,
đường dẫn, `label`, nguồn (`env` hay `store`), và cờ `missing` khi thư mục không còn trên
đĩa. Kiểm được: số trả về khớp đúng 2 → 1 trong chuỗi đo.

**R4 — Thêm.** Hai cách, cùng một đường: kèm `repo_url` thì clone; không kèm thì nhận một
thư mục đã có sẵn dưới `working_dir`. Cả hai được `intent.md:92` cho phép — "thêm" và
"clone" là hai mục riêng trên dòng phạm vi. Kiểm được: sau khi thêm, `name` xuất hiện ở R3
và `working_dir / name` tồn tại.

**R5 — Clone chỉ `https://`, không tương tác, môi trường tối thiểu.** `git` được gọi bằng
argv (không qua shell), subcommand cố định, không nhận cờ nào từ người dùng, `repo_url`
phải bắt đầu bằng `https://` và không bắt đầu bằng `-`. Tiến trình `git` nhận một môi
trường **dựng mới**, chỉ gồm `PATH`, `HOME`, `GIT_TERMINAL_PROMPT=0` và một `GIT_ASKPASS`
luôn thất bại — không kế thừa môi trường của app. Kiểm được: repo riêng tư trả về lỗi
trong vòng thời gian chờ thay vì treo; `CLAUDE_CODE_OAUTH_TOKEN` và mọi biến `COS_*` không
có trong môi trường tiến trình con.

**R6 — Clone hoặc thành công trọn vẹn, hoặc không để lại gì.** Clone đi vào một thư mục tạm
dưới `working_dir` rồi mới đổi tên sang `name`. Thất bại thì thư mục tạm bị dọn và không có
mục nào được ghi vào store. Kiểm được: ép clone hỏng (URL không tồn tại), sau đó R3 trả về
đúng số cũ và `working_dir` không có thư mục thừa.

**R7 — Sửa label.** `label` là chuỗi ≤ 200 ký tự, sửa được, và không ảnh hưởng đường dẫn hay
định danh. Kiểm được: đặt label, đọc lại đúng chuỗi đó.

**R8 — Xoá là gỡ khỏi danh sách, không xoá đĩa.** Xoá một workspace bỏ mục của nó khỏi
store; thư mục trên đĩa **không** bị đụng tới. Kiểm được: sau khi xoá, R3 còn 1 mục và
`working_dir / name` vẫn tồn tại.

**R9 — Trạng thái sống qua khởi động lại, không cần biến môi trường nào đổi.** Dựng lại app
từ đúng môi trường cũ thì danh sách và label còn nguyên. Kiểm được: đây là hai mắt xích
"tắt bật lại" trong chuỗi đo, và store phải là thứ duy nhất mang trạng thái đó.

**R10 — `pull latest` chạy được và báo thất bại ra ngoài.** `git -C <path> pull --ff-only`,
cùng ràng buộc môi trường như R5. Không fast-forward được (cây bẩn, nhánh phân kỳ) là **lỗi
được trả về**, không phải im lặng. Kiểm được: pull một workspace sạch thì thành công; làm
bẩn cây rồi pull thì nhận lỗi có nội dung.

**R11 — Ranh giới workspace không nới ra.** `is_workspace` vẫn là cổng gác của mọi đường
làm việc (`app/web.py:44`, `:60`, `:85`). Sau unit này nó nhận một path khi và chỉ khi path
đó nằm trong danh sách env **hoặc** giải ra đúng `working_dir / name` của một mục trong
store. Việc kiểm phải làm **lúc đọc**, mỗi lần, chứ không phải lúc ghi. Kiểm được: sửa tay
file store để trỏ ra ngoài `working_dir`, mọi đường làm việc vẫn từ chối.

**R12 — Session không đổi tư thế.** Session vẫn chat only, không tool nào
(`app/config.py:44-47`). Không knob nào của `0002` bị lật. Kiểm được: `effective_tools()`
vẫn rỗng trong chuỗi đo, và session tạo trong mỗi workspace vẫn trả lời được.

**R13 — Chứng minh không cần trình duyệt, chỉ loopback.** Toàn bộ chuỗi đo chạy qua đúng bề
mặt HTTP mà tab dùng, như `0002` R4 (`.cos/0002_no-session-management/spec.md:23-25`), và
vẫn chỉ bind `127.0.0.1`.

**R14 — `0002` còn xanh.** `scripts/verify_0002.py` dựng `Config` thẳng
(`scripts/verify_0002.py:96`) và không biết gì về working folder. Nó phải chạy nguyên trạng
sau unit này.

## Design

**Cách chặn đường thoát ra ngoài gốc là hình dạng dữ liệu, không phải một hàm kiểm.** Store
chỉ lưu `name` — một đoạn đường dẫn. Không có đường dẫn tuyệt đối nào trong store, nên
không có gì để một file bị sửa tay trỏ ra ngoài `working_dir`. Đường dẫn được **dựng ra**
từ gốc mỗi lần đọc. Kiểm containment vẫn còn (R11) như lớp thứ hai, nhưng lớp thứ nhất là
việc không tồn tại một chỗ nào để đặt `/etc` vào. Mọi thứ khác trong thiết kế chảy ra từ
câu này.

**Bốn lớp.** Ba lớp của `0002` giữ nguyên vai trò; unit này thêm một lớp và sửa một cổng
gác.

- **Lớp store (mới).** Đọc và ghi danh sách workspace. Không biết HTTP, không biết `git`.
  Ghi bằng file tạm rồi `rename`, và nối tiếp nhau bằng một khoá trong tiến trình, để hai
  request đồng thời không cắt đuôi nhau.
- **Lớp git (mới).** Gọi `git` bằng argv với môi trường dựng mới (R5). Chỉ hai thao tác:
  clone và pull. Không nhận cờ, không nhận subcommand từ ngoài.
- **Lớp cấu hình.** `app/config.py` vẫn là nơi duy nhất đọc môi trường
  (`app/config.py:97-103`). Nó nay trả lời `is_workspace` bằng hợp của hai nguồn: danh sách
  env bất biến, và store. Đây là chỗ `app/config.py:1-11` dự trù khi nói đổi nguồn cấu hình
  là đổi một chỗ.
- **Lớp HTTP.** Thêm các đường ghi cho danh sách workspace. Cùng bề mặt phục vụ cả tab lẫn
  lệnh kiểm, không có đường riêng cho test — lý do như `0002`.

**Ranh giới và dữ liệu đi qua.**

| Ranh giới | Đi vào | Đi ra |
|---|---|---|
| Trình duyệt → HTTP | `name`, `label`, `repo_url` | số đếm, danh sách, lỗi |
| HTTP → store | `name`, `label` | mục, hoặc danh sách mục |
| HTTP → git | `name`, `repo_url` | thành công, hoặc lỗi có nội dung |
| git → mạng | `repo_url` | nội dung repo |
| store → đĩa | mục | file JSON dưới `working_dir` |
| cấu hình → mọi cổng gác | một path | thuộc/không thuộc |

**Store nằm ngoài repo, dưới chính working folder.** Trả lời `intent.md:105-106` (OQ5):
không commit, vì nó chứa những gì người dùng đã clone về máy mình. Đặt dưới `working_dir`
để nó đi cùng cái gốc mà nó mô tả, và để không có trạng thái nào của app nằm ở chỗ thứ ba.
File mang một số `version` để lần đổi hình dạng sau không phải đoán.

**Hai nguồn workspace, thứ tự rõ ràng.** `COS_WORKSPACES` của `0002` không bị bỏ và không
được tự động chuyển vào store. Một path thuộc nếu nó có ở một trong hai nguồn. Khi
`COS_WORKING_DIR` không được đặt, lớp store tắt hẳn và app cư xử đúng như `0002` — đó là
cách R14 được giữ.

**"Khởi động lại" trong lệnh kiểm** là dựng lại app từ đúng môi trường cũ, không phải dựng
lại từ một `Config` tạo trong bộ nhớ. Nếu trạng thái sống sót qua một lần dựng lại như thế
thì nó đến từ đĩa chứ không từ tiến trình, và đó là toàn bộ điều R9 cần chứng minh.

**Không có kho dữ liệu thứ hai cho session.** Store chỉ chứa workspace và label. Session
store của SDK vẫn là nguồn sự thật cho hội thoại
(`.cos/0002_no-session-management/spec.md:135-141`).

## Out of scope

- **Đăng ký một thư mục bất kỳ trên máy bằng đường dẫn tuyệt đối.** `intent.md:70-75` cấm.
  Project nằm ngoài gốc vẫn đi qua `COS_WORKSPACES`. Xem C4 — đây là chỗ đau nhất của
  thiết kế này.
- **Clone repo riêng tư.** Cần credential, và app không có chỗ hỏi. R5 chọn thất bại nhanh
  thay vì treo. `intent.md:97-99` (OQ1) được trả lời theo nghĩa "không treo", không theo
  nghĩa "làm được".
- **`git` ngoài clone và pull.** Branch, commit, push, fetch, checkout, submodule. Chúng
  thuộc intent sau, và intent đó phải bật tool cho session trước
  (`intent.md:80-82`).
- **Bật tool cho session.** Bốn knob của `0002` giữ nguyên mặc định
  (`.cos/0002_no-session-management/spec.md:112-115`).
- **Hiện branch, trạng thái sạch/bẩn, số commit đi sau** trong danh sách. Hữu ích, nhưng là
  năng lực thứ ba và tác giả đã cắt nó khi chọn phạm vi git.
- **Xoá thư mục khỏi đĩa.** Xem R8 và C6.
- **Tự động dọn workspace trỏ vào thư mục đã mất.** Hiện cờ `missing`, không tự xoá.
- **Nhiều người dùng, đăng nhập, TLS, giao diện đẹp.** Như `0002`.

## Concerns

**C1 — `is_workspace` mất chỗ dựa cũ.** Ở `0002` nó đứng được một phần nhờ danh sách bất
biến suốt đời tiến trình (`app/config.py:82-94`). Sau unit này danh sách đổi được trong lúc
chạy, nên cùng một hàm phải chịu tải lớn hơn. Câu trả lời của thiết kế là R2 và R11: không
lưu đường dẫn, dựng từ gốc, kiểm lúc đọc. **Chỗ này hỏng thì mọi thứ khác trong `0002` hỏng
theo**, vì cả ba đường làm việc đều gọi nó.

**C2 — App lần đầu chạy tiến trình ngoài và lần đầu chạm mạng.** `0002` không làm cả hai.
`.cos/0002_no-session-management/spec.md:121-125` (C3) nói rõ: đường nào trong app chạy lệnh
theo chữ người dùng gửi là đường làm lộ token. R5 là câu trả lời trực tiếp cho câu đó — argv
chứ không shell, subcommand cố định, môi trường dựng mới chứ không kế thừa. Đây là yêu cầu
an toàn trung tâm của unit này, không phải một chi tiết hiện thực.

**C3 — Thời gian chờ là một quyết định, không phải một phép đo.** Clone và pull phải có hạn
thời gian, nếu không một repo lớn hoặc một host im lặng giữ mãi một request. Con số đề xuất:
**120 giây cho clone, 60 giây cho pull**. **Không có nguồn** — đây là lựa chọn ở đây, chưa
đo lần nào trên repo thật, và là thứ nên chỉnh sau lần chạy đầu chứ không nên tin.

**C4 — Unit này không giải hết vấn đề mà `intent.md` nêu, và đó là một mâu thuẫn có thật.**
`intent.md:6-9` than rằng thêm một project nghĩa là tắt app, sửa env, bật lại. Với ràng buộc
gốc-bằng-env ở `intent.md:70-72`, điều đó **vẫn đúng** cho mọi project đang nằm ngoài
`working_dir` — tức tất cả project hiện có. Chỉ project sinh ra dưới gốc mới thoát.

Ba lối, và spec này **không** chọn: (a) chấp nhận, và chuyển dần project về dưới gốc; (b)
cho `COS_WORKING_DIR` nhận nhiều gốc, vẫn env, vẫn không đặt được qua HTTP; (c) cho đăng ký
đường dẫn tuyệt đối, tức bỏ ràng buộc `intent.md:70-75`. **Người quyết là tác giả**, vì (b)
và (c) đều sửa constraint trong intent chứ không phải sửa thiết kế. Nếu không ai quyết,
mặc định là (a) — và khi đó phải nói thẳng rằng vấn đề chỉ được giải một nửa.

**C5 — Clone dở dang.** Một clone bị ngắt giữa chừng để lại thư mục lưng chừng trong
`working_dir`, và nếu store đã ghi trước thì danh sách có một mục không dùng được. R6 chọn
thứ tự clone-vào-tạm → rename → ghi store, nên trạng thái xấu nhất là một thư mục tạm bị bỏ
quên, không phải một workspace hỏng. Thư mục tạm bị bỏ quên vẫn là rác **chưa ai dọn**.

**C6 — Xoá không đụng đĩa là quyết định, và nó sẽ gây khó chịu.** Người dùng bấm xoá sẽ có
lúc nghĩ là thư mục biến mất. Lý do chọn: app không có undo, còn một thư mục clone có thể
chứa công việc chưa commit. Hậu quả: `working_dir` sẽ đầy dần những thư mục không còn trong
danh sách. Đây là lựa chọn giữa rác và mất việc, và unit này chọn rác.

**C7 — `pull --ff-only` sẽ thất bại thường xuyên, và đó là hành vi đúng.** Cây bẩn, nhánh
phân kỳ, không có upstream — tất cả đều là lỗi. Rủi ro không nằm ở việc nó thất bại mà ở
việc nó thất bại **im lặng**; R10 đòi lỗi phải ra tới người gọi. Thêm một chỗ chưa ai nghĩ
hết: pull vào một workspace đang có session chạy dở sẽ đổi file dưới chân Claude giữa lượt.
Không có khoá nào ngăn, và unit này không dựng.

**C8 — Store là trạng thái đầu tiên app tự sở hữu.** `0002` cố ý không có
(`.cos/0002_no-session-management/spec.md:61`). Một khi có file để ghi, cám dỗ ghi thêm
message, tóm tắt, thứ tự session vào đó là có thật — và
`.cos/0002_no-session-management/spec.md:135-141` (C6) đã viết sẵn lý do không được:
nguồn sự thật của hội thoại là cái Claude thực sự đọc. Store này chỉ được chứa workspace và
label.

**C9 — Đây chưa phải kho cấu hình trung tâm ở `0002` C8.**
`.cos/0002_no-session-management/spec.md:146-151` nói tới một kho phục vụ nhiều hồ sơ agent.
Store ở đây lưu workspace, không lưu knob, không lưu hồ sơ. Bốn knob vẫn chỉ đọc từ env.
Nhầm hai thứ này là cách một file JSON nhỏ lớn lên thành schema chưa ai thiết kế.

**C10 — Hạn mức vẫn không được đếm.** `.cos/0002_no-session-management/spec.md:127-128`
(C4) còn nguyên hiệu lực, và unit này thêm hai thứ tiêu được: băng thông và đĩa. Một vòng
lặp hỏng gọi clone tốn nhiều hơn một vòng lặp hỏng gọi chat.

## Open questions

1. **Đã trả lời** (`intent.md:97-99`, OQ1): clone repo riêng tư nằm ngoài phạm vi. R5 chọn
   thất bại nhanh và rõ thay vì treo chờ một mật khẩu không ai gõ được. Credential cần một
   intent riêng.
2. **Đã trả lời** (`intent.md` OQ2): metadata chỉ có `label`. Nguồn, cờ `missing` và đường
   dẫn là thứ tính ra lúc đọc, không lưu. Mỗi trường lưu thêm là một trường phải di trú.
3. **Đã trả lời** (`intent.md` OQ3): xoá là gỡ khỏi danh sách. Xem R8 và C6.
4. **Đã trả lời** (`intent.md` OQ4): hiện cờ `missing`, không tự dọn, và từ chối tạo session
   trong đó. Tự dọn là thao tác không hồi phục được dựa trên một suy đoán.
5. **Đã trả lời** (`intent.md` OQ5): file JSON dưới `working_dir`, không commit.
6. **Còn mở** (`intent.md` OQ6): docstring sai của `with_workspaces`
   (`app/config.py:117-118` so với `scripts/verify_0002.py:96`). Sửa docstring là một dòng;
   câu hỏi thật là hàm đó còn nên tồn tại không khi store đã có. Plan quyết.
7. **Còn mở, và là câu ở C4:** một gốc hay nhiều gốc? Trả lời "nhiều" thì sửa `intent.md:70`
   và phần lớn vấn đề gốc được giải ngay; trả lời "một" thì unit này giải một nửa và phải
   ghi rõ như vậy. Đợi tác giả.
8. **Còn mở:** hai app cùng chạy trên một `working_dir` thì store bị hai tiến trình ghi.
   Khoá trong tiến trình không đủ. Chưa gặp, vì đây là công cụ một người — nhưng `0002` OQ3
   đã cho thấy chính xác kiểu hỏng này mất dữ liệu im lặng
   (`.cos/0002_no-session-management/spec.md:165-176`), nên nó đáng ghi trước khi đáng sửa.
