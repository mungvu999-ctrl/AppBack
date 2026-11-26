# RULE-CODE — Luật viết mã bắt buộc

> **Mọi phiên (người hay agent) đọc file này trước khi viết dòng mã đầu tiên.**
> Thứ tự ưu tiên khi mâu thuẫn: `docs/charter/BE-00.md` → `docs/charter/BE-KFM.md` →
> file này → prompt. `docs/RULE.md` là bảng chấm review sau khi mã đã xong; file này là
> luật lúc **đang viết**.
> Mỗi finding trong review trích được mã `R-xx` của file này.

---

## 1. Tài liệu trong mã (DOC)

| ID | Luật |
|---|---|
| R-01 | **Mỗi hàm, lớp, module có docstring 1–12 dòng**: nó làm gì, vì sao tồn tại, bất biến nó giữ, lỗi nó ném. Hàm một dòng hiển nhiên (`_meta_path`) thì một câu là đủ; hàm có luật nghiệp vụ thì ghi luật và trích điều khoản (`W23`, `K15`, `BE-00 §8`). |
| R-02 | Docstring **không kể lại code** (“gán x bằng y”). Viết cái người đọc không suy ra được từ thân hàm: lý do, ràng buộc, hệ quả, cạm bẫy. |
| R-03 | Comment trong thân hàm giải thích **tại sao**, không phải **cái gì**. Comment đứng ngay trên đoạn nó nói tới. |
| R-04 | Định danh viết tiếng Anh; docstring, comment và chuỗi cho người dùng viết tiếng Việt (BE-00 §10). |
| R-05 | Quyết định lệch chuẩn (dùng API riêng của thư viện, thuật toán chậm có chủ ý, giới hạn đã biết) phải có comment nêu **ngưỡng** và **đường nâng cấp**. |

## 2. Sạch và không trùng lặp (CLEAN)

| ID | Luật |
|---|---|
| R-06 | **Tìm trước khi viết.** `grep` hàm/hằng/fixture đã có trong `packages/`, `apps/` rồi mới viết mới. Viết lại thứ đã có là lỗi review. |
| R-07 | Cùng một logic xuất hiện **lần thứ hai** → tách thành hàm dùng chung ngay lần đó, đặt ở module sở hữu đúng theo BE-00 §2.2. Chép–dán là lỗi. |
| R-08 | Hàm ≤ 50 dòng, lồng ≤ 3 cấp, cyclomatic ≤ 10. Vượt thì tách, không “chú thích cho dễ đọc”. |
| R-09 | Một hàm làm một việc; hàm trả về hai kiểu nghĩa khác nhau thì tách hai hàm. |
| R-10 | **YAGNI.** Không interface cho một cài đặt, không factory cho một sản phẩm, không tham số cấu hình cho giá trị không bao giờ đổi, không mã “để dành”. Cổng hai bộ điều hợp (`ObjectStorage`) là ngoại lệ đã ghi trong hiến chương. |
| R-11 | Xoá hơn thêm: mã chết, flag hết hạn, nhánh không ai gọi → xoá trong cùng PR phát hiện. |
| R-12 | Không tạo file cấu hình công cụ riêng, không `conftest.py` lồng, không hạ ngưỡng cổng (K24). |

## 3. Đúng từ đầu, phủ hết case (CORRECT)

| ID | Luật |
|---|---|
| R-13 | Trước khi viết, liệt kê case: **thường · biên · lỗi · đồng thời**. Biên tối thiểu: rỗng, 0, âm, 1 phần tử, chạm trần, vượt trần, Unicode NFD, giá trị `None`. |
| R-14 | Mọi nhánh mã viết ra phải có test chạy qua nó. Ngưỡng 90% dòng **và** 90% nhánh là **sàn**, không phải đích; nhánh không test được thì đừng viết nhánh đó. |
| R-15 | Test có cả **negative case** (input xấu, thiếu quyền, phụ thuộc hỏng), không chỉ đường thường. |
| R-16 | Không nuốt ngoại lệ. Phân biệt lỗi **thử lại được** (503, timeout) và lỗi của người gọi (4xx); lỗi lạ để nó nổi lên. |
| R-17 | Validate ở **biên tin cậy** (HTTP, hàng đợi, tệp, khoá object): kiểu, độ dài, dải, mẫu. Mặc định **fail-closed**. |
| R-18 | Kiểu dữ liệu đúng ngay lần đầu: tiền dùng `Decimal`/số nguyên đơn vị nhỏ nhất, thời gian dùng `datetime` có múi giờ qua `Clock` tiêm được, độ dài mm số nguyên, chuỗi lưu NFC (K20). |
| R-19 | Sửa lỗi là sửa **gốc**: `grep` mọi caller của hàm sắp sửa, vá ở chỗ mọi đường đi qua, không vá riêng đường mà ticket nhắc. |

