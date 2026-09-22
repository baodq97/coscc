# Plan: Bootstrap the convention on the branch that introduces it
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: done.

Mười sáu bước. Bước 1 cắt branch; bước 2 dựng bằng chứng và nó phải **đỏ**, như `0008` đã
làm. **Mọi thứ từ bước 1 trở đi đi qua cổng mà unit này dựng lên** — kể cả `impl.md` và
`pr.md` của chính nó. Bước 12 bật ruleset và không dễ lùi.

Đây là lần đầu repo này làm việc trên branch. `intent.md` `## Proposed outcome` miễn trừ
`intent.md`, `spec.md` và `plan.md` — ba file đó vào thẳng `main` vì chúng là thứ tạo ra
quy ước. Ngoài ba file đó không còn miễn trừ nào.

> **Sửa cùng ngày, trước dòng code đầu tiên.** Bản đầu đặt proof lên `main` rồi mới cắt
> branch, gọi nó là "file cuối cùng được miễn trừ". Người khởi xướng hỏi *"cần checkout trước
> nhỉ?"* và câu hỏi đúng: proof là một **bước**, không phải artifact của vòng lặp, nên không
> có gì miễn trừ nó. Cắt branch trước thì ngoại lệ ít đi một, và PR mang trọn phần triển khai
> thay vì thiếu đúng cái file quyết định pass/fail. Bản đầu cũng đánh số bước ruleset là 13
> trong phần mở đầu trong khi `## Order of work` để ở 12; nay là 12 ở cả hai.
>
> Cùng lúc, `intent.md` constraint 9 và `spec.md` R13 thêm khái niệm **type của work unit**.
> Việc này chạm bước 3, bước 4, một file skill, và thêm một claim vào `## Proof`.

## Files that change

### Mới

- `scripts/verify_0009.py` **(new)** — bằng chứng của unit này.
- `.github/workflows/pr.yml` **(new)** — chạy trên pull request.
- `.github/workflows/release.yml` **(new)** — chạy khi đẩy tag.
- `.github/` chưa tồn tại; unit này tạo thư mục đó.

### Sửa

- `.claude/scripts/cos.mjs` (275 dòng). Ba chỗ: thêm các hàm thuần được export cạnh
  `parseStatus` (`:50`) … `nextNumber` (`:164`); thêm bốn khoá vào bảng `run` (`:265-269`);
  sửa dòng usage (`:271`). Cộng phần từ chối `--root` — hiện `--root` bị tước khỏi `argv`
  trước khi đọc lệnh (`:261`), nên dispatcher phải biết nó **đã** có mặt.
- `.claude/scripts/cos.test.mjs` (175 dòng, **23** test). Nó import hàm thuần trực tiếp từ
  `cos.mjs` (`:6`) chứ không gọi qua subprocess — hàm mới theo đúng hình dạng đó.
- `.claude/harness.md` — một mục mới. Mục cuối hiện là `## Copying this into another
  repository` (`:167`).
- `.claude/skills/write-intent/SKILL.md` — template header hiện là `Author: <name>. Status:
  accepted.` và phải đòi `Type:` (`spec.md` C8). Không sửa chỗ này thì lệnh đọc type luôn trả
  về rỗng.
- `pyproject.toml:3`, `package.json:3` — `0.0.1` → `0.1.0`.
- `uv.lock:151`, `package-lock.json:3` và `:9` — sinh lại theo.

### Không đổi

`.cos/` của tám unit khác — không artifact nào bị sửa (`intent.md` constraint 7), và `Type:`
cố ý **không** backfill cho chúng (`spec.md` R13). Artifact của chính `0009` đã sửa xong
trước bước 1 và không đổi nữa. `coscc/` — unit này không chạm vào ứng dụng.

## Order of work

**1. Cắt branch.**
`git switch -c feat/branch-and-release-conventions`. Tên này suy ra từ `0009` +
`Type: feat` (`intent.md:2`) theo `spec.md` R13, và thoả đúng ngữ pháp mà chính branch này
sắp định nghĩa — vòng tròn là có thật và là cách duy nhất khởi động.
*Kiểm:* `git rev-parse --abbrev-ref HEAD` trả đúng tên đó; `main` không nhận thêm commit nào
từ đây tới bước 11.

