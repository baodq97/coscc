# Spec: a wheel that carries its own frontend, and a service that comes back after a reboot
Intent: intent.md. Author: Bao Do. Status: accepted.

Skip assessment, ngày 2026-09-22, cả năm tiêu chí:

| # | Tiêu chí | Verdict |
|---|---|---|
| 1 | ≤2 file đã tồn tại | **Fail** — tối thiểu `release.yml`, `pyproject.toml`, `coscc/build.py`, `coscc/run.py`, `coscc/config.py`, `README.md`, cộng bốn file mới |
| 2 | Không đổi public interface / stored data | **Fail** — artifact phân phối là một interface mới, `coscc/run.py:52` phải đổi câu trả lời, và mặc định `host` đổi |
| 3 | Không thêm dependency | Pass |
| 4 | Không có hành vi ngoài `intent.md` | Pass |
| 5 | Không chạm auth/PII/bề mặt bảo mật | **Fail** — `curl \| sh`, và bind ra mọi interface một app không có xác thực |

Ba tiêu chí fail nên spec phải viết. Tiêu chí **1** ép mạnh nhất; tiêu chí **5** là cái đắt
nhất nếu sai, và nó là toàn bộ nội dung `## Concerns` mục C1.

Mọi số dẫn lại ở đây đều đã đo trong `intent.md` và không đo lại.

## Requirements

**R1 — Distribution mang theo frontend đã compile.** Wheel chứa bundle dưới `coscc/_web/`.
Bắt buộc vì `pyproject.toml:24-25` khai `module-root = ""` và `module-name = "coscc"`, nên
chỉ những gì nằm trong `coscc/` mới đi theo gói. Kiểm: `unzip -l` trên wheel dựng ra liệt kê
`coscc/_web/index.html`, và số file dưới `coscc/_web/` ≥ 3.800.

**R2 — Một câu hỏi "frontend ở đâu", một nơi trả lời.** Hôm nay có hai đường độc lập cùng
trả lời nó: `coscc/build.py:53-56`, và mount của Reflex vốn tự gọi `get_web_dir()` lần nữa.
Sau thay đổi này chỉ còn một: giá trị được quyết một lần rồi đặt vào `REFLEX_WEB_WORKDIR`
**trước khi import app**, để mount của Reflex đọc đúng cái đó. `coscc/run.py:32` đã có tiền
lệ đặt biến trước import và ràng buộc thứ tự y hệt. Kiểm: một test khẳng định thư mục
`build.web_dir()` trả về trùng với thư mục mount phục vụ, và `git grep` tìm ra **0** chỗ thứ
hai tự giải đường dẫn đó.

**R3 — Địa chỉ được viết lại ở cả hai bản, và phép kiểm phải giải nén.** Sau khi viết lại
cho `host:port` đang chạy, quét toàn bộ bundle — **giải nén mọi file `.gz` trước khi quét** —
tìm thấy **0** lần xuất hiện của bất kỳ địa chỉ nào khác địa chỉ đích. `intent.md` đo rằng
bản `.gz` mới là bản trình duyệt nhận, nên một phép kiểm không giải nén sẽ báo xanh trên
đúng cái hỏng.

**R4 — Không tìm thấy mục tiêu thì dừng hẳn.** Nếu phép viết lại khớp **0** file theo mẫu
`assets/reflex-env-*.js`, tiến trình thoát khác 0 và in ra tên mẫu nó đã tìm. Không được
phục vụ. Đây là `intent.md` C4: tên file mang hash nội dung nên đổi mỗi lần build, và Reflex
không hứa giữ nguyên cách phát chunk đó.

