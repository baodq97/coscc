# Intent: a released version installs nowhere except a developer checkout
Author: Bao Do. Type: build. Status: accepted.

## Problem

Người khởi xướng nói, nguyên văn, ngày 2026-09-22, qua bốn lượt:

> "hiện tại phần gh release chỉ package source only, tôi nghĩ nên có cách nào để giúp khách
> hàng triển khai dễ hàng hơn, bản này phải cài được update được nhỉ?"

> "đáng lẽ là package phải kèm cả frontend nhỉ? có cách nào không?"

> "với phần này bundle thành 1 bản cài như nào. chỉ hỗ trợ linux thôi."

> "thiếu doc cài đặt nhỉ?"

Hỏi lại hai điều và được trả lời: **"khách hàng"** là người trên một **máy Linux lạ, không có
gì sẵn** — chưa clone repo, chưa có `uv`, chưa có Python 3.14, chưa có Node. Và **"run
được"** tính là **trang mở ra và sáu màn hình render**, không kèm Connection Error; không
tính việc gửi được tin nhắn chat, vì phép đo đó phụ thuộc credential của người khác.

Mọi số dưới đây đo ngày 2026-09-22 trên HEAD `5707c5b`.

### Release không mang gì

`gh release view v0.1.0 --json assets --jq '.assets | length'` trả **0**. Hai dòng "Source
code (zip / tar.gz)" hiện trên trang release là link GitHub tự sinh từ tag, không phải asset
ai đó gắn vào. Lý do ở `.github/workflows/release.yml:45-48`: `gh release create` được gọi
với `--generate-notes` và không một cờ đính kèm nào.

### Và cái repo đóng gói được thì lại không chứa frontend

`pyproject.toml:24-25` khai `module-root = ""`, `module-name = "coscc"` — nên một
distribution dựng từ đây chỉ mang `coscc/*.py`. Bundle đã compile nằm ở `.web/`, mà
`.gitignore:1` bỏ qua `.web`. `coscc/build.py:53-56` tìm thư mục đó qua
`reflex.utils.prerequisites.get_web_dir()`, tức là giải theo thư mục làm việc hiện tại.

Hệ quả: một bản cài đặt ngoài checkout rơi thẳng vào `coscc/run.py:52`, thoát mã 2 với câu
*"the frontend is not built yet"*. Muốn qua được câu đó thì phải chạy `uv run coscc-build`,
lệnh này gọi `reflex export` (`coscc/build.py:150`) và kéo theo toàn bộ toolchain Node —
thứ mà máy khách theo định nghĩa ở trên không có.

### Địa chỉ bị nướng vào bundle, và cơ chế gỡ nó ra không áp dụng cho ta

`rxconfig.py:13-20` đã ghi lại phép đo ngày 2026-09-21: frontend compile xong **nướng cứng**
địa chỉ nó sẽ mở `/_event`, và `api_url="/"` bị Reflex ném `TypeError: Invalid URL`. Vì thế
`coscc/build.py:84-85` đưa host và port vào fingerprint, và `coscc/build.py:124` biến lệch
địa chỉ thành lý do từ chối chạy.

Ba phép đo dưới đây lấy trên **bản build cục bộ**, không phải file có trong repo — `.web/`
bị gitignore nên không trích dẫn được theo path:lines. Lệnh tái lập ghi kèm.

| Đo | Kết quả | Lệnh |
|---|---|---|
| Bundle | 3.846 file; 4,69 MB không kể `.gz`, thêm 1,22 MB `.gz` | `find .web/build/client -type f \| wc -l` |
| Số file mang địa chỉ | **2**: `assets/reflex-env-<hash>.js` (306 B, 6 lần) và bản `.gz` 205 B của nó | `grep -rl 127.0.0.1:8790 .web/build/client` |
| Danh sách host được thay lúc chạy | `[localhost, 0.0.0.0, ::, 0:0:0:0:0:0:0:0]` | `grep -o "SAME_DOMAIN_HOSTNAMES=\[[^]]*\]" .web/build/client/assets/*.js` |

