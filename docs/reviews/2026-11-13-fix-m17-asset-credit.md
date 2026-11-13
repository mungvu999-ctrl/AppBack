# Review merge fix/m17-asset-credit → main (PR #66)

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review (độc lập, không viết mã nhánh) · Commit đầu nhánh: 5d05be1d6fc2 (= `headRefOid` của PR) · base `origin/main`
- Phạm vi diff: 1 commit, 5 tệp, +64/−7. Logic ~20 dòng (`files.py`), dưới 400 nên không có MNT-05. Cây làm việc sạch, không có commit chưa đẩy.
- Phạm vi kiểm: đầy đủ (CI trên HEAD 5d05be1d6fc21b6392319695946f73d3f756051f) — local full gate forbidden by owner. Lượt 1 của nhánh nên theo R-33b là đầy đủ; không chạy `run.sh verify` cục bộ theo chỉ đạo của chủ dự án.
- Cổng: 24/24 job của PR (CI + CodeQL + Commits) `COMPLETED SUCCESS` trên đúng HEAD: `changes`, `commits`, `lint`, `typecheck`, `unit-shards (1,2)`, `integration-shards (1–5)`, `ml-shards (1–3)`, `perf`, `build`, `unit`, `integration`, `contract`, `ml`, `coverage`, CodeQL ×3 (run 43258704185). Chưa chạy `--fast` cục bộ (CI đã bao trùm).
- Độ phủ (job `coverage` trên HEAD): tổng dòng 99,31% · nhánh 97,52%; `packages/domain` dòng 99,75% · nhánh 98,62%. Cổng `coverage_gate` đạt.
- Luật chấm: `RULE.md`/`RULE-CODE.md` đã bị xoá khỏi repo ở 6b2e4d52 (TIDY-ROOT); lấy bản `6b2e4d52^` như các phán quyết 2026-11-12. `DEBT.md` cũng đã bỏ, nợ ghi trong `changes/`.

## Điều kiện dừng sớm (skill §2)
`changes/M17-CREDIT.md` có. Dòng đầu commit `fix(library): …` (≤72 ký tự), trailer `Prompt: M17-ROOM-FUNCTION` (khác mã tên tệp changes, xem Nit 3), job `commits` pass. Không chạm `docs/charter/*`, `openapi.json`, `APPFRONT_SHA`, `uv.lock`. Không `pragma`/`type: ignore`/`noqa`/skip/xfail trên dòng `+`. Không có điều kiện dừng.

## Kiểm nội dung đối chiếu finding #5 (CC BY 4.0 §3(a)(1))
Đã đọc `files.json` (978 mục: 879 `CC-BY 4.0`, 5 `CC-BY 3.0`, 7 `CC-BY` không bản, 87 `CC0`; 891 mục CC BY khớp hằng 891 trong test).
- Mọi mục CC BY đều có `author` (0 thiếu) và đúng ≥1 URL trong `source` (885 mục 1 URL, 6 mục 2 URL; 0 mục không URL). Nên chuỗi mới luôn có tác giả, giấy phép, URI nguồn, ghi chú sửa đổi. Tên gốc là tuỳ chọn (có ở 28 mục), đúng TASL "title if supplied".
- URI giấy phép: 4.0 → `…/licenses/by/4.0/`, 3.0 → `…/by/3.0/` (đúng). Ghi chú "đã chỉnh sửa" gắn cho mọi mục từ tệp; hợp lý vì manifest giữ sha của GLB đã chuẩn hoá.
- CC0 và giấy phép khác: nhánh `not startswith("CC-BY")` trả `"<ai> · <giấy phép>"` y như cũ; các case Poly Haven/Pascal/CC0 trong `test_attribution` giữ nguyên. Đạt.
- Hợp đồng: kiểu dây vẫn `string|null`, `openapi.json` không đổi (job `contract` xanh). FE phải tách `" · "` — PR đi cùng AppFront `fix/m17-asset-credit`; thứ tự merge FE trước/cùng lúc đã nêu trong thân PR (nếu BE vào trước, FE cũ chỉ hiện chuỗi dài, không lỗi).
- Test: tham số hoá 3 case CC BY (có tên gốc, nhiều URL + ký tự chặn `()`/`;`, bản lạ) và test quét 891 mục kiểm cuối chuỗi là ghi chú sửa đổi, có URL CC, có URL nguồn không phải CC. Không phụ thuộc thời gian/mạng, chạy song song được.

