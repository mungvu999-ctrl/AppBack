# Review merge fix/share-bake-honest-2 → main

- Ngày: 2026-11-05 · Reviewer: phiên /merge-review (agent độc lập, lượt 1) · Commit đầu nhánh: fa4c2114999a (base `origin/main` d1deb23c0294 = merge-base; nhánh chưa đẩy, đúng dự kiến)
- Cổng: Phạm vi kiểm: đầy đủ (CI GitHub Actions, không chạy `run.sh verify` tại chỗ, theo chỉ định của phiên gọi và tiền lệ `2026-11-04-fix-wall-runs-lcorner.md`). Run `CI` 42045788876 và `Commits` 42045788545 của PR #59: **`success`, 24/24 mục xanh**, nhưng chạy trên **717ae9fc, không phải đầu nhánh** (`headSha` đọc từ API).
  - Chuyển bằng chứng sang fa4c2114 được cho các job theo cây mã: `git diff 717ae9fc fa4c2114 --stat` chỉ có `changes/DOT1-HONEST.md` +11, và PR #59 cùng base d1deb23c. Job `commits` thì **không chuyển được** vì nó soát commit 5fedeb30 + 717ae9fc; ba commit của nhánh này reviewer tự soát (xem Phạm vi). Điều kiện 2 đòi CI trên đúng đầu nhánh.
  - Trong log: bước 3 mypy đạt (1323 tệp), bước 8 openapi `--compare` đạt, `case_gate` đạt (100 thao tác, N49 đủ mã bắt buộc), bước 7 H1 H3 H4 H5 đạt với AppFront @ f8e5e50b (100 mục / 100 dòng BE-BIND), `tools.ci.h2` đạt, job `perf` xanh.
  - Reviewer tự chạy @ fa4c2114 (`run.sh shell`, chạy tại chỗ): `python -m apps.api.core.openapi --out /tmp/o.json --compare docs/contracts/openapi.json` **thoát 0**; `pytest apps/api/lighting_bake/tests apps/api/share_links/tests tools/contract/tests tools/tests/test_charter.py` **thoát 0**, 180 passed.
- Độ phủ (CI @ 717ae9fc, `coverage_gate: đạt`): tổng dòng 99,31 % · nhánh 97,49 % | `apps/api/lighting_bake` 98,59/100 · `apps/api/share_links` 100/91,67 · `apps/worker/lighting_bake` 100/100.
- Ghi chú: checkout không có `RULE.md`, `RULE-CODE.md`, `DEBT.md`. Reviewer không đọc được nội dung các tệp đó (kể cả R-33b, §5). Mã hạng mục và trọng số chấm lấy theo phán quyết trước, nợ ghi vào `changes/<mã>.md`.

## Phạm vi và điều kiện dừng sớm

- Cây sạch. Diff: 3 commit, 19 tệp, +194/−14; logic dưới 400 dòng.
- `00cba41c docs(charter)`, `f8eb4887 fix(api)`, `fa4c2114 chore(contract)`: đều qua `.githooks/commit-msg`, đều có `Prompt: DOT1-HONEST`. Có `changes/DOT1-HONEST.md`.
- `docs/charter/*`: chỉ 00cba41c chạm, chỉ chạm `changes/DOT1-HONEST.md` và `docs/charter/BE-BIND.md`, có trailer `Charter-Approved: changes/DOT1-HONEST.md (người dùng duyệt 2026-11-05)`; dòng duyệt có thật ở `changes/DOT1-HONEST.md:7`. Đạt ngoại lệ. Hai giới hạn: dòng duyệt do tác giả ghi, reviewer không xem được lời người dùng (tiền lệ AID-01 cũng chấp nhận dòng trong tệp); câu được duyệt là câu tóm tắt, không phải nguyên văn dòng N49, nội dung thì khớp.
- `openapi.json` + `APPFRONT_SHA`: chỉ commit `chore(contract)` chạm, đúng tiền lệ AID-01. `openapi.json` đúng là bản xuất (`--compare` thoát 0), diff chỉ gồm `LightingBakeAvailability`, route N49, `maxLength: 0` và mô tả `ShareLinkCreateIn`. `uv.lock`, `pyproject.toml` không đổi.
- Không có `pragma: no cover/branch`, `type: ignore`, `noqa`, skip/xfail mới. Không hạ ngưỡng; hai số đếm `test_check.py` (99→100), `test_charter.py` (50→51) tăng đúng theo dòng mới.
- Nhánh cũ: 5fedeb30 trên PR #59 gộp `docs/charter/BE-BIND.md` với `.py` và `tools/**`, không có `Charter-Approved`; merge PR #59 sẽ dính REJECT. Chỉ merge nhánh này.

