# Impl: The convention, its four commands, and the first branch to obey it
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

Mười ba commit trên `feat/branch-and-release-conventions`, squash thành `82d599d` ngày
2026-09-22. **15 file, +1344 −46.** Branch này là bản dùng thử đầu tiên của chính quy ước nó viết ra.

## What was built

**Bằng chứng, viết trước và đỏ 12/13** (`90d4bb5`). `scripts/verify_0009.py`, 522 dòng, 13
claim, exit `0`/`1`/`2`. Claim duy nhất xanh ở lần chạy đầu là `npm test` — mười hai claim
còn lại đỏ vì mỗi lệnh nó gọi chưa tồn tại. Ba claim là negative control: C1 và C2 mang
những dòng **phải bị từ chối**, C3 làm lệch từng chỗ khai version một và đòi lệnh đỏ mỗi lần.

**Bốn ngữ pháp như hàm thuần** (`85291cc`). `branchProblem`, `tagProblem`, `versionProblem`,
`unitBranch` trong `.claude/scripts/cos.mjs` — nhận chuỗi, trả chuỗi, không đọc file và không
gọi git. Ba điều được quyết ở đây chứ không ở CLI:

- Chúng trả **quy tắc bị phạm**, không trả boolean. Một quy ước mà mọi lần từ chối đều nói
  một câu thì không nói được cái tên bạn vừa chọn sai ở đâu.
- `versionProblem` **nêu nguồn** trong câu báo lỗi. "Hai bên lệch nhau" chưa dùng được cho
  tới khi có chỗ nói bên nào đúng — `spec.md` R11 chọn `pyproject.toml`, và câu báo lỗi nói
  lại lựa chọn đó mỗi lần.
- `unitBranch` suy tên branch từ thư mục unit và `Type:`. Slug đi ra từ `UNIT_RE`, vốn đã là
  chữ thường và gạch ngang đơn, nên cách duy nhất suy ra một tên sai là một type sai — và
  type sai bị từ chối tại chỗ.

**Bốn lệnh, và `--root` dừng đúng chỗ** (`80ff053`). `check-branch`, `check-tag`,
`check-version` nói về bản checkout mà script đang nằm trong; `unit-branch` đọc một `.cos/`
và nhận `--root` như `status` với `gate`. Rào nằm ở **dispatcher** chứ không ở từng lệnh, vì
`--root` bị tước khỏi `argv` trước khi lệnh được đọc: "lệnh này không đụng `cosDir`" là mệnh
đề yếu hơn "cờ đó đã bị từ chối", và chỉ mệnh đề thứ hai ngăn được app trỏ một câu hỏi về git
vào bản checkout của repo khác.

`check-tag` in `prerelease` hoặc `release`, để workflow đọc ngữ pháp thay vì tự so chuỗi.
Node không mang theo trình đọc TOML và repo này không thêm dependency, nên `pyproject.toml`
và `uv.lock` được đọc tay — đọc hẹp: `uv.lock` có một dòng `version` cho **mỗi** gói nó khoá,
nên một regex quét cả file trả về con số của gói nào xếp trước.

`.claude/skills/write-intent/SKILL.md` hỏi `Type:` và trỏ sang `unit-branch` để lấy tên. Không
sửa chỗ này thì lệnh đọc type luôn trả rỗng — `spec.md` C8.

**Harness mang quy ước, và nói thứ không đi theo** (`38e662b`). Mục mới 100 dòng trong
`.claude/harness.md` (180 → 268 dòng): ngữ pháp branch, ngữ pháp tag, nguồn version và năm
chỗ, lệnh nào nhận `--root`, và một bảng ba dòng về việc mỗi nơi kiểm **không** làm được gì.
Nó mở đầu bằng câu chân cưỡng chế duy nhất — ruleset — là một cài đặt của repo và không đi
theo bản copy nào.

**Version lên `0.1.0`, năm chỗ** (`6ab1b4d`). Hai chỗ sửa tay, ba chỗ sinh lại. Diff của hai
lockfile chạm đúng ba dòng và **không dòng `version` nào của gói thứ ba**.

**Hai workflow** (`3c09ce7`). `pr.yml` kiểm tên branch nguồn và chạy `npm test`;
`release.yml` dựng release từ tag đã đẩy. `permissions` khai tường minh và hẹp nhất còn chạy
được — `contents: read` cho PR, `contents: write` chỉ cho release. Ba action bên thứ ba ghim
theo **commit SHA** kèm comment ghi tag lúc ghim: `actions/checkout` v7,
`actions/setup-node` v6, `astral-sh/setup-uv` v7. `gh` có sẵn trên runner nên việc tạo
release không cần action nào.

