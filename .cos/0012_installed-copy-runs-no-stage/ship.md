# Ship: v0.2.3, the first release on which the loop runs after installing
Review: review.md. Author: Bao Do. Status: accepted.

## What went out

**`v0.2.3`**, publish 2026-09-22T14:54:39Z,
https://github.com/baodq97/coscc/releases/tag/v0.2.3 — `coscc-0.2.3-py3-none-any.whl`
(4.524.826 byte), `install.sh`, `SHA256SUMS`.

Wheel `v0.2.2` là 4.486.625 byte; chênh **38.201 byte** chính là `cos.mjs` cộng 9 skill giờ
đi theo bản phát hành.

Hai PR đã merge vào `main`:

- **#10** `fix(0012)` — `38ff734`, squash. 17 file, +1.473 / −55.
- **#11** `chore: 0.2.3` — `933fef3`, squash. Chỉ 5 chỗ khai version.

**Lần đầu chạy thật trong CI** (run `35743597...`, workflow `release.yml`): bước
`Copy the harness into the package` và bước guard mới. Guard in ra
`coscc-0.2.3-py3-none-any.whl: carries a frontend and a harness`. Trước lần này cả hai chỉ
từng được chạy tay trên một máy, và `review.md` mục `## What was not reviewed` đã ghi đó là
khoảng trống.

## Did the outcome hold

`intent.md` đặt: *"Trước 2026-10-06: trên một máy không có checkout của repo này, chỉ có bản
cài từ release, mọi năng lực của app cho ra kết quả không phân biệt được với chính app chạy
từ checkout"*, đo bằng `scripts/verify_0012.py` với hai mốc — board khớp gate, và prompt
stage `spec` ≥ 18.000 ký tự.

**Đúng, ngày 2026-09-22, trước hạn 14 ngày.** Đo trên bản `v0.2.3` cài bằng đúng một dòng
trong `docs/install.md`, không phải wheel dựng tại chỗ:

```
PASS  the installed copy resolves cos.mjs and 9 skill(s), outside this checkout
PASS  the board agrees with the gate on all 88 (unit, stage, status) rows
PASS  a spec step's prompt carries its rules and clears 18,000 characters
PASS  rules that cannot be found stop the step, and the refusal names the paths tried
PASS  docs/install.md declares node under ## Prerequisites
exit=0
```

Cùng file đó, cùng máy đó, trên `v0.2.2`: **exit 1**, 4/5 đỏ — prompt 14.320 ký tự không có
khối rules, board 400, hai claim không import nổi `coscc.harness`.

**Hai mốc số của intent, đối chiếu thẳng:** board khớp trên **88** dòng
`(unit, stage, status)`; prompt stage `spec` qua ngưỡng 18.000 (đo từ checkout là 18.882).

**Hai điều outcome này không bao gồm, và không được đọc như thể có.** `verify_0012.py`
chạy trên một máy *có* `node` và *có* checkout ở nơi khác trên cùng đĩa; nó chứng minh
được phần đóng gói vì đường dẫn giải từ `__file__`, nhưng không chứng minh được nửa
"máy sạch" của prerequisite `node` (`spec.md` C5, và chính proof in câu đó ra). Và
`verify_0011.py` chưa chạy lần nào trong unit này — reboot, và đường update, không có ai đo
lại dù `docs/install.md` với `release.yml` đều bị sửa.

**Một phần của outcome đã tự kiểm trong lúc ship:** đường update. Lệnh chạy là đúng dòng
`## Update` của `docs/install.md`; nó báo `coscc 0.2.3 installed and running` và
`/home/bd/.config/coscc/env already exists, leaving it untouched` — dòng `PATH` thêm tay cho
nvm vẫn còn nguyên sau khi cập nhật, đúng như tài liệu hứa.

## How it is watched

Ba tín hiệu, không phải ba ý định.

1. **`uv run python scripts/verify_0012.py`**, chạy lại sau mỗi lần cài hoặc cập nhật. Exit
   0 là còn đúng; exit 1 là một claim gãy; exit 2 là máy không đo được, không phải bản phát
   hành hỏng. Cần `node`, một service ở `COS_URL` và ít nhất một workspace có `.cos/`.
2. **Bước guard trong `.github/workflows/release.yml`.** Không release nào đi qua được nếu
   wheel thiếu frontend, thiếu compile marker, thiếu `cos.mjs`, thiếu skill, hoặc **mang
   theo file settings**. Nó đỏ thì release dừng — đó là tín hiệu, và nó tự đọc được bằng
   `uv run python scripts/check_wheel.py <wheel>` ở bất kỳ đâu.
3. **Board của một bản cài thật.** `GET /api/board?cwd=<ws>` trả 400 là dấu hiệu đầu tiên và
   rõ nhất rằng cái này hỏng lại; nó chính là triệu chứng đã mở unit này.

**Band bị phá thì viết `idea.md` mới:** wheel bất kỳ mà `check_wheel.py` từ chối và vẫn được
phát hành; hoặc một bản cài cho `/api/board` trả 400 ngoài trường hợp thiếu `node`.

## What to do if it breaks

**Revert `38ff734`** (PR #10) — nó là một commit squash, chứa toàn bộ thay đổi mã của unit
này. `933fef3` chỉ là version và có thể để lại hoặc revert riêng.

Quay về được thì **mất gì**: bản cài trở lại đúng trạng thái `v0.2.2` — Board 400, step chạy
thiếu luật mà không nói. Nghĩa là revert ở đây không phải đường an toàn; nó đưa về một trạng
thái đã biết là hỏng. Nếu chỗ hỏng nằm ở `skill_for` từ chối quá tay (F1 trong `review.md`
là chỗ gần nhất với rủi ro đó), thì sửa đúng chỗ ấy rẻ hơn nhiều so với revert cả unit.

**Người đang chạy bản cũ không tự được sửa.** Chạy lại dòng install là đường cập nhật duy
nhất (`docs/install.md` mục `## Update`), nên bất kỳ bản vá nào sau này cũng chỉ tới tay họ
khi có release mới. `uv tool upgrade coscc` gõ tay vẫn không làm gì cả.
