# Intent: Work reaches main with no convention, and nothing is ever released
Author: Bao Do. Type: feat. Status: accepted.

> **Sửa ngày 2026-09-22, sau khi bản đầu đã accepted và commit (`ff5634d`).** Viết `spec.md`
> làm lộ ra rằng `## Proposed outcome` tự làm mình sai: nó lấy mốc "hôm nay" và đòi mọi commit
> sau đó phải qua pull request, trong khi chính `intent.md`, `spec.md` và `plan.md` của unit
> này đi thẳng vào `main` **trước khi** cái cổng ấy tồn tại. Mốc đổi sang commit bật ruleset,
> và phần miễn trừ được nói ra thay vì để người đọc tự suy. Bản đầu đọc được ở `ff5634d`.

## Problem

Người khởi xướng nói, nguyên văn:

> "với branch thì cứ theo bestpracices thôi. feat,fix,docs, ... etc với đi theo một branch
> main và dùng release/tag, có prerelease và release các kiểu là được"

Đây là yêu cầu đã được nêu từ lần đầu — `.cos/0008_personal-name-blocks-publishing/intent.md`
mở bằng đúng câu *"Tôi muốn chuẩn hóa branch naming lại"* — và bị tách ra thành unit riêng ở
constraint 9 của file đó, với lý do là lúc ấy repo chưa có remote để thử. Nay có:
`https://github.com/baodq97/coscc`, public từ 2026-09-22.

### Không có quy ước nào, và không phải vì đã chọn không có

Đo trên HEAD ngày 2026-09-22:

| Thứ | Số đo |
|---|---|
| Branch | **1** — `main` |
| Tag | **0** |
| Release trên GitHub | **0** |
| Pull request từng mở | **0** |
| Commit, tất cả vào thẳng `main` | **137** |
| `.github/` | không tồn tại |

`.claude/` — nơi chứa toàn bộ harness — nói **0 chữ** về branch, tag, release hay version.
`.claude/scripts/cos.mjs` kiểm **0** thứ trong số đó; đếm nguyên từ, mỗi từ trả về 0. Nghĩa
là đây không phải một quy ước bị phá, mà là một khoảng trống chưa ai lấp.

`.claude/harness.md:94` ghi tình trạng hiện tại như một sự thật chứ không như một lựa chọn
đã cân: *"The author works alone and commits to `main`."*

### Version khai bốn chỗ và không gì giữ chúng bằng nhau

`pyproject.toml:3`, `package.json:3`, `uv.lock:151`, và `package-lock.json:3` cùng `:9` —
tất cả ghi `0.0.1`. Hai chỗ sau là file sinh ra, nhưng chúng vẫn mang con số và vẫn lệch
được. Chúng bằng nhau lúc này, và không có lệnh, test hay check nào làm cho điều đó tiếp tục
đúng. Không có tag nào, nên cũng không có chỗ thứ năm để đối chiếu — một bản phát hành hiện
không có số hiệu nào cả.

> **Sửa cùng ngày, lúc viết `plan.md`.** Bản đầu của mục này viết "hai chỗ" và nêu hai file.
> Đọc `uv.lock` và `package-lock.json` cho thấy bốn. Con số cũ không sai về hướng — không gì
> giữ chúng bằng nhau — nhưng nó sai về lượng, và một check dựng theo nó sẽ bỏ sót hai nơi.

### Vì sao hai việc này là một unit

Quy ước branch và quy ước release không tách được: một release là một điểm trên `main`, và
`main` nhận gì là do quy ước branch quyết. Đặt tên branch mà không bao giờ cắt release thì
quy ước không có gì kiểm nó ngoài thiện chí; cắt release mà không có quy ước branch thì không
nói được cái gì đã vào trong đó. Nên chúng dùng chung một kết quả đo, ở dưới.

## Proposed outcome

Trước **2026-10-13**, `gh release list --repo baodq97/coscc` trả về **hai dòng**: một
prerelease và một release `v0.1.0` sau nó. Tag của `v0.1.0` trỏ vào một commit trên `main`,
và **mọi commit vào `main` kể từ lúc quy ước có hiệu lực** đều đến qua một pull request, từ
một branch có tên theo quy ước được ghi trong `.claude/`.

"Kể từ lúc có hiệu lực" nghĩa là: từ commit bật ruleset trở đi. **Artifact của chính unit
này — `intent.md`, `spec.md`, `plan.md` — vào thẳng `main` và được miễn**, vì chúng là thứ
tạo ra quy ước và không có quy ước nào để chúng tuân theo lúc chúng được viết. Bản đầu của
mục này lấy mốc là "hôm nay", và như vậy nó tự làm mình sai: nó đòi những commit viết ra
chính nó phải đi qua một cổng mà chúng vừa dựng lên. Mốc phải là cái cổng, không phải cái
ngày.

