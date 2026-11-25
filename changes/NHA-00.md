# NHA-00 — bộ đo bộ hồ sơ trên đường sản phẩm

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.G, §5 (lát NHA-00). Người dùng duyệt GATE 1 ngày 2026-11-25.
Nhánh dựng trên `feature/k33-muc3-bench` (K-33, chưa gộp): bộ chấm `score_set.py` đã được chuyển vào
`tools/bench/muc3/` ở đó, nên lát này **không chuyển lại** và không tạo `tools/bench/dset/` như thiết kế ghi.

- `tools/bench/muc3/readings.py` (mới): dựng đệm đọc trang bằng đúng đường K-32a đã đo (`jobs._render_page` 24 Mpx →
  `drawing_set_read._read_one`, `RapidOcrReader` trên model ghim của wheel qua `load_onnx`); `MANIFEST.json` ghi sha256.
- `tools/bench/muc3/predict.py` (mới): đệm → `analysis._to_domain` → `solve_set` → dự đoán theo `SCHEMA.md`; khung máy
  được đặt mã hình đáp án bằng `sheets.match_boxes` (IoU >= 0,5); ghi `split_clean.json` / `split_exposed.json`
  (27 bộ đã lộ của K-32a).
- `packages/domain/drawing_set/set_analysis.py`: tách `solve_set` (trả thêm `FloorProposal` của từng mặt bằng) khỏi
  `guess_views`; `guess_views` giữ nguyên chữ ký và kết quả. Không sửa luật nào.
- Không đụng baseline/`ml-cache` của cổng bench cũ; không sửa tệp sẵn có nào của K-33 ngoài `README.md`.

## Số "trước" (2026-11-25)

Đệm: 50 tờ của 51 bộ `test` + `dev` (một bộ không có tờ ghép → tính là không dự đoán), `MANIFEST.json` sha256
`9259e86a…`, ngoài repo ở `F:/runs/dwglab/muc3-nha00/readings`. Đáp án `truth_v3`. Mỗi ô: đúng/tổng (Wilson 95 %, tính
ngoài thước). "Sạch" = 24 bộ không thuộc `predict.EXPOSED`; phần lớn là bộ quốc tế. Chạy `predict` hai lần cho tệp trùng
từng byte.

| Nhóm | Hình tìm được | Đúng loại / hình khớp | Đúng loại / đáp án | Số tầng | Bộ đúng hết | Chiều cao ±50 mm |
|---|---|---|---|---|---|---|
| sạch test (16 bộ) | 80/103 (69–85 %) | 23/80 (20–39 %) | 22/104 (14–30 %) | 3/40 (3–20 %) | 0/16 (0–19 %) | 0/3 |
| sạch dev (8 bộ) | 55/79 (59–79 %) | 16/55 (19–42 %) | 16/78 (13–31 %) | 2/20 (3–30 %) | 1/8 (2–47 %) | — |
| đã lộ test (18 bộ) | 153/194 (73–84 %) | 94/153 (54–69 %) | 92/186 (42–57 %) | 27/48 (42–69 %) | 1/18 (1–26 %) | 2/31 (2–21 %) |
| đã lộ dev (9 bộ) | 79/102 (68–84 %) | 37/79 (36–58 %) | 37/102 (28–46 %) | 10/27 (22–56 %) | 0/9 (0–30 %) | 2/20 (3–30 %) |

- Đối chiếu K-32a (18 tờ đã lộ/test của nó): hình tìm được 141/186 (76 %) so với 153/194 (79 %) ở đây — cùng đường
  đọc, khác chỗ K-32a bỏ hình `minor`/`unknown` khỏi mẫu số. "Đúng loại" của K-32a tính mặt bằng mái là mặt bằng;
  thước này chấm `roof` riêng nên số thấp hơn, không so trực tiếp.
- Nhóm sạch thấp hẳn ở loại hình (29 % trên hình khớp so với 61 %): tên hình tiếng nước ngoài — mốc "trước" của NHA-03.
- Chiều cao: sản phẩm chỉ đề xuất 26/51 cặp có đáp án ở nhóm đã lộ, đúng 4. Bộ quốc tế gần như không có đáp án
  chiều cao (3 cặp).
- Giới hạn: tờ là bản CAD đổi ra ảnh giả quét 150 dpi ghép lại, không phải PDF người dùng thật; OCR là model ghim
  của wheel (không dấu tiếng Việt).

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-25, cây làm việc trên `fd5bc2f9`): bước 1 ruff format, 2 ruff
  check, 3 mypy --strict, 4 lint-imports, 5 pytest bị ảnh hưởng (97 tệp test, 2486 đạt; độ phủ đơn vị
  `packages/domain`, `tools` đạt), 8 openapi — **đạt**. Bước 5b, 6, 7 — **chưa chạy** (cổng nhanh không chạy).
- Phạm vi kiểm: fast — **chưa đủ để merge**. Lát chạm `tools/**` và `packages/domain/**` (sàn cứng R-33b), thêm file
  `.py` mới: phải `run.sh verify` đầy đủ trước `/merge-review`.
- Review: `ecc:python-reviewer` (không CRITICAL; 2 HIGH đã sửa: chọn hình có chiều cao khi trùng số tầng, ghi đệm qua
  tệp tạm; 3 MEDIUM đã sửa hoặc ghi README). Không chạy `security-reviewer`: công cụ đo, không có đầu vào người dùng.

## Nợ và việc chưa làm

- NO-569 — `score_roof.py` (84 dòng, ngoài repo) chưa chuyển: sản phẩm chưa sinh mái nên chưa có gì để chấm. Gốc: mái
  thuộc lát NHA-05b/NHA-07a. Chủ: NHA-10. P3. Mở.
- NO-570 — Thước không chấm cốt tầng và chiều cao tầng trên cùng (chỉ cặp `(from, to)` ± 50 mm), không in khoảng tin
  cậy Wilson, và "lỗi im lặng" của nó là "sai ở độ chắc >= 0,9" chứ không phải "sai mà không kèm câu hỏi" của K-32a.
  Gốc: giữ nguyên bộ chấm K-33 để số so được. Chủ: `tools/bench/muc3` (K-33/NHA-10). P3. Mở.
