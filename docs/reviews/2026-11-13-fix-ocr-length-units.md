# Review merge fix/ocr-length-units → main

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review (agent độc lập, lượt 1) · Commit đầu nhánh: 2a19a73e82a6 (PR #67; merge-base = `origin/main` b95e4d02e4e2 sau `git fetch`, GitHub `MERGEABLE`)
- Cổng: Phạm vi kiểm: đầy đủ — bằng CI GitHub Actions của PR (run 43359859988, headSha = 2a19a73e), theo quy ước máy này không chạy `run.sh verify` 17–25 phút tại chỗ (F: còn ~4–5 GB, không dựng ảnh/worktree). `gh pr checks 67 --repo mungvu999-ctrl/AppBack` mã thoát 0: 24/24 `pass` (changes, commits, lint, typecheck, unit-shards 1–2, integration-shards 1–5, ml-shards 1–3, perf, coverage, build, contract, CodeQL, analyze actions/python). Reviewer không chạy test tại chỗ; test đích `test_chain_units.py` chạy trong các shard CI trên đúng SHA.
- Độ phủ (log job `coverage` 131672797461): `tools.coverage_gate (VERIFY_BRANCH=integration)` đạt, `case_gate` đạt — `apps/worker/pipeline_build`: dòng 98,62 % · nhánh 94,98 %. Mọi nhánh mới của `_chain_samples`/`_metric_mm`/`_largest_cluster` có ca test riêng (xem TEST).
- Ghi chú: checkout không có `RULE.md`, `RULE-CODE.md`, `DEBT.md`; trọng số chấm theo các phán quyết trước trong `docs/reviews/`, nợ ghi ở `changes/<mã>.md`.

## Phạm vi

- Cây sạch (`git status --porcelain` rỗng). `git diff origin/main...HEAD`: 4 commit, 4 tệp, +224/−3; logic +56/−3 ở `apps/worker/pipeline_build/build.py`, còn lại là test (154 dòng), dữ liệu test (1 dòng JSON) và hồ sơ.
- `0227305a fix(pipeline_build): …`, `6f70d91f docs(changes): …`, `cd3b20eb test(pipeline_build): …`, `2a19a73e docs(changes): …`: đúng mẫu Conventional Commits, đều có `Prompt: ML-ACC-R9`; job `commits` xanh. Hồ sơ `changes/ML-ACC-R9-OCR.md` có.
- Không chạm `docs/charter/*`, `openapi.json`, `tools/contract/APPFRONT_SHA`, `uv.lock`, `pyproject.toml`. Không pragma, `noqa`, `type: ignore`, skip/xfail mới, không hạ ngưỡng.

## Soát nội dung

- **Gốc lỗi**: `_chain_samples` gọi `parse_length_mm` cho mọi số trần → trên bản vẽ "mét-cm" (cvcfp-2: `2.25` = m, `80` = cm) số mét thập phân bị bỏ, số cm đọc thành mm, cụm "70/60/80/90" quyết 1,79 mm/px thay 18,19. Sửa ở `_chain_samples`, nơi cả ba người gọi trong bước dựng đi qua (`_scale` dòng 328, `_axis_clusters` dòng 466, `_chain_dimensions` dòng 704) → một quyết định đơn vị cho cả trang, nhất quán giữa tỉ lệ, cụm theo trục và `Dimension` dựng từ chuỗi (R-19).
- **An toàn cho trang mm** (`build.py:410-421`): chỉ chọn "mét-cm" khi cụm lớn nhất của nó ≥ 2 mẫu, đông hơn hẳn cụm mm và chứa ≥ 1 chữ mà cách đọc mm bỏ. Trang chỉ có số nguyên không bao giờ thoả (c), nên bản vẽ mm thuần (VN/Nhật) giữ nguyên prod. Hoà → mm. Chữ có đơn vị: `_metric_mm` rơi về `parse_length_mm` (`build.py:456`) → hai cách đọc như nhau.
- **`_metric_mm`** (`build.py:448-456`): `\d{1,2}[.,]\d{1,3}` → mét qua `Fraction` (không lỗi làm tròn float, chỉ nhận mm nguyên > 0; `0.00` → None); `\d{1,4}` → cm ×10, `000` → None. `3.450` cho 3450 ở cả hai cách (dấu nhóm nghìn mm = 3,450 m) nên không lọt vào tập "chữ mm bỏ". 5 chữ số và chữ không phải số rơi về `parse_length_mm`. Đúng các case biên đã liệt kê trong test.
- **TEST** (`test_chain_units.py`, 27 ca): bảng từng chữ (16 ca, gồm chấm/phẩy thập phân, nhóm nghìn chấm và khoảng trắng, chữ hỏng `48OO`); bảng cấp trang 8 ca phủ từng điều kiện (a) `mot-so-met`, (b) `hoa`, (c) `mm-thuan-ngoai-dai`/`mm-ngoai-dai-cao-do`, đường chọn mét `met-cm`; hồi quy cvcfp-2 với chữ OCR thật qua `build_wrapped` → `chain` trong 1 % của 18,19; hai trang thật trộn quy ước giữ prod. Không phụ thuộc thời gian thật (FakeClock), không thứ tự, chạy song song được.
- **Bằng chứng ngoài repo** (tự đọc `docs/ml-accuracy-research-next/evidence/R9-ocr-ket-qua.txt`): 60 tham chiếu r6 — chỉ 9 ảnh cvcfp đổi, đều giảm e (cvcfp-2 2,316 → 0,000), 0 ảnh từ e ≤ 5 % lên > 15 %; 18 ảnh tập mới đóng băng trước khi chạy — 0 ảnh đổi. Khớp hồ sơ. Giới hạn hồ sơ tự nêu đúng: cách đọc mét-cm **chưa được kiểm ngoài tập phát triển** (ảnh mét-cm duy nhất của tập mới không có chuỗi kích thước); tập mới chỉ chứng minh "không làm hỏng" trang mm.
- **PERF**: `_chain_samples` giờ đọc hai lượt + hai lần `_largest_cluster` O(n²) với n = số chữ có `span_px` (vài chục–vài trăm), gọi tối đa 3 lần/trang; không đáng kể, job `perf` xanh.
- SEC/RES/CON/DB-API: hàm thuần trên chữ OCR, không I/O, không đổi hợp đồng `layer.json`/API/DB, không phụ thuộc mới.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | R-07 | Bản sao luật chuỗi ở bước ML (`_chain_scale_found`) vẫn đọc mọi số trần bằng `parse_length_mm`, trong khi docstring của nó khẳng định "cùng phép của bước dựng (`pipeline_build.build._chain_samples` …)". Sau PR này hai bên lệch: trang nhỏ (< 2000 px) kiểu mét-cm mà cách đọc mm không ra cụm sẽ bị bước ML coi là "chưa có tỉ lệ chuỗi" → thêm một lượt OCR phóng, và chỉ nhận lượt phóng theo phán đoán mm. Không hồi quy so với main (bước ML không đổi, số đo R9 đã chạy qua đúng bước ML này), nhưng docstring giờ sai và quyết định lượt đọc lại có thể khác quyết định đơn vị của bước dựng. | `apps/ml/text/tasks.py:119-137` | Khi chạm lại bước ML: chuyển phép đọc hai cách (`_metric_mm` + luật chọn) vào `packages/vision/dimensions` để cả hai bước dùng chung, hoặc ít nhất sửa docstring nói rõ bước ML chỉ đọc mm. Không chặn merge. |
| 2 | Nit | R-10 | `_read_chain(texts, read: Any)` khai tham số hàm là `Any`, mypy không kiểm được chữ ký `read(chữ) -> mm \| None`. | `apps/worker/pipeline_build/build.py:436` | `read: Callable[[str], int \| None]` (`collections.abc.Callable`). |
| 3 | Nit | OBS | Log `pipeline_build_chain_unit` nằm trong `_chain_samples`, mà hàm này được gọi 2–3 lần một trang (`_scale`, `_axis_clusters`, `_chain_dimensions`) → cùng một sự kiện log lặp. | `apps/worker/pipeline_build/build.py:419` | Chấp nhận được; nếu muốn một dòng/trang thì log ở `_scale` theo kết quả. |
| 4 | Nit | R-07 | `_largest_cluster` lặp vòng tâm thử của `densest_cluster` (bỏ luật hoà); docstring đã nêu lý do (cần cỡ cụm cả khi hoà). | `apps/worker/pipeline_build/build.py:424-433` | Không cần sửa; nếu gom về `chains.py` (finding #1) thì đưa luôn hàm này. |

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 4,5 | 0,675 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 5 | 0,50 |
| DB/API | 10% | 5 | 0,50 |
| TEST | 7% | 4,5 | 0,315 |
| OBS/OPS | 5% | 4,5 | 0,225 |
| MNT | 3% | 4 | 0,12 |

Tổng: 4,84 / 5

## PHÁN QUYẾT: APPROVE

Sửa đúng gốc ở hàm mà mọi người gọi trong bước dựng đi qua, có luật căn cứ ba điều kiện để trang mm thuần không bao giờ bị đổi, test phủ từng điều kiện cùng hồi quy cvcfp-2 trên chữ OCR thật, và số đo đối chứng (9 ảnh cvcfp cải thiện, 0 ảnh xấu đi, tập mới 0 ảnh đổi) khớp hồ sơ. CI đầy đủ của PR xanh 24/24 trên đúng đầu nhánh 2a19a73e, `pipeline_build` 98,62/94,98 %. Không P0/P1/P2; finding #1 (P3) là lệch tài liệu/bản sao ở bước ML cho lần chạm sau, không cần dòng nợ. Lưu ý cho người merge: cách đọc mét-cm mới có bằng chứng tích cực trên tập phát triển (hồ sơ tự nêu). Merge thuộc phiên gọi review.
