# Review merge feature/m14-catalog → main (HỒI TỐ, PR #63)

- Ngày: 2026-11-12 · Reviewer: phiên /merge-review (độc lập, hồi tố) · Commit đầu nhánh: 1ec29adb0bc3 · Merge: f317c17ec014 (đã merge 12/11 lúc 13:42, **trước** review này)
- Phạm vi diff: `git diff f317c17e^1 f317c17e` = một commit 1ec29adb, 5 tệp, +869/−4 (855 dòng là dữ liệu `files.json`, ~14 dòng mã/test)
- Cổng: phạm vi **đầy đủ** — lượt 1 của nhánh (R-33b đk 1), chạm `packages/domain/**` (sàn cứng, đk 2) và thêm tệp `.py` mới (đk 3). Không chạy cổng local theo chỉ đạo; bằng chứng là CI GitHub, mã thoát thật:
  - PR run `43166686556` (headSha 1ec29adb): `success`, 26 check đều `pass` (lint, typecheck, unit/integration/ml shards, coverage, contract, perf, build, commits, changes, CodeQL).
  - main push run `43170066454` (headSha f317c17e): `success`, 20 job `success`; Commits `43170066471` và CodeQL `43170066425` `success`.
- Độ phủ (CI job `coverage`, run 43170066454, cây f317c17e): tổng dòng 99,31 % · nhánh 97,50 % | `apps/api/library` 98,63 % / 97,62 % · `packages/domain` 99,75 % / 98,61 %.
- Luật chấm: `RULE.md`, `RULE-CODE.md`, `DEBT.md` đã bị xoá khỏi repo ở 6b2e4d52 (18/10, "docs(repo): remove…") trong khi skill và CLAUDE.md vẫn dẫn chúng; review dùng bản tại `6b2e4d52^`. BE-00 §13.2 vẫn còn trong `docs/charter/BE-00.md`.

## Kiểm riêng theo yêu cầu

| Hạng mục | Cách kiểm | Kết quả |
|---|---|---|
| 95 mục mới | so `files.json` hai phía | 883 → 978; 883 mục cũ giữ nguyên thứ tự và nội dung; 95 mục mới đủ 7 khoá, `license` = `CC-BY 4.0` cả 95, không `author` rỗng, không trùng id/sha/nguồn |
| Giấy phép ABO (78) | `source` + `F:/runs/m13/abo/items.jsonl` | 78/78 nguồn là `amazon-berkeley-objects.s3…/3dmodels/original/<X>/<ASIN>.glb`, mọi ASIN có trong metadata ABO; bộ ABO là CC BY 4.0 (đã xác minh 02/11); tác giả ghi "Amazon Berkeley Objects (thương hiệu …)" |
| Giấy phép Objaverse (17) | `F:/runs/m13/objaverse/beds.jsonl` | 17/17 uid có `"licence": "by"` (Sketchfab CC Attribution = CC BY 4.0), 17/17 tác giả trong manifest khớp tác giả Sketchfab, nguồn là trang Sketchfab của từng mẫu |
| Ghim sha256 | sha256 của `F:/runs/m13/libpack/<id>.glb` | **95/95 khớp**, 0 sai, 0 thiếu; `load_file_catalogue` kiểm định dạng 64 hex lúc nạp |
| Tệp cấm / pragma / skip | diff | không chạm `docs/charter/*`, `openapi.json`, `APPFRONT_SHA`, `uv.lock`; không pragma, `type: ignore`, `noqa`, skip/xfail, không hạ ngưỡng |
| Commit | `git show -s 1ec29adb` | `fix(library): …` đúng Conventional Commits; trailer `Prompt: M13-CATALOG` + `Co-Authored-By` sát nhau, có dòng trống trước (R-36b đạt) |
| Bằng chứng giấy phép trong repo | `git grep` | metadata giấy phép (`beds.jsonl`, `items.jsonl`) chỉ nằm ở `F:/runs/m13`, ngoài git — xem #7 |

## Finding

| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P1 | R-37 | Merge vào `main` không có phán quyết `/merge-review` APPROVE. Bằng chứng: `gh pr view 63` → `"reviews":[]`, merge bởi `mungvu999-ctrl` lúc 13:42; `docs/reviews/` không có tệp cho nhánh này (tệp gần nhất 04/11). R-37 ghi "tuyệt đối không merge" khi chưa có phán quyết. Không hoàn tác được; tác động: mã vào `main` không qua người gác. | merge f317c17e | **Không revert** (xem #4: hạ trần sau khi người dùng đã lưu mẫu xếp sau 500 sẽ làm hỏng mẫu đó). Người dùng ghi waiver hồi tố cho #1 ngay dưới phán quyết này; từ nay merge vào `main` chỉ sau tệp `docs/reviews/*` APPROVE. |
| 2 | P1 | BE-00 §13.2 (điều kiện dừng sớm §2) | Thiếu mảnh changelog `changes/M13-CATALOG.md` cho prompt bàn giao. Bằng chứng: `git show f317c17e:changes/M13-CATALOG.md` → "does not exist"; `ls changes` không có tệp M1x/CATALOG nào. BE-00 dòng 544: "Tạo nó trước lượt verify đầu tiên". Skill §2: thiếu tệp này → REJECT. Job CI `changes` (5 s) xanh nhưng không bắt được việc thiếu. | `changes/` | Thêm `changes/M13-CATALOG.md` (3–10 dòng: trần 500→2000 và lý do, +95 mẫu CC BY 4.0 với nguồn ABO/Objaverse, ghi chú rollback "không hạ trần") trên một nhánh `docs/…` (R-36: `changes/*` phải đi nhánh). |
| 3 | P3 | R-36 | Gộp bằng merge commit thay vì squash dù nhánh chỉ có một prompt. Bằng chứng: f317c17e có hai cha (d1deb23c, 1ec29adb). Trailer `Prompt:` chỉ nằm ở cha thứ hai, `git log --first-parent main --grep "Prompt: M13"` không thấy. Đây là thói quen chung của các PR gần đây (mọi merge first-parent đều là "Merge pull request #…"). | merge f317c17e | Đặt GitHub "Allow squash merging" làm mặc định cho PR một prompt, hoặc sửa R-36 cho khớp cách làm thực tế. |
| 4 | P2 | PERF-02 / PERF-09 / R-19 | Nâng trần là **giải pháp tạm**, chưa sửa gốc. Gốc lỗi: #14 trả cả danh sách trong một lần, cắt im lặng ở `LIMIT`, không có tín hiệu "còn nữa" cho FE. Bằng chứng: `settings.py:20` `library_list_max = 2000`; `service.py:71-79` một `SELECT … LIMIT 2000`, `_to_outs` ký `model` + `preview` cho từng dòng → ~2×994 ≈ 2.000 URL ký mỗi lần `GET /api/library`, payload tăng tuyến tính (chưa đo kích thước/p95). Danh mục tăng 883→978 chỉ trong một vòng; ở ~100 mục/vòng thì còn khoảng 10 vòng trước khi chạm 2000. Điểm cộng: `test_list_cap.py` làm CI đỏ **trước** khi cắt im lặng quay lại. PR body ghi đúng ràng buộc rollback (không được hạ trần) nhưng ràng buộc này không nằm trong mã hay sổ nợ. | `apps/api/library/settings.py:20`, `apps/api/library/service.py:71-79` | Chấp nhận làm tạm vì phân trang đang làm riêng theo hợp đồng chủ dự án đã duyệt (`F:/runs/m15/charter-14-library-cursor.md`). Ghi nợ P2 trỏ tới việc đó; khi phân trang xong thì giữ được mẫu đã lưu xếp sau vị trí 500 (ràng buộc rollback) và đo payload/p95 trước–sau. |
| 5 | P2 | G-07 (tuân thủ giấy phép) | Ghi công CC BY 4.0 chưa đủ. `FileItem.attribution` chỉ trả `"<tác giả> · CC-BY 4.0"`; FE (`ModelCreditCard.tsx`, AppFront origin/master) hiện đúng chuỗi đó: không có liên kết tới nguồn, không có liên kết giấy phép, không ghi đã sửa (manifest ghi sha của "tệp đã chuẩn hoá", tức là đã thay đổi). CC BY 4.0 §3(a)(1) đòi URI tới tác phẩm khi làm được, URI giấy phép và ghi chú đã sửa. Hành vi này có từ trước, nhưng diff đưa thêm 95 mẫu CC BY vào đúng đường này (17 tác giả cá nhân trên Sketchfab). | `packages/domain/library/files.py:33-43`; dữ liệu `files.json` +95 | Đưa `source` (và URL giấy phép) lên dây `LibraryItemOut`, FE hiện liên kết + "đã chỉnh sửa", hoặc làm một trang ghi công đầy đủ (kiểu `public/landing/LICENSES.md`) liệt kê mọi mẫu CC BY. |
| 6 | P3 | LOG-02 | `test_list_cap` chỉ đếm danh mục hệ thống (`CATALOGUE + FILE_CATALOGUE`), trong khi `visible_to` còn tính mục `mine` của chính người gọi (`owner_id == user_id`). Hiện chưa có đường nào ghi `owner_id` cho bảng thư viện (`git grep "owner_id="` chỉ thấy `apps/api/projects`), nên lỗi chưa xảy ra được; khi mở tải mẫu riêng, tổng có thể vượt trần mà không test nào báo. | `apps/api/library/tests/test_list_cap.py:8`, `apps/api/library/service.py:26-33` | Gộp vào việc phân trang (#4). Nếu phân trang trễ, chừa phần dư cho mục riêng (trần riêng mỗi người) trong test. |
| 7 | P3 | G-07 / MNT-04 | Bằng chứng giấy phép từng mục (metadata ABO và Objaverse kèm `licence`) chỉ nằm ở `F:/runs/m13/**`, ngoài git và chỉ có trên máy này. Manifest giữ `license` nhưng không giữ nguồn của khẳng định giấy phép (ví dụ phiên bản annotation Objaverse). | `packages/domain/library/files.json` | Commit một tệp metadata nhẹ (uid → licence → ngày lấy) cạnh manifest, hoặc thêm khoá `license_evidence` cho từng mục. |
| 8 | Nit | R-01 | Hàm test mới không có docstring (chỉ có docstring module); các test cùng gói (`test_files.py`) đều có. | `apps/api/library/tests/test_list_cap.py:7` | Thêm một câu docstring. |
| 9 | Nit | MNT-04 | Mã prompt `M13-CATALOG` lệch tên nhánh `feature/m14-catalog`; chuỗi giấy phép `CC-BY 4.0` không theo tên chuẩn `CC BY 4.0`. | commit 1ec29adb; `files.json` | Thống nhất mã vòng; chuẩn hoá chuỗi giấy phép khi làm #5. |

Ngoài diff (không tính điểm, báo người điều phối): 15 merge first-parent gần nhất trên `main`, kể từ sau 03/11, đều không mang tệp `changes/`, và `docs/reviews/` không có phán quyết nào sau 04/11. Sổ `DEBT.md` không còn trong repo, nên không ghi được "dòng nợ" mà ma trận R-34/§6 đòi cho các finding P2.

## Điểm

| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC (gồm tuân thủ giấy phép, #5 #7) | 25 % | 3 | 0,75 |
| CON | 15 % | 5 | 0,75 |
| LOG (#6) | 15 % | 4 | 0,60 |
| PERF (#4) | 10 % | 3 | 0,30 |
| RES | 10 % | 5 | 0,50 |
| DB, API | 10 % | 5 | 0,50 |
| TEST (#6, #8) | 7 % | 4 | 0,28 |
| OBS, OPS (quy trình, #1 #2) | 5 % | 1 | 0,05 |
| MNT (#3 #9) | 3 % | 4 | 0,12 |

Tổng: **3,85 / 5**

## PHÁN QUYẾT: REJECT (hồi tố)

REJECT theo điều kiện dừng sớm của skill §2 (thiếu `changes/M13-CATALOG.md`), cùng với P1 R-37 (merge không review), và chưa có waiver cho cả hai. Phần mã và dữ liệu thì đứng được: cổng CI xanh ở cả 1ec29adb lẫn f317c17e, 95/95 sha256 khớp tệp đã chuẩn hoá, 95/95 mục có giấy phép CC BY và tác giả kiểm được theo metadata nguồn. Nếu chỉ chấm mã thì được 3,85, tức **APPROVE WITH COMMENTS**. **Không revert**: hạ trần sẽ làm hỏng các mẫu đã lưu xếp sau vị trí 500.

Để đóng phán quyết này:
1. Thêm `changes/M13-CATALOG.md` trên một nhánh riêng rồi merge (#2).
2. Người dùng ghi waiver hồi tố cho #1 (và #3 nếu giữ cách merge commit) ngay dưới phán quyết này.
3. Ghi nợ P2 cho #4 (trỏ tới việc phân trang theo hợp đồng đã duyệt, kèm ràng buộc rollback) và #5 (ghi công đủ nguồn, giấy phép và ghi chú đã sửa) vào sổ nợ mà dự án đang dùng. `DEBT.md` đã bị xoá ở 6b2e4d52, nên phải khôi phục sổ hoặc chỉ ra nơi ghi thay.
4. #6–#9 không chặn; gộp vào việc phân trang (#6) hoặc việc ghi công (#7, #9).
