# NHA-10a — đo mái trên đường sản phẩm bằng nhãn mái sẵn có

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.C Câu 8, §2.G, §5 (lát NHA-10). Người dùng duyệt GATE 1 ngày 2026-11-26
("Duyệt, bắt đầu"). Nhánh tách từ `main` (`b6b90b20`, đã có NHA-00…06a); không phụ thuộc PR nào đang mở.

**Tách lát:** NHA-10 của thiết kế gồm ba phần. Lát này (10a) chỉ làm phần đo được ngay bằng 30 bộ nhãn mái thô sẵn có.
Phần còn lại là NHA-10b: nhãn chi tiết (2 mái / 4 mái / 1 mái, độ dốc, cốt diềm, đường bao), lượt chạy trọn tầng 2 trên
stack thật và kịch bản e2e — chờ người gắn nhãn và chờ PR 121 cùng ba PR AppFront gộp xong.

- `tools/bench/muc3/roof.py` (mới): bộ chấm mái, chuyển từ `F:/runs/dwglab/muc3/score_roof.py` (84 dòng, ngoài repo),
  giữ nguyên luật chấm (loại trùng nhau; cốt chân khớp chân mái **hoặc** chân mái tum; dung sai 0,2 m). Thêm: khoảng
  Wilson 95 % của đúng/tổng, hai mốc "trước" trên cùng tập bộ (không có mái, luôn đoán mái bằng), `--split-file` để
  tách sạch / đã lộ, `--json`. CLI: `python -m tools.bench.muc3.roof --pred <dự đoán>`.
- `tools/bench/muc3/predict.py`: mỗi tệp dự đoán có thêm khoá `roof` = bản nháp mái từ `propose_building` của route
  đọc `building` (`building_read._propose`), với hình máy đề xuất coi như đã duyệt. `roof_draft` đổi `RoofSpec` sang
  loại thô của đáp án: `flat` → bằng, `shed`/`gable`/`hip` → dốc, lẫn cả hai → hỗn hợp; cốt chân là diềm cao nhất.
- `tools/bench/muc3/SCHEMA.md`, `README.md`: khoá `roof` và cách chạy.
- Test viết trước mã: `tools/bench/tests/test_muc3_roof.py` (mới, 9 hàm test), sáu ca mới ở `test_muc3_predict.py`.
- Không chỉnh luật nào của sản phẩm. Phần dự đoán ngoài khoá `roof` không đổi: 50 tệp của lượt này bỏ khoá `roof`
  trùng băm với lượt đo gần nhất (`pred-nha-03-r3`).

## Số đo đầu tiên của mái trên đường sản phẩm

Đệm NHA-00 (`F:/runs/dwglab/muc3-nha00/readings`, 50 tờ của 51 bộ), đáp án `truth_review/roof_review.json` (30 bộ
Việt Nam: bằng 10, dốc 7, hỗn hợp 12, không rõ 1; 12 bộ đánh dấu chắc). Ô ghi **đúng/trả lời/tổng**, kèm khoảng Wilson
95 % của đúng/tổng.

| Nhóm | Nguồn dự đoán | Loại mái | Cốt chân | Cốt đỉnh |
|---|---|---|---|---|
| dev, 10 bộ | đường sản phẩm | 2/8/10 [6–51 %] | 0/4/9 [0–30 %] | 0/0/10 [0–28 %] |
| dev, 10 bộ | mốc 1: không có mái | 0/0/10 | 0/0/9 | 0/0/10 |
| dev, 10 bộ | mốc 2: luôn mái bằng | 3/10/10 [11–60 %] | — | — |
| dev, 10 bộ | mốc 3: `pred_roof` nghiên cứu | 4/8/10 [17–69 %] | 7/8/9 [45–94 %] | 4/5/10 [17–69 %] |
| test, 20 bộ | đường sản phẩm | 7/18/19 [19–59 %] | 2/10/19 [3–31 %] | 0/0/19 [0–17 %] |
| test, 20 bộ | mốc 1: không có mái | 0/0/19 | 0/0/19 | 0/0/19 |
| test, 20 bộ | mốc 2: luôn mái bằng | 7/19/19 [19–59 %] | — | — |
| test, 20 bộ | mốc 3: `pred_roof` nghiên cứu | 8/12/19 [23–64 %] | 11/17/19 [36–77 %] | 4/5/19 [9–43 %] |

Nhóm "chắc" (12 bộ): dev 5 bộ — loại 1/4/5, cốt chân 0/3/5; test 7 bộ — loại 3/7/7, cốt chân 1/4/7.

