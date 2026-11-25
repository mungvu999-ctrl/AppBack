# MAIN-RED — cổng thôi đọc `docs/charter/`, `main` qua lại được cổng

Hai commit "fix: delete" (`3a919521`, `5e08d45f`, 24/11/2026) xoá `docs/charter/`; cổng đỏ từ lúc thu thập test
(`FileNotFoundError: docs/charter/BE-BIND.md`). Chủ dự án quyết ngày 25/11/2026: "Không khôi phục", và giao việc này trong
chat: "oke làm đi, đơn giản mà thực dụng là tốt nhất" — vì vậy nhánh sửa cả `tools/charter.py`, `tools/case_gate.py`,
`tools/contract/check.py`, `tools/verify/steps.py`, `CLAUDE.md` và `cases.toml` của 27 module.

- Bảng thao tác hợp đồng không còn là bảng Markdown: `tools.charter.load_bind_rows(repo)` ghép method/đường/tên từ
  `docs/contracts/openapi.json` với `type`, `lock`, `logged`, `outside` ở mục `[[endpoint]]` của `apps/api/<module>/cases.toml`.
  Đã đối chiếu: 111 dòng đọc theo cách mới trùng khít 111 dòng của `BE-BIND.md` tại `419130c2` (mọi trường còn dùng).
- `BindRow` bỏ `row_id`, `owner`. `case_gate`: case thêm cố định và C26/C27 tra theo tên thao tác (bảng trong mã là nguồn
  duy nhất, bỏ test so với `CASE.md`); `type` lạ trong `cases.toml` là hỏng.
- Cổng hợp đồng: bỏ `CONTRACT_REQUIRE_ALL` và luật "chủ đã hợp nhất"; lượt thật luôn đòi mọi thao tác trong `openapi.json` đã mount.
- Cổng nhanh: bước 7 không còn chạy khi chỉ `cases.toml` đổi (cổng đầy đủ vẫn chạy).

## Nợ và việc chưa làm
- NO-569: `CLAUDE.md` mục đọc trước 1–3 và skill `merge-review` còn trỏ `docs/charter/BE-00.md`, `BE-KFM.md`, `ENV.md`, `docs/RULE.md`
  (đã xoá, chủ dự án chưa quyết thay bằng gì); chú thích `tools/ci/job.sh:311` và nhiều docstring còn gọi tên `BE-BIND`/`CASE.md`.
