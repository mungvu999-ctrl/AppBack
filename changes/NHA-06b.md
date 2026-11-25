# NHA-06b — quyết định của người duyệt: N59 lưu, N60 chặn và đóng băng, tầng mới mang cốt đã duyệt

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.E (cột `drawing_sets.building`, "Xác nhận (N60) chặn ở BE"), §5 (lát NHA-06,
phần đường ghi). Người dùng duyệt GATE 1 ngày 2026-11-25, kèm cho phép xuất lại `openapi.json`.
**Nhánh xếp chồng**: tách từ `feature/nha-06a-building-read` (`44050e1e`), nhánh ấy đã chứa `main` (`8d43eb2f`).

- **Migration** `r20261125_nha_06b` (expand thuần): thêm `drawing_sets.building JSONB NULL`. Không backfill; NULL = bộ
  chưa từng nhận quyết định.
- **Miền** `packages/domain/drawing_set/decisions.py` (mới): mô hình `Decisions` (số người duyệt nhập cho tầng và mái,
  nguồn đã chọn cho mâu thuẫn, cảnh báo đã xem), `decide` (đề xuất tính kèm quyết định), `blockers` (lý do chặn xác
  nhận), `hit_key` (khoá của một vi phạm).
- **Miền** `building.py`: `propose_building` nhận số người duyệt nhập (`levels`, `roof_edits`, `skip_roofs`).
  `BuildingProposal` thêm `levels` = cốt và chiều cao **đang hiệu lực** của từng hình (số nhập > nguồn đã chọn > máy);
  `floors` vẫn là giá trị máy. Mọi luật kiểm trên giá trị đang hiệu lực, và M-02, M-17 nay được gọi cho từng mái.
- **N59** (`PUT …/views`): thân nhận thêm `building`. Vắng = giữ quyết định đã lưu; có = thay cả bản (`{}` là xoá hết).
  Lưu vào `building.decisions` cùng `revision` với danh sách hình. Mỗi `levelRef` phải là một hình mặt bằng đang dùng
  trong chính bản duyệt đó, mỗi hình một quyết định; cốt, chiều cao, độ dốc, hướng mái, độ đua có dải kiểm.
- **Route đọc** `GET …/building`: trả thêm `decisions` và `proposal.levels`; đề xuất tính kèm quyết định. Bộ đã xác
  nhận có bản đóng băng thì trả đúng bản đó, không tính lại.
- **N60** (`POST …/confirm`), chỉ với bộ đã có quyết định: tính đề xuất kèm quyết định trước khi chép tệp; còn quyết
  định trỏ tới hình không còn, mâu thuẫn chưa chọn nguồn, vi phạm mức lỗi hay cảnh báo chưa xem → 422 `VALIDATION`,
  `field` lần lượt `building.decisions`, `building.conflicts`, `building.violations`, `building.warnings`. Đạt thì
  tầng mới mang cốt và chiều cao đang hiệu lực, và bản đề xuất được ghi vào `building.confirmed`
  (`schemaVersion` 1). Tầng có sẵn không bị đổi cốt.
- `docs/contracts/openapi.json` xuất lại bằng lệnh của kho (480 dòng thêm; 5 dòng bị thay đều là mô tả).
  `tools/contract/strict/houseProposal.ts`: `SetBuildingStrictSchema` thêm `levels` và `decisions`.

## Tương thích ngược

- **Bộ chưa từng nhận `building`** (FE hiện tại chưa gửi khoá này) xác nhận đúng như trước: không chặn gì thêm, tầng
  mới xếp chồng 3000 mm, không ghi bản đóng băng. Có test riêng. N60 chỉ đổi hành vi từ lúc FE gửi `building` (06c).
- Thân N59 không có `building` không làm mất quyết định đã lưu.

## Lệch khỏi kế hoạch đã duyệt

- **NO-584 chưa đóng** (GATE 1 ghi là đóng): số tầng người duyệt sửa ở N59 vẫn chưa vào đề xuất. `solve_house` chỉ
  nhận "thứ tự hình là thứ tự tầng" (`order_known`), không nhận số tầng tường minh; ép số tầng qua thứ tự sẽ sai với
  hai hình cùng một tầng và với tầng hầm. Cần sửa bộ giải, không thuộc lát này.
- **T-04 không được gọi**: N60 đã từ chối hai hình cùng một đích tầng khi lập kế hoạch đích (`_plan`), gọi thêm luật
  là kiểm hai lần. `rules.t04_one_plan_per_storey` còn lại như hàm không nơi dùng.
- **Số người duyệt nhập làm mâu thuẫn của trường đó biến mất** thay vì hiện là "đã chọn": nguồn `reviewer` không có
  trong ứng viên của mâu thuẫn nên không ghi được vào `chosenSource`.
