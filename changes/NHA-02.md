# NHA-02 (phần BE) — dựng mái từ tham số thô: đường bao phòng → chữ nhật → node Pascal

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.C Câu 4-5, §5 (lát NHA-02; tệp thiết kế nằm trên nhánh
`feature/nha-00-dset-bench`). Người dùng duyệt GATE 1 ngày 2026-11-25. Nhánh tách từ `419130c2`, không phụ thuộc
K-33 / NHA-00 / NHA-01 / NHA-04. Phần test AppFront của lát này làm sau, trên repo AppFront.

- `packages/domain/spatial/roof_build.py` (mới): `roof_nodes(rects, *, form, pitch_deg, quarter_turns,
  eave_above_floor_mm, overhang_mm, wall_thickness_mm, roof_id)` trả phần tử của `Roof.nodes` (node `roof` + mỗi chữ
  nhật một `roof-segment`, đủ khoá hình học, không cây); `ridge_height_mm`; kiểu `RoofRect`, `RoofRects`, `RoofForm`.
  Xuất thêm năm tên này ở `packages/domain/spatial/__init__.py`.
- `packages/vision/roofs/rects.py` (mới): `roof_outline(rooms_mm, grow_mm)` hợp đường bao phòng thành đường bao mái;
  `roof_rects(outline_mm)` ra `RoofRects` (gần chữ nhật → một mặt; L/T/U → chữ nhật cực đại) hoặc mã từ chối
  `no_rooms` / `split` / `hole` / `poor_fit` / `complex`.
- `packages/vision/roofs/tests/data/roof_golden.json` (mới): bảy ca do BE phát nguyên văn (chữ nhật hai dốc xoay 20°,
  L hip, T gable, U hip, mái bằng, shed xoay một phần tư, mái bằng chữ L); `test_golden.py` khẳng định tệp trùng đầu ra.
- Test viết trước mã (ba tệp, 64 ca sau review). Không nối vào luồng nào, không đọc `RoofSpec`, không ghi DB, không đổi hợp đồng.

## Lệch khỏi thiết kế

- **Trục chính** lấy theo hướng cạnh cho khung bao nhỏ nhất (như `fitRoofFootprint` của Pascal) chứ không dùng
  `minimum_rotated_rectangle`: khung bao nhỏ nhất của bao lồi có thể nằm chéo với chữ L mảnh.
- **Ngưỡng 2 %** (phủ thiếu / thừa) chỉ áp cho nhánh ghép chữ nhật. Nhánh "gần chữ nhật" theo đúng ngưỡng 0,96 của
  Pascal nên mái có thể thừa tới 4 % đường bao. Độ đua không tính vào phần thừa (`roof_rects` không biết độ đua).
- **"Quá 32 mặt"**: từ chối sớm hơn, khi lưới có quá 8 đường mỗi trục (`complex`); trần 32 vẫn kiểm sau đó.
- **Mái bằng**: `roof_nodes` ghi đúng độ đua ngang được truyền (không ép về 0), vì bản vẽ có thể ghi rõ mái đua; nơi
  gọi (NHA-07a) truyền 0 khi độ đua là mặc định. `pitch` luôn ghi 40.
- **Lọc phòng** theo công năng/tên (loggia, sân thượng, ban công) và các mã `OPEN_ENVELOPE` / `WALL_FRAGMENTS` không
  nằm ở đây: `roof_outline` chỉ nhận đường bao; NHA-07a lọc trước khi gọi.
- **Kiểm hợp đồng với Pascal** (`setScene` thật, 0 lệnh, `getActiveRoofHeight`) đặt ở test AppFront chứ không ở
  `tools/contract`: cổng ấy chỉ giải schema zod và không có dòng nào về mái. Vì vậy **mái bằng chữ L/T/U chưa được
  quyết nhận hay loại**; `roof_nodes` vẫn dựng nếu được gọi.
- `quarter_turns` quay đỉnh mái quanh tâm **từng** chữ nhật (thiết kế chỉ định nghĩa cho một mặt). Mái một dốc: mép
  cao ở phía -z cục bộ của mặt mái; hướng theo từng phần tư vòng ghi ở docstring `roof_nodes` và có test ghim.
- **Giới hạn đã biết:** khung bao vuông (chữ L hai cánh bằng nhau) có hai trục chính tương đương, hàm chọn hướng
  có góc nhỏ hơn; đa giác rất nhỏ không bị chặn cỡ tối thiểu; nhánh "quá 32 mặt" chỉ kiểm được bằng cách hạ trần trong
  test (chưa dựng được đường bao thật nào chạm trần mà vẫn qua phép kiểm phủ).

## Phạm vi kiểm

- `bash tools/verify/run.sh verify` đầy đủ (2026-11-25, cây làm việc trên `419130c2`, bản sau review): bước 0
  node_modules, 1 ruff format, 2 ruff check, 3 mypy --strict, 4 lint-imports, 5 pytest + coverage_gate (12245 đạt;
  tổng dòng 99,33 % · nhánh 97,40 %; tập tệp bị chạm 100 % · 100 %), 5b perf + case_gate, 6 migration, 7 hợp đồng
  H1 H3 H4 H5, 8 openapi — **đạt**, mã thoát 0. Tệp `changes` này sửa mục "Phạm vi kiểm" sau lượt đó; mã và test
  không đổi. Lượt cổng nhanh đầu hỏng bước 3 (mypy, kiểu trả về của `_maximal_boxes`); đã sửa.
- Review: `ecc:python-reviewer`, không CRITICAL/HIGH. MEDIUM đã sửa: đường bao rỗng không trả `no_rooms`, phòng tự
  cắt mất nửa diện tích, `roof_nodes` không chặn cạnh 0/âm/NaN, hướng mép cao mái một dốc chưa định nghĩa. LOW đã sửa:
  gom đường lưới dính chuỗi, chọn trục chính đổi theo nhiễu số thực. Còn lại ghi ở "Lệch khỏi thiết kế". Không chạy
  `security-reviewer`: hàm thuần, chưa có đầu vào người dùng nào tới được.
- **Chưa kiểm qua Pascal thật** (NO-578): danh sách khoá node và quy ước toạ độ mới dựa trên đọc mã vendor và công
  thức `getRoofPlanBounds` chép vào test.

## Nợ và việc chưa làm

- NO-577 — `roof_nodes`, `roof_rects`, `roof_outline` chưa có nơi dùng trong mã sản phẩm (lệch R-10 theo thiết kế §5:
  lát tách riêng để NHA-07a nhỏ lại). Nếu NHA-07a đổi hướng thì xoá. Chủ: NHA-07a. P3. Mở.
- NO-578 — Chưa có phép kiểm nào chạy node BE phát qua Pascal thật: test AppFront của NHA-02 (nạp fixture vàng, đọc
  ngược 0 lệnh với cờ chi tiết bật/tắt và tầng xoay, đối chiếu `getActiveRoofHeight`, dựng hình mái bằng chữ L, sửa
  fixture cũ còn `roofHeight`) chưa làm. Cho tới lúc đó danh sách khoá node chỉ dựa trên đọc mã vendor. Chủ: NHA-02
  phần AppFront. P1. Mở.