**2. Dựng `scripts/verify_0009.py`, và nó phải đỏ.**
Khuôn theo `scripts/verify_0008.py`: claim tĩnh, exit `0`/`1`/`2`, dùng `say`, `EXIT_PASS`,
`EXIT_BROKEN`, `EXIT_ENV` từ `scripts/proof_harness.py`. Claim liệt kê ở `## Proof`.
*Kiểm:* chạy nó, exit `1`, và số claim đỏ gần bằng tổng — một proof viết trước mà xanh ngay
là một proof không hỏi gì.

**3. Hàm thuần cho ngữ pháp và cho type, cùng test.**
Export từ `cos.mjs`: một hàm nhận tên branch, một nhận tên tag, một so các chuỗi version, và
một đọc `Type:` ra khỏi header `intent.md` rồi ghép `<type>/<slug>`. Ngữ pháp tag là **một**
bản cài đặt dùng chung: workflow của bước 7 gọi lệnh chứ không tự so chuỗi. Cả bốn **không đọc file,
không gọi git** — chúng nhận chuỗi và trả kết quả, đúng hình dạng `parseStatus` (`cos.mjs:50`)
đang có. Test import trực tiếp; tám dòng bảng `spec.md` R1 và bốn ví dụ tag của R2 thành tám
cộng bốn assertion, cộng các trường hợp của R13: type hợp lệ, type ngoài tập mười, không có
`Type:`.
*Kiểm:* `npm run test:node` xanh, số test lớn hơn 23.

**4. Bốn lệnh, và rào `--root` cho đúng ba trong bốn.**
Nối bốn khoá vào bảng `run` (`cos.mjs:265-269`), sửa dòng usage (`:271`). `check-branch` đọc
branch đang checkout khi không có tham số; `check-tag` kiểm ngữ pháp tag; `check-version` đọc
năm con số ở bốn file; `unit-branch` nhận một unit và in tên branch suy ra.

Ba lệnh đầu **từ chối `--root`** với exit khác `0` — `spec.md` R6, lý do ở
`coscc/board.py:48,90`: script này được app trỏ vào repo của người khác và nó "needs no
secret, so it is given none". `unit-branch` **nhận** `--root`: nó chỉ đọc `.cos/`, đúng việc
`--root` sinh ra để làm. Đường kẻ là "lệnh mô tả bản checkout này, hay mô tả một `.cos/`".

Cộng `.claude/skills/write-intent/SKILL.md`: header của template đòi `Type:` (`spec.md` C8).
*Kiểm:* tám dòng bảng R1 chạy qua CLI cho đúng tám kết quả; `cos.mjs --root /tmp <lệnh git>`
exit khác 0 cho cả ba; `cos.mjs --root . unit-branch 0009_branch-and-release-conventions` in
ra `feat/branch-and-release-conventions`; `npm test` xanh.

**5. Mục mới trong `.claude/harness.md`.**
Ngữ pháp branch (đủ mười type), ngữ pháp tag, bốn lệnh, trường `Type:` của `intent.md` cùng
cách suy ra tên branch, và **một câu nói thẳng rằng thứ cưỡng chế mạnh nhất — ruleset — không
đi theo bản copy** (`spec.md` C1). Không có câu đó thì người copy harness nhận một quy ước
không ai gác.
*Kiểm:* claim tài liệu của proof chuyển xanh.

**6. Bump version lên `0.1.0`.**
`pyproject.toml:3` và `package.json:3` sửa tay; `uv lock` và `npm install --package-lock-only`
sinh lại hai lockfile.
*Kiểm:* lệnh kiểm version ở bước 4 exit `0`; `git diff` trên hai lockfile chỉ chạm dòng
version — bất kỳ dòng nào của package thứ ba xuất hiện thì hoàn tác (Risk 5).

**7. Hai workflow.**
`pr.yml`: kiểm tên branch nguồn bằng `check-branch`, chạy `npm test`. `release.yml`: gọi
`check-tag` để quyết prerelease rồi dựng release từ tag — không tự so chuỗi, vì hai bản cài
đặt của một ngữ pháp là thứ `spec.md` R2 vừa sửa để tránh. Cả hai khai `permissions` tường minh ở mức tối thiểu và
ghim mọi action bên thứ ba **theo commit SHA**, không theo tag (`spec.md` C2).
*Kiểm:* claim C7 của proof xanh. Nó kiểm **cấu trúc, không phải cú pháp**: `permissions:` có
mặt, không có `pull_request_target`, mọi `uses:` ghim theo SHA 40 hex. Không có trình đọc YAML
ở đây — `spec.md` tiêu chí 3 cấm thêm dependency và pyyaml không được cài. Một file YAML hỏng
vì thế lọt qua C7 và chỉ lộ ở bước 9, nơi GitHub là trình parse và triệu chứng là `gh pr
checks` rỗng. Đó là Risk 1, viết ra chứ không vá bằng một gói mới.

