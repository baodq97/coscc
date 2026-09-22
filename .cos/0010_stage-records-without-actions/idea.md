# Idea: A stage record is being used as an instruction to do the work
Author: GitHub Copilot. Status: accepted.

## What was noticed

Ngày 2026-09-22, trong lúc bàn prototype UI, người dùng nói: "với hiện tại mấy các skills
write-review, write-ship, write-impl là không đúng thực tế á".

Các skill hiện yêu cầu công việc đã xảy ra trước khi ghi artifact:
`write-impl` chạy sau khi code đã viết
(`.claude/skills/write-impl/SKILL.md:1-12`); `write-review` sau khi thay đổi đã được
review (`.claude/skills/write-review/SKILL.md:1-4`); `write-ship` sau khi thay đổi đã
landed (`.claude/skills/write-ship/SKILL.md:1-12`). Trong khi đó, bảng stage lại đưa
người dùng tới `write-impl` với mô tả "implementation starts"
(`.claude/scripts/cos.mjs:24-33`). Ghi nhận kết quả và thực hiện hành động đang bị
trình bày như cùng một việc.

Người dùng chọn: "Tiếp tục prototype trước; ghi nhận việc sửa skill thành phần việc
riêng". Với ship, người dùng chọn: "Chưa chốt; chưa cho phép tự thực hiện ship".

## Why it might matter

Một file báo đã review không chứng minh diff đã được đọc. Một file báo đã ship
không chứng minh có bản chạy hoặc bản bàn giao. Nếu board coi việc sinh file là đã
làm công việc, trạng thái có thể trông hoàn tất trong khi hoạt động thực tế còn thiếu.

Đặc biệt, prototype chạy local, thiết kế được người dùng duyệt, backend được nối và
sản phẩm được phát hành là những sự kiện khác nhau. Hiện chưa có quyết định thống
nhất về sự kiện nào cho phép đóng một unit.

## What is not known yet

- Skill phải trực tiếp thực hiện công việc hay chỉ ghi bằng chứng từ executor khác?
- Review thay đổi local không có PR đi qua gate nào, ai kết luận, và bằng chứng gì
  đủ để phân biệt self-review của agent với người dùng duyệt?
- Ship của sản phẩm local nghĩa là gì, cần ai cho phép, có cần xác nhận riêng không?
- Cần thay đổi những chỗ nào trong gate, runner và board để không dùng sự tồn tại
  của artifact thay cho bằng chứng thực thi?

Chưa có intent, spec hoặc plan cho việc sửa này. Không skill, gate hoặc runner nào
được đổi từ quan sát trên; chưa cho phép tự ship.
