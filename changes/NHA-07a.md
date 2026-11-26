# NHA-07a — tác vụ ráp nhà `drawing_set.assemble`

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.E, §3 (luật nhóm M kiểm lúc ráp), §5 (lát NHA-07a). Người dùng duyệt GATE 1
ngày 2026-11-26 ("Duyệt, bắt đầu"), kèm năm chỗ lệch thiết kế ở dưới.
**Nhánh xếp chồng**: tách từ `feature/nha-02-roof-nodes` (`ad28f305`, đã gộp đầu chuỗi `nha-07b`), gộp thêm
`chore/perf-13-fast-gate-stacked-base` (`9a6ac584`) để cổng nhanh so với lát trước thay vì `main`.

- `packages/domain/drawing_set/assembly.py` (mới, thuần): `FloorAssembly` (kết quả ráp một tầng), `roof_id` (id tất
  định `RF-` + 16 base36 theo tầng), `fingerprint`, `eave_above_floor_mm`, và `settle` — luật M-11 (không ghi đè mái
  người vẽ / đã duyệt / đã sửa / đã xoá) và M-16 (tường đổi: mái AI chưa sửa dựng lại, mái đã sửa chỉ ghi `ROOF_STALE`).
- `apps/api/drawing_sets/assembly.py` (mới): `assemble_sets` — một câu SQL chọn tầng tới lượt (bộ `confirmed`, bản
  đóng băng có mái cho hình, lượt chạy mới nhất đã xong, bộ ba dấu vân khác bộ ba đã lưu), rồi mỗi tầng một giao dịch:
  khoá bộ → tầng → tài liệu tầng, `build_roof` từ phòng hiện tại (`roof_outline`/`roof_rects`/`roof_nodes` của
  NHA-02), `settle` dưới khoá, ghi qua `write_layer(merge=…)` với `SYSTEM_PIPELINE`, lưu kết quả vào JSONB
  `drawing_sets.building.assembly[<levelRef>]`. Mái ghi ra `source="ai"`, `reviewed=False`.
- `apps/api/drawing_sets/jobs.py`: lịch `default.drawing_set.assemble`, 60 giây một lượt.
- `apps/api/drawing_sets/building_read.py`: `_frozen` gọi `assembly.frozen_proposal` (một chỗ đọc bản đóng băng).
- `packages/domain/drawing_set/rules.py`: luật M-04 (`m04_ridge`, ± 300 mm), kiểm lúc ráp, vi phạm lưu ở `hits`.
- `packages/vision/roofs/rects.py`: `roof_rects` từ chối `poor_fit` khi các chữ nhật không liền một khối (M-08).
- Không migration, không route mới, không đổi `openapi.json`; không sửa `merge.py`, `runs.py`, `writer.py`.
- Test viết trước mã: `packages/domain/drawing_set/tests/test_assembly.py`, `apps/api/drawing_sets/tests/
  test_set_assembly.py` (`test_assemble_drawing_sets__J01`/`__J06`, M-11 bốn ca, M-16, một tầng hỏng không chặn tầng
  khác, trần lô, hai lượt quét chồng nhau, ghi đồng thời với người dùng, người xoá mái rồi pipeline chạy lại trước lượt
  quét — ca thiết kế §"việc chưa làm" yêu cầu), cùng ca M-04 và M-08 ở test sẵn có.

## Lệch thiết kế (đã duyệt ở GATE 1)

1. Kết quả ráp chỉ nằm trong DB (`building.assembly`), chưa ra dây: `SetBuildingSchema` của FE là `.strict()` (NO-595).
2. Chu kỳ quét 60 giây, không phải 30: sàn của `periodic` (`MIN_PERIOD_S`).
3. Ráp theo từng tầng bằng lượt chạy mới nhất của tầng, không chờ mọi tầng của bộ xong.
4. M-13 không có mã riêng (tầng không có mái đọc được từ `assembly`); M-08 trả `ROOF_SHAPE_COMPLEX` thay vì lỗi nội bộ.
5. Bộ đã xác nhận mà không có bản đóng băng (`building.confirmed`) thì không có mái.

Phát hiện lúc viết và lúc review (chưa qua GATE 1, báo ở GATE 2):

6. M-04 lưu dạng `RuleHit` mã `M-04` trong `hits` (thiết kế gọi tên `ROOF_RIDGE_MISMATCH`), cùng khuôn với mọi luật
   khác. Cốt đỉnh/diềm không đọc nổi thành số thì bỏ phép so, mái vẫn dựng.
7. M-15 "lượt chạy của tầng hỏng": lượt hỏng không tự nó là lý do từ chối. Nó không ghi lớp, nên tầng chưa có tường
   vẫn ra `NO_WALLS`, còn tầng đang mang lớp tốt của lượt trước giữ mái. Theo đúng chữ thiết kế thì một lượt chạy lại
   bị huỷ hay quá hạn sẽ gỡ mái AI đang khớp tường.