Hôm nay, 2026-09-22: **0** release, **0** tag, **0** pull request, **137** commit đều vào
thẳng `main`.

Kết quả này sai nếu: tới ngày đó chưa có `v0.1.0`, hoặc có nhưng không có prerelease trước
nó, hoặc tag không nằm trên `main`, hoặc có ít nhất một commit sau mốc bật ruleset vào `main`
mà không qua pull request.

Ngày **2026-10-13 là do file này đặt**, không phải do người khởi xướng nêu. Sửa được, và sửa
ở đây.

## Affected users and systems

- **Người khởi xướng** — người duy nhất làm việc trên repo này, và là người mà mọi bước thêm
  vào quy trình sẽ làm chậm lại.
- **Bất kỳ ai copy `.claude/` sang repo khác.** Người khởi xướng chọn đặt quy ước **vào
  harness**, nên nó đi theo mọi bản sao. `.claude/harness.md:169` nói *"Copy `.claude/`. That
  is the whole harness"* — sau unit này, cái được copy sẽ còn mang theo một ý kiến về git,
  thứ nó chưa bao giờ có.
- **`.claude/scripts/cos.mjs`** — người khởi xướng chọn có một check chạy cục bộ. Script này
  hiện chỉ biết về `.cos/`; sau unit này nó biết về git.
- **`.github/workflows/`** — chưa tồn tại. Người khởi xướng chọn có CI, nên unit này tạo ra
  hạ tầng CI đầu tiên của repo.
- **`pyproject.toml:3` và `package.json:3`** — hai khai báo version.
- **Bốn unit đang kẹt ở `pr`** (`0005`, `0006`, `0007`, `0008`). Xem `## Open questions` mục 1:
  unit này **không** sửa chỗ kẹt đó, nhưng nó là thứ đầu tiên chạm vào cùng vùng.

## Constraints

1. **Quy ước vào `.claude/`, không vào `docs/`.** Người khởi xướng chốt. Hệ quả phải nói
   thẳng: harness tới giờ chỉ nói về *vòng lặp artifact*, chưa bao giờ nói về git. Đây là
   một loại nội dung mới trong đó, và nó sẽ được copy sang repo của người khác.
2. **Kiểm ở cả hai nơi: một check trong `cos.mjs` và một workflow CI.** Người khởi xướng
   chốt cả hai. Đây là lựa chọn đắt hơn hẳn so với prose thuần, và lý do nó đắt là đúng chỗ
   quan trọng: `.claude/harness.md:149-166` liệt kê những thứ *cố ý không xây*, và văn hoá
   của repo là mọi invariant đều advisory trừ thứ `cos.mjs` kiểm.
3. **Trunk là `main`.** Một branch dài hạn. Branch công việc ngắn hạn, merge vào rồi xoá.
4. **Tiền tố branch theo quy ước phổ biến** — `feat`, `fix`, `docs`, và các loại tương tự.
   Danh sách chính xác là việc của spec.
5. **Có prerelease trước release.** Người khởi xướng nêu cả hai.
6. **Hai khai báo version phải đồng bộ.** Người khởi xướng nêu như một yêu cầu. Cần nói rõ:
   "đồng bộ" là *yêu cầu*, không phải *cơ chế* — khi hai file lệch nhau, phải có chỗ nói cái
   nào đúng. Chọn chỗ đó là việc của spec.
7. **Không sửa artifact đã `accepted` trong `.cos/`.** Cùng tiền lệ đã dùng ở `0008`:
   `.cos/0002_no-workspace-management/spec.md:245-250`.
8. **Repo đã public.** Mọi thứ unit này thêm vào — kể cả file CI — là công khai ngay khi push.
9. **Mỗi work unit khai type của nó, và tên branch suy ra từ đó.** Người khởi xướng nêu:
   *"các intent/ work unit cũng nên biết type nó là gì nhỉ?"* Hiện `intent.md` mang
   `Author:` và `Status:` và không gì khác; `.claude/scripts/cos.mjs:12` khoá tên thư mục ở
   `NNNN_slug` — **0** chỗ trong repo nói một unit thuộc loại gì. Không có ràng buộc này thì
   tên branch là một lựa chọn độc lập với tên unit, và hai cái sẽ trôi khỏi nhau. Nơi khai và
   cách xử lý 8 unit đã đóng là việc của spec.
