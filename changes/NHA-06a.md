# NHA-06a — route đọc đề xuất ngôi nhà của một bộ hồ sơ

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.E ("Thay đổi hợp đồng FE–BE", dòng route đọc mới), §5 (lát NHA-06). Lát NHA-06
của thiết kế được chia ba: 06a đường đọc (lát này), 06b đường ghi (N59, N60), 06c phía FE. Người dùng duyệt GATE 1 ngày
2026-11-25, kèm cho phép chạy lệnh xuất lại `openapi.json` của kho.
**Nhánh xếp chồng**: tách từ `feature/nha-05b-roof-reading` (`2bf0d5e8`); chuỗi NHA-04 → 01 → 05a → 05b chưa vào `main`.
**Đã merge `origin/main` (`8d43eb2f`) vào nhánh này** (commit `e5686fe1`, không xung đột): trên nền cũ của chuỗi, cổng
hợp đồng đọc bảng thao tác từ `docs/charter/BE-BIND.md` (tệp bảo vệ, `main` đã xoá) nên không nhận được route mới. Từ
`main` (MAIN-RED, PR #119) bảng đó đọc từ `openapi.json` + `cases.toml`.

- `GET /api/projects/{projectId}/drawing-sets/{setId}/building` (`drawing_sets_read_building`, thành viên dự án): trả
  `{enabled, proposal?}`; `proposal` gồm `unitSystem`, `floors`, `roofs`, `conflicts`, `violations`. Tính ngay lúc đọc
  bằng `propose_building`, không ghi DB. Công tắc `DRAWING_SET_ENABLED` tắt → 200 `{"enabled": false}` (mẫu N52). Bộ
  chưa qua phân tích → 200 không có `proposal`. Đọc + giải quá 12 s → 503 `DEPENDENCY_UNAVAILABLE` có `Retry-After`.
- `apps/api/drawing_sets/building_read.py` (mới): lấy hình chưa bị loại của bộ, đọc song song kết quả đọc từng trang,
  chia chữ của trang cho từng hình theo khung người duyệt đã sửa, rồi gọi miền trong nhóm luồng riêng.
- `apps/api/drawing_sets/building_schemas.py` (mới): model trên dây; phần tầng tái dùng `FloorProposalOut` của N52.
- `packages/domain/drawing_set/building.py`: `propose_building` nhận hình mặt bằng mái qua tham số mới `roofs`. Trước
  đây nó tự tìm hình mái bằng loại mà `solve_house` đoán từ chữ; nay loại do người gọi (tức người duyệt) quyết định.
- `packages/domain/drawing_set/set_analysis.py`: tách điều kiện "mặt cắt là nguồn cốt" thành `is_ladder_section` để
  route dùng chung với lượt phân tích; hành vi không đổi.
- `docs/contracts/openapi.json`: xuất lại bằng `bash tools/verify/run.sh openapi` (346 dòng thêm, không dòng nào bị
  xoá): route mới và các schema của nó.
- Khai cho cổng hợp đồng: `apps/api/drawing_sets/cases.toml` (thao tác mới, Loại Đ, khoá "thành viên");
  `tools/contract/strict/houseProposal.ts` thêm `SetBuildingStrictSchema`; `tools/contract/schema-map.ts` ánh xạ thao
  tác tới schema đó. FE chưa có schema cho route này (NHA-06c) nên cổng giải bằng bản strict của harness, như N52.

## Luật chọn đầu vào

- Hình `plan` chưa bị loại → tầng (`floorId` trên dây là mã hình của bộ).
- Hình `roof` → nguồn chữ của mái, hình `section` → nguồn cốt và độ dốc; cả hai chỉ khi loại đã chắc: độ tin từ 0,8
  (ngưỡng `NOT_PLAN_SURE` của lượt phân tích) hoặc người duyệt tự đặt loại (N59 ghi độ tin 1). Mặt cắt còn phải đọc
  được từ ba cốt, như lượt phân tích.
- Hình loại khác (mặt đứng, tổng mặt bằng, chi tiết, khác) không vào.

## Lệch khỏi kế hoạch đã duyệt

- **Cột `drawing_sets.building` và migration dời sang NHA-06b.** Lát này không có nơi nào đọc hay ghi cột đó: quyết
  định của người duyệt chỉ có khi N59 biết lưu (06b). Thêm cột ở đây là thêm cột chết.
- **Phản hồi chưa có `decisions`, `confirmed`, `assembly`** (thiết kế liệt kê cả ba): chưa nơi nào ghi chúng. Khoá nào
  có người ghi thì lát đó thêm vào dây (06b, 07a).
- **Bộ đã `confirmed` vẫn tính lại lúc đọc**, chưa đọc bản đóng băng: việc đóng băng là của N60 ở 06b.
- **Số tầng người duyệt sửa ở N59 chưa vào đề xuất**, và thứ tự hình không được coi là thứ tự tầng
  (`order_known=False`, như lúc máy phân tích). Đề xuất vì thế có thể ghi số tầng khác số người duyệt đã đặt. Nợ NO-584.
- **Không cấp mã N cho route**: sổ đăng ký N nằm ở `docs/charter/` (tệp bảo vệ, không có trong kho).

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-25, trên nhánh đã merge `main`, bản sau review): bước 1 ruff
  format, 2 ruff check, 3 mypy --strict (1500 tệp), 4 lint-imports, 5 pytest (cổng tự chạy cả bộ vì `openapi.json`
  đổi: 12 513 đạt), 7 hợp đồng H1 H3 H4 H5, 8 openapi — **đạt**, mã thoát 0. Hai lượt trước hỏng: lượt một vì nền cũ
  không nhận route mới (xem đầu tệp); lượt hai vì hai ca test ghim số thao tác hợp đồng 111, nay 112
  (`tools/tests/test_charter.py`, `tools/contract/tests/test_check.py`). Tệp `changes` này sửa sau lượt đạt; mã và test
  không đổi.
- Review: `ecc:fastapi-reviewer` (kèm phần phân quyền). Không CRITICAL, không lỗ đọc chéo dự án (`load_set` lọc theo
  dự án trên đường). HIGH đã sửa: `propose_building` gán đè tham số `roofs` bằng kết quả kiểu khác. Đã thêm test: bộ
  của dự án khác → 404; mặt cắt chắc/chưa chắc; loại hết hình; khung để trống; lỗi kho lan ra 503. Không chạy
  `security-reviewer` riêng.
- Cổng đầy đủ **không chạy ở máy**: để CI của GitHub chạy khi nhánh được push. Bước kiểm hợp đồng (bước 7) với mẫu
  thật chỉ chạy ở cổng đầy đủ.

## Nợ và việc chưa làm

- NO-580 (từ NHA-05a) **đóng một phần**: `propose_building` đã có nơi dùng trong mã sản phẩm (route này). `rules.m02_eave`,
  `rules.m17_consistent`, `rules.t04_one_plan_per_storey` vẫn chưa có nơi gọi (NO-582).
- NO-584 — Đề xuất chưa dùng số tầng người duyệt đã sửa và chưa coi thứ tự hình là thứ tự tầng. Chủ: NHA-06b. P2. Mở.
- NO-585 — Route mới chưa có schema zod và client phía AppFront; cổng đang giải bằng bản strict trong
  `tools/contract/strict/houseProposal.ts`. Khi FE có schema thì cân nhắc trỏ `schema-map.ts` sang đó. Chủ: NHA-06c.
  P2. Mở.
