# NHA-04 — mặt cắt/mặt đứng tự vào thang cốt; tầng trên cùng hết chiều cao mặc định

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.B mục 2, §2.D mục 2, §2.C Câu 6, §5 (lát NHA-04). Người dùng duyệt GATE 1
ngày 2026-11-25. Nhánh dựng trên `feature/nha-00-dset-bench` (cần `solve_set` và thước đo của NHA-00).

- `packages/domain/drawing_set/set_analysis.py`: trong luồng bộ hồ sơ, hình máy xếp `section` với độ tin
  >= `NOT_PLAN_SURE` (0,8) **và đọc được từ 3 cốt** tự vào `solve_house(sections=…)`. Trước đây chỉ dòng người dùng tự
  đánh dấu mới vào thang cốt, và chỉ ở N52. Số tầng không đổi; chỉ cốt/chiều cao (nguồn `plan+section`, `section`)
  trong `solve_set` đổi. Mặt đứng (`elevation`) chưa vào — xem "Lệch khỏi thiết kế".
- `packages/domain/drawing_set/heights.py`: tầng trên cùng lấy chiều cao tới bậc thang hợp lý phía trên sàn
  (2,4-4,6 m, chọn bằng `_plausible_rungs` như `_fill_defaults`) — nguồn `section` ở 0,55, lớp trên vẫn hỏi
  `HEIGHT_ASSUMED`. Không có bậc thì giữ 3,3 m ở 0,4 `assumed`. N52 (dự án có dòng mặt cắt) cũng nhận thay đổi này.
- Test sửa trước, đỏ 7 ca rồi mới sửa mã: `test_heights.py` (biên 2,39 / 2,4 / 4,6 / 4,61 m; hai bậc trong dải),
  `test_proposal.py` (tầng trên cùng của ca hai nguồn), `test_set_analysis.py` (ngưỡng 0,8 / 0,79; `elevation` và
  `roof` không vào; 3 cốt vào, 2 cốt không).

## Lệch khỏi thiết kế

- **Chỉ `section`, chưa `elevation`, và thang tự động cần >= 3 cốt** (thiết kế: cả hai loại, không nêu số cốt). Lý do
  là số đo dưới đây: bản đầu (cả hai loại, từ 2 cốt) làm bộ DS-836 mất một chiều cao đúng — ba mặt đứng "đọc được
  thang" thực ra là số kích thước bị đọc thành cốt, và mặt cắt chỉ có hai cốt (4.1, 7.0) đè cốt mặt bằng 3.58 đúng
  (đáp án 3.6). DS-836 thuộc nhóm **đã lộ, phần test**: hai chốt này được đặt sau khi nhìn nó, nên không được coi
  là đã kiểm trên dữ liệu sạch.
- Chọn bậc "gần 3,3 m, nhiều mặt cắt hơn" thay vì "bậc đầu tiên phía trên sàn": cốt lanh tô (+2,7 m trên sàn) là bậc
  thấp nhất trong dải và sẽ thắng sàn mái; `_plausible_rungs` đã là luật của `_fill_defaults`.
- **Chưa làm** hai mục của lát trong thiết kế vì chưa có nơi dùng (R-10): `solve_chain` trả danh sách "cốt trên mái"
  và `side_levels` trả vị trí + chữ gốc. Cả hai phục vụ phân vai diềm/đỉnh/parapet của `RoofSpec` → làm ở NHA-05b.
  Chiều cao tầng trên cùng lấy thẳng từ thang cốt có sẵn trong `solve_heights`, không cần danh sách đó.

## Số trước/sau

Thước NHA-00, cùng đệm (`MANIFEST.json` sha256 `9259e86a…`, 50 tờ của 51 bộ), đáp án `truth_v3`, đúng/tổng đáp án:

| Nhóm | Loại hình | Số tầng | Bộ đúng hết | Chiều cao (±50 mm) |
|---|---|---|---|---|
| sạch, test (16 bộ) trước = sau | 22/104 | 3/40 | 0/16 | 0/3 |
| sạch, dev (8 bộ) trước = sau | 16/78 | 2/20 | 1/8 | 0/0 |
| đã lộ, test (18 bộ) trước = sau | 92/186 | 27/48 | 1/18 | 2/31 |
| đã lộ, dev (9 bộ) trước = sau | 37/102 | 10/27 | 0/9 | 2/20 |

