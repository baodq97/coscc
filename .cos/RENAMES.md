# How to read paths in artifacts written before 0008

`0008_personal-name-blocks-publishing` đổi tên gói Python của repo. Các artifact viết trước
nó trỏ vào tên cũ, và **chúng không được sửa**. File này là bảng tra.

## The mapping

| Viết trong artifact | Nay là |
|---|---|
| `cos_baodo/<file>` | `coscc/<file>` |
| `cos_baodo/cos_baodo.py` | `coscc/coscc.py` |
| `cos_baodo.cos_baodo:app` | `coscc.coscc:app` |
| lệnh `cos-baodo` | lệnh `coscc` |
| lệnh `cos-build` | lệnh `coscc-build` |
| tên phân phối `cos-baodo` | `coscc` |

Đo ngày 2026-09-22: **272** trích dẫn đường dẫn dạng `cos_baodo/...` nằm trong `.cos/`, trong
đó **97** trích dẫn trên **20 file** có kèm số dòng. Con số này tăng mỗi lần một artifact mới
nhắc tới tên cũ, nên phải đo lại chứ không dùng lại.

## Two things the table cannot fix

**`cos_baodo/store.py:<N>` lệch cả số dòng.** `0008` bước 2 xoá đường import legacy khỏi
`Store` — 52 dòng. Nên với `store.py`, đổi `cos_baodo/` thành `coscc/` là **chưa đủ**: mọi
trích dẫn có số dòng lớn hơn 41 nay trỏ vào nội dung khác. Những chỗ trích
`STORE_FILENAME`, `_import_legacy`, `_load_legacy`, `_needs_import` hoặc `legacy_path` trỏ
vào thứ **không còn tồn tại**; đó là điều `0008 spec.md` R3 cố ý làm, không phải hỏng.

**Tên cũ vẫn nằm trong history.** `0008 intent.md` constraint 5 giữ nguyên 121 commit đầu,
nên `git log -p` vẫn đọc ra tên cũ. Outcome của unit đó đo trên tree, không trên history.

## Why the artifacts were not rewritten

Repo đã gặp đúng tình huống này một lần. `0002` đổi `app/` thành tên gói cũ, và
`.cos/0002_no-workspace-management/spec.md:245-250` kết luận:

> Sửa nghĩa là viết lại một artifact đã ký; không sửa nghĩa là harness có trích dẫn hỏng,
> trong khi chính nó đòi "cite only a file committed in this repository". Không lối nào
> sạch. Unit này chọn không sửa và ghi lại ở đây.

Kết quả là bảng tra ở `.cos/0001_no-session-management/plan.md:4-21`, đặt ở đầu chính file bị
ảnh hưởng, với lý do rằng viết lại thân file sẽ thành *"một bản ghi sai theo kiểu khác"*.

`0008` không đặt được bảng theo cách đó. Lúc `0002` làm, chỉ có **1** file bị ảnh hưởng.
Ở `0008` có **20**, và `0008 spec.md` C5 đo được rằng **5** trong số đó đang bị nơi khác trích
dẫn kèm số dòng — thêm một header vào chúng sẽ đẩy mọi dòng xuống và làm **19** trích dẫn
đang đúng thành sai. Tức là chữa 272 trích dẫn chết bằng cách tạo ra 19 trích dẫn chết kiểu
khác. Nên bảng nằm một chỗ, ở đây.

## Why this file is here and not in .claude/

Bảng tra buộc phải viết ra tên cũ. `.claude/harness.md` nằm **ngoài** `.cos/`, và outcome của
`0008` là `git grep -in <tên cũ> -- . ':!.cos'` bằng 0 — nên đặt bảng ở đó thì outcome không
bao giờ đạt. `.cos/` là vùng duy nhất vừa được loại khỏi phép đo vừa đúng về ngữ nghĩa.

File này nằm ở tầng `.cos/`, không trong unit dir nào, nên harness bỏ qua nó:
`.claude/scripts/cos.mjs:97-98` chỉ liệt kê thư mục, và kiểm tra file lạ ở `:83-84` chỉ chạy
bên trong một unit dir. `.claude/harness.md` trỏ tới đây.
