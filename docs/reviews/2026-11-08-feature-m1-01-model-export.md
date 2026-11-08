# Review merge feature/m1-01-model-export → main

- Ngày: 2026-11-08 · Reviewer: phiên /merge-review (độc lập, không phải tác giả) · Commit đầu nhánh: 2ec750a9448a · base origin/main d1deb23c0294
- Cổng: phạm vi **đầy đủ** — lượt 1 của nhánh (R-33b điều kiện 1; diff còn chạm `packages/{core,db,domain,storage,messaging,testing}`, migration, `tools/**`, `docs/charter/*`, `docs/contracts/**`, `uv.lock`). `bash tools/verify/run.sh verify` mã thoát **0** (log: `F:/runs/m1/gates/be-full-0811.log`; log trong container `.cache/src-out/verify/20261108T070843Z-2ec750a9448a.log` → **đúng HEAD 2ec750a9**; người điều phối chạy, reviewer đọc lại bảng bước 0–8 và dòng `mã thoát: 0`). Không chạy lại cổng 13 phút (máy dùng chung, RAM hẹp).
- Đối chiếu riêng của reviewer (`run.sh shell` @ 2ec750a9): `python -m apps.api.core.openapi --out /tmp/o.json --compare docs/contracts/openapi.json` → **thoát 1** ("lệch với bản vừa xuất"). Bước 8 của cổng chỉ chạy chế độ xuất (nhánh worker), nên không bắt được lệch này. Xem finding 2.
- Độ phủ (cổng đầy đủ @ 2ec750a9): tổng dòng 99,34 % · nhánh 97,53 %; tập file bị chạm 99,46 % / 97,85 %. Gói chạm: `apps/api/model_export` 99,42/91,18 · `apps/worker/model_export` 96,79/97,06 · `apps/api/floors` 100/100 · `apps/api/spatial_write` 99,80/97,66 · `apps/api/core` 99,92/99,67 · `packages/core` 100/100 · `packages/db` 97,64/95,90 · `packages/domain` 99,83/98,66 · `packages/messaging` 100/100 · `packages/storage` 99,68/98,61 · `packages/testing` 99,48/98,11 · `tools` 98,77/96,49. Case gate (5b): N49–N51, `run_model_export`, `sweep_model_exports` đạt.
- Nguồn luật: `RULE.md`, `RULE-CODE.md`, `DEBT.md` đã bị xoá khỏi repo ở `6b2e4d52` (18/10, TIDY-ROOT); reviewer đọc bản tại `6b2e4d52^`. Sổ nợ theo tiền lệ AID-01 nằm trong `changes/<mã>.md`.

## Phạm vi và điều kiện dừng sớm (§2)

- Cây sạch (`git status --porcelain` rỗng). 15 commit; cả 15 dòng đầu đúng mẫu `<type>(<scope>): …`, cả 15 có `Prompt: M1-01`. Có `changes/M1-01.md`.
- Không `pragma: no cover|no branch`, `type: ignore` trần, `noqa` trần, `skip`/`xfail` mới (grep các dòng `+` của diff: rỗng). Không hạ ngưỡng.
- `uv.lock`: chỉ thêm `shapely` vào `appback-domain` (dependencies + requires-dist), đúng hệ quả của `packages/domain/pyproject.toml:7` cùng nhánh; không có `[[package]]` mới (shapely đã khoá sẵn). Đạt.
- `docs/contracts/openapi.json`, `tools/contract/APPFRONT_SHA`: tiền lệ LB-01/AID-01 cho người điều phối sinh/ghim trong nhánh — chấp nhận về thủ tục, nhưng nội dung hỏng (finding 2, 3).
- **`docs/charter/*`: KHÔNG đạt ngoại lệ → REJECT.** Commit `f4ae5ac6 docs(charter): add N49-N51 model export contract` chạm `docs/charter/BE-00.md`, `BE-BIND.md`, `HOP-DONG-MOI.md` **cùng** `tools/case_gate.py`, `tools/contract/check.py`, `tools/contract/schema-map.ts`, `tools/contract/tests/test_check.py`, `tools/tests/test_charter.py`, `tools/contract/APPFRONT_SHA`, `docs/contracts/openapi.json`, và **không** có trailer `Charter-Approved:` (`git log --format='%(trailers:key=Charter-Approved)' origin/main..HEAD` rỗng). Văn bản charter tự ghi chưa duyệt: `docs/charter/HOP-DONG-MOI.md:720-721` "**chờ chủ dự án duyệt**"; `changes/M1-01.md` NO-M1-9 **[merge]** "chờ chủ dự án duyệt". Skill §2 đòi đủ cả hai: commit charter chỉ chạm tài liệu **và** trailer trỏ tới bản duyệt có thật. Ở đây thiếu cả hai → REJECT bất kể cổng xanh (tiền lệ `2026-10-06-fix-fix-378-pascal-flag-compose-round-2.md`, `9bff3bf7` LB-01 lượt 1). Reviewer không có thẩm quyền duyệt câu chữ thay chủ dự án; phiên review không phải "bản duyệt".

