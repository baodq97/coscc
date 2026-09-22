# Spec: Real data behind COS Studio, kept in one durable place
Intent: intent.md. Author: Claude Opus 5. Status: accepted.

## Skip assessment

Năm tiêu chí, từng cái một:

1. Chạm **hai file trở xuống**? — **Trượt.** Riêng việc thay dữ liệu mẫu bằng dữ liệu
   thật đã chạm `cos_baodo/prototype.py` (1372 dòng) và `cos_baodo/cos_baodo.py`
   (868 dòng), chưa kể tầng lưu trữ.
2. Không đổi **schema hay dữ liệu đã lưu**? — **Trượt.** Đây là nội dung chính: bỏ
   `.cos-baodo.json` (`cos_baodo/store.py:41-42`) và `.cos-journal.jsonl`
   (`cos_baodo/journal.py:36-37`), thay bằng SQLite.
3. Không thêm **dependency**? — **Đạt.** `sqlite3` nằm trong thư viện chuẩn Python;
   `python3 -c "import sqlite3"` trên máy này ngày 2026-09-22 báo SQLite 3.46.1.
4. Không có **hành vi ngoài intent**? — **Đạt.**
5. Không chạm **auth, PII, bề mặt an toàn**? — **Trượt.** Chạm đúng chỗ nhạy cảm nhất:
   nơi workspace được lưu, và tính chất chống mất ghi đồng thời mà `scripts/verify_0004.py`
   đã chứng minh.

Ba tiêu chí trượt. Spec là bắt buộc. Tiêu chí ép mạnh nhất là **số 5**.

## Requirements

**Nơi cất dữ liệu**

- **R1.** App có đúng **một** thư mục dữ liệu. Mặc định `~/.cos`. Đọc được từ biến môi
  trường, và chỉ đọc trong một hàm duy nhất — cùng cơ chế đã giữ `COS_WORKING_DIR` không
  set được qua HTTP (`cos_baodo/config.py:110-133`). Không có setter nào khác.
- **R2.** Thư mục đó tự tạo khi chưa có, với quyền `0o700`. Một người chạy app lần đầu
  không phải làm gì trước.
- **R3.** Metadata nằm trong **một** file SQLite trong thư mục đó. Object nằm thành file
  riêng trong một thư mục con của nó. Không có trạng thái app nào nằm ngoài hai chỗ này.
- **R4.** Thư mục dữ liệu **không** chứa workspace. Đường dẫn workspace vẫn dựng từ
  `COS_WORKING_DIR`. Một request không có đường nào đặt được `COS_WORKING_DIR`, và tính
  chất này phải còn đúng sau thay đổi.

**Schema và tính bền**

- **R5.** Database mang số phiên bản schema. Mở một database phiên bản cao hơn code thì
  app **từ chối khởi động** kèm thông báo nêu cả hai số, chứ không đoán.
- **R6.** Một entry workspace vẫn lưu **tên** — một path segment, qua `require_name`
  (`cos_baodo/store.py:75-83`) — và không có cột nào chứa đường dẫn tuyệt đối tới
  workspace. Một database sửa tay không trỏ được app vào `/etc`.
- **R7.** `Store` và `Journal` giữ nguyên interface đang có. Đổi chỗ chứa, không đổi cách
  gọi. `cos_baodo/service.py` không được đổi vì lý do lưu trữ.
- **R8.** Một lần duy nhất, tự động: nếu `<root>/.cos-baodo.json` tồn tại và root đó chưa
  có bản ghi nào trong database, các entry hợp lệ được nhập vào. File JSON **không bị
  xóa**. Lần nhập được ghi lại để không chạy lần hai. Trên máy này ngày 2026-09-22 file
  đó có **1** entry (`cos-baodo`), đọc trực tiếp từ đĩa.
- **R9.** Bốn tiến trình ghi đồng thời **20** entry vào một database giữ đủ **20**. Đây
  là đúng số và đúng cách đo mà `scripts/verify_0004.py` đang dùng; proof đó phải chạy
  lại trên cơ chế mới và xanh.
- **R10.** Chờ database bận có **hạn**, mặc định **10 giây** — bằng `LOCK_TIMEOUT` hiện
  tại (`cos_baodo/store.py:49`, `cos_baodo/journal.py:43`). Hết hạn thì báo lỗi nêu tên
  file, không treo.
- **R11.** Ghi object là **atomic**: một object đọc được thì nội dung của nó đầy đủ. Một
  lần crash giữa chừng không để lại object hỏng mang tên thật.

**Sáu màn trên dữ liệu thật**

- **R12.** Không module UI nào import dữ liệu mẫu. Sáu màn lấy toàn bộ nội dung qua
  `Service`.
- **R13.** Năm luồng của outcome chạy được trên dữ liệu thật: (a) thêm/đổi nhãn/xóa và
  chọn workspace; (b) tìm và mở một work unit trên board; (c) xem artifact và timeline
  của nó; (d) mở/chuyển phiên chat và gửi một tin; (e) đổi tùy chọn giao diện.
