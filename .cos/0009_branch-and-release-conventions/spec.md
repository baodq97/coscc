# Spec: A branch grammar, a tag grammar, and three places that check them
Intent: intent.md. Author: Bao Do. Status: accepted.

Skip assessment, ngày 2026-09-22:

| # | Tiêu chí | Verdict |
|---|---|---|
| 1 | ≤2 file đã tồn tại | **Fail** — `harness.md`, `cos.mjs`, `cos.test.mjs`, `pyproject.toml`, `package.json`, cộng `.github/` chưa tồn tại |
| 2 | Không đổi public interface / stored data | **Fail** — `cos.mjs` hiện khai ba lệnh (`:271`) và sẽ khai thêm; hai khai báo version đổi |
| 3 | Không thêm dependency | Pass — không gói npm hay pip nào |
| 4 | Không có hành vi ngoài `intent.md` | Pass |
| 5 | Không chạm bề mặt bảo mật | **Fail** — workflow đầu tiên của một repo **public**, chạy với `GITHUB_TOKEN` |

Tiêu chí 5 là cái ép mạnh nhất, không phải tiêu chí 1: thêm CI vào một repo công khai là mở
một bề mặt chưa từng có ở đây.

## Requirements

Mọi số đo ngày 2026-09-22.

**R1 — Ngữ pháp tên branch.** `<type>/<slug>`, trong đó `type` thuộc một tập đóng và `slug`
là chữ thường, nối bằng gạch ngang, 1–60 ký tự. Tập type: `feat`, `fix`, `docs`, `refactor`,
`test`, `chore`, `perf`, `build`, `ci`, `revert`. Đây là tập của Conventional Commits, dùng
lại nguyên vì `intent.md` constraint 4 nói "quy ước phổ biến" chứ không nói tập riêng.

Kiểm bằng bảng, mỗi dòng là một test:

| Tên | Kết quả |
|---|---|
| `feat/branch-conventions` | nhận |
| `fix/version-drift` | nhận |
| `main` | **từ chối** — trunk không phải branch công việc |
| `feature/foo` | **từ chối** — `feature` không thuộc tập |
| `feat/Foo` | **từ chối** — có chữ hoa |
| `feat/` | **từ chối** — slug rỗng |
| `feat/a--b` | **từ chối** — gạch ngang đôi |
| `feat/foo/bar` | **từ chối** — hai dấu gạch chéo |

**R2 — Ngữ pháp tag, và `cos.mjs` khai một lệnh kiểm nó.** Release là `vX.Y.Z`. Prerelease là
`vX.Y.Z-rc.N` với `N` ≥ 1. Không chấp nhận dạng khác. `v0.1.0` nhận; `0.1.0` từ chối (thiếu
`v`); `v0.1` từ chối; `v0.1.0-rc` từ chối (thiếu số); `v0.1.0-rc.0` từ chối (`N` phải ≥ 1).

> **Sửa cùng ngày, lúc chạy proof lần đầu.** Bản đầu của R2 chỉ nêu ngữ pháp, không nêu lệnh
> — R3 và R4 khai hai lệnh, R13 khai lệnh thứ ba, và tag không có lệnh nào. Hệ quả: workflow
> của R8 phải tự quyết định prerelease bằng một phép so chuỗi của riêng nó, và ngữ pháp tag
> có **hai** bản cài đặt trôi độc lập — đúng thứ R4 tồn tại để chặn, ở một chỗ khác. Vậy có
> lệnh thứ tư, và workflow gọi nó thay vì tự đoán.

**R3 — `cos.mjs` khai một lệnh kiểm tên branch.** Nhận một tên làm tham số, hoặc đọc branch
đang checkout khi không có tham số. Exit `0` nhận, `1` từ chối kèm lý do nêu tên quy tắc bị
phạm. Kiểm: tám dòng của bảng R1 chạy qua lệnh này và cho đúng tám kết quả đó.

**R4 — `cos.mjs` khai một lệnh kiểm version đồng bộ, trên bốn chỗ.** `pyproject.toml:3`,
`package.json:3`, `uv.lock:151` và `package-lock.json:3`+`:9` đều mang số; khi có tag
`vX.Y.Z` trên HEAD thì so cả năm. Exit `0` khi khớp, `1` khi lệch kèm giá trị của từng bên.
Kiểm: sửa **bất kỳ** chỗ nào trong bốn chỗ thành số khác thì lệnh phải đỏ — negative control,
chạy thật chứ không chỉ mô tả.