Theo skill, REJECT ở §2 không cần review tiếp; reviewer vẫn soát toàn bộ `git diff origin/main...HEAD` để lượt sau khỏi mất một vòng.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | **P0 (dừng sớm)** | merge-review §2 / BE-00 §2 / R-37 | Charter đổi trong commit có `tools/**` + `openapi.json`, không `Charter-Approved:`, chủ dự án chưa duyệt (NO-M1-9 tự ghi **[merge]**). Kéo theo: `tools/contract/check.py:47` thêm `model_export_download_file` vào `H1_EXEMPT` trong khi docstring ngay dưới ghi "thêm tên vào đây là trái hiến chương" — chỉ hợp lệ khi câu BE-00 §12 bước 7 mới được duyệt. Squash merge sẽ gộp charter vào commit mã, không thể thoả luật sau khi gộp. | `f4ae5ac6`; `docs/charter/BE-00.md:146,323,386,474`; `BE-BIND.md:18,135-137`; `HOP-DONG-MOI.md:708-721` | (a) Bỏ 3 tệp charter khỏi nhánh (`git checkout origin/main -- docs/charter/` rồi commit). (b) Người điều phối đưa câu chữ cho chủ dự án duyệt (gộp luôn finding 6, 9), ghi dòng duyệt vào `changes/M1-01.md`, rồi tự commit `docs(charter): …` **chỉ** gồm `docs/charter/*` (+ `changes/M1-01.md`), trailer `Prompt: M1-01` + `Charter-Approved: changes/M1-01.md (người dùng duyệt <ngày>)` (khuôn `aa64e199` AID-01). `tools/**`/`openapi.json` đi ở commit `chore(contract)` riêng. |
| 2 | P1 | API (hợp đồng) / BE-00 §12 bước 8 | `docs/contracts/openapi.json` lệch mã: reviewer `--compare` thoát 1. Diff (bản commit vs bản xuất): bản xuất có thêm `"enum": ["minor","major","critical"]` ở `severity` của `ExportIssue`. Nguyên nhân: `8c5048f8` đổi `severity` sang `Literal` sau khi `openapi.json` được sinh ở `f4ae5ac6`, không sinh lại. CI chạy bước 8 chế độ so sánh → PR và `main` đỏ. | `docs/contracts/openapi.json:1915`; `apps/api/model_export/schemas.py:22` | `bash tools/verify/run.sh openapi`, chép vào `docs/contracts/openapi.json`, commit `chore(contract): …`; chạy lại `--compare` = 0. |
| 3 | P1 | API (hợp đồng FE) / CI | `tools/contract/APPFRONT_SHA` = `f984dd3c` chỉ có trên nhánh **local** `feature/m1-01-model-export` của AppFront: `git -C F:/App/AppFront branch -a --contains f984dd3c` → chỉ nhánh local đó, không remote nào; `merge-base --is-ancestor f984dd3c origin/master` → 1 (`origin/master` = `a2238c11`). Vào `main` thì CI không checkout được commit FE, và `main` ghim FE chưa merge (tiền lệ AID-01 lượt 1 finding 2). | `tools/contract/APPFRONT_SHA:1` | Merge FE M1-01 vào `master` AppFront và push; ghim lại `APPFRONT_SHA` vào commit trên `origin/master`; chạy lại `run.sh verify --steps 5,7` (bước 7 cần junit bước 5). |
| 4 | P2 | LOG (cache trả kết quả đã dọn) | `_LIVE` coi mọi dòng `done` cùng dấu vân là cache, kể cả dòng lịch quét đã dọn (`report_summary.pruned = true`, object đã `delete_prefix`, `glb_bytes = NULL`). Dấu vân chỉ gồm `[levelId, order, elevationMm, heightMm, hiddenIn3d, revision]` + versions + style, nên lặp lại khi người dùng bật/tắt `hiddenIn3d`, đổi thứ tự/cao độ rồi đổi lại, hay đổi qua lại `scene_style` mà không sửa tài liệu. Sau ≥ 5 lượt `done` mới hơn, N49 trả 200 `done` cho lượt đã mất tệp → N51 404, và người dùng không xuất lại được (unique partial `uq_model_exports_fingerprint` gồm `done` chặn chèn lượt mới). | `apps/api/model_export/service.py:47,118-120`; `apps/api/model_export/jobs.py:47-55`; `packages/domain/model_export/snapshot.py:39`; `packages/db/migrations/versions/r20261107_m1_01_model_exports.py:57-62` | Trong `start_export`: dòng cache có `pruned` → `UPDATE … SET status='superseded'` dòng đó (ra khỏi chỉ mục partial) rồi đi tiếp nhánh tạo lượt mới. Test: 6 lượt `done`, quét, N49 với dấu vân của lượt cũ nhất → 202 và lượt mới `queued`. |
| 5 | P2 | MNT-05 | ~3.960 dòng logic thêm (ngoài test/json/md), > 400. Nhánh gộp 3 việc tách được: H2/H4 sửa lưu (`floors`, `spatial_write`), bộ biên dịch (`packages/domain/model_export`, worker), API N49–N51. | toàn nhánh | Lần này không bắt tách (đã qua cổng, merge squash); lần sau tách H2/H4 thành nhánh `fix/` riêng. |
| 6 | P2 | BE-00 §13.1 (bảng thư viện theo gói) | `packages/domain` thêm `shapely` nhưng bảng §13.1 vẫn ghi `packages/domain` chỉ có `pydantic`; phần charter của nhánh không sửa dòng này. | `packages/domain/pyproject.toml:7`; `docs/charter/BE-00.md:514` | Thêm `shapely` vào dòng `packages/domain` trong cùng bản charter đưa chủ dự án duyệt (finding 1). |
| 7 | P3 | OPS / PERF (rác kho object) | Lượt bị `superseded` khi còn `queued` để lại `snapshot.json` (trần 64 MiB) tới khi xoá dự án: `start_export` bỏ giá trị trả về của `state.supersede`; worker gặp `claim → None` thì `return`, không dọn; `_PRUNE` chỉ quét `status = 'done'`. | `apps/api/model_export/service.py:121`; `apps/worker/model_export/tasks.py:249-251`; `apps/api/model_export/jobs.py:51` | Lấy id từ `state.supersede` và `on_after_commit` xoá `model_export_prefix` của các lượt đó (lượt `running` bị thay vẫn tự dọn ở `finish → False`); hoặc cho `_PRUNE` dọn cả `superseded`. |
| 8 | P3 | CON | Hai N49 đồng thời **khác** dấu vân: cả hai `supersede` (READ COMMITTED, ảnh chụp câu lệnh không thấy dòng mới của bên kia) rồi cả hai chèn `queued` → hai lượt sống, thêm một lượt Blender. Không hỏng dữ liệu (N50 lấy mới nhất). | `apps/api/model_export/service.py:121-140` | `pg_advisory_xact_lock` theo `project_id` trước `supersede`, hoặc ghi nợ `[sau]`. |
| 9 | P3 | LOG / DOC | `snapshotRevisions[].levelName` có trên dây (`SnapshotRevision.level_name`; FE `modelExport.ts` @ f984dd3c có khoá) nhưng HOP-DONG-MOI §13 chỉ ghi `{levelId, revision}[]`. Tên tầng không nằm trong dấu vân nên đổi tên tầng vẫn trả lượt cache mang tên cũ, không `stale`. | `docs/charter/HOP-DONG-MOI.md:712-713`; `apps/api/model_export/schemas.py:15`; `apps/api/model_export/snapshot_source.py:81` | Sửa câu charter thành `{levelId, levelName, revision}[]` trong bản đưa duyệt; ghi rõ đổi tên tầng không làm lượt cũ `stale` (hoặc đưa tên vào dấu vân). |
| 10 | Nit | PERF | `_compiler()` đọc và băm `snapshot_source.py` đồng bộ trên vòng sự kiện mỗi lần N49/N50 (N50 thăm dò 3 s/người); `compiler_version()` đã `@cache` còn phần này thì không. | `apps/api/model_export/service.py:90-99` | Đưa phần băm tệp vào một hàm `@functools.cache`. |

