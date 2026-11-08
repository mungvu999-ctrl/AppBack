# Review merge feature/m1-01-model-export → main (lượt 2)

- Ngày: 2026-11-08 · Reviewer: phiên /merge-review (độc lập, không phải tác giả) · Commit đầu nhánh: 0419fd30baaf · base origin/main d1deb23c0294 · vòng sửa `99de3a8c..0419fd30` (14 commit)
- Lượt 1: `docs/reviews/2026-11-08-feature-m1-01-model-export.md` (REJECT).
- Cổng: phạm vi **đầy đủ** — vòng sửa chạm `tools/**` (`charter.py`, `case_gate.py`, `contract/check.py`), `docs/contracts/openapi.json`, `cases.toml` và thêm tên test `test_<op>__<case>` mới (`__pruned_cache`, `__concurrent_supersede`, `__superseded_snapshot`) → R-33b điều kiện (2), (5). `bash tools/verify/run.sh verify` mã thoát **0** (log: `F:/runs/m1/gates/be-full-0811-r3.log`; log trong container `.cache/src-out/verify/20261108T120128Z-0419fd30baaf.log` → **đúng HEAD 0419fd30**; người điều phối chạy, reviewer đọc lại bảng bước 0–8 và dòng `mã thoát: 0`). Lượt r2 cùng HEAD hỏng 1 test `apps/worker/pipeline_steps/tests/test_sweep_rules.py::test_sweep_resends_remaining_families_when_walls_used` (dòng 425 của `be-full-0811-r2.log`); nhánh không chạm `apps/worker/pipeline_steps` (`git diff --stat origin/main...HEAD -- apps/worker/pipeline_steps` rỗng) — không tính cho nhánh này. Reviewer **không** chạy `verify --fast`: lúc review đang có container `appback-verify-m2-ql-verify-run-7218f019752c` (cổng của worktree khác).
- Độ phủ (cổng đầy đủ @ 0419fd30): tổng dòng 99,32 % · nhánh 97,52 %; tập file bị chạm 99,48 % / 97,95 %. Gói chạm: `apps/api/model_export` 99,44/91,67 · `apps/worker/model_export` 96,88/97,14 · `tools` 98,77/96,49 · `packages/domain` 99,83/98,67 · các gói chạm khác ≥ 95,90 nhánh. Case gate 5b: N49–N51, `run_model_export`, `sweep_model_exports` đạt (N49 có C14).
- Nguồn luật: `RULE.md`, `RULE-CODE.md` đọc ở `6b2e4d52^` (đã xoá khỏi repo ở `6b2e4d52`); sổ nợ trong `changes/M1-01.md` (tiền lệ AID-01).

## Phạm vi và điều kiện dừng sớm (§2)

- Cây sạch. 14 commit vòng sửa: dòng đầu đúng mẫu, cả 14 có `Prompt: M1-01`. Không `pragma`, `type: ignore` trần, `noqa` trần, `skip`/`xfail` mới (grep dòng `+` của `git diff 99de3a8c...HEAD`: rỗng).
- `docs/charter/*`: `git diff origin/main HEAD -- docs/charter/` **rỗng** → diff nhánh không còn chạm tệp cấm; điều kiện dừng sớm của lượt 1 hết. Lịch sử nhánh vẫn còn `f4ae5ac6` (charter + `tools/**`, không `Charter-Approved:`) và `031ba618` (trả về bản `main`) → chỉ được merge squash (Nit 6).
- `openapi.json`, `APPFRONT_SHA`: người điều phối sinh/ghim trong nhánh (tiền lệ LB-01/AID-01) — đạt thủ tục; nội dung xem bảng dưới.

## Soát lại finding lượt 1

