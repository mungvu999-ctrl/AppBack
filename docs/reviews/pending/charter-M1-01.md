# Nguyên văn thay đổi hiến chương AppBack cho M1-01 — **chờ chủ dự án duyệt**

Trạng thái: **CHƯA DUYỆT.** Tài liệu này chỉ trích nguyên văn từ commit `f4ae5ac6` trên nhánh `feature/m1-01-model-export` (worktree `F:/worktrees/AppBack/m1-01`) để bạn đọc và quyết. Diff gốc: `vong-1/hien-chuong-N49-51.diff` (3 tệp, +22/−5 dòng). Không ai được ghi "đã duyệt" cho tới khi bạn trả lời; khi bạn duyệt, người điều phối thêm trailer `Charter-Approved: <đường tệp này + ngày bạn duyệt>` vào commit hiến chương theo luật `/merge-review` §2.

Cách trả lời đủ: "duyệt nguyên văn", hoặc liệt kê dòng cần sửa.

---

## 1. `docs/charter/BE-00.md` — 4 dòng sửa

**W4 (bảng dây W), thêm tiền tố `mex_`:**
> Id tài nguyên khác: `<tiền tố>_<ULID>` (`prj_ usr_ upl_ drw_ ver_ ntf_ tok_ mdl_ dst_ dsv_ job_ run_ tpl_ bak_ dcn_ **mex_**`).

**§7 Hàng đợi, thêm vế M1-01 vào mục `bake`:**
> `bake` (LB-01: nướng ánh sáng Blender, dịch vụ `bake` riêng, concurrency 1, giữ khoá GPU `gpu:0` khi rảnh, bận thì chạy CPU; **M1-01: task `model_export.compile.run` dựng GLB từ ảnh chụp tài liệu, chạy CPU, không giữ `gpu:0`**).

**§8 Lưu trữ đối tượng, thêm hai khoá:**
> …, `projects/{p}/lighting-bakes/{bak}/{…}` (LB-01; …), **`projects/{p}/model-exports/{mex}/{snapshot.json|model.glb|report.json|CREDITS.txt}` (M1-01), `model-export-packs/{version}/…` (M1-01: gói vật liệu/mẫu có phiên bản, nạp bằng CLI)**. Chặn `..` và ký tự điều khiển trong khoá.

**§12 bước 7 cổng, thêm miễn H1:**
> H1 miễn đóng: `/api/health`, `/api/ready`, `GET /api/files/{token}`, N44 `lighting_bake_download_file` (thân nhị phân, LB-01), **N51 `model_export_download_file` (thân nhị phân, M1-01)**, response 204 (có kiểm thân rỗng).

## 2. `docs/charter/BE-BIND.md` — 1 dòng sửa, 3 dòng thêm

**Ghi chú đầu bảng:** `kiem_bo_prompt.py` đòi: đủ 49 + 2 + **53** dòng (cũ: 50).

**Ba thao tác mới (thêm sau N48):**

| N | Đường | operationId | Schema FE | Loại | Khoá | Prompt | Ghi chú |
|---|---|---|---|---|---|---|---|
| N49 | `POST /api/projects/{project_id}/model-exports` | `model_export_start_export` | `modelExport.ts` | G | `model.export` | M1-01 | màn Xuất, lựa chọn "GLB dựng trên máy chủ" (sau cờ `VITE_EXPORT_ENGINE=compiler`); thân `{}`; 202 job mới, 200 khi đã có job cùng dấu vân (cache) |
| N50 | `GET /api/projects/{project_id}/model-exports/latest` | `model_export_read_latest` | `modelExport.ts` | Đ | thành viên | M1-01 | thăm dò mỗi 3 s; bỏ job `superseded`; `stale` khi tài liệu đổi sau lần xuất; chưa có → 404 |
| N51 | `GET /api/projects/{project_id}/model-exports/{export_id}/files/{file_name}` | `model_export_download_file` | nhị phân | Đ | thành viên | M1-01 | model.glb / report.json / CREDITS.txt; model.glb chỉ khi `done` và `quality ≠ blocked` (409 `MODEL_EXPORT_NOT_READY`); miễn H1 (thân nhị phân) |

## 3. `docs/charter/HOP-DONG-MOI.md` — mục mới §13

