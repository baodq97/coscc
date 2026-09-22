# Review: an installed copy could run no stage of the loop
PR: pr.md. Author: Bao Do. Concluded by: agent — Claude Opus 4.5, the same agent that wrote
the code. Status: accepted.

**Không có ai ngoài tác giả đọc file này.** `.claude/CLAUDE.md`, mục
`## What is deliberately not built`, gọi đúng tên thứ đang xảy ra ở đây: một ô `review`
xanh do chính bên viết code tô. Điều duy nhất khác so với các unit trước là bản rà được
chạy bởi một lượt rà **độc lập với ngữ cảnh** — một agent khác, chỉ nhìn diff, không biết gì
về các quyết định dẫn tới nó — và nó tìm được thứ tác giả không tìm ra. Đó không phải sự
tách bạch trách nhiệm. Đó là một con mắt thứ hai không có quyền phủ quyết.

## Findings

**F1 — `coscc/runner.py:57-63` → `coscc/service.py:365` · nặng · đã sửa ở `f6ecd0e`.**
`MissingRules` không phải `RunError`, mà `run_step` chỉ có một `except RunError`. Một step
có skill bị thiếu sẽ **thoát khỏi handler** và thành 500 ở route, thay vì 400 sạch nêu
đường dẫn — tức là đúng tính năng mà cả unit này tồn tại để xây, lại hỏng theo kiểu xấu
nhất. Không phải giả thuyết: tái hiện ngày 2026-09-22 trên một workspace có `cos.mjs`
nhưng không có skill nào, và nó in ra
`ESCAPED as MissingRules (500): no rules found; looked at ...`. Đây đúng là hình dạng mà
`wheel_complaints` tồn tại để từ chối, nên nó là đường đi thật chứ không phải đường hiếm.

Hai cách vá. Chọn cách gói lỗi trong `runner.skill_for` thành `RunError` thay vì mở rộng
`service.py`, vì `service` và `api` đã hiểu đúng một từ vựng từ chối; thêm từ vựng thứ hai
buộc **mọi** caller tương lai của `runner` cũng phải học nó. Hệ quả tốt kèm theo: giả định
của `plan.md` — không đụng năm file, trong đó có `service.py` — vẫn đứng.

`coscc/service_test.py` có test mới cho đúng ranh giới này. **Đã kiểm nó biết đỏ:** gỡ chỗ
vá ra thì `FAILED (errors=1)`, lắp lại thì `OK`.

**F2 — `coscc/harness.py:157` · nhẹ · đã sửa ở `f6ecd0e`.** Lời phàn nàn khi wheel thiếu
skill viết *"every step would run without its rules"*. Đó là hành vi **trước** unit này, và
chính unit này xoá nó. Một người đọc log CI đỏ sẽ đi tìm prompt bị thiếu luật, trong khi
thứ thật sự xảy ra là mọi Run đều từ chối. Sửa thành "every step would refuse to run".

**F3 — `coscc/harness.py:160-166` · nhẹ · đã sửa ở `80d7492`.** Tác giả tự tìm khi đọc lại
diff. Phép kiểm rò rỉ khớp chuỗi con `"settings"` ở bất kỳ đâu trong đường dẫn, nên một
skill đặt tên hợp lệ là `write-settings` sẽ làm hỏng release. Một phép kiểm nổ trên input
đúng thì tệ hơn không có, vì cách đi qua nó là xoá nó. Giờ khớp theo basename và đuôi
`.json`, có test cho chính ca dương tính giả đó.

**F4 — `scripts/verify_0012.py` · trung bình · đã sửa ở `80d7492`.** Tác giả tự tìm. Claim 2
so hai tập; nếu workspace không có `.cos/` thì cả hai đều rỗng và claim **xanh mà không đo
gì**. Đúng thứ `coscc/build.py:3-6` gọi là bằng chứng xanh về một thứ không tồn tại. Giờ là
exit 2.

**F5 — `coscc/board.py`, `coscc/runner.py` · vụn · đã sửa ở `544cf54`.** Một comment đứng
trên khoảng trống nơi hằng số bị xoá, và một docstring bị ngắt dòng hỏng.

**Không có finding nào chưa sửa.**

## What was not reviewed

1. **`scripts/verify_0011.py` chưa chạy.** Nó cần một máy thứ hai qua SSH
   (`COS_PROOF_TARGET`) và không có máy nào. PR này sửa `docs/install.md` và
   `release.yml` — hai thứ `0011` đo — nên **mọi claim về reboot và về đường update đều
   không có ai kiểm trong lần này.** Đây là khoảng trống lớn nhất của review này.
2. **Đường release chưa từng chạy thật.** Bước copy và bước guard mới trong `release.yml`
   được kiểm bằng cách chạy tay từng lệnh trên máy này, không phải bằng một lần release
   thật. Lần release đầu tiên dùng chúng sẽ là lần đầu chúng chạy trong CI.
3. **Trang web chưa mở bằng trình duyệt.** Board được kiểm qua `/api/board` (88 dòng khớp
   gate), không qua màn hình. `verify_0003` và `verify_0006` là hai proof mở trình duyệt và
   cả hai đều đòi dừng app trước; không chạy lần này.
4. **Không ai đo lại `0011` sau khi `docs/install.md` đổi.** Xem mục 1.
5. **Bản rà độc lập chỉ đọc diff.** Nó không chạy app, không cài wheel, không kiểm số đo
   trong `impl.md`. Hai finding nó đưa ra đều đúng và đều đã tái hiện được bằng tay trước
   khi sửa — nhưng phạm vi của nó là văn bản của thay đổi, không phải hành vi của sản phẩm.

## Verdict

**Accepted**, ở đúng mức các finding cho phép và không hơn.

Thứ được chứng minh: cùng một file proof, cùng một máy, exit 1 trên bản `v0.2.2` tải từ
release và exit 0 sau khi vá; `npm test` 299 Python + 60 node; `check_wheel.py` đỏ trước
bước copy và xanh sau; hai check CI trên PR #10 xanh.

Thứ **không** được chứng minh: bản cài vẫn sống sau reboot, đường update còn chạy, và trang
web dựng được sáu màn hình — cả ba đều nằm trong phạm vi `0011` và không được đo lại ở đây.
Nếu một trong ba hỏng vì thay đổi này, nó sẽ lộ ra ở lần release tới chứ không phải ở đây,
và `ship.md` phải là chỗ nói rằng chuyện đó đang được theo dõi.
