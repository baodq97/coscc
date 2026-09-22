# Intent: a released version installs nowhere, and nothing brings it back after a reboot
Author: Bao Do. Type: build. Status: accepted.

> **Sửa ngày 2026-09-22, sau khi bản đầu đã accepted và commit (`d2f55fd`).** Người khởi
> xướng nói thêm: *"với này là run dạng app server, nên cần có cơ chế restart vm thì nó vẫn
> auto bình thường"*. Bản đầu đo đúng nhưng đo thiếu — nó dừng ở "cài được và update được",
> tức coi phần mềm này là một lệnh người ta gõ, trong khi nó là một dịch vụ chạy thường
> trực. Một bản cài chết sau lần reboot đầu tiên thì cái outcome cũ vẫn xanh, mà khách hàng
> vẫn không có gì dùng.
>
> Harness không có bước sửa đổi, nên ghi đè một file đã `accepted` là một lựa chọn có chủ ý
> chứ không phải một quy trình — `0008` đã làm đúng việc này và ghi lý do y như đây. Bản đầu
> đọc được ở `d2f55fd`; file này thay nó. Người khởi xướng chọn đường ghi đè ngày
> 2026-09-22, sau khi được chào ba phương án gồm cả việc tách thành unit riêng.
>
> **Slug không đổi và giờ chỉ gọi tên được một nửa vấn đề.** `.claude/CLAUDE.md` chốt slug
> tại lúc tạo, nên `no-install-path-on-a-clean-machine` ở lại dù nó không nhắc gì tới
> reboot. Ghi ra đây để người đọc sau không tưởng là đọc nhầm thư mục.

## Problem

Người khởi xướng nói, nguyên văn, ngày 2026-09-22, qua năm lượt:

> "hiện tại phần gh release chỉ package source only, tôi nghĩ nên có cách nào để giúp khách
> hàng triển khai dễ hàng hơn, bản này phải cài được update được nhỉ?"

> "đáng lẽ là package phải kèm cả frontend nhỉ? có cách nào không?"

> "với phần này bundle thành 1 bản cài như nào. chỉ hỗ trợ linux thôi."

> "thiếu doc cài đặt nhỉ?"

> "với này là run dạng app server, nên cần có cơ chế restart vm thì nó vẫn auto bình thường"

Hỏi lại và được trả lời: **"khách hàng"** là người trên một **máy Linux lạ, không có gì
sẵn** — chưa clone repo, chưa có `uv`, chưa có Python 3.14, chưa có Node. **"Run được"** tính
là **trang mở ra và sáu màn hình render**, không kèm Connection Error; không tính việc gửi
được tin nhắn chat, vì phép đo đó tiêu quota thật và phụ thuộc credential của người khác.
Và **đích chạy là một VM**, nên vòng đời của nó tính bằng lần reboot chứ không tính bằng
phiên terminal.

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

### Địa chỉ bị nướng vào bundle, và nó nằm ở hai bản chứ không phải một

`rxconfig.py:13-20` đã ghi lại phép đo ngày 2026-09-21: frontend compile xong **nướng cứng**
địa chỉ nó sẽ mở `/_event`, và `api_url="/"` bị Reflex ném `TypeError: Invalid URL`. Vì thế
`coscc/build.py:84-85` đưa host và port vào fingerprint, và `coscc/build.py:124` biến lệch
địa chỉ thành lý do từ chối chạy.

Bốn phép đo dưới đây lấy trên **bản build cục bộ và trên package reflex 0.9.12 đã cài**,
không phải file có trong repo — `.web/` bị `.gitignore:1` bỏ qua và `.venv/` không được
commit, nên không trích dẫn được theo `path:lines`. Lệnh tái lập ghi kèm.

