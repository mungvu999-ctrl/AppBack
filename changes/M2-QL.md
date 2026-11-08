# M2-QL — lớp đề xuất: gói miền thuần `packages/domain/suggestions`

- Bảng tham số `rule_params.json` (19 luật của hợp đồng M2 §5) + `rulesetSha`, mô hình `Suggestion`/`SuggestionsMeta`.
- `reconcile` thuần, tất định (§3 bước 1–6, đổi bản phụ/chính, trần 2 000) và `apply_state` cho `suggestionState`.
- Luật đã làm: R1, R4, C1, C2, D2, F1, L1; các luật còn lại đăng ký nhưng chưa sinh gì.
- Ca vàng cho FE mirror ở `tests/data/` (bench r5 ch-04, ch-06).
- Chưa nối writer/compiler/FE: không đổi API, DB, migration, openapi, hợp đồng FE–BE.
- Cổng áp dụng: ruff, lint-imports, mypy, pytest `packages/domain/suggestions`. Migration/hợp đồng: không áp dụng.

## M2-QL — compiler dựng lớp đề xuất (`packages/domain/model_export`, `apps/worker/model_export`)

- `build_plan` đọc `floors[i].suggestions` (+ `suggestionsMeta`) của snapshot; dựng bản `active`/`overridden` không
  `stale` với `{**params, **override}`: skirting, wallTile, cornice, casing, sash (gắn pivot cánh của bộ cửa sổ),
  railing, cabinetRun (dãy dưới thay hộp chủ + `mergedIds`; dãy trùng ≥ 50 % bị bỏ), curtain (xếp nếp, nửa thanh
  treo), rug, ceilingLight, item (neo hộp bao, co giãn ±10 % hoặc bỏ), modelPick (mẫu của đồ chủ chưa có `model`).
- `adopted` đã nằm trong `layer`; `disabled`/`stale` không dựng, vào extras scene `appSuggestions {rulesetSha, counts,
  disabled}`. Sổ ghi `suggestion:<rule>`, nặng nhất `minor` (kể cả vật liệu thiếu trong gói, hình hở phía Blender);
  extras nút `{prov:"suggested", rule, ruleVersion, suggestionId, hostRef, state}`.
- Vật liệu toàn cục (theo `compiler_version`): kính truyền sáng 1 / nhám ≤ 0,05; vải sheen 0,6; vật liệu phẳng phi kim
  nhiễu độ nhám ±0,06 (ảnh 128 px lặp được, theo khoá); `repeatM` của gói; mỗi phòng một bản vật liệu sàn/tường
  `<ref>@<roomId>` dùng chung ảnh (Blender nạp ảnh `check_existing`).
- `snapshot.suggestion_keys(document)`: document không có lớp đề xuất giữ nguyên `snapshotSha` (test ghim giá trị M1).
  **Chưa nối:** `apps/api/model_export/snapshot_source.take` cần ghép `**suggestion_keys(row["document"])` vào từng
  tầng (ngoài phạm vi tệp của lượt này).
- Cổng áp dụng: ruff, mypy, pytest `packages/domain/model_export` + `apps/worker/model_export/tests` (độ phủ 99 %).
  Migration/openapi/hợp đồng FE–BE: không đổi.

## M2-QL — writer, wire đọc, bảng luật (`apps/api/spatial_write`, `spatial_read`, `suggestion_rules`)

- `write_layer`: `suggestionState` (thưa) kiểm rồi áp bằng `apply_state` **trước** `reconcile`; `reconcile` chạy trong
  `_attempt` cùng revision khi lớp bật và `layer` đổi (sửa tay, `merge`, đổi tỉ lệ) hoặc lớp vừa bật. Lỗi 422
  `SUGGESTION_UNKNOWN`, `SUGGESTION_OVERRIDE_INVALID`, `SUGGESTION_CAP`; id trùng → `VALIDATION`. C09b/bước 10 không
  sinh lại. Đổi `rulesetSha` → log `suggestions_ruleset_changed` (sha cũ → mới).
- Tài liệu: khoá `suggestions`/`suggestionsMeta` (vắng khi rỗng) + `suggestionsEnabled: true` (vắng = tắt; tầng
  revision 0 suy là bật, §8.1). `Furniture.origin?` ở miền. Id đề xuất không vào `floor_entity_ids`.