## Where the plan was departed from

1. **Lệnh thứ tư.** `plan.md` bước 4 khai ba lệnh; ngữ pháp tag không có lệnh nào. Hệ quả:
   `release.yml` phải tự quyết prerelease bằng một phép so chuỗi của riêng nó, và ngữ pháp
   tag có **hai** bản cài đặt trôi độc lập — đúng thứ `versionProblem` tồn tại để chặn, ở một
   file khác. `spec.md` R2 và `plan.md` bước 4, 7 sửa cùng commit với proof (`90d4bb5`).
2. **`check-branch` cắt branch trước, không viết proof trước.** Người khởi xướng hỏi *"cần
   checkout trước nhỉ?"*. Proof là một **bước**, không phải artifact của vòng lặp, nên không
   có gì miễn trừ nó khỏi cổng. Sửa ở `e2c18e9`, trước dòng code đầu tiên.
3. **Work unit khai type** — `intent.md` constraint 9, `spec.md` R13, thêm theo lời người
   khởi xướng ở `e2c18e9`. Kéo theo một file skill và hai claim (C8, C9) mà `plan.md` bản đầu
   không có.
4. **C7 không parse YAML.** `plan.md` bước 7 bản đầu viết "parse được bằng một trình đọc
   YAML". Không có: pyyaml chưa cài và `spec.md` tiêu chí 3 cấm thêm dependency. C7 vì thế
   kiểm **cấu trúc** — `permissions:` có mặt, không có `pull_request_target:`, mọi `uses:`
   ghim theo SHA 40 hex. Một file YAML hỏng lọt qua C7 và chỉ lộ ở bước 9. Đó là Risk 1, và
   nó được viết ra thay vì vá bằng một gói mới.
5. **Proof tự sai bốn lần, và cả bốn đều là cùng một loại lỗi**: nó nhắc đến thứ nó đang đo.
   - C5 dùng **một** regex cho cả bốn file, nên đọc `version` của một dependency trong
     `uv.lock` và gọi đó là số của dự án; và trượt dạng JSON, nơi khoá là `"version":`. Nay
     mỗi file đọc theo đúng định dạng của nó, và có **năm** con số ở bốn file chứ không bốn.
   - C3 làm lệch bằng cách thay chuỗi `0.1.0` vào các file đang mang `0.0.1` — không thay gì
     cả, nên mọi bản "đã lệch" trùng khít bản sạch và **negative control đạt bằng cách không
     làm gì**. Nay nó làm lệch giá trị thật mà cây đang khai.
   - C6 đòi mỗi type có backtick, trong khi harness viết chúng trong code fence; rồi khi
     chuyển sang tìm theo mục, nó bắt trúng **cross-reference** tới tiêu đề nằm cách tiêu đề
     thật 77 dòng và đo nhầm đoạn văn. Nay neo vào đầu dòng và so nguyên từ.
   - C7 báo `pr.yml` dùng `pull_request_target` — vì file ấy có một comment giải thích **vì
     sao nó không dùng**. Nay bỏ comment trước khi đo, và tìm khoá chứ không tìm chuỗi.
6. **Trích dẫn số dòng lệch một.** Thêm một dòng `import` vào `cos.mjs` đẩy mọi trích dẫn
   xuống một. Hai chỗ trong `.claude/` sửa theo: `cos.mjs:24-33` → `:25-34`, `:122-135` →
   `:123-136`. Trích dẫn cùng loại nằm trong `.cos/` của các unit khác **giữ nguyên** —
   `intent.md` constraint 7.
7. **Hai câu trong `harness.md` hết đúng và được sửa, không để lại.** *"The author works
   alone and commits to `main`"* nay nói công việc đi qua branch và pull request, và rằng
   điều đó đổi **đường đi chứ không đổi người duyệt**. *"CI integration"* đang nằm trong
   `## What is deliberately not built` — nay mục đó nói hai workflow là gì và rằng **không
   cái nào là cổng**. Dòng cũ còn trỏ tới "the playbook", tài liệu `0008` đã gỡ khỏi repo.
8. **Hai file rỗng tên `25-34` và `123-136` lọt vào `80ff053`** và bị gỡ ở commit sau. Thông
   điệp commit truyền bằng `-m` có một dấu nháy đơn, nháy đóng sớm, bash chạy phần còn lại
   như lệnh, và `->` là một dấu gạch cộng một redirect. `git add -A` quét cả hai vào. Đúng
   hình dạng departure 7 của `0008`.

