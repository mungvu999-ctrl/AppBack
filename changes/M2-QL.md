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
