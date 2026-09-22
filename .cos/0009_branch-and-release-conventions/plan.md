# Plan: Bootstrap the convention on the branch that introduces it
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

Mười sáu bước. Bước 1 dựng bằng chứng và nó phải **đỏ** trước, như `0008` đã làm. Bước 2 cắt
branch, và **mọi thứ từ đó trở đi đi qua cổng mà unit này dựng lên** — kể cả `impl.md` và
`pr.md` của chính nó. Bước 13 bật ruleset và không dễ lùi.

Đây là lần đầu repo này làm việc trên branch. `intent.md` `## Proposed outcome` miễn trừ
`intent.md`, `spec.md` và `plan.md` — ba file đó vào thẳng `main` vì chúng là thứ tạo ra
quy ước. Từ `impl` trở đi không còn miễn trừ nào.

## Files that change

### Mới

- `scripts/verify_0009.py` **(new)** — bằng chứng của unit này.
- `.github/workflows/pr.yml` **(new)** — chạy trên pull request.
- `.github/workflows/release.yml` **(new)** — chạy khi đẩy tag.
- `.github/` chưa tồn tại; unit này tạo thư mục đó.

### Sửa

- `.claude/scripts/cos.mjs` (275 dòng). Ba chỗ: thêm các hàm thuần được export cạnh
  `parseStatus` (`:50`) … `nextNumber` (`:164`); thêm hai khoá vào bảng `run` (`:265-269`);
  sửa dòng usage (`:271`). Cộng phần từ chối `--root` — hiện `--root` bị tước khỏi `argv`
  trước khi đọc lệnh (`:261`), nên dispatcher phải biết nó **đã** có mặt.
- `.claude/scripts/cos.test.mjs` (175 dòng, **23** test). Nó import hàm thuần trực tiếp từ
  `cos.mjs` (`:6`) chứ không gọi qua subprocess — hàm mới theo đúng hình dạng đó.
- `.claude/harness.md` — một mục mới. Mục cuối hiện là `## Copying this into another
  repository` (`:167`).
- `pyproject.toml:3`, `package.json:3` — `0.0.1` → `0.1.0`.
- `uv.lock:151`, `package-lock.json:3` và `:9` — sinh lại theo.

### Không đổi

`.cos/` — không artifact nào bị sửa (`intent.md` constraint 7). `coscc/` — unit này không
chạm vào ứng dụng.

## Order of work

**1. Dựng `scripts/verify_0009.py`, và nó phải đỏ.**
Khuôn theo `scripts/verify_0008.py`: claim tĩnh, exit `0`/`1`/`2`, dùng `say`, `EXIT_PASS`,
`EXIT_BROKEN`, `EXIT_ENV` từ `scripts/proof_harness.py`. Claim liệt kê ở `## Proof`.
*Kiểm:* chạy nó, exit `1`. Commit lên `main` — đây là file cuối cùng được miễn trừ, vì không
có nó thì không có gì để nói bước sau đã xong.

**2. Cắt branch.**
`git switch -c feat/branch-and-release-conventions`. Tên này thoả đúng ngữ pháp mà chính
branch này sắp định nghĩa — vòng tròn là có thật và là cách duy nhất khởi động.
*Kiểm:* `git rev-parse --abbrev-ref HEAD` trả đúng tên đó.

**3. Hàm thuần cho ba ngữ pháp, cùng test.**
Export từ `cos.mjs`: một hàm nhận tên branch, một nhận tên tag, một so các chuỗi version. Cả
ba **không đọc file, không gọi git** — chúng nhận chuỗi và trả kết quả, đúng hình dạng
`parseStatus` đang có. Test import trực tiếp; tám dòng bảng của `spec.md` R1 và bốn ví dụ tag
của R2 thành tám cộng bốn assertion.
*Kiểm:* `npm run test:node` xanh, số test lớn hơn 23.

**4. Hai lệnh, và rào `--root`.**
Nối hai khoá vào bảng `run`, sửa dòng usage. Lệnh kiểm branch đọc branch đang checkout khi
không có tham số; lệnh kiểm version đọc bốn file. Cả hai **từ chối `--root`** với exit khác
`0` — `spec.md` R6, và lý do ở `coscc/board.py:48,90`: script này được app trỏ vào repo của
người khác, và nó "needs no secret, so it is given none".
*Kiểm:* tám dòng bảng R1 chạy qua CLI cho đúng tám kết quả; `cos.mjs --root /tmp <lệnh mới>`
exit khác 0; `npm test` xanh.

