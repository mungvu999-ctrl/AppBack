# Review merge feature/door-symbol-walls → main

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review (agent độc lập, lượt 1) · Commit đầu nhánh: c2ff89cfbf90 (PR #68; nền `origin/main` b95e4d02e4e2, đã `git fetch`; GitHub `MERGEABLE`)
- Cổng: Phạm vi kiểm: đầy đủ, lấy từ CI GitHub Actions của PR (run 43359871649, headSha = c2ff89cf). Theo quy ước máy này, reviewer không chạy `run.sh verify` 17–25 phút tại chỗ (F: còn ~4–5 GB) và không chạy test tại chỗ. `gh pr checks 68` mã thoát 0: 24/24 `pass` (changes, commits, lint, typecheck, unit-shards 1–2, integration-shards 1–5, ml-shards 1–3, perf, coverage, build, contract, CodeQL, analyze actions/python).
- Độ phủ (log job `coverage`, `tools.coverage_gate` đạt): tổng dòng 99,31 % · nhánh 97,51 %; `apps/worker/pipeline_build` dòng 98,53 % · nhánh 94,57 %. Reviewer tự gộp 11 tệp `.coverage.*` tải từ artifact của run trên: `geometry.py` dòng 99,16 % · nhánh 96,49 % (330/342). Dòng mới chưa phủ: 1649–1651 (họ quạt góc), 1688 (hộp ra ngoài trang), 1709 (loại vì quạt khác vùng sát điểm).
- Ghi chú: checkout không có `RULE.md`, `RULE-CODE.md`, `DEBT.md`. Trọng số chấm theo các phán quyết trước trong `docs/reviews/`; nợ ghi ở `changes/<mã>.md`.

## Phạm vi

- Cây sạch (`git status --porcelain` rỗng). `git diff origin/main...HEAD`: 1 commit, 3 tệp, +457/−3: `geometry.py` +243/−3, `tests/test_door_symbols.py` +193 (mới), `changes/ML-ACC-R9-WALL.md` +21.
- `c2ff89cf feat(pipeline_build): drop door-symbol arcs from wall geometry`: dòng đầu đúng mẫu, có `Prompt: ML-ACC-R9`; job `commits` xanh. Hồ sơ là `changes/ML-ACC-R9-WALL.md` (mã có hậu tố `-WALL`, vẫn truy được).
- Không chạm `docs/charter/*`, `openapi.json`, `tools/contract/APPFRONT_SHA`, `uv.lock`, `pyproject.toml`. Diff không có pragma, `noqa`, `type: ignore`, skip/xfail mới, cũng không hạ ngưỡng. `cv2` đã là phụ thuộc sẵn có.
- Mã được đo bằng mã được review: `git diff 5555f2c1 c2ff89cf` trên hai tệp mã rỗng. Bản đo (`R9-wall-dong-bang.txt`) và đầu nhánh cho ra cùng mã.

## Soát nội dung

- **Luồng** (`geometry.py:1214-1221`): `_drop_door_symbols` chạy trước `_gap_walls`, ô mở gắn lên tường còn lại, `_symbol_closers` thêm đoạn khép phòng qua khoảng ô mở của cửa đã bỏ nét (chỉ vào `bridges`). Dời `input_to_wall` xuống sau `_drop_bare_gap_walls` không đổi kết quả vì tường 3c không có `inputs` và nằm cuối danh sách (docstring `geometry.py:1830`), về lâu dài lại an toàn hơn. Nét bị bỏ biến mất khỏi `input_to_wall`. Mọi chỗ dùng đều chịu được khoá vắng: `arcs.py:111` dùng `.get`, `build.py:612` dùng `if k in`, `build.py:718` dùng `.values()`.
- **Đọc quạt v5** (`_read_wedge`, `geometry.py:1675-1711`): cắt vùng quanh hộp, bỏ mực đặc ≥ 80 mm bằng mở hình thái, chấm cánh và cung theo 13 góc. Ngưỡng là cánh ≥ 0,6, cung ≥ 0,6 và hơn quạt khác vùng ≥ 0,15. Hễ không đọc được thì giữ hết. Khổ trang lệch mặt nạ đã bị chặn khi giải mã (`residual.decode_page_raster` ném `ValueError`), nên ở đây không cần kiểm lại như `read_leaf`. Tường neo không bao giờ bị bỏ. `_tied_outside` giữ vách có đầu xa nối ra tường ngoài quạt (ca X2-C1, JN0702).
- **Bằng chứng ngoài repo** (tự đọc `R9-wall-ket-qua.txt`, `R9-wall-dong-bang.txt`): đóng băng 10:06 13/11, trước khi chấm. Các tiêu chí 3c trên cvcfp 34 đều có số khớp hồ sơ: tường giả −1,62 [−2,66; −0,75], IoU +0,0067, tường thật mất 3,9 % (≤ 10 %), xem mù 0/51 cụm bị cắt, crash 0, thời gian 0,84 lần, nhóm khác 0 nét bị bỏ. Đây là kết luận trên tập phát triển với nhãn tâm AI; hồ sơ ghi rõ giới hạn (cửa đơn, cửa sổ pháp, cửa lùa chưa xử lý).
- **PERF**: mỗi hộp cửa có 4–12 giả thuyết × 13 góc × 48 điểm mẫu. Phép mở/giãn chỉ chạy trên vùng cắt (≤ ~3 lần cạnh hộp). `_in_wedges` quét mọi nét cho mỗi hộp, O(nét × hộp) bằng Python, nhưng đo ABBA vẫn 0,84 lần nền và job `perf` xanh.
- SEC/RES/CON/DB-API: hàm hình học thuần, không I/O, không trạng thái chung, không đổi hợp đồng (`WallSet`/`BuiltLayer` giữ nguyên lược đồ, chỉ đổi nội dung tường).

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | LOG / R-19 | Khi `page is None`, `_drop_door_symbols` chạy nhánh v2 (`_swing_zone` + `_touches`): bỏ nét theo hình học, không đọc mực. Ở production nhánh này **có chạy**: `tasks._read_page` trả `None` khi trang/`walls.png` vắng, quá trần byte hay hỏng (`tasks.py:111-126`), rồi `build.py:716` truyền thẳng vào. v2 chỉ là thăm dò sau khi xem số: `R6-wall-v2-tham-do.txt:1` ghi "KHONG dua di review dua tren so nay", phòng chưa chấm. Chính X2-C1 phát hiện v2 cắt vách tủ thật. R9 không đo nhánh này. Nó còn trái nguyên tắc v5 ghi trong mã "không đọc được → giữ hết" (`geometry.py:1786`): mất trang thì nền (`main`) giữ mọi nét, còn nhánh này bỏ theo luật chưa duyệt. | `apps/worker/pipeline_build/geometry.py:1797-1801`, `:1727-1743` | `if page is None: return segs, set()` (giữ như nền), xoá `_swing_zone` và `_touches`. Các test đang dựa vào `page=None` đổi sang trang có mực, hoặc khẳng định giữ nguyên. Nếu muốn giữ v2 thì phải có số đo đăng ký trước cho nhánh không trang, kèm phòng. Hiếm (chỉ khi đọc trang thất bại) nên không chặn merge. Ghi nợ ở `changes/ML-ACC-R9-WALL.md`, xử lý vòng sau. |
| 2 | P2 | MNT-05 | Nhánh +457 dòng (> 400): mã 243 + test 193 + hồ sơ 21. | toàn diff | Không đáng tách: một tính năng liền mạch, test đi kèm. Làm #1 sẽ bớt ~25 dòng mã và nhánh v2. |
| 3 | P3 | TEST | 7/11 test chạy nhánh `page=None` (v2, xem #1). Lối v5 dùng ở production chỉ có 2 test: một ca bỏ, một ca trang trắng giữ. Chưa test: họ quạt góc cho hộp gần vuông (`geometry.py:1649-1651`), dù detector thường cho hộp ôm cả quạt; ca loại vì quạt khác vùng sát điểm (`:1709`), tức chốt an toàn chống đọc nhập nhằng; hộp ngoài trang (`:1688`); cửa đôi có mực; hộp gọi sai trên tường thật có mực. | `apps/worker/pipeline_build/tests/test_door_symbols.py` | Thêm 3 ca có trang: (a) hộp vuông ôm quạt, bản lề ở góc → bỏ cánh và cung; (b) hai quạt đối xứng cùng mực → `None`, giữ hết; (c) hộp gọi sai, vách thật xuyên hộp, trang có mực → giữ. Kèm luôn khi làm #1. |
| 4 | P3 | OBS | Nét ký hiệu bị bỏ không được đếm ở đâu. Các bước bỏ khác đều cộng `dropped[...]` (`geometry.py:299-815`). Trên production không biết mỗi bản bỏ bao nhiêu nét, nên khó soát hồi quy. | `geometry.py:1805-1807` | `DROPPED_KEYS` là hợp đồng 12 khoá (B5-05), không thêm khoá trong PR này. Khi cần, ghi một log cấu trúc gồm số nét và số cửa bỏ, hoặc thêm khoá qua thay đổi hợp đồng riêng. |
| 5 | Nit | MNT | Import tên riêng `_hit` từ `swing.py` sang `geometry.py`. `wedges = (wedge,)` cùng vòng `for w in wedges` chỉ chạy cho một quạt. `inside = within` là bí danh thừa. | `geometry.py:47`, `:1790-1796` | Khi chạm lại, đổi `_hit` thành tên công khai (`hit_ratio`) và gỡ tổng quát hoá một phần tử. |

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 3,5 | 0,525 |
| PERF | 10% | 4,5 | 0,45 |
| RES | 10% | 5 | 0,50 |
| DB/API | 10% | 5 | 0,50 |
| TEST | 7% | 3,5 | 0,245 |
| OBS/OPS | 5% | 4 | 0,20 |
| MNT | 3% | 3,5 | 0,105 |

Tổng: 4,53 / 5

## PHÁN QUYẾT: APPROVE

Lối chính (có trang, đọc quạt trên mực) được đo theo tiêu chí đóng băng trước khi chấm, trên đúng mã của đầu nhánh, và đạt mọi tiêu chí 3c trên tập phát triển cvcfp 34. Mã an toàn theo hướng giữ: không đọc được quạt thì giữ, tường neo không bao giờ bị bỏ, vách nối ra ngoài quạt cũng giữ. CI đầy đủ của PR xanh 24/24 trên c2ff89cf; `geometry.py` phủ 99,16/96,49 %. Không có P0/P1, điểm 4,53 ≥ 4,0. Hai P2 không chặn merge nhưng phải ghi nợ trước hoặc ngay sau merge: #1, nhánh `page=None` dùng luật v2 chưa duyệt (nên đổi thành giữ hết, kèm các test #3); #2 là ghi nhận kích thước, không cần tách. Merge thuộc phiên gọi review.

---

# Lượt 2: soát vòng sửa 2ae80b15

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review (cùng phiên lượt 1) · Commit đầu nhánh: 2ae80b158702 (sau phán quyết 8e774a06; `origin/main` vẫn b95e4d02)
- Cổng: Phạm vi kiểm: đích (lượt 2 chỉ soát diff vòng sửa), dựa vào CI đầy đủ của PR: run 43380835771, headSha = 2ae80b15, `gh run watch --exit-status` mã thoát 0, `gh pr checks 68` 24/24 `pass`. Không chạy cổng hay test tại chỗ.
- Độ phủ (log job `coverage`): tổng dòng 99,31 % · nhánh 97,50 %; `apps/worker/pipeline_build` 98,52 % · 94,54 %. Reviewer tự gộp `.coverage.*` của run: `geometry.py` dòng 99,14 % · nhánh 96,43 % (324/336).

## Soát diff vòng sửa (`git diff 8e774a06 2ae80b15`: 3 tệp, +83/−61)

- **P2 #1, đã sửa** (`geometry.py:1758`): `if page is None or not segs or not chosen: return segs, set()`. Mất trang thì giữ hết như `main`. `_swing_zone`, `_touches` và nhánh `else` đã xoá. Lối có trang giữ nguyên logic, chỉ bỏ bí danh `inside = within`. Test `no_page_keeps_everything_like_main` khoá hành vi mới. Bằng chứng ngoài repo (`F:/runs/mlr-next/r9/w/fix-p2.txt`): 5/5 ảnh cvcfp có trang dựng lại giống hệt `out/v5` sau khi bỏ id ngẫu nhiên và thời gian. Lối có trang không đổi, nên số đo đóng băng của lượt 1 vẫn đúng cho mã này.
- **P3 #3, đã sửa:** test cũ chuyển sang trang có mực (`_ink`), thêm 3 ca: hộp vuông, bản lề ở góc; hai quạt đối xứng (cửa đôi) phải ra `None` và giữ hết; hộp gọi sai trên vách thật có mực phải giữ hết. Độ phủ xác nhận họ quạt góc (`_wedge_hypotheses`) và nhánh loại vì quạt khác vùng sát điểm trong `_read_wedge` nay đã chạy. Test cửa đôi cũ khẳng định bỏ cả hai cánh qua v2; nay thay bằng ca "giữ hết", khớp giới hạn đã ghi (v5 chưa xử lý cửa đôi).
- `changes/ML-ACC-R9-WALL.md` thêm dòng ghi vòng sửa. Commit đúng mẫu, có `Prompt: ML-ACC-R9`; job `commits` xanh. Không pragma/`noqa`/skip mới.

## Finding lượt 2
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 6 | P3 | TEST | Sau khi chuyển test sang trang có mực, hai chốt bảo vệ tường thật không còn test nào chạy tới: `_tied_outside` trả `True` (vách tủ nối ra ngoài quạt, sửa X2-C1), và `_symbol_of` trả `False` khi nét nằm trên tim tường neo. Trong `closet_wall_at_jamb_kept`, vách tủ nằm ngoài quạt 1/4 tròn nên bị `_in_wedges` loại trước, chưa tới chốt. Xoá một trong hai chốt thì test vẫn xanh. Độ phủ file vẫn đạt (99,14/96,43). | `apps/worker/pipeline_build/geometry.py:1611`, `:1740`; `tests/test_door_symbols.py:163-172` | Thêm một ca có mực: vách thật nằm trọn trong quạt, đầu xa nối tường ngoài quạt, phải giữ. Thêm một ca: mẩu tường chủ trùng tim neo trong quạt, phải giữ. Không chặn merge. |
| 7 | Nit | MNT | Chữ cũ còn sót: docstring `"""v2: …"""` ở hai test (`test_door_symbols.py:147`, `:157`); chú thích `ponytail:` "cần đọc mực (cung tròn) nếu đo thấy ca đó" (`geometry.py:1764`), trong khi giờ đã đọc mực. `sliding_door_…`, `false_box_does_not_cut_real_wall`, `thin_box_keeps_real_wall_from_jamb` vẫn chạy `page=None`, nên nay chỉ đi qua lối thoát sớm, không còn kiểm luật bỏ nét. Ca hộp gọi sai đã có bản có mực (#3c). | như cột mô tả | Khi chạm lại: sửa docstring/chú thích, chuyển hai test còn lại sang `_ink(...)` hoặc đổi tên cho đúng việc chúng kiểm. |

Finding lượt 1: #1 (P2) và #3 (P3) đã đóng. #2 (P2 MNT-05) vẫn là ghi nhận kích thước, vòng sửa còn làm nhánh gọn hơn (+219 dòng mã ở `geometry.py`). #4 (P3 OBS) và #5 (Nit) giữ nguyên, không chặn.

## Điểm lượt 2
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 4,5 | 0,675 |
| PERF | 10% | 4,5 | 0,45 |
| RES | 10% | 5 | 0,50 |
| DB/API | 10% | 5 | 0,50 |
| TEST | 7% | 4 | 0,28 |
| OBS/OPS | 5% | 4 | 0,20 |
| MNT | 3% | 3,5 | 0,105 |

Tổng: 4,71 / 5

## PHÁN QUYẾT LƯỢT 2: APPROVE

Vòng sửa đóng đúng P2 #1: mất trang thì giữ hết như `main`, luật v2 chưa duyệt đã xoá. P3 #3 cũng đóng: đã có test cho họ quạt góc và cho chốt đọc nhập nhằng. Lối có trang không đổi (5/5 ảnh giống hệt `out/v5`), nên số đo đóng băng vẫn áp dụng. CI đầy đủ trên 2ae80b15 xanh 24/24, `geometry.py` phủ 99,14/96,43 %. Không có P0/P1, điểm 4,71. Hai finding mới (#6 P3, #7 Nit) để vòng sau. Merge thuộc phiên gọi review.