10. **`main` chỉ nhận squash merge.** Người khởi xướng nêu: *"thêm 1 rule chỉ squash merge"*,
   nói đúng lúc pull request đầu tiên sắp được merge bằng một merge commit. Hiện repo bật cả
   ba cách — `allow_merge_commit`, `allow_rebase_merge`, `allow_squash_merge` đều `true`, đo
   bằng `gh api repos/baodq97/coscc` ngày 2026-09-22 — và không cách nào bị chặn. Hệ quả cần
   nói thẳng: một branch công việc có tám commit sẽ thành **một** commit trên `main`, nên
   lịch sử từng bước của nó chỉ còn trong pull request. Chỗ đặt rule và cách cưỡng chế là
   việc của spec.

> **Sửa ngày 2026-09-22, lần thứ ba, trước khi viết dòng code đầu tiên.** Thêm constraint 9
> theo lời người khởi xướng, và file này nhận `Type: feat` ngay trên header — nó là unit đầu
> tiên khai, và là thứ tên branch của chính nó sẽ được suy ra.
>
11. **Branch phải đứng trên `main` mới nhất trước khi merge, và cập nhật bằng rebase.**
   Người khởi xướng nêu: *"à yêu cầu rebase update latest so với main trước nữa"*. Hiện
   không gì đòi điều đó: `gh pr view 1` trả `mergeStateStatus: CLEAN` trong khi CI đã chạy
   trên một nền có thể đã cũ. Hệ quả: một pull request xanh **không** chứng minh được `main`
   sau khi merge cũng xanh, vì hai thay đổi độc lập cùng pass rồi hỏng khi đứng cạnh nhau.
   Cập nhật bằng **rebase** chứ không bằng merge `main` vào branch, vì constraint 10 vừa cấm
   merge commit — hai quy tắc này phải cùng chiều. Cơ chế là việc của spec.

> **Sửa lần thứ tư, ngay trước khi merge pull request đầu tiên.** Constraint 10. Nó đến muộn
> hơn ba lần kia — `impl.md` và `pr.md` đã viết xong — nhưng nó đến *trước* lần merge đầu
> tiên, và đó là lần duy nhất còn kịp: sau khi một merge commit nằm trên `main` thì quy tắc
> chỉ còn áp cho tương lai chứ không còn mô tả được lịch sử.

## Open questions

1. **Bốn unit kẹt ở `pr` không được unit này sửa, và người khởi xướng đã nói để đó.** Tình
   trạng, đo 2026-09-22: `.claude/harness.md:94` nói tác giả commit thẳng `main`;
   `.claude/skills/write-pr/SKILL.md` invariant 4 bắt ghi `draft` khi không mở được PR;
   `.claude/scripts/cos.mjs:30` cho `pr` đúng ba status và **không có `skipped`**, trong khi
   `spec` thì có. Nên `cos.mjs gate 0005_hand-driven-invisible-loop review` trả về exit 1,
   *"pr.md is 'draft', not accepted"*, và ba stage cuối không với tới được.

   Unit này làm cho các unit **sau** nó có PR thật, nên chúng sẽ không gặp bức tường đó. Nó
   **không** gỡ được bốn unit đã lỡ, vì commit của chúng đã nằm trên `main`. Điều một câu trả
   lời sẽ đổi: nếu `pr` được thêm một status nghĩa là "không áp dụng", bốn unit đó đóng được;
   nếu không, chúng đứng đó vĩnh viễn và bảng `cos-status` mang bốn dòng không bao giờ sạch.

2. **Chỗ nào quyết version khi hai file lệch?** Constraint 6 đòi chúng bằng nhau nhưng chưa
   nói cái nào đúng. Điều một câu trả lời sẽ đổi: nếu tag là nguồn thì hai file là dẫn xuất và
   một check chỉ cần so ba thứ; nếu `pyproject.toml` là nguồn thì tag được dựng từ nó và
   `package.json` là bản sao.

3. **CI chạy những gì?** Kiểm tên branch là một việc; chạy `npm test` trên mỗi PR là việc
   khác và tốn khác. Điều một câu trả lời sẽ đổi: nếu CI chạy `npm test` thì nó cần `uv` và
   Python 3.14 trên runner, và thời gian mỗi PR tính bằng phút chứ không phải giây.

4. **Quy ước này có áp cho chính repo đang chứa nó không, ngay lập tức?** Harness được copy đi
   nơi khác, nhưng repo này cũng dùng nó. Điều một câu trả lời sẽ đổi: nếu có, thì mọi công
   việc từ `0010` trở đi phải đi qua branch và PR, kể cả việc sửa một dòng chính tả.

5. **`0.1.0` là số đúng cho bản đầu tiên?** Hiện cả hai file ghi `0.0.1`. `## Proposed outcome`
   chọn `v0.1.0` để có một con số cụ thể mà kiểm; nếu người khởi xướng muốn số khác thì sửa ở
   đây chứ không ở spec.
