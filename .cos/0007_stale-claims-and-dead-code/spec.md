# Spec: Correct what the repository says about itself, and stand it on current versions
Intent: intent.md. Author: Bao Do. Status: accepted.

## Skip assessment

Năm tiêu chí của `.claude/skills/write-spec/SKILL.md`, phán quyết từng cái:

1. **Sửa từ hai file đang có trở xuống — TRƯỢT.** Mười lăm file đang có bị sửa, hai file bị
   xoá, cộng một file nữa nếu open question 1 trả lời là sửa.
2. **Không đổi interface công khai, schema hay dữ liệu đã lưu — TRƯỢT.** `objects_dir` là
   thuộc tính công khai của `Data` (`cos_baodo/data.py:153`), và app thôi tạo
   `~/.cos/objects/`. Một data root đã dùng thì đã có thư mục ấy trên đĩa.
3. **Không thêm dependency — ĐẠT.** Một cái bị bỏ, không cái nào thêm.
4. **Không có hành vi nào vượt `intent.md` — ĐẠT.**
5. **Không chạm auth, PII hay bề mặt an toàn — TRƯỢT.** Mục 11 sửa đúng chỗ đóng các tiến
   trình CLI giữ `CLAUDE_CODE_OAUTH_TOKEN`; `.cos/0001_no-session-management/spec.md:121`
   gọi token đó là thông tin đăng nhập sống lâu nằm trong một tiến trình đang nghe HTTP. Hook
   ấy hỏng thì token sống lâu hơn server.

Ba tiêu chí trượt, nên spec phải viết. Tiêu chí buộc nó là 1, 2 và 5 — và trong ba cái thì
cái 5 là cái không được đổi lấy tốc độ.

Người khởi xướng chọn "unit mới qua loop" ngày 2026-09-22 với mô tả là một spec `skipped`.
Phép đánh giá không cho phép, nên spec này được viết thay vì bỏ. Ghi ra để lần chọn của họ
không bị đọc thành lần này đã skip.

## Requirements

Mười sáu yêu cầu. R1–R11 đóng mười ba mục của `intent.md`; R12–R15 là phần người khởi xướng
thêm ngày 2026-09-22 — "fix luôn phần deprecation nhé, đảm bảo mọi thứ là latest. và đúng
theo phiên bản và stacks mới nhất"; R16 là điều kiện chạy suốt.

**R1.** Phép đo của `0004` chỉ còn một chiều. `.cos/0004_silent-concurrent-loss/plan.md:115`
là bản gốc — **8 trên 20 mục còn lại**, tức 12 mất. Mọi chỗ khác nêu con số này đọc cùng
chiều đó và dẫn về dòng ấy. Kiểm: một lệnh in ra mọi chỗ nêu `20`, và không chỗ nào nói
"mất 8".

**R2.** Ba citation ở `intent.md` mục 3, 4, 5 trỏ tới dòng đỡ được câu chúng đỡ. Kiểm: một
lệnh in `path:line` cùng nội dung dòng, cho cả ba.

**R3.** `README.md` tả vòng lặp tám stage. Kiểm: đọc, và số stage khớp `STAGES` ở
`.claude/scripts/cos.mjs:24-33`.

**R4.** Không tên test nào trong `.claude/scripts/cos.test.mjs` mang một số đếm unit. Kiểm:
`grep -c "eight units"` bằng 0, và `npm run test:node` xanh với 23 test.

**R5.** Ba bề rộng của R24 chỉ còn một nguồn: `scripts/verify_0003.py:180`. `cos_baodo/ui.py`
không còn hằng số nào không ai đọc, và không còn câu nào nói một hằng số như vậy đang được
dùng. Kiểm: `grep -c BREAKPOINTS cos_baodo/` bằng 0.

**R6.** Không module nào trong `cos_baodo/` chỉ được import bởi test của chính nó. Kiểm: một
lệnh in danh sách, danh sách rỗng.