## 4. Tối ưu ngay lúc viết (PERF)

| ID | Luật |
|---|---|
| R-20 | Không query/gọi API trong vòng lặp (N+1). Gom bằng `IN (...)`, `JOIN`, hoặc một lượt tải trước. Chỗ buộc phải lặp (việc nền) ghi comment nêu lý do. |
| R-21 | Không `SELECT` không `LIMIT`; danh sách mới luôn phân trang theo cursor (W22). Query mới phải có index tương ứng, ghi trong migration. |
| R-22 | Không nạp cả tệp/kết quả vào RAM: đọc và ghi theo khúc (stream), có trần byte. |
| R-23 | Trong `async`: không gọi hàm chặn (đĩa, CPU nặng, client đồng bộ). Bọc bằng `asyncio.to_thread` hoặc executor có trần (K36). |
| R-24 | **Mọi** lời gọi ra ngoài (HTTP, DB, Redis, S3, SMTP) có timeout kết nối và đọc tường minh; không dựa vào mặc định của thư viện. Thử lại chỉ cho thao tác idempotent, có trần lần và backoff. |
| R-25 | Chọn cấu trúc dữ liệu đúng độ phức tạp: không O(n²) trên dữ liệu người dùng quyết định kích thước; không regex có thể ReDoS. |
| R-26 | Cache phải có TTL và giới hạn; khoá nhạy cảm đặt ở kho an toàn, không ở cache bị đẩy ra khi đầy (BE-00 §11). |

## 5. Ranh giới và bàn giao (BOUNDARY)

| ID | Luật |
|---|---|
| R-27 | Chỉ sửa file trong cột **Sở hữu** của prompt mình. Cần sửa chỗ khác → ghi “Nợ và việc chưa làm” (K27). |
| R-28 | Giữ ranh giới import của BE-00 §2.1; không nhập `packages.testing` từ mã không phải test. |
| R-29 | Mock/fake đúng tầng (K23, luật lai 2026-09-29): **dịch vụ thật** cho SQL, constraint, khoá, tranh chấp, idempotency, after-commit, migration, hợp đồng/golden và case khẳng định trạng thái đã ghi; **được fake** Postgres/Redis/storage khi kiểm logic phía trên (nhánh, validate, mã lỗi, ánh xạ). Mỗi op/task ≥ 1 test đường chính với dịch vụ thật. Không SQLite. Test chạy được song song (BE-00 §12). Không tải mạng trong test. |
| R-30 | Bí mật không vào log, không vào URL, không hardcode; so sánh token/MAC bằng `hmac.compare_digest`. |
| R-31 | Ghi bền vững trong chính request đó (không “lưu giả”, K22); xếp job **sau** commit (`on_after_commit`, K17). |
| R-32 | Migration theo expand → migrate → contract; có đường lùi đã chạy thử (BE-00 §6.1). |
| R-33 | Trước khi báo xong: `bash tools/verify/run.sh verify --fast` thoát 0 (chỉ test bị ảnh hưởng bởi phần vừa sửa, R-33b), bảng cổng in trạng thái từ mã thoát thật (K25), commit theo Conventional Commits + trailer `Prompt:`. |
| R-33b | **Chỉ chạy test của phần vừa sửa; không chạy cổng đầy đủ trên máy** (chủ dự án quyết 2026-11-25, thay bảng điều kiện đầy đủ/đích cũ). Trên máy: `run.sh verify --fast` (ruff, lint-imports, mypy, pytest các tệp test bị ảnh hưởng theo đồ thị import, độ phủ đơn vị bị chạm, bước 6/7/8 khi đầu vào đổi), cần hẹp hơn thì `verify --steps …` hay pytest đích qua `run.sh shell`. **Cổng đầy đủ là workflow `CI` của GitHub Actions**: đẩy nhánh, mở PR vào `main`, đợi mọi job xanh trên đúng SHA đầu nhánh; sau khi gộp, lượt `push` trên `main` là verify tích hợp. Báo cáo và phán quyết ghi `Phạm vi kiểm: fast + CI — <đường dẫn lượt CI>`; không chép kết quả của SHA khác (K25). CI đỏ → sửa rồi đẩy lại, tái hiện trên máy bằng test đích của job hỏng. `run.sh verify` đầy đủ trên máy chỉ khi chủ dự án yêu cầu, hoặc khi một job CI đỏ không tái hiện được bằng cổng nhanh và test đích. Lý do: lượt đầy đủ trên máy ~17 phút, giữ khoá cổng của cả máy, trong khi CI chạy đúng những bước đó mà không chiếm máy. |

