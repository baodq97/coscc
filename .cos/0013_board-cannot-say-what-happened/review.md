# Review: transitions are the record, and the state set is configuration
PR: pr.md. Author: Bao Do. Concluded by: agent — Claude, reviewing code it wrote itself. Status: accepted.

**Đọc dòng này trước.** Không có tách bạch vai trò ở đây. Người viết code và người kết luận
review là một, nên `accepted` bên trên **không phải một người đã duyệt**. Thứ duy nhất khiến
review này không rỗng là ba phát hiện dưới đây được tìm bằng cách **dựng ca hỏng ra và chạy**,
không phải bằng đọc lại và thấy hài lòng. Hai trong ba nằm ở `scripts/verify_0013.py` — tức
chính cái file đáng lẽ bắt lỗi giùm tôi.

## Findings

### 1. `scripts/verify_0013.py:95-118` — proof đỏ được trên một sản phẩm đúng. **Nghiêm trọng.** Đã sửa ở `f6ae6af`.

Bản đầu chỉ duyệt các path **có ở HEAD**. Xoá một artifact đã chốt khỏi một unit vẫn còn
sống: sản phẩm ghi đúng một chuyển trạng thái sang "không còn" và đếm nó; proof không nhìn
thấy path đó lần nào. Dựng ra và chạy, 2026-09-22:

```
product settled edits: 1 [('spec.md', 'accepted', 'not started')]
proof-style count: 0 (paths at HEAD: 1)
>>> DISAGREE — the proof would go red on a correct product <<<
```

Đây là loại lỗi tệ nhất một proof có thể mang, vì cách duy nhất người ta xử lý một proof đỏ
vô cớ là **sửa proof**. Repo này hôm nay không có ca đó — 60 path, không unit sống nào mất
artifact — nên nó sẽ nằm im cho tới đúng ngày nó đắt nhất.

### 2. `scripts/verify_0013.py:120-148` — hai đường tính ra cùng một số vì hai lý do khác nhau. **Nghiêm trọng.** Đã sửa ở `f6ae6af`.

Cùng vòng lặp đó, khi `git show` không có blob thì bản đầu `continue`, để nguyên trạng thái
trước đó. Với chuỗi `accepted → xoá → thêm lại`, proof bỏ qua lần xoá rồi đếm lần thêm lại
(vì `previous` vẫn là `accepted`), còn bộ nhập đếm lần xoá. **Cùng ra 1, vì hai sự kiện khác
nhau.**

Cái này đáng sợ hơn mục 1 chứ không nhẹ hơn: `plan.md` Risk 1 nói cả thiết kế đứng trên việc
"hai chương trình độc lập cùng ra một số là bằng chứng". Một sự trùng số do bù trừ làm câu đó
sai mà vẫn xanh. Nay một blob không có là trạng thái "không còn", ở cả hai bên.

### 3. `coscc/history.py:426` — `settled_edits` mặc định lấy tập trạng thái sai. **Trung bình.** Đã sửa ở `f6ae6af`.

Tham số `machine` từng mặc định `states.default()`. Hàng ghi dưới một tập khác sẽ bị lọc bằng
định nghĩa "settled" của tập mặc định, không khớp gì, **trả về danh sách rỗng, không lỗi ở
đâu cả**. Đó đúng là kịch bản `spec.md` C5 mô tả, khoác áo một tiện lợi. Nay bắt buộc truyền,
và có test khẳng định gọi thiếu là `TypeError` chứ không phải một phỏng đoán.

### 4. `coscc/history.py:341` — `sessions_of` chỉ lấy `actor` của hàng đầu tiên. **Thấp. Không sửa.**

Một session mà hàng sau mang actor khác thì hàng sau bị bỏ qua. Hôm nay không xảy ra được:
mọi hàng nhập vào đều `unknown`, và chưa có gì ghi hàng thật. Sẽ thành thật khi có agent, và
`spec.md` `## Out of scope` đẩy agent ra ngoài unit này.

### 5. `coscc/service.py:51` — `STAGE_FILES` vẫn là nguồn thứ hai cho danh sách stage. **Thấp. Không sửa.**

Có trước unit này, và là tên *stage* chứ không phải tên *trạng thái*, nên R6 không bị vi phạm
theo chữ. Nhưng tinh thần R6 muốn nó hỏi `coscc/states.py`. Sửa nó đụng `artifact()` và
`run_step`, tức đụng đường chạy mà unit này cố ý không chạm.

### 6. Cấu trúc: `scripts/` không nằm trong `npm test`. **Trung bình. Không sửa được ở đây.**

Hai trong ba phát hiện trên nằm ở `scripts/verify_0013.py`, và `npm test` không chạy file đó
một dòng nào — `package.json:8` chỉ discover `coscc/*_test.py`. Proof là thứ duy nhất phán
xử unit đã xong hay chưa, và nó là thứ duy nhất không ai kiểm. Không phải vấn đề của unit
này, nhưng nó vừa tự chứng minh bằng hai lỗi, nên ghi ra đây để có chỗ mà trích.

### 7. `coscc/states.py:216` — `default()` được `lru_cache`. **Thông tin, không phải lỗi.**

Sửa `states.json` rồi mà tiến trình chưa khởi động lại thì vẫn đọc tập cũ. Đúng ý (đọc một
lần), nhưng người đầu tiên thử "đổi cấu hình xem sao" sẽ gặp nó.

## What was not reviewed

- **Sáu màn hình.** Không màn nào gọi đường đọc mới; `coscc/screens.py` và `coscc/state.py`
  không đổi một dòng. Nghĩa là chưa ai **nhìn** thấy 44 sự kiện này trong app — chúng chỉ
  tới được qua JSON.
- **Hiệu năng.** `state()` gấp trong Python, `_latest` một truy vấn. Đo trên 211 hàng của repo
  này, không đo trên thứ gì lớn hơn. Không có số nào để nói nó chịu được bao nhiêu.
- **Đồng thời.** Hai tiến trình cùng chạy bộ nhập một lúc chưa bao giờ được chạy thử.
  `once_key` làm cho kết quả đúng về nội dung, nhưng `scripts/verify_0004.py` (bốn tiến
  trình) chưa chạy lại trên schema 2.
- **Bản cài.** Mọi số đo ở đây lấy từ checkout. `scripts/verify_0012.py` — thứ đo bản cài —
  chưa chạy lại sau thay đổi này, và `SCHEMA_VERSION = 2` chính là thứ bản cài `v0.2.3` sẽ
  từ chối.
- **Repo khác repo này.** `spec.md` C2 đã nói: chỗ khác không có `.cos/` để nhập, nên bộ nhập
  chưa từng chạy trên một lịch sử không phải lịch sử này.

## Verdict

Đủ để merge. Ba lỗi tìm được đều đã sửa và đều kèm ca dựng lại được; số đo cuối là
**44 / 44**, `npm test` **60 + 370** xanh, proof đỏ khi không có `--import` và xanh khi có.

Không nâng verdict cao hơn thế, vì hai lý do đã viết ở trên: **không có người nào đọc**, và
**hai trong ba lỗi nằm trong chính cái file đóng vai người kiểm**. Cái thứ hai mới là thứ
đáng mang sang unit sau — nó nói rằng proof ở repo này đang được tin nhiều hơn mức nó được
kiểm.
