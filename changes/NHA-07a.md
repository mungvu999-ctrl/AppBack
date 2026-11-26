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
8. Hai bộ `confirmed` cùng nhắm một tầng: chỉ bộ tạo sau cùng được ráp tầng đó (`_newer_set_on_floor`). Không có điều
   kiện này hai bộ mang `RoofSpec` khác nhau thay mái của nhau mỗi phút.
9. Cờ "người đã xoá mái" (`userRemoved`) lưu theo **bộ**: bộ mới được xác nhận trên cùng tầng là một khởi đầu mới và
   sẽ dựng lại mái. Muốn cờ dính qua các bộ thì phải lưu nó theo tầng (chưa làm).

## Thứ tự triển khai

Mái do lát này dựng là `source="ai"`, chưa duyệt, nên `readiness` (NHA-07b) báo `roofUnreviewed` và tầng không còn "Sẵn
sàng". **Lát này không được tới người dùng trước khi** phần FE của NHA-07b (AppFront, nhánh
`feature/nha-07b-roof-unreviewed`) và NHA-09 (nút "Duyệt mái" trong 3D) đã ra: thiếu 07b thì FE cũ không giải được tài
liệu tầng, thiếu NHA-09 thì người dùng không có cách đưa tầng về "Sẵn sàng". Nợ mở liên quan: NO-584, NO-591, NO-593,
NO-594.

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

## Nợ và việc chưa làm

- NO-595 — Kết quả ráp (`building.assembly`: mã từng tầng, `ROOF_STALE`, `USER_REMOVED`, vi phạm M-04) chưa ra dây nên
  màn duyệt và 3D chưa hiện được "chưa có mái" / "mái lệch tường". Cần thêm trường vào `SetBuildingOut` cùng lúc FE nới
  `SetBuildingSchema` (đổi hợp đồng, cần GATE riêng). Chủ: lát NHA-08/NHA-09. P2. Mở.
- shortcut đã ghi trong mã: mỗi chu kỳ quét mọi bộ đã xác nhận bằng một truy vấn (đọc JSONB `building` của từng bộ,
  chưa chạy `EXPLAIN`); tầng lỗi lặp lại (tài liệu hỏng, kết quả lưu không đọc được) được thử lại mỗi chu kỳ và chiếm
  một chỗ của lô 200 — từ 200 tầng lỗi trở lên thì các tầng sau không tới lượt. Lỗi hạ tầng (DB) dừng cả lượt quét
  theo R-16; cùng một tầng gây lỗi DB lặp lại sẽ chặn các tầng xếp sau nó.
- Review ghi nhận, chưa sửa (LOW): `FloorAssembly` cấm khoá lạ nên thêm trường về sau phải kèm cách đọc bản cũ; tầng có
  mái AI đã duyệt mà bộ chưa có kết quả ráp trước đó ra `ROOF_STALE` dù hình khớp (chỉ là nhãn, không ghi gì) — xử lý
  cùng NO-595 khi mã ra dây.
