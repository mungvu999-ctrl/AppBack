# Review merge feature/m15-library-cursor → main (PR #64)

- Ngày: 2026-11-12 · Reviewer: phiên /merge-review (độc lập, không viết mã nhánh) · Commit đầu nhánh: 29cb7a081360 (= `origin/feature/m15-library-cursor` = `headRefOid` của PR) · base `origin/main` f317c17ec014
- Phạm vi diff: 9 commit, 18 tệp, +978/−24. Mã logic ~300 dòng (`pack.py` 127, `service.py` +73, `router.py` +61, `cli.py` +35), test ~490 dòng, phần còn lại là tài liệu/compose/openapi. Dưới 400 dòng logic nên không có MNT-05.
- Cây làm việc: `git status --porcelain` rỗng; HEAD trùng nhánh trên remote, không có commit chưa đẩy.
- Cổng: phạm vi **đầy đủ**, vì đây là lượt 1 của nhánh (R-33b đk 1) và diff chạm `deploy/**`, `docs/charter/*`, `docs/contracts/**` (đk 2), thêm `.py` mới (đk 3), đổi route #14 (đk 5). Theo chỉ đạo, không chạy cổng local. Bằng chứng lấy từ CI GitHub của PR trên HEAD 29cb7a08:
  - Đã **pass**: `changes`, `commits`, `lint`, `typecheck`, CodeQL `analyze (actions)`, `analyze (python)`, `CodeQL`, `ml-shards (2)`.
  - **Còn chờ** lúc chốt phán quyết: `unit-shards (1,2)`, `integration-shards (1–5)`, `ml-shards (1,3)`, `perf`, `build`, cùng các job chạy sau shard: `unit`, `integration`, `ml`, `contract` (bước 7–8, openapi), `coverage`.
- Độ phủ: **chưa có số**. Job `coverage` chưa chạy, nên không ghi số của lượt nào khác (K25). Điều kiện là `apps/api/library` và tổng đều đạt ≥ 90 % dòng và ≥ 90 % nhánh trong job `coverage` của run trên 29cb7a08.
- Luật chấm: `RULE.md` và `RULE-CODE.md` lấy theo bản `6b2e4d52^` vì đã bị xoá khỏi repo, giống phán quyết hồi tố #63. Không còn `DEBT.md`, nên nợ được ghi trong `changes/M15-LIBRARY.md`.

## Kiểm theo điều kiện dừng sớm (skill §2)