**8. Viết `impl.md`.** Stage `impl`, trên branch. Đo lại mọi con số tại thời điểm đó.
*Kiểm:* `cos.mjs gate 0009_branch-and-release-conventions pr` exit 0.

**9. Đẩy branch và mở PR thật.**
`git push -u origin feat/branch-and-release-conventions` rồi `gh pr create`. Đây là pull
request **đầu tiên** của repo; hiện `gh pr list --state all` trả về 0 dòng.
*Kiểm:* lệnh trả về một URL thật; `gh pr checks` cho thấy cả hai job và cả hai xanh. Nếu
không có job nào chạy thì workflow sai cú pháp — Risk 1.

**10. Viết `pr.md` với URL thật, commit lên cùng branch, đẩy.**
Stage `pr`. Đây là `pr.md` đầu tiên trong repo có URL thay vì một lời giải thích tại sao
không có.
*Kiểm:* `cos.mjs gate 0009_branch-and-release-conventions review` exit 0.

**11. Squash-only, rồi ruleset, rồi mới merge.** *(Thứ tự là cái quan trọng — Risk 8. Bước
này và bước 12 đã **đảo chỗ** so với bản đầu; lý do ở `spec.md` R15.)*

a. Đặt `allow_merge_commit=false`, `allow_rebase_merge=false`, `delete_branch_on_merge=true`
   trên repo, để lần merge đầu tiên **là** một squash chứ không phải một ngoại lệ được tha.
b. Bật ruleset của bước 12 — **trước** khi merge, không sau. Một cổng bật sau khi pull
   request đầu tiên đã đi qua là một cổng mà pull request đó chưa từng đi qua.
c. `gh pr update-branch --rebase` nếu branch đã cũ.
d. `gh pr merge --squash --delete-branch`.

*Kiểm:* `gh api repos/baodq97/coscc` trả `allow_squash_merge: true` và hai cái kia `false`;
`gh pr view 1 --json state` trả `MERGED`; `main` nhận **một** commit chứ không mười ba;
`git log --merges main` không có commit mới; branch biến mất khỏi remote.

**12. Ruleset trên `main` — ba rule.** *(Khó lùi — Risk 2. Chạy ở bước 11b.)*
`pull_request`; `required_linear_history`, chân thứ hai của R14, từ chối merge commit kể cả
khi ai đó bật lại hai cái nút; và `required_status_checks` với
`strict_required_status_checks_policy: true` cùng hai context `branch-name` và `tests`,
là R15. Hiện `gh api repos/baodq97/coscc/rulesets` trả `[]`.

Hai context phải khớp **đúng** tên job trong `.github/workflows/pr.yml`, nên chúng được đối
chiếu với `gh pr checks 1` chứ không chép từ trí nhớ: tên sai thì pull request không bao giờ
merge được.
*Kiểm:* lệnh đó trả về một ruleset `active` áp cho `main` mang cả ba rule; và một lần
`git push` thẳng lên `main` bị **từ chối** — negative control, chạy thật.

**13. Tag prerelease.**
`git tag v0.1.0-rc.1 && git push origin v0.1.0-rc.1`.
*Kiểm:* `gh release view v0.1.0-rc.1 --json isPrerelease` trả `true`.

**14. Tag release.**
`git tag v0.1.0 && git push origin v0.1.0`.
*Kiểm:* `gh release view v0.1.0 --json isPrerelease` trả `false`; `gh release list` trả **hai
dòng**; `git branch --contains v0.1.0` chứa `main`.

**15. Chạy `verify_0009.py`.**
*Kiểm:* exit `0`.