> **Sửa cùng ngày, lúc viết `plan.md`.** Bản đầu của R4 chỉ nêu hai file, vì `intent.md` đếm
> hai. Đọc code lúc lập kế hoạch cho thấy bốn: hai file khai bằng tay và hai lockfile sinh
> ra. Một check dựng theo con số cũ sẽ để hai nơi trôi tự do — đúng thứ R4 tồn tại để chặn.
> `intent.md` đã sửa theo.

**R5 — Bốn lệnh mới có test trong `.claude/scripts/cos.test.mjs`.** File này hiện có **23**
test và `npm test` chạy nó. Sau unit này số test tăng, và `npm run test:node` vẫn xanh. Đây
là điều kiện để bốn lệnh đó không phải là prose: văn hoá repo ghi ở `.claude/harness.md:149-166`
là mọi invariant đều advisory trừ thứ có script kiểm.

**R6 — Ba lệnh mới không nhận `--root`.** `cos.mjs` hiện nhận `--root <dir>` và
`coscc/board.py:90` dùng nó để trỏ script vào **`.cos/` của repo người khác**;
`coscc/board.py:48` ghi rằng script "reads files and prints JSON. It needs no secret, so it
is given none". Một lệnh đọc git mà tôn trọng `--root` sẽ đi đọc trạng thái git của bản
checkout của người khác. Kiểm: gọi ba lệnh đó kèm `--root` bị từ chối với exit khác 0, và lệnh của R13 thì nhận.

**R7 — Một workflow chạy trên pull request.** Kiểm tên branch nguồn theo R1, và chạy
`npm test`. `permissions` khai tường minh ở mức tối thiểu; mọi action bên thứ ba ghim theo
commit SHA, không theo tag. Kiểm: mở một PR từ branch tên sai thì job đỏ; từ branch tên đúng
thì xanh.

**R8 — Một workflow chạy khi đẩy tag khớp R2**, dựng GitHub release từ tag đó, và đánh dấu
**prerelease** khi tag có hậu tố `-rc.N`. Kiểm: `gh release view` cho tag prerelease trả
`isPrerelease: true`, cho tag release trả `false`.

**R9 — `main` chỉ nhận commit qua pull request.** Hiện `gh api repos/baodq97/coscc/rulesets`
trả về `[]`. Kiểm: `gh api repos/baodq97/coscc/rulesets` trả về ít nhất một ruleset áp cho
`main` đòi pull request; và một lần `git push` thẳng lên `main` bị từ chối. **Đây là một cài
đặt của repository, không phải một file** — xem C1.

**R10 — Harness ghi quy ước.** `.claude/harness.md` có một mục mới nói ngữ pháp branch, ngữ
pháp tag, bốn lệnh, và trường `Type:`. Kiểm: mục đó nêu đủ mười type của R1, cả hai dạng tag
của R2, và một câu nói ruleset không đi theo bản copy (C1).

**R11 — Nguồn sự thật của version, và ba chỗ khớp nhau.** `pyproject.toml` là nguồn;
`package.json` là bản sao; tag dựng từ nguồn. Xem C5 — người khởi xướng yêu cầu "đồng bộ" mà
chưa chọn nguồn, và spec này chọn.

**R12 — Phép đo nghiệm thu.** `gh release list --repo baodq97/coscc` trả về **hai dòng**:
một prerelease và `v0.1.0`. Tag `v0.1.0` nằm trên `main`. **Mọi commit vào `main` từ commit
bật ruleset trở đi** đều qua pull request — không phải "từ hôm nay", vì `intent.md`
`## Proposed outcome` đã sửa đúng chỗ đó: artifact của chính unit này vào thẳng `main` trước
khi cổng tồn tại, và được miễn. Hôm nay: **0** release, **0** tag, **0** PR, **137** commit
đều vào thẳng `main`. Đây là outcome của `intent.md`, nguyên văn.

