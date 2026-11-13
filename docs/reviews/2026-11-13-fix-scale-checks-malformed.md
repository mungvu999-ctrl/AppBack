# Review merge fix/scale-checks-malformed → main

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review (agent độc lập, lượt 1) · Commit đầu nhánh: 8c0bcb1dd6b6 (PR #70; 1 commit trên `origin/main` 66cebe8e sau `git fetch`, GitHub `MERGEABLE`)
- Cổng: Phạm vi kiểm: đầy đủ — bằng CI GitHub Actions của PR #70 trên headSha 8c0bcb1d (run 43400607034). Theo quy ước máy này không chạy `run.sh verify` đầy đủ tại chỗ (tiền lệ `docs/reviews/2026-11-13-fix-scale-state-api.md`). `gh pr checks 70 --repo mungvu999-ctrl/AppBack` cuối cùng: 24/24 `pass`, 0 fail, 0 pending (gồm changes, commits, lint, typecheck, unit-shards 1–2, integration-shards 1–5, ml-shards 1–3, perf, build, contract, coverage, CodeQL, analyze). Reviewer không chạy test tại chỗ. Nhật ký tác giả `F:/runs/mlr-next/r10/c/fast.log` (chỉ để tham chiếu): `run.sh verify --fast` mã thoát 0 (`GATE_EXIT=0`), `784 passed in 228.62s`, bước 8 openapi `đạt`.
- Độ phủ (log job `coverage` 131805280307): `coverage_gate: đạt` — tổng dòng 99,32 % · nhánh 97,55 %; `apps/api/spatial_read`: dòng 98,99 % · nhánh 99,29 %. `case_gate`: 99 thao tác đã mount, 0 cảnh báo.
- Ghi chú: checkout không có `RULE.md`, `RULE-CODE.md`, `DEBT.md`; trọng số chấm theo các phán quyết trước trong `docs/reviews/`; nợ ghi ở `changes/<mã>.md` (như tiền lệ).

## Phạm vi

- Cây sạch (`git status --porcelain` rỗng). `git diff origin/main...HEAD`: 1 commit, 5 tệp, +79/−4; logic thật ~+6 dòng ở `readiness.py` (cộng 2 dòng docstring), 1 dòng mô tả ở `wire.py`; còn lại test (~66 dòng) và hồ sơ 7 dòng. Dưới ngưỡng 400 dòng logic, không cần `MNT-05`.
- Commit `8c0bcb1d fix(spatial-read): never report determined on malformed scaleChecks`: đúng mẫu Conventional Commits, thân có `Prompt: ML-ACC-R10`; job `commits` xanh. Hồ sơ `changes/ML-ACC-R10-CHECKS.md` có (7 dòng).
- Không có `pragma`, `noqa`, `type: ignore` mới không mã (chỉ dùng lại `# type: ignore[arg-type]` có mã của bảng test cũ), không skip/xfail, không hạ ngưỡng. Không đụng `docs/charter/*`, `uv.lock`, `pyproject.toml`, `openapi.json`, `APPFRONT_SHA` (`git diff --stat origin/main...HEAD -- docs tools` rỗng). Không có điều kiện dừng sớm.

## Kiểm từng khẳng định của tác giả

1. **Luật mới của `scale_state_of`** — đúng. Mã: `raw = summary.get("scaleChecks")`; `None` → `[]`; không phải `list` hoặc có phần tử không phải `str` → `("unresolved", ("basisUnknown",))`; còn lại `tuple(raw)` đi tiếp luật cũ. Đặt SAU các nhánh `effective`/`projectDefault`/`pageChanged`/`basisUnknown` cũ nên không đổi thứ tự ưu tiên. Dò từng dạng đầu vào (đọc mã, không chạy):
   - `str` ("anisotropic"), `int`, `float`, `bool` (`True`/`False`), `dict`, `tuple` (JSON không sinh ra tuple, vẫn rơi nhánh `not isinstance(raw, list)`): → `unresolved/basisUnknown`.
   - `list` có `None`, `list` lồng `list`, `list` có `int`/`bool`/`dict`: `all(isinstance(c, str))` sai → `unresolved/basisUnknown`.
   - `[]`, vắng khoá, `null`: giữ cũ (`chain/paired/…` → `determined`; `door/note/wallThickness` → `estimated`).
   - `[""]`: là danh sách chuỗi nên được nhận như cũ (xem finding 3).
   - Gốc ước lượng (`door`/`note`/`wallThickness`) với checks hỏng: nhánh trả `unresolved` đứng trước khi tới `_ESTIMATED_BASES`, nên không còn `estimated`. Có test `door-checks-string`; `note`/`wallThickness` dùng chung mã nên cùng kết quả.
   - Gốc `default`/`human`/`None` hay `summary is None` thoát ở nhánh trước, không bị luật mới đụng.
2. **Cách `scaleChecks` được ghi** — `git grep scaleChecks origin/main -- apps packages`: nơi ghi duy nhất vào cột `quality_summary` là `apps/worker/pipeline_quality/service.py:163`, `summary["scaleChecks"] = sorted(built.scale_checks)`. `sorted(dict)` luôn là `list[str]` (khoá dict). `BuiltLayer` đọc từ `layer.json` qua `build.py:218-223`, vốn bắt `scaleChecks` không phải dict hay khoá không thuộc `_SCALE_CHECK_KEYS` bằng `ValueError`. Vậy giá trị hợp lệ do pipeline sinh ra luôn là danh sách chuỗi thuộc tập khoá đã biết (kể cả chuỗi rỗng không xuất hiện được); khoá vắng khi `scale_basis is None`; lượt cũ trước BENCH3D-09 không có khoá. Luật mới chỉ ảnh hưởng dữ liệu hỏng/thủ công, **không biến tầng tốt thành `unresolved`**.
3. **Bảy ca bảng + một ca HTTP** — đúng: `chain-checks-string/number/dict/mixed`, `door-checks-string`, `paired-checks-empty`, `chain-checks-null` (7 ca `test_scale_state_of_table`) và `chain-malformed` (`test_routes_scale_state.py`, dựng qua DB thật, đi tới `unresolved` + `["basisUnknown"]`). Lưu ý hồ sơ nói có `bool` nhưng bảng không có ca `bool` (finding 1).
4. **`readiness_of` và 128 tổ hợp** — `readiness_of` gọi chính `scale_state_of` (`state, _ = scale_state_of(...)`), nên `scaleUnresolved` đi theo luật mới theo cấu trúc. Test tính chất `test_scale_unresolved_iff_state_is_doubtful` (4 nguồn × 4 checks × 8 gốc = 128) chỉ dùng `checks` dạng danh sách hợp lệ nên không vi phạm và cũng không chạm ca hỏng (finding 2); phát biểu của nó vẫn đúng với luật mới vì cả hai bên cùng gọi một hàm.
5. **Không đổi hợp đồng** — đúng. `docs/contracts/openapi.json` không đổi trong diff. Mô tả `scale_reasons` ở `wire.py` là docstring thuộc tính (không phải `Field(description=...)`; repo không đặt `use_attribute_docstrings`), và `scaleReasons` trong `openapi.json` không có khoá `description` (chỉ `anyOf` + `title`), nên không thể làm đổi bản xuất. Job `contract` xanh trên 8c0bcb1d. Kiểu dây không đổi: `scaleState` vẫn một trong bốn giá trị, `scaleReasons` vẫn `tuple[str, ...]` (kết quả mới `("basisUnknown",)` là mã đã có sẵn).
6. **Fast gate** — `F:/runs/mlr-next/r10/c/fast.log`: `784 passed`, mã thoát 0, đúng lời tác giả. Không dùng làm bằng chứng chính.
7. **Docstring/tên test** — docstring `scale_state_of` cập nhật đúng và nêu mã ML-ACC-R10 (R-01/R-02 đạt); docstring bảng test và test HTTP sửa đúng ("ML-ACC-R9/R10", "Tám loại tầng"); tên ca `chain-checks-string…` rõ nghĩa. Không có hàm mới nên không thiếu docstring.
8. **Bốn finding 3–6 của `docs/reviews/2026-11-13-fix-scale-state-api.md`** — hồ sơ và diff không tuyên bố đã sửa. Finding 2 của lượt trước (đúng đề tài nhánh này) được xử lý đúng theo hướng "đổi luật" ở đề xuất. Cả bốn (3: thiếu ca HTTP `pageChanged`; 4: `readiness_of` chặt hơn một ca không phát sinh; 5: `scaleReasons` không giới hạn tập giá trị; 6: hai nợ cvcfp-2/`scaleDecision.sources` mới chỉ ở `changes/ML-ACC-R9-SCALE.md`) vẫn **mở**, không bị che đi.

## Finding

| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | TEST | Hồ sơ liệt kê `bool` trong các dạng hỏng nhưng bảng test không có ca `bool` (`True`/`[True]`). `isinstance(True, str)` sai nên hành vi đúng (đã đọc mã), nhưng không có test khoá: sau này ai đổi `isinstance(c, str)` thành kiểm khác (ví dụ chấp nhận `bool` là `int`) sẽ không bị bắt. Cũng chưa có ca `list` lồng `list`/`None` trong phần tử riêng lẻ (ca `mixed` chỉ có `int`). | `apps/api/spatial_read/tests/test_readiness.py` (bảng `test_scale_state_of_table`, 7 ca mới) | Thêm hai ca: `{"scaleChecks": True}` và `{"scaleChecks": [None, ["x"]]}` → `("unresolved", ("basisUnknown",))`, hoặc bỏ chữ `bool` khỏi hồ sơ. Không chặn merge. |
| 2 | Nit | TEST | Test tính chất 128 tổ hợp chỉ nạp `scaleChecks` dạng danh sách hợp lệ nên chưa kiểm sự đồng thuận `scaleUnresolved ⇔ state` trên dạng hỏng; đúng theo cấu trúc (cùng một hàm) nhưng chưa có test nói ra. | `apps/api/spatial_read/tests/test_readiness.py::test_scale_unresolved_iff_state_is_doubtful` | Thêm `"bad"` (chuỗi) vào tham số `checks` (kiểu `list[str] | str`) nếu muốn. Không cần cho merge. |
| 3 | Nit | LOG | `[""]` (danh sách chuỗi chứa chuỗi rỗng) vẫn được coi là hợp lệ, sinh `scaleReasons` có `""`; FE schema `scaleReasons: z.array(z.string().min(1))` sẽ từ chối. Không phát sinh thực tế vì `build.py` chặn khoá lạ khi dựng `BuiltLayer` và `sorted(dict)` không sinh `""`; chỉ xảy ra khi sửa DB tay. | `apps/api/spatial_read/readiness.py` dòng `not all(isinstance(c, str) for c in raw)` | Nếu muốn khép hẳn: `isinstance(c, str) and c`. Không chặn merge. |
| 4 | Nit | R-34 | Hồ sơ ghi "Nợ: không có", nhưng bốn finding 3–6 của lượt trước và hai nợ của `changes/ML-ACC-R9-SCALE.md` vẫn mở (xem mục 8); nhánh này không làm chúng tệ hơn. Checkout không có `DEBT.md`. | `changes/ML-ACC-R10-CHECKS.md` dòng "Nợ" | Khi có `DEBT.md`, chép hai nợ R9 thành `NO-<nnn>`; ghi rõ "nợ R9 còn mở" ở dòng này. Không chặn merge. |

## Điểm

| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 0,20 | 5,0 | 1,00 |
| LOG | 0,20 | 4,8 | 0,96 |
| CON | 0,10 | 5,0 | 0,50 |
| PERF | 0,10 | 5,0 | 0,50 |
| RES | 0,05 | 5,0 | 0,25 |
| DB/API | 0,10 | 5,0 | 0,50 |
| TEST | 0,15 | 4,3 | 0,645 |
| OBS/MNT | 0,10 | 4,8 | 0,48 |

Tổng: 4,8 / 5

## PHÁN QUYẾT: APPROVE

Sửa đúng gốc: một luật duy nhất trong `scale_state_of` nên cả `scaleState`/`scaleReasons` lẫn `scaleUnresolved` của `readiness_of` đổi cùng lúc; dữ liệu hỏng giờ ra `unresolved/basisUnknown` thay vì tin hơn. Phần ghi `scaleChecks` trong pipeline luôn là danh sách chuỗi, và các dạng cũ hợp lệ (vắng khoá, `null`, `[]`) giữ nguyên, nên tầng tốt không bị hạ. Không đổi hợp đồng (`openapi.json`/`APPFRONT_SHA` không đụng, mô tả `wire.py` không vào OpenAPI, job `contract` xanh). CI 24/24 xanh trên 8c0bcb1d. Không có P0/P1/P2; bốn mục còn lại (P3 và Nit) không chặn merge. Điều kiện merge: giữ head 8c0bcb1d (đẩy thêm commit thì CI chạy lại và cần review lại phần chênh); việc merge thuộc phiên gọi.
