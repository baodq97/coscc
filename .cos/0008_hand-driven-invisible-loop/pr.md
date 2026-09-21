# PR: hand-driven invisible loop
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: draft.

## Where

**Chưa có.** Không có pull request nào được mở, vì repo này không có remote:

```
git remote -v        # rỗng, đo lại 2026-09-22
git branch --show-current
main
```

Đây đúng là `spec.md` C2 và `plan.md` Risk 2, và `.claude/skills/write-pr/SKILL.md` bảo
dừng lại nói thẳng thay vì bịa một URL. Nên file này là `draft`, không phải `accepted`:
nó ghi một PR **chưa tồn tại**, và nó sẽ còn là `draft` cho tới khi có remote thật và
lệnh mở PR trả về một URL thật.

Việc tạo remote với ra ngoài máy. Đó là quyết định của tác giả, không phải của bước này,
nên tôi không tự làm.

Mười một commit của unit nằm thẳng trên `main`, từ `dc47a9e` tới `cfae90c`. Đó là tình
trạng `.claude/harness.md` mô tả: tác giả làm một mình và commit thẳng lên `main`.

## Scope of the diff

`git diff --stat dc47a9e~1..cfae90c`, ngày 2026-09-22:

**29 file, +4997 −174.**

Mười file mới trong `cos_baodo/`: `board.py`, `journal.py`, `policy.py`, `runner.py`,
`ui.py` và năm file test đi kèm. Một lệnh chứng minh mới, `scripts/verify_0008.py` (+634).
Năm skill mới trong `.claude/skills/`. File đổi nhiều nhất là `cos_baodo/cos_baodo.py`
(+732), tức là trang.

Phần còn lại là sửa chỗ đã có: `cos.mjs` (+194) mở vòng lặp từ ba lên tám giai đoạn,
`sessions.py` (+138) giữ lại con số của mỗi lượt và `terminal_reason`, `verify_0004.py`
(+171) thêm sàn thẩm mỹ, `service.py` (+157) và `api.py` (+77) nối board vào.

## What a reviewer should look at first

Theo thứ tự đáng ngờ, không theo thứ tự to nhỏ.

**1. `cos_baodo/policy.py` — bảng grant, và luật chuỗi lệnh.** Đây là chỗ duy nhất trong
repo cấp tool cho một session. Hai câu hỏi đáng hỏi: `check_command` có còn lọt cách nào
không (thay thế, chuyển hướng, nối lệnh), và `decide` có thật sự chặn được đường ghi ra
ngoài workspace sau khi resolve. `plan.md` Risk 3 đã thừa nhận luật chặn theo **tên nhị
phân** chứ không theo lệnh con: `git push --force` và `gh api` vẫn lọt, và
`cos_baodo/policy_test.py:148-158` giữ giới hạn ấy thành một test xanh.

**2. `cos_baodo/runner.py` — ai cầm bút.** Đây là chỗ plan đi khác spec (`impl.md`
`## Where the plan was departed from` mục 1). Sáu giai đoạn chữ có grant rỗng, nên app ghi
artifact từ câu trả lời. Đáng kiểm: `check_reply` có để lọt file nửa vời vào thư mục người
đọc không, và `_hit_ceiling` có đang nuốt một lỗi thật thành `exhausted` không.

**3. `cos_baodo/board.py:31` cùng `board.py:90`, và `runner.py:38-39` cùng
`runner.py:57-63` — biên với workspace.** Cả hai đều
dùng bản của **repo này**, không chạy bản tìm thấy trong workspace. Nếu chỗ nào đó lỡ đảo
lại, một repo clone về sẽ chạy được code của nó trong tiến trình app.

**4. `scripts/verify_0008.py`, mệnh đề 5.** Nó hỏi **cả** 0 tool **và** 0 MCP server, vì
đo riêng danh sách tool cho kết quả nhảy giữa hai lần chạy liên tiếp (`impl.md`
`## What was measured`). Nếu ai đó bỏ vế sau cho đỡ đỏ, `0008` sẽ tự cấp cho mình một dấu
xanh — và `0007` sẽ biến mất khỏi tầm nhìn.