| Đo | Kết quả | Lệnh |
|---|---|---|
| Bundle | 3.846 file; 4,69 MB không kể `.gz`, thêm 1,22 MB `.gz` | `find .web/build/client -type f \| wc -l` |
| Số file mang địa chỉ | **2**: `assets/reflex-env-<hash>.js` (306 B, 6 lần) và bản `.gz` 205 B của nó | `grep -rl 127.0.0.1:8790 .web/build/client` |
| Host được thay lúc chạy | `[localhost, 0.0.0.0, ::, 0:0:0:0:0:0:0:0]` | `grep -o "SAME_DOMAIN_HOSTNAMES=\[[^]]*\]" .web/build/client/assets/*.js` |
| `.gz` có được phục vụ không | **Có.** `frontend_compression_formats` mặc định `['gzip']`, và `PrecompressedStaticFiles` ưu tiên sidecar `.gz` khi client gửi `Accept-Encoding: gzip` | `uv run python -c "from reflex.config import get_config; print(get_config().frontend_compression_formats)"` |

Dòng thứ ba giải thích được lỗi 2026-09-21 mà `rxconfig.py` chỉ ghi triệu chứng: bundle
**có** sẵn hàm thay hostname lúc chạy, nhưng chỉ cho những host trong danh sách đó, và
`127.0.0.1` — mặc định của `coscc/config.py` — không nằm trong.

Dòng thứ tư là cái bẫy đắt nhất. Địa chỉ nằm ở hai bản, **cả hai đều được phục vụ**, và bản
`.gz` là bản mà mọi trình duyệt thật nhận. Một phép sửa chạm vào `.js` mà bỏ `.gz` sẽ đúng
dưới `curl` và sai dưới mọi trình duyệt — lại đúng lớp lỗi mà không phép kiểm HTTP nào nhìn
thấy.

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

### Và không có gì khởi động nó lại

Đếm ngày 2026-09-22: `git grep -ciE "systemd|autostart" -- . ':!.cos'` trả **0**. Không một
unit file, không một script init, không một dòng tài liệu nào nói tới việc chạy thường trực.

`coscc/run.py:60-66` gọi `uvicorn.run(...)` và chạy tiền cảnh cho tới khi bị giết. Đó là
hành vi **đúng** cho một tiến trình do service manager trông, và cũng là hành vi khiến nó
biến mất cùng phiên terminal đã sinh ra nó. Trên một VM, lần reboot đầu tiên là lần cuối
cùng trang còn mở được.

Cách truyền cấu hình hiện tại cũng gắn chặt vào cái phiên đó: chỗ duy nhất đọc environment
là `coscc/config.from_env`, và cách duy nhất được tài liệu hoá để đưa `COS_WORKING_DIR` vào
là tiền tố shell trên cùng một dòng lệnh (`README.md:47`, `docs/studio.md:14`). Một dịch vụ
không có shell nào để gắn tiền tố vào.

## Proposed outcome

Trên một máy Linux sạch — không có checkout của repo này, không `uv`, không Python 3.14,
không Node — một người chỉ đọc tài liệu cài đặt của dự án và không hỏi ai:

1. mở được `http://127.0.0.1:8790` với **sáu màn hình render và không có Connection Error**,
   bằng **≤ 3 lệnh**;
2. reboot máy đó, và trang lại mở được mà **không ai gõ thêm lệnh nào** — **0 lệnh**;
3. chuyển sang bản phát hành kế tiếp bằng **1 lệnh**, rồi reboot lần nữa và bước 2 vẫn đúng.

"Lệnh" đếm là một dòng người đó gõ vào shell; tiền tố biến môi trường trên cùng một dòng
không tính thêm.

**Ba bước nhưng một verdict, và đó là một outcome chứ không phải ba** — vì không bước nào
còn nghĩa nếu thiếu bước kia: một bản cài không sống qua reboot thì không phải app server,
và một app server không nhận được bản vá thì không phải một bản phát hành. Một proof script
dựng máy từ ảnh sạch, chạy đúng các lệnh chép ra từ tài liệu, và in một kết quả duy nhất.