9. **Squash-only, nêu ra lúc pull request đầu tiên sắp merge.** Người khởi xướng: *"thêm 1
   rule chỉ squash merge"* — đúng lúc tôi gọi `gh pr merge --merge`. `intent.md` constraint
   10, `spec.md` R14 và C9, `plan.md` bước 11–12 và Risk 8. Thời điểm là thứ đáng ghi: có
   **đúng một** cửa sổ để quy tắc mô tả được toàn bộ lịch sử `main` thay vì chỉ tương lai, và
   một merge commit nằm xuống là đóng nó vĩnh viễn. Cưỡng chế ở hai chỗ vì chúng chặn hai thứ
   khác nhau — setting bỏ hai cái nút, ruleset `required_linear_history` từ chối merge commit
   kể cả khi nút được bật lại.

10. **Branch phải đứng trên `main` mới nhất, cập nhật bằng rebase.** Người khởi xướng: *"à
    yêu cầu rebase update latest so với main trước nữa"*. `intent.md` constraint 11,
    `spec.md` R15, `plan.md` bước 11–12 và Risk 9. Cơ chế là `required_status_checks` ở chế
    độ **`strict`** — không có `strict` thì rule chỉ đòi check xanh ở đâu đó, có `strict`
    thì check phải xanh trên một branch đã có `main` mới nhất bên dưới. Không có nó, hai
    thay đổi độc lập cùng pass rồi hỏng khi đứng cạnh nhau, và cả hai đều merge được.

    **Điều này đảo bước 11 với bước 12 của `plan.md`.** Ruleset phải bật **trước** lần merge
    đầu tiên: một cổng bật sau khi pull request đầu tiên đã đi qua là một cổng mà pull
    request đó chưa từng đi qua, và C12 sẽ đo từ một mốc nằm sau chính thứ nó định đo.

11. **Ruleset mang năm rule, không phải ba.** `plan.md` bước 12 kể ba. Hai cái thêm vào lúc
    viết payload: `deletion` và `non_fast_forward` — không có chúng thì `main` vẫn xoá được
    và vẫn force-push được, tức là cổng chỉ chặn đúng lối đi thẳng mà để ngỏ hai lối vòng.
    Rule `pull_request` còn mang `allowed_merge_methods: ["squash"]`, nên squash-only có
    **ba** chân chứ không hai: setting của repo, `required_linear_history`, và chính rule
    này. `required_approving_review_count` là **0** — repo này không có người thứ hai để
    duyệt, và đòi một chữ ký không có ai ký là tự khoá mình ra ngoài.

12. **C12 báo xanh trên một câu hỏi chưa bao giờ được hỏi.** `review.md` finding 1: mốc
    thời gian của ruleset mang offset `+07:00`, dấu `+` trong query string giải mã thành dấu
    cách, GitHub nhận mốc hỏng và trả rỗng, `all([])` là `True`. Claim in ra *"all **0**
    commits"* và PASS, ngay sau lần merge đầu tiên. Đây là **lần thứ năm** trong unit này
    proof tự sai, và là lần thứ **hai** nó sai theo hướng đạt chứ không theo hướng đỏ. Sửa
    trên `fix/proof-0009-vacuous-claim`.
13. **Phần miễn trừ trong `intent.md` không cần dùng tới.** Ba artifact của unit này được
    commit vào `main` **cục bộ** và `main` chưa bao giờ được push, nên chúng nằm trong pull
    request và đi qua cùng một cổng. `origin/main` đi từ `89e7ed1` thẳng tới `82d599d`. Kết
    quả mạnh hơn dự định nhưng do tai nạn thứ tự push; đính chính ở `intent.md`.
14. **Branch đóng unit đổi tên** từ `docs/close-0009` sang `fix/proof-0009-vacuous-claim`,
    vì nó mang một bản vá thật chứ không chỉ artifact. `plan.md` bước 16 sửa theo.

## What was measured

Tất cả ngày 2026-09-22, trên branch.

