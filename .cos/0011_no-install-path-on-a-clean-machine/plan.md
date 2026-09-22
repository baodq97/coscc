# Plan: build the proof first, then the wheel, then the service
Intent: intent.md. Spec: spec.md. Author: Bao Do. Status: accepted.

Hai open question của `spec.md` được chốt ở đây, vì cả hai quyết định file nào bị chạm.

**OQ1 — proof reboot chạy ở đâu.** Trên một máy mà người chạy proof đưa vào, qua SSH, đặt ở
`COS_PROOF_TARGET`. Không có biến đó thì proof thoát **2** kèm câu nói rõ nó không trả lời
được — đúng lối `scripts/verify_0007.py:365`, `verify_0008.py:447` và `verify_0009.py:549`
đã đi. **`systemctl --user restart` bị loại thẳng làm phương án thay thế**: nó không chạm
tới `enable-linger`, mà linger chính là cơ chế R10 dựa vào để lên lúc boot, nên nó sẽ xanh
trên đúng thứ hỏng. Một proof trả lời "không biết" thì sửa được; một proof trả lời sai thì
không.

**OQ2 — `EnvironmentFile` nằm ở đâu.** `${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env`. Lý do
loại `$COS_DATA_DIR/env`: file cấu hình `COS_DATA_DIR` mà lại nằm trong thư mục do chính
`COS_DATA_DIR` đặt tên là một vòng tròn — đổi giá trị xong thì lần đọc sau tìm ở chỗ khác và
mất luôn cái vừa đặt. Đường dẫn mới không phụ thuộc bất kỳ giá trị nào nó chứa, và nằm cùng
cây với unit file ở `~/.config/systemd/user/coscc.service`.

**OQ3 — update có giữ cấu hình cũ không.** Có. `install.sh` **không bao giờ ghi đè**
`env` nếu nó đã tồn tại; unit file thì ghi lại mỗi lần, vì nó là thứ sinh ra chứ không phải
thứ người dùng sửa. Đó chính là lý do hai file phải tách nhau.

## Files that change

**Đã tồn tại**

| Path | Thay đổi |
|---|---|
| `coscc/build.py` | `web_dir()` nhường việc phân giải cho `coscc/frontend.py`; marker thêm `packaged`; `check()` thêm nhánh cho bundle đóng gói |
| `coscc/build_test.py` | Test cho nghĩa mới của marker đóng gói |
| `coscc/config.py` | `:66` và `:141` đổi mặc định `host` sang `0.0.0.0`; docstring `:5-10` phải nói mặc định nào vừa hết là "tư thế an toàn" và vì sao |
| `coscc/run.py` | Đặt `REFLEX_WEB_WORKDIR` trước import; gọi bộ viết lại địa chỉ; banner `:57-59` nói ra bề mặt mạng; thêm `--version` |
| `pyproject.toml` | Chỉ sửa comment `:5-8` — câu "nobody downstream" hết đúng. **Không** cần khai package data: đo ngày 2026-09-22 bằng `uv build --wheel`, `uv_build` gom mọi file dưới `coscc/` kể cả `.gz` mà không cần khai gì |
| `.gitignore` | Thêm `coscc/_web/`, để một lần build cục bộ không lọt vào commit |
| `.github/workflows/release.yml` | Build bundle → chép vào `coscc/_web/` → `uv build` → chặn wheel rỗng → đính 3 asset |
| `scripts/proof_harness.py` | `:65` — xem ghi chú dưới bảng |
| `.claude/rules/coscc-app.md` | `:80` — xem ghi chú dưới bảng |
| `docs/studio.md` | `:73` — xem ghi chú dưới bảng |
| `scripts/verify_0006.py` | `:24` — xem ghi chú dưới bảng |
| `coscc/coscc.py` | `:1` — module tự gọi mình là "one loopback port" |
| `rxconfig.py` | `:44` — cùng loại |
| `pyproject.toml` | cộng thêm `:40`, cùng loại |
| `README.md` | Trỏ sang tài liệu cài đặt; nói rõ khối lệnh hiện có là đường dành cho checkout |
| `coscc/screens.py` | `:109-110` và `:785` — xem ghi chú thứ ba |
| `coscc/state.py` | `:326` — cờ `loopback_only`, xem ghi chú thứ ba |
| `coscc/api_test.py` | Test cấm markup viết tay phải loại trừ `coscc/_web/` |

**Mới**

