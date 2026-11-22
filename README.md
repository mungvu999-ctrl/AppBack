# AppBack

Backend cho ứng dụng dựng bản vẽ mặt bằng từ ảnh/PDF (FastAPI + Celery +
Postgres). Khung repo, hiến chương, cổng chất lượng: xem `CLAUDE.md` và
`docs/charter/`.

## Chạy cổng chất lượng

```
bash tools/verify/run.sh verify
```

(`just verify` là bí danh, chỉ dùng được trên máy có `just`.) Chi tiết ở
`CLAUDE.md`.

## Giới hạn đề tài

Dự án dùng mô hình và dữ liệu có giấy phép hạn chế thương mại — **không dùng
cho mục đích thương mại**:

- **ultralytics (YOLO)**: giấy phép **AGPL-3.0**.
- **CubiCasa5K** (dữ liệu huấn luyện phân đoạn mặt bằng): giấy phép
  **CC BY-NC 4.0** — phi thương mại; trọng số huấn luyện từ dữ liệu này kế
  thừa giới hạn phi thương mại đó.
- **IoU, mAP** (độ chính xác phát hiện/phân đoạn) là **mục tiêu báo cáo**, đo
  và ghi lại để theo dõi chất lượng mô hình qua các lần huấn luyện —
  **không phải cổng chất lượng**: `just verify` không hỏng vì các số đo này
  thấp.

Bảng đầy đủ giấy phép từng thành phần: `docs/legal/THIRD-PARTY-LICENSES.md`. Trọng số mô hình được tải về khi
chạy (bản ghim kèm SHA-256 ở `packages/ml_contracts/pinned.py`), **không phân phối trong repo**.
