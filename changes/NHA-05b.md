# NHA-05b — đọc chữ mái thành `RoofSpec`, luật nhóm M kiểm lúc đề xuất

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.C Câu 3 và Câu 6, §2.E, §3 nhóm M, §5 (lát NHA-05b). Người dùng duyệt GATE 1
ngày 2026-11-25.
**Nhánh xếp chồng**: tách từ `feature/nha-05a-building-proposal` (`e5ff9b1c`), nhánh ấy lại xếp trên NHA-04 và NHA-01;
chưa nhánh nào vào `main`. Khi các nhánh dưới được gộp kiểu squash thì nhánh này phải rebase.

- `packages/domain/drawing_set/roof_reading.py` (mới): `read_roof` đọc từ chữ của hình mặt bằng mái và mặt cắt ra độ
  dốc theo nguồn (`roofPlan`, `section`), vật liệu (ngói, tôn, shingle) và việc có chữ thoát nước hay không.
- `packages/domain/drawing_set/rules.py`: thêm chín luật M kiểm lúc đề xuất vào bảng `RULES` và mỗi luật một hàm kiểm
  thuần (`m02_eave` … `m19_ambiguous`). `RuleHit.values` nhận thêm số thực (độ dốc); số mm vẫn là số nguyên.
- `packages/domain/drawing_set/building.py`: kiểu `RoofSpec` (mỗi trường là `Measured`); `BuildingProposal.roofs`;
  nguồn mới `roofPlan`; `Conflict` có thêm trường `pitchDeg`; `propose_building` gọi `read_roof` rồi đề xuất mái.
- Test viết trước mã, bổ sung sau review: `read_roof`, mỗi luật M một ca đạt và một ca vi phạm, và đề xuất mái (bộ
  có mặt bằng mái ra `RoofSpec` có nguồn; bộ không có ra mặc định kèm câu hỏi; `i=2%` ra mái bằng; tum ra hai mái).
  Không dựng hình, không API, không DB.

## Cách suy

- **Tầng có mái**: tầng cao nhất. Tầng cao nhất là tum/áp mái thì hai mái bằng (trên nó, và tấm phủ tầng ngay dưới)
  kèm M-06. Khi đó chữ mà ghi mái dốc: áp mái nhận mái dốc ấy, không có tấm phủ; tum thì không đề xuất mái nào và
  M-06 mang thêm chữ `sloped`. Hình sân thượng nằm trên cùng không phải tầng được che: mái gắn vào tầng dưới nó, dạng
  `flat` suy từ nhãn hình (nguồn `plan`, độ tin 0,6); sân thượng giữa nhà không được coi là bằng chứng.
- **Độ dốc**: `parse_slope` của NHA-01. Dưới 5 % là mái bằng, trị số chỉ ghi lại. Hai nguồn lệch quá 3° thành mâu
  thuẫn `pitchDeg:<hình>` cho người duyệt chọn nguồn (M-03, thêm M-05 nếu một bên nói bằng, một bên nói dốc).
- **Dạng**: chữ không cho biết dạng. Có độ dốc từ 5 %, hoặc có chữ vật liệu, hoặc bộ feet-inch → `gable` theo quy
  ước (độ tin 0,4). Còn lại `flat` mặc định (0,3).
- **Câu hỏi**: dạng hoặc độ dốc là giả định thì M-18 khi bộ có hình mặt bằng mái, ngược lại T-07 với trường
  `roofForm` / `roofPitchDeg`.
- **Cốt diềm** = cốt tầng + chiều cao tầng, thừa kế nguồn và độ tin của chiều cao tầng; rỗng khi tầng chưa có cốt.
- **Độ đua** quy ước: mái bằng 0; bộ feet-inch 450 mm; còn lại 300 mm. **Hướng mái** luôn 0, độ tin 0,6.

## Lệch khỏi thiết kế

- **Không đọc cốt đỉnh mái, cốt parapet, cốt diềm có từ khoá.** Ghép từ khoá (`level_words`) với con số cần vị trí
  trên hình và chưa có bộ thật để đo. Hệ quả: `ridgeElevationMm`, `parapetMm` luôn rỗng; M-02 và M-17 có hàm kiểm và
  test nhưng `propose_building` không gọi, vì với giá trị máy đề xuất chúng không thể vi phạm (diềm luôn bằng đỉnh
  tường, độ dốc của mái dốc luôn từ 5 % và dưới 85°). Nơi gọi là NHA-06, khi người duyệt sửa số. Nợ NO-582.
