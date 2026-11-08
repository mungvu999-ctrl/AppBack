# BE-BIND — Hợp đồng FE gọi tới và prompt sở hữu

> **Bản 5 · 2026-09-17.** Thay đổi so với bản 4 (tranh luận lô 4b): #32 Loại `G +ngoài` (nắn lại xếp hàng pipeline); §4 thêm bảng nguồn trạng thái `Progress` (chuyển từ B2-04).
>
> **Bản 4 · 2026-09-17.** Thay đổi so với bản 3 (tranh luận lô 4a): N8 đổi Loại `C +ngoài` → `C` (route công khai không có idempotency; lặp được chặn bằng C28); #20 ghi rõ id của người khác kiểm bằng test theo việc, không phải C23.
>
> **Bản 3 · 2026-09-17.** Thay đổi so với bản 2: N2 sang v2 (dùng #24); N3/N4 trả `UserSchema`; schema chia F-00a/F-00b/F-00c.
>
> **Bản 2 · 2026-09-17.** FE đo tại `master @ 7dccb44`; bằng chứng dòng ở `01-bao-cao-do.md` §2; đặc tả hợp đồng mới ở `HOP-DONG-MOI.md`.
> **Thay đổi so với bản 1:**
> - thêm các cột `operationId`, Loại (CASE §2), Khoá (quyền), Nhật ký (C18);
> - #2 đăng ký chuyển sang v2 (K7 = B, chỉ vào qua lời mời);
> - #34 đổi chủ sang B2-03; #22 idempotent; N3 gọi `ProjectInviteSink`;
> - thêm §4 luật `Progress`;
> - S1/S2 chia chủ S-case.
>
> **Đường v1** là đường thật sau F-01a (tiền tố `/api`, E1) và F-03 (lồng tạo và liệt kê tầng, T3). Tham số đường viết snake_case (W21).
> `kiem_bo_prompt.py` đòi: đủ 49 + 2 + 50 dòng; mỗi thao tác đúng **một** chủ hoặc "v2"; khối [2] của chủ nhắc đúng `METHOD /api/…`.
> **Khoá:** `—` = mọi vai đã đăng nhập (miễn C07), `thành viên` = mọi vai là thành viên dự án, `admin` = `require_admin` (route trong dự án: `require_project("admin")` — thành viên **và** vai admin, 404 trước 403).

## 1. Hợp đồng CŨ — 49 thao tác HTTP + 2 kênh SSE

| # | Nhóm | Đường v1 | operationId | Schema FE | Loại | Khoá | Nhật ký | Chủ | Ghi chú |
|---|---|---|---|---|---|---|---|---|---|
| 1 | auth | `POST /api/auth/login` | `auth_login` | rỗng + cookie | C | — | — | B1-01 | chống dò (C27); `Origin` (C24) |
| 2 | auth | `POST /api/auth/register` | — | rỗng + cookie | v2 | — | — | **v2** | K7 = B: không mount ở v1; F-09a tắt tab đăng ký |
| 3 | auth | `POST /api/auth/refresh` | `auth_refresh` | thân W16 (golden strict riêng) | R | — | — | B1-01 | HMAC tất định, ân hạn 30 s; cấp lại cookie luồng |
| 4 | auth | `POST /api/auth/logout` | `auth_logout` | rỗng | P | — | — | B1-01 | thu hồi phiên + đóng luồng |
| 5 | drawings | `POST /api/projects/{project_id}/floors/{floor_id}/drawings/uploads` | `drawings_init_upload` | `Progress` | T-init | `floor.upload` | có | B2-04 | + `pageIndex`, `crop?` `{xRatio,yRatio,widthRatio,heightRatio}` (tỉ lệ trang gốc, diện tích ≥ 0,01; sai → 422 `field` = `crop` hoặc `crop.<khoá>`, MULTI-FLOOR-01); `.dwg` → 422 |
| 6 | drawings | `POST /api/projects/{project_id}/drawings/uploads/{upload_id}/chunks` | `drawings_upload_chunk` | `Progress` | T-chunk | `floor.upload` | — | B2-04 | ghi đè theo (upload, chunkIndex) |
| 7 | drawings | `POST /api/projects/{project_id}/drawings/uploads/{upload_id}/complete` | `drawings_complete_upload` | `Progress` | T-complete +ngoài | `floor.upload` | có | B2-04 | idempotent; xếp hàng pipeline sau commit |
| 8 | drawings | `GET /api/projects/{project_id}/drawings/uploads/{upload_id}/progress` | `drawings_read_progress` | `Progress` | Đ | thành viên | — | B2-04 | luật §4 |
| 9 | featureFlags | `GET /api/feature-flags` | `telemetry_read_feature_flags` | F-00a `FeatureFlagsSchema` | Đ\* | — | — | B7-01 | tính theo phiên |
| 10 | floors | `POST /api/projects/{project_id}/floors` | `floors_create_floor` | `Floor` | G | `layer.edit` | có | B2-03 | id `L-…` client gửi; trùng trong dự án → 409 `FLOOR_ID_TAKEN`; trùng tầng vừa xoá mềm → khôi phục; bỏ qua `drawings`/`areaM2` |
| 11 | floors | `DELETE /api/floors/{floor_id}` | `floors_delete_floor` | `Floor` | G | `layer.edit` | có | B2-03 | resolver `project_of_floor`; xoá mềm |
| 12 | floors | `GET /api/projects/{project_id}/floors` | `floors_list_floors` | `Floor[]` | Đ | thành viên | — | B2-03 | danh sách cũ, theo `order` |
| 13 | floors | `PATCH /api/floors/reorder` | `floors_reorder_floors` | `Floor[]` | G | `layer.edit` | có | B2-03 | resolver; thiếu hoặc lẫn tầng → 422 |
| 14 | library | `GET /api/library` | `library_list_items` | `LibraryItem[]` | Đ\* | — | — | B2-06 | |
| 15 | library | `GET /api/library/{item_id}` | `library_read_item` | `LibraryItem` | Đ\* | — | — | B2-06 | |
| 16 | measurements | `GET /api/projects/{project_id}/measurements` | `measurements_list_records` | F-00a `MeasurementRecordSchema[]` | Đ | thành viên | — | B2-07 | trần 1000 |
| 17 | measurements | `POST /api/projects/{project_id}/measurements` | `measurements_create_record` | `MeasurementRecord` | G | `layer.edit` | — | B2-07 | cùng id + cùng thân → 200; khác thân → 409 `MEASUREMENT_ID_TAKEN` |
| 18 | measurements | `DELETE /api/projects/{project_id}/measurements/{measurement_id}` | `measurements_delete_record` | rỗng (204) | G | `layer.edit` | — | B2-07 | |
| 19 | notifications | `GET /api/notifications` | `notifications_list` | `Notification[]` | Đ\* | — | — | B4-02 | trần 200 mới nhất |
| 20 | notifications | `POST /api/notifications/read` | `notifications_mark_read` | rỗng | G\* | — | — | B4-02 | id của người khác bỏ qua, dữ liệu của họ không đổi (test theo việc, không phải C23) |
| 21 | notifications | `POST /api/notifications/read-all` | `notifications_mark_all_read` | rỗng | G\* | — | — | B4-02 | |
| 22 | notifications | `POST /api/notifications/{notification_id}/accept-invite` | `notifications_accept_invite` | `Notification` | G\* | — | — | B4-02 | **idempotent**, đánh dấu đã đọc; membership đã có hiệu lực từ N3 |
| 23 | projects | `GET /api/projects` | `projects_list_projects` | `Project[]` | Đ\* | — | — | B2-01 | chỉ dự án mình là thành viên |
| 24 | projects | `GET /api/projects/{project_id}` | `projects_read_project` | `Project` | Đ | thành viên | — | B2-01 | |
| 25 | projects | `POST /api/projects` | `projects_create_project` | `Project` | G\* | `project.create` | có | B2-01 | người tạo là thành viên |
| 26 | projects | `PATCH /api/projects/{project_id}` | `projects_update_project` | `Project` | G | `project.settings.edit` | có | B2-01 | last-write-wins |
| 27 | projects | `DELETE /api/projects/{project_id}` | `projects_delete_project` | `Project` | G | `admin` | có | B2-01 | xoá mềm; thành viên **và** vai admin (`require_project("admin")`, BUG-513) |
| 28 | propertyTemplates | `GET /api/projects/{project_id}/property-templates` | `templates_list_templates` | F-00a `PropertyTemplateSchema[]` | Đ | thành viên | — | B2-07 | trần 500 |
| 29 | propertyTemplates | `POST /api/projects/{project_id}/property-templates` | `templates_create_template` | F-00a `PropertyTemplateSchema` | G | `layer.edit` | — | B2-07 | |
| 30 | quality | `GET /api/projects/{project_id}/floors/{floor_id}/quality` | `quality_read_assessment` | `ImageQualityAssessment` | Đ | thành viên | — | B2-05b | 5 mã theo F-00a |
| 31 | quality | `POST /api/projects/{project_id}/floors/{floor_id}/quality/corners` | `quality_set_corners` | `ImageQualityAssessment` | G +ngoài | `floor.upload` | — | B2-05b | sau `complete` → xếp hàng lại |
| 32 | quality | `POST /api/projects/{project_id}/floors/{floor_id}/quality/straighten` | `quality_straighten` | `ImageQualityAssessment` | G +ngoài | `floor.upload` | — | B2-05b | nắn xong → xếp hàng lại pipeline |
| 33 | spatial | `GET /api/projects/{project_id}/floors/{floor_id}/spatial` | `spatial_read_floor` | `Floor` | Đ | thành viên | — | B3-02 | |
| 34 | spatial | `PATCH /api/projects/{project_id}/floors/{floor_id}/spatial` | `floors_patch_spatial_floor` | `Floor` | G | `layer.edit` | có | **B2-03** | bảng `floors`; bỏ qua `drawings`/`areaM2` |
| 35 | spatial | `PUT /api/projects/{project_id}/floors/{floor_id}/spatial/layer` | `spatial_write_layer` | F-00a `FloorLayerWriteResultSchema` | GV | `layer.edit` | — | B3-03 | **đổi PATCH → PUT** (F-04b); `remoteChanges` ≥ 1 |
| 36 | spatial | `GET /api/projects/{project_id}/versions/{version_id}` | `versions_read_version` | `Version` | Đ | thành viên | — | B3-04 | |
| 37 | telemetry | `POST /api/telemetry` | `telemetry_ingest_batch` | rỗng (204) | B | — | — | B7-01 | `text/plain` hoặc JSON; không đòi token |
| 38 | users | `GET /api/users` | `users_list_users` | `{total, users}` | A | `user.manage` | — | B1-05 | trần 1000 |
| 39 | users | `GET /api/users/{user_id}/memberships` | `users_list_memberships` | `UserMembership[]` | A | `user.manage` | — | B1-05 | |
| 40 | users | `GET /api/users/{user_id}/activity` | `users_list_activity` | `UserActivity[]` | A | `user.manage` | — | B1-05 | trần 200 |
| 41 | users | `PATCH /api/users/{user_id}/role` | `users_change_role` | `AdminUser` | A | `user.manage` | có | B1-05 | C21; C14 admin cuối; tăng `token_version` |
| 42 | users | `POST /api/users/{user_id}/disable` | `users_disable_user` | `AdminUser` | A | `user.manage` | có | B1-05 | thu hồi phiên + đóng luồng |
| 43 | users | `POST /api/users/{user_id}/enable` | `users_enable_user` | `AdminUser` | A | `user.manage` | có | B1-05 | |
| 44 | users | `POST /api/users/invitations` | `users_invite_users` | `AdminUser[]` | A +ngoài | `user.manage` | có | B1-05 | tạo người `pending` (`usr_`) + token một lần của B1-03 + thư |
| 45 | users | `POST /api/users/invitations/{user_id}/resend` | `users_resend_invitation` | `AdminUser` | A +ngoài | `user.manage` | có | B1-05 | `{user_id}` = `AdminUser.id`; token cũ mất hiệu lực |
| 46 | users | `DELETE /api/users/{user_id}` | `users_delete_user` | `AdminUser` | A | `user.manage` | có | B1-05 | **có thân** `{confirmEmail, userId}` (W13) |
| 47 | share-links | `GET /api/projects/{project_id}/share-links` | `share_links_list` | `ShareLinkWire[]` | Đ | `share.create` | — | FIX-SHARE | mới trước; 404 người ngoài trước 403 viewer |
| 48 | share-links | `POST /api/projects/{project_id}/share-links` | `share_links_create` | `ShareLinkWire` | G | `share.create` | — | FIX-SHARE | 201; token ngẫu nhiên 256 bit |
| 49 | share-links | `DELETE /api/projects/{project_id}/share-links/{link_id}` | `share_links_revoke` | `ShareLinkWire` | G | `share.create` | — | FIX-SHARE | giữ dòng, đặt `revokedAt`; lặp lại vẫn trả `revoked` |
| S1 | SSE | `GET /api/streams/projects/{project_id}/uploads/{upload_id}/progress` | `streams_open_progress` | sự kiện `Progress` | S | thành viên | — | B4-01 | S06 và S08 do B2-04 cài qua `StreamAccessPolicy`/`SnapshotProvider` |
| S2 | SSE | `GET /api/streams/notifications` | `streams_open_notifications` | sự kiện `Notification` | S | — | — | B4-01 | không S06, không S08 (không ảnh chụp) |

**Tổng:** 48/49 HTTP có chủ, 1 để v2 (#2); 2/2 SSE có chủ.

## 2. Hợp đồng MỚI v1

Đặc tả trường ở `HOP-DONG-MOI.md`. Khối [2] của prompt BE trỏ về file schema + mục HOP-DONG-MOI.

| # | Đường v1 | operationId | Schema (file) | Loại | Khoá | Nhật ký | Chủ BE | Nối FE |
|---|---|---|---|---|---|---|---|---|
| N1 | `GET /api/project-summaries` | `projects_list_summaries` | `projectSummaries.ts` | Đ\* | — | — | B2-01 | F-07 |
| N2 | `GET /api/projects/{project_id}/members` | — | — (F-07 đọc #24) | v2 | — | — | **v2** | — |
| N3 | `POST /api/projects/{project_id}/members` | `members_add_member` | `members.ts` (thân) · `UserSchema` (response) | G +ngoài | `project.settings.edit` | có | B2-02 | F-07 |
| N4 | `DELETE /api/projects/{project_id}/members/{user_id}` | `members_remove_member` | `members.ts` · `UserSchema` (response) | G | `project.settings.edit` | có | B2-02 | F-07 |
| N5 | `GET /api/projects/{project_id}/settings` | `settings_read_settings` | `projectSettings.ts` | Đ | thành viên | — | B2-02 | F-07 |
| N6 | `PUT /api/projects/{project_id}/settings` | `settings_replace_settings` | `projectSettings.ts` | GV | `project.settings.edit` | có | B2-02 | F-07 |
| N7 | `GET /api/projects/{project_id}/drawings/uploads/latest` | `drawings_list_latest_uploads` | `uploads.ts` | Đ | thành viên | — | B2-04 | F-05b |
| N7b | `GET /api/projects/{project_id}/pipeline-runs` | `drawings_list_pipeline_runs` | `pipelineRuns.ts` | Đ | thành viên | — | UIFIX-B7 | F-05b |
| N8 | `POST /api/auth/password-reset` | `auth_request_password_reset` | `auth.ts` | C | — | — | B1-03 | F-09a |
| N9 | `POST /api/auth/password-reset/confirm` | `auth_confirm_password_reset` | `auth.ts` | C | — | — | B1-03 | F-09a |
| N10 | `POST /api/auth/invitations/accept` | `auth_accept_invitation` | `auth.ts` | C | — | — | B1-03 | F-09a |
| N10b | `POST /api/auth/invitations/preview` | `auth_preview_invitation` | `auth.ts` | C | — | — | UIFIX-B2 | F-09a |
| N11 | `GET /api/me` | `me_read_profile` | `me.ts` | Đ\* | — | — | B1-04 | F-09b |
| N12 | `PATCH /api/me` | `me_update_profile` | `me.ts` | G\* | — | — | B1-04 | F-09b |
| N13 | `POST /api/me/password` | `me_change_password` | `me.ts` | G\* | — | — | B1-04 | F-09b |
| N14 | `PUT /api/me/avatar` | `me_replace_avatar` | `me.ts` | T-avatar | — | — | B1-04 | F-09b |
| N15 | `GET /api/projects/{project_id}/spatial` | `spatial_read_graph` | `spatialGraph.ts` | Đ | thành viên | — | B3-02 | F-04a |
| N16 | `GET /api/projects/{project_id}/floors/{floor_id}/spatial/layer` | `spatial_read_layer` | `spatialLayer.ts` | Đ | thành viên | — | B3-02 | F-04a |
| N17 | `GET /api/projects/{project_id}/versions?floorId=` | `versions_list_versions` | `versions.ts` | Đ | thành viên | — | B3-04 | F-08 |
| N18 | `GET /api/projects/{project_id}/versions/{version_id}/snapshot?floorId=` | `versions_read_snapshot` | `versions.ts` | Đ | thành viên | — | B3-04 | F-08 |
| N19 | `POST /api/projects/{project_id}/versions/{version_id}/restore` | `versions_restore_version` | `versions.ts` | GV | `layer.edit` | có | B3-04 | F-08 |
| N20 | `PATCH /api/projects/{project_id}/versions/{version_id}/label` | `versions_label_version` | `versions.ts` | G | `layer.edit` | có | B3-04 | F-08 |
| N21 | `GET /api/projects/{project_id}/rule-config` | `rules_read_config` | `ruleConfig.ts` | Đ | thành viên | — | B3-05 | F-10 |
| N22 | `PUT /api/projects/{project_id}/rule-config` | `rules_replace_config` | `ruleConfig.ts` | GV | `ruleset.edit` | có | B3-05 | F-10 |
| N23 | `GET /api/admin/ml/model-families` | `ml_list_families` | `adminMl.ts` | A | admin | — | B6-01 | F-11 |
| N24 | `PUT /api/admin/ml/model-families/{family}/active` | `ml_activate_version` | `adminMl.ts` | A | admin | có | B6-01 | F-11 |
| N25 | `GET /api/admin/ml/model-versions` | `ml_list_versions` | `adminMl.ts` | A | admin | — | B6-01 | F-11 |
| N26 | `POST /api/admin/ml/model-versions` | `ml_upload_version` | `adminMl.ts` | A | admin | có | B6-01 | — (CLI/API, multipart ≤ 512 MiB) |
| N27 | `GET /api/admin/ml/model-versions/{model_version_id}` | `ml_read_version` | `adminMl.ts` | A | admin | — | B6-01 | F-11 |
| N28 | `GET /api/admin/ml/datasets` | `ml_list_datasets` | `adminMl.ts` | A | admin | — | B6-02 | F-12 |
| N29 | `POST /api/admin/ml/datasets` | `ml_create_dataset` | `adminMl.ts` | A | admin | có | B6-02 | — (CLI/API) |
| N30 | `GET /api/admin/ml/datasets/{dataset_id}/versions` | `ml_list_dataset_versions` | `adminMl.ts` | A | admin | — | B6-02 | F-12 |
| N31 | `POST /api/admin/ml/datasets/{dataset_id}/versions` | `ml_build_dataset_version` | `adminMl.ts` | A +ngoài | admin | có | B6-02 | — (FE chỉ xem, E7) |
| N32 | `GET /api/admin/ml/training-jobs` | `ml_list_jobs` | `adminMl.ts` | A | admin | — | B6-03a | F-12 |
| N33 | `POST /api/admin/ml/training-jobs` | `ml_create_job` | `adminMl.ts` | A +ngoài | admin | có | B6-03a | F-12 |
| N34 | `GET /api/admin/ml/training-jobs/{job_id}` | `ml_read_job` | `adminMl.ts` | A | admin | — | B6-03a | F-12 |
| N35 | `POST /api/admin/ml/training-jobs/{job_id}/cancel` | `ml_cancel_job` | `adminMl.ts` | A +ngoài | admin | có | B6-03a | F-12 |
| N36 | `GET /api/admin/ml/training-jobs/{job_id}/metrics?since=` | `ml_list_job_metrics` | `adminMl.ts` | A | admin | — | B6-03a | F-12 |
| N37 | `GET /api/admin/ml/training-jobs/{job_id}/logs?since=` | `ml_list_job_logs` | `adminMl.ts` | A | admin | — | B6-03a | F-12 |
| N38 | `GET /api/share/{token}` | `share_links_open` | `client.ts` (`SharedModel`) | C | — | — | FIX-2436 | `SharedModelPage` |
| N39 | `POST /api/projects/{project_id}/exports` | `share_links_export` | `spatialGraph.ts` | Đ | `model.export` | — | FIX-SHARE | nút Xuất (chỉ đọc, cùng dạng N15) |
| N40 | `DELETE /api/projects/{project_id}/creation` | `projects_cancel_create_project` | `index.ts` (`Project`) | G | thành viên | — | B2-01 | toast "Đã tạo" (Hoàn tác: người tạo, ≤ 10 phút) |
| N41 | `POST /api/projects/{project_id}/drawings/page-thumbnails` | `drawings_page_thumbnails` | `uploads.ts` (`PageThumbnails`) | Đ | `floor.upload` | — | UX41-U05 | ô chọn trang PDF (thân = byte PDF ≤ 8 MiB, không ghi gì) |
| N42 | `POST /api/projects/{project_id}/lighting-bakes` | `lighting_bake_start_bake` | `lightingBake.ts` | G | `layer.edit` | — | LB-01 | nút "Nướng ánh sáng" (thân = byte GLB ≤ 128 MiB; 409 khi đang có lượt chờ/chạy) |
| N43 | `GET /api/projects/{project_id}/lighting-bakes/latest` | `lighting_bake_read_latest` | `lightingBake.ts` | Đ | thành viên | — | LB-01 | thăm dò trạng thái mỗi 5 s; chưa có → 404 |
| N44 | `GET /api/projects/{project_id}/lighting-bakes/{bake_id}/files/{file_name}` | `lighting_bake_download_file` | nhị phân | Đ | thành viên | — | LB-01 | baked.glb / lightmap.png / manifest.json; miễn H1 (thân nhị phân) |
| N45 | `GET /api/projects/{project_id}/floors/{floor_id}/dimension-strings` | `spatial_read_dimension_strings` | `quality.ts` (`DimensionStrings`) | Đ | thành viên | — | QT-SCALE-API | màn tỉ lệ: chữ kích thước toàn số 300–30 000 mm của lượt mới nhất; chưa có lượt → `items: []` |
| N46 | `GET /api/projects/{project_id}/floors/{floor_id}/detected-geometry` | `spatial_read_detected_geometry` | `quality.ts` (`DetectedGeometry`) | Đ | thành viên | — | QT-SCALE-API | màn tỉ lệ: điểm bắt, bề dày tường, cửa, phòng lớn nhất, hộp ngoài (px trang); chưa có lượt → rỗng |
| N47 | `GET /api/projects/{project_id}/design-concept` | `design_director_read_state` | `designConcept.ts` (`DesignConceptState`) | Đ | thành viên | — | AID-01 | editor: công tắc, kiểu AI đề xuất, concept đang chọn; thăm dò 3 s khi đang chạy |
| N48 | `POST /api/projects/{project_id}/design-concept` | `design_director_select_concept` | `designConcept.ts` (`DesignConcept`) | G | `project.settings.edit` | — | AID-01 | ô chọn phong cách; luôn 202 (dùng lại concept cùng đầu vào); 503 `DESIGN_DIRECTOR_DISABLED`, 429 `DESIGN_DIRECTOR_QUOTA` |

N24 có version: áp thêm C09, C09b, C14 theo CASE §2.2 A.

## 3. Thân dùng chung (F-00a) — schema mới chia file theo `HOP-DONG-MOI.md` §9: F-00a (lõi, không gian, phiên bản, phép đo, khuôn, cờ, mã chất lượng), F-00b (quản trị ML), F-00c (dự án, thành viên, cài đặt, tải lên, xác thực, tài khoản, cấu hình luật)

| Schema | File | Dùng ở |
|---|---|---|
| `ApiErrorBodySchema` | `errors.ts` | mọi status ≥ 400 trừ `VERSION_CONFLICT` |
| `VersionConflictBodySchema` | `errors.ts` | 409 của GV (W20) |
| `CursorPageSchema(item)`, `VersionedWriteSchema(body)` | `common.ts` | danh sách mới (W22), thân GV |

## 4. Luật `Progress` (#5–#8, S1)

`ProgressSchema` strict có 4 trạng thái (`src/api/schemas/index.ts:165-174`). FE coi `completed || endedAt !== undefined` là đã xong và vẽ **mọi** bước "xong" (`src/screens/pipeline/ProcessingScreen/processingGateway.ts`, hàm `toStageBreakdown`, nhánh "đã xong"). Vì vậy:

- `id` là **upload id**.
- Từ init tới trước khi xếp hàng pipeline: `status:"pending"`, `step:"preprocess"`, `progressPercent: 0`.
- `step` luôn là một trong 6 id của `src/lib/realtime/pipeline.ts:48-55`: bước **chưa xong có thứ tự thấp nhất**. `progressPercent` tính theo trọng số 5/30/20/15/20/10 và tăng đơn điệu.
- `endedAt` **chỉ** có khi `status:"completed"`. Lượt hỏng **không** có `endedAt`.
- Lượt bị thay thế bởi lần tải lại: `status:"failed"`, `error:"PIPELINE_SUPERSEDED"`.
- `error` luôn là **mã UPPER_SNAKE**, không phải câu.
- Golden H1 bắt buộc cho các nhánh: `pending`, `running`, `completed`, `failed`.

**Nguồn trạng thái** (B2-04 cài `progress.progress_wire`; `current_step` ∈ 6 id, trọng số 5/30/20/15/20/10):

| Nguồn | `status` | `step` | `progressPercent` | Tuỳ chọn |
|---|---|---|---|---|
| upload `receiving`, hoặc lượt mới nhất `pending` | `pending` | `preprocess` | 0 | — |
| upload `rejected` | `failed` | `preprocess` | 0 | `error` = `rejected_code` |
| lượt `running` | `running` | `current_step` | `progress_percent` | `startedAt` |
| lượt `completed` | `completed` | `qualityCheck` | 100 | `startedAt?`, `endedAt` |
| lượt `failed` | `failed` | `current_step` | giá trị cuối | `error`, `startedAt?`; **không** `endedAt` (K33) |

## 5. Tên dùng chung với FE

| Tên | Giá trị | Nguồn |
|---|---|---|
| Vai | `admin`, `engineer`, `viewer` | `src/lib/auth/permissions.ts` |
| Khoá quyền | 10 khoá `PermissionKey` | `src/lib/auth/permissions.ts:19-29` |
| Bước pipeline | `preprocess 5`, `wallSegmentation 30`, `openingAndFurnitureDetection 20`, `dimensionReading 15`, `spatialDataBuild 20`, `qualityCheck 10` | `src/lib/realtime/pipeline.ts:48-55` |
| Mã chất lượng ảnh | 5 mã | `src/screens/upload/InputQualityGate/useInputQualityGate.ts:275-331` |
| Mã luật | 25 mã (H4) | `src/domain/rules` |
| Loại thông báo | `aiCompleted`, `violationFound`, `projectInvite` (`commentMention` v2) | `src/api/schemas/notifications.ts` |
