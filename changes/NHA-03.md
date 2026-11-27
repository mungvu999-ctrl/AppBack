# NHA-03 — từ khoá tên hình kiểu Anh/Mỹ/Canada

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.A mục 2, §5 (lát NHA-03). Người dùng duyệt GATE 1 ngày 2026-11-26.
Nhánh dựng trên `feature/nha-06a-building-read` (đầu PR 120): cần thước đo của NHA-00, không nhánh nào sau đó chạm
`titles.py`.

- `packages/domain/drawing_set/titles.py`: `MAIN FLOOR` / `MAIN LEVEL` là tầng trệt; `SECTIONS` số nhiều là mặt cắt;
  `MAIN`, `LEVEL` vào `_VOCAB` để bước sửa lỗi OCR không đụng tới. Ba dòng mã.
- `tests/test_titles.py`: 17 dòng tên hình mới (3 đỏ trước khi sửa: `MAIN FLOOR PLAN`, `MAIN LEVEL FLOOR PLAN`,
  `BUILDING SECTIONS`; phần còn lại ghim hành vi sẵn có: `UPPER/LOWER FLOOR`, `LEVEL n`, `CROSS/LONGITUDINAL SECTION`,
  `… ELEVATIONS`, tiêu đề lẫn hai ngôn ngữ) và 5 chuỗi không được thành tên hình.
- **Vòng sửa sau merge-review PR 123** (`docs/reviews/2026-11-26-feature-nha-03-title-keywords.md`, finding 1, P2):
  `storeys._decide` coi `MAIN FLOOR` / `MAIN LEVEL` không kèm chữ trệt kiểu Anh là dấu hiệu cách đếm Mỹ/Canada
  (`us_english`), như `FIRST FLOOR`. Trước vòng này bộ `MAIN FLOOR PLAN` + `SECOND FLOOR PLAN` ra tầng 1 và tầng 3,
  không kèm câu hỏi; nay ra 1 và 2, không có cốt để kiểm thì độ tin dưới 0,7 (phải hỏi). Lệch phạm vi "chỉ `titles.py`"
  của thiết kế §5: một dòng ở `storeys.py`, vì từ khoá mới làm hai đường đánh số lệch nhau. Test:
  `test_storeys.py::test_main_floor_sets_the_north_american_count`. Finding 2 (P3) ghim bằng hai dòng `_TITLES`,
  xem NO-597.
- **Vòng sửa sau merge-review lượt 2** (P3 mới, `storeys._storeys`): tên hai ngôn ngữ `TẦNG 1 / MAIN FLOOR PLAN` +
  `TẦNG 2 / SECOND FLOOR PLAN` ra trùng tầng 1 sau vòng sửa trên (cách đếm Mỹ lùi cả tầng mà phần tiếng Việt đã
  quyết định). Với `TẦNG 1 / FIRST FLOOR PLAN` lỗi này có từ trước lát. Nay chỉ lùi tầng mà `_written_floor` đã cộng
  một vì thứ tự tiếng Anh (`numbering == "floor_ordinal"`). Test:
  `test_storeys.py::test_bilingual_titles_keep_the_vietnamese_count` (hai ca, đều đỏ trước khi sửa).

## Lệch khỏi kế hoạch đã duyệt

- **`BASEMENT`, `ATTIC`, `LEVEL n` đứng một mình không thành tên hình** (kế hoạch: `BASEMENT`/`ATTIC` một mình là mặt
  bằng). Lý do: cùng chữ đó là tên phòng trong mặt bằng và nhãn cốt trên mặt cắt; `pick_title` nhận mọi dòng
  `parse_title` đọc được, nên sẽ lấy nhầm tên phòng làm tên hình. Có chữ `FLOOR`/`PLAN` đi kèm thì vẫn nhận như cũ.