Outcome trả về **false** nếu: số lệnh vượt các con số trên, hoặc bất kỳ bước nào cần tới
repository, hoặc trang hiện Connection Error, hoặc sau reboot phải có người can thiệp, hoặc
bản mới sau khi update vẫn phục vụ bundle cũ.

## Affected users and systems

- **Người cài trên máy lạ.** Hôm nay không có đường nào tới đích ngoài việc trở thành lập
  trình viên của dự án này.
- **VM đích.** Nó có một service manager, và hiện không có gì trong repo này biết điều đó.
- **`.github/workflows/release.yml`.** Nơi duy nhất quyết định một release mang theo cái gì.
- **`pyproject.toml`.** Vừa là chỗ khai cái gì được đóng gói (`:24-25`), vừa là chỗ dựng sàn
  Python (`:9`) kèm một lời giải thích sẽ hết đúng.
- **`coscc/build.py` và `coscc/run.py`.** Hai file cùng giữ một câu hỏi "bundle này có khớp
  không", và câu trả lời hiện tại là từ chối chạy — hợp lý cho checkout, chưa rõ có còn hợp
  lý cho bản cài sẵn không.
- **`coscc/config.py`.** Nơi duy nhất đọc environment, và bây giờ nguồn của environment đó
  không còn là một phiên shell.
- **`docs/`.** Sẽ phải nhận tài liệu mà outcome ở trên nhắc tới; hiện chưa có.
- **Không ảnh hưởng `.claude/`.** Harness đi theo con đường copy thư mục, không qua release.

## Constraints

**C1 — Chỉ Linux, và có systemd.** Người khởi xướng chốt Linux ngày 2026-09-22. Bước 2 của
outcome đòi một service manager, và trên Linux thực tế đó là systemd; bản phân phối Linux
không dùng systemd nằm ngoài phạm vi và tài liệu phải nói ra điều đó.

**C2 — Vạch đích dừng trước phần chat, có chủ ý.** Người khởi xướng chọn "trang mở được" chứ
không chọn "gửi được tin nhắn", vì phép đo thứ hai tiêu quota thật và phụ thuộc credential
của người khác. Nhưng điều đó **không** cho phép im lặng về Claude Code CLI: tài liệu phải
nói ra rằng thiếu nó thì chat không chạy, kể cả khi outcome không đo phần đó.

**C3 — Sàn Python 3.14 không được lặng lẽ hạ xuống.** `pyproject.toml:5-8` nói mọi phép đo
trong repo này lấy trên interpreter ở `.python-version`, và hạ sàn sẽ biến các con số đó
thành lời nói không kiểm được. Cái phải sửa là **câu giải thích**, vốn sẽ hết đúng khi có
người dùng ở hạ nguồn — không phải con số.

**C4 — Địa chỉ trong bundle phải được xử lý ở cả hai bản, và phải hỏng ồn ào.** Bảng đo ở
trên nói nó nằm ở đâu và nói rằng bản `.gz` mới là bản trình duyệt nhận. Nó nằm trong file
có tên mang hash nội dung, tức tên đổi mỗi lần build. Bất kỳ cách nào đụng vào đó mà không
tìm thấy mục tiêu đều phải dừng hẳn, không được chạy tiếp.

**C5 — Phép đo về Reflex là đọc nội bộ, không phải hợp đồng.** `SAME_DOMAIN_HOSTNAMES`,
`PrecompressedStaticFiles` và `REFLEX_WEB_WORKDIR` là chi tiết bên trong 0.9.12. Spec không
được coi chúng là ổn định; bằng chứng cuối cùng phải là trình duyệt thật mở trang thật,
đường mà `scripts/verify_0003.py` đã đi.

**C6 — Tiến trình vẫn phải chạy tiền cảnh.** `coscc/run.py:60-66` không daemon hoá, và phải
giữ nguyên như vậy: tự fork là lấy mất của service manager đúng thứ nó cần để biết tiến
trình còn sống hay không.

