# Review merge fix/share-link-public-base-url → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): e63961ac
- Cổng: phạm vi đầy đủ (lượt 1); `bash tools/verify/run.sh verify` mã thoát 0, 9/9 bước đạt (log: scratchpad/vf/d1c-be.log)
- Độ phủ: `coverage_gate: đạt` (bước 5); không chép số riêng.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| — | — | — | Không có finding | — | — |

Kiểm đặc biệt: `url` nay = `get_core_settings().public_base_url` + `SHARE_PATH` + token. `public_base_url` được kiểm ở
`packages/core/settings.py:38-46` (URL http(s) tuyệt đối, không `/` cuối) nên không sinh `//`; staging/production bắt buộc
https. Bỏ phụ thuộc `Host` (SEC: host-header injection vào liên kết gửi cho khách). Tham số `request` thừa bỏ ở list/create/
revoke; `share_links_open` vẫn dùng `request.app`. Test mới đổi `PUBLIC_BASE_URL` qua `monkeypatch` + `reset_settings_cache`;
fixture `api_env` (`packages/testing/fixtures/api.py:163-165`) đặt lại cache sau test nên không rò sang test khác. Openapi
không đổi (bước 8 đạt). f14 đặt `PUBLIC_BASE_URL=http://localhost:18080` = cổng nginx người dùng mở → liên kết đúng.
Commit có trailer `Prompt: L1-N06`; có `changes/L1-N06.md`; không pragma/skip/xfail mới; không chạm tệp cấm.

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| Tổng hợp (không finding) | 1 | 4,7 | 4,7 |
Tổng: 4,7 / 5

## PHÁN QUYẾT: APPROVE
Cổng đầy đủ xanh, thay đổi nhỏ, có test âm (Host giả).
