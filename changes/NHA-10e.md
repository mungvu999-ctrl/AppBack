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
  qua giao diện thật (DS-261 và DS-466 hai lượt, cùng kết quả; năm bộ kia một lượt): tải cả bộ → máy đọc → mỗi mặt
  bằng một tầng → chọn bên cho mâu thuẫn → xem hết cảnh báo → xác nhận → pipeline từng tầng → tác vụ ráp.

## Kết quả

Cả bảy bộ đi hết tới bước ráp. **Không bộ nào có mái** (0/8 tầng mang mái), nên bước "Duyệt mái" trên trang 3D chưa
chạy được trên BE thật. Lý do từ chối, tính lại bằng chính `roof_outline`/`roof_rects` trên lớp đã lưu:

| Lý do | Tầng | Bộ |
|---|---|---|
| `roof_outline` trả `split`: các phòng có mái che không liền thành một khối | 4 | DS-466, DS-254, DS-571, DS-264 |
| Đường bao phủ được bằng 2–3 chữ nhật, nhưng dạng `flat` chỉ nhận một chữ nhật | 2 | DS-267 (hai mái) |
| Pipeline của tầng hỏng `NOT_A_FLOOR_PLAN` → không có lớp → `NO_WALLS` | 2 | DS-261, DS-396 |

Cả tám `RoofSpec` đều là `flat`, nhưng từ ba nguồn khác nhau:

- 4 mái độ tin 0,6, nguồn hình mái `roofPlan` (DS-261, DS-254, DS-267 hai mái): máy đọc được dốc thoát nước. Chỉ nhóm
  này là ca của NHA-10c.
- 1 mái độ tin 0,6, nguồn `plan` (DS-466): suy từ mặt bằng, không liên quan NHA-10c.
- 3 mái độ tin 0,3, nguồn `default` (DS-571, DS-264, DS-396): không có dấu hiệu nào về mái.

DS-571 và DS-264 còn mang `OPEN_ENVELOPE` nên bị M-15 từ chối trước khi tính hình.

## Nợ và việc chưa làm

- NO-624 — Mái `flat`/một dốc trên đường bao nhiều chữ nhật (nhà chữ L) bị `build_roof` từ chối
  (`apps/api/drawing_sets/assembly.py`, nhánh `_SINGLE_FACE`). Trên dữ liệu thật đây là 2/8 tầng, và là chỗ gần có
  mái nhất. Chủ: lát ráp mái kế tiếp. P2. Mở.
- NO-625 — Phòng máy nhận ra không liền nhau (`split`) thì không có mái: 4/8 tầng. Cần quyết lấy đường bao từ tường
  bao thay cho hợp các phòng, hay nới ngưỡng nối. Chủ: lát ráp mái kế tiếp. P2. Mở.
- NO-626 — Tầng mang mái hỏng `NOT_A_FLOOR_PLAN` (mặt bằng mái, tầng thượng ít tường): 2/8 tầng không có lớp để ráp.
  Chủ: bước tách tường (`apps/ml/walls`). P2. Mở.
- NO-627 — Mã kết quả ráp (`ROOF_SHAPE_COMPLEX`, `NO_WALLS`) chỉ nằm trong `drawing_sets.building`; không route nào
  trả nó và AppFront không có câu nào cho nó. Người dùng xác nhận xong, không thấy mái và không được báo vì sao.
  Chủ: lát màn bộ hồ sơ (AppBack trả mã, AppFront nói thành câu). P2. Mở.
- NO-628 — Mặt cắt thành tầng `is_section` và chạy pipeline là đường đã thiết kế (`apps/api/drawing_sets/confirm.py`).
  Vấn đề thấy được là ở màn Xử lý của AppFront: tầng mặt cắt đứng lẫn trong danh sách tầng như tầng thật (DS-396: năm
  dòng "MAT cat …"). Chưa xem chúng có được tính vào "đã xong k/N tầng" hay không. Chủ: màn Xử lý (AppFront). P3. Mở.
- NO-629 — Trên DB mới, bản gốc `dimensionReading` chấm ra cer 0,53 và `openingAndFurnitureDetection` map50 0,0, khác
  số của stack f14. Chưa tìm nguyên nhân. Chủ: registry model. P3. Mở.

## Kiểm chứng và giới hạn

- Nửa 3D của bài kiểm (chọn mái, "Duyệt mái", tải lại) chưa từng chạy trên BE thật; nó chỉ được kiểm bằng máy chủ giả
  ở `e2e/nha10/house-roof.spec.ts` của AppFront. NO-619 vì thế còn mở cho phần này, chờ NO-624.
- Không đo lặp có hệ thống: năm bộ chạy một lượt, DS-261 và DS-466 chạy hai lượt (lượt đầu trên CPU, lượt hai trên
  GPU). DS-254 chạy trên CPU, bốn bộ còn lại trên GPU.
- Không có test hay mã sản phẩm nào đổi ở AppBack nên không chạy cổng verify cho lát này.
