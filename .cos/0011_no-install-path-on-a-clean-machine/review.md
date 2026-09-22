# Review: A release a customer can install, update, and reboot
PR: pr.md. Author: Bao Do. Concluded by: agent (Claude, the same session that wrote the code). Status: accepted.

**Không có tách biệt trách nhiệm ở đây.** Người viết code và người kết luận review là cùng
một tác nhân, trong cùng một phiên. Ô `review` xanh dưới đây là một cái ghế không có ai ngồi,
đúng như `.claude/CLAUDE.md` `## What is deliberately not built` nói. Nó ghi lại một lượt đọc
lại, không phải một sự chấp thuận. `Concluded by:` ở trên là chỗ duy nhất phân biệt được hai
thứ đó, và nó đang nói "agent".

## Findings

**1. `scripts/install.sh:332` — nhánh báo lỗi nhanh gần như không bao giờ chạy. Mức: trung
bình.** Unit file do chính script ghi mang `Restart=on-failure` (`scripts/install.sh:261`),
nên một service chết ở mỗi lần khởi động xoay vòng qua `activating`/`active` và chỉ ở
`failed` trong tích tắc. Đo được ngay trong unit này: bản đóng gói không phục vụ được gì đã
chạy hết 90 giây timeout mà nhánh này không hề kích hoạt. Sửa ở `357790b` — đọc thêm
`NRestarts`, thứ chỉ tăng khi systemd thật sự phải khởi động lại.

**2. `scripts/install.sh:346-348` — hết giờ chờ thì đưa cho người ta hai câu lệnh và một cái
nhún vai. Mức: trung bình.** Đúng người vừa mới gặp chương trình này lần đầu lại là người ít
có khả năng đi tìm `journalctl` nhất. Sửa ở `357790b` — in thẳng 40 dòng log cuối.

**3. `docs/install.md:17-32` — mục Prerequisites kể ba thứ và không nói Node không phải một
trong số đó. Mức: thấp, nhưng đáng xấu hổ.** Đây đúng là prerequisite mà dự án này hiểu sai
nghiêm trọng tới mức ship ra một wheel không phục vụ được gì. Sửa ở `357790b`.

**4. `.gitignore` thiếu `dist/`. Mức: thấp.** Một lần `uv build --wheel` cục bộ để lại thư
mục không được track. Bán kính nhỏ — không tài liệu nào bảo người dùng build cục bộ — nhưng
`coscc/_web/` đã được ignore vì đúng lý do đó. Sửa ở `357790b`.

## What was not reviewed

**Phần lớn của thay đổi này chưa được ai đọc lại một cách độc lập.** Cụ thể:

- **`scripts/verify_0011.py` (649 dòng) chưa từng chạy trọn vẹn.** Lượt đọc này không thay
  thế được điều đó. Các nhánh chưa bao giờ thực thi: bước 1 chạy lệnh cài thật qua SSH, bước
  5 toàn bộ, và `wait_for_a_newer_release` thêm ở `19143fb` — hàm đó chỉ mới được kiểm phần
  `latest_published_version()`, trả về `0.1.0` đúng với release hiện tại.
- **`.github/workflows/release.yml` chưa chạy lần nào với các thay đổi này.** Hai guard, bước
  chép `.web/backend/`, và phép thay placeholder đều mới. Chúng được đối chiếu bằng tay với
  một lần build cục bộ tái tạo đúng chuỗi lệnh, không phải bằng một lần chạy CI.
- **Đường `--host` và `--working-dir` của `install.sh`.** Chỉ `--port` được thử, và chỉ ở
  dạng bị bỏ qua đúng ý OQ3 khi chạy lại.
- **Bề mặt bảo mật.** Không ai rà. App bind `0.0.0.0` và không có xác thực ở bất kỳ route
  nào; đó là quyết định của người khởi xướng (`spec.md` C1) chứ không phải kết luận của một
  lượt review, và lượt đọc này không đánh giá hệ quả của nó.
- **Năm artifact trong `.cos/` (853 dòng).** Đọc để viết, không đọc để phản biện.

## Verdict

Đủ để merge và để cắt release, **không đủ để nhận outcome của unit**.

Bốn phát hiện đều đã sửa trong `357790b`, và không phát hiện nào chạm vào đường đã được đo
thật trên VM. Điều quyết định verdict không phải là chúng: mà là `scripts/verify_0011.py`
chưa thoát 0 lần nào. `plan.md` giữ nguyên trạng thái chưa `done`, và `ship.md` sau đây phải
nói rõ rằng release được cắt ra để **làm cho proof chạy được**, chứ không phải vì proof đã
xanh.