**R5 — Mặc định `host` đổi thành `0.0.0.0`, và ai cũng nhìn thấy điều đó.** Hai chỗ:
`coscc/config.py:66` và `coscc/config.py:141`. `COS_HOST` vốn đã cấu hình được nên đây là
đổi mặc định, không phải thêm khả năng. Banner khởi động ở `coscc/run.py:57-59` phải nói ra
địa chỉ đang bind và nói ra rằng khi nó không phải loopback thì máy khác trong mạng mở được
trang mà không cần đăng nhập. Kiểm: một test khẳng định banner chứa câu đó khi host không
thuộc `{127.0.0.1, localhost, ::1}`.

**R6 — Bundle đóng gói dựng với `api_url = http://0.0.0.0:<port>`.** `intent.md` đo rằng
`0.0.0.0` nằm trong `SAME_DOMAIN_HOSTNAMES`, nên trình duyệt thay hostname bằng chính
hostname nó mở trang. Đây là thứ khiến **một** bundle dựng sẵn phục vụ được mọi địa chỉ.
Kiểm: trình duyệt thật mở trang qua một hostname không phải `0.0.0.0` và WebSocket `/_event`
kết nối được.

**R7 — Bản cài sẵn không từ chối chạy vì lệch địa chỉ.** `coscc/run.py:52` hiện thoát mã 2;
với một bundle đóng gói, câu trả lời đúng là viết lại rồi chạy tiếp, vì máy khách không có
Node để build lại. Với một checkout thì hành vi cũ giữ nguyên — ở đó "bundle cũ hơn source"
vẫn là lỗi thật và vẫn sửa được bằng `uv run coscc-build`. Kiểm: hai test, một cho mỗi
đường.

**R8 — `coscc --version` in ra phiên bản đang chạy, một dòng.** Nó là thứ duy nhất phân biệt
được "đã update" với "tưởng là đã update", nên bước 3 của outcome không đo được nếu thiếu
nó. Kiểm: chuỗi in ra trùng `version` trong `pyproject.toml` của chính wheel đã cài.

**R9 — `install.sh`, tên cố định, tự kiểm toàn vẹn.** Lấy qua
`/releases/latest/download/install.sh`. Số phiên bản và **sha256 của wheel** được nướng vào
script lúc release. Script tải wheel từ release đúng phiên bản đó, so sha256 trước khi cài,
và **thoát khác 0 khi lệch**. Nếu máy chưa có `uv` thì cài `uv` trước. Kiểm: sửa một byte
của wheel rồi chạy lại, script phải từ chối.

**R10 — Dịch vụ là systemd user unit, và lên được lúc boot.** `install.sh` ghi
`~/.config/systemd/user/coscc.service`, chạy `loginctl enable-linger` cho user đó, rồi
`systemctl --user enable --now coscc`. Không cần root ở bất kỳ bước nào. Kiểm: sau reboot,
`systemctl --user is-active coscc` trả `active` mà không ai đăng nhập.

**R11 — Cấu hình của dịch vụ đi qua đúng cửa cũ.** Unit dùng `EnvironmentFile=` trỏ tới một
file người dùng sửa được; mọi giá trị trong đó là biến `COS_*` và được đọc bởi
`coscc/config.from_env` chứ không phải bởi một trình đọc mới. `intent.md` C7. Kiểm:
`git grep` vẫn tìm ra **0** chỗ đọc environment ngoài `coscc/config.py`.

**R12 — `COS_PORT` chọn được lúc cài, mặc định `8790`.** `install.sh` nhận một cờ, ghi giá
trị vào `EnvironmentFile`, và dùng chính giá trị đó khi viết lại địa chỉ trong bundle (R3).
Kiểm: cài với một port khác mặc định, trang mở được ở port đó và không còn dấu vết port cũ
sau phép quét của R3.

**R13 — Release mang đúng ba asset, và không mang một wheel rỗng.** `release.yml` đính
`coscc-<ver>-py3-none-any.whl`, `install.sh`, `SHA256SUMS`. Workflow **thất bại** nếu wheel
dựng ra không chứa `coscc/_web/index.html` — không có bước này thì một thay đổi ở CI sẽ phát
hành một wheel không frontend mà mọi check vẫn xanh. Kiểm: `gh release view --json assets`
đếm được 3.