- Dây: N16 và kết quả #35 thêm `suggestions?`, `suggestionsMeta?`, `suggestionsEnabled?`; `FurnitureOut.origin?`.
- Route mới `GET /api/suggestion-rules` (`suggestion_rules_read`, Đ\*, ETag = `rulesetSha`, 304).
- Hiến chương **chưa duyệt**: `docs/reviews/pending/charter-M2-QL.md` (N52, #35, N16, HOP-DONG-MOI §14). Cổng
  bước 7 (H1, BE-BIND) và FE strict sẽ đỏ cho tới khi duyệt + FE thêm zod.
- NO-M2-1 (CHARTER) **[merge]**: câu chữ BE-BIND N52, #35, N15/N16, HOP-DONG-MOI §14 chờ chủ dự án duyệt; nguyên văn
  ở `docs/reviews/pending/charter-M2-QL.md`. Phụ thuộc cổng (như NO-M1-9): bước 7 và 5b đọc dòng N52 từ phụ lục máy
  đọc của tệp đó qua `tools.charter.pending_bind_rows`; #35/N15/N16 không cần dòng (không đổi `operationId`/đường).
  `docs/contracts/openapi.json` sinh lại theo route và trường M2 dù hiến chương chưa duyệt (cổng so `--compare`).
  `schema-map.ts` **chưa** có `suggestion_rules_read`: tệp FE `suggestionRules.ts` chỉ có ở nhánh FE M2, thêm mục khi
  ghim `6fbff729` làm Smoke hỏng mọi test dùng runner. Bước 7 "Bản đồ đủ" đỏ một dòng, kéo theo 5 test
  `tools/contract/tests/test_check.py` dùng bản đồ thật (`test_real_map_covers_every_contract_row`,
  `test_map_missing_a_bind_row_fails`, `test_map_extra_entry_fails`, `test_run_checks_passes_on_consistent_inputs`,
  `test_real_repo_check_runs_through`) cho tới khi FE M2 được đẩy: cùng commit ghim `APPFRONT_SHA` thêm
  `suggestion_rules_read: object('@/api/schemas/suggestionRules', 'SuggestionRulesWireSchema')`. Duyệt xong: chép dòng N52 vào BE-BIND, xoá tệp chờ; không
  duyệt thì gỡ route N52 và mục bản đồ.
- Chưa làm: 409 liệt kê đổi `state`/`override` (cần migration CHECK `entity_type` + lõi `VersionEntityType`);
  `refreshSuggestions`; concept AI vào `reconcile` (truyền `None`); phiên bản (`versions`) chưa chụp lớp đề xuất.
- Cổng áp dụng: ruff, mypy, pytest đích `spatial_write`, `spatial_read`, `suggestion_rules`, `packages/domain/spatial`,
  `versions`, `core`. Migration: không. openapi/hợp đồng FE–BE: đổi, chờ duyệt.

## M2-QL — luật va chạm và đặt đồ (`packages/domain/suggestions`)

- `rules/placement.py`: vết chân theo bbox mẫu (`data/model_bbox.json`, chép từ thí nghiệm B2), `place` áp tường
  (chỉ đoạn cạnh có tường thật phía sau, tỉ lệ ±10 % rồi bỏ), `check_collisions(layer, suggestions)` gắn nhãn `doc`
  (có sẵn giữa hộp document, chỉ ghi nhật ký) / `rule` (luật không để lại), `model_origin` (F0).
- Luật mới: R2 gạch ốp phòng ướt (cắt cửa, phần dưới bậu), R3 phào, F2 dãy tủ dưới (gộp hộp trùng ≥ 50 %, cổng công
  năng phòng và áp tường ≥ 80 % trong 0,15 m), F3 tủ trên (bỏ khi vướng ô mở), P1 đồ chính, P2 tab đầu giường, D1 thảm,
  P3 bàn trà (đứng trên thảm), D3 bình, D4 tranh, D5 cây. Mọi bỏ/giảm có ghi chú tiếng Việt.
- Luật sau thấy mọi đề xuất hiệu lực của luật trước; `params` thêm `cabinetRun.start/end/mergedIds/degradedVi`,
  `wallTile.sillSpansMm`, `item.variant`; `rule_params.json` thêm tham số (đổi `rulesetSha`).
- Ca vàng: ch-04, ch-06 mở rộng (`serverSuggestions`, `collisions`), thêm `syn-penthouse`; 0 va chạm `rule` trên cả 3.
- Cổng áp dụng: ruff, mypy, pytest `packages/domain/suggestions` (độ phủ 100 % dòng/nhánh). Migration/openapi/hợp đồng
  FE–BE: không đổi.

## M2-QL — Tích hợp (snapshot, vật liệu mặc định, một luật tỉ lệ, F0/F4 cho đồ của document)

- **Nối snapshot:** `snapshot_source.take` ghép `**suggestion_keys(row["document"])` vào từng tầng. Tầng có đề xuất
  mang `suggestions` + `suggestionsMeta`; tầng không có giữ đúng dạng M1 nên `snapshotSha` không đổi (test
  `apps/api/model_export/tests/test_snapshot_source.py`). Dấu vân không đổi (đã gồm `revision`).
- **Vật liệu mặc định:** R1/C1 dùng `library:preset-white` (có trong gói m1-01-pack-2) thay `library:paint-white`
  (không có, sinh 37 bản ghi `degraded minor` trên ch-04). Ca vàng sinh lại, **`rulesetSha` mới
  `8d81d381b90256a6c9f477243fc76cc2da4b2b523908d2a3018d2a1ec8932f60`**: ca vàng mirror của AppFront phải chép lại.
- **Một luật tỉ lệ:** `suggestions.rules.placement.footprint_ratio` (vết chân, cạnh dài theo cạnh dài, bỏ chiều cao)
  là luật duy nhất: F1 gọi nó, compiler gọi qua `model_export.assets.fit_scale` (làm tròn 3 số như luật, kẹp ±10 %;
  phép thử cho mỗi cạnh hộp 0,5 mm vì luật lưu mm nguyên). Lavabo ch-04 (917x636 co 90 % → 825x572) giờ dựng ở 0,9
  thay vì bị bỏ "89,9 %". Compiler vẫn đọc cỡ mẫu từ gói (`models.json`), luật đọc `data/model_bbox.json`; hai bảng
  trùng số với gói hiện tại. Giới hạn: `compiler_version` chỉ băm `model_export`, sửa `footprint_ratio` không tự đổi
  phiên bản bộ biên dịch.
- **F0/F4 cho mọi đồ:** đồ của document cũng neo hộp bao (`anchor: "bbox"`): đáy trên sàn hoàn thiện (+20 mm) +
  `liftMm`, tâm ở tâm hộp; cạnh dài mẫu cắt cạnh dài hộp (cả hai dài ≥ 1,2 lần) → xoay 90°, lưng về tường gần hơn;
  tỉ lệ = `fit_scale`, cần quá ±10 % → kẹp ở ±10 % + `degraded minor` có lý do ("mẫu toilet cần co giãn 43 %… chỉ
  cho 90 %"). Đề xuất `item` ngoài ±10 % vẫn bị bỏ (luật không bao giờ để vậy). Không áp lưng mẫu vào tường khi mẫu
  sâu hơn hộp (B2 có; ở đây tâm luôn ở tâm hộp). `model_size_m` chuyển sang `assets`; `assets._size_mm` (bản sửa
  `w/d/h` của M1) đã xoá khi gộp M1 `07a957d9`: `model_size_m` đọc `w/d/h` rồi mới `x/y/z`; mất log
  `model_export_pack_size_xyz` của M1; test `w/d/h` của M1 giữ, đổi kỳ vọng theo `fit_scale` (kẹp ±10 %).
- **ch-04 dựng lại** (`appback-bake:f14`, mã worktree): 83 đề xuất dựng hết, 0 bỏ; bản ghi `degraded minor` 40 → 15
  (3 đề xuất: rèm 2, gộp tủ 1; 12 đồ document theo F4: hộp tài liệu nhỏ/lớn hơn mẫu); `major` 3 (tường chồng, như
  trước); chất lượng `degraded` như trước; plan tất định; không hình hở/suy biến.
- **Khronos glTF-Validator** (ch-04 trước → sau): lỗi 0 → 0; cảnh báo 46 → 46 (đều
  `MESH_PRIMITIVE_GENERATED_TANGENT_SPACE`); info 56 → 56 (`UNUSED_OBJECT` TEXCOORD_0); vật liệu 59 → 58; 46 699 tam
  giác, 441 draw call.
- **Nợ còn mở:** 409 `remoteChanges` chưa liệt kê đổi `state`/`override` của đề xuất (cần migration CHECK
  `entity_type` + `VersionEntityType`); concept AI truyền `None` vào `reconcile`; `versions` chưa chụp lớp đề xuất;
  FE strict (zod) và hiến chương `docs/reviews/pending/charter-M2-QL.md` chưa duyệt → cổng bước 7 đỏ tới khi duyệt;
  ca vàng FE mirror theo `rulesetSha` mới.
- Cổng áp dụng: ruff, mypy, lint-imports, pytest `packages/domain/suggestions`, `packages/domain/model_export`,
  `apps/worker/model_export/tests`, `apps/api/model_export/tests/test_snapshot_source.py`. Migration/openapi/hợp đồng
  FE–BE: không đổi thêm.

## M2-QL — vòng sửa review 2026-11-08 (`docs/reviews/2026-11-08-feature-m2-ql-suggestions.md`)

- F4: `_write_log` ghi đổi `state`/`override` (loại `suggestion`) → nền cũ nhận 409 liệt kê đổi ấy. **Migration có:**
  `r20261108_m2_ql` (contract, thay CHECK `entity_type` như `r20261024_fe3d_roof`, có downgrade + test vòng lên/xuống),
  đăng ký trong `docs/contracts.toml`; `VersionEntityType`/`CHANGE_ENTITY_TYPES` thêm `suggestion`.
- F5: snapshot bỏ đề xuất khi `suggestionsEnabled` không bật. F6: `flag_collisions` (trong `reconcile` và lượt chỉ bật
  lại) đánh `stale` có lý do bản đề xuất va nhau. F7: `gapMm`/`plinthMm`/`countertopMm` trong `params` dãy tủ, xoá bốn
  hằng `CABINET_*` và lọc trùng của compiler. F8: bản chính không `migrate` → `stale` giữ trạng thái người dùng.
  F9: `check_override` → 422 `SUGGESTION_OVERRIDE_INVALID`. F13 + F12: đồ của document không xoay (như editor), vừa hộp
  theo hướng dựng thật. F10 (một phần): `origin` ⇒ `source='human'`. F11: `SUGGESTION_CAP` mang số sau `reconcile`.
  F17 (một phần): ca 401 N52. Nit 19: lý do chỉ tiếng Việt. Nit 21: xoá `model_origin`. F14/F15 (một phần): compiler
  không còn nhập `suggestions` (xoá phụ thuộc vòng; `compiler_version` băm đủ mã compiler).
- Cổng áp dụng: ruff, mypy, lint-imports, migration (bước 6), pytest đích `spatial_write`, `spatial_read`,
  `suggestion_rules`, `model_export`, `packages/domain/{suggestions,model_export,spatial}`, `packages/db`, `packages/core`.
  openapi: không đổi thêm. Hợp đồng FE–BE: 409 thêm `entityType: "suggestion"` (NO-M2-2).

Phân loại: **[merge]** chặn merge · **[mặc định]** chặn bật cờ mặc định · **[phát hành]** chặn phát hành · **[sau]** để sau, giới hạn đã ghi.

- NO-M2-2 (CON/API) **[merge]**: 409 `remoteChanges` nay có `entityType: "suggestion"` (`entityId` = id đề xuất,
  `field` = `state`/`override`). AppFront `VERSION_ENTITY_KINDS` (`src/api/schemas/errors.ts:155`) là enum strict → FE
  hiện từ chối thân 409 ấy (báo lỗi chung, không trộn; không mất dữ liệu vì máy chủ đã chặn ghi). Cần cùng lúc: FE thêm
  `suggestion` vào enum + chiến lược trộn, câu chữ HOP-DONG-MOI §1.3 (dòng mới ở `docs/reviews/pending/charter-M2-QL.md`
  §2.3), ghim lại `APPFRONT_SHA`. Revision `r20261108_m2_ql` được worker đăng ký vào `docs/contracts.toml` (tệp của người
  điều phối, tiền lệ `f7585326`): người điều phối xác nhận dòng ấy. Đi cùng NO-M2-1.
- NO-M2-3 (LOG) **[sau]**: bản vừa bật lại mà va món khác (F6) ở `stale` tới lượt đổi `layer` sau mới được xếp lại;
  tắt món chặn nó cũng không gỡ `stale` ngay. Lý do: chạy lại `reconcile` ở lượt chỉ đổi trạng thái sẽ áp bộ luật mới
  ngoài lượt đổi `layer` (trái §8 quyết định 4). Lý do va chạm hiện trong `stale.reasonVi` nên người dùng thấy.
- NO-M2-4 (LOG) **[sau]**: F10 phần còn lại — `suggestionState` gửi `adopted` cho đề xuất không có món `origin` khớp
  khi `layer` không đổi vẫn được nhận (lượt đổi `layer` sau ép về `disabled`); hai món cùng `origin.suggestionId` thì
  món sau thắng im lặng. Không mất món của người dùng (món nằm trong `layer`), chỉ lệch trạng thái một lượt.
- NO-M2-5 (RES) **[phát hành]**: lỗi trong một luật làm `reconcile` ném → mọi lượt lưu trên tầng bật lớp hỏng (kể cả
  `merge` của pipeline); chưa bọc, chưa lùi về danh sách cũ. Chưa từng gặp trên 66 ca bench; sửa trước phát hành
  (bọc + log + giữ danh sách cũ) hoặc chứng minh bằng fuzz.
- NO-M2-6 (PARITY) **[phát hành]**: concept AI chưa vào `reconcile` (truyền `None`; hợp đồng §8 quyết định 5): dự án
  chọn concept thì editor bày `additions`/mẫu theo vai, GLB không có.
- NO-M2-7 (DATA) **[sau]**: `versions` chưa chụp lớp đề xuất; khôi phục phiên bản đổi `layer`, lượt `reconcile` sau
  sinh lại theo id và giữ trạng thái người dùng theo id, nên chỉ mất trạng thái của id không còn sinh ra.
- NO-M2-8 (R-07) **[sau]**: hai bảng cỡ mẫu không ai so (`suggestions/data/model_bbox.json` của luật, `sizeMm` trong
  `models.json` của gói) — F15 phần còn lại. Lệch thì luật đặt món mà compiler ghi `degraded` "cần co giãn"; hiện hai
  bảng trùng số. Thêm test so ở bước dựng gói.
- NO-M2-9 (PARITY) **[sau]**: F1 chọn mẫu bằng `footprint_ratio` cạnh dài–cạnh dài (bảng mirror FE), còn compiler từ
  F13 dựng mẫu không xoay, vừa hộp theo hướng thật → mẫu F1 chọn cho hộp có trục dài cắt trục mẫu ra `degraded minor`
  (kẹp 90 %) thay vì vừa. Đúng với editor (cũng không xoay); đổi F1 là đổi ca vàng mirror FE.
- NO-M2-10 (TEST) **[sau]**: test hiệu năng chỉ chặn ở 3 000 ms (F17): máy cổng + `coverage` làm số dao động; p95 đo
  08/11 trong `run.sh shell` (`-n 3`, không coverage): ch-04 27,2 ms, ch-06 30,1 ms, syn-penthouse 81,8 ms (< 150 ms).
- NO-M2-11 (OBS) **[sau]**: `apps/worker/datasets/tasks._reviewed_after` coi mọi dòng nhật ký của người là "đã duyệt
  sau khi tải ảnh"; dòng `suggestion` (chỉ bật/tắt đề xuất) nay cũng tính. Tín hiệu yếu nhưng đúng là người đã mở tầng;
  lọc `entity_type <> 'suggestion'` nếu bộ dữ liệu cần chặt hơn (tệp của prompt datasets).
- NO-M2-12 (R-07) **[sau]**: Nit 20 — hai danh sách mẫu "vải" (`materials.py:50`, `compile_plan.bpy:315`, bpy còn khớp
  `sheet`); ảnh nhiễu `roughVar` gieo theo khoá có `@roomId` nên mỗi phòng một ảnh 128 px.
- NO-M2-13 (MNT-05) **[sau]**: F18 — nhánh ~8.500 dòng logic, reviewer không bắt tách; merge squash.
- F16 các nợ tác giả cũ: 409 không liệt kê đổi đề xuất → **đã sửa** (F4); `compiler_version` không băm luật tỉ lệ →
  **đã sửa** (F13: compiler không còn nhập `suggestions`); concept AI → NO-M2-6; `versions` → NO-M2-7; `reconcile` lỗi
  chặn lượt lưu → NO-M2-5.
- Ca vàng `ch-04.golden.json`: hai dãy tủ (F2/F3, luật chỉ máy chủ) thêm `gapMm`/`plinthMm`/`countertopMm`;
  `rulesetSha` không đổi, ca mirror FE (R1/R4/C2/F1/L1) không đổi.