Sai mà chắc: đường sản phẩm không có câu trả lời nào đạt độ chắc 0,7 (0/0 ở mọi ô; 0,7 là mốc "một nguồn" của quy
ước độ chắc ở `SCHEMA.md`, không phải ngưỡng hỏi của sản phẩm). Mốc 3 có: loại sai 2/4 (dev) và 2/9 (test) trong các
câu chắc >= 0,7.

Một bộ test (DS-262, nhóm sạch) có đáp án mái nhưng **không có dự đoán của đường sản phẩm**: đệm NHA-00 không có tờ
nào của bộ này (50 tờ của 51 bộ), nên nó được đếm là "không trả lời" ở cả ba trường. Bảng chữ in số bộ như vậy ở cột
"không đọc được dự đoán" (test: 1; dev: 0). Mốc 3 có đủ tệp cho cả 30 bộ.

**Kết luận, không viết "đạt":**

1. Đường sản phẩm **chưa thắng** mốc "luôn đoán mái bằng" về loại mái (dev 2/10 so với 3/10; test 7/19 so với 7/19),
   và thấp hơn nhiều so với bộ đoán nghiên cứu cũ về cốt chân (dev 0/9 so với 7/9; test 2/19 so với 11/19). Về loại
   mái các khoảng Wilson chồng nhau: mẫu quá nhỏ để nói chênh lệch có ý nghĩa. Mốc 3 không phải phép so ngang hàng:
   luật của bộ đoán nghiên cứu được viết trong lúc nhìn chính các bộ này (27/30 bộ đã lộ).
2. Cốt đỉnh không bao giờ có câu trả lời: `RoofSpec.ridge_elevation_mm` chưa nơi nào đọc (docstring của nó ghi vậy).
3. **Không kết luận được trên nhóm sạch**: 27/30 bộ có đáp án mái nằm trong danh sách đã lộ của K-32a (chỉnh luật
   tách hình). Nhóm sạch chỉ còn 3 bộ (dev 1, test 2). Số ở trên là trên mọi bộ có đáp án, phải đọc kèm ghi chú này.
   Thiết kế §6 câu 2 đã nêu hướng: 22 bộ `DN3-` chưa từng bị dùng, cần người duyệt nhãn (NHA-10b).

Chẩn đoán trên **tập dev** (10 bộ, không nhìn tập test):

- 8/8 câu trả lời về loại đều là `flat`, độ chắc 0,3–0,6: trên dev đường sản phẩm chưa lần nào ra mái dốc hay hỗn
  hợp. Đáp án dev: hỗn hợp 6, bằng 3, dốc 1. 2 bộ không có mái nào vì không có tầng nào được giải (DS-266, DS-292).
- Cốt chân: 9 bộ có đáp án thì 5 bộ không có câu trả lời (không có mái, hoặc tầng mái che không có cốt), 4 bộ trả lời
  sai. Trong 4 bộ
  đó: 2 bộ ra 3,3 m (mọi mặt bằng bị xếp chung tầng 1, diềm = 0 + chiều cao mặc định), 1 bộ lệch cốt (DS-261: 10,78
  so với 14,5), 1 bộ coi sàn chân mái +8,25 là một tầng rồi cộng thêm chiều cao tầng (DS-396: 11,25, trùng cốt
  **đỉnh** của đáp án).

## Lần nguyên nhân: ảnh tờ của thước, đo lại trên tờ dựng đúng phông (chỉ tập dev)

Người dùng duyệt hai bước này ngày 2026-11-27. Không sửa mã; script lần và kết quả thô ở ngoài repo
(`F:/runs/dwglab/muc3-nha00/roof_trace.py`, `roof_trace-dev.txt`, `measure-v3dev.txt`).

Trên 10 bộ dev, kết quả đọc trang của `sheets/` gần như không có chữ nào về mái: không dòng độ dốc nào dùng được, 2
dòng chữ rác nhắc vật liệu. Lý do nằm ở ảnh: `sheets/` ghép mọi hình của bộ vào một tờ từ bản dựng 150 dpi dùng phông
thay thế (chữ VNI / TCVN3 ra chữ rác: "MAET BAENG MAUI"), cạnh dài chặn 7000 px nên DS-292 bị co còn 12 %, DS-266 còn
31 % — đúng hai bộ không giải ra tầng nào. Tổng số dòng chữ đọc được: 2 177, so với 12 386 của chuỗi nghiên cứu (đọc
từng hình 300 dpi đã dựng lại đúng phông, `render_vn`). Mốc 3 vì vậy hơn đường sản phẩm trước hết ở **ảnh đầu vào**.

Đo lại bằng cùng mã, cùng 10 bộ, trên `sheets_v3` (trang khổ A3/A2, một hai hình mỗi trang, đúng phông; 112 trang,
đệm `readings-v3dev`, `BENCH_MUC3_ROOT` trỏ vào một gốc tạm có `sheets` = `sheets_v3`):