**R14 — `docs/install.md` tồn tại, và proof chạy đúng các lệnh chép từ nó.** Tài liệu phải
gọi tên ba prerequisite mà `intent.md` đo được là chưa viết ở đâu — Python 3.14, Claude Code
CLI kèm tài khoản đã đăng nhập, và systemd — cộng với câu về bind address ở R5. Kiểm: proof
trích các khối lệnh từ chính file đó; tài liệu trôi khỏi thực tế thì proof đỏ.

**R15 — Các con số của outcome.** ≤ 3 lệnh để tới trang; **0** lệnh sau reboot; **1** lệnh
để lên bản kế tiếp. Proof in ra số lệnh nó đã chạy.

## Design

Sáu phần, và ranh giới giữa chúng là chỗ dễ hỏng nhất.

**1. Đường dựng gói (CI).** Compile bundle → chép `.web/build/client/**` vào `coscc/_web/`
→ `uv build`. Bundle không nằm trong git (`.gitignore:1`) nên bước chép là thứ duy nhất đưa
nó vào wheel, và R13 là cái canh bước đó.

**2. Gói.** `coscc/_web/` cộng một marker cạnh nó. Marker khác marker hôm nay
(`coscc/build.py:75-86`) ở chỗ nó khai `packaged: true`, phiên bản của gói, phiên bản
reflex, và địa chỉ đã nướng lúc build. Nó **không** mang digest của source: trong một wheel,
source và bundle đi cùng một chuyến nên không thể lệch nhau, và `rxconfig.py` thậm chí không
có trong gói. Đó là lý do câu hỏi mà marker trả lời phải đổi nghĩa chứ không chỉ đổi giá
trị — xem C3.

**3. Nơi quyết định frontend ở đâu.** Một hàm: có `coscc/_web/` thì dùng nó, không thì dùng
`.web` của checkout. Kết quả đặt vào `REFLEX_WEB_WORKDIR` trước khi import app. Đây là ranh
giới R2 bảo vệ; mọi thứ phía sau chỉ đọc biến đó.

**4. Bộ viết lại địa chỉ.** Nhận `(thư mục, host, port)`, tìm `assets/reflex-env-*.js`, thay
sáu chuỗi, **dựng lại bản `.gz` từ nội dung mới** thay vì sửa nó, rồi trả về số file đã
chạm. 0 file là lỗi (R4). Nó chạy trước khi uvicorn lên, và chỉ chạy trên bundle đóng gói.

**5. `install.sh`.** Một file, chạy theo thứ tự: kiểm Linux và systemd → cài `uv` nếu thiếu
→ tải wheel của phiên bản nướng sẵn → so sha256 → `uv tool install --force` → ghi
`EnvironmentFile` (giữ lại giá trị cũ nếu đã có) → ghi unit → `enable-linger` → `enable --now`.
Chạy lại chính nó chính là đường update: script mới ở `/latest/download/` trỏ tới wheel mới.

**6. Proof.** Một máy dựng từ ảnh sạch, chạy các lệnh trích từ `docs/install.md`, đếm lệnh,
mở trang bằng trình duyệt thật, reboot, mở lại, update, reboot lại. Trình duyệt đứng ngoài
lái vào — `scripts/proof_harness.py` đã là đường đó.

Dữ liệu đi qua ranh giới chỉ có ba thứ: marker JSON, chunk env, và `SHA256SUMS`.

## Out of scope

- **macOS, Windows, và Linux không systemd.** `intent.md` C1.
- **PyPI và index trên GitHub Pages.** Người khởi xướng loại ngày 2026-09-22.
- **Docker.** Không ai yêu cầu, và nó làm khó ba thứ app này cần: đọc checkout trên máy,
  chạy `git`, dùng credential Claude của chính người dùng.
