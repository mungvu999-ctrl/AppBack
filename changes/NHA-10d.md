# NHA-10d — test nối chuỗi "duyệt → xác nhận → ráp → mái → duyệt mái" bằng route thật

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §5 (lát NHA-10, tiêu chí "kịch bản e2e duyệt → xác nhận → thấy mái"). Nhánh tách
từ `main` (`1a915eff`), không phụ thuộc PR nào đang mở. Bước này do một sub-agent review chọn theo luật chủ dự án đặt
ngày 2026-11-27 ("tự tạo 1 sub agent review, nếu ko thể quyết được thì mới nhắn hỏi tôi"), không qua câu hỏi GATE 1.

- `apps/api/drawing_sets/tests/test_set_house_chain.py` (mới, một test). Trước lát này hai nửa của chuỗi chỉ có test
  rời: `test_set_building_decisions.py` dừng ở N60, `test_set_assembly.py` bắt đầu từ một bản đóng băng dựng tay và
  không gọi N60, `roofUnreviewed` chỉ có test đơn vị. Test mới đi một mạch bằng route thật:
  N59 ghi "Đã xem" → N60 xác nhận → `assemble_sets` khi pipeline chưa xong (không ráp gì) → tầng chạy xong →
  `assemble_sets` (`BUILT`) → N16 trả mái `source: ai`, dạng và độ dốc đúng đề xuất đã đóng băng, `readiness` có
  `roofUnreviewed` → N15 mang mái → #35 ghi mái thành `source: human, reviewed: true` → lượt ráp sau có xét tầng mà
  không ghi đè (M-11), `roofUnreviewed` hết.
- Chỉ thêm test; không đổi mã sản phẩm, hợp đồng hay migration. Dùng lại helper sẵn có của ba tệp test anh em.

## Vì sao không chạy trên stack F-14

Đã dò bằng script tạm ngoài repo (`F:/runs/dwglab/muc3-nha00/probe_fake_chain.py`), không dựng stack:

- Với bộ ML giả, trang tổng hợp `render_plan(7)` và `render_plan(3)` bị tách thành 1–4 khung không phủ cả trang, loại
  `elevation` 0,30 hoặc `other`, không có tầng: đề xuất không có mái.
- `FakeWallSegmenter` trả mặt nạ rỗng cho mọi khung cắt (dấu của trang tính cả khổ): tầng sẽ ra `NO_WALLS`.
- Chỉ khi hình là mặt bằng phủ đúng cả trang thì mới có đề xuất mái và `build_roof` trả mái. Màn duyệt của AppFront
  không có chỗ sửa khung cắt, nên kịch bản trình duyệt phải gọi thẳng N59 — không còn là đường người dùng thật.
- Chạy F-14 còn cần dựng lại bốn ảnh Docker (có tải về) và ảnh `web` từ một PR chưa gộp.

## Giới hạn

- Pipeline của tầng không chạy: lượt chạy N60 xếp hàng chỉ được đổi `status` thành `completed` (bước hiện tại, phần
  trăm, `pipeline_state` của lượt tải giữ như lúc xếp hàng; không có `quality_summary` nên M-15 không được xét), và
  lớp (một tường, một phòng chữ nhật) gieo bằng factory. Đủ cho điều kiện tới lượt của tác vụ ráp, không phải dấu vết
  đầy đủ của một lượt chạy thật. Chỗ nối pipeline → lớp vẫn chỉ có ở `tests/e2e/test_pipeline_e2e.py` (không có bộ
  hồ sơ).
- `readiness` chỉ được kiểm ở một lý do: có `roofUnreviewed` trước khi duyệt, hết sau khi duyệt. Các lý do khác của
  tầng không được khẳng định.
- Dạng mái `gable` trùng với mặc định; thứ chứng minh mái lấy từ bản đóng băng là độ dốc 16,7° (mặc định là 30°).
- Không có trình duyệt: việc AppFront đọc mái từ N15 thật và thân #35 nó gửi khi bấm "Duyệt mái" vẫn chỉ được kiểm
  với API giả (`e2e/nha10/house-roof.spec.ts`, AppFront PR 116). Test này gửi thân #35 theo cách AppFront làm (bỏ
  `clearAreaM2`), nhưng đó là bản chép tay.

## Phạm vi kiểm

- Phạm vi kiểm: fast. Lát chỉ thêm một tệp test và ghi chú này.
- Cổng nhanh (`bash tools/verify/run.sh verify --fast`) trên trạng thái cuối: **đạt** sáu bước đã chạy — ruff format,
  ruff check, mypy --strict, lint-imports, pytest bị ảnh hưởng (11 tệp test, 305 test đạt; độ phủ tập file bị chạm
  dòng 100 % · nhánh 100 %), openapi. Bước migration và hợp đồng không chạy vì đầu vào không đổi. Cổng đầy đủ để CI.
- Lượt chạy đầu của test hỏng ở #35 (422 `body.layer.rooms.0.clearAreaM2`): lỗi của test, không phải của sản phẩm —
  `clearAreaM2` là trường N16 tự tính, AppFront bỏ nó trước khi ghi (`src/lib/autosave/spatialLayerSave.ts`).
- Review (python-reviewer): không có CRITICAL / HIGH. Hai MEDIUM đã xử lý: lượt ráp sau khi duyệt khẳng định đúng
  `Counter({"BUILT": 1})` thay cho "có một tầng được xét"; mục "Giới hạn" ghi rõ lượt chạy chỉ được giả tối thiểu.

## Nợ và việc chưa làm

- NO-619 — Chưa có lượt chạy trình duyệt nào trên BE thật cho chuỗi bộ hồ sơ → mái (F-14). Với bộ ML giả, chuỗi không
  tới được mái nếu không sửa khung qua API; nên làm cùng lượt chạy trọn tầng 2 bằng ML thật của §2.G, sau khi PR 116
  vào `master`. Chủ: NHA-10b. P2. Mở.
- NO-620 — Màn duyệt bộ hồ sơ của AppFront không có chỗ sửa khung cắt của hình, dù N59 nhận `crop`. Khi máy tách hình
  sai, người duyệt chỉ đổi được loại/tầng hoặc loại bỏ hình. Chủ: lát màn duyệt (AppFront). P3. Mở.