- **`LEVEL n` chưa được đọc thành số tầng** (kế hoạch: đếm như `TẦNG n`). Bản đầu có làm, lượt review chỉ ra ba lỗi:
  trên đường OCR `_snap` bỏ dấu `'` nên cốt `LEVEL 10'-0"` thành "tầng 10"; ghi chú `FINISHED FLOOR LEVEL 2` thành mặt
  bằng tầng 2; `storeys.py` (`_RELAXED`, `_FIRST_WORD`) không biết cách đếm này nên hai đường đánh số lệch nhau. Sửa
  đúng phải đụng `storeys.py` (ngoài phạm vi "chỉ `titles.py`") và không có bộ nào dùng `LEVEL n` để đo → NO-597.
- Không thêm tiếng Pháp: chưa có bộ tiếng Pháp nào để đo, thiết kế §6 câu 6 chưa được chọn.

## Số trước/sau — không đổi, và vì sao

Thước NHA-00, cùng đệm (`F:/runs/dwglab/muc3-nha00/readings`, 50 tờ của 51 bộ), đáp án `truth_v3`, đúng/tổng đáp án.
Trước = sau ở mọi ô; 50 tệp dự đoán trùng từng byte (`diff -rq`).

| Nhóm | Loại hình | Số tầng | Bộ đúng hết |
|---|---|---|---|
| sạch, dev (8 bộ) | 16/78 | 2/20 | 1/8 |
| sạch, test (16 bộ) | 22/104 | 3/40 | 0/16 |
| đã lộ, dev (9 bộ) | 37/102 | 10/27 | 0/9 |
| đã lộ, test (18 bộ) | 92/186 | 27/48 | 1/18 |

Chẩn đoán trên **tập dev** (không nhìn tập test), 78 hình có đáp án của nhóm sạch:

- Đưa thẳng tên hình đáp án (`title_raw`) vào `parse_title` của nhánh gốc: đúng loại hình ở mọi hình tiếng Anh, đúng
  cả nhãn tầng (`GROUND/FIRST/SECOND/THIRD FLOOR PLAN`, `BASEMENT FLOOR PLAN`, `ROOF PLAN`, `… ELEVATION`,
  `SECTION A-A`). Tập dev không có tên hình nào dùng từ khoá của lát này.
- Trên đường sản phẩm: 16 đúng; **38 có khung nhưng không đọc ra tên** (`ViewGuess.title_text` rỗng); **23 không có
  khung khớp**; 1 lấy nhầm tên hình bên cạnh. OCR chỉ trả 9-33 dòng chữ mỗi tờ quốc tế (6-12 hình/tờ), có tờ không
  có dòng nào đọc được thành tên hình.
- Kết luận: ghi chú "nhóm sạch thấp vì tên hình tiếng nước ngoài" ở `changes/NHA-00.md` là chẩn đoán sai. Chỗ mất là
  chữ tên hình không tới được bộ đọc tên và việc tách hình, không phải từ khoá. Lát này vì vậy chỉ có giá trị với hồ
  sơ dùng `MAIN FLOOR` hay `SECTIONS` (chưa có trong bộ đo) và được kiểm bằng test đơn vị, không bằng thước.

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-26, cây làm việc trên `1cc66a63`, mã thoát 0): bước 0 làm ấm
  node_modules, 1 ruff format, 2 ruff check, 3 mypy --strict, 4 lint-imports, 5 pytest bị ảnh hưởng (chạy cả bộ; độ phủ
  đơn vị `apps/api/drawing_sets`, `packages/domain`, `tools`), 7 hợp đồng H1/H3/H4/H5, 8 openapi — **đạt**. Bước 5b, 6 —
  **chưa chạy** (cổng nhanh không chạy).
- Review: `ecc:python-reviewer` — không CRITICAL; 1 HIGH và 2 MEDIUM đều về `LEVEL n`, xử lý bằng cách rút `LEVEL n`
  khỏi lát (NO-597); LOW về số dòng test trong ghi chú đã sửa. Không chạy `security-reviewer`: không có đầu vào mới.
- Phạm vi kiểm: fast + CI. Cổng đầy đủ (CI `ci.yml`, run 45815447539) **đạt** 20/20 job trên `3c2ba523`, bản trước vòng
  sửa.