8. Hai bộ `confirmed` cùng mang mái cho một tầng: chỉ bộ tạo sau cùng được ráp tầng đó (`_newer_set_on_floor`). Không
   có điều kiện này hai bộ mang `RoofSpec` khác nhau thay mái của nhau mỗi phút. Bộ mới hơn không mang mái cho tầng
   thì không che bộ cũ (sửa ở vòng merge-review, xem dưới).
9. Cờ "người đã xoá mái" (`userRemoved`) lưu theo **bộ**: bộ mới được xác nhận trên cùng tầng là một khởi đầu mới và
   sẽ dựng lại mái. Muốn cờ dính qua các bộ thì phải lưu nó theo tầng (chưa làm).

## Thứ tự triển khai

Mái do lát này dựng là `source="ai"`, chưa duyệt, nên `readiness` (NHA-07b) báo `roofUnreviewed` và tầng không còn "Sẵn
sàng". **Lát này không được tới người dùng trước khi** phần FE của NHA-07b (AppFront, nhánh
`feature/nha-07b-roof-unreviewed`) và NHA-09 (nút "Duyệt mái" trong 3D) đã ra: thiếu 07b thì FE cũ không giải được tài
liệu tầng, thiếu NHA-09 thì người dùng không có cách đưa tầng về "Sẵn sàng". Nợ mở liên quan: NO-584, NO-591, NO-593,
NO-594.

**Thứ tự gộp (merge-review PR 121, finding 6):** AppFront PR 113 (NO-578, NO-579), PR 114 (NHA-06c, 08a-08c; NO-592)
và PR 115 (NHA-07b, 09, 09b) vào `master` **trước** PR 121. `master` hiện chưa có `roofUnreviewed` trong enum chặt:
FE cũ gặp tầng có mái AI chưa duyệt sẽ không giải được cả tài liệu tầng. Sau đó người điều phối cập nhật
`tools/contract/APPFRONT_SHA` (NO-594).

## Phạm vi kiểm

- Phạm vi kiểm: fast + CI.
- `bash tools/verify/run.sh verify --fast` (2026-11-26, trên cây cuối cùng): cổng so với lát trước `ad28f305` (luật
  PERF-13), 151 tệp test bị ảnh hưởng — 3 999 đạt trong 2 phút 58 giây; bước 1 ruff format, 2 ruff check, 3 mypy
  --strict, 4 lint-imports, 5 pytest + `coverage_gate`, 8 openapi (không đổi) — **đạt**, mã thoát 0. Bước 6, 7 không
  chạy vì đầu vào của chúng không đổi (không migration, không đổi hợp đồng).
- Review: `ecc:fastapi-reviewer` (đọc tĩnh, không chạy gì), 0 CRITICAL, 0 HIGH, 4 MEDIUM, 5 LOW. Đã sửa kèm test: lượt
  chạy hỏng gỡ mái đang khớp tường (mục 7), cốt đỉnh không phải số chặn dựng mái (mục 6), bản đóng băng sai phiên bản
  bị khoá lại mỗi phút, bộ mới che bộ cũ bằng hình không phải mặt bằng tầng. Giữ nguyên có ghi chú: mục 9, và hai điểm
  về tầng lỗi lặp lại ở "Nợ và việc chưa làm". `ecc:security-reviewer` không chạy: lát không chạm xác thực, quyền hay
  dữ liệu người dùng nhập.
- Cổng đầy đủ **không chạy ở máy**: để CI của GitHub chạy khi nhánh được push.

## Vòng sửa sau merge-review PR 121

Phán quyết `docs/reviews/2026-11-26-feature-nha-07a-set-assembly.md` (REQUEST CHANGES: 1 P1, 5 P2, 8 P3). Người dùng
duyệt kế hoạch sửa ngày 2026-11-26. Test viết trước mã, mỗi điểm một ca đỏ rồi xanh.

- **Finding 1 (P1), sửa** — `packages/domain/drawing_set/assembly.py::eave_above_floor_mm`: diềm mang nguồn `reviewer`
  mà trùng đỉnh tường đã duyệt (cốt + chiều cao đang hiệu lực) là diềm suy ra từ chiều cao người duyệt nhập
  (`building._wall_top` cho nó thừa kế nguồn), nên vẫn đi theo tường hiện tại. Trước đó người duyệt chỉ nhập chiều cao
  tầng là mái bị ghim ở số ấy: tầng có sẵn cao khác thì mái lệch đỉnh tường, sửa chiều cao tầng sau đó mái không theo.
  Hệ quả đã chọn: người duyệt nhập cốt diềm **đúng bằng** đỉnh tường đã duyệt cũng được coi là "ở đỉnh tường" và đi
  theo tường. Test: hai ca mới ở `test_assembly.py::test_eave_follows_the_current_wall_top_unless_the_reviewer_typed_it`.
