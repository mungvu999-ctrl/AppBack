# M2-QL — lớp đề xuất: gói miền thuần `packages/domain/suggestions`

- Bảng tham số `rule_params.json` (19 luật của hợp đồng M2 §5) + `rulesetSha`, mô hình `Suggestion`/`SuggestionsMeta`.
- `reconcile` thuần, tất định (§3 bước 1–6, đổi bản phụ/chính, trần 2 000) và `apply_state` cho `suggestionState`.
- Luật đã làm: R1, R4, C1, C2, D2, F1, L1; các luật còn lại đăng ký nhưng chưa sinh gì.
- Ca vàng cho FE mirror ở `tests/data/` (bench r5 ch-04, ch-06).
- Chưa nối writer/compiler/FE: không đổi API, DB, migration, openapi, hợp đồng FE–BE.
- Cổng áp dụng: ruff, lint-imports, mypy, pytest `packages/domain/suggestions`. Migration/hợp đồng: không áp dụng.