**R7.** `Data` không còn `objects_dir`, và không đường nào của app tạo `objects/` nữa. App
**không xoá** gì dưới data root: một `~/.cos/objects/` đã có thì bị bỏ lại nguyên vẹn. Kiểm:
`Data(tmp).ensure_dir()` rồi liệt kê `tmp` — không có `objects`; và không lệnh xoá nào trong
`cos_baodo/data.py` trỏ vào data root.

**R8.** `package.json` không khai dependency nào không được import. Kiểm: một lệnh đối chiếu
mọi key trong `dependencies` với mọi `import`/`require` trong `.claude/scripts/`.

**R9.** `npm test` in **0** dòng chứa `Warning`. Hôm nay là **70** — 68 `DeprecationWarning`
và 2 `ResourceWarning`. Nguồn: đo 2026-09-22; cả 70 dòng đều từ code của repo này, 68 từ
`cos_baodo/api.py:252` và 2 từ `cos_baodo/store_test.py:263`.

**R10.** Hook đóng session lúc tiến trình dừng có một phép kiểm chạy được trong `npm test`:
không cần cổng trống, không cần trình duyệt, không tạo session thật. Hôm nay không có phép
kiểm nào chạy qua nó — `scripts/verify_0001.py:153` ghi rằng `ASGITransport` không chạy
lifespan.

**R11.** `.gitignore` không có entry nào trùng nghĩa. Kiểm: một lệnh đếm, kết quả 0.

**R12.** Không API nào đã bị deprecate còn được gọi từ code của repo này. Kiểm: R9 đạt,
**và** `uv run cos-build` in 0 dòng chứa `Deprecat`.

**R13.** Mọi dependency khai ở `pyproject.toml` có floor bằng version đang cài, và version
đang cài là bản stable mới nhất. Hôm nay có một floor lệch hẳn: `claude-agent-sdk>=0.1.0`
trong khi bản đang cài là `0.2.157` — hai dòng phiên bản khác nhau, nên `uv sync` trên máy
mới có quyền cài một bản mà code này không chạy được. Kiểm: `uv pip list --outdated` in danh
sách rỗng, hoặc mỗi dòng còn lại được nêu tên trong `impl.md` cùng ràng buộc chặn nó; và
`npm outdated` rỗng.

**R14.** Python là **3.14**, bản stable mới nhất. 3.15.0rc2 có sẵn nhưng là release
candidate, nên không tính. Reflex hỗ trợ 3.10–3.14 — nguồn: classifier của `reflex`
`0.9.11.post1` đang cài, đọc 2026-09-22. Kiểm: `.python-version` và `.venv/bin/python -V`.

**R15.** Sau khi nâng version: `npm test` xanh, `uv run cos-build` chạy xong, và
`uv run python scripts/verify_0003.py` thoát **0**. Ba cái này là điều kiện để nói bản nâng
cấp còn chạy được; hai cái đầu không đủ vì trang chỉ hiện ra trong trình duyệt.

**R16.** `npm test` xanh sau **mỗi** commit của unit này, không chỉ ở commit cuối.

## Design

Năm hình dạng, và mỗi cái trả lời một nhóm yêu cầu.

**Một con số, một nguồn.** Hôm nay phép đo của `0004` bị chép lại bằng chữ ở ba chỗ và một
chỗ chép sai chiều. Hình dạng đúng là: bản gốc nằm ở đúng một nơi — artifact của unit đã đo
nó — và mọi chỗ khác **dẫn** về đó thay vì thuật lại con số. Chỗ nào buộc phải nhắc con số
thì nhắc kèm citation, để lần sau ai đọc còn có đường kiểm. (R1, R2)

**Không dựng máy dò citation.** `intent.md` open question 2 nói rõ đây là một unit riêng.
Ba citation trôi được sửa tại chỗ, và không có gì trong unit này ngăn cái thứ tư trôi. Đổi
lấy: unit này nhỏ và đóng được trong tuần, thay vì mở một hạng mục mới.