- **Finding 2 (P2), sửa** — `apps/api/drawing_sets/assembly.py::_newer_set_on_floor`: bộ mới hơn chỉ che bộ cũ khi bản
  đóng băng của nó có mái cho hình trên tầng đó (`_frozen_roof`, cùng phép chứa JSONB với `_due`). Sửa mục 8 ở "Lệch
  thiết kế". Test: `test_assemble_a_newer_set_without_a_roof_does_not_take_the_floor`. Phần còn lại: NO-602.
- **Finding 3 (P2), sửa** — phòng tên `SÂN THƯỢNG`, `BAN CÔNG`, `TERRACE`, `BALCONY`, `PATIO` (thiết kế §2.C Câu 5
  bước 1) không vào đường bao mái: `packages/domain/drawing_set/assembly.py::open_air_room`, gọi ở `build_roof`. "Sân"
  đứng một mình không loại (thiết kế chỉ nêu năm tên; sân trong có mái). Chỉ khớp danh từ chính của tên (tiếng Việt
  đứng đầu, tiếng Anh đứng cuối): "Phòng ngủ có ban công" vẫn là phòng trong nhà; tên ghép kiểu "WC + BALCONY" vẫn bị
  loại (giới hạn đã biết). Test:
  `test_open_air_room_reads_the_folded_name`, `test_build_roof_leaves_terraces_and_balconies_unroofed`.
- **Finding 4 (P2), sửa** — `packages/vision/roofs/rects.py::roof_rects`: đường bao quá 500 đỉnh trả `complex` trước
  mọi phép tính. Test: `test_outline_with_too_many_vertices_is_too_complex`.
- **Finding 5 (P2), sổ nợ** — cấp mã NO-598…NO-608 dưới đây; NO-577 đóng, NO-578/579 (`changes/NHA-02.md`) và NO-592
  (`changes/NHA-06b.md`) ghi số PR AppFront.
- **Finding 6 (P2), thứ tự gộp** — ghi ở "Thứ tự triển khai".
- **Finding 7–15 (P3, Nit)** — không sửa mã, ghi nợ NO-599, NO-601, NO-603…NO-608.
- Kiểm vòng này (2026-11-26, cây làm việc trên `cab2a794`): `verify --fast` so với lát trước `ad28f305`, 151 tệp test,
  4 021 đạt; bước 1, 2, 3, 4, 5, 8 — **đạt**, mã thoát 0; bước 5b, 6, 7 — **chưa chạy**. Review `ecc:python-reviewer`:
  không CRITICAL/HIGH; MEDIUM "tên phòng chỉ nhắc tới ban công cũng bị loại" đã sửa kèm test; LOW chưa sửa: trần 500
  đỉnh đứng sau `roof_outline` (phần shapely phía trước chưa có trần). CI phải chạy lại trên SHA mới trước khi gộp.

## Nợ và việc chưa làm

- NO-595 — Kết quả ráp (`building.assembly`: mã từng tầng, `ROOF_STALE`, `USER_REMOVED`, vi phạm M-04) chưa ra dây nên
  màn duyệt và 3D chưa hiện được "chưa có mái" / "mái lệch tường". Cần thêm trường vào `SetBuildingOut` cùng lúc FE nới
  `SetBuildingSchema` (đổi hợp đồng, cần GATE riêng). Chủ: lát NHA-08/NHA-09. P2. Mở.
- NO-598 — Mỗi chu kỳ (60 giây) quét mọi bộ đã xác nhận bằng một truy vấn đọc JSONB `building` của từng bộ; chưa có
  chỉ mục, chưa chạy `EXPLAIN` trên dữ liệu thật. Hạn câu lệnh của DB là 10 giây: vượt là cả tác vụ hỏng mỗi phút
  (shortcut đã ghi ở docstring module). Chủ: lát riêng trước khi số bộ lên hàng nghìn. P2. Mở.
- NO-599 — Lượt quét không tự thoát khỏi tầng hỏng (finding 7): tầng lỗi lặp lại (tài liệu hỏng, bản đóng băng hỏng
  `model_validate`) được thử lại mỗi chu kỳ và chiếm một chỗ của lô 200; lỗi ngoài `AppError`/`ValueError`/
  `DocumentCorruptError` (`GEOSException`, `TypeError`, hết `lock_timeout`) dừng cả lượt ở cùng một tầng mỗi chu kỳ.
  Cần lưu dấu "đã thử" kèm bộ ba và bắt rộng theo từng tầng. Chủ: lát riêng. P3. Mở.