**R13 — `intent.md` khai type, và tên branch suy ra từ unit.** `intent.md` constraint 9.
Type khai trên dòng header, cùng chỗ `Author:` và `Status:` đang ở — **không** trong tên thư
mục, vì `.claude/scripts/cos.mjs:12` khoá `UNIT_RE` ở `^(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)$`
và đổi nó là đổi tên 9 thư mục đã tồn tại. Dạng: `Type: <type>.`, với `type` thuộc đúng tập
mười của R1.

Từ đó, branch của một unit là **suy ra chứ không đặt tay**: `<type>/<slug>`, trong đó `slug`
là phần sau `NNNN_`. `0009_branch-and-release-conventions` với `Type: feat` cho
`feat/branch-and-release-conventions`, và tên ấy thoả R1 mà không cần ai kiểm lại bằng mắt.

**Tuỳ chọn cho `0001`–`0008`, bắt buộc từ `0009`.** Tám unit kia đã `accepted` và đã đóng;
backfill chúng là sửa artifact đã ký, đúng thứ `.cos/0008_personal-name-blocks-publishing/intent.md`
constraint 4 cấm, đổi lấy gần như không gì — chúng không có branch nào để đối chiếu. Thiếu
`Type:` trên một unit cũ **không** là lỗi; thiếu trên một unit mới thì là.

Kiểm: `cos.mjs` đọc được type, in ra tên branch suy ra cho một unit, và **từ chối** một type
ngoài tập mười. `0009` hiện khai `Type: feat` (`intent.md:2`); tám unit còn lại khai **0** lần.

> **Sửa ngày 2026-09-22, lần thứ hai, trước dòng code đầu tiên.** R13 thêm theo `intent.md`
> constraint 9. Nó không mở rộng outcome — R12 vẫn là phép đo nghiệm thu — mà đóng một chỗ hở
> trong R1: ngữ pháp tên branch không nói tên *nào* là đúng cho một công việc cụ thể, nên hai
> tên đều hợp lệ mà chỉ một cái khớp unit.

## Design

### Ba nơi kiểm, và chúng không thay thế được nhau

Đây là phần quan trọng nhất của thiết kế, vì `intent.md` constraint 2 chọn "cả `cos.mjs` và
CI" mà chưa ai nói hai thứ đó đủ chưa. **Chúng không đủ.**

| Nơi | Kiểm được gì | Không kiểm được gì |
|---|---|---|
| `cos.mjs`, cục bộ | tên branch, version đồng bộ | không chặn được gì — nó chỉ trả exit code |
| CI, trên GitHub | tên branch nguồn của PR, test, dựng release | **không chặn được một `git push` thẳng lên `main`** |
| Ruleset của repo | đúng một việc: `main` chỉ nhận qua PR | không biết gì về ngữ pháp hay version |

Một `git push origin main` không đi qua PR nên không có workflow nào chạy, và `cos.mjs` thì
chạy khi người ta gọi nó. Chỉ ruleset đứng chắn được. Nên R9 tồn tại, và nó là chân thứ ba
bắt buộc chứ không phải phần thêm.

### Ranh giới `cos.mjs` không được vượt

Script này tới giờ **chỉ đọc file và in ra JSON**, và điều đó là cố ý: `coscc/board.py:31,90`
chạy **bản của repo này** trỏ vào `.cos/` của workspace khác bằng `--root`, chính xác để
không chạy file `cos.mjs` nằm trong repo mà ai đó đã clone. Hai lệnh mới đọc git, nên chúng
phải nằm **ngoài** đường `--root` — R6. Cách chia: `--root` tiếp tục chỉ áp cho các lệnh đọc
`.cos/`; lệnh đọc git luôn làm việc trên thư mục hiện tại.

Lệnh của R13 nằm **bên kia** ranh giới đó: nó chỉ đọc `intent.md` và tên thư mục, đúng loại
việc `--root` sinh ra để làm, nên nó nhận `--root` như `status` và `gate`. Bốn lệnh mới, ba
bên này một bên kia — và đường kẻ là "lệnh mô tả bản checkout này hay mô tả một `.cos/`",
không phải "mới hay cũ". Lệnh kiểm tag không chạm git, nhưng nó trả lời về tag của repo đang
đứng, nên nó ở cùng phía với hai lệnh kia.

### Quy ước đi theo harness, nhưng thứ cưỡng chế thì không

