# CONTRACT-SHA — đồng bộ mốc hợp đồng FE sau khi AppFront #70..#72 vào master

- `tools/contract/APPFRONT_SHA`: `d5e44a95` → `1dbcd273` (AppFront master sau khi #70, #71, #72 merge).
  Bước 7 của cổng trước đó kiểm hợp đồng với một bản FE cũ hơn ba PR.
- `docs/contracts/openapi.json`: chạy lại `run.sh openapi` — **không đổi byte nào**, các PR BE
  #70/#71/#72 đã xuất lại rồi.
- Chỉ đổi mốc, không chạm mã. Cổng chạy bước 5 + 7 (bước 7 cần junit của bước 5).
- Nợ và việc chưa làm: không.

Prompt: CONTRACT-SHA