- **Dạng mái không suy từ số nhóm mũi tên dốc**: mũi tên là hình vẽ, không có trong chữ OCR.
- **Mỗi nguồn một trị số dốc** (trị số rõ và dốc nhất), và mọi mái của bộ dùng chung dạng, độ dốc, vật liệu. Nhà có
  hai mái khác nhau bị gộp (ghi `shortcut:` trong mã; mái nhiều cấp là NHA-11).
- **Độ dốc trên mặt cắt đọc ở mọi chỗ của hình**, không riêng chỗ mái: `i=15%` của ram dốc hay `30°` của cầu thang
  cũng thành ứng viên độ dốc mái. Dạng `a:b` nhập nhằng trên mặt cắt thì bỏ hẳn (thường là tỉ lệ chi tiết). Nợ NO-583.
- **Ngưỡng 5 % so trên trị số đã làm tròn 0,1°**: độ dốc 4,96–4,99 % bị coi là mái dốc.
- **Chữ của hình sân thượng không được đọc** (chỉ hình loại `roof` và mặt cắt): sê nô ghi trên hình sân thượng không
  tắt được gợi ý M-07.
- **M-18 bật cho cả dạng theo quy ước**, không riêng mặc định: bộ có hình mái mà mái dốc thì luôn hỏi dạng, vì chữ
  không bao giờ cho biết dạng.
- **Mặt đứng chưa là nguồn riêng**: thiết kế M-03 kể cả mặt đứng, nhưng `propose_building` chỉ nhận `sections`.
- **M-09** chỉ có ngưỡng cho ngói (2½:12) và shingle (2:12); tôn không có ngưỡng xác minh được nên không kiểm.
- **Từ khoá vật liệu tiếng Việt** phải có "MÁI"/"LỢP" đứng trước (bỏ dấu thì "ngồi" trùng "ngói"): "NGÓI ĐẤT NUNG"
  đứng riêng bị bỏ sót.
- `Measured.value` và `RuleHit.values` giờ là `int | float | str`; `Conflict` của cốt vẫn bắt buộc số mm nguyên.
- Hai ca test của NHA-05a đổi cách so (`floor_codes`): bộ nào giờ cũng có mái đề xuất nên luôn có câu hỏi về mái.

## Phạm vi kiểm

- Ba tệp test của lát (2026-11-25, bản sau review): 151 ca đạt.
- `bash tools/verify/run.sh verify --fast` (2026-11-25, bản sau review): bước 1 ruff format, 2 ruff check, 3 mypy
  --strict, 4 lint-imports, 5 pytest bị ảnh hưởng (105 tệp test, 2810 đạt), 8 openapi — **đạt**, mã thoát 0. Tệp
  `changes` này sửa mục "Phạm vi kiểm" sau lượt đạt; mã và test không đổi.
- Cổng đầy đủ **không chạy ở máy**: để CI của GitHub chạy khi nhánh được push.
- Review: `ecc:python-reviewer`, không CRITICAL. HIGH đã sửa: áp mái có chữ mái dốc bị mất hẳn mái (nay nhận mái dốc;
  tum thì M-06 ghi rõ `sloped`). MEDIUM đã sửa: từ khoá `TILE ROOF`/`METAL ROOF` khớp cả `ROOFTOP`; dạng `a:b` trên
  mặt cắt (tỉ lệ chi tiết) bị lấy làm độ dốc; biên 3° của M-03 lệch vì số thực; sân thượng giữa nhà bị coi là bằng
  chứng mái bằng. LOW đã sửa: hai nguồn khớp nhau thì lấy trị số rõ thay vì trị số nhập nhằng. Còn lại ghi ở "Lệch
  khỏi thiết kế". Không chạy `security-reviewer`: hàm thuần, chưa có đầu vào người dùng nào tới được.

## Nợ và việc chưa làm

- NO-580 (từ NHA-05a) vẫn mở: `propose_building`, `rules.py`, nay thêm `roof_reading.py`, chưa có nơi dùng trong mã
  sản phẩm. Chủ: NHA-06.
- NO-582 — Chưa đọc cốt đỉnh mái / parapet / diềm theo từ khoá; M-02 và M-17 chưa được gọi ở đâu. Chủ: NHA-06 (gọi
  khi người duyệt sửa số) và lát đọc cốt theo vị trí (chưa có mã lát). P2. Mở.
- NO-583 — Mọi ngưỡng và từ khoá của lát này (5 %, 3°, độ tin, từ khoá vật liệu, "trị số dốc nhất") chưa đo trên bộ
  thật nào; chỉ có ca tổng hợp. Chủ: NHA-10 (đo). P2. Mở.