`intent.md` constraint 1 đặt quy ước vào `.claude/` để nó được copy. Copy được: ngữ pháp,
hai lệnh, và test của chúng — tất cả nằm trong `.claude/`. **Không copy được:** workflow
(`.github/` nằm ngoài `.claude/`) và ruleset (một cài đặt trên GitHub). Nên bản copy mang
theo *quy ước* và *phép kiểm cục bộ*, còn hai chân kia phải dựng lại ở mỗi repo. C1 ghi điều
này như một chỗ hở chứ không như một chi tiết.

### Luồng của một unit sau khi có quy ước

`write-intent` khai `Type:` → cắt branch `<type>/<slug>`, tên lấy từ lệnh của R13 chứ không
gõ tay → commit → push → mở PR → CI kiểm tên và chạy test →
merge → xoá branch. Release: chọn version, cập nhật `pyproject.toml`, đồng bộ `package.json`,
merge qua PR, rồi đẩy tag `vX.Y.Z-rc.N` để có prerelease và `vX.Y.Z` để có release.

`pr.md` của unit nhận URL thật từ lệnh mở PR, nên stage `pr` không còn phải ghi `draft` vì
không có gì để ghi. Điều này **chỉ đúng cho unit từ `0010` trở đi**; bốn unit đã kẹt không
được unit này gỡ — OQ1.

## Out of scope

- **Gỡ bốn unit kẹt ở `pr`** (`0005`–`0008`). Người khởi xướng đã nói để đó. Unit này làm cho
  unit sau không gặp bức tường, không làm cho unit trước thoát ra.
- **Thêm status cho stage `pr`.** Đó là sửa `cos.mjs:30` và là một quyết định về vòng lặp,
  không phải về git.
- **Conventional Commits cho *thông điệp commit*.** R1 mượn tập type của nó cho *tên branch*.
  Ràng buộc thông điệp commit là việc khác và không có trong `intent.md`.
- **Sinh changelog tự động, `semantic-release`, tag có chữ ký.** Không có gì trong intent cho
  phép.
- **Bump version tự động.** R11 chọn nguồn sự thật; ai bump và khi nào là việc của người dùng.
- **Chạy lại năm proof cũ trong CI.** `verify_0005.py` đẩy branch lên một repo thật và tốn
  quota; đưa nó vào CI là biến mỗi PR thành một lần tiêu tiền.
- **Áp quy ước ngược lại cho 137 commit đã có.**

## Concerns

**C1 — Chân cưỡng chế duy nhất không đi theo harness, và đó là chân quan trọng nhất.**
`intent.md` constraint 1 đặt quy ước vào `.claude/` "để nó được copy đi khắp nơi". Nhưng thứ
thật sự ngăn một commit vào thẳng `main` là **ruleset**, một cài đặt trên GitHub của từng
repo. Nó không nằm trong bất kỳ file nào và không được copy. Bản sao của harness sẽ mang theo
một quy ước mà mặc định **không ai cưỡng chế**, và người copy sẽ không biết điều đó trừ khi
`harness.md` nói ra. R10 vì thế phải nói. **Người khởi xướng quyết** có chấp nhận hình dạng
đó không.

**C2 — Đây là workflow đầu tiên của một repo public, và bề mặt đó chưa từng tồn tại ở đây.**
Những thứ phải đúng ngay từ file đầu: `permissions` khai tường minh chứ không dựa mặc định;
action bên thứ ba ghim theo commit SHA chứ không theo tag, vì tag đổi được; và `pull_request`
chạy với quyền đọc — dùng `pull_request_target` sẽ cho code của người lạ chạy với token ghi.
Không lỗi nào trong số này làm test đỏ; chúng chỉ lộ ra khi đã muộn.

**C3 — CI chạy `npm test` thì nó đo trên một interpreter khác cái đã đo các proof.** `0007`
đo lại mọi thứ trên Python 3.14 cục bộ, và `.claude/CLAUDE.md` ghi rằng đổi interpreter không
mang bằng chứng cũ theo. Runner của GitHub sẽ có bản vá khác. Nên CI xanh **không** thay thế
được `npm test` cục bộ, nó chỉ bắt được thứ hỏng ở cả hai nơi. Ghi lại để không ai đọc badge
xanh thành "đã đo trên nền của proof".