- **R14.** Tắt app rồi bật lại: workspace vừa thêm, nhãn vừa sửa, tùy chọn giao diện vừa
  đổi, và các bản ghi run vừa sinh đều còn. Phiên chat còn vì SDK giữ, không vì app.
- **R15.** Một workspace trống, hoặc một workspace không có `.cos/`, hiện trạng thái rỗng
  có chữ giải thích — không phải bảng trắng và không phải lỗi.
- **R16.** Mọi thao tác bị `Service` từ chối hiện đúng câu `Invalid` mang lên, không bị
  UI viết lại thành câu khác.
- **R17.** Nút chạy một bước dùng `Service.run_step` thật. Trước khi bấm, màn hình hiện
  danh sách tool mà bước đó được cấp và cảnh báo của nó — hai trường `grants` và `warning`
  mà `Service.board` đã trả (`cos_baodo/service.py:246-252`).
- **R18.** Màn Settings **chỉ đọc** với bốn knob an toàn và bảng grant. Nó không bật được
  tool, không đổi được `COS_WORKING_DIR`, không đổi được thư mục dữ liệu.

**Trang và bằng chứng**

- **R19.** Chỉ còn **một** trang ở `/`. Trang cũ bị bỏ.
- **R20.** Một proof mở trình duyệt thật kiểm đủ **5/5** luồng của R13 và kiểm R14 bằng
  cách khởi động lại tiến trình app giữa chừng. Nó tự dọn dữ liệu nó tạo ra.
- **R21.** `scripts/verify_0003.py` được viết lại theo trang mới, giữ nguyên bốn thứ nó
  đang giữ: theme, responsive, contrast, và negative control phát hiện backend chết.
- **R22.** `npm test` xanh. `uv run cos-build` chạy được và fingerprint phủ mọi file
  nguồn UI mới.

## Design

**Bốn tầng, và ranh giới giữa chúng là điều đáng giá ở đây.**

*Tầng 1 — thư mục dữ liệu.* Một component nhỏ biết đúng một việc: thư mục dữ liệu ở đâu,
và mở kết nối tới database trong đó. Nó là nơi duy nhất biết đường dẫn thật. Đọc biến môi
trường đi qua `Config` như mọi thứ khác, nên vẫn chỉ có một hàm trong app đọc môi trường.

Database mở ở chế độ WAL. Lý do không phải hiệu năng: WAL cho phép nhiều tiến trình đọc
trong khi một tiến trình ghi, và đó chính là hình dạng của R9. Mọi read-modify-write mở
transaction ở dạng ghi ngay từ đầu, không nâng cấp từ đọc lên ghi giữa chừng — nâng cấp
giữa chừng là cách một transaction bị hủy khi có tiến trình khác chen vào.

*Tầng 2 — metadata.* Bảng cho: phiên bản schema; các lần migrate đã chạy; workspace
`(root, name, label)`; bản ghi run; tùy chọn giao diện dạng khóa-giá trị. `root` là đường
dẫn thư mục làm việc đã resolve, và nó ở đó để một database phục vụ được nhiều thư mục
làm việc. `name` vẫn là một path segment; đường dẫn workspace **không** có cột nào, vì
không có cột thì không có chỗ cho `/etc`.

*Tầng 3 — object.* File, đặt tên theo digest nội dung. Ghi bằng cách ghi ra file tạm rồi
`rename` vào chỗ — `rename` trong cùng filesystem là atomic, và đó là toàn bộ cơ chế đằng
sau R11. Object là bất biến: cùng nội dung thì cùng tên, ghi lại là không-op. Bản ghi run
trong database giữ digest, không giữ nội dung, nên một reply dài không làm phình một bảng
đang được truy vấn.

*Tầng 4 — trang.* Một Reflex state gọi `Service` và không quyết định gì. Quy tắc của
`cos_baodo/service.py:1-13` được giữ nguyên: một câu `if` về nghiệp vụ nằm trong state là
một lỗi ở `service.py`, không phải ở state. Sáu màn là sáu hàm dựng component đọc từ state
đó; phần primitive trình bày đã có ở `cos_baodo/studio.py` được dùng lại.

Vì việc đọc board phải gọi `cos.mjs` như một tiến trình con (`cos_baodo/board.py:31-38`),
màn board đọc khi được mở và khi người dùng yêu cầu, chứ không tự làm mới theo chu kỳ.

**Điều gì không đổi.** `Service`, `Sessions`, `Runner`, `policy.py`, API JSON, và bảng
grant. Unit này đổi *chỗ chứa* và đổi *mặt trước*; phần giữa đứng yên, và đó là lý do nó
có thể làm trong một lần.

## Out of scope

