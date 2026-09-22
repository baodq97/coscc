# Impl: No install path on a clean machine
Intent: intent.md. Plan: plan.md. Author: Bao Do. Status: accepted.

## What was built

Một release mà khách cài được bằng một dòng, và một app tự lên lại sau khi máy khởi động lại.

**Wheel tự chở frontend.** `5cf4809` dựng `coscc/frontend.py`, nơi duy nhất trả lời hai câu
hỏi "bundle nằm đâu" và "bundle đang trỏ tới địa chỉ nào". `3094eb9` cho wheel phép mang
bundle và chặn một lần build cục bộ lọt vào commit. `6aa0ab8` dạy workflow release dựng
frontend, chép vào `coscc/_web/`, và **từ chối ship một wheel không có frontend**.

**Một bản cài chạy được ở nơi nó rơi xuống.** `187ca19` tách đôi hành vi: checkout gặp
bundle lệch source thì vẫn từ chối như `0003` đã định, còn bản đóng gói thì **ghi lại địa
chỉ** rồi chạy — vì nó không có Node để build lại, và từ chối ở đó là đưa cho người ta một
wheel không bao giờ khởi động được. `8e5bcda` ghi lại rằng marker đóng gói không được dựng,
kèm lý do.

**Mặc định mở ra mạng, và nói thẳng điều đó.** `1f96a27` đổi `host` mặc định sang `0.0.0.0`
và bắt mỗi lần khởi động phải in ra bề mặt mạng vừa mở. `7342f67` sửa một lỗi tệ hơn: trang
đang **tự khẳng định** "Local only / Loopback" trong khi bind mọi interface — trấn an về
đúng thứ vừa bị mở ra.

**Một dòng để cài, cùng dòng đó để nâng cấp.** `d76b6c6` viết `scripts/install.sh`: kiểm
Linux + systemd, cài `uv` nếu thiếu, tải wheel của đúng phiên bản nướng sẵn trong chính nó,
**so sha256 trước khi đụng vào bất cứ thứ gì**, `uv tool install --force`, ghi env file một
lần duy nhất và không bao giờ ghi đè, luôn ghi lại unit file, `enable-linger`, rồi chờ tới
khi app thật sự trả lời. `47ab35a` thêm `coscc --version`, trả lời trước mọi thứ có thể hỏng.

**Tài liệu và dọn câu sai.** `ce64493` sửa chín câu trong repo đã hết đúng hoặc chỉ còn đúng
một nửa, và trỏ `README.md` sang `docs/install.md`.

**Bản sửa do máy lạ tìm ra.** `23efd75` — xem mục dưới.

## Where the plan was departed from

Năm chỗ, tất cả đã ghi trong `plan.md` cùng commit với thay đổi (plan invariant 8):

1. **Bước 10 đếm thiếu.** Plan nói "hai câu đã hết đúng"; đếm lại là chín, và phần lớn
   *còn đúng một nửa* chứ không sai hẳn — checkout và bản đóng gói trả lời khác nhau. Nên
   bước 10 thành thêm vế phân biệt, không phải xoá.
2. **Marker đóng gói không được dựng.** `spec.md` `## Design` phần 2 mô tả nó; đọc code thì
   `build.check()` chỉ có hai caller ngoài test và cả hai đều là đường checkout. Ghi ra chứ
   không sửa file đã accepted.
3. **Trang tự nói dối.** Không plan nào cho phép sửa `screens.py`/`state.py`, nhưng ship một
   trang khẳng định một thuộc tính an toàn nó không có là đúng loại lỗi `0007` sinh ra để dọn.
4. **`install.sh` nhận thêm `COSCC_DOWNLOAD_BASE` và một bước chờ sẵn sàng.** Cái sau là hệ
   quả của phép đo: `Type=simple` báo "started" khi tiến trình sinh ra, app mất ~4 giây mới
   bind, nên người làm đúng tài liệu nhận connection refused.
5. **Bước 11 làm đổi cả code lẫn chính proof.** Ghi chú thứ tư và thứ năm trong `plan.md`.
   Đây là departure lớn nhất và là lý do unit này tồn tại — xem mục kế.

## What was measured

Tất cả 2026-09-22. Máy đích: VM Proxmox 101, Debian 13 trixie, 4 vCPU, 5931 MB, vào qua SSH,
**không `node`, không `npm`, không `bun`, không `git`, không `pip`** (k3s đã gỡ trước đó).

| Đo | Lệnh | Kết quả |
|---|---|---|
| Bộ test | `npm test` | **281 tests, OK**, cả hai runtime |
| Phiên bản | `node .claude/scripts/cos.mjs check-version` | `0.2.0`, năm chỗ khớp |
| Proof không có target | `uv run python scripts/verify_0011.py` | exit **2** |
| Wheel | `uv build --wheel` | 4.3 MB, **3848** mục `coscc/_web/`, **1756** sidecar `.gz` |
| Guard frontend | `unzip -l` | `index.html` có; `backend/stateful_pages.json` có |

