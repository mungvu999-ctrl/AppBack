# DOT1-HONEST — bỏ chỗ sản phẩm nói sai với người dùng (phần backend)

- `POST share-links`: `password` không rỗng → 422, không nhận rồi bỏ qua; `passwordProtected` vẫn trong response, luôn `false`.
- N49 `GET /api/projects/{project_id}/lighting-bakes/availability` trả `{enabled}` theo `LIGHTING_BAKE_ENABLED` (mặc định `false`). Tắt thì N42 trả 503 `LIGHTING_BAKE_DISABLED`, không tạo dòng, không ghi object.
- N43 trả 404 khi chưa có lượt nào nên không mang cờ được; vì vậy thêm hẳn một route.
- Compose: `base.yml` truyền cờ vào `api`; `env.example`, `prod.yml` ghi cách bật cùng profile `bake`.
- Người dùng duyệt câu chữ mục N49 của `docs/charter/BE-BIND.md` ngày 2026-11-05: "Thêm API báo máy chủ có nướng ánh sáng được không. Khi tắt, nút Nướng ánh sáng bị ẩn và yêu cầu nướng bị từ chối (503)."
- Hợp đồng: `openapi.json` xuất lại bằng `apps.api.core.openapi`; `APPFRONT_SHA` nâng lên commit AppFront có `LightingBakeAvailabilitySchema` (AppFront#47).
- Giới hạn: cờ đặt tay, không dò worker sống; cờ bật mà worker `bake` chết thì lượt vẫn chờ tới `queued_max_age_s` rồi lịch quét chốt `failed`.
- Không migration. Stack đang dùng nướng ánh sáng phải đặt `LIGHTING_BAKE_ENABLED=true`, nếu không nút bị ẩn.
- Cổng: CI của PR (mục `contract`, `integration`, `coverage`).
- Nợ để đợt sau (review vòng 1, P3): `HOP-DONG-MOI.md` và `BE-BIND.md` dòng 72, 128 chưa nêu N49, 503, 422 `password`; thiếu test `password: ""` và viewer đọc N49.
