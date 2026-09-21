# Intent: A second app pulls files out from under the first one's session
Author: Bao Do. Status: accepted.

> Khung vấn đề dưới đây do agent viết, không phải lời của người khởi xướng. Tác giả chỉ ra
> `0005` C2 và bảo tự chốt các lựa chọn nhỏ. Ghi ra để người đọc sau không tưởng đây là lời
> của một con người — invariant 1 của `write-intent` đòi ngược lại, và nó không được thoả.

## Problem

`0005` đóng được một nửa của cuộc đua. Nửa còn lại vẫn mở, và nó mở **theo thiết kế chứ
không phải do sót**: chỗ mở đã được viết ra ở `.cos/0005_silent-concurrent-loss/spec.md:104-108`.

Hai chỗ trong app hỏi những câu rất giống nhau, nhưng chỉ một chỗ trả lời được cho toàn máy:

- **Danh sách workspace** khoá bằng `flock` trên một file cạnh store
  (`cos_baodo/store.py:43`). Khoá của hệ điều hành, nên bản app thứ hai thấy được. `0005`
  đo: 4 tiến trình ghi cùng lúc, 20/20 mục sống.
- **"Workspace này có ai đang nói không"** đọc một `dict` trong bộ nhớ
  (`cos_baodo/sessions.py:156`, hỏi ở `cos_baodo/sessions.py:163`). Không có gì trên đĩa,
  nên bản app thứ hai **không thấy gì cả**.

`cos_baodo/service.py:189` hỏi câu thứ hai rồi mới cho `git` chạy. Với một tiến trình thì
đúng. Với hai thì bản B thấy danh sách rỗng, kết luận là không ai đang làm gì, và `pull`
ghi đè file trong lúc một lượt của bản A đang đọc chúng. Lượt đó không được báo, và không
có cách nào báo cho nó.

Điều làm nó đáng sửa không phải xác suất, mà là **hình dạng của cái thua**: nó im lặng và
nó giống hệt lúc đúng. `pull` trả về "Already up to date" hoặc một dòng fast-forward bình
thường; session bên kia trả lời bằng nội dung file đã bị thay. Không có lỗi ở đâu để đọc.

**Chưa có con số đo.** Lập luận ở trên đọc ra từ code, không phải từ một lần chạy: `_live`
là `dict` trong bộ nhớ nên hai tiến trình không thể chia sẻ nó. Đó là một lập luận mạnh
nhưng vẫn là lập luận. `0005` đã cho thấy chênh lệch giữa hai thứ — ở đó "mất một vài mục"
hoá ra là **mất 12 trên 20**. Nên đo trước, sửa sau.

## Proposed outcome

Ngày **2026-09-21**, với **2** tiến trình `cos-baodo` chạy trên cùng một `COS_WORKING_DIR`:

bản A mở một session trong workspace `W`; bản B gọi `pull` trên `W` và **bị từ chối**, lý do
nêu tên `W`; đóng session ở A rồi bản B gọi lại thì **thành công**.

Hôm nay điều này **sai**: bản B không thấy session của bản A, nên nó pull.

Con số **2** là số tiến trình nhỏ nhất dựng lại được lỗi; nhiều hơn không thêm gì.

## Affected users and systems

- **Người chạy hai bản app** — hai cửa sổ, hoặc một bản quên tắt. Đây là người duy nhất lỗi
  này chạm tới, và cũng là tình huống đã xảy ra thật: trong lúc làm `0005`, cổng 8790 bị một
  tiến trình `python -m app.web` từ bản code cũ giữ, kèm hai tiến trình `claude` con đang
  sống. Máy này đã ở đúng trạng thái đó rồi.
- **`cos_baodo/sessions.py`** — nơi câu hỏi được trả lời, và nơi vòng đời client được giữ.
- **`cos_baodo/service.py`** — nơi `pull` hỏi trước khi chạy.
- **`scripts/verify_0005.py`** — lệnh chứng minh hiện tại kiểm điều này **trong một tiến
  trình**. Nó sẽ vẫn xanh kể cả khi lỗi liên tiến trình còn nguyên, nên nó không thay được
  lệnh mới.
- **Không chạm:** `cos_baodo/store.py`. Khoá ở đó đã liên tiến trình và không phải sửa.

## Constraints

- **Dấu hiệu để lại sau một lần crash phải tự hết.** App bị `kill -9` giữa lúc mở session
  không được để lại một workspace vĩnh viễn không pull được. Đó sẽ là hỏng nặng hơn cái đang
  sửa, và nó sẽ hỏng im lặng theo cách khó đoán hơn. Spec chọn cơ chế; ràng buộc là **không
  ai phải dọn tay**.
- **Chỉ `pull`.** `remove` chỉ bỏ mục khỏi danh sách và không đụng thư mục
  (`.cos/0003_no-workspace-management/spec.md:96` R18), nên nó không cắt file dưới chân ai. Đổi
  label càng không. Mở rộng ra chúng là chặn vì gọn, không vì có lỗi đã đo.
- **Chỉ các bản `cos-baodo` khác.** Một session `claude` chạy ở terminal trong cùng workspace
  vẫn không được thấy. Nó không do app tạo, nên app chỉ có thể suy ra từ dấu vết trên đĩa của
  SDK — chưa kiểm, và là một vấn đề khác. Xem open question 1.
- **Không xếp hàng.** Từ chối, như `0005` đã chốt. Chờ tới khi session đóng là dựng lại đúng
  cái treo vô hạn mà `0005` R2 vừa bỏ đi.
- **Ba lệnh chứng minh cũ phải còn xanh** nguyên trạng: `scripts/verify_0002.py`,
  `scripts/verify_0003.py`, `scripts/verify_0005.py`. `verify_0005` có mở session thật rồi
  `pull` trong cùng tiến trình, nên nó là chỗ dễ vỡ nhất khi thêm một tầng kiểm nữa.
- **`npm test` không được cần trình duyệt và không được cần hạn mức** (`0004` đã chốt).
- **Lệnh chứng minh tiêu hạn mức.** Dựng lại lỗi cần một session thật ở tiến trình A; giả lập
  sẽ kiểm một đường không ai đi. Chạy có người trông, không vòng lặp.

## Open questions

1. Session `claude` ở terminal thì sao? Ràng buộc trên loại nó ra, nhưng người dùng sẽ không
   phân biệt được: với họ, "tôi đang mở session trong foo" là một câu, không phải hai. Một
   bản sửa chỉ thấy app khác sẽ vẫn cắt dưới chân terminal của chính họ.
2. Dấu hiệu "đang bận" đặt ở đâu — trong workspace, hay trong working folder? Trong workspace
   thì nó là file lạ nằm trong repo của người ta và sẽ vào `git status`. Trong working folder
   thì nó tập trung nhưng phải mã hoá tên workspace.
3. App B bị từ chối thì người dùng làm gì? Session đang sống ở **tiến trình khác**, nên trang
   của B không có cách nào đóng nó. Đây là `0005` open question 5, rộng hơn một bậc.
4. Sau khi có dấu hiệu trên đĩa, `0005` C4 (`flock` là giả định POSIX) nặng thêm hay không —
   tuỳ spec chọn cơ chế gì, và nên được trả lời ở đó chứ không để trôi.
5. Đo thế nào cho ra con số? Cuộc đua này cần A đang **ở giữa** một lượt lúc B pull, mà độ
   dài một lượt thì không điều khiển được. Một phép đo không dựng lại được lỗi sẽ xanh vì
   sai lý do — đúng cái bẫy bước 4 của `0005` suýt rơi vào.