Đã soát, không thành finding: SEC — N51 chặn tên tệp theo danh sách trắng + `is_id("mex")` + lọc `project_id` (không IDOR/path traversal); N49 đòi `model.export`, N50/N51 thành viên, người ngoài 404; Blender chạy env tối thiểu, `killpg`, không DSN (NO-M1-5 `[sau]` có lý do); tệp gói kiểm sha256 + `is_relative_to`, khoá qua `check_key`. CON — mọi chuyển trạng thái lọc trạng thái nguồn; C14 cùng dấu vân có test; xoá object sau commit. RES — Blender 270 s < soft 300 s < chốt kẹt 15 phút; OOM → `DEPENDENCY_UNAVAILABLE` thử lại. DB — migration expand thuần, CHECK có tên, bước 6 đạt. H2/H4 — đi `write_layer` (khoá, revision, nhật ký), H4 chỉ ở N16 người sửa, `_check_candidate` chạy trước nên không `KeyError`.

## Sổ nợ
- `changes/M1-01.md` NO-M1-1…14 phân loại đủ. NO-M1-9 (CHARTER) **[merge] mở** → chặn merge (R-38). Còn lại `[mặc định]`/`[phát hành]`/`[sau]` có lý do, không chặn merge (cờ FE mặc định tắt).
- Finding 4, 6–9 phải được sửa hoặc có dòng nợ trước merge (R-38): hiện chưa có.

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 4 | 0,60 |
| LOG | 15% | 3 | 0,45 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 5 | 0,50 |
| DB/API | 10% | 1 | 0,10 |
| TEST | 7% | 5 | 0,35 |
| OBS/OPS | 5% | 4 | 0,20 |
| MNT | 3% | 3 | 0,09 |