| Lệnh | Kết quả |
|---|---|
| `uv run python scripts/verify_0009.py`, lần đầu | exit 1, **đỏ 12/13** |
| — sau bước 7 | exit 1, **xanh 10/13** |
| `npm test` | **55** node + **256** python, xanh |
| — node, trước unit này | 23 |
| `node cos.mjs check-branch <8 tên của R1>` | 8/8 đúng kỳ vọng: 2 nhận, 6 từ chối |
| `node cos.mjs check-tag <8 tên của R2>` | 8/8 đúng kỳ vọng |
| `node cos.mjs check-version` | `0.1.0` |
| `node cos.mjs unit-branch 0009_...` | `feat/branch-and-release-conventions` |
| `node cos.mjs --root /tmp <3 lệnh git>` | exit **2** cả ba; không `--root` thì cả ba khác 2 |
| `node cos.mjs --root . unit-branch 0009_...` | exit 0 — `--root` vẫn tới lệnh đọc `.cos/` |
| `git diff uv.lock package-lock.json` | **3 dòng**, không dòng `version` nào của gói thứ ba |
| `git diff --stat main...HEAD` | **14 file, +1194 −46** |
| `.claude/scripts/cos.mjs` | 275 → **517** dòng |
| `.claude/harness.md` | 180 → **268** dòng |
| Action bên thứ ba ghim theo tag | **0** — ba pin, cả ba là SHA 40 hex |
| `gh pr checks 1` | `branch-name` pass 9s, `tests` pass 25s |
| — log của job `tests` trên runner | **55** node + **256** python, chạy thật |
| `gh api repos/baodq97/coscc`, trước bước 11 | cả ba cách merge `true`; `delete_branch_on_merge` `false` |
| — sau | `allow_squash_merge: true`, hai cái kia `false`, `delete_branch_on_merge: true` |
| `gh api .../rulesets` trước | `[]` |
| — sau | một ruleset `active` id **23815312**, năm rule |
| `git push origin HEAD:main` | **rejected**, `GH013: Repository rule violations` — negative control, chạy thật |
| `gh pr merge 1 --squash --delete-branch` | `MERGED`, 13 commit → **`82d599d`**, branch xoá |
| `git log --merges --since=2026-09-22 main` | **0** — không merge commit nào |
| `git log -1 --format=%B 82d599d \| wc -l` | **235** dòng — cả 13 thông điệp commit còn nguyên |
| `git diff <tip branch> origin/main` | **rỗng** — squash giữ đúng từng byte |
| `gh release list` | **2 dòng**: `v0.1.0` Latest, `v0.1.0-rc.1` Pre-release |
| `uv run python scripts/verify_0009.py`, cuối | **exit 0, 13/13** |

**Ba claim còn đỏ, và không claim nào trong đó là code.** C10 ruleset cộng squash-only, C11
hai release, C12 mọi commit vào `main` qua PR. Cả ba cần bước 11–14: merge, bật ruleset, đẩy
hai tag. Chúng đỏ **đúng** lúc này.

**Hai workflow đã chạy — cập nhật.** Mục này viết trước khi PR mở. `gh pr checks 1` cho cả
hai job xanh, và log in ra 55 test node cùng 256 test python **trên runner**, nghĩa là cả hai
file YAML parse được. Risk 1 của `plan.md` gỡ. `release.yml` thì vẫn chưa chạy lần nào.

**Câu viết trước khi có bằng chứng, giữ lại để đọc được cả hai:** C7 kiểm cấu trúc, không kiểm cú pháp. Một file YAML hỏng
lọt qua và chỉ lộ ở bước 9, nơi GitHub là trình parse và triệu chứng là `gh pr checks` rỗng.
Không tuyên bố gì hơn thế cho tới khi PR mở ra.

**Không proof cũ nào chạy lại.** `verify_0001`–`verify_0008` không được động tới. Unit này
không chạm `coscc/`, nhưng nó bump version ở `pyproject.toml`, và không gì ở đây đo rằng bảy
proof kia vẫn xanh sau cú bump đó.

## What is still open

1. **Ba claim của proof phụ thuộc vào GitHub**, nên `verify_0009.py` không bao giờ xanh được
   trên một bản clone không có mạng hoặc không có `gh` — nó trả exit 2, đúng thiết kế, nhưng
   nghĩa là bằng chứng của unit này yếu hơn `verify_0008.py` ở chỗ đó.
2. **CI xanh không đo trên nền của các proof.** `spec.md` C3: runner mang bản vá Python khác
   máy này. Badge xanh không thay `npm test` cục bộ.
3. **Bốn unit kẹt ở `pr`** (`0005`–`0008`) vẫn kẹt. `spec.md` `## Out of scope`, theo lời
   người khởi xướng. Sau bước 12 repo có **hai** chỗ quy trình chặn được việc, và mới một
   trong hai từng được cân nhắc — `plan.md` Risk 7.
4. **Tập mười type là tập đóng** và sẽ chặn một công việc nào đó không rơi vào nó; lối thoát
   duy nhất là sửa harness. `spec.md` C7.
5. **`Type:` không backfill cho tám unit cũ**, nên `unit-branch` trả lỗi cho tất cả chúng.
   Đó là lựa chọn, `spec.md` R13, không phải thiếu sót.
6. **Ba SHA ghim sẽ cũ đi** và không gì ở đây nhắc nâng. Đổi lấy: một tag đổi được, một SHA
   thì không.
