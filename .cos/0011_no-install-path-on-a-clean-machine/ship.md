# Ship: A release a customer can install, update, and reboot
Review: review.md. Author: Bao Do. Status: accepted.

## What went out

Ba release, và cả ba đều thật. Chỉ release đầu mang thay đổi:

| Tag | Nội dung | Vai trò |
|---|---|---|
| `v0.2.0` | PR #6, squash `aef7d63` — toàn bộ unit này | Release đầu tiên mang asset |
| `v0.2.1` | PR #7, squash `3e0770a` — chỉ năm chỗ khai version | Bản kế tiếp để proof đo đường update |
| `v0.2.2` | PR #8, squash `1c91687` — chỉ năm chỗ khai version | Bản kế tiếp cho lần chạy proof thành công |

Mỗi release mang **ba asset**: `coscc-<ver>-py3-none-any.whl` (4.492.696 byte cho v0.2.0),
`install.sh` (15.840 byte, đã thay số phiên bản và sha256), và `SHA256SUMS`. So sánh với
`v0.1.0`, vốn có **0 asset** — chính là con số `intent.md` đo được làm vấn đề.

PR #9 sửa hai khiếm khuyết trong chính proof, tìm ra bởi hai lần chạy thật đầu tiên.

**Ba release cho một unit là nhiều hơn dự tính, và lý do đáng ghi:** mỗi lần chạy proof
trọn vẹn **tiêu một release**. Bước 5 đo "chuyển sang bản phát hành kế tiếp", mà cả lệnh cài
lẫn lệnh update đều phân giải qua `/releases/latest/` — nên bản kế tiếp phải xuất hiện
*trong lúc* proof đang chờ. Đây không phải khiếm khuyết của proof; đó là sự thật về điều
đang được khẳng định. `v0.2.0` và `v0.2.1` bị tiêu bởi hai lần chạy dừng lại ở hai lỗi thật.

## Did the outcome hold

**Có.** Đo ngày 2026-09-22, `scripts/verify_0011.py` thoát **0**, 17 claim xanh, trên
`bd@192.168.15.101` — VM Proxmox 101, Debian 13 trixie, 4 vCPU, 5931 MB, không `uv`, không
`coscc`, không `node`/`npm`/`bun`, không `git`, không `pip`, `Linger=no`, cổng 8790 trống.

Trả lời đúng ba con số `intent.md` nêu:

1. **≤ 3 lệnh → chạy 1.** `commands run: 1 to install`. Sáu màn hình render và `/_event` kết
   nối, đo bằng trình duyệt thật chứ không bằng HTTP. Bundle server gửi ra, đọc với
   `Accept-Encoding: gzip` và giải nén, mang **đúng một** địa chỉ socket: `ws://0.0.0.0:8790`.
2. **0 lệnh sau reboot → đúng, hai lần.** Reboot thật, xác nhận bằng `boot_id` đổi, không
   phải `systemctl --user restart`. Trang tự trả lời sau **4 giây** lần một và **2 giây**
   lần hai, không ai gõ gì.
3. **1 lệnh để lên bản kế tiếp → `commands run: 1 to update`.** `coscc --version` đổi số, và
   env file **không đổi một dòng nào**.

Không điều kiện nào trong danh sách "outcome trả về false" xảy ra: không bước nào cần tới
repository, không có Connection Error, không ai can thiệp sau reboot, và bundle sau update
là bundle mới.

**Điều outcome này không nói, và không bao giờ định nói:** trang mở được không có nghĩa là
chat chạy được. Proof không kiểm Claude Code CLI có mặt hay đã đăng nhập chưa —
`intent.md:168-171` cố ý dừng ở đó, và `docs/install.md` nêu nó thành prerequisite thứ ba.

## How it is watched

```sh
COS_PROOF_TARGET=<user@host> uv run python scripts/verify_0011.py
```

Đây là tín hiệu duy nhất. Nó cần một máy Linux sạch và **một release chưa publish** để
bước 5 có chỗ chuyển tới; thoát **2** khi thiếu máy đích, thoát **1** khi một claim sai.

Hai điều rẻ hơn, chạy được mà không cần máy đích:

```sh
npm test                              # 281 tests, cả hai runtime
unzip -l <wheel> | grep coscc/_web/backend/stateful_pages.json
```

Lệnh thứ hai là thứ đáng canh nhất. Workflow release đã có guard cho nó, nhưng **một wheel
thiếu file đó vẫn cài được, vẫn báo `active`, và không phục vụ gì** — đó là hình dạng lỗi
mà toàn bộ unit này suýt ship ra.

**Băng kiểm soát:** trang phải trả lời trong vòng **180 giây** sau reboot. Đo được 4s và 2s;
proof in ra con số thật mỗi lần, nên một hồi quy hiện ra dưới dạng số chứ không phải dưới
dạng một dấu xanh. Vượt 180 giây là `plan.md` Risk 6 quay lại và là `intent.md` kế tiếp.

## What to do if it breaks

**Nếu một bản cài lên rồi không phục vụ gì** — `systemctl --user is-active coscc` nói
`active` mà `curl` trả `000`: đọc `journalctl --user -u coscc -n 40`. Nếu thấy
`Bun or npm not found` thì wheel đó thiếu `coscc/_web/backend/`; đó là lỗi của build chứ
không phải của máy. Quay về `v0.2.2` bằng cách chạy `install.sh` của release đó:
`curl -LsSf https://github.com/baodq97/coscc/releases/download/v0.2.2/install.sh | sh`.

**Nếu cần lùi toàn bộ unit:** revert `aef7d63` trên `main`. Nó là một commit squash duy nhất
mang cả 31 file. Lùi nó đưa mặc định `host` về `127.0.0.1` và bỏ luôn đường cài — tức là
quay lại đúng vấn đề `intent.md` mô tả, nên đây là nút thoát hiểm chứ không phải một lựa chọn.

**Cách quay lại rẻ hơn, nếu vấn đề chỉ là bề mặt mạng:** sửa một dòng trong
`${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env` thành `COS_HOST=127.0.0.1` rồi
`systemctl --user restart coscc`. `install.sh` không bao giờ ghi đè file đó, nên thay đổi
này sống qua mọi lần update — đó chính là lý do nó được viết như vậy.

**Cái không có đường quay lại:** `loginctl enable-linger` ở lại sau khi gỡ cài đặt, và
không có `uninstall.sh`. `spec.md` C8 ghi nhận, và không unit nào đã sửa.