- **Domain + TLS + Let's Encrypt.** Người khởi xướng nêu ngày 2026-09-22, rồi **hoãn nó cùng
  ngày** sau khi mâu thuẫn ở C2 được nêu. Nó xứng đáng một unit riêng: một dịch vụ thứ hai
  (reverse proxy), một giao thức thứ hai (ACME), và một câu hỏi về quyền mà R10 đã trả lời
  ngược lại. Thiết kế ở đây **không chặn** nó: nhờ R6, đúng bundle này chạy sau proxy HTTPS
  mà không build lại, vì lúc đó trình duyệt nâng `http→https`, `ws→wss` và **xoá port**.
- **Báo cho người dùng biết có bản mới.** Người khởi xướng nêu ngày 2026-09-22. Là hành vi
  của trang và là lệnh gọi mạng ra ngoài đầu tiên của app, nên Type của nó là `feat` chứ
  không phải `build`. R8 để sẵn cái móc nó cần.
- **Xác thực, TLS, nhiều người dùng.** Xem C1. Không dòng nào trong `intent.md` cho phép làm
  chúng ở đây, và làm nửa vời còn tệ hơn không làm.
- **Chat chạy thông suốt đầu-cuối.** `intent.md` C2 chốt vạch đích trước chỗ đó.
- **Ký artifact bằng GPG hoặc sigstore.** R9 dừng ở sha256.

## Concerns

**C1 — Bind `0.0.0.0` trên một app không có xác thực.** Đo ngày 2026-09-22: quét `coscc/`
tìm auth/token/password/bearer cho ra **0** phép kiểm danh tính trên bề mặt HTTP; mọi kết
quả đều là comment nói về credential mà tiến trình đang giữ. `coscc/api.py:9-11` viết ra
giả định thiết kế bằng chính lời nó — *"a long-lived login credential is in this process,
and those two habits are what keep it there"* — và hai thói quen nó kể là không đọc
environment và không chạy thứ caller đặt tên. Đó không phải xác thực. `coscc/coscc.py:1`
đặt tên module là *"one page, one ASGI app, one loopback port"*, và `rxconfig.py:8-12` ghi
rằng `0001` cố ý bind ngược lại mặc định `0.0.0.0` của Reflex.

Hệ quả của R5: ai route tới được cổng đó đều dùng được toàn bộ app mà không cần đăng nhập,
gồm hai nút tiêu quota Claude thật (`README.md:56-57`). **Policy owner: người khởi xướng,
đã quyết ngày 2026-09-22 sau khi được nêu.** Spec này thi hành quyết định đó và không làm
nhẹ nó đi; R5 bắt banner nói ra, và R14 bắt tài liệu nói ra.

**C2 — R10 và phương án TLS mâu thuẫn trực tiếp, và spec này không giải.** R10 chọn user
unit, không root. Let's Encrypt qua HTTP-01 cần cổng 80; mọi cổng dưới 1024 cần root,
`setcap`, hoặc chuyển sang DNS-01 kèm credential DNS. Không có đường nào vừa không-root vừa
tự-xin-cert. **Policy owner: người khởi xướng.** Cho tới khi có quyết định, TLS nằm ở
`## Out of scope` và R10 giữ nguyên.

**C3 — Marker đóng gói làm yếu đúng cái chốt `0003` đã dựng.** `coscc/build.py` được viết để
trả lời "bundle này có khớp source không" và `coscc/run.py` từ chối chạy khi lệch. Với một
wheel, câu hỏi đó mất nghĩa — source và bundle không lệch nhau được. Nhưng đổi lại, lớp bảo
vệ chống "bundle cũ hơn source" biến mất ở bản đóng gói, và cái thay thế nó là niềm tin vào
bước chép ở CI cộng với R13. Đó là một chốt yếu hơn, và nó yếu ở chỗ khác chỗ cũ.

