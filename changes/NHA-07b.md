# NHA-07b — lý do `roofUnreviewed` chặn "Sẵn sàng"

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.E đoạn "Sẵn sàng", §3 luật M-10, §5 (lát NHA-07b). Người dùng duyệt GATE 1 ngày
2026-11-25, kèm cho phép xuất lại `openapi.json`.
**Nhánh xếp chồng**: tách từ `feature/nha-06b-building-decisions` (`97602f61`). Phần FE ở AppFront, nhánh
`feature/nha-07b-roof-unreviewed` (tách từ `master`).

- `apps/api/spatial_read/readiness.py`: `readiness_of` thêm lý do `roofUnreviewed` khi lớp tầng có mái `source="ai"`
  chưa duyệt. Là dữ kiện của tầng: báo cả khi mọi thực thể khác đã duyệt và cả khi chưa có lượt chạy; luôn đứng cuối
  `reasons` nên thứ tự cũ trên dây không đổi. Mái người vẽ không tính.
- `apps/api/spatial_read/wire.py`: enum `ReadinessReason` thêm `roofUnreviewed`.
- `docs/contracts/openapi.json`: xuất lại bằng lệnh của kho (thêm đúng một giá trị enum).
- Test viết trước mã: mái AI chưa duyệt → `needsReview`; mái đã duyệt (thành `human`) → `ready`; mái người vẽ → `ready`;
  chưa có lượt → `notEvaluated` kèm lý do; thứ tự trên dây; và một ca so enum dây với bảy lý do hàm sinh ra (trước
  đây chưa có ca nào như vậy).

## Thứ tự triển khai

FE giải `readiness.reasons` bằng enum chặt: BE gửi lý do FE chưa biết thì FE không giải được cả tài liệu tầng. Vì vậy
**phần FE phải vào trước phần BE**. Lý do này chưa thể bật trong thực tế cho tới khi NHA-07a dựng mái AI vào tầng.

## Phạm vi kiểm

- `bash tools/verify/run.sh verify --fast` (2026-11-25): bước 1 ruff format, 2 ruff check, 3 mypy --strict (1504
  tệp), 4 lint-imports, 5 pytest (cổng tự chạy cả bộ vì `openapi.json` đổi: 12 563 đạt), 6 lint_migrations →
  migrate_check, 7 hợp đồng H1 H3 H4 H5, 8 openapi — **đạt**, mã thoát 0.
- Sau lượt cổng đó có thêm hai ca test theo review (tầng nhiều mái lẫn lộn; tầng `sectionOnly` vẫn `ready`). Hai ca
  này chỉ chạy trong container nhẹ (`test_readiness.py`: 577 đạt), **chưa qua lượt cổng nào**; mã sản phẩm không đổi.
- Review: `ecc:code-reviewer`, không CRITICAL, không HIGH. Ghi nhận: tầng `sectionOnly` trả sớm nên không xét mái (đã
  ghim bằng test); bản FE cũ còn trong bộ nhớ đệm trình duyệt sẽ không giải được lý do mới, nên NHA-07a không được ra
  trước khi phần FE của lát này đã tới người dùng.
- Phần FE: `pnpm verify:fast` đạt cả bốn bước (typecheck, eslint, vitest, giới hạn độ dài tệp).
- Cổng đầy đủ **không chạy ở máy**: để CI của GitHub chạy khi nhánh được push.

## Nợ và việc chưa làm

- NO-594 — Cổng hợp đồng của BE giải mẫu `spatial_read_layer` bằng schema FE ở bản ghim `tools/contract/APPFRONT_SHA`
  (tệp chỉ người điều phối sửa). Bản ghim chưa có `roofUnreviewed`; các mẫu hiện có không chứa lý do mới nên cổng vẫn
  qua, nhưng nó chưa chứng minh FE giải được lý do mới. Chủ: người điều phối (cập nhật bản ghim sau khi phần FE vào
  `master`). P2. Mở.
- Chưa làm: hành động "Duyệt mái" trong 3D (NHA-09); cho tới đó người dùng chưa có cách đưa mái AI về trạng thái đã
  duyệt, nhưng cũng chưa có mái AI nào (NHA-07a).