- NO-600 — `FloorAssembly` cấm khoá lạ nên thêm trường về sau phải kèm cách đọc bản cũ; tầng có mái AI đã duyệt mà bộ
  chưa có kết quả ráp trước đó ra `ROOF_STALE` dù hình khớp (chỉ là nhãn, không ghi gì). Chủ: cùng NO-595. P3. Mở.
- NO-601 — Cờ "người đã xoá mái" (`userRemoved`) lưu theo bộ (mục 9), và suy từ việc mái vắng mặt (finding 11): khôi
  phục phiên bản (N19) về bản chụp trước khi có mái làm mái vắng → `USER_REMOVED` vĩnh viễn cho bộ + tầng đó. Chủ:
  lát riêng (lưu cờ theo tầng, xoá cờ khi khôi phục phiên bản). P3. Mở.
- NO-602 — Phần còn lại của finding 2: bộ mới hơn mà người duyệt chọn "không dựng mái", hoặc tầng không còn là tầng
  trên cùng ở bộ đó, không gỡ mái AI chưa sửa của bộ cũ trên tầng ấy (mái vẫn được dựng lại theo tường). Chủ: lát
  riêng. P3. Mở.
- NO-603 — Quyết định mái (không phải `skip`) cho hình không được đề xuất mái bị bỏ qua im lặng: không chặn, số người
  duyệt nhập mất (finding 8; `building.py:406`, `decisions.py:117-120`). Chủ: NHA-06b. P3. Mở.
- NO-604 — Cốt diềm người duyệt nhập (finding 9): tầng không có cốt thì bị thay im lặng bằng chiều cao tầng; thấp hơn
  cốt tầng thì mái nằm dưới sàn (M-02 chỉ là cảnh báo). Chủ: NHA-06b / `roof_nodes`. P3. Mở.
- NO-605 — Khoá cảnh báo M-19 ghép chữ OCR thô; chữ có ký tự điều khiển thì client không gửi lại được trong `seen`
  (mẫu `PRINTABLE` từ chối 422), N60 kẹt ở `building.warnings` trừ khi nhập độ dốc (finding 10, chưa lần OCR có trả ký
  tự ấy không). Chủ: NHA-06b. P3. Mở.
- NO-606 — `level_reviewed` tính cả mái: mái AI chưa duyệt trên tầng đã duyệt hết làm các lý do mức lượt chạy
  (`criticalIssues`, `geometrySuspect`, `fallback*`) hiện lại, không chỉ thêm `roofUnreviewed` (finding 12). Cần quyết
  định chủ ý rồi ghim bằng test. Chủ: NHA-07b. P3. Mở.
- NO-607 — Bộ đã xác nhận: `stored_decisions` đọc nghiêm, nên mô hình `Decisions` đổi về sau thì `GET …/building` của
  bộ đã xác nhận trả 500 (finding 13; `building_read.py:155-162`). Chủ: NHA-06a/06b. P3. Mở.
- NO-608 — Thiếu test mức route cho 422 `building.conflicts` và chuỗi chọn nguồn → xác nhận; bậc 100–300 mm ở góc
  trong cho thêm một mặt mái thừa (finding 14); trùng định nghĩa `RoofForm`, 85°, `_GUESSED_SOURCES`, docstring cũ,
  `caplog` trỏ sai tên logger (finding 15). Chủ: vòng dọn sau. P3. Mở.
- NO-610 — Trần 500 đỉnh của `roof_rects` đứng sau `roof_outline`: bước dựng đường bao (`make_valid`, `union_all`, hai
  lượt `buffer` của shapely) vẫn chạy trên đường bao phòng không có trần số điểm, trong lúc tác vụ ráp giữ khoá
  (merge-review PR 121 lượt 2; `packages/vision/roofs/rects.py:34-54`). Phần này là mã C của shapely nên nhẹ hơn phép
  tìm trục chính nhiều, chưa đo. Sửa: trần số đỉnh từng phòng ở `build_roof` hoặc đầu `roof_outline`. Chủ: lát riêng.
  P3. Mở.
- NO-611 — `open_air_room` bỏ cả phòng mang tên ghép kết thúc bằng từ khoá tiếng Anh (`WC + BALCONY`,
  `Bedroom / Terrace`) khỏi đường bao mái, dù một phần của nó ở trong nhà (merge-review PR 121 lượt 2;
  `packages/domain/drawing_set/assembly.py:34`). Mái co lại hoặc ra `ROOF_SHAPE_COMPLEX`, người dùng thấy và vẽ tay
  được. Sửa đúng cần tách phòng ghép, không chỉ đổi mẫu tên. Chủ: lát riêng. P3. Mở.
