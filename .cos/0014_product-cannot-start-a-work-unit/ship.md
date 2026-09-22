# Ship: the product can start a work unit, and finish one
Review: review.md. Author: Bao Do. Status: accepted.

## What went out

**`v0.4.0`**, https://github.com/baodq97/coscc/releases/tag/v0.4.0, phát hành
2026-09-22T17:17:44Z. Asset: `coscc-0.4.0-py3-none-any.whl`, `install.sh`, `SHA256SUMS`.

Hai pull request đã merge vào `main`:

| PR | Merge commit | Gì |
|---|---|---|
| [#18](https://github.com/baodq97/coscc/pull/18) | `ea3f6a8` | Toàn bộ `0014`: 27 file, +2413 −84 |
| [#19](https://github.com/baodq97/coscc/pull/19) | `c535612` | Phiên bản 0.4.0 ở năm chỗ khai báo |

**Bản cài trên máy này đã nâng cấp**, bằng đúng một dòng `docs/install.md` `## Update` ghi:
`curl -LsSf .../install.sh | sh`. Service `coscc.service` chạy lại và trả lời
`coscc 0.4.0 installed and running` trên `127.0.0.1:8790`.

Điều **không** đi ra: không có finding nào của `review.md` được sửa trong bản này. Cả bốn
vẫn mở, và mục 1 (nhánh cắt từ `main` cũ) là cái đáng làm trước ở unit sau.

## Did the outcome hold

`intent.md` `## Proposed outcome` viết: *"Trước 2026-10-20: một work unit đi từ **chưa tồn
tại** tới **pull request đã merge**, làm **chỉ bằng HTTP API của sản phẩm**... Tính theo
bảng sáu bước: hôm nay **1/6**, đích là **6/6**."*

**Đạt, đo 2026-09-22, sớm 28 ngày.** `scripts/verify_0014.py` exit 0, tám claim `PASS`,
**6 of 6**, kết thúc ở https://github.com/baodq97/coscc-proof/pull/1 — `MERGED` lúc
`17:01:08Z`, merge commit `a2cfb0d`. Năm session, **$3.2836**, **11m49s**. Số đo đầy đủ
trong `impl.md` `## What was measured`.

**Một điều kiện gắn liền với con số đó, và `review.md` không phải chỗ giấu nó.** `6/6`
không phải sáu bước `intent.md` viết nguyên văn: bước 5 ở đó là *"chạy các stage, mỗi
artifact một commit"*, và `spec.md` R2 đã bỏ vế sau khi chuyển artifact ra khỏi cây repo.
Proof chấm bước 5 là *"work the stages"*. Định nghĩa đã dịch giữa intent và spec, có chủ
đích, và ai đọc `6/6` cần biết.

**Đo lại trên bản đã cài, không phải trên checkout.** `intent.md` mở đầu bằng một lệnh
`curl` trả `405`. Cùng lệnh đó, cùng máy, trên `0.4.0`:

```
POST /api/units -> 400   {"error":"not a configured workspace: "}
```

`400` chứ không phải `405`: route có thật, và cổng thành viên trả lời. Rồi gọi đủ tham số:

```
POST /api/units -> 200
{"unit":"0001_the-installed-copy-proves-the-route-exists",
 "path":"/home/bd/.cos/units/coscc-fb0599d12eeb/.cos/0001_...","brief":true}
```

`git status --porcelain` trong `/home/bd/coscc-wp/coscc` sau đó: **không một dòng nào** —
R2 giữ được trên bản phát hành, không chỉ trong proof. Unit đó là unit thật, tạo ra để đo,
và xoá được: `rm -rf /home/bd/.cos/units/coscc-fb0599d12eeb/.cos/0001_*`.

## How it is watched

Ba lệnh, theo thứ tự rẻ tới đắt.

```sh
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8790/api/units \
     -H 'content-type: application/json' -d '{"slug":"x"}'
```
`400` là khoẻ. **`405` là band đã vỡ** — route biến mất, tức là bản cài đã tụt lại dưới
`0.4.0`. Không tốn gì.

```sh
COS_URL=http://127.0.0.1:8790 uv run python scripts/verify_0012.py
```
Đo **bản đã cài**, không phải checkout. `exit 0`, năm claim, 2026-09-23. Không session,
không quota. **Hai claim của nó đã mục vì chính `0014`** và được sửa trong `31e120b`: claim
2 chạy gate trên workspace trong khi board đọc store nên đỏ cả 104 dòng, còn claim 3 gọi
`build_prompt` bằng chữ ký cũ và ném `TypeError` thay vì đo gì. Chỉ chạy thật trên bản cài
mới lộ ra — đó là lý do lệnh này nằm ở đây chứ không phải trong CI.

```sh
COS_PROOF_REPO=<một repo dùng một lần> uv run python scripts/verify_0014.py
```
Vòng đầy đủ. **Tốn tiền thật**, đo được $3.2836 một lần chạy, và `.claude/rules/coscc-app.md`
`## Hazards` nói nó không thuộc về một vòng lặp không người trông. Đây là thứ duy nhất đo
được `6/6`; hai lệnh trên chỉ đo bước 1.

## What to do if it breaks

**Lùi bản cài:** cài lại `0.3.0` bằng `install.sh` của release đó —
`curl -LsSf https://github.com/baodq97/coscc/releases/download/v0.3.0/install.sh | sh`.
Không cần đụng `~/.config/coscc/env`; install script để nguyên.

**Lùi mã:** `git revert ea3f6a8` là toàn bộ `0014`. Một chiều duy nhất cần biết trước:
`0013` đã đẩy `cos.db` lên schema 2 và `v0.2.3` trở về trước **từ chối mở** nó — `0014` không
thêm migration nào, nên lùi tới `0.3.0` an toàn, lùi xa hơn thì phải dọn `~/.cos/cos.db`.

**Nếu unit "biến mất" sau khi lùi:** chúng không mất. Từ `0014` chúng nằm dưới
`~/.cos/units/<basename>-<digest>/.cos/`, không nằm trong cây repo, và `units.slot()` dựng
digest từ **đường dẫn đã resolve** của workspace. Di chuyển workspace là tách nó khỏi unit
của chính nó (`coscc/units.py:76-87`, đã ghi sẵn ở đó). Đường về là đưa workspace lại đúng
đường dẫn cũ, không phải đi tìm file.