| Path | Là gì |
|---|---|
| `scripts/verify_0011.py` *(new)* | Proof của cả hành trình: cài, mở trang, reboot, update, reboot |
| `coscc/frontend.py` *(new)* | Hai việc: phân giải thư mục frontend, và viết lại địa chỉ trong bundle |
| `coscc/frontend_test.py` *(new)* | Test cho cả hai, gồm cả trường hợp khớp 0 file |
| `scripts/install.sh` *(new)* | Bản mẫu; release thay số phiên bản và sha256 vào rồi đính kèm |
| `docs/install.md` *(new)* | Tài liệu mà R14 đòi, và là nguồn các lệnh proof chép ra |
| `coscc/run_test.py` *(new)* | Test cho banner và `--version`; plan bản đầu không kể nó ra |

**Ghi chú, thêm 2026-09-22 trong lúc implement — plan invariant 8.** Bản đầu của plan nói
bước 10 dọn "hai câu đã hết đúng". Đếm lại bằng `git grep -n hardcode -- . ':!.cos'` thì
câu đó nằm ở **sáu** chỗ, và quan trọng hơn: **nó không hết đúng, nó chỉ còn đúng một
nửa.** Với một checkout, bundle vẫn nướng cứng địa chỉ và `verify_0003`/`verify_0006` vẫn
không dời được sang port khác — R7 giữ nguyên hành vi đó có chủ ý. Với một bản đóng gói thì
địa chỉ được ghi lại lúc khởi động và câu đó sai.

Nên bước 10 không phải xoá, mà là thêm vế phân biệt vào từng chỗ. Một câu đúng-một-nửa
không kèm vế phân biệt còn tệ hơn một câu sai, vì người đọc không có cách nào biết mình
đang ở thế giới nào. Hai chỗ còn lại — `coscc/build.py:125` và `coscc/build_test.py:72` —
đã nằm trong bước 4.

**Ghi chú thứ hai, 2026-09-22 trong lúc implement — plan invariant 8.** Bước 4 được viết để
dựng một marker đóng gói khai `packaged: true`, theo `spec.md` `## Design` phần 2. Đọc code
xong thì thấy **không ai sẽ đọc nó**. `git grep` cho ra đúng hai chỗ gọi `build.check()`
ngoài test — `coscc/run.py` và `scripts/proof_harness.py:72-73` — và cả hai đều là đường
checkout; `run.py` rẽ nhánh trên `frontend.is_packaged()` **trước khi** hỏi `build.py` bất
cứ điều gì. Câu hỏi mà marker trả lời, "bundle có cũ hơn source không", cũng mất nghĩa
trong một wheel: hai thứ đi chung một file nên không lệch nhau được.

Nên marker không được dựng. Lý do ghi ở đây và trong docstring của `coscc/build.py`, chứ
không phải để người đọc sau tưởng là bỏ sót. `spec.md` `## Design` phần 2 là chỗ duy nhất
mô tả nó, và nó sai — ghi ra chứ không sửa file đã accepted.

**Ghi chú thứ ba, 2026-09-22 trong lúc implement — plan invariant 8.** Chạy thử `install.sh`
thật rồi mở bản vừa cài bằng trình duyệt cho thấy **trang tự nói dối về chính nó**. Hai chỗ
trong `coscc/screens.py` khẳng định loopback như một sự thật: `:109-110` in "Local only /
Loopback, and chat sessions with no tools by default.", và `:785` in "Address — Loopback
only." Sau bước 5 thì mặc định là `0.0.0.0`, nên cả hai câu sai, và sai theo hướng nguy
hiểm nhất: chúng trấn an về đúng thứ vừa bị mở ra.

Không plan nào cho phép việc này, và không requirement nào nêu nó — `spec.md` R5 chỉ nói tới
banner. Nhưng ship một trang khẳng định một thuộc tính an toàn nó không có là đúng loại lỗi
`0007` sinh ra để dọn, nên nó được sửa tại đây thay vì để lại: `coscc/state.py` thêm
`loopback_only` đọc từ địa chỉ thật, và hai chỗ kia đọc cờ đó thay vì tự khẳng định.

Kéo theo `coscc/api_test.py`: phép kiểm "không có HTML/CSS viết tay trong `coscc/`" bắt phải
`coscc/_web/`, vốn là output compile chứ không phải markup ai gõ. Phạm vi loại trừ đúng bằng
một thư mục, và có thêm một test ghim chính cái tên đó để không ai nới nó ra thành `coscc`.

Và `scripts/install.sh` nhận thêm hai thứ không có trong plan: `COSCC_DOWNLOAD_BASE` để chạy
thử được trước khi tồn tại release nào — nó không nới lỏng gì, sha256 vẫn so với giá trị
nướng sẵn — và **một bước chờ cho tới khi app thật sự trả lời**. Bước chờ là hệ quả của một
phép đo: `Type=simple` khiến systemd báo "started" ngay khi tiến trình sinh ra, còn app mất
khoảng bốn giây mới bind. Thiếu bước chờ, người làm đúng theo tài liệu mở trình duyệt và
nhận connection refused trên một máy hoàn toàn bình thường.