| # lượt 1 | Trạng thái | Bằng chứng |
|---|---|---|
| 1 P0 charter | **Tách xong, chưa duyệt** | Charter nhánh = `main`. Câu chữ chuyển sang `docs/reviews/pending/charter-M1-01.md` (tự ghi "CHƯA DUYỆT"). Chủ dự án chưa duyệt; NO-M1-9 vẫn **[merge] mở**. Shim cổng thay vào → finding 1 mới. |
| 2 P1 openapi | **Đã sửa** | `e2729967` thêm `enum` cho `severity`. Bản `run.sh openapi` lúc 15:15 (`.cache/src-out/openapi/openapi.json`) so JSON với `docs/contracts/openapi.json` @ HEAD → bằng nhau; sau `e2729967` không commit nào chạm `apps/**/schemas.py`, `apps/**/router.py`, `apps/api/core`, `packages/core`. Không chạy `--compare` tại chỗ (cổng worktree khác đang giữ máy). |
| 3 P1 APPFRONT_SHA | **Sửa một nửa** | `6fbff729`: `git branch -r --contains` → chỉ `origin/feature/m1-01-model-export`; `merge-base --is-ancestor 6fbff729 origin/master` → 1 (13 commit chưa vào `master`); `origin/master` (`a2238c11`) là tổ tiên của `6fbff729` → 0. CI lấy được (checkout theo SHA từ remote), nhưng vẫn ghim FE chưa merge → finding 2. |
| 4 P2 cache đã dọn | **Đã sửa** | `service.py:135-138`: dòng `done` có `report_summary.pruned` → `_RETIRE` sang `superseded` (ra khỏi chỉ mục partial) rồi mở lượt mới; test `test_model_export_start_export__pruned_cache` (6 lượt done, quét, N49 → 202, lượt cũ `superseded`). |
| 5 P2 MNT-05 | Giữ nguyên | Lượt 1 đã chấp nhận không tách (squash). |
| 6 P2 `shapely` §13.1 | **Chuyển vào bản chờ duyệt** | `charter-M1-01.md` §4. Theo finding 1. |
| 7 P3 snapshot của lượt bị thay | **Đã sửa** | `jobs.py:59-66,119-121`: `_CLEAN_SUPERSEDED` dọn `superseded` quá trần `running`, đánh dấu `pruned` để không dọn lặp; test `test_sweep_model_exports__superseded_snapshot` (lượt cũ dọn, lượt mới giữ, lượt quét 2 = 0). |
| 8 P3 CON hai N49 khác dấu vân | **Đã sửa** | `service.py:132` `lock_project_scope` (`pg_advisory_xact_lock`) trước ảnh chụp; bỏ nhánh `IntegrityError` (mọi N49 của dự án tuần tự). Test `__concurrent_supersede` (hai yêu cầu song song khác dấu vân → `queued` + `superseded`). |
| 9 P3 `levelName` | **Chuyển vào bản chờ duyệt** | `charter-M1-01.md` §4. |
| 10 Nit băm mỗi lượt | **Đã sửa** | `service.py:99-102` `@functools.cache _reader_hash`. |

Phần khác của vòng sửa đã soát, không thành finding: `assets.py` đọc `sizeMm {w,d,h}` (gói `F:/runs/m1/fix-fe-r1/pack`: 84/84 mẫu dùng `{w,d,h}`, ví dụ `double-bed` `{w:1520, d:2000, h:710}`, khớp trục x/y/cao của `target`), `{x,y,z}` cũ vẫn đọc + log; `CREDITS.txt` nối nguyên văn MIT khi có mẫu MIT được dựng và gói có `LICENSE-PASCAL-MIT.txt` (có trong `manifest.json` của gói r1), có test hai chiều; mặc định gói `m1-01-pack-r1`, bước nạp gói ghi trong `changes/M1-01.md`.

## Shim `tools.charter.pending_bind_rows()` — trả lời theo luật

**Câu hỏi:** cổng (bước 7, 5b) đọc dòng N49–N51 từ `docs/reviews/pending/charter-*.md` khi chủ dự án **chưa** duyệt — có chấp nhận được không?

**Trả lời:** chấp nhận được **chỉ như công cụ trong nhánh** trong lúc chờ duyệt (giữ cho H1/H3/case gate vẫn kiểm N49–N51 thay vì bỏ test). **Không được vào `main`.** Lý do theo luật:

1. BE-00 §2: `docs/charter/*` là tệp chung chỉ người điều phối sửa, và đổi charter phải có bản duyệt của chủ dự án (skill §2, `Charter-Approved:`). Cổng bước 7 kiểm "thao tác đã mount ⊆ BE-BIND" chính là để không endpoint nào lọt vào mà không có dòng charter (K06). Shim thêm một nguồn thứ hai — một thư mục **không** nằm trong danh sách tệp chung, prompt nào cũng sửa được — có hiệu lực **y như** BE-BIND: `check.py:98` gộp dòng chờ vào `contract_rows`, `case_gate.py:549` gộp vào `bind_rows`, không đánh dấu, không cảnh báo. Cổng xanh vì nguồn luật được nới, không phải vì charter cho phép → K24 ("qua cổng bằng cách né cổng") / R-12.
2. **Dòng chưa duyệt có pass như dòng đã duyệt không? Có.** Sau merge, `main` có 3 route N49–N51 đang mount, không dòng BE-BIND, và cổng `main` vẫn xanh. Không có cơ chế nào làm cổng `main` đỏ khi `docs/reviews/pending/` còn tệp.
3. `check.py:47-48` có `model_export_download_file` trong `H1_EXEMPT` trong khi `BE-00.md:474` (bản `main`, nhánh giữ nguyên) liệt kê miễn H1 **đóng** không có N51; docstring ngay dưới (`check.py:50`) ghi "thêm tên vào đây là trái hiến chương". Tương tự `case_gate.py:35` `INFRA_OPS`.
4. Tác giả tự xếp NO-M1-9 là **[merge]** (chặn merge) và ghi rõ cách gỡ. R-38: nợ chặn merge còn mở → không merge.

## Finding

| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | **P1** | K24 / R-12 / BE-00 §2, §12 bước 7 / R-38 | Shim `pending_bind_rows` cho dòng BE-BIND chưa duyệt qua cổng bước 7 và 5b như dòng đã duyệt; `H1_EXEMPT`/`INFRA_OPS` có N51 trái danh sách miễn đóng của `BE-00.md:474`. Merge lúc này đưa N49–N51 vào `main` khi chủ dự án chưa duyệt (NO-M1-9 **[merge] mở**). | `tools/charter.py:147-157`; `tools/contract/check.py:47-54,98`; `tools/case_gate.py:35,549`; `changes/M1-01.md:26` | Chủ dự án duyệt `docs/reviews/pending/charter-M1-01.md` (cả §4). Người điều phối commit riêng `docs(charter): …` chỉ gồm `docs/charter/*` (+ `changes/M1-01.md`), trailer `Prompt: M1-01` + `Charter-Approved: <dòng duyệt thật>`; chép N49–N51 vào BE-BIND (đếm 53), HOP-DONG-MOI §13, BE-00 W4/§7/§8/§12 bước 7/§13.1. Cùng lúc **gỡ shim** (`pending_bind_rows`, hai nơi gọi, test của nó) và xoá tệp chờ; chạy lại `run.sh verify --steps 5,7` (5b cần junit bước 5). Không duyệt → gỡ N49–N51 và hai miễn. |
| 2 | **P1** | API (hợp đồng FE) / OPS | `APPFRONT_SHA` = `6fbff729` chỉ có trên `origin/feature/m1-01-model-export`, không trên `origin/master`. CI lấy được (checkout theo SHA), nhưng: `main` sẽ ghim FE chưa merge; `deploy.yml:83` dựng ảnh web từ đúng SHA này → deploy `main` phát hành nhánh FE chưa vào `master`; FE squash/xoá nhánh → SHA mồ côi. Tiền lệ AID-01 lượt 1 F2 (P1, đòi SHA trên `master`). | `tools/contract/APPFRONT_SHA:1`; `.github/workflows/deploy.yml:51,83`; `.github/workflows/ci.yml:176-181` | Merge FE M1-01 vào `master` AppFront (merge thường, hoặc ghim lại SHA sau squash), push; ghim lại `APPFRONT_SHA` = commit trên `origin/master`; chạy lại `--steps 5,7`. |
| 3 | P3 | LOG (shim) | Docstring `pending_bind_rows` hứa "dòng trùng `operationId` thì dòng BE-BIND thắng ở nơi gọi"; `check.py:98` đặt BE-BIND sau (thắng, đúng), nhưng `case_gate.py:549` đặt dòng chờ **sau** BE-BIND → `rows_by_op` (`case_gate.py:456`) lấy dòng **chờ duyệt**, có thể đổi Loại/Khoá của một dòng đã duyệt và đổi tập case bắt buộc. | `tools/case_gate.py:549`; `tools/charter.py:150-156` | Tự hết khi gỡ shim (finding 1). Nếu còn giữ tạm: đảo thứ tự như `check.py`. |
| 4 | P3 | TEST (shim) | `contract_rows(bind_file)` nhận BE-BIND tạm cho test nhưng luôn trộn dòng chờ từ `REPO_ROOT` thật → test đơn vị dùng BE-BIND giả vẫn bị tiêm N49–N51. | `tools/contract/check.py:96-99` | Tự hết khi gỡ shim. |
| 5 | P2 | MNT-05 | (lượt 1, giữ) ~3.960 dòng logic. | toàn nhánh | Như lượt 1: không bắt tách, merge squash. |
| 6 | Nit | Git / §2 | Lịch sử nhánh còn `f4ae5ac6` (charter + `tools/**` không `Charter-Approved:`). Merge thường sẽ đưa commit này vào lịch sử `main`. | `f4ae5ac6`, `031ba618` | Chỉ merge squash. |