**Tầng object store biến mất cùng mọi dấu của nó, nhưng không chạm đĩa của người dùng.**
Ranh giới: app thôi **tạo**, app không **dọn**. `Data` thôi biết tới khái niệm `objects`;
một data root đã có thư mục ấy thì nó nằm đó như rác vô hại. Đây cùng một lựa chọn mà
`0002` C6 đã chốt cho workspace bị xoá — chọn giữa rác và mất việc, và lần này cũng chọn
rác. (R6, R7)

**Shutdown đi từ hook sang lifespan của app ngoài cùng.** Đo 2026-09-22:
`reflex/app.py:815` mount ASGI app của Reflex **vào trong** app FastAPI, nên app FastAPI là
app ngoài cùng và lifespan của nó là cái uvicorn chạy. Phép kiểm mới drive lifespan ấy trực
tiếp trong tiến trình test — không cổng, không trình duyệt, không session thật — nên nó nằm
được trong `npm test` thay vì trong một lệnh tay không ai chạy. Ranh giới không đổi: chỗ duy
nhất đóng session vẫn là một chỗ, chỉ khác cách nó được nối vào vòng đời tiến trình.
(R10, R12)

**Nâng version là một bước có phép kiểm riêng, không phải một dòng trong commit khác.**
`0.9.12` có bốn breaking change và cả bốn ở vùng State/router: năm base var mới, lỗi khi
shadow var kế thừa, luật tên dành riêng, và `state.dict()` bỏ key `router`. Đo 2026-09-22:
`cos_baodo/` không dùng `router`, không gọi `.dict()`, không dùng `deps=` — nên ba trong bốn
không chạm tới đây. Cái còn lại, luật tên dành riêng, chỉ lộ ra lúc chạy và repo có 112
state var, nên nó phải được đo chứ không suy luận. Đó là lý do R15 đòi cả `cos-build` và
`verify_0003.py`, không chỉ `npm test`. (R13, R14, R15)

## Out of scope

- **`setting_sources` và các MCP tool đi vòng qua "chat only".** Người khởi xướng nói nó sẽ
  đổi. Đây là món lớn nhất còn mở trong repo và nó không được đóng ở đây.
- **Chạy lại `verify_0001.py`, `verify_0002.py`, `verify_0005.py`.** Quyết định ngày
  2026-09-22. Xem C2 về việc R14 làm khoảng trống này rộng ra chứ không hẹp đi.
- **Tạo remote.** Nên `0005 pr.md` còn `draft`, và `pr`/`review`/`ship` vẫn không đạt được
  cho bất kỳ unit nào.
- **Tách `screens.py` (1175 dòng) và `state.py` (1024 dòng).** Refactor, không phải clean-up.
- **Một phép kiểm tự động cho citation.** `intent.md` open question 2; unit riêng.
- **Python 3.15.** Còn là release candidate ngày 2026-09-22.
- **Nâng version của trình duyệt trong `verify_0003.py`/`verify_0006.py`.** `playwright`
  `1.63.0` đã là bản mới nhất; bản Chromium nó tải về không nằm trong `pyproject.toml` và
  không có ai ghim, nên nó không phải một version repo này khai.

## Concerns

**C1 — Sửa một figure trong `spec.md` đã `accepted` là sửa dấu vết audit.**
`.claude/harness.md` viết rằng chuỗi commit là dấu vết của những gì được yêu cầu và những gì
agent làm ra. `.cos/0006_demo-data-and-no-durable-store/spec.md:144` nêu con số sai chiều.
Hai luật đánh nhau: giữ artifact bất biến, và `.claude/CLAUDE.md` đòi cắt một figure không
có nguồn. Spec này **không** tự chọn. **Người quyết là tác giả**, ở `plan.md`. Hai lối, cả
hai đều phải để lại dấu: sửa con số tại chỗ và ghi lần sửa vào `plan.md` của unit này, hoặc
để nguyên và thêm ở đây một dòng nói nó sai — cái sau giữ artifact nguyên nhưng để con số sai
tiếp tục được trích.