**16. Đóng unit qua một branch thứ hai.**
`fix/proof-0009-vacuous-claim` mang `review.md`, `ship.md`, bản vá C12, và ba đính chính
trong artifact. Mở PR, merge. Không có lối nào khác: bước 11b đã bật cổng, nên hai artifact
cuối cũng phải đi qua nó. Đó là lần đầu quy ước tự áp lên chính nó mà không phải bootstrap.

*Tên branch đổi so với bản đầu.* Kế hoạch viết `docs/close-0009`, nhưng branch này mang một
**bản vá thật**: `verify_0009.py` C12 từng báo xanh trên một câu hỏi chưa bao giờ được hỏi
(`review.md` finding 1). `docs/` sẽ nói sai nội dung của nó.
*Kiểm:* `cos.mjs status` cho `0009` đủ tám cột, và `plan.md` này đổi sang `Status: done`.

### Chọn không làm

- **Không gỡ bốn unit kẹt ở `pr`.** `spec.md` `## Out of scope`; người khởi xướng đã nói để
  đó. Unit này làm cho unit **sau** có PR thật, không làm cho unit trước thoát ra.
- **Không ràng buộc thông điệp commit.** R1 mượn tập type của Conventional Commits cho *tên
  branch*; ràng buộc thông điệp là việc khác và không có trong `intent.md`.
- **Không đưa năm proof cũ vào CI.** `verify_0005.py` đẩy branch lên repo thật và tốn quota;
  đưa vào CI là biến mỗi PR thành một lần tiêu tiền.
- **Không sinh changelog, không `semantic-release`, không tag có chữ ký.**
- **Không sửa `README.md`.** `intent.md` constraint 1 đặt quy ước vào `.claude/`. README của
  một repo public thường có mục đóng góp, nhưng không có gì trong intent cho phép, và thêm
  nó ở đây là mở rộng phạm vi lặng lẽ.

## Risks

**1. Workflow sai cú pháp thì không chạy, và "không chạy" trông giống "chưa xong".** GitHub
không báo lỗi ồn ào cho một file YAML hỏng — PR chỉ đơn giản không có check nào. Bước 9 vì
thế kiểm **sự có mặt của hai job**, không chỉ kiểm chúng xanh.
*Dấu hiệu:* `gh pr checks` in ra rỗng.

**2. Bật ruleset rồi thì đường sửa cũng đi qua ruleset.** Nếu bước 12 để lại `main` hỏng, cách
sửa là một PR nữa. Với một người làm một mình đó là chi phí cố ý (`spec.md` C4), nhưng nó
đắt đúng lúc đang vội. Không bật cho tới khi bước 11 đã merge sạch.
*Dấu hiệu:* một `git push` bị từ chối lúc không mong — đó là ruleset làm đúng việc, và cũng
là lúc nó phiền nhất.

**3. Rào `--root` không có test thì nó là một câu trong file này.** `spec.md` C6. `--root`
hiện bị tước khỏi `argv` **trước** khi lệnh được đọc (`cos.mjs:261`), nên "lệnh mới không
dùng `cosDir`" là **chưa đủ** — phải biết cờ đó đã có mặt rồi chủ động từ chối.
*Dấu hiệu:* `cos.mjs --root /tmp <lệnh mới>` chạy bình thường thay vì báo lỗi.

**4. Ghim action sai SHA thì workflow không phân giải được; ghim theo tag thì mở cửa sau.**
Repo đã public. Tag đổi được; SHA thì không.
*Dấu hiệu:* job đỏ ngay ở bước checkout với lỗi không tìm thấy action.

**5. Sinh lại lockfile có thể kéo theo nâng dependency.** Đúng Risk 4 của `0008`, lặp lại vì
bước 6 chạm cả hai lockfile. `0007` đo mọi proof trên Python 3.14; một lần `uv lock` nhân
tiện nâng gói sẽ làm các con số đó mất nền mà không báo.
*Dấu hiệu:* `git diff uv.lock` chứa dòng `version` của một gói thứ ba.

**6. CI xanh không có nghĩa là proof còn đúng.** `spec.md` C3: runner GitHub chạy bản vá
Python khác máy này. Badge xanh **không** thay `npm test` cục bộ.
*Dấu hiệu:* không có — đó là lý do nó được viết ra đây.

