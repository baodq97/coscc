# Impl: Renamed to coscc, and published
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

Mười sáu commit, `fa7d47c..HEAD`, ngày 2026-09-22. **57 file, +1648 −340.**

## What was built

**Bằng chứng, viết trước và đỏ** (`b19ee82`). `scripts/verify_0008.py`, 14 claim, exit
`0`/`1`/`2`. Chạy lần đầu: **đỏ 11/14**. Hai thứ về file này là bắt buộc chứ không phải phong
cách: nó **không bao giờ viết ra tên cũ** — kim ghép lúc chạy — vì nó nằm ngoài `.cos/` và sẽ
tự làm claim C1 đỏ vĩnh viễn; và nó **không import gì từ gói ứng dụng**, vì tên gói cũ chứa
chính cái kim còn tên mới thì chưa tồn tại lúc nó được viết.

**Đường import legacy của `Store` bị xóa** (`f9caae1`). Mười chỗ trong `store.py`, hai lớp
test, và `import json` thành chết theo. `Data.import_once` ở lại vì `Journal` gọi nó; đường
legacy của `Journal` không đụng tới. Hai câu ở `.claude/CLAUDE.md` và `docs/studio.md` viết
lại để chỉ còn nói về `.cos-journal.jsonl` — trước đó chúng nói về cả hai và sau bước này chỉ
còn đúng một nửa.

**Đổi tên, một bước** (`cb8e886`). `cos_baodo/` → `coscc/`, `cos_baodo.py` → `coscc.py`, rồi
thay định danh trên **43 file** ngoài `.cos/` và hai lockfile. Ba chỗ literal được đọc lại
bằng mắt vì chúng không hỏng ồn ào: `_SOURCES` trong `build.py`, đích ASGI `"coscc.coscc:app"`
trong `run.py:61`, và `app_name` trong `rxconfig.py`.

**Cái tên được giải nghĩa** (`1d45808`). `README.md` nói `coscc` là Chief of Staff trên Claude
Code, và nói thẳng **chức năng đó chưa được xây**. Trước unit này, 81 file dùng cái tên và
**0** file nói nó nghĩa là gì. Trang đổi từ "COS Studio" sang "CoS Studio".

**Bảng tra cho artifact cũ** (`cd21b51`). `.cos/RENAMES.md`, ở tầng `.cos/`, không trong unit
dir nào — `cos.mjs:97-98` chỉ liệt kê thư mục nên harness bỏ qua nó. `.claude/harness.md` trỏ
tới đó và **không** viết ra tên cũ, vì nó nằm ngoài `.cos/`.

**Lockfile, bundle, dữ liệu dev** (`801ca37`, `9233f90`). Hai lockfile chỉ đổi dòng tên.
`.web/` dựng lại từ đầu. `~/.cos/cos.db` và `/home/bd/personal-projects/.cos-baodo.json` bị
xóa — người khởi xướng xác nhận là dữ liệu dev.

**History bị viết lại** (`96647cb`, `f3f0a84`). Một lần quét trước khi publish tìm ra
`docs/ai-native-sdlc-playbook.md`: 611 dòng văn bản của Anthropic, giữ nguyên văn, không ghi
nguồn, hot-link bốn ảnh từ CDN của họ, nằm ở **commit đầu tiên**. Cùng với
`.claude/settings.local.json.tmp.*` lọt vào commit do `git add -A`. Cả hai bị gỡ khỏi **toàn
bộ 134 commit** bằng `git filter-repo` qua `uvx`. Bản đọc chuyển sang `.raws/`, và `.raws/`
vào `.gitignore`.

**Publish.** `gh repo create baodq97/coscc --public --source=. --push`.
**https://github.com/baodq97/coscc**, 136 commit, không viết lại nội dung commit nào.

## Where the plan was departed from

