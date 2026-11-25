# NHA-05a — mô hình ngôi nhà, mâu thuẫn theo nguồn, `propose_building`, luật ghép nhóm T

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.E, §3 nhóm T, §5 (lát NHA-05a). Người dùng duyệt GATE 1 ngày 2026-11-25.
**Nhánh xếp chồng**: tách từ `feature/nha-04-sections-into-ladder` (`6452a965`) rồi gộp
`feature/nha-01-roof-text-parsers` (`10364656`) vào, vì lát này cần cả hai mà chưa nhánh nào vào `main`. Khi hai nhánh
ấy được gộp kiểu squash thì nhánh này phải rebase.

- `packages/domain/drawing_set/rules.py` (mới): bảng `RULES` (`Rule(code, level, tolerance)`), kiểu `RuleHit`, bảy hàm
  kiểm thuần `t01_stack` … `t07_assumed`.
- `packages/domain/drawing_set/building.py` (mới): `Measured`, `Conflict`, `BuildingProposal` (pydantic, khoá dây
  camelCase); bảng ưu tiên nguồn `LEVEL_PRIORITY`; `level_conflict`, `effective`; `propose_building(views, sections,
  *, order_known, picks)` bọc `solve_house`.
- Test viết trước mã (hai tệp, 67 ca sau review): mỗi luật T một ca đạt và một ca vi phạm; lựa chọn theo nguồn không
  đổi khi ứng viên đổi thứ tự; bộ mẫu ba tầng + hai mặt cắt không sinh vi phạm nào. Không API, không DB, không mái.
- **`BuildingProposal.floors` luôn là giá trị máy đề xuất.** Lựa chọn của người duyệt (`picks`) chỉ ghi vào
  `Conflict.chosen_source`; giá trị sau lựa chọn lấy bằng `effective(conflict)`. Luật T-01 kiểm trên giá trị đó. Việc
  trộn quyết định vào tầng để lưu là của NHA-06 (`decisions` / `confirmed`).

## Điều đáng biết rút ra khi làm

Khi bộ có thang cốt của mặt cắt, `solve_house` lấy bậc thang làm cốt tầng kể cả khi mặt bằng tự ghi khác hẳn (thử:
hình ghi `+5.200`, đề xuất 3300, nguồn `section`). Trước lát này việc đó không hiện ra ở đâu; nay nó thành `Conflict`
(`section` 3300 đối `plan` 5200) và vi phạm T-05 cho tới khi người duyệt chọn nguồn.

## Lệch khỏi thiết kế

- **Ứng viên của mâu thuẫn** chỉ gồm giá trị `solve_house` đã chọn và cốt chính hình tự ghi. Không sinh ứng viên mặt
  cắt cho hình mà `solve_house` giữ cốt của chính nó: không dựng được ca nào như vậy khi thang cốt có bậc gần (ghi
  `shortcut:` trong mã). Không sửa `proposal.py`.
- **Mâu thuẫn chỉ có cho cốt** (`elevationMm`). Chiều cao tầng suy từ cốt nên chưa có ứng viên riêng; độ dốc và dạng
  mái thuộc NHA-05b.
- **So với tầng có sẵn của dự án** (vế sau của T-01 và T-05) chưa làm: dữ liệu tầng có sẵn nằm ở tầng API (NHA-06).
- **T-04** có hàm kiểm và test, nhưng `propose_building` không gọi: luật cần đích tầng do người duyệt gán, chỉ có ở
  NHA-06.
- **T-02** đếm tầng trên thang cốt bằng khoảng cách bậc (2,4–4,6 m là một tầng, bậc gần hơn bị bỏ qua) vì chưa biết
  vai của từng bậc; mặt cắt thiếu bậc mái sẽ đếm hụt một tầng và sinh cảnh báo oan. Thang không có bậc trệt (±300 mm)
  hoặc có khoảng trống quá 4,6 m thì coi là không đọc được, không so. Cốt của các mặt cắt được gộp thành một thang;
  hai mặt cắt khác gốc cốt sẽ đan vào nhau. Mặt bằng tum / áp mái / sân thượng và tầng hầm không tính vào số tầng đem
  so. NHA-05b có từ khoá cốt thì thay.
- **T-01** chỉ kiểm cặp tầng có số liền nhau; thiếu số tầng ở giữa thì cặp đó bị bỏ qua. Với giá trị máy đề xuất luật
  gần như luôn đạt (chiều cao suy từ hiệu hai cốt); nó có tác dụng khi người duyệt chọn nguồn khác.
- **Bảng ưu tiên** có hai nguồn chưa nơi nào sinh ra (`reviewer`, `elevation`): giữ đúng bảng của thiết kế để hình
  dạng JSONB không đổi khi NHA-06 và NHA-05b bắt đầu dùng.
- **Hệ đơn vị** suy bằng cách đếm chữ feet-inch (`parse_feet_inch`) và cốt mét trên mọi hình; chưa đọc chuỗi kích
  thước.
- `RuleHit.floor_refs` của T-02 và T-06 rỗng: `SectionView` không mang id hình nên chưa trỏ được hình nguồn.
- Hằng `HEIGHT_OK_MM` (2400, 4600) và `CONFLICT_CAP` 0,65 lặp lại giá trị của `heights._LOOSE` và
  `proposal._CONFLICT_CAP` (hằng riêng tư của tệp khác), có chú thích trỏ về nguồn.

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-25, bản sau review): bước 1 ruff format, 2 ruff check, 3 mypy
  --strict, 4 lint-imports, 5 pytest bị ảnh hưởng (104 tệp test, 2726 đạt; tập tệp bị chạm của cả nhánh xếp chồng
  phủ 99,47 % dòng · 97,95 % nhánh), 8 openapi — **đạt**, mã thoát 0. Hai lượt trước đó hỏng ở mypy/ruff vì chú thích
  thừa (`noqa`, `type: ignore`); đã bỏ. Tệp `changes` này sửa mục "Phạm vi kiểm" sau lượt đạt; mã và test không đổi.
- Cổng đầy đủ **không chạy ở máy**: để CI của GitHub chạy khi nhánh được push (chủ dự án yêu cầu ngày 2026-11-25).
- Review: `ecc:python-reviewer`, không CRITICAL. HIGH đã sửa: lựa chọn của người duyệt không được dùng khi kiểm
  T-01. MEDIUM đã sửa: tum/áp mái/sân thượng làm T-02 báo oan; đếm tầng trên thang cốt đếm hụt khi thiếu bậc trệt
  hoặc có khoảng trống; `Conflict` nhận dữ liệu hỏng (một ứng viên, trùng nguồn, giá trị chuỗi); `t05_sources` vỡ với
  dãy rỗng. Còn lại ghi ở "Lệch khỏi thiết kế". Không chạy `security-reviewer`: hàm thuần, chưa có đầu vào người dùng
  nào tới được.

## Nợ và việc chưa làm

- NO-580 — `propose_building`, `rules.py` chưa có nơi dùng trong mã sản phẩm (lệch R-10 theo thiết kế §5). Chủ:
  NHA-05b / NHA-06. P3. Mở.
- NO-581 — Đếm tầng trên thang cốt (T-02) và suy hệ đơn vị (T-06) chưa đo trên bộ thật nào; chỉ có ca tổng hợp. Chủ:
  NHA-05b (từ khoá cốt) và NHA-10 (đo). P2. Mở.
