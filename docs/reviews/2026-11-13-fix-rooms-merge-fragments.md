# Review merge fix/rooms-merge-fragments → main

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review (agent độc lập, lượt 1) · Commit đầu nhánh: eb610878c01e (PR #65; merge-base d1deb23c0294, `origin/main` 6a53970f40068 — 3 commit mới trên main không chạm `apps/worker/pipeline_build/`; `git merge-tree` sạch, GitHub `MERGEABLE`)
- Cổng: Phạm vi kiểm: đầy đủ — bằng CI GitHub Actions của PR (run 43243527492, `pull_request`, headSha = eb610878), theo quy ước máy này không chạy `run.sh verify` 17–25 phút tại chỗ; reviewer không chạy cổng, Docker hay test tại chỗ. `gh pr checks 65` mã thoát 0: 24/24 `pass` (changes, commits, lint, typecheck, unit-shards 1–2, integration-shards 1–5, ml-shards 1–3, perf, coverage, build, contract, CodeQL, analyze actions/python).
- Độ phủ (log job `coverage` của run trên): tổng dòng 99,31 % · nhánh 97,51 % — `apps/worker/pipeline_build`: dòng 98,52 % · nhánh 94,49 %. Hàm mới `_shared_edges` không có nhánh, chạy qua hai test hồi quy.
- Ghi chú: checkout không có `RULE.md`, `RULE-CODE.md`, `DEBT.md`; trọng số chấm theo các phán quyết trước trong `docs/reviews/`, nợ (nếu có) ghi ở `changes/<mã>.md`.

## Phạm vi

- Cây sạch (`git status --porcelain` rỗng). `git diff origin/main...HEAD`: 2 commit, 3 tệp, +51/−1 (logic +11/−1 ở `rooms.py`).
- `3f27d3a8 fix(pipeline_build): keep only shared edge lines when merging fragments` (`Prompt: ML-ACC-R5`) và `eb610878 docs(changes): add review dossier …` (`Prompt: ML-ACC-R8`): đúng mẫu, có trailer; job `commits` xanh. Có `changes/ML-ACC-R8.md` mô tả cả commit sửa (mã R5).
- Không chạm `docs/charter/*`, `openapi.json`, `tools/contract/APPFRONT_SHA`, `uv.lock`, `pyproject.toml`. Không pragma, `noqa`, `type: ignore`, skip/xfail mới, không hạ ngưỡng. Hai `# fmt: skip` chỉ giữ hàng toạ độ test dễ đọc.

## Soát nội dung

- **Gốc lỗi** (`rooms.py:1268-1275`): trước sửa `shared = polygon.exterior.intersection(alive[j].exterior)` có thể là `GeometryCollection` lẫn đường + điểm; `weak = union_all([])` rỗng khi không có `long_closers`; `shared.intersection(weak)` → GEOS 3.13 OverlayNG ném `GEOSException`. Sửa ở đúng điểm sinh `shared`, dùng chung cho cả ba nhánh điểm số (`∩ weak`, `.length`, `− near`) → sửa gốc, không bắt ngoại lệ (R-19).
- **Bảo toàn kết quả**: `_shared_edges` (`rooms.py:1291-1298`) giữ phần `get_dimensions == 1`; điểm có độ dài 0 nên `shared.length`, `(shared ∩ weak).length`, `(shared − near).length` không đổi về số. Kết quả overlay GEOS là tập phẳng (LineString/Point), `get_parts` một tầng đủ; không có phần → `MultiLineString` rỗng, mọi điểm số = 0 như trước (giao viền rỗng/điểm vốn cho 0). `alive[j]` luôn là `Polygon` (hợp không ra đa giác đặc thì bỏ qua, `rooms.py:1281-1282`) nên `.exterior` an toàn.
- **Bằng chứng ngoài repo** (tự đọc `F:/runs/mlr-next/r5-rooms/compare.txt`): các ca khác `GIONG` (cùng số tường/ô mở/phòng); cis-429 và cvcfp-IIa_SL0801 từ CRASH → completed. Khớp hồ sơ.
- **TEST** (`test_rooms.py:1319-1345`): hai cặp phòng thật, gọi thẳng `_merge_fragments` với `long_closers` rỗng → trước sửa ném, sau sửa trả nguyên hai phòng (hợp có lỗ). Không phụ thuộc thời gian/thứ tự, chạy song song được.
- **PERF**: thêm `get_parts` + `get_dimensions` trên vài đoạn mỗi cặp phòng kề — không đáng kể; job `perf` xanh.
- SEC/RES/CON/DB-API/OBS: hàm hình học thuần, không I/O, không đổi hợp đồng, không log.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | TEST | Test hồi quy chỉ phủ đường "không gộp" (hợp có lỗ → giữ hai phòng). Chưa có ca cạnh chung đường + điểm mà phòng **có** được gộp (ví dụ `long_closers` khác rỗng), nên chưa khoá rằng điểm số vẫn đủ ngưỡng khi có thành phần điểm. Rủi ro thấp vì lập luận độ dài 0 và đối chứng 36/36. | `apps/worker/pipeline_build/tests/test_rooms.py:1338-1345` | Khi chạm lại `_merge_fragments`, thêm một ca đường + điểm có `long_closers` ≥ 300 mm và khẳng định phòng nhỏ được gộp. Không chặn merge. |
| 2 | Nit | MNT | Commit sửa mang `Prompt: ML-ACC-R5`, hồ sơ là `changes/ML-ACC-R8.md`; dòng đầu hồ sơ đã nói rõ (commit 3f27d3a8, mã R5) nên truy vết được. | `changes/ML-ACC-R8.md:1` | Không cần sửa. |

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 4,5 | 0,675 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 5 | 0,50 |
| DB/API | 10% | 5 | 0,50 |
| TEST | 7% | 4 | 0,28 |
| OBS/OPS | 5% | 4,5 | 0,225 |
| MNT | 3% | 5 | 0,15 |

Tổng: 4,83 / 5

## PHÁN QUYẾT: APPROVE

Sửa nhỏ, đúng gốc (lọc thành phần điểm khỏi cạnh chung trước mọi phép overlay), không đổi điểm số với hình hợp lệ, có test hồi quy hai ca thật và bằng chứng đối chứng 36 ca. CI đầy đủ của PR xanh 24/24 trên đúng đầu nhánh, độ phủ tổng 99,31/97,51 %, `pipeline_build` 98,52/94,49 %. Không P0/P1/P2; finding #1 (P3) là gợi ý cho lần chạm sau, không cần dòng nợ. Merge thuộc phiên gọi review.
