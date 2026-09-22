# Intent: the product can run a stage but cannot start or finish a work unit
Author: Bao Do. Type: feat. Status: accepted.

## Problem

Người khởi xướng nói ngày 2026-09-22, sau khi `0013` ship: *"bạn sẽ tự chủ hành động đến khi
bạn sử dụng được sản phẩm này để impl hoàn thành toàn bộ 1 workunit như một người dùng thông
thường. thiếu gì thì thêm đó, sửa đó và thử lại."*

Thử thật trên bản đã cài `coscc 0.3.0`, workspace `/home/bd/coscc-wp/coscc`, ngày
2026-09-22. Nó dừng ở bước đầu tiên:

```
$ curl -X POST http://127.0.0.1:8790/api/units -d '{"slug":"try-it"}'
405

$ curl -X POST http://127.0.0.1:8790/api/board/run \
       -d '{"cwd":".../coscc","unit":"0014_try-it","stage":"intent"}'
{"error":"no such work unit in this workspace: 0014_try-it"}
```

**Board chỉ liệt kê được thứ đã có sẵn.** `coscc/service.py:314` và `:347` từ chối mọi unit
không nằm trong kết quả `cos.mjs status`, mà `cos.mjs` thì đọc các thư mục có thật dưới
`.cos/`. Không route nào trong **14** route của `coscc/api.py` tạo ra một thư mục như thế.

**Đo bằng chính sáu bước mà `.claude/CLAUDE.md` `## Branches, tags and releases` định nghĩa
là "thứ tự cho mỗi unit":**

| | Bước | Sản phẩm làm được? |
|---|---|---|
| 1 | `cos.mjs new-path <slug>` — cấp số | **không** |
| 2 | Viết `intent.md` với `Type:` | có — `POST /api/board/run` |
| 3 | `cos.mjs unit-branch <unit>` — tên nhánh | **không** |
| 4 | `git switch -c <tên đó>` — cắt nhánh từ `main` | **không** |
| 5 | Chạy các stage, **mỗi artifact một commit** | một nửa — viết file, không commit |
| 6 | `gh pr create`, rồi `gh pr merge --squash` | một nửa — chỉ stage `pr` ở chế độ autonomous |

**1 trên 6.** Và bước 1 chặn cả năm bước còn lại: bước 2 chỉ chạy được trên một unit đã tồn
tại, nên hôm nay không có đường nào từ "chưa có gì" tới "có unit".

**Phần git trống hoàn toàn.** `grep -n "branch\|commit"` trên `coscc/api.py`,
`coscc/service.py`, `coscc/gitops.py` cho **4** dòng, và cả bốn đều là văn xuôi trong
docstring. `coscc/gitops.py` chỉ có `clone` và `pull`. `coscc/runner.py:292` ghi file
artifact rồi dừng — không có `git add`, không `git commit`, không nhánh.

**Bằng chứng cuối, và nó khớp với `0013`.** Bảng `runs` trong `~/.cos/cos.db` có **0** dòng
cho repo này, trong khi `.cos/` có **13** unit. Cả mười ba đều được tạo ngoài app. Sản phẩm
chưa bao giờ tự đưa một unit nào đi hết vòng lặp của chính nó — kể cả `0013`, unit được viết
ra để nói về vòng lặp đó.

## Proposed outcome

Trước 2026-10-20: một work unit đi từ **chưa tồn tại** tới **pull request đã merge**, làm
**chỉ bằng HTTP API của sản phẩm**, không một lệnh nào gõ vào terminal.
`scripts/verify_0014.py` đo bằng cách chỉ gọi API và không gọi `git` hay `cos.mjs` để *làm*
bất cứ việc gì — nó chỉ dùng git để *kiểm tra* kết quả.

Tính theo bảng sáu bước ở trên: hôm nay **1/6**, đích là **6/6**.

Outcome này sai được: nếu sau khi chạy, nhánh không tồn tại, hoặc artifact không có commit
riêng, hoặc PR không merge được, proof trả exit 1 và con số không phải 6.

## Affected users and systems

- **Người dùng board** — hôm nay không có ai ngoài người viết nó, và người đó vẫn phải mở
  terminal để bắt đầu bất cứ việc gì.
- **`coscc/api.py` và `coscc/service.py`** — chỗ phải mọc thêm năng lực, không phải chỗ
  quyết định (`spec.md` R10 của `0001` vẫn giữ).
- **`coscc/gitops.py`** — hôm nay chỉ `clone` và `pull`; nhánh và commit sẽ vào đây.
- **`coscc/policy.py`** — nếu app tự chạy git thì đó là một năng lực **mới**, không đi qua
  bảng grant vốn chỉ nói về thứ một *session* được làm. Phải nói rõ ranh giới đó ở đâu.
- **`.cos/` của workspace đích** — `0013` `intent.md` chốt rằng không gì của coscc vào cây
  repo dùng chung. Unit này tạo thư mục `.cos/` trong workspace, nên **mâu thuẫn trực diện
  với quyết định đó** và spec phải xử lý, không được lờ đi.
- **`coscc/screens.py`** — "người dùng thông thường" bấm nút chứ không gọi `curl`. Không đổi
  màn hình thì outcome đạt mà lời hứa thì không.

## Constraints

1. **Proof không được gõ lệnh thay sản phẩm.** Một proof gọi `git switch` giùm rồi tuyên bố
   sản phẩm làm được là proof đo chính nó. Nó chỉ được gọi HTTP, và chỉ dùng `git` để đọc.
2. **Không chạy trên repo này.** Unit này sinh commit và nhánh thật; chạy thử trên
   `cos-baodo` là trộn công việc thật với công việc thử. Phải là một repo dùng một lần.
3. **Việc app tự chạy `git` là một quyết định an ninh mới, không phải một tiện ích.**
   `coscc/policy.py:1-20` nói bảng grant là thứ duy nhất nói một *step* được làm gì; app tự
   commit thì nằm **ngoài** bảng đó. Spec phải nói ai bị chặn ở đâu.
4. **Không xoá và không sửa nghĩa của `cos.mjs`.** Cấp số và tên nhánh đã có đúng một nguồn;
   thêm một bản Python thứ hai là đúng thứ `.claude/CLAUDE.md` cấm.

## Open questions

1. **Merge có phải việc của sản phẩm không?** Bước 6 gồm cả `gh pr merge --squash`. Một app
   không có xác thực, bind `0.0.0.0` (`docs/install.md`), mà merge được vào `main` là một bề
   mặt khác hẳn mọi thứ nó có hôm nay. Có thể phải dừng ở "PR đã mở".
2. **Ai bấm "accept"?** Vòng lặp chạy tiếp khi `Status: accepted`. Session tự viết
   `accepted` cho bài của chính nó (`.claude/CLAUDE.md` `## What is deliberately not built`).
   Qua app thì chuyện đó thành một nút, và nút ấy nên thuộc về người hay về agent, chưa rõ.
3. **`.cos/` trong workspace đích.** Xem `## Affected users and systems`. Hoặc `0013` sai,
   hoặc unit này phải tạo unit ở chỗ khác, hoặc hai chuyện đó khác nhau vì một lý do phải
   viết ra.
4. **Nhánh và worktree.** Nhiều unit chạy song song thì một nhánh trên một cây làm việc là
   không đủ — đúng thứ người khởi xướng đã nêu ngày 2026-09-22 và chưa có unit nào nhận.