- Chuyển workspace vào `~/.cos`. Người khởi xướng đã chọn ngược lại ngày 2026-09-22.
- Nhiều người dùng, đăng nhập, và mở ra ngoài loopback.
- Đổi bảng grant ở `cos_baodo/policy.py`, hay mở quyền tool cho sáu stage văn xuôi.
- Các stage `pr`, `review`, `ship` của chính unit này. Dừng sau `impl`.
- Đổi hình dạng API JSON. Client hiện có phải chạy tiếp không sửa.
- Tìm kiếm toàn văn, đồng bộ nhiều máy, sao lưu tự động, và xuất dữ liệu.
- Kết luận thẩm mỹ thay người khởi xướng. "Ưng ý" về prototype không phải là duyệt thiết
  kế.

## Concerns

- **C1. Hai gốc dữ liệu, một bản backup.** `~/.cos` giữ metadata; workspace giữ artifact
  và git. Sao lưu `~/.cos` không sao lưu công việc, và ngược lại. Đây là hệ quả trực tiếp
  của lựa chọn ở intent, không phải lỗi thiết kế — nhưng nó là thứ sẽ làm ai đó mất dữ
  liệu nếu không ai nói ra. **Người khởi xướng quyết định** có chấp nhận hay không.
- **C2. R9 chưa được chứng minh cho tới khi proof chạy lại.** `flock` và `BEGIN IMMEDIATE`
  là hai cơ chế khác nhau. `0004` đo được rằng cách cũ **chỉ để lại 8 trên 20 entry** trước
  khi có khóa (`.cos/0004_silent-concurrent-loss/plan.md:115`); con số đó là lý do không
  được suy luận rằng SQLite đương nhiên đúng. Cho tới khi
  `scripts/verify_0004.py` xanh trên cơ chế mới, R9 là một yêu cầu, không phải một sự thật.
- **C3. Bỏ trang cũ là bỏ mặt trước duy nhất đã được chứng minh chạy với backend.** Sau
  R19, nếu trang mới sai ở chỗ nào thì trong cùng một build không còn chỗ nào để đối chiếu.
  Giảm nhẹ bằng R20 và R21, nhưng không triệt tiêu được. **Người khởi xướng đã chọn R19**
  ngày 2026-09-22 sau khi được nêu chi phí viết lại `verify_0003.py`.
- **C4. `~/.cos` trùng tên với `.cos/` của mỗi repository, và hai cái khác vai trò hoàn
  toàn.** Một cái là dữ liệu của app, một cái là artifact của một unit công việc. Đổi tên
  về sau tốn một lần migrate. Giữ tên là lựa chọn của người khởi xướng; ghi lại ở đây để
  lần nhầm đầu tiên có chỗ để đối chiếu.
- **C5. `COS_WORKING_DIR` không đặt thì app vẫn có database nhưng không dựng được đường
  dẫn workspace nào.** Trạng thái này phải hiện ra thành chữ trên màn hình, chứ không phải
  thành một danh sách rỗng trông như chưa ai thêm gì. R15 phủ phần hiển thị; điều đáng ghi
  là hai nguyên nhân rỗng đó **không giống nhau** và không được hiện giống nhau.
- **C6. Tùy chọn giao diện lưu trong database là tùy chọn của cả máy, không phải của một
  trình duyệt.** Với một người dùng local-first thì đúng; nếu sau này có hai người, nó sai.
  Chấp nhận trong phạm vi người dùng mà intent đã chọn.
- **C7. Sáu stage văn xuôi không có tool, nên app viết artifact từ nội dung reply** —
  hành vi có sẵn từ `0005`, đã ghi ở `.cos/0005_hand-driven-invisible-loop/plan.md` Risk 1.
  Trang mới phải tiếp tục nói điều này ra chỗ người dùng bấm nút, chứ không được làm nó
  trông như agent tự ghi file.
- **C8. Chạy một bước tiêu quota thật.** Prototype có nút "simulate" không tốn gì
  (`cos_baodo/prototype.py:376-395`). Cùng vị trí, cùng hình dạng, nút mới gọi model thật.
  Nút phải nói rõ điều đó **trước** khi bấm, không phải sau.

## Open questions

- Object store hiện chỉ có một loại nội dung để chứa: reply của một lần chạy bước. Nếu
  chỉ có một loại, một cột BLOB có thể đã đủ. Câu trả lời đổi việc: nếu sau này không có
  loại object thứ hai, tầng 3 là chi phí không cần. Giữ vì R11 và vì reply có thể dài;
  xem lại sau khi có số đo thật về kích thước.
- Bao nhiêu bản ghi run thì một truy vấn board chậm thấy được? Chưa có số. Chưa có index
  nào được thiết kế theo số đo, chỉ theo hình dạng truy vấn.
- Từ intent, còn mở: người khởi xướng chưa duyệt thiết kế một cách tường minh; `~/.cos`
  có gây nhầm khi dùng thật hay không.
- `scripts/verify_0003.py` viết lại rồi thì nó và proof mới của R20 có phần trùng nhau.
  Gộp hay giữ hai, plan quyết định; giữ hai thì phải nói rõ mỗi cái giữ tính chất gì.