Dòng thứ ba là thứ giải thích được lỗi 2026-09-21 mà `rxconfig.py` chỉ ghi triệu chứng:
bundle **có** sẵn hàm thay hostname lúc chạy, nhưng chỉ cho những host trong danh sách đó,
và `127.0.0.1` — mặc định của `coscc/config.py` — không nằm trong. Đây là hành vi đọc được
từ JS đã compile của reflex 0.9.12, không phải API có bảo hành.

### Không có một dòng nào chỉ cách cài

`docs/` có đúng **1** file, `studio.md`, và nó nói về trang chứ không nói về việc cài. Đếm
chữ `install`, không phân biệt hoa thường: `README.md` **0**, `docs/studio.md` **0**.

Hai chỗ duy nhất chỉ cách chạy — `README.md:46-47` và `docs/studio.md:13-14` — đều là
`uv run coscc-build` rồi `uv run coscc`, tức đã giả định sẵn một checkout có `uv sync` chạy
xong. Không chỗ nào nói người dùng cần gì trước đó.

Ba prerequisite tồn tại thật và không cái nào được viết ra cho người đọc:

- **Python 3.14**, ở `pyproject.toml:9`. Ngay trên nó, `pyproject.toml:5-8` giải thích sàn
  này bằng câu *"This is one person's local app, not a library, so there is nobody
  downstream the floor shuts out."* Có khách hàng thì câu đó thành sai.
- **Node/Bun**, không ghi ở đâu cả; nó là hệ quả ngầm của `reflex export`.
- **Claude Code CLI và một tài khoản đã đăng nhập**. Chuỗi `claude-agent-sdk` xuất hiện ở
  đúng **1** file ngoài `.cos/` — `pyproject.toml:11` — và chỉ dưới dạng một dòng
  dependency, không ở đâu nói ra rằng thiếu nó thì chat chết.

## Proposed outcome

Trên một máy Linux sạch — không có checkout của repo này, không `uv`, không Python 3.14,
không Node — một người chỉ đọc tài liệu cài đặt của dự án và không hỏi ai, mở được
`http://127.0.0.1:8790` với **sáu màn hình render và không có Connection Error**, bằng
**≤ 3 lệnh**; rồi chuyển sang bản phát hành kế tiếp bằng **1 lệnh nữa**.

"Lệnh" đếm là một dòng người đó gõ vào shell; tiền tố biến môi trường trên cùng một dòng
không tính thêm. Một proof script dựng máy sạch, chạy đúng các lệnh trong tài liệu, in ra số
lệnh đã dùng và kết quả render.

Outcome này trả về **false** nếu: số lệnh vượt các con số trên, hoặc bất kỳ bước nào cần tới
repository, hoặc trang hiện Connection Error, hoặc bản mới sau khi update vẫn phục vụ bundle
cũ.

## Affected users and systems

- **Người cài trên máy lạ.** Hôm nay không có đường nào tới đích ngoài việc trở thành lập
  trình viên của dự án này.
- **`.github/workflows/release.yml`.** Nơi duy nhất quyết định một release mang theo cái gì.
- **`pyproject.toml`.** Vừa là chỗ khai cái gì được đóng gói (`:24-25`), vừa là chỗ dựng sàn
  Python (`:9`) kèm một lời giải thích sẽ hết đúng.
- **`coscc/build.py` và `coscc/run.py`.** Hai file cùng giữ một câu hỏi "bundle này có khớp
  không", và câu trả lời hiện tại là từ chối chạy — hợp lý cho checkout, chưa rõ có còn hợp
  lý cho bản cài sẵn không.
- **`docs/`.** Sẽ phải nhận tài liệu mà outcome ở trên nhắc tới; hiện chưa có.
- **Không ảnh hưởng `.claude/`.** Harness đi theo con đường copy thư mục, không qua release.