**8. Squash-only đặt sau lần merge đầu tiên thì lần đó thành ngoại lệ vĩnh viễn.** Quy tắc
đến lúc PR đầu tiên đã sẵn sàng merge, nên có đúng một cửa sổ để nó mô tả được toàn bộ lịch
sử `main` thay vì chỉ tương lai. Đóng cửa sổ đó là một merge commit nằm trên `main` mãi, và
C10 sẽ xanh trong khi `git log --merges` kể chuyện khác.
*Dấu hiệu:* `git log --merges main` có một commit sau ngày hôm nay.

**9. Hai context của `required_status_checks` sai tên thì PR không merge được, và triệu
chứng trông như GitHub hỏng.** Chúng là chuỗi tự do trong API; GitHub không đối chiếu với
workflow nào. Một context không bao giờ được report sẽ nằm `pending` vĩnh viễn.
*Dấu hiệu:* `gh pr view --json mergeStateStatus` trả `BLOCKED` trong khi `gh pr checks` xanh
hết.

**7. Cái tôi muốn không phải viết ra.** Unit này đặt một cổng lên `main` trong một repo mà
**bốn unit đã kẹt sau một cổng khác** — `cos.mjs:30` không cho stage `pr` một status nghĩa là
"không áp dụng", nên `0005`–`0008` không với tới `review`. Sau bước 12, repo có **hai** chỗ
quy trình chặn được việc, và mới chỉ một trong hai được cân nhắc. Nếu ruleset hoá ra sai, nó
gỡ được bằng một lệnh; bức tường kia thì chưa ai biết gỡ bằng gì.
*Dấu hiệu:* claim cuối của proof đỏ vì một commit lọt vào `main` không qua PR — nghĩa là cổng
mới hoặc chưa bật, hoặc đã bị đi vòng.

## Proof

```
uv run python scripts/verify_0009.py
```

**Đạt là exit `0`.** `1` là có claim không đạt; `2` là môi trường không trả lời được — không
có `gh`, `git`, `node`, hoặc không có mạng. Cách tách `1` khỏi `2` theo
`scripts/verify_0003.py:8-14`.

Mười ba claim:

| # | Claim |
|---|---|
| C1 | Tám dòng bảng `spec.md` R1 chạy qua lệnh kiểm branch cho đúng tám kết quả. |
| C2 | Bốn ví dụ tag của R2 cho đúng bốn kết quả. |
| C3 | Lệnh kiểm version xanh trên cây hiện tại, **và đỏ** khi một trong bốn chỗ bị sửa lệch — negative control, chạy trên bản sao tạm. |
| C4 | Ba lệnh mô tả bản checkout này **từ chối** `--root`; `unit-branch` thì nhận. |
| C5 | **Năm** chỗ khai version ở bốn file đều đọc `0.1.0` — `package-lock.json` mang hai. |
| C6 | `.claude/harness.md` nêu đủ mười type, cả hai dạng tag, trường `Type:`, và câu nói ruleset không đi theo bản copy. |
| C7 | Hai workflow parse được; `permissions` khai tường minh; **0** action bên thứ ba ghim theo tag. |
| C8 | `0009/intent.md` khai `Type: feat`, lệnh của R13 in ra `feat/branch-and-release-conventions`, và một type ngoài tập mười bị từ chối. |
| C9 | `.claude/skills/write-intent/SKILL.md` template đòi `Type:`. |
| C10 | `gh api repos/baodq97/coscc/rulesets` trả về một ruleset `active` áp cho `main`, mang `pull_request`, `required_linear_history`, và `required_status_checks` ở chế độ `strict`; và repo cho phép **chỉ** squash. |
| C11 | `gh release list` trả **hai dòng**; `v0.1.0-rc.1` là prerelease, `v0.1.0` không; tag `v0.1.0` nằm trên `main`. |
| C12 | Mọi commit vào `main` **từ commit bật ruleset trở đi** đều có một pull request đã merge chứa nó. |
| C13 | `npm test` xanh. |

C11 và C12 cộng lại là outcome của `intent.md`, nguyên văn. C12 lấy mốc là commit bật ruleset
chứ không phải "hôm nay" — `intent.md` đã sửa đúng chỗ đó, vì artifact của chính unit này vào
thẳng `main` trước khi cổng tồn tại.

C8 và C9 là một cặp và phải cùng xanh: một trường không ai được dạy để viết là một trường
không ai viết, nên C8 một mình sẽ xanh trên đúng một unit — cái unit tôi vừa sửa tay.
