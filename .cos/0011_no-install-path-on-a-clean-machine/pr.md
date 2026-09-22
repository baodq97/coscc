# PR: A release a customer can install, update, and reboot
Intent: intent.md. Impl: impl.md. Author: Bao Do. Status: accepted.

## Where

https://github.com/baodq97/coscc/pull/6

Nhánh `build/no-install-path-on-a-clean-machine`, vào `main`. Tên nhánh lấy từ
`cos.mjs unit-branch`, không gõ tay. 21 commit, squash thành một khi merge.

## Scope of the diff

`git diff --stat main..HEAD`: **31 file, 2939 dòng thêm, 75 dòng bớt.**

Năm file mới mang gần hết khối lượng — `scripts/verify_0011.py` (649),
`scripts/install.sh` (347), `coscc/frontend.py` (214), `coscc/frontend_test.py` (198),
`docs/install.md` (135), `coscc/run_test.py` (83). Năm artifact của unit trong `.cos/`
chiếm 853 dòng và không phải code.

Hai file sửa nhiều nhất: `coscc/run.py` (+210) và `.github/workflows/release.yml` (+128).
Phần còn lại là sửa câu chữ đã hết đúng, rải trên mười file, không file nào quá 40 dòng.

Đổi version 0.1.0 → 0.2.0 chạm bốn file: `pyproject.toml`, `package.json`,
`package-lock.json`, `uv.lock`.

## What a reviewer should look at first

**`23efd75`, và lý do nó tồn tại.** Không phải file lớn nhất; là chỗ rủi ro nhất.

Mọi commit trước nó đã qua `npm test`, qua `verify_0003`, và qua một lần cài thử trên máy
phát triển. Lần chạy đầu trên một VM Debian 13 thật không có Node cho kết quả: wheel cài
xong, `systemctl --user is-active` trả lời `active`, và `curl` trả **`000`** — không phục vụ
được gì. `Type=simple` báo một tiến trình đã sinh ra, không phải một tiến trình đang phục vụ,
nên toàn bộ vòng kiểm tra trước đó nhìn vào một service đang crash-loop và thấy màu xanh.

Điều đáng soi khi review: **đặt `__REFLEX_SKIP_COMPILE` là chưa đủ.** `compile_app` hỏi
`_should_compile()` rồi vẫn rơi xuống compile đầy đủ nếu thiếu
`<web>/backend/stateful_pages.json` (`reflex/compiler/compiler.py:1254-1267`). Cả hai nửa
phải đi cùng nhau, và guard thứ hai trong workflow là thứ duy nhất ngăn ship lại đúng hình
dạng "cài sạch, báo active, không phục vụ gì".

Thứ hai đáng soi: **`spec.md` C1**. Mặc định bind `0.0.0.0` trong khi app không có xác thực
ở bất kỳ route nào. Người khởi xướng quyết ngày 2026-09-22 sau khi được cho xem đúng điều
đó. Tài liệu, banner khởi động và dòng cuối của `install.sh` đều phải nói thẳng; nếu chỗ nào
làm nhẹ đi thì đó là lỗi cần chặn ở review.

Thứ ba: **`plan.md` chưa `done`, và PR này không nhận outcome của unit.**
`scripts/verify_0011.py` chưa chạy trọn một lần — nó gọi đúng dòng lệnh trong
`docs/install.md` nên không trả lời được trước khi có release thật, và bước update cần
release thứ hai. Mọi con số trong `impl.md` là đo tay trên VM đó, gồm hai lần reboot thật
kiểm bằng trình duyệt.