- **Không chỉ số nào đổi.** Dự đoán chỉ khác ở 1/50 bộ (DS-396, dev, đã lộ): chiều cao tầng 1→2 = 4,35 m (đúng đáp án)
  từ nguồn `plan` lên `plan+section`; tầng 3 có cốt 8,25 m và tầng 2→3 = 3,9 m từ mặt cắt (đáp án không có để chấm).
- Vì sao gần như không tác dụng trên dữ liệu này: 49 hình mặt cắt/mặt đứng được xếp chắc (>= 0,8) nhưng chỉ 4 hình
  `side_levels` đọc ra thang cốt — OCR trên tờ ghép 24 Mpx hiếm khi đọc được ký hiệu cốt. Lợi ích thật phụ thuộc
  chất lượng đọc chữ (NHA-14, NO-546), không phụ thuộc lát này.
- Chiều cao tầng trên cùng **không được thước chấm** (NO-570): thay đổi ở `heights.py` chỉ có test đơn vị.
- Bản đầu của lát (cả `elevation`, thang từ 2 cốt): đã lộ/test 2/31 (một đúng thành sai, một sai thành đúng ở DS-836);
  bản chỉ `section` từ 2 cốt: 1/31. Bản hiện tại: 2/31, DS-836 không đổi.

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-25, cây làm việc gồm cả NHA-00 trên `fd5bc2f9`): bước 1 ruff
  format, 2 ruff check, 3 mypy --strict, 4 lint-imports, 5 pytest bị ảnh hưởng (99 tệp test, 2546 đạt, gồm test của
  `apps/api/drawing_sets`; độ phủ đơn vị `packages/domain`, `tools` đạt), 8 openapi — **đạt**. Bước 5b, 6, 7 — **chưa
  chạy** (cổng nhanh không chạy).
- Phạm vi kiểm: fast — **chưa đủ để merge**. Lát chạm `packages/domain/**` (sàn cứng R-33b): phải `run.sh verify` đầy
  đủ trước `/merge-review`. Cổng bench cũ (`BENCH_ROOT`) **chưa chạy**: nó phát lại luồng một tầng, không đi qua
  `set_analysis.py`; `heights.py` chỉ đổi phần tử tầng trên cùng.
- Review: `ecc:code-reviewer` (không CRITICAL/HIGH; 2 MEDIUM thành NO-572, NO-573; LOW đã sửa hoặc thành NO-574).

## Nợ và việc chưa làm

- NO-571 — "Cốt trên mái" (`chain.py`) và vị trí + chữ gốc của từng cốt (`sections.py`) chưa có: xem "Lệch khỏi thiết
  kế". Gốc: chưa có nơi dùng. Chủ: NHA-05b. P3. Mở.
- NO-572 — Mặt đứng chưa vào thang cốt: `levels_from_texts` đọc số kích thước trên mặt đứng thành cốt (DS-836: 1.0,
  1.5, 2.4, 3.8, 5.0…), khớp dọc 3-4 điểm vẫn qua. Gốc: chưa xác định cách phân biệt ký hiệu cốt với số kích thước
  khi chỉ có chữ OCR. Chủ: `packages/domain/drawing_set/{levels,sections}.py`. P3. Mở.
- NO-573 — Trong `solve_heights`, một bậc thang (kể cả chỉ một mặt cắt đỡ) thắng cốt riêng của mặt bằng không nằm
  trên bậc; và bậc được nhiều hình đỡ hơn thắng khi chọn chiều cao tầng trên cùng (review: nhiều mặt đứng cùng ghi
  cốt đỉnh parapet sẽ thắng cốt sàn mái của một mặt cắt). Luật có từ HS-02, nay lộ ra với mặt cắt máy tự chọn. Gốc:
  điểm bậc `_RUNG_SCORE` không xét số cốt của hình nguồn. Chủ: `heights.py` (HS-02). P2. Mở.
- NO-574 — Chưa có test nào cho thấy bộ phân loại thật xếp một trang mặt cắt thực tế >= 0,8 (test của lát thay
  `classify_page`), và trang thử chỉ có một khung cả trang nên chưa đi qua phép dời toạ độ theo khung. Chủ: NHA-04.
  P3. Mở.