**C4 — Ba thứ spec này dựa vào đều là nội bộ của Reflex 0.9.12.** `SAME_DOMAIN_HOSTNAMES`
(R6), `PrecompressedStaticFiles` và mặc định `['gzip']` (R3), `REFLEX_WEB_WORKDIR` (R2).
Không cái nào là API có bảo hành. Nâng Reflex có thể làm hỏng cả ba mà mọi unit test vẫn
xanh — chỉ proof mở bằng trình duyệt thật mới thấy. `intent.md` C5.

**C5 — `uv tool upgrade coscc` gõ trơn sẽ không làm gì.** Tên file wheel bắt buộc mang số
phiên bản nên `/releases/latest/download/` không phục vụ nó dưới một tên cố định; đó là lý
do R9 chọn `install.sh`. Người dùng quen `uv` sẽ gõ `upgrade` trước, và nó im lặng không
tìm thấy gì. R14 phải viết thẳng điều này ra, nếu không tài liệu đúng mà trải nghiệm sai.

**C6 — `uv build` chạy tay sẽ cho ra một wheel hỏng.** Không có bước chép ở phần 1 của
Design, wheel vẫn dựng thành công và vẫn cài được, chỉ là không có frontend. R13 chặn được
đường CI; nó không chặn được người chạy `uv build` trên máy mình rồi gửi file đó cho ai đó.

**C7 — Không có bước phê duyệt, và lần này artifact đi sang máy người khác.**
`.claude/CLAUDE.md`, `## What is deliberately not built`. Mọi unit trước đó, hậu quả của
việc thiếu người duyệt dừng lại trong repo này. Từ unit này thì không.

**C8 — `enable-linger` là trạng thái hệ thống, và nó sống lâu hơn bản cài.** R10 bật nó cho
user đó. Gỡ `coscc` không tắt nó, và không có requirement nào ở đây nói tới việc gỡ cài đặt.

## Open questions

1. **Proof reboot ở đâu?** (`intent.md` mục 3, vẫn mở.) Container không reboot. Ba đường:
   VM thật, `systemd-nspawn --boot`, hoặc chấp nhận `systemctl --user restart` như một phép
   thay thế. Đường thứ ba **không** đo được `enable-linger`, tức bỏ sót đúng cơ chế R10 dựa
   vào. Câu trả lời quyết định R15 đo được thật hay chỉ đo gần đúng, và phải ghi rõ nó yếu ở
   đâu nếu chọn đường yếu.
2. **`EnvironmentFile` nằm ở đâu và ai sở hữu nó?** R11 chốt là phải đi qua `from_env`,
   nhưng chưa chốt đường dẫn. `~/.config/coscc/env` và `$COS_DATA_DIR/env` cho ra hai kết
   quả khác nhau khi người dùng đổi `COS_DATA_DIR` — một trong hai sẽ tự trỏ vào chỗ nó vừa
   được cấu hình rời khỏi.
3. **Update có giữ lại cấu hình cũ không, và nếu unit file đổi thì sao?** Design phần 5 nói
   giữ `EnvironmentFile` cũ, nhưng chưa nói chuyện gì xảy ra khi bản mới cần một biến mới
   hoặc một unit file khác. Câu trả lời quyết định bước 3 của outcome có thật sự là một lệnh
   hay không.
4. **Bundle đóng gói có mang theo 1.757 file `.gz` không?** → **Trả lời tạm: có, giữ chúng.**
   Có thì wheel nặng thêm 1,22 MB và mỗi lần viết lại địa chỉ phải dựng lại một file nữa
   (R3). Không thì mất nén trên đường truyền — ở loopback gần như không đáng kể, nhưng R5
   vừa đổi mặc định thành `0.0.0.0`, nên đường truyền thật sự đã là mạng chứ không còn là
   loopback, và 1,22 MB trong wheel rẻ hơn việc phải thêm lại chúng sau. Câu này vốn buộc
   vào C2; C2 bị hoãn ngày 2026-09-22 nên nó được trả lời tại chỗ thay vì treo.