## 7 mục "Flat Archiviz" (CC-BY không ghi bản)
Không chặn merge. Trước PR này chúng cũng không có link nào; nay chúng được ghi tác giả, nguồn (URL tệp .blend gốc), "CC-BY", ghi chú sửa đổi, kèm liên kết trang danh mục giấy phép CC. Đây là cải thiện thuần so với main, chưa phải vi phạm mới. Phần còn thiếu (không chỉ tới một bản giấy phép cụ thể) là rủi ro tuân thủ nhỏ trên 7/891 mục (0,8%), tác giả đã tự nêu trong PR và `changes/M17-CREDIT.md`. Việc cần làm: xác minh bản giấy phép trên trang tải của Blender, rồi thêm khoá vào `_LICENSE_URLS` (hoặc sửa `license` trong manifest). Ghi thành nợ có mã (Finding 1).

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | LOG-01 | 7 mục `CC-BY` không bản trỏ `https://creativecommons.org/licenses/` (trang danh mục), không phải URI giấy phép cụ thể; TASL cần chỉ đúng giấy phép. Nợ mới chỉ được nêu một dòng trong changes, chưa có mã `NO-nnn` (R-34) | `packages/domain/library/files.py:20-24`, `changes/M17-CREDIT.md:8` | Gán `NO-438` (P2, mở) trong `changes/M17-CREDIT.md`; xác minh bản của "Flat Archiviz" rồi đổi khoá `CC-BY` trong `_LICENSE_URLS` hoặc `license` trong `files.json`; thêm test cho 7 mục này |
| 2 | P3 | MNT-01 | Chuỗi gộp bằng `" · "` mà tên gốc/tác giả có thể chứa `" · "` (hiện chưa mục nào chứa, test không chặn tương lai), FE tách ngược theo ký tự đó: hợp đồng ngầm | `files.py` (`attribution`), test quét 891 mục | Thêm vào test quét: không phần tử nào trước URL chứa `" · "`, hoặc về sau trả mảng có kiểu |
| 3 | Nit | R-36b | Trailer `Prompt: M17-ROOM-FUNCTION` còn tên tệp changes là `M17-CREDIT.md` | commit 5d05be1d | Squash khi merge: đặt trailer đúng mã nhánh nếu có |
| 4 | Nit | MNT | `changes/M15-LIBRARY.md` sửa dòng NO-437 thành "đóng" trước khi PR vào main; NO-436 vẫn mở, không liên quan | `changes/M15-LIBRARY.md:14` | Không cần làm |

Không có P0/P1.

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 3 (P2 #1) | 0,45 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 5 | 0,50 |
| DB, API | 10% | 5 | 0,50 |
| TEST | 7% | 5 | 0,35 |
| OBS, OPS | 5% | 5 | 0,25 |
| MNT | 3% | 4 (P3 #2) | 0,12 |
Tổng: 4,67 / 5

## PHÁN QUYẾT: APPROVE
Diff nhỏ, đúng gốc (một nơi dựng chuỗi ghi công cho cả 891 mục), đáp ứng finding #5 của phán quyết M14 (URI nguồn, URI giấy phép, ghi chú sửa đổi) cho mọi mục CC BY có bản giấy phép xác định, và CC0 không đổi. CI trên HEAD 5d05be1d xanh toàn bộ, độ phủ 99,31%/97,52%. Điều kiện khi merge: gán mã nợ `NO-438` cho 7 mục "Flat Archiviz" (Finding 1, không chặn) và merge AppFront `fix/m17-asset-credit` trước hoặc cùng lúc. Squash merge theo quy ước; việc merge thuộc phiên gọi.