**Lần chạy đầu trên máy lạ trả lời "không", và đó là phép đo giá trị nhất của unit.**
Wheel đã qua mọi test, qua `verify_0003`, qua một lần cài thử trên máy phát triển — cài lên
VM xong **không phục vụ được trang nào**:

```
FileNotFoundError: Bun or npm not found.
```

Reflex chạy lại toàn bộ compile mỗi lần khởi động, và compile đó kết thúc bằng
`install_frontend_packages`, thứ đòi Bun hoặc npm. Ba điều khiến nó sống sót qua mọi phép
kiểm trước đó, đo được từng cái:

- `systemctl --user is-active coscc` trả lời **`active`** trong lúc service crash-loop.
  `Type=simple` báo một tiến trình đã sinh ra, không phải một tiến trình đang phục vụ.
- `curl http://127.0.0.1:8790/` trả **`000`**. Không phép kiểm nào dựa trên trạng thái
  systemd nhìn thấy được điều này.
- Máy phát triển có sẵn Node/Bun trong cache Reflex, nên lần thử trước xanh **vì môi
  trường, không phải vì code**.

Đặt `__REFLEX_SKIP_COMPILE` là chưa đủ: `compile_app` hỏi `_should_compile()` rồi vẫn rơi
xuống compile đầy đủ trừ khi có `<web>/backend/stateful_pages.json`
(`reflex/compiler/compiler.py:1254-1267`). File đó nằm ngoài `build/client`, nên release chỉ
chép cây static sẽ ship một wheel đặt biến rồi vẫn chết. Sửa ở `23efd75`.

**Sau khi sửa, đo lại trên chính VM đó, cài bằng `install.sh` chứ không can thiệp tay:**

| Đo | Kết quả |
|---|---|
| `install.sh` thoát | **0**, bước chờ sẵn sàng thành công |
| Service | `active`, `enabled`, `Linger=yes`, nghe `0.0.0.0:8790` |
| Trình duyệt thật | websocket mở tới `ws://192.168.15.101:8790/_event/`; sáu màn hình bấm được; **"Connection Error" không xuất hiện**; 0 page error |
| Bundle server gửi ra | `content-encoding: gzip`, giải nén ra **đúng một** địa chỉ socket: `ws://0.0.0.0:8790/_event` |
| Reboot thật lần 1 | boot_id đổi; trang trả 200 sau ~5s, **0 lệnh**; trình duyệt lại xanh |
| Reboot thật lần 2 | boot_id đổi; trang trả 200 sau ~10s, **0 lệnh**; trình duyệt lại xanh |
| Chạy lại `install.sh` | thoát **0**; env file **giống hệt từng byte** (md5 `df17556b…` trước và sau) |

Hai lỗi nhỏ hơn cũng do máy lạ tìm ra, đã sửa trong `23efd75`: `install.sh` chỉ tìm `uv`
bằng `command -v`, mà shell không-đăng-nhập có PATH `/usr/local/bin:/usr/bin:/bin:/usr/games`
— không có `$HOME/.local/bin` — nên mỗi lần update tải lại ~50 MB `uv` (sau khi sửa: chuỗi
`uv not found` xuất hiện **0** lần); và dòng cuối in `http://127.0.0.1:8790/` cho người vừa
cài lên một VM qua SSH.

## What is still open

**`scripts/verify_0011.py` chưa chạy trọn vẹn một lần nào.** Mọi bảng ở trên là phép đo tay
trên cùng máy đích, không phải output của proof. Proof chạy **đúng dòng lệnh trong
`docs/install.md`**, tức `/releases/latest/download/install.sh`, nên nó không trả lời được
trước khi có release thật — và bước 5 đòi `coscc --version` đổi số, nên cần **hai** release.
Precondition đó không được viết ra ở đâu cả; lần chạy thật làm nó lộ ra, và `19143fb` sửa
proof để nó **chờ** release kế tiếp thay vì báo đỏ, với timeout là exit 2.

Vì vậy `plan.md` vẫn **chưa** `done`. Nó chỉ được đặt sau khi lệnh dưới `## Proof` thoát 0.

Ba thứ có thật và cố ý không làm, đã ghi trong `plan.md` `## What this plan chose not to do`:
không có xác thực dù mặc định mở ra mạng (`spec.md` C1, người khởi xướng quyết 2026-09-22);
không có `uninstall.sh`, và `enable-linger` ở lại sau khi gỡ (`spec.md` C8); không ký
artifact, dừng ở sha256. TLS + domain được người khởi xướng hoãn lại và `spec.md` C2 giữ
nguyên mâu thuẫn chưa giải.