## Constraints

**C1 — Chỉ Linux.** Người khởi xướng chốt ngày 2026-09-22. Không nền tảng nào khác được đo,
và outcome không nói gì về chúng.

**C2 — Vạch đích dừng trước phần chat, có chủ ý.** Người khởi xướng chọn "trang mở được" chứ
không chọn "gửi được tin nhắn", vì phép đo thứ hai tiêu quota thật và phụ thuộc credential
của người khác. Nhưng điều đó **không** cho phép im lặng về Claude Code CLI: tài liệu phải
nói ra rằng thiếu nó thì chat không chạy, kể cả khi outcome không đo phần đó.

**C3 — Sàn Python 3.14 không được lặng lẽ hạ xuống.** `pyproject.toml:5-8` nói mọi phép đo
trong repo này lấy trên interpreter ở `.python-version`, và hạ sàn sẽ biến các con số đó
thành lời nói không kiểm được. Cái phải sửa là **câu giải thích**, vốn sẽ hết đúng khi có
người dùng ở hạ nguồn — không phải con số.

**C4 — Địa chỉ trong bundle phải được xử lý, và phải hỏng ồn ào.** Bảng đo ở trên nói nó nằm
ở đâu; nó nằm trong file có tên mang hash nội dung, tức tên đổi mỗi lần build. Bất kỳ cách
nào đụng vào đó mà không tìm thấy mục tiêu đều phải dừng hẳn, không được chạy tiếp. Đây đúng
là lớp lỗi `rxconfig.py:13-18` ghi lại: trang render, API khỏe, và không bao giờ kết nối —
không phép kiểm HTTP nào nhìn thấy.

**C5 — Phép đo về Reflex là đọc JS đã compile, không phải hợp đồng.** `SAME_DOMAIN_HOSTNAMES`
và hàm thay hostname là chi tiết nội bộ của 0.9.12. Spec không được coi chúng là ổn định;
bằng chứng cuối cùng phải là trình duyệt thật mở trang thật, đường mà `scripts/verify_0003.py`
đã đi.

**C6 — Không có bước phê duyệt.** Như `.claude/CLAUDE.md` mục `## What is deliberately not
built`: unit này do chính agent đề xuất, chấp nhận và ship. Một release cài được sẽ đi tới
máy người khác, nên hệ quả của việc thiếu người duyệt lần này rộng hơn mọi unit trước.

## Open questions

1. **Đưa lên PyPI, hay chỉ đính asset vào release?** Hai đường cho ra hai trải nghiệm update
   khác hẳn nhau. Đo ngày 2026-09-22: `pypi.org/pypi/coscc/json` trả **404**, tên còn trống —
   nhưng còn trống không có nghĩa là nên dùng. Spec quyết.
2. **Bản `.gz` có được phục vụ không?** Chưa đo. Mount ASGI mà `coscc/run.py` dựng có ưu tiên
   `.gz` hay không sẽ quyết định 1.757 file đó là bắt buộc hay là 1,22 MB thừa — và nếu có
   phục vụ, chúng là một bản sao thứ hai của địa chỉ ở C4.
3. **Proof chạy ở đâu?** Outcome đòi một máy sạch, còn phép kiểm render đòi trình duyệt.
   Chưa rõ trình duyệt đứng trong máy sạch đó hay ngoài nó lái vào.
4. **Bản cài sẵn gặp bundle lệch thì làm gì?** `coscc/run.py:52` hiện từ chối chạy, và đó là
   câu trả lời đúng cho một checkout có thể build lại. Máy khách không build lại được. Spec
   phải nói câu trả lời mới là gì.
5. **Cái tên nào đi ra ngoài?** `0008` đã chọn `coscc` cho repo và package. Chưa ai quyết
   định tên đó có phải là tên khách hàng gõ để cài hay không.