> ## 13. Xuất mô hình dựng trên máy chủ → `modelExport.ts` (M1-01)
>
> - `MODEL_EXPORT_STATUSES = ['queued', 'running', 'done', 'failed', 'superseded']`; `MODEL_EXPORT_QUALITIES = ['ready', 'degraded', 'blocked']`.
> - `ModelExportSchema` (strict): `id` (`mex_` + ULID), `status`, `stale` (bool), `fingerprint`, `currentFingerprint`, `snapshotRevisions` (`{levelId, revision}[]`), `counts` (`Record<string, number>`, khoá `<loại>:<trạng thái>`), `issues` (`{severity: minor|major|critical, reason, count}[]`, lý do tiếng Việt do BE sinh), `createdAt`; `startedAt` khi `status ≠ queued`; `endedAt` khi `done|failed`; `quality` khi và chỉ khi `done`; `failureCode` khi và chỉ khi `failed`; `glbBytes` khi `done` và còn tệp. Không trả URL (W1).
> - N49 thân `{}`; dựng ảnh chụp bất biến (mọi tầng: cao độ, cao tầng, ẩn, revision, tài liệu) + phiên bản bộ biên dịch/gói → dấu vân. Cùng dấu vân đã có job `queued|running|done` → 200 job đó; khác → job `queued|running` cũ thành `superseded`, 202 job mới.
> - N50 404 `resource: "modelExport"` khi chưa có. N51 `model.glb` khi chưa `done` hoặc `quality = blocked` → 409 `MODEL_EXPORT_NOT_READY`.
> - Mã hỏng: `MODEL_EXPORT_FAILED`, `MODEL_EXPORT_TIMEOUT`, `MODEL_EXPORT_OUTPUT_INVALID`, `MODEL_EXPORT_STALLED`, `MODEL_EXPORT_PACK_MISSING`.
> - Câu chữ mục này, dòng N49–N51 của BE-BIND và phần thêm ở BE-00 (W4 `mex_`, hàng `bake`, khoá lưu trữ, miễn H1 N51) **chờ chủ dự án duyệt** (tiền lệ LB-01).

---

## Điểm tôi lưu ý khi bạn đọc (không phải đề xuất sửa)
- `model.export` là quyền mới; cần chắc bảng vai trò đã có (reviewer độc lập sẽ kiểm).
- N49 trả 200 khi trùng dấu vân và 202 khi tạo mới: khác với N48 (luôn 202). Nếu bạn muốn đồng nhất thì nói.
- M2-QL sắp tới sẽ cần thêm trường cho lớp đề xuất (hợp đồng dữ liệu đang soạn); đó là một lần duyệt hiến chương khác, không gộp vào đây.

---

## 4. Bổ sung đề nghị sau review lượt 1 (chưa có trong `f4ae5ac6`, cùng chờ duyệt)

Theo phán quyết `docs/reviews/2026-11-08-feature-m1-01-model-export.md` (finding 4, 6, 9):

- **HOP-DONG-MOI §13, `snapshotRevisions`** (F9): sửa `({levelId, revision}[])` thành
  `({levelId, levelName, revision}[])`. Dây đã gửi `levelName` và FE strict đòi khoá này
  (`AppFront src/api/schemas/modelExport.ts`, `SnapshotRevisionSchema`). Thêm câu: "`levelName` là tên tầng lúc
  chụp, không nằm trong dấu vân: đổi tên tầng không làm lượt cũ `stale`, lượt cache giữ tên cũ."
- **HOP-DONG-MOI §13, câu N49** (F4): sau "Cùng dấu vân đã có job `queued|running|done` → 200 job đó" thêm
  "— trừ job `done` đã bị lịch quét dọn tệp (ngoài 5 bản mới nhất): job đó thành `superseded`, 202 job mới".
- **BE-00 §13.1, bảng thư viện** (F6): dòng `packages/domain` thành `pydantic, shapely` (nhánh đã thêm
  `shapely` vào `packages/domain/pyproject.toml`; `shapely` đã khoá sẵn trong `uv.lock`).

---

## Phụ lục — dòng BE-BIND máy đọc (cổng dùng trong lúc chờ duyệt)

Nguyên văn ba dòng N49–N51 của `f4ae5ac6`, đúng khuôn BE-BIND §2. `tools/charter.py` `pending_bind_rows`
đọc bảng này để cổng hợp đồng (bước 7) và cổng case (5b) vẫn kiểm N49–N51 khi `docs/charter/BE-BIND.md`
chưa có dòng. Duyệt xong: chép ba dòng vào BE-BIND, xoá tệp này (`changes/M1-01.md` NO-M1-9).

| # | Đường v1 | operationId | Schema (file) | Loại | Khoá | Nhật ký | Chủ BE | Nối FE |
|---|---|---|---|---|---|---|---|---|
| N49 | `POST /api/projects/{project_id}/model-exports` | `model_export_start_export` | `modelExport.ts` | G | `model.export` | — | M1-01 | màn Xuất, lựa chọn "GLB dựng trên máy chủ" (sau cờ `VITE_EXPORT_ENGINE=compiler`); thân `{}`; 202 job mới, 200 khi đã có job cùng dấu vân (cache) |
| N50 | `GET /api/projects/{project_id}/model-exports/latest` | `model_export_read_latest` | `modelExport.ts` | Đ | thành viên | — | M1-01 | thăm dò mỗi 3 s; bỏ job `superseded`; `stale` khi tài liệu đổi sau lần xuất; chưa có → 404 |
| N51 | `GET /api/projects/{project_id}/model-exports/{export_id}/files/{file_name}` | `model_export_download_file` | nhị phân | Đ | thành viên | — | M1-01 | model.glb / report.json / CREDITS.txt; model.glb chỉ khi `done` và `quality ≠ blocked` (409 `MODEL_EXPORT_NOT_READY`); miễn H1 (thân nhị phân) |