**C4 — Quy ước áp cho chính repo này từ `0010` làm mọi việc chậm lại, kể cả việc một dòng.**
Sửa một lỗi chính tả cũng phải cắt branch, mở PR, chờ CI. Với một người làm một mình, đó là
chi phí thật và nó sẽ bị phá vào đúng lúc vội. R9 tồn tại chính vì thiện chí không đủ — và
cũng chính R9 là thứ sẽ gây khó chịu trước tiên. **Người khởi xướng quyết** nếu muốn nới.

**C5 — Nguồn sự thật của version do spec này chọn, không do người khởi xướng nêu.** Họ nói
*"cả 2 phải đồng bộ chứ nhỉ?"* — đó là *yêu cầu*, và nó không nói cái nào đúng khi hai bên
lệch. R11 chọn `pyproject.toml` vì đây là app Python và `package.json` chỉ tồn tại để chạy
`npm test` (`package.json:7-9`). Nếu người khởi xướng muốn tag là nguồn thì R11 và R4 đổi
theo, và đó là một đoạn văn ở đây chứ không phải một lần viết lại sau.

**C6 — Hai lệnh mới làm `cos.mjs` biết về git, và nó chưa bao giờ biết.** Hệ quả nằm ở chỗ
script này được app chạy trỏ vào repo của người khác (`coscc/board.py:90`). R6 dựng rào, và
rào đó phải có test — một rào không có test là một câu trong file này, không phải một rào.

**C7 — Mười type là một tập đóng, và tập đóng sẽ chặn đúng lúc không ngờ.** Một công việc
không rơi vào `feat|fix|docs|refactor|test|chore|perf|build|ci|revert` sẽ không đặt được tên
branch, và lối thoát duy nhất là sửa harness. Đó là chủ ý — một tập mở thì không kiểm được gì
— nhưng nó sẽ gây vướng ít nhất một lần.

**C8 — R13 chết nếu `write-intent` không đòi `Type:`.** Template ở
`.claude/skills/write-intent/SKILL.md` hiện in header là `Author: <name>. Status: accepted.`
và không gì khác, nên một session làm đúng skill sẽ viết ra một `intent.md` thiếu type. Một
lệnh đọc type mà không ai viết type là một lệnh luôn trả về rỗng. Vậy R13 kéo theo một sửa
đổi trong skill, và đó là **file thứ tư trong `.claude/` mà unit này chạm** ngoài `harness.md`,
`cos.mjs`, `cos.test.mjs`.

## Open questions

1. **Bốn unit kẹt ở `pr` vẫn kẹt.** Mang nguyên từ `intent.md` OQ1. Điều một câu trả lời sẽ
   đổi: thêm một status nghĩa là "không áp dụng" cho stage `pr` thì bốn unit đóng được và
   `cos-status` sạch; không thì bốn dòng đó đứng vĩnh viễn. Unit này **không** đụng tới, theo
   lời người khởi xướng.

2. **CI chạy `npm test` đầy đủ hay chỉ kiểm tên branch?** `intent.md` OQ3. R7 chọn chạy đầy
   đủ. Điều một câu trả lời khác sẽ đổi: bỏ `npm test` thì workflow chạy trong vài giây và
   không cần `uv` hay Python trên runner, nhưng PR sẽ xanh mà không ai biết test có qua không.

3. **`0.1.0` có phải số đúng?** `intent.md` OQ5. Hai file hiện ghi `0.0.1`. R12 giữ `v0.1.0`.
   Điều một câu trả lời sẽ đổi: nếu muốn `0.0.2` thì R12 và outcome của intent đổi số, và chỗ
   sửa là `intent.md` chứ không phải file này.

4. **Ruleset đặt ở mức nào?** R9 chỉ đòi "`main` nhận qua PR". Chưa nói có đòi CI xanh trước
   khi merge không, và có cho phép chủ repo bỏ qua không. Điều một câu trả lời sẽ đổi: đòi CI
   xanh thì R7 thành một cổng thật; không đòi thì nó là một đèn báo.

5. **Bản copy của harness ở repo khác dựng hai chân kia bằng gì?** C1. Điều một câu trả lời
   sẽ đổi: nếu unit này kèm một workflow mẫu và một lệnh dựng ruleset thì bản copy dùng được
   ngay; nếu không, `harness.md` chỉ mô tả và mỗi người tự dựng lại.