## `APPFRONT_SHA` và thứ tự merge

- `tools/contract/APPFRONT_SHA` = f8e5e50b. Trong `F:/worktrees/AppFront/dot1` sau `git fetch origin`: `merge-base --is-ancestor f8e5e50b origin/master` → **không**; `origin/master` = a0dc5d2e; chỉ `origin/fix/honest-ui-round1` chứa commit này (AppFront #47 đang mở, `MERGEABLE`, đầu bfba1994).
- CI FE: run 42039252367 @ f8e5e50b `success`; run 42048540077 @ bfba1994 `success`. `gh pr checks 47` lúc đầu báo "no checks", nên đã đọc thẳng từng run. `git diff f8e5e50b bfba1994` chỉ đổi `usePascalEdits`, không chạm hợp đồng.
- **Ý nghĩa với phán quyết:** tiền lệ AID-01 xếp đúng tình huống này là P1, nên nhánh chưa được duyệt lúc này dù mã và CI đều tốt. Ở đây còn có hậu quả cho người dùng, xem finding 1.
- Merge FE trước là an toàn: `useLightingBake.ts:64-66` @ f8e5e50b coi N49 hỏng (BE cũ trả 404) là `available=false`, nên ẩn nút; ShareDialog không gửi `password` (`ShareDialog.test.tsx:336`).

## Soát nội dung

- **N42 tắt:** `service.py:69-70` kiểm cờ trước mọi truy vấn và trước khi đọc thân, sau cổng `layer.edit` của router. Test xác nhận 503, không dòng, không object. Đường trả sớm cùng kiểu 409 có sẵn, không thêm rủi ro với `proxy_request_buffering off`.
- **N49:** cổng thành viên như N43, người ngoài 404 (C06), dự án thiếu 404 (C08). Không chạm DB ngoài cổng. Literal `availability` không đụng route `{bake_id}/files/{file_name}`. Regex nginx 128 MiB của N42 có `$`, không ảnh hưởng N49. Mẫu `enabled` giống N47.
- **Mật khẩu:** `Field(max_length=0)` trả 422 `field: "password"`. `validation_error` (`apps/api/core/errors.py:216-229`) chỉ đưa `field` + `count` ra dây và không log `input`, nên mật khẩu người dùng không lọt vào response hay log.
- **Compose:** `base.yml:180` dùng `${…:-false}`, chuỗi rỗng vẫn về false. `docker compose config api` với `prod.yml` và `ci.yml` cho `"false"` mặc định; `prod.yml` cho `"true"` khi đặt biến. `dev.yml` không dựng được cấu hình vì thiếu `APPFRONT_CONTEXT`, nên chưa kiểm. Lệnh chuẩn `--env-file /etc/appback/appback.env` (`prod.yml:5`) truyền được cờ như chú thích `prod.yml:192` nói.
- **Nợ đã ghi:** cờ đặt tay, không dò worker sống, worker chết thì lượt chờ tới `queued_max_age_s` rồi `failed` (`settings.py`, `env.example`, `changes/DOT1-HONEST.md:9`). Lý do đứng được, có đường nâng cấp. Mặc định `false` nghĩa là stack đang dùng nướng phải tự đặt cờ khi nâng cấp; `changes/DOT1-HONEST.md:10` có ghi.
- **SEC/CON/PERF:** không thêm trạng thái ghi, truy vấn hay đầu vào tệp; N49 trả về một cờ đã cache.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P1 | API (hợp đồng FE) | `APPFRONT_SHA` = f8e5e50b chưa có trên `origin/master` (a0dc5d2e), chỉ ở nhánh của AppFront #47. Vào `main` lúc này thì `main` ghim FE chưa merge; nếu #47 squash hoặc xoá nhánh, SHA có thể mồ côi và job `contract` (`git fetch … origin <sha>`) hỏng. BE vào trước FE còn gây lỗi thật: `master` FE gửi `password` khi bật ô mật khẩu (`useShareDialog.ts:396` @ origin/master) → nay 422; `master` FE chưa có N49 nên vẫn hiện nút Nướng → 503 trên stack mặc định | `tools/contract/APPFRONT_SHA:1` | Merge AppFront #47 vào `master` trước, bằng merge commit như các PR gần đây, rồi kiểm `git merge-base --is-ancestor f8e5e50b origin/master`. Nếu squash: ghim lại vào commit trên `origin/master` bằng một commit `chore(contract)` và chạy lại CI |
| 2 | P3 | DOC (charter) | `HOP-DONG-MOI.md` mô tả mã lỗi N42 và có mục cho N47/N48 (dòng 704-705), nhưng nhánh không thêm N49 hay 503 `LIGHTING_BAKE_DISABLED`; dòng N42 của BE-BIND và dòng 48 share-links cũng chưa nêu 503, 422 `password`. Bản duyệt của người dùng chỉ phủ N49 ở BE-BIND | `docs/charter/HOP-DONG-MOI.md:685-686`; `docs/charter/BE-BIND.md:72,128` | Thêm vào bản charter kế tiếp sau khi người dùng duyệt câu chữ; ghi một dòng nợ trong `changes/DOT1-HONEST.md` |
| 3 | P3 | TEST / LOG | Chỉ test `password` không rỗng. Biên `""` và `null` vẫn được nhận (ghi trong docstring, không có test); `useShareDialog.ts:396` trên FE `master` không tự lọc chuỗi rỗng. N49 chỉ test chủ dự án, chưa test viewer → 200 dù dòng charter ghi "thành viên" | `apps/api/share_links/tests/test_routes.py:57-59`; `apps/api/lighting_bake/tests/test_routes.py:325-336` | Thêm hai assert ngắn: `{"password": ""}` → 201 với `passwordProtected=false`; viewer đọc N49 → 200 |
| 4 | Nit | MNT | `retry_after=10` là số trần, trong khi `design_director/errors.py:17` đặt tên hằng `RETRY_AFTER_S`; test không assert header `Retry-After` | `apps/api/lighting_bake/service.py:70`; `apps/api/lighting_bake/tests/test_routes.py:373-374` | Đặt hằng trong `errors.py`, thêm một assert |
| 5 | Nit | DOC | Docstring ghi câu FE "Nướng ánh sáng chưa được bật.", nhưng AppFront @ f8e5e50b không có chuỗi hay nhánh cho mã này. Cờ tắt khi editor đang mở thì người dùng thấy "Không gửi được mô hình để nướng." (`useLightingBake.ts:106`) | `apps/api/lighting_bake/errors.py:12` | Bỏ câu FE khỏi docstring, hoặc FE thêm nhánh cho mã này |
| 6 | Nit | MNT | Commit f8eb4887 thêm `LightingBakeAvailabilitySchema` vào `schema-map.ts` khi pin còn cũ và `openapi.json` chưa xuất lại, nên một mình commit đó không qua bước 7, 8 (suy từ diff, không chạy riêng). Tiền lệ AID-01 #15 cũng là Nit | `tools/contract/schema-map.ts:145` | Không cần làm gì: dùng CI đầu nhánh |
| 7 | Nit | DOC | Cột prompt của N49 và chú thích `schema-map.ts` ghi `LB-01`, còn mã thật là `DOT1-HONEST`. Tra nguồn gốc dòng này sẽ dẫn sai về LB-01 | `docs/charter/BE-BIND.md:135` | Giữ nguyên nếu cột mang nghĩa "module chủ"; nếu không, sửa ở bản charter kế tiếp |

Không P0, không P2. Một P1, hai P3, bốn Nit.

## Điểm
| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 5 | 1,25 |
| CON | 15% | 5 | 0,75 |
| LOG | 15% | 4,5 | 0,675 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 4,5 | 0,45 |
| DB/API | 10% | 3,5 | 0,35 |
| TEST | 7% | 4,5 | 0,315 |
| OBS/OPS | 5% | 4 | 0,20 |
| MNT | 3% | 4,5 | 0,135 |
Tổng: 4,63 / 5

## PHÁN QUYẾT: REQUEST CHANGES

Mã backend không có lỗi chặn; CI của cùng cây mã xanh và test đích tại chỗ đạt. Chỗ chặn duy nhất là P1 #1: pin AppFront chưa nằm trên `master`, và ma trận không cho APPROVE khi P1 chưa có waiver. Việc phải làm nằm ở thứ tự merge, không phải sửa mã backend. Để được duyệt:
1. Merge AppFront #47 vào `master` trước, không squash. Kiểm `git merge-base --is-ancestor f8e5e50b origin/master` thoát 0. Nếu squash thì ghim lại `APPFRONT_SHA` bằng một commit `chore(contract)`.
2. Đẩy nhánh này, mở PR mới, đóng PR #59 không merge. CI phải xanh trên **đúng đầu nhánh**, gồm `commits` và `contract`.
3. P3 #2, #3: sửa hoặc ghi dòng nợ trong `changes/DOT1-HONEST.md`. Nit không chặn.

Lượt 2 chỉ cần kiểm mục 1, mục 2 và phần diff mới, không cần soát lại mã đã soát ở đây.