1. **`spec.md` R3 đếm thiếu.** Không phải chín chỗ mà **mười**, và có **hai điểm vào** chứ
   không phải một — `store.py:132` trên đường ghi, và khối `:185-187` trên đường **đọc**. Hai
   trích dẫn cũng lệch một dòng. Sửa ở `7c4d0ee`.
2. **Bảng tra không thể nằm ngoài `.cos/`.** Nó buộc phải viết ra tên cũ; `.claude/harness.md`
   nằm ngoài `.cos/`, nên đặt bảng ở đó thì outcome không bao giờ đạt. Tách: bảng ở
   `.cos/RENAMES.md`, con trỏ ở `harness.md`. Sửa ở `a48b04b`.
3. **Thứ tự bị đảo.** Thay hàng loạt `cos-baodo` → `coscc` trước sẽ biến literal
   `.cos-baodo.json` thành `.coscc.json` — đổi tên một file đã tồn tại trên đĩa. Nên bước xóa
   legacy đi **trước** bước đổi tên.
4. **"Bốn chỗ" trong `store_test.py` là hai lớp test.** `TheOneShotImportFromJson` (8 test, 67
   dòng) bỏ cả lớp. `NothingWritesTheOldFile` (3 test) đặt tên ba artifact cũ dưới dạng
   literal; đổi tên chúng sẽ là nói rằng những file đó từng tồn tại dưới một cái tên chúng
   chưa bao giờ có, nên cả ba được thay bằng **một** test phát biểu bất biến mà chúng là ba
   mẫu. Ba test cũ truyền `Store(d, d)` nên bất biến đó không phát biểu được cho tới khi tách
   hai root ra.
5. **C2 hỏi sai câu.** Nó pin hai con số của R10 rồi đỏ ở bước 9 — vì chính proof nhắc `COS_`
   sáu lần khi đi kiểm nó, và vì test mới ở mục 4 có một docstring nhắc `COS_DATA_DIR`. Cả
   hai lần tăng đều đúng. C2 nay so **từng file với `BASE`** và chỉ đỏ khi một file **mất**
   thứ nó từng có.
6. **`impl.md` không ghi tổng `baodo` trong `.cos/`.** Người khởi xướng nêu, và đúng: con số
   đó tự sinh ra bởi chính artifact đang đếm nó, và **không phải thứ outcome đo**. Ghi nó như
   một figure còn mời người đọc đi dọn một artifact đã ký.
7. **Tôi commit một file temp** ở bước 6 do `git add -A`. Đã gỡ, và `.claude/settings.local.json`
   hóa ra chỉ được ignore bởi gitignore **toàn cục của máy này** — một bản clone public sẽ
   không có. Hai dòng vào `.gitignore` của repo.
8. **Chạy `verify_0003.py` dù spec để ngoài phạm vi.** Log app in một dòng
   `frontend/backend state mismatch` sau khi dựng lại bundle. `.claude/CLAUDE.md` ghi rằng loại
   lỗi này "no HTTP-level check could see it", nên một tín hiệu thật đáng hơn một ranh giới
   phạm vi. Nó PASS; dòng kia là do request socket.io méo của tôi.
9. **Viết lại history** — đảo `intent.md` constraint 5, sửa ở `96647cb`.
10. **Stub bị bỏ.** Bản đầu thay playbook bằng một stub cùng đường dẫn để 5 trích dẫn còn phân
    giải. Người khởi xướng bỏ cách đó: một stub vẫn là repo tự nhận có tài liệu đó, còn
    `.gitignore` nói thẳng đây là nguồn đọc chứ không phải thứ repo sở hữu.
11. **Một skill không được phụ thuộc `docs/`** — người khởi xướng chỉ ra. Lỗi có sẵn:
    `write-ship` trích dẫn `docs/`, trong khi `.claude/harness.md:168-170` tuyên bố copy
    `.claude/` là đủ. Đã viết lại bằng lời của chính skill.

## What was measured

Tất cả ngày 2026-09-22.