Tổng: 4,04 / 5 (không quyết định: §2 dừng sớm thắng; nếu bỏ §2 thì vẫn còn P1 → REQUEST CHANGES)

## PHÁN QUYẾT: REJECT
Lý do: điều kiện dừng sớm §2 — `docs/charter/*` đổi trong commit `f4ae5ac6` có cả `tools/**` và `openapi.json`, không trailer `Charter-Approved:`, và chủ dự án **chưa** duyệt câu chữ (NO-M1-9 tự ghi chặn merge). Mã và cổng đầy đủ @ 2ec750a9 (thoát 0, độ phủ ≥ 90/90 mọi gói chạm) không đổi được kết luận này. Để được duyệt:
1. Bỏ thay đổi `docs/charter/*` khỏi nhánh; người điều phối lấy duyệt câu chữ của chủ dự án (N49–N51; HOP-DONG-MOI §13 có sửa `levelName` (F9); BE-00 W4 `mex_`, hàng `bake`, khoá lưu trữ, miễn H1 N51; `shapely` cho `packages/domain` ở §13.1 (F6)), rồi commit charter riêng chỉ tài liệu, có `Charter-Approved:` trỏ dòng duyệt thật.
2. F2: sinh lại `openapi.json`, `--compare` = 0.
3. F3: FE M1-01 vào `origin/master` AppFront, ghim lại `APPFRONT_SHA`, chạy `--steps 5,7`.
4. F4: sửa (khuyến nghị) hoặc ghi nợ có lý do; F7–F9 sửa hoặc ghi nợ trong `changes/M1-01.md`.

Lượt 2: nếu diff vòng sửa chỉ là bỏ tệp charter + `openapi.json` + `APPFRONT_SHA` (+ dòng nợ) thì `--steps 5,7,8` + `--compare` là đủ; nếu có sửa `.py` (F4, F7) thì chạm `apps/*`/test → theo R-33b, ghi rõ phạm vi trong phán quyết lượt 2.