- **Người duyệt đổi dạng mái thì độ đua quy ước không tự đổi theo**; họ nhập độ đua riêng. Tầng mà máy không đề xuất
  mái (tum có chữ mái dốc) thì người duyệt không thêm mái được ở màn duyệt, phải vẽ tay trong 3D.
- **Chưa làm** (đã nêu ở GATE 1): `LEVEL_SOURCE_CONFLICT` so với tầng có sẵn của dự án; mã câu hỏi mới trên dây; gộp
  `LevelSource`. Chuyển sang NHA-06c vì cần FE hiển thị.
- **Cốt/chiều cao máy đề xuất nằm ngoài dải của tầng** (#10) thì tầng đó xếp chồng như trước, không báo gì.
- **Tầng mới lấy cốt tuyệt đối của bản vẽ**, kể cả giá trị máy mà người duyệt không sửa (đã qua mọi cảnh báo).
  Dự án đã có tầng ở cùng cốt thì tầng mới chồng lên đó: chưa có kiểm tra với tầng có sẵn (NO-593).
- **T-07 của dạng mái nay hỏi cho từng mái** (nhà có tum: hai mái, hai câu hỏi), vì mỗi mái sửa riêng được.
  Trước lát này chỉ hỏi mái trên cùng.
- **Route đọc và N60 dùng chung nhóm 2 luồng, chưa có hạn mức** (review bảo mật, MEDIUM): một thành viên gọi
  dồn route đọc có thể làm N60 của người khác nhận 503. Gộp vào NO-591 của NHA-06a.

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-25, bản sau review, đã có bản sửa 06a `1cc66a63`): bước 1 ruff
  format, 2 ruff check, 4 lint-imports, 5 pytest (cổng tự chạy cả bộ vì `openapi.json` đổi: 12 557 đạt, độ phủ đơn vị
  `apps/api/drawing_sets`, `packages/db`, `packages/domain`, `tools` đạt) — **đạt**; bước 3 mypy **hỏng** ở lượt đó (4
  lỗi kiểu: hai vòng lặp gộp hai kiểu mô hình, một biến test), đã sửa.
- `verify --steps 3,6,7,8` sau khi sửa: 3 mypy --strict (1504 tệp) **đạt**; 6 lint_migrations → migrate_check **đạt**;
  7 hợp đồng: Smoke, "Bản đồ đủ", "Thao tác đã mount", H3, H4 đạt, còn H1 và H5 **không có mẫu** vì bước 5 không chạy
  trong cùng lượt (mọi thao tác đều báo thiếu mẫu) — H1/H5 trên mẫu thật **chưa kiểm ở máy**; 8 openapi **chưa chạy**
  (bước 7 hỏng). `openapi.json` đã xuất lại sau lần đổi model cuối.
- Lượt cổng đầu tiên của lát bị treo và phải dừng: tôi chạy chồng hai lượt cổng nhanh, mỗi lượt cả bộ test, máy thiếu
  bộ nhớ. Từ đó chỉ chạy một lượt mỗi lần.
- Review: `ecc:fastapi-reviewer` và `ecc:security-reviewer`, không CRITICAL. Đã sửa: khoá cảnh báo do máy chủ
  phát trên dây (`violations[].key`) thay vì để FE tự ghép (số thực `30.0`/`30` làm khoá lệch, cảnh báo không
  bao giờ "đã xem" được); khoá dài (M-18 với hàng chục hình mái) được băm để luôn gửi lại được; bản đóng băng
  khác phiên bản hay không còn khớp mô hình thì route đọc tính lại thay vì trả 500 mãi; `seen` và khoá `picks`
  từ chối ký tự điều khiển (JSONB từ chối `\u0000`). Thêm test: cốt người duyệt nhập, dải ngoài, T-07 cho
  từng mái, khoá băm.
- Cổng đầy đủ **không chạy ở máy**: để CI của GitHub chạy khi nhánh được push.

## Nợ và việc chưa làm

- NO-582 **đóng**: M-02 và M-17 được gọi cho từng mái trên giá trị đang hiệu lực.
- NO-584 — vẫn mở (xem trên). Chủ: lát sửa `solve_house` (chưa có mã lát). P2.
- NO-585 — vẫn mở: FE chưa có schema cho route đọc. Chủ: NHA-06c. P2.
- NO-592 — FE chưa gửi `building` ở N59 (`ReplaceViewsSchema` của FE đang `.strict()` với `{views, name}`), nên các
  điều kiện chặn của N60 chưa có hiệu lực với người dùng thật cho tới NHA-06c. Chủ: NHA-06c. P1. Mở.
- NO-593 — Chưa có `LEVEL_SOURCE_CONFLICT` với tầng có sẵn, mã câu hỏi mới, và `LevelSource` một định nghĩa. Chủ:
  NHA-06c. P2. Mở.