**C7 — Không được sinh ra người đọc environment thứ hai.** `coscc/config.from_env` là chỗ
duy nhất, và `spec.md` của `0001` đã trả giá để dựng nên điều đó. Dịch vụ phải đưa cấu hình
vào qua chính cửa ấy, không phải qua một file cấu hình mới đọc ở chỗ khác.

**C8 — Không có bước phê duyệt.** Như `.claude/CLAUDE.md` mục `## What is deliberately not
built`: unit này do chính agent đề xuất, chấp nhận và ship. Một release cài được, chạy
thường trực trên máy người khác, là unit có hệ quả rộng nhất từ trước tới nay khi thiếu
người duyệt.

## Open questions

1. **Đưa lên PyPI, hay chỉ đính asset vào release?** → **Đã trả lời**, người khởi xướng,
   2026-09-22: thuần GitHub, không PyPI, không GitHub Pages; phân phối bằng một `install.sh`
   tên cố định lấy qua `/releases/latest/download/`. Đo cùng ngày: endpoint đó trả 302 về
   release mới nhất, và `api.github.com/repos/baodq97/coscc` trả 200 nên không cần token.
   Cái giá đi kèm phải được ghi vào spec: tên file wheel bắt buộc mang số phiên bản, nên
   `uv tool upgrade coscc` gõ trơn sẽ **không** tìm thấy gì — update là chạy lại một lệnh,
   không phải lệnh `upgrade`.
2. **Bản `.gz` có được phục vụ không?** → **Đã trả lời**: có, xem bảng đo.
3. **Proof chạy ở đâu?** Bước 2 của outcome đòi một lần reboot thật, mà container thì không
   reboot. Chưa biết proof dựng VM thật, dùng `systemd-nspawn`, hay chấp nhận một phép thay
   thế yếu hơn — và nếu yếu hơn thì yếu ở chỗ nào phải ghi ra.
4. **Bản cài sẵn gặp bundle lệch thì làm gì?** `coscc/run.py:52` hiện từ chối chạy, và đó là
   câu trả lời đúng cho một checkout có thể build lại. Máy khách không build lại được. Spec
   phải nói câu trả lời mới là gì.
5. **Cái tên nào đi ra ngoài?** → **Đã trả lời**: `coscc`. Không lên PyPI nên không có tranh
   chấp tên.
6. **User unit hay system unit?** User unit cần `loginctl enable-linger` thì mới chạy lúc
   boot khi chưa ai đăng nhập; system unit thì cần quyền root lúc cài và một user riêng để
   chạy. Hai đường có hai kiểu hỏng khác nhau và hai kiểu quyền khác nhau. Chưa chọn.
7. **Trang phục vụ cho ai nhìn?** Đây là câu hỏi nặng nhất còn mở. `coscc/config.py` mặc
   định bind `127.0.0.1` và `rxconfig.py:8-12` ghi rõ rằng bind loopback là một thuộc tính
   an toàn phải giành lại từ mặc định `0.0.0.0` của Reflex. Nhưng "app server trên VM" nghe
   như là sẽ có người mở nó từ máy khác. Nếu đúng thế thì mọi tư thế an toàn trong repo này
   bị lật, và `0001` đã dựng nó lên có chủ đích. Nếu không, tài liệu phải nói thẳng rằng
   trang chỉ mở được từ chính VM đó, qua SSH tunnel hoặc tương đương.
8. **Cấu hình `COS_*` sống ở đâu với một dịch vụ?** C7 chặn việc đẻ ra người đọc thứ hai,
   nhưng chưa nói giá trị đi vào bằng đường nào. Và một trong các giá trị đó, `COS_WORKING_DIR`,
   trỏ tới git checkout của người dùng — nên câu trả lời quyết định luôn việc dịch vụ chạy
   dưới danh nghĩa user nào.