`coscc/_web/` không có trong bảng nào: nó là sản phẩm của bước build, không phải file được
commit.

## Order of work

1. **`scripts/verify_0011.py`** — proof trước, theo invariant 4. Nó phải chạy được ngay hôm
   nay và phải **đỏ**: không có `COS_PROOF_TARGET` thì thoát 2; có thì thoát 1 vì chưa có
   release nào cài được. Trạng thái kiểm được: chạy nó, đọc mã thoát.
2. **`coscc/frontend.py` + `coscc/frontend_test.py`** — bộ phân giải và bộ viết lại, chưa ai
   gọi. Bộ viết lại dựng lại `.gz` từ nội dung mới thay vì sửa nó, và trả về số file đã
   chạm; 0 là lỗi. Kiểm: `npm test`.
3. **`coscc/run.py` gọi hai thứ đó**, đặt `REFLEX_WEB_WORKDIR` trước khi import app. Đường
   checkout phải không đổi hành vi. Kiểm: `npm test`, và `uv run python scripts/verify_0003.py`
   vẫn xanh.
4. **`coscc/build.py` + `build_test.py`** — **phạm vi thu lại trong lúc làm, xem ghi chú
   dưới đây.** Không có marker đóng gói; hai file chỉ nhận vế phân biệt checkout/đóng gói.
   Kiểm: `npm test`.
5. **`coscc/config.py` đổi mặc định host, `run.py` đổi banner.** Tách khỏi bước 3 vì nó đổi
   hành vi mạng chứ không đổi cách phục vụ file, và nó phải xem lại được một mình. Kiểm:
   `npm test`, và một test khẳng định banner nói ra bề mặt khi host không phải loopback.
6. **`coscc --version`.** Kiểm: lệnh in ra đúng số của `pyproject.toml`.
7. **`.gitignore` + `pyproject.toml` comment.** Kiểm: `uv build --wheel` sau khi chép tay
   một `coscc/_web/` giả, `unzip -l` thấy nó.
8. **`scripts/install.sh` + `docs/install.md`.** Kiểm: chạy script với một wheel dựng cục
   bộ, trên chính máy này, tới bước `systemctl --user is-active coscc` trả `active`.
9. **`.github/workflows/release.yml`.** Kiểm: đẩy một tag prerelease `vX.Y.Z-rc.N`, rồi
   `gh release view --json assets` đếm được 3, và bước chặn wheel rỗng có chạy.
10. **Mọi câu đã thành sai, hai nhóm.** Nhóm một: bốn chỗ nói "the bundle hardcodes the
    address" — thêm vế phân biệt checkout/đóng gói. Nhóm hai, phát hiện khi quét lại:
    **năm chỗ nữa nói app bind loopback**, và sau bước 5 thì chúng sai thẳng chứ không
    phải đúng một nửa — `README.md:50`, `.claude/rules/coscc-app.md:25`, `coscc/coscc.py:1`,
    `pyproject.toml:40`, `rxconfig.py:44`. `README.md` cũng trỏ sang `docs/install.md`.
    Kiểm: `git grep -in "hardcode\|loopback" -- . ':!.cos'` và đọc từng dòng; `npm test`.
11. **Chạy proof thật** với `COS_PROOF_TARGET`. Đây là bước duy nhất trả lời được outcome.

Bước 1 và bước 11 là cùng một file. Nó được viết trước để có thứ đỏ, và được chạy sau cùng
để có thứ xanh; giữa hai lần đó nó không đổi.

## Risks

**1. Viết lại `.js` mà bỏ `.gz` — và mọi phép kiểm rẻ tiền đều xanh.** Đây là rủi ro lớn
nhất và nó giống hệt lỗi 2026-09-21. `curl` không gửi `Accept-Encoding` nên nhận bản `.js`
đã sửa; mọi trình duyệt gửi, nên nhận bản `.gz` cũ. Cái phát hiện được: phép quét của R3,
bắt buộc giải nén trước khi tìm. Cái **không** phát hiện được: bất kỳ test nào dùng `httpx`
mặc định.

**2. Đổi mặc định `host` làm rộng bề mặt mạng của một bản cài đã có, lúc update.** Đây là
rủi ro tôi muốn không phải viết ra: người đang chạy bản cũ mà không đặt `COS_HOST` thì đang
ở `127.0.0.1`, và chạy lại `install.sh` cho bản mới sẽ để mặc định trôi sang `0.0.0.0` —
máy họ mở ra mạng mà không ai nói gì.

**Hôm nay nó không có dân số.** Đo ngày 2026-09-22: `v0.1.0` có **0** asset, nên không tồn
tại đường nào để ai đó đã cài; người khởi xướng xác nhận cùng ngày. Dân số xuất hiện đúng
vào lần update đầu tiên, tức bản ngay sau unit này.