- Vòng sửa sau merge-review (cây làm việc trên `3c2ba523`, 2026-11-26): `verify --fast` mã thoát 0, cùng các bước như
  trên — **đạt**; bước 5b, 6 — **chưa chạy**. Thước NHA-00 chạy lại: 50 tệp dự đoán trùng từng byte với vòng trước.
  Review `ecc:python-reviewer`: không CRITICAL/HIGH; một LOW chưa sửa — `_decide` quét cả hình loại khác mặt bằng, nên
  mặt cắt tên `… MAIN LEVEL` cũng bật `us_english` (lỗ sẵn có của `FIRST FLOOR`, có cốt thì `_level_ok` sửa lại). Lát
  chạm `packages/domain/**` (sàn cứng R-33b): CI phải chạy lại trên SHA mới trước khi gộp.
- Vòng sửa sau merge-review lượt 2 (cây làm việc trên `88f3db67`, 2026-11-26): `verify --fast` so với `main`, 63 tệp
  test, 2 123 đạt; bước 1, 2, 3, 4, 5, 8 — **đạt**, mã thoát 0; bước 5b, 6, 7 — **chưa chạy**. Thước NHA-00: 50 tệp dự
  đoán trùng từng byte với vòng trước. Review `ecc:python-reviewer`: không CRITICAL/HIGH/MEDIUM; LOW chưa sửa: chưa có
  ca test cho `LẦU 1 / FIRST FLOOR` (cũng thôi lùi tầng), điều kiện `_ENG.search` nay thừa nhưng vô hại.

## Nợ và việc chưa làm

- NO-596 — Nhóm bộ quốc tế mất loại hình ở khâu đọc chữ và tách hình, không ở từ khoá: 38/78 hình dev sạch có khung
  mà không có tên, 23/78 không có khung. Gốc chưa rõ: có thể là giới hạn của bộ đo (tờ CAD đổi ra ảnh giả quét 150 dpi
  rồi co về 24 Mpx làm chữ tên hình quá nhỏ) hoặc của sản phẩm (OCR trên trang lớn; thiết kế §2.A mục 4 "đọc lớp chữ
  PDF vector"). Chủ: lát riêng sau NHA-03. P2. Mở.
- NO-597 — `LEVEL n` trong tên mặt bằng chưa thành số tầng (xem "Lệch khỏi kế hoạch"). Cần: phân biệt nhãn cốt với số
  tầng trên cả đường OCR, thêm cách đếm vào `storeys.py`, và ít nhất một bộ hồ sơ dùng `LEVEL n` trong bộ đo. Cùng lớp
  nhầm nhãn cốt / ghi chú thành tên hình (merge-review PR 123, finding 2): `MAIN FLOOR` đứng một mình (nhãn cốt trên
  mặt cắt Bắc Mỹ) được đọc là mặt bằng tầng trệt, `SEE SECTIONS` / `WALL SECTIONS` là tên hình mặt cắt, như
  `GROUND FLOOR` và `SEE SECTION A-A` từ trước; hành vi hiện tại ghim ở `_TITLES`. Chủ: lát riêng. P3. Mở.
- NO-609 — `storeys._decide` dò từ khoá quy ước (`FIRST FLOOR`, `MAIN FLOOR`/`MAIN LEVEL`, chữ trệt) trên mọi hình
  được giữ làm `PlanView`, kể cả hình loại khác mặt bằng có độ tin thấp: mặt cắt tên `SECTION A-A MAIN LEVEL` trong bộ
  có `SECOND FLOOR PLAN` và không có chữ trệt cũng bật `us_english` (merge-review PR 123 lượt 2). Có cốt thì
  `_level_ok` sửa lại; không có cốt thì tầng bị hỏi (độ tin dưới 0,7). Lỗ có sẵn với `FIRST FLOOR`. Sửa: chỉ xét hình
  `kind == "plan"`. Chủ: lát riêng. P3. Mở.
