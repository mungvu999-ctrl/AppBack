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
  sâu hơn hộp (B2 có; ở đây tâm luôn ở tâm hộp). `model_size_m` chuyển sang `assets`; `assets._size_mm` để nguyên
  (sửa ở nhánh M1) và không còn được gọi — xoá khi gộp M1.
- **ch-04 dựng lại** (`appback-bake:f14`, mã worktree): 83 đề xuất dựng hết, 0 bỏ; bản ghi `degraded minor` 40 → 15
  (3 đề xuất: rèm 2, gộp tủ 1; 12 đồ document theo F4: hộp tài liệu nhỏ/lớn hơn mẫu); `major` 3 (tường chồng, như
  trước); chất lượng `degraded` như trước; plan tất định; không hình hở/suy biến.
- **Khronos glTF-Validator** (ch-04 trước → sau): lỗi 0 → 0; cảnh báo 46 → 46 (đều
  `MESH_PRIMITIVE_GENERATED_TANGENT_SPACE`); info 56 → 56 (`UNUSED_OBJECT` TEXCOORD_0); vật liệu 59 → 58; 46 699 tam
  giác, 441 draw call.
- **Nợ còn mở:** 409 `remoteChanges` chưa liệt kê đổi `state`/`override` của đề xuất (cần migration CHECK
  `entity_type` + `VersionEntityType`); concept AI truyền `None` vào `reconcile`; `versions` chưa chụp lớp đề xuất;
  FE strict (zod) và hiến chương `docs/reviews/pending/charter-M2-QL.md` chưa duyệt → cổng bước 7 đỏ tới khi duyệt;
  `_size_mm` chết chờ gộp M1; ca vàng FE mirror theo `rulesetSha` mới.
- Cổng áp dụng: ruff, mypy, lint-imports, pytest `packages/domain/suggestions`, `packages/domain/model_export`,
  `apps/worker/model_export/tests`, `apps/api/model_export/tests/test_snapshot_source.py`. Migration/openapi/hợp đồng
  FE–BE: không đổi thêm.