Cách chặn vẫn giữ, và nó là một requirement lên `install.sh` chứ không phải một lời nhắc:
**lần cài đầu tiên ghi `COS_HOST` thành một dòng tường minh trong `env`**, nên về sau không
có mặc định nào trôi được nữa. Giữ nó vì nó tốn một dòng và vì thứ nó bảo vệ là bản kế tiếp,
không phải bản này. Cái phát hiện được: bước 11, nếu proof chạy kịch bản update trên một máy
đã cài bản cũ — và nó phải chạy kịch bản đó.

**3. `REFLEX_WEB_WORKDIR` đặt sau khi import app.** Reflex đọc nó lúc dựng ASGI stack, nên
đặt muộn một dòng là mount trỏ vào `.web` không tồn tại và trang trả 404 trong khi API vẫn
khỏe. `coscc/run.py:30-32` đã có đúng ràng buộc thứ tự này cho `MOUNT_FLAG`. Cái phát hiện
được: proof mở trang bằng trình duyệt; một health check HTTP thì không.

**4. CI không chép bundle, wheel vẫn dựng thành công.** `uv build` không biết gì về
`coscc/_web/`, nên thiếu bước chép thì nó cho ra một wheel hợp lệ, cài được, không frontend.
Cái phát hiện được: bước chặn ở R13. Cái không: mọi check khác trong workflow.

**5. Nâng Reflex làm hỏng ba thứ nội bộ cùng lúc** — `SAME_DOMAIN_HOSTNAMES`,
`PrecompressedStaticFiles`, `REFLEX_WEB_WORKDIR`. Cái phát hiện được: chỉ proof mở trang
thật. `spec.md` C4.

**6. Quên `enable-linger`, và nó trông như đã xong.** Ngay sau khi cài,
`systemctl --user is-active coscc` trả `active` dù linger tắt; sai lệch chỉ lộ ra ở lần
reboot đầu tiên, có thể là nhiều ngày sau. Cái phát hiện được: chỉ bước 11 với reboot thật.
Đây là lý do OQ1 không nhận `restart` làm thay thế.

**7. `install.sh` ghi đè `env` của người dùng.** Mất `COS_WORKING_DIR` là app khởi động
xong nhưng không thấy workspace nào, và trông như mất dữ liệu. Chặn bằng OQ3. Cái phát hiện
được: kịch bản update ở bước 11.

## Proof

```sh
npm test
COS_PROOF_TARGET=<user@host> uv run python scripts/verify_0011.py
```

`npm test` phải xanh cả hai runtime.

`verify_0011.py` thoát **0** chỉ khi tất cả những điều sau cùng đúng trên máy đích, dựng từ
ảnh sạch:

- tới trang được bằng **≤ 3 lệnh** chép ra từ `docs/install.md`, và script in ra số lệnh đã chạy;
- sáu màn hình render, WebSocket `/_event` kết nối — đo bằng trình duyệt thật, không bằng HTTP;
- quét toàn bộ bundle đang phục vụ, **giải nén mọi `.gz`**, còn **0** địa chỉ khác địa chỉ đích;
- reboot máy, không ai gõ gì, trang lại mở được và sáu màn hình lại render;
- **1 lệnh** đưa lên bản kế tiếp; `coscc --version` đổi số; `env` giữ nguyên từng dòng;
- reboot lần nữa, trang vẫn mở được.

Thoát **1** là một trong các điều trên sai. Thoát **2** là môi trường không trả lời được —
không có `COS_PROOF_TARGET`, không có chromium, máy đích không có systemd. Ba mã giữ riêng
vì gộp 2 vào 1 sẽ báo "chưa đưa máy đích vào" thành "bản cài hỏng", đúng cái `verify_0003.py`
được viết ra để không lặp lại.

## What this plan chose not to do

- **Không đụng tới xác thực**, dù bước 5 mở app ra mạng. `intent.md` không cho phép, và một
  lớp auth làm nửa vời còn tệ hơn không có — nó tạo ra niềm tin không có gì đỡ. `spec.md` C1
  ghi ai đã quyết và ngày nào.
- **Không khai package data trong `pyproject.toml`.** Đo ngày 2026-09-22: không cần. Ghi ra
  đây để người đọc sau không tưởng là bỏ sót.
- **Không gỡ bỏ được.** Không có `uninstall.sh`, và `enable-linger` ở lại sau khi gỡ
  (`spec.md` C8). Không requirement nào đòi, nên không làm — nhưng nó là thiếu sót có thật
  chứ không phải ngoài phạm vi vì vô hại.
- **Không ký artifact.** Dừng ở sha256 trong `install.sh`, như `spec.md` R9.