## 6. Nợ, merge và review (FLOW)

| ID | Luật |
|---|---|
| R-34 | **Nợ phải có dòng trong `changes/<mã prompt>.md` (mục "Nợ và việc chưa làm") trước khi phiên kết thúc** — mỗi nợ một dòng `NO-<nnn>`, đủ: nợ là gì, nguyên nhân gốc (hoặc "chưa xác định"), chủ sở hữu file, mức theo `docs/RULE.md`, trạng thái. Mã `NO-` tăng dần toàn repo, không dùng lại: mã kế tiếp = số lớn nhất `git grep -ohE 'NO-[0-9]+' -- changes/ | sort -t- -k2 -n | tail -1` cộng một. Báo cáo cuối phiên chỉ trích id, không kể lại. Nợ đã sửa hay đã chấp nhận thì đổi trạng thái, **không xoá dòng**. |
| R-35 | **Xong task mà còn nợ mở → giao sub-agent** tìm nguyên nhân gốc rồi sửa, ngay trong phiên đó. Sub-agent phải **tái hiện được** lỗi trước khi sửa; không tái hiện được thì ghi vào `changes/<mã prompt>.md` số lượt đã chạy và giả thuyết nào đã bị loại trừ bằng bằng chứng gì — **cấm sửa mò**, cấm nới assert, cấm `skip`/`xfail`/retry để giấu (K24). |
| R-36 | Không viết thẳng lên `main`. Mọi việc đi trên nhánh `<loại>/<mã>-<mô tả>`, vào `main` bằng **merge có review**. Gộp giữ được trailer `Prompt:` của **mọi** prompt trong nhánh: một prompt thì squash, nhiều prompt (tính năng + FIX cho prompt khác) thì `--no-ff`. **Ngoại lệ hẹp:** nhật ký quản trị — file phán quyết `docs/reviews/*` — được commit thẳng lên `main` vì không phải mã, không qua cổng nào; mọi thứ khác (kể cả `changes/*.md`, tài liệu trong mã) vẫn phải đi nhánh. |
| R-36b | Khối trailer là **đoạn cuối cùng** của thân commit: có **một dòng trống ngay trước** nó và **không** dòng trống nào **bên trong** (`Prompt:`, `Fix:`, `Co-Authored-By:` sát nhau). Sai một trong hai thì `git log --format='%(trailers:key=Prompt)'` trả rỗng và mất đường tra chủ sở hữu. Kiểm sau khi commit: `git log -1 --format='%(trailers:key=Prompt,valueonly)'` phải in ra mã prompt. |
| R-37 | **Mọi merge phải qua skill `/merge-review` chạy ở một phiên riêng**, không phải phiên đã viết mã. Phán quyết lưu ở `docs/reviews/<ngày>-<nhánh>.md`. **Tuyệt đối không merge** khi phán quyết là `REQUEST CHANGES` hay `REJECT`, kể cả khi cổng xanh. Sửa theo finding rồi xin review lại. |
| R-38 | Nợ do review chỉ ra được ghi `changes/<mã prompt>.md` **trước** khi merge; nợ mức P0/P1 chặn merge, không có ngoại lệ (`docs/RULE.md` §5). |

---

## 7. Tự kiểm trước khi commit

- [ ] Mỗi hàm mới có docstring 1–12 dòng đúng R-01, R-02.
- [ ] Không đoạn nào lặp lần thứ hai mà chưa tách (R-07).
- [ ] Đã `grep` để chắc không viết lại thứ đã có (R-06).
- [ ] Danh sách case thường/biên/lỗi/đồng thời đã có test tương ứng (R-13…R-15).
- [ ] Mọi lời gọi ra ngoài có timeout; không chặn vòng sự kiện; không N+1 (R-20…R-24).
- [ ] `verify --fast` xanh và CI của PR xanh, không `pragma`, không `type: ignore` trần, không hạ ngưỡng (R-12, R-33).
- [ ] Mọi nợ còn lại đã có dòng trong `changes/<mã prompt>.md`; nợ mở đã giao sub-agent (R-34, R-35).
- [ ] Merge: nhánh riêng + `/merge-review` ở phiên khác đã `APPROVE` (R-36, R-37).
