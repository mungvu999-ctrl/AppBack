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