**C2 — R14 làm mọi phép đo ghi ngày 2026-09-21 và 2026-09-22 thành phép đo trên một
interpreter khác.** `.claude/CLAUDE.md` chứa ít nhất bốn con số được đo trên Python 3.12:
thứ tự WAL/`busy_timeout` hỏng một lần trên mười, 20 trên 20 entry của `verify_0004.py`, cổng
bị nhúng vào bản build, và `*:3000` của dev mode. Cái chịu ảnh hưởng thật nhất là
`verify_0004.py`, vì nó đo hành vi khoá của SQLite và module `sqlite3` đi theo interpreter.
Nó tốn một session thật. Người khởi xướng đã để ba proof khác ngoài scope; `verify_0004.py`
**không** nằm trong ba cái đó. **Người quyết là tác giả**: chạy lại nó sau khi nâng, hay ghi
rằng nó chưa được chạy lại trên 3.14.

**C3 — R15 treo vào một lệnh tay.** `npm test` không mở trình duyệt, cố ý
(`.claude/CLAUDE.md`). Nên nếu không ai chạy `verify_0003.py` sau khi nâng framework, trang
là thứ duy nhất trong repo đi qua một lần đổi version mà không được chứng minh. Đây đúng loại
bằng chứng mà `0003` sinh ra để chống, và unit này không dựng thêm gì để ép nó chạy.

**C4 — "0 warning" là một ngưỡng mong manh theo version.** Hôm nay nó đạt được vì cả 70 dòng
đều từ code của repo này. Một bản `reflex` hoặc `fastapi` sau này in deprecation của riêng nó
sẽ làm R9 đỏ vì một lý do không ai trong repo sửa được, và lúc đó ngưỡng phải đổi thành "0
dòng từ file của repo này" chứ không phải bị bỏ. Ghi trước để lần ấy không ai lặng lẽ hạ
ngưỡng.

**C5 — Xoá `objects.py` bỏ luôn tầng mà `0006` R3 và R11 đòi.** Nó được xây theo một yêu cầu
đã accepted, và giờ bị bỏ vì chưa ai nối vào. Nếu sau này cần lưu blob thì phải dựng lại từ
git. Người khởi xướng chọn lối này ngày 2026-09-22, biết cả hai lối.

**C6 — Mười sáu yêu cầu trong một unit là rộng, và `intent.md` chỉ có một mệnh đề.** Rộng ở
đây được chấp nhận vì mọi mục đều là cùng một loại việc — sửa một câu nói sai hoặc bỏ một thứ
không ai dùng — và vì không mục nào đổi hành vi trang. Nhưng nó có nghĩa là một mục hỏng sẽ
làm cả unit không đóng được, và `plan.md` phải chia commit sao cho mục hỏng không chặn mười
lăm mục kia.

## Open questions

1. **Còn mở, và là C1:** sửa figure trong artifact đã accepted của `0006`, hay để nguyên kèm
   một dòng ghi chú? Tác giả quyết ở `plan.md`.
2. **Còn mở** (`intent.md` OQ2): không lệnh nào đếm lại được mười ba mục, nên mệnh đề kiểm
   được từng mục mà không kiểm được rằng không còn mục thứ mười bốn. Một phép kiểm cho
   citation là unit riêng. Trả lời nó sẽ đổi việc: nếu dựng, unit này lẽ ra nên chờ.
3. **Đã trả lời** (`intent.md` OQ3): lifespan sẽ chạy, vì app FastAPI là app ngoài cùng —
   `reflex/app.py:815`. Phần chưa kiểm của câu hỏi cũ, dựng được phép kiểm không cần cổng
   hay quota, là R10 và nó nằm trong scope.
4. **Còn mở, và là C2:** chạy lại `verify_0004.py` trên Python 3.14 hay ghi là chưa chạy?
   Tác giả quyết. Nó tốn một session.
5. **Mới:** sau khi nâng lên `0.9.12`, luật tên dành riêng của Reflex (#7136) có đụng vào
   112 state var của `state.py` không? Không trả lời được bằng cách đọc; nó là một trong ba
   phép kiểm của R15, và nếu đụng thì nó đổi việc từ clean-up thành đổi tên state var.