## Sổ nợ

- `changes/M1-01.md` NO-M1-1…14 đủ phân loại. **NO-M1-9 [merge] mở** → chặn merge (R-38). Còn lại `[mặc định]`/`[phát hành]`/`[sau]` có lý do (cờ FE mặc định tắt); NO-M1-12 mở rộng đúng thực tế (đồ tự bày dùng mẫu thư viện từ `a2238c11`).
- Ngoài nhánh: test dễ vỡ khi tải cao `test_sweep_resends_remaining_families_when_walls_used` (r2) nên có dòng nợ ở chủ `pipeline_steps`; không chặn nhánh này.

## Điểm

| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 4 | 0,60 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 5 | 0,50 |
| DB/API | 10% | 2 | 0,20 |
| TEST | 7% | 5 | 0,35 |
| OBS/OPS | 5% | 4 | 0,20 |
| MNT | 3% | 3 | 0,09 |

Tổng: 4,44 / 5 (không quyết định: còn 2 P1 chưa waiver)

## PHÁN QUYẾT: REQUEST CHANGES

Lý do: mã của vòng sửa tốt — F2, F4, F7, F8, F10 lượt 1 đã sửa có test, cổng đầy đủ @ 0419fd30 thoát 0, độ phủ ≥ 90/90. Nhưng điều cốt lõi của lượt 1 chưa xong: chủ dự án **chưa duyệt** N49–N51, và shim `pending_bind_rows` + miễn H1 N51 làm hợp đồng chưa duyệt qua cổng như đã duyệt nếu merge bây giờ (finding 1); `APPFRONT_SHA` ghim nhánh FE chưa vào `master` (finding 2). Để được duyệt:

1. Có bản duyệt thật của chủ dự án → commit charter riêng với `Charter-Approved:`; chép dòng vào BE-BIND; gỡ shim + xoá `docs/reviews/pending/charter-M1-01.md` (finding 3, 4 tự hết). Không duyệt → gỡ N49–N51 và hai miễn.
2. FE M1-01 vào `origin/master` AppFront; ghim lại `APPFRONT_SHA`.
3. `run.sh verify --steps 5,7` (+ 5b) thoát 0 sau bước 1–2; merge squash.

Lượt 3: diff đó chạm `tools/**` và `docs/charter/*` → R-33b (2): ghi rõ phạm vi; tối thiểu `--steps 1,2,3,4,5,7` + 5b nếu người điều phối chứng minh không đổi `.py` ngoài `tools/`, còn không thì đầy đủ.
