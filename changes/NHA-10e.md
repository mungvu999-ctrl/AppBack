# NHA-10e — chạy chuỗi bộ hồ sơ → mái bằng trình duyệt trên BE thật, ML thật (NO-619)

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §5 (lát NHA-10). Lát này chỉ ghi kết quả và nợ; không đổi mã, hợp đồng hay
migration của AppBack. Bài kiểm nằm ở AppFront: `e2e/fullstack/houseset.fullstack.ts` (nhánh
`test/nha-10e-fullstack-house-set`).

## Đã chạy gì

- Stack compose riêng `nha10` (ngoài repo, `F:/runs/dwglab/nha10-stack/`), ảnh dựng từ `main` `5c7f7bd5` và AppFront
  `541fe311`, `DRAWING_SET_ENABLED=true`, `ML_BACKEND=onnx`, DB mới ở head `r20261125_nha_06b`. Không dùng lại stack
  f14: DB của nó đang ở hai revision của nhánh chưa gộp nên ảnh của `main` không nâng cấp được.
- Model lấy từ registry của f14, nạp qua N26 và kích hoạt qua N24: tường `seg-b2-a124-stride2` (iou 0,991), cửa và
  đồ đạc `yolo11m-v4-ft-B` (map50 0,197). ML chạy trên GPU (dịch vụ `ml-gpu`, ảnh `ML_VARIANT=cuda`): một lượt pipeline
  khoảng 90 giây, so với khoảng 276 giây trên CPU.
- Bảy bộ hồ sơ thật của `F:/runs/dwglab/muc3/sheets_v3` (DS-261, DS-466, DS-254, DS-267, DS-571, DS-264, DS-396),
  mỗi bộ một lượt qua giao diện thật: tải cả bộ → máy đọc → mỗi mặt bằng một tầng → chọn bên cho mâu thuẫn → xem hết
  cảnh báo → xác nhận → pipeline từng tầng → tác vụ ráp.

## Kết quả

Cả bảy bộ đi hết tới bước ráp. **Không bộ nào có mái** (0/8 tầng mang mái), nên bước "Duyệt mái" trên trang 3D chưa
chạy được trên BE thật. Lý do từ chối, tính lại bằng chính `roof_outline`/`roof_rects` trên lớp đã lưu:

| Lý do | Tầng | Bộ |
|---|---|---|
| `roof_outline` trả `split`: các phòng có mái che không liền thành một khối | 4 | DS-466, DS-254, DS-571, DS-264 |
| Đường bao phủ được bằng 2–3 chữ nhật, nhưng dạng `flat` chỉ nhận một chữ nhật | 2 | DS-267 (hai mái) |
| Pipeline của tầng hỏng `NOT_A_FLOOR_PLAN` → không có lớp → `NO_WALLS` | 2 | DS-261, DS-396 |

Cả tám `RoofSpec` đều là `flat` với độ tin 0,6 (NHA-10c): máy chưa đọc được dạng mái nào từ bản vẽ. DS-571 và DS-264
còn mang `OPEN_ENVELOPE` nên bị M-15 từ chối trước khi tính hình.

## Nợ và việc chưa làm

- NO-624 — Mái `flat`/một dốc trên đường bao nhiều chữ nhật (nhà chữ L) bị `build_roof` từ chối
  (`apps/api/drawing_sets/assembly.py`, nhánh `_SINGLE_FACE`). Trên dữ liệu thật đây là 2/8 tầng, và là chỗ gần có
  mái nhất. P2. Mở.
- NO-625 — Phòng máy nhận ra không liền nhau (`split`) thì không có mái: 4/8 tầng. Cần quyết lấy đường bao từ tường
  bao thay cho hợp các phòng, hay nới ngưỡng nối. P2. Mở.
- NO-626 — Tầng mang mái hỏng `NOT_A_FLOOR_PLAN` (mặt bằng mái, tầng thượng ít tường): 2/8 tầng không có lớp để ráp.
  P2. Mở.
- NO-627 — Mã kết quả ráp (`ROOF_SHAPE_COMPLEX`, `NO_WALLS`) chỉ nằm trong `drawing_sets.building`; không route nào
  trả nó và AppFront không có câu nào cho nó. Người dùng xác nhận xong, không thấy mái và không được báo vì sao. P2. Mở.
- NO-628 — Hình mặt cắt được máy gán tầng đích và chạy pipeline như một tầng (DS-466 một hình, DS-396 năm hình
  "MAT cat …"). P2. Mở.
- NO-629 — Trên DB mới, bản gốc `dimensionReading` chấm ra cer 0,53 và `openingAndFurnitureDetection` map50 0,0, khác
  số của stack f14. Chưa tìm nguyên nhân. P3. Mở.

## Kiểm chứng và giới hạn

- Nửa 3D của bài kiểm (chọn mái, "Duyệt mái", tải lại) chưa từng chạy trên BE thật; nó chỉ được kiểm bằng máy chủ giả
  ở `e2e/nha10/house-roof.spec.ts` của AppFront. NO-619 vì thế còn mở cho phần này, chờ NO-624.
- Số liệu là một lượt mỗi bộ; không đo lặp. DS-261, DS-466, DS-254 chạy pipeline trên CPU, bốn bộ còn lại trên GPU.
- Không có test hay mã sản phẩm nào đổi ở AppBack nên không chạy cổng verify cho lát này.