**5. Mục mới trong `.claude/harness.md`.**
Ngữ pháp branch (đủ mười type), ngữ pháp tag, hai lệnh, và **một câu nói thẳng rằng thứ cưỡng
chế mạnh nhất — ruleset — không đi theo bản copy** (`spec.md` C1). Không có câu đó thì người
copy harness nhận một quy ước không ai gác.
*Kiểm:* claim tài liệu của proof chuyển xanh.

**6. Bump version lên `0.1.0`.**
`pyproject.toml:3` và `package.json:3` sửa tay; `uv lock` và `npm install --package-lock-only`
sinh lại hai lockfile.
*Kiểm:* lệnh kiểm version ở bước 4 exit `0`; `git diff` trên hai lockfile chỉ chạm dòng
version — bất kỳ dòng nào của package thứ ba xuất hiện thì hoàn tác (Risk 5).

**7. Hai workflow.**
`pr.yml`: kiểm tên branch nguồn, chạy `npm test`. `release.yml`: dựng release từ tag, đánh
dấu prerelease khi tag có `-rc.N`. Cả hai khai `permissions` tường minh ở mức tối thiểu và
ghim mọi action bên thứ ba **theo commit SHA**, không theo tag (`spec.md` C2).
*Kiểm:* `.github/workflows/*.yml` parse được bằng một trình đọc YAML; claim tương ứng của
proof xanh. Chúng **chưa** chạy thật cho tới bước 9.

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

**11. Merge PR, xoá branch.**
*Kiểm:* `gh pr view --json state` trả `MERGED`; `main` chứa toàn bộ công việc; branch biến
mất khỏi remote.

**12. Bật ruleset trên `main`.** *(Khó lùi — Risk 2.)*
Đòi pull request. Hiện `gh api repos/baodq97/coscc/rulesets` trả `[]`.
*Kiểm:* lệnh đó trả về một ruleset áp cho `main`; và một lần `git push` thẳng lên `main` bị
**từ chối** — negative control, chạy thật.

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
`docs/close-0009` mang `review.md` và `ship.md`, mở PR, merge. Không có lối nào khác: bước 12
đã bật cổng, nên hai artifact cuối cũng phải đi qua nó. Đó là lần đầu quy ước tự áp lên
chính nó mà không phải bootstrap.
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

Mười một claim:

| # | Claim |
|---|---|
| C1 | Tám dòng bảng `spec.md` R1 chạy qua lệnh kiểm branch cho đúng tám kết quả. |
| C2 | Bốn ví dụ tag của R2 cho đúng bốn kết quả. |
| C3 | Lệnh kiểm version xanh trên cây hiện tại, **và đỏ** khi một trong bốn chỗ bị sửa lệch — negative control, chạy trên bản sao tạm. |
| C4 | Cả hai lệnh mới **từ chối** `--root`. |
| C5 | Bốn chỗ khai version đều đọc `0.1.0`. |
| C6 | `.claude/harness.md` nêu đủ mười type, cả hai dạng tag, và câu nói ruleset không đi theo bản copy. |
| C7 | Hai workflow parse được; `permissions` khai tường minh; **0** action bên thứ ba ghim theo tag. |
| C8 | `gh api repos/baodq97/coscc/rulesets` trả về một ruleset áp cho `main` đòi pull request. |
| C9 | `gh release list` trả **hai dòng**; `v0.1.0-rc.1` là prerelease, `v0.1.0` không; tag `v0.1.0` nằm trên `main`. |
| C10 | Mọi commit vào `main` **từ commit bật ruleset trở đi** đều có một pull request đã merge chứa nó. |
| C11 | `npm test` xanh. |

C9 và C10 cộng lại là outcome của `intent.md`, nguyên văn. C10 lấy mốc là commit bật ruleset
chứ không phải "hôm nay" — `intent.md` đã sửa đúng chỗ đó, vì artifact của chính unit này vào
thẳng `main` trước khi cổng tồn tại.