| Chỉ số (dev, 10 bộ) | `sheets/` (tờ ghép cũ) | `sheets_v3` |
|---|---|---|
| Loại mái | 2/8/10 | 3/10/10 (mốc luôn mái bằng: 3/10) |
| Cốt chân mái | 0/4/9 | 6/8/9 [35–88 %] |
| Cốt đỉnh mái | 0/0/10 | 0/0/10 |
| Loại mái sai trong câu chắc >= 0,7 | 0/0 | 2/3 |
| Chiều cao tầng (mặt bằng + mặt cắt) | 2/20 | 16/20 |
| Số tầng | 10/27 | 15/27 |
| Loại hình | 38/128 | 56/128 |
| Tách hình: tìm lại / chính xác | 89/128 · 89/102 | 68/128 · 68/117 |
| Loại hình sai trong câu chắc >= 0,9 | 0/1 | 3/9 (đều lẫn mặt cắt ↔ mặt đứng) |

Đọc bảng này thế nào:

- Cốt chân mái và chiều cao tầng sai trên tờ cũ **chủ yếu do ảnh**, không do luật: cùng mã, đổi ảnh thì 0/9 → 6/9 và
  2/20 → 16/20. Hai bộ còn sai cốt chân: DS-396 (sàn chân mái +8,25 bị coi là một tầng, ra 11,25) và DS-267 (15,6 so
  với 15,3).
- Loại mái **không khá lên** dù đã có chữ: vẫn ngang mốc "luôn mái bằng". Trên `sheets_v3` có 3–30 dòng nói về mái
  mỗi bộ ("MÁI LỢP TOLE", "LỢP NGÓI", "SÀN MÁI", "XÀ GỒ"), nhưng từ khoá vật liệu của `roof_reading` khớp **0 dòng**
  ở cả 10 bộ; 6/10 đáp án là hỗn hợp, loại mà mã không thể ra. Đây mới là phần của luật (NO-612).
- Hai câu "sai mà chắc" (DS-254, DS-261: `flat` 0,8) là bộ hỗn hợp có hình mặt bằng mái ghi độ dốc 2 %: máy đọc đúng
  phần bằng, sai vì một bộ chỉ có một dạng mái. Trên `sheets_v3` 9/10 bộ có hình mặt bằng mái được xếp loại chắc
  (tờ cũ: 3/10), nên khâu xếp loại hình mái không phải chỗ nghẽn.
- Tách hình tụt trên `sheets_v3` (tìm lại 69,5 % → 53,1 %, chính xác 87,3 % → 58,1 %): luật tách hình được chỉnh trên
  tờ ghép (K-32a), chưa từng đo trên trang kiểu này (NO-617).
- `sheets_v3` cũng không phải PDF thật: mỗi trang một hai hình, sạch. 9/10 bộ dev đã lộ. Không dùng bảng này để
  chốt ngưỡng; nó chỉ trả lời câu "sai do ảnh hay do luật".

## Giới hạn của phép đo

- Loại thô không tách 2 mái / 4 mái / 1 mái; không có đáp án độ dốc, cốt diềm, đường bao: bốn chỉ số còn lại của
  Câu 8 chưa đo được.
- Sai số của người gắn nhãn chưa biết (chưa có hai người gắn nhãn độc lập).
- Dữ liệu là tờ CAD đổi ra ảnh giả quét, không phải PDF người dùng thật tải lên (giới hạn 1 của §2.G); thước tự nhận
  mọi đề xuất của máy, trong sản phẩm người duyệt sửa loại hình trước khi có đề xuất mái.
- Mái chưa dựng hình ở tầng này: đây là "sai mà không kèm câu hỏi" của tầng 1, chưa phải lỗi im lặng trên mô hình.

## Phạm vi kiểm

- Phạm vi kiểm: fast. Lát chỉ chạm `tools/bench/muc3/**` và test của nó; không chạm mã sản phẩm, hợp đồng, migration.
- Cổng nhanh (`bash tools/verify/run.sh verify --fast`): lần 1 hỏng riêng bước `mypy --strict` (một dòng thiếu chú
  thích kiểu trong test mới); lần 2 sau khi sửa: **đạt** cả 5 bước — 898 test đạt, độ phủ tập file bị chạm dòng
  99,07 % · nhánh 96,15 %. Cổng đầy đủ để CI chạy.
- Review (python-reviewer): không có CRITICAL / HIGH. Hai MEDIUM đã xử lý: bộ chấm đếm số bộ không đọc được dự đoán
  (`missing`); ghi chú nêu rõ mốc 3 không ngang hàng. LOW để nguyên, có chủ ý: cốt không phải số tính là "không trả
  lời"; `predict.main` không bắt lỗi của `_propose` (lỗi thì lượt đo dừng, không ghi tệp dở); "cốt chân = diềm cao
  nhất" lệch một phía (ghi ở NO-613).