| Lệnh | Kết quả |
|---|---|
| `uv run python scripts/verify_0008.py` | **exit 0, 14/14 claim** |
| — trên bản clone mới từ GitHub | **exit 0**, 136 commit, `docs/` chỉ còn `studio.md` |
| — lần chạy đầu, trước mọi thay đổi | exit 1, **đỏ 11/14** |
| `git grep -in <tên cũ> -- . ':!.cos'` | **0 dòng / 0 file**, từ 193/45 |
| `npm test` | **23** node + **256** python, xanh (từ 266 — bỏ 11 test legacy, thêm 1) |
| `uv run python scripts/verify_0003.py` | **PASS**, cả negative control; chạy hai lần, trước và sau khi dọn dữ liệu |
| `git diff uv.lock package-lock.json` | chỉ **3 dòng tên**, không dòng `version` nào của package thứ ba |
| `uv run coscc-build` | reflex 0.9.12, **6 source file**, bundle cho `http://127.0.0.1:8790` |
| `ss -ltn` khi app chạy | chỉ `127.0.0.1:8790`; **0** socket bind ra ngoài |
| `git log -p --all \| grep -icE '<mẫu bí mật>'` | **0** trong cả 136 commit |
| `git log -p --all \| grep -ic 'website-files.com'` | **0** |
| `gh repo view baodq97/coscc --json visibility` | `public` |

**Hai con số cố ý không ghi.** Tổng `baodo` trong `.cos/` — lý do ở departure 6. Và số commit
"sạch tên cũ": việc viết lại history chỉ bỏ hai **đường dẫn**, không đụng nội dung, nên tên cũ
vẫn tra ra được bằng `git log -p`. Outcome đo trên tree, đúng như `intent.md` đã viết.

**Sáu proof cũ không chạy lại.** `verify_0001`, `0002`, `0004`, `0005`, `0006` chỉ được đảm
bảo là **import được**, không đảm bảo còn xanh. Chúng tốn quota thật và `verify_0005.py` đẩy
một branch lên một repo thật. Đây là lỗ hổng có chủ ý, đã ghi ở `spec.md` `## Out of scope`.

## What is still open

1. **44 trích dẫn SHA trong `.cos/` đã chết**, cộng **3 trích dẫn** tới
   `docs/ai-native-sdlc-playbook.md` trong `0005`. Không vá tại chỗ: constraint 4 cấm, và
   `spec.md` C5 đo được rằng vá sẽ hỏng thêm 19 trích dẫn số dòng đang đúng.
   `.cos/RENAMES.md` ghi một lần cho tất cả.
2. **23 dòng `/home/bd` trong 6 file `.cos/`** nay công khai. `intent.md` OQ1 để mặc định là
   giữ nguyên và không ai quyết khác. Là đường dẫn máy cá nhân, không phải bí mật.
3. **URL vẫn chứa tên một người** — `github.com/baodq97/coscc`. `spec.md` C2; người khởi
   xướng chốt chấp nhận.
4. **`coscc` không dễ đọc hơn `cos-baodo`.** Chỉ `README.md` gánh nửa đó của vấn đề, và không
   test nào kiểm được chất lượng một câu prose. `spec.md` C3.
5. **Chief of Staff vẫn chưa có artifact nào giữ nó.** Tên repo trỏ tới một đích đến mà repo
   không mô tả ở đâu ngoài một đoạn README. `intent.md` OQ2.
6. **Thư mục trên đĩa vẫn tên `cos-baodo`**, nên app tự thấy một workspace tên `cos-baodo` qua
   `source: "env"`. Ngoài git, `intent.md` OQ3.
7. **Bất đối xứng `Store`/`Journal`** — `spec.md` C6, đã nói ra ở `.claude/CLAUDE.md`.
8. **Bản history trước khi viết lại** chỉ còn trong một git bundle trên máy tác giả, ngoài
   repo. Không ai khác tra được.