| Hạng mục | Cách kiểm | Kết quả |
|---|---|---|
| `changes/<mã>.md` | diff | Có `changes/M15-LIBRARY.md`, kèm `changes/M13-CATALOG.md` bổ sung cho #63 |
| Conventional Commits + `Prompt:` | `git log --format='%(trailers:key=Prompt,valueonly)'` | 9/9 commit đúng mẫu `feat/fix/docs(scope): …`, 9/9 in ra `M15-LIBRARY` (R-36b đạt); job CI `commits` pass |
| `docs/charter/*` | `git show --stat` từng commit | 99859965 và 73f6a0c7 chỉ chạm `docs/charter/BE-BIND.md`, `HOP-DONG-MOI.md`, `changes/M15-LIBRARY.md`; không có `.py` hay `tools/**`. Cả hai mang trailer `Charter-Approved: changes/M15-LIBRARY.md (người dùng duyệt 2026-11-12)`. Tệp này chép nguyên văn câu chữ đã duyệt, khớp với bản gốc `F:/runs/m15/charter-14-library-cursor.md`. Dòng HOP-DONG-MOI khớp câu chữ duyệt. BE-BIND hàng 14 đủ 10 cột như hàng 13/15. **Đạt ngoại lệ.** |
| `openapi.json` | diff | Không có `openapi.json` ở gốc. `docs/contracts/openapi.json` đổi trong commit riêng 94717c05, chỉ chạm tệp này: bổ sung 2 query `cursor`/`limit` (1..500), 2 header, 422. Đây là quy trình BE-00 dòng 475: người điều phối cập nhật bản tham chiếu trong chính lần gộp đổi API. Job `contract` (bước 8) sẽ so với bản xuất; **còn chờ**. |
| `APPFRONT_SHA`, `uv.lock` | diff | Không chạm |
| pragma / `type: ignore` / `noqa` / skip / xfail / hạ ngưỡng | `grep` trên các dòng `+` | Không có (lần trúng duy nhất là câu chữ trong phán quyết hồi tố #63) |

## Kiểm riêng theo yêu cầu

| Hạng mục | Kết quả |
|---|---|
| Cursor ký kèm user | `router.py:305,310-312`: `encode_cursor`/`decode_cursor(…, LIST_OP, {"user": principal.user_id})`, HMAC, so sánh `compare_digest` (`apps/api/core/pagination.py`). Test `__C15_cursor_invalid` thử cursor của người khác, cursor sửa vị trí giữ MAC cũ, và cursor bịa: cả ba trả 422 `CURSOR_INVALID`. Vị trí trong cursor chỉ được đọc sau khi MAC đã đạt. |
| Mục `mine` không lộ | Cả trang lẫn dấu phiên bản đều lọc bằng `visible_to(user_id)` (`service.py` `_revision_of`, `list_page`). Test `__C15_mine_only_for_owner`: người kia không thấy mục và dấu của họ không đổi; dấu của chủ khác dấu của người kia. Dấu là md5 của tập mục mà chính người gọi thấy, nên không mang thông tin của người khác. |
| Snapshot dấu và trang | Dấu và các dòng của trang nằm trong một câu SQL (subquery dấu `LEFT JOIN` trang), kể cả khi trang rỗng. `test_list_page_empty_page_one_statement` đếm được đúng 1 lượt execute. `__C15_retired_between_pages` và `__C15_revision_tracks_visible_set` cho thấy dấu đổi khi thêm/rút/đổi tên mục và giữ nguyên khi chỉ `verified_at`/`updated_at` đổi. |
| Tương thích ngược #14 | Khi không có tham số, route vẫn trả mảng trần tới `LIBRARY_LIST_MAX` như cũ, chỉ thêm header. `__C15_past_old_cap` dùng 2.150 mục: đi theo cursor đọc đủ, đúng thứ tự, không trùng; lượt không tham số dừng đúng ở trần và có cursor dẫn tiếp. FE cũ không vỡ. |
| Compose / migrate | `base.yml:117-122`: `[ -z "$${LIBRARY_PACK_DIR:-}" ] \|\| … import-files --strict`. Khi không có file chồng, migrate chạy như cũ. `library-pack.yml` gắn gói **chỉ đọc**, bắt buộc `LIBRARY_PACK_HOST_DIR`, chờ `minio-init`. `test_compose_migrate_imports_library_pack_only_when_mounted` khẳng định các điểm này. Stack cô lập (`F:/runs/m15/iso/migrate1.log`, `migrate2.log`): `published=978 skipped=0 failed=0` ở cả hai lượt; `check_before/after_recreate.json`: 978 mục, cùng thứ tự, 1 dấu, đọc được cả mục sau vị trí 500, đủ 95 mẫu mới. |
| An toàn dữ liệu (gói) | `build_pack` kiểm hết rồi mới chép; chép vào thư mục tạm, kiểm lại sha, rồi đổi tên. Lượt hỏng giữ nguyên `out` cũ (test cho đĩa đầy, bản chép lệch, đổi tên lần hai hỏng, khôi phục lượt bị cắt giữa hai lần đổi tên). Không có DB/migration mới. |
| Codex | BE-1 có 4 finding. BE-2 kết luận #2/#3/#4 đã đóng và chỉ còn #1 (mất `out` khi đổi tên lần hai hỏng hoặc bị cắt; tên bản lưu theo pid). Commit 29cb7a08 sửa đúng ba ý: trả bản cũ về, đặt tên bằng `uuid4`, `_recover` khi bắt đầu lượt, cộng 3 test mới. Tôi đã đọc lại 29cb7a08 và không thấy lỗi mới. |
| Hiệu năng | `iso/bench_container.txt`: không tham số 0,39 s (1,37 MB), `limit=500` 0,21 s, `limit=1` 12 ms. Chi phí dấu (string_agg trên ~1.000 dòng) không đáng kể. Bản đo từ host qua `localhost` cộng thêm ~2 s mỗi lượt do phân giải `::1`, không phải lỗi của nhánh (`bench_host_127.txt` bình thường). |

## Finding

| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | RES-xx / OPS | Khi dùng `library-pack.yml`, `import-files --strict` trở thành điều kiện để `api`/`worker` khởi động: `dev.yml:98-100,120-122,140-142` chờ `migrate` `service_completed_successfully`. Nếu MinIO trục trặc một lúc trong lúc `put` (`failed>0`), hoặc `files.json` có bản mới mà gói trên máy chủ chưa dựng lại, thì cả API không lên, chỉ vì một bước nạp nội dung thư viện. Cố ý báo lỗi to là đúng ý đồ (Codex BE-2 đồng ý) và file chồng là tuỳ chọn, nhưng SETUP §4.1 bước 2 chỉ ghi "`migrate` thất bại", không nói hệ quả là API không khởi động. | `deploy/compose/base.yml:122`, `docs/SETUP.md:131` | Thêm một câu vào SETUP: "migrate hỏng ⇒ api/worker không khởi động; nâng `files.json` ⇒ dựng gói mới trước khi `up`". Nếu muốn tách hẳn, chuyển bước nạp sang một service một lượt `library-import` mà `api` không phụ thuộc. |
| 2 | P3 | OPS | `_publish` chỉ cảnh báo khi không xoá được bản cũ, và `_recover` chỉ dọn khi `out` vắng. Vì vậy các bản `.<out>.old-<uuid>` (mỗi bản ~900 MB) cộng dồn qua mỗi lần dựng lại mà không lượt nào dọn. Chính test cũng khẳng định còn 2 bản. | `apps/api/library/pack.py:97-127` | Khi `out` đã có, xoá thử các `.old-*` cũ (bỏ qua lỗi) ở đầu `build_pack`, hoặc in đường dẫn bản sót ra stderr của CLI để người vận hành dọn. |
| 3 | P3 | R-36 / MNT | Hai commit hồi tố cho #63 (18ca31fc `changes/M13-CATALOG.md`, 33cd7dcb phán quyết #63) mang `Prompt: M15-LIBRARY`, nên `git log --grep "Prompt: M13"` vẫn không tìm ra tệp changes của M13. Ngoài ra `docs/reviews/*` được phép commit thẳng lên `main` (ngoại lệ R-36), không cần đi nhánh tính năng. | commit 18ca31fc, 33cd7dcb | Khi squash, thêm một dòng `Prompt: M13-CATALOG` vào thân commit gộp (nhiều prompt thì dùng `--no-ff` theo R-36), hoặc chấp nhận và ghi rõ trong `changes/M15-LIBRARY.md`. |
| 4 | Nit | R-07 | Hằng `NEXT_CURSOR_HEADER = "X-Next-Cursor"` khai trùng với `apps/api/notifications/router.py:29`. | `apps/api/library/router.py:23` | Đưa lên `apps/api/core/pagination.py` khi có route thứ ba dùng. |
| 5 | Nit | MNT | Dòng chú thích 241 byte trong YAML (ba ý gộp vào một dòng). | `deploy/compose/library-pack.yml:8` | Tách thành 2–3 dòng. |

Ngoài diff (không tính điểm): NO-436 (P1 quy trình, PR #63 merge không qua review) vẫn mở và chờ người dùng ghi waiver dưới `docs/reviews/2026-11-12-feature-m14-catalog.md`. Nợ này thuộc #63, không thuộc diff này. Nhánh này còn mang phần khắc phục của nó (`changes/M13-CATALOG.md`), nên chặn #64 vì NO-436 sẽ chặn luôn việc khắc phục. Vì vậy tôi **không** tính NO-436 là điều kiện chặn #64 theo R-38. NO-437 (P2, ghi công CC BY) có trong `changes/M15-LIBRARY.md` như yêu cầu R-34.

## Điểm

| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25 % | 5 | 1,25 |
| CON | 15 % | 5 | 0,75 |
| LOG | 15 % | 5 | 0,75 |
| PERF | 10 % | 5 | 0,50 |
| RES (#1) | 10 % | 4 | 0,40 |
| DB, API | 10 % | 5 | 0,50 |
| TEST | 7 % | 5 | 0,35 |
| OBS, OPS (#2) | 5 % | 4 | 0,20 |
| MNT (#3–#5) | 3 % | 4 | 0,12 |

Tổng: **4,82 / 5**

## PHÁN QUYẾT: APPROVE (có điều kiện CI)

**APPROVE nếu CI xanh trên đúng HEAD `29cb7a081360b62c527f95cc78a7a53b5dc00f5c`**. Cụ thể, mọi check còn chờ phải `pass`: `unit-shards (1,2)`, `integration-shards (1–5)`, `ml-shards (1,3)`, `unit`, `integration`, `ml`, `perf`, `contract`, `build`, `coverage`. Riêng job `coverage` phải cho `apps/api/library` và tổng ≥ 90 % dòng và ≥ 90 % nhánh. Nếu có check đỏ, phán quyết chuyển thành **REQUEST CHANGES**, kèm bước hỏng lấy từ `gh run view <id> --log-failed`. Nếu HEAD đổi, phải review lại phần diff mới.

Lý do: không có P0/P1. Cursor ký kèm user và đã có test chống dùng chéo hay giả mạo. Mục `mine` không lộ, ở cả trang lẫn dấu. Dấu và trang lấy cùng một snapshot. Hợp đồng #14 chỉ thêm, không phá (charter có `Charter-Approved` trỏ đúng câu chữ người dùng duyệt 12/11/2026). Gói mẫu dựng nguyên tử và không ghi đè gói tốt khi hỏng. Stack cô lập đã chứng minh nạp đủ 978 mẫu qua hai lần `migrate`/recreate. Ba P3 và hai Nit không chặn merge; nên ghi #1–#3 vào `changes/M15-LIBRARY.md` (mục nợ) hoặc sửa ở vòng sau. Cách gộp: squash nếu chỉ một prompt (R-36), hoặc giữ trailer M13 như #3.