- Commit thứ hai của lát chỉ sửa tệp ghi chú này (mục lần nguyên nhân và các khoản nợ): không chạy lại cổng.
- Gộp `main` sau khi PR 121 và PR 123 vào (2026-11-27): `_propose` nay nhận `BuildingInputs` và `BuildingProposal` có
  thêm `levels`, nên `predict._proposal` và hàm dựng dữ liệu của test đổi theo (thước truyền "không có quyết định của
  người duyệt"). Đo lại trên cả hai bộ ảnh: mọi tệp dự đoán trùng với lượt trước, các bảng số ở trên không đổi.

## Nợ và việc chưa làm

- NO-569 (`changes/NHA-00.md`) **đóng**: `score_roof.py` đã chuyển vào `tools/bench/muc3/roof.py`.
- NO-612 — Đề xuất mái của đường sản phẩm chưa thắng mốc "luôn đoán mái bằng" và trên dev luôn trả `flat`. Một phần
  là do cấu trúc, đã lần: `building._roofs` cho mọi `RoofSpec` của một bộ cùng một `form` (`building.py:360`), nên
  đường sản phẩm **không thể** ra loại `mixed` — 12/30 bộ có đáp án (dev 6/10) là hỗn hợp. Phần thứ hai, đã lần trên
  `sheets_v3`: từ khoá vật liệu của `roof_reading` khớp 0 dòng ở 10 bộ dev dù mỗi bộ có 3–30 dòng nói về mái — thiếu
  "TOLE", "XÀ GỒ", "VÌ KÈO", thiếu dấu hiệu mái bằng ("SÀN MÁI"), và không chịu được lỗi OCR ("MAL LQP NGOI"). Giả
  thuyết ghi trước đây (chữ mái không tới `read_roof` vì hình không được xếp loại chắc) **không phải nguyên nhân
  chính**. Chủ: lát riêng (từ khoá trước, mái nhiều dạng là NHA-11); chỉ chỉnh trên dev. P2. Mở.
- NO-613 — Cốt chân mái sai trên tờ ghép cũ (0/9) chủ yếu do ảnh: trên `sheets_v3` cùng mã ra 6/9. Còn lại thuộc
  luật: DS-396 coi sàn chân mái là một tầng rồi cộng thêm chiều cao tầng; DS-267 lệch 0,3 m. Ghi chú về thước: khi bộ
  có hai mái, `roof_draft` lấy diềm cao nhất (mái tum); đáp án không có cốt tum thì diềm thấp hơn mới là câu trả lời
  đúng — chưa đo trường hợp này. Chủ: lát của khâu tầng. P3. Mở.
- NO-614 — Cốt đỉnh mái không có nguồn máy nào (`ridge_elevation_mm` chỉ người duyệt nhập). Thước đếm 0 câu trả lời.
  Cần đọc cốt đỉnh từ mặt cắt / mặt đứng, hoặc tính từ độ dốc và đường bao sau khi dựng hình (tầng 2 của §2.G). Chủ:
  NHA-10b hoặc lát riêng. P3. Mở.
- NO-615 — Nhóm sạch có đáp án mái chỉ còn 3 bộ; cần duyệt nhãn 22 bộ `DN3-` (thiết kế §6 câu 2) trước khi chốt
  ngưỡng nào cho mái. Chủ: chủ dự án (người gắn nhãn). P2. Mở.
- NO-616 — Ảnh tờ `sheets/` của thước Mức 3 (tờ ghép 150 dpi, phông thay thế, chặn 7000 px) làm mất phần lớn chữ;
  mọi số NHA-00…NHA-10a về tầng, cốt, chiều cao và mái đo trên nó đều thấp hơn thực vì ảnh, không tách được khỏi lỗi
  của luật. `sheets_v3` (đúng phông, kiểu trang) mới có 21/51 bộ: thiếu 9 bộ `DS-` của test và mọi bộ quốc tế. Cần
  dựng đủ rồi đổi mốc của thước; `readings.py` chưa có `--sheets` (lượt này dùng gốc tạm). Chủ: lát thước. P2. Mở.
- NO-617 — Trên trang kiểu `sheets_v3`, tách hình tìm lại 68/128 và chính xác 68/117 (tờ ghép: 89/128 và 89/102), và
  3/9 loại hình chắc >= 0,9 bị sai (lẫn mặt cắt ↔ mặt đứng). Chưa lần nguyên nhân. Chủ: lát tách hình. P2. Mở.
