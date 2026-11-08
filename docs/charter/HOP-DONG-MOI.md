# HOP-DONG-MOI — Đặc tả dây của hợp đồng mới v1

> **Bản 11 · 2026-10-14** (QA-04, BUG-577, QD-001): §4 #35 nhận thêm `dimensions?` — thay trọn danh sách kích thước của tầng, ghi nhật ký §1.3 theo id `M-…`, base cũ → 409 như lớp; N16 `dimensions` không còn chỉ đọc.
>
> **Bản 10 · 2026-09-18** (chỉnh sau kiểm áp 6b: §4 "áp cho mọi tầng" — chủ là F-04c, base của tầng bẩn, hộp thoại A9; sau kiểm áp 6c: §5 N19 trùng hiện trạng nhận ra bằng `floorRevision`). Tranh luận phần 6 (`dieu-phoi/phan-6/phan-xu-6a.md`): §7.1 FE không tái dùng id đã xoá trong phiên, hoàn tác xoá gặp 409 → id mới gửi lại một lần; §10 AC1 chỉ báo lưu của giao diện/thông báo nói "chỉ giữ trong phiên này"; §5 N19 trùng hiện trạng trả bản sẵn có (phân xử 6c); §1.1 gửi lại đúng thân sau mất phản hồi (phân xử 6b).
>
> **Bản 9 · 2026-09-18.** Chuẩn bị phần 6: §1.1 rào C1 thêm N19 (409 → dải tải lại, không `resolveConflict`); §3 tách F-09 → F-09a/F-09b; §10 ML2/ML4 theo bản gốc `pending`.
>
> **Bản 8 · 2026-09-17.** Tranh luận phần 5: §4 #35 tỉ lệ người hiệu chỉnh gắn với trang; §8 không có bản gốc `wallSegmentation`, N24 `versionId: null` chỉ cho họ tường, N36/N37 cửa sổ muộn, `metrics` bản gốc đo trên tập tổng hợp.
>
> **Bản 7 · 2026-09-17.** Chuẩn bị phần 5: §8 bản gốc seed `pending`, ML đánh giá khi worker `ml` lên; `wallSegmentation` gốc không kích hoạt sẵn; N24 chỉ nhận `onnx`.
>
> **Bản 6 · 2026-09-17.** Tranh luận lô 4c: §0.1 BE kiểm W4 khi ghi; §1 `baseVersion > revision` ở #35/N19 → 422; §2 N1 hàm đếm lại ở B3-02; §4.1 `Level.reviewed`; §4 #35 tỉ lệ, `Dimension.line`, `Room.areaM2`, trần thân; §5 N19 khôi phục `dimensions`, ảnh chụp lược đồ cũ.
>
> **Bản 5 · 2026-09-17.** Tranh luận lô 4b: N1 nguồn tính là bảng đếm theo tầng; N3 gọi sink trong giao dịch; N7 chọn upload `complete` mới nhất.
>
> **Bản 4 · 2026-09-17.** Thay đổi so với bản 3 (tranh luận lô 4a): §3 chốt đường link trong thư, luật đọc token và phân loại lỗi cho F-09; N8 có cửa sổ chờ 15 phút (C28).
>
> **Bản 3 · 2026-09-17.** Thay đổi so với bản 2 (áp tranh luận phần 2 chặng 3, `04-tranh-luan-chang-3.md` §2):
> - Tách schema cho **F-00a / F-00b / F-00c**; file mới **không** `export *` qua `index.ts`.
> - Luật id cho schema mới; bảng refine §0.1, chia hai loại: kiểm trong zod, hoặc "H1 ngữ cảnh" ở runner B0-07.
> - `RemoteFieldChange` luôn có khoá `value`, thêm `changedByName`, thêm bảng đặt tên `field` (§1.3).
> - N1: luật suy `status`, cursor theo `id`, bảng đếm. **N2 sang v2**; N3/N4 trả `UserSchema`; `MEMBER_LAST_EDITOR`.
> - Thực thể không gian có đủ trường duyệt kèm luật giá trị; `Dimension` nới theo miền.
> - #35 `layer` tuỳ chọn; thứ tự ưu tiên tỉ lệ; định nghĩa "pixel".
> - Phiên bản có `floorRevision`. Phép đo **xoá cứng**.
> - ML: bản gốc seed sẵn; refine `TrainingJob`; `nextCursor` khi polling.
>
> Thiết kế từ màn FE thật (`F:\App\AppFront\src` @ `7dccb44`). Trường không màn nào cần thì **không** đưa vào (W1).
> **F-00a / F-00b / F-00c** viết zod từ file này. **Prompt BE** trỏ về file này cho tới khi schema FE đã hợp nhất; sau đó chính schema FE là nguồn chuẩn (H1). Bằng chứng dòng rút gọn: `S:` = `src/`.

---

## 0. Quy ước chung

| Chủ đề | Luật |
|---|---|
| Schema nhận (response) | `.strict().transform(...)`, dựng lại object để vắng vẫn là vắng (`S:api/schemas/index.ts:141-160`). Ngoại lệ duy nhất: `RemoteFieldChange.value` (§1.1) |
| Schema gửi (request) | chỉ `.strict()` (`S:api/schemas/users.ts:265-297`) |
| Schema hai chiều | `MeasurementRecordSchema`: strict + transform. Body GV (`XxxBodySchema`) không transform, để còn `.extend()` được; bản response = `Body.extend({revision}).strict().transform(…)`. Nhánh Draft dựng bằng `branch.pick(…)`. Mọi đối số của `VersionedWriteSchema` là `z.object(…).strict()` |
| Import và export | File mới **không** nhập `./index`, **không** được `export *` qua `index.ts`. Nơi dùng nhập thẳng `@/api/schemas/<file>` (tiền lệ `S:api/client.ts:34,47`). Chỉ export kiểu (`z.infer`) cho schema **không** khai `z.ZodType<Miền>`. Không nhập kiểu từ `../client` |
| Ngày giờ | Schema mới dùng `isoInstantSchema = z.string().datetime({ precision: 3 })` (UTC `Z`, 3 chữ số, W3), khai ở `common.ts`. Schema cũ giữ nguyên |
| Version | tài nguyên có `revision: int ≥ 0`; request GV gửi `{baseVersion, body}`; 409 trả `currentVersion` |
| Danh sách mới | query `cursor`, `limit` (mặc định 50); response `CursorPageSchema(item)`. Gateway giải `CursorEnvelopeSchema`, rồi gọi `safeParseList(Item, env.items)`, đọc tới hết `nextCursor` khi màn tính trên toàn danh sách |
| Trần `limit` | 200; N1 tối đa 500; N17 tối đa 200 |
| Refine | chỉ đặt `path`, **không** `message` (`S:api/schemas/index.ts:44-50`) |
| Mã lỗi chung | BE-00 §4 |
| Quyền | không phải thành viên → 404 `resource:"project"`; thiếu quyền → 403; nhóm `admin/ml` → `require_admin` |

### 0.1 Luật id trong schema mới

| Id | Kiểm |
|---|---|
| Tài nguyên `prj_ usr_ upl_ ver_ tpl_ mdl_ dst_ dsv_ job_` | `^<tiền tố>[0-9A-HJKMNP-TV-Z]{26}$` (ULID) |
| `creatorId`, `changedBy` | `usr_` + ULID **hoặc** literal `system:pipeline` |
| Thực thể không gian, mọi `floorId`, `defaultFloorId`, `referenceIds`, `Note.entityId` | `entityId<T>()` của `S:api/schemas/spatial.ts:133-143` (chuỗi không rỗng, không regex; `S:api/__tests__/spatial.test.ts:190-203`) |
| Phép đo | `^MS-\d{4,}$` |

BE vẫn kiểm id thực thể theo W4 (BE-00) khi **ghi** (#35, N19; mô hình B3-01). zod không thêm regex.

Fixture cũ (`S:api/__mocks__/client.ts`: `project-1`, `user-1`…) chỉ đi qua schema cũ nên **không** phải đổi. Chỉ fixture đi qua schema mới mới phải dùng id đúng mẫu; prompt F-xx nào nối schema mới thì tự đổi fixture của mình.

### 0.2 Bảng refine

**A. Kiểm trong zod** (F-00a/b/c viết, có test):

| Schema | Điều kiện | `path` |
|---|---|---|
| `BuildingSchema`, `LevelSchema`, `AxisSchema`, `DimensionSchema`, `NoteSchema` | A5: không có `source:'ai'` + `reviewed:true` | `['reviewed']` |
| `ProjectSummarySchema` | `wallsReviewedCount ≤ wallsTotalCount` | `['wallsReviewedCount']` |
| `ProjectSummarySchema` | `status === 'done'` ⇒ `wallsTotalCount > 0` và `wallsReviewedCount === wallsTotalCount` | `['status']` |
| `ProjectSummarySchema` | `floorCount === 0` ⇔ vắng `defaultFloorId`; `status === 'qc'` ⇒ có `defaultFloorId` | `['defaultFloorId']` |
| `UpdateMeSchema`, `RuleOverrideSchema`, `FloorLayerWriteBodySchema` | ≥ 1 khoá | `[]` |
| `RuleOverrideSchema` | `thresholds` (nếu có) không rỗng | `['thresholds']` |
| `ProjectRuleConfigSchema`, `UpdateRuleConfigSchema` | `overrides.GENERAL` không có `enabled` hay `severity` (dùng `superRefine` trên `overrides`) | `['overrides','GENERAL','enabled' \| 'severity']`; bản gửi có tiền tố `['body', …]` |
| `SpatialGraphDocumentSchema` | mỗi `graph.levels[i].id` có đúng một mục `floorRevisions` và ngược lại | `['floorRevisions']` |
| `FloorLayerDocumentSchema` | có `scaleStatus` ⇒ có `level.scaleMillimetresPerPixel` | `['level','scaleMillimetresPerPixel']` |
| `DimensionSchema` | `kind !== 'elevation'` ⇒ `valueMm > 0` | `['valueMm']` |
| `MeasurementRecordSchema` | `floorArea` ⇒ ≥ 3 điểm; chế độ khác ⇒ ≥ 2 | `['points']` |
| `RemoteFieldChangeSchema` | có khoá `value` thì khác `null` | `['value']` |
| `UploadAvatarSchema` | `contentBase64.length ≤ 699052` (≈ 512 KiB sau giải mã) | `['contentBase64']` |
| F-00b | mọi luật "khi và chỉ khi" của §8; `endedAt ≥ startedAt` (so chuỗi, cùng dạng `.sssZ`) | trường vế phải; `['endedAt']` |

**B. H1 ngữ cảnh** (không đưa vào zod, vì một thực thể lệch sẽ làm hỏng cả lượt giải mã ở gateway FE, `S:api/schemas/spatial.ts:37-49`). Runner B0-07 chạy trên chính các mẫu golden, **kèm tham số đường**:
- N16: `level.id === {floor_id}`; mọi `levelId` trong `layer`, `axes`, `dimensions` bằng `level.id`.
- N15: `levels` sắp theo `order`; mọi `levelId` trỏ tới một `level` có thật.
- N17: `sequence` giảm dần.
- N1: `areaM2` làm tròn 2 chữ số.
- N7: mục sắp theo `Floor.order`.
- N23: đủ 3 họ.
- Mọi schema: chỉ BE kiểm `Level.id === Floor.id`.

---

## 1. Thân dùng chung

### 1.1 `errors.ts` (F-00a)

**`ApiErrorBodySchema`** (strict + transform, mọi status ≥ 400 trừ `VERSION_CONFLICT`):

| trường | zod | bằng chứng |
|---|---|---|
| `code` | `z.string().regex(/^[A-Z][A-Z0-9_]{2,63}$/)` | `S:lib/http/client.ts:136-152,463-480` |
| `requestId` | `z.string().min(1)` | `S:lib/errors/toAppError.ts:71-83` |
| `step?` | `z.enum([...6 id bước])` khai **tại chỗ** (không nhập `S:lib/realtime/pipeline.ts`: tuple không export, và file nhập `vi.json`, có `throw` khi nạp). Test so danh sách với `PIPELINE_STAGES` | `toAppError.ts:35,239-241`; `S:lib/realtime/pipeline.ts:48-55` |
| `floor?` | `z.string().min(1)` | `toAppError.ts:248-250` |
| `count?` | `z.number().int().nonnegative()` | `toAppError.ts:100-115` |
| `field?` | `z.string().regex(/^[A-Za-z][A-Za-z0-9]*(\.[A-Za-z0-9]+)*$/)` | BE-00 §4 |
| `resource?` | `z.enum` khai tại chỗ, không export: `project, floor, member, version, upload, measurement, template, libraryItem, modelFamily, modelVersion, dataset, datasetVersion, trainingJob, notification, user` | BE-00 §4 |
| `fileName?` | `z.string().min(1)` | `toAppError.ts:35` |
| `layer?` | `z.string().min(1)` | `toAppError.ts:35` |

BE chỉ gửi `code`, không gửi bí danh `errorCode`.

**`VersionConflictBodySchema`** (409 của mọi GV). Khai `z.ZodType<ConflictResponseBody, z.ZodTypeDef, unknown>`; transform bỏ `code` và `requestId`:

| trường | kiểu | ràng buộc |
|---|---|---|
| `code` | literal `VERSION_CONFLICT` | |
| `requestId` | string min 1 | |
| `currentVersion` | int ≥ 0 | `S:lib/versioning/conflict.ts:22` |
| `remoteChanges` | `RemoteFieldChange[]` | được rỗng chỉ ở N6, N22, N24; #35 và N19 luôn ≥ 1 (ở hai đường này `baseVersion > revision` → 422 `VALIDATION` `field:"baseVersion"`; F-04b, F-08 coi như "tải lại") |

**`RemoteFieldChangeSchema`** (strict):

| trường | zod | ghi chú |
|---|---|---|
| `entityId` | string min 1 | id thực thể; điểm theo §1.3 |
| `entityType` | `z.enum(VERSION_ENTITY_KINDS)` = `vertex, wall, door, window, furniture, room, dimension` | `S:lib/versioning/mergeStrategies.ts:1` |
| `field` | string min 1 | tên theo §1.3 |
| `value` | `z.unknown()`; refine khác `null` | **vắng khoá trên dây = trường bị gỡ**. Transform **luôn** đặt `value: wire.value`, nên đầu ra có khoá `value` (bằng `undefined` khi vắng). Đây là ngoại lệ duy nhất của luật vắng-vẫn-vắng, vì `FieldChange.value: unknown` là khoá bắt buộc và repo bật `exactOptionalPropertyTypes` (`S:lib/versioning/mergeStrategies.ts:3-13`) |
| `changedAt` | `isoInstantSchema` | |
| `changedBy` | `usr_`+ULID hoặc `system:pipeline` (§0.1) | |
| `changedByName` | string min 1 | server ghép; `system:pipeline` → "hệ thống AI" |

**Rào của C1** (bắt buộc cho F-07, F-10, F-11, có test FE chặn):
- 409 của N6, N22, N24 chỉ xử lý bằng `kind === 'conflict'` (khuôn `S:screens/project/ProjectSettings/useProjectSettings.ts:644-650`), **không** đưa vào `resolveConflict`. Nếu gọi với `remoteChanges: []`, hàm này trả `autoMerged` và nâng base (`S:lib/versioning/conflict.ts:103-110`), tức ghi đè im lặng.
- **N19** (bản 9): 409 và 422 `field:"baseVersion"` → dải "tải lại" như #35. F-08 gỡ lời gọi `resolveConflict` hiện có ở `S:screens/export/VersionHistory/versionHistoryGateway.ts:395`: phục hồi là thao tác trên cả tầng, trộn thay đổi từ xa vào bản phục hồi không có nghĩa. `remoteChanges` (≥ 1) chỉ dùng để nói ai vừa đổi.
- *(bản 10, phân xử 6b)* **Mất phản hồi** của mọi ghi có version (#35, N6, N19, N22, N24): FE giữ `{ baseVersion, body }` và gửi lại **đúng** thân và base đó trước (C09b trả 200 hiện trạng), rồi mới gửi thân mới với `revision` vừa nhận.

### 1.2 `common.ts` (F-00a)

```ts
export const isoInstantSchema                                         // z.string().datetime({ precision: 3 })
export function CursorPageSchema<T extends z.ZodTypeAny>(item: T)     // z.object({ items: z.array(item), nextCursor: z.string().min(1).optional() }).strict() — KHÔNG transform
export const CursorEnvelopeSchema                                     // z.object({ items: z.array(z.unknown()), nextCursor: z.string().min(1).optional() }).strict()
export function VersionedWriteSchema<T extends z.AnyZodObject>(body: T) // z.object({ baseVersion: z.number().int().nonnegative(), body }).strict()
```

Thiếu `baseVersion` → BE trả **428 `PRECONDITION_REQUIRED`** (kiểm trước Pydantic), không trả 422.

### 1.3 Bảng đặt tên dùng chung cho nhật ký thay đổi (B3-03 ghi, F-08 đọc)

| Thực thể miền | `entityType` | `entityId` | `field` (khoá snapshot, snake_case, `S:lib/format/semantic.ts:154-162`) |
|---|---|---|---|
| Wall | `wall` | `W-…` | `thickness_mm`, `height_mm`, `kind`, `paint`, `curve_offset_mm` |
| Hai đầu tường | `vertex` | `V-<wallId>-start` / `V-<wallId>-end` | `x`, `y` |
| Opening `door` / `window` | `door` / `window` | `D-…` | `width_mm`, `height_mm`, `sill_height_mm`, `offset_mm`, `swing`, `wall_id` |
| Room | `room` | `R-…` | `name`, `usage`, `outline` |
| Furniture | `furniture` | `F-…` | `kind`, `centre`, `rotation_deg`, `room_id` |
| Dimension | `dimension` | `M-…` | `value_mm`, `override_value_mm`, `reference_ids` |

Thực thể bị xoá: một mục `field:"__deleted__"` không có `value`. Thực thể mới: một mục cho mỗi trường.

---

## 2. Dự án, thành viên, tải lên (F-00c, trừ ghi chú)

### N1 — `GET /api/project-summaries` → `projectSummaries.ts`

- **Màn:** `S:screens/dashboard/ProjectDashboard/projectsGateway.ts:22-37,99-107`, `S:screens/dashboard/ProjectDashboard/useProjectDashboard.ts:178-193,240-307`, `S:screens/onboarding/WelcomeScreen/useWelcomeScreen.ts:217-266,325`.
- **Quyền:** đã đăng nhập; chỉ dự án mình là thành viên.
- **Thứ tự:** `id` tăng dần (cursor ổn định). F-07 tự sắp theo `updatedAt` sau khi đọc hết.
- **Response:** `ProjectSummaryPageSchema = CursorPageSchema(ProjectSummarySchema)`.

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `id` | string | có | `prj_`+ULID |
| `name` | string | có | trim, 3–80 (`S:domain/project/limits.ts:22-23`) |
| `floorCount` | int ≥ 0 | có | tầng chưa xoá mềm |
| `areaM2` | number ≥ 0 | có | tổng diện tích phòng đo từ `outline`, làm tròn 2 số |
| `status` | `processing \| qc \| done` | có | luật dưới + refine §0.2 |
| `wallsReviewedCount` | int ≥ 0 | có | |
| `wallsTotalCount` | int ≥ 0 | có | |
| `updatedAt` | `isoInstantSchema` | có | |
| `members` | `{id, name}[]` strict | có | `id` là `usr_`+ULID; `name` min 1 |
| `defaultFloorId` | `entityId` | không | tầng `order` nhỏ nhất **còn tường chưa duyệt**, không có thì tầng `order` nhỏ nhất; vắng khi 0 tầng |
| `hasScale` | boolean | không | `false` khi một tầng đã chạy pipeline chưa có tỉ lệ `human`/`pipeline` (cột `floor_documents.scale_source`) (U07) |
| `jobActive` | boolean | không | có tầng với lượt pipeline mới nhất `pending \| running` (U10) |

**Luật suy `status`:**
- `done` khi đủ điều kiện refine.
- Ngược lại, `processing` khi:
  - có lượt pipeline mới nhất `pending | running`; hoặc
  - chưa có tường nào **và** (0 tầng; hoặc có tầng chưa có lượt tải nào; hoặc có lượt mới nhất `failed`).
  Đã có tường thì tầng trống hay lượt hỏng không giữ `processing` (U10).
- Ngược lại, `qc`.

**Nguồn tính (BE):** bảng đếm **theo tầng** `project_floor_summaries` (`floor_order`, `walls_total`, `walls_reviewed`, `area_m2`, `has_upload`, `pipeline_state`, `hidden`), do B2-01 tạo kèm cổng ghi; `floor_count`, `area_m2` dự án, `default_floor_id`, `status` suy lúc đọc bằng một `GROUP BY` (bỏ dòng `hidden`).
- B2-03, B3-03, B5-06a, B5-06b cập nhật bảng **trong giao dịch ghi của mình**.
- B3-02 có hàm đếm lại thuần `layer_counts` và lịch đối chiếu 5 phút; B3-03 gọi hàm đó; B5-07 có test đối chiếu.
- Không đọc jsonb từng tầng lúc gọi N1.

**Không đưa lên dây:** `planVariant` (FE suy từ băm `id`), `initials` (FE dựng từ `name`).

### N2 — v2

`GET /api/projects/{project_id}/members` **để v2**. F-07 đọc thành viên từ `Project.members` (#24, `S:screens/project/ProjectSettings/projectSettingsGateway.ts:208-226`), rồi làm mới `project.detail` sau N3/N4.
Email thành viên lộ cho viewer qua #24 (`UserSchema.email` bắt buộc, `S:api/schemas/index.ts:97`): chấp nhận ở v1, không sửa FE.

### N3, N4 — thành viên → `members.ts`

- **Luật v1:** thêm là thành viên **ngay**; không có trạng thái `invited`.
- Thông báo `projectInvite` chỉ để báo tin; #22 `accept-invite` idempotent, đánh dấu đã đọc.
- Người `pending` (được mời vào hệ thống nhưng chưa nhận lời) **được** thêm, và thấy dự án sau khi nhận lời mời hệ thống.

| # | Đường | Request | Response | Mã lỗi riêng | Quyền |
|---|---|---|---|---|---|
| N3 | `POST /api/projects/{project_id}/members` | `AddProjectMemberSchema {email: .min(1).email()}` strict; nhận `Idempotency-Key` | 201 `UserSchema` (schema cũ); đã là thành viên → **200** trả bản ghi có sẵn. Trong giao dịch gọi `ProjectInviteSink` (chỉ khi thêm mới; B2-02 khai, B4-02 cài; sink tự đăng ký phát sau commit) → thông báo `projectInvite` (`place:"projectSettings"`, `projectId`, `projectName`, `objectLabel` = tên dự án, `message` server ghép) + S2 đúng một lần | `MEMBER_USER_UNAVAILABLE` 422 `field:"email"` (không có tài khoản **hoặc** bị vô hiệu, một mã chung để không lộ trạng thái) | `project.settings.edit` |
| N4 | `DELETE /api/projects/{project_id}/members/{user_id}` | — | 200 `UserSchema` của người vừa gỡ; đóng luồng của họ (S07) | `MEMBER_LAST_EDITOR` 422 (không gỡ người cuối còn `project.settings.edit` theo vai hiện tại) · 404 `resource:"member"` | `project.settings.edit` |

F-07: hộp thoại A9 khi gỡ; ánh xạ `MEMBER_*`.

### N5–N6 — cài đặt dự án → `projectSettings.ts`

- **Màn:** `S:screens/project/ProjectSettings/projectSettingsGateway.ts:59-67,141-202,269-277`, `S:screens/project/ProjectSettings/useProjectSettings.ts:525,636-682`.
- **`ProjectSettingsBodySchema`** (strict, không transform) và **`ProjectSettingsSchema`** = `Body.extend({revision}).strict().transform(…)`:

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `revision` | int ≥ 0 | có (chỉ response) | |
| `buildingType` | `residential \| commercial \| industrial \| mixed \| other` | có | |
| `notes` | string | không | 1–500; vắng = rỗng (F-07 không gửi `''`) |
| `lengthUnit` | `mm \| m` | có | |
| `snapToleranceMm` | int | có | 1–120 (`S:domain/units/snap.ts:65`) |
| `confidenceThreshold` | number | có | 0–1; chỉ để hiển thị, BE **không bao giờ** dùng để đặt `reviewed` |
| `defaultScaleMmPerPx` | number | có | 0,01–1000; mặc định cho tầng mới (W24) |

- **N5 `GET …/settings`:** chưa lưu → `revision: 0` + mặc định FE (`residential`, `mm`, `50`, `0.75`, `1`).
- **N6 `PUT …/settings`** (GV): `UpdateProjectSettingsSchema = VersionedWriteSchema(ProjectSettingsBodySchema)`; 409 với `remoteChanges: []`.
- Tên, mã, địa chỉ vẫn đi #26. F-07 gửi hai request và báo lỗi phần hỏng.

### N7b — `GET /api/projects/{project_id}/pipeline-runs` → `pipelineRuns.ts` *(UIFIX-B7)*

- Lượt xử lý của dự án, mới nhất trước; `limit` + `cursor` (`CursorPage`); thành viên dự án đọc được, người ngoài/dự án không có → 404. Mỗi mục một lượt của một tầng: trạng thái, bước, %, `createdAt`/`startedAt`/`endedAt`/`updatedAt`, mã lỗi. Màn: tab Nhật ký của màn Xử lý (N5-08).
- `GET /api/notifications` nhận thêm `limit`, `cursor` tuỳ chọn; thân vẫn là mảng; còn trang sau thì header `X-Next-Cursor` (G1-15).

### N7 — `GET /api/projects/{project_id}/drawings/uploads/latest` → `uploads.ts`

- **Màn:** `S:screens/pipeline/ProcessingScreen/useProcessingScreen.ts:254-269,550-563`.
- **Response:** `LatestFloorUploadPageSchema = CursorPageSchema(LatestFloorUploadSchema)`.
- Mỗi tầng chưa xoá có ≥ 1 lượt tải → một mục: upload của lượt `pipeline_runs` mới nhất (`created_at DESC, id DESC`); tầng chưa có lượt thì upload mới nhất khác `rejected`. Init bỏ dở không có lượt nên không che upload đã chạy; U2 hỏng rồi #31 chạy lại trên U1 → mục là U1.
- Thứ tự `Floor.order` (H1 ngữ cảnh); ≤ 50 tầng nên vừa một trang.

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `floorId` | `entityId` | có | |
| `floorName` | string | có | min 1 |
| `uploadId` | string | có | `upl_`+ULID |
| `sourceImageUrl` | string | không | `.url()`; là **ảnh trang đã nắn** (§4.2) |

---

## 3. Xác thực và tài khoản (F-00c)

### N8–N10 — `auth.ts` (công khai, có rate limit, 429)

| # | Đường | Request (strict) | Response | Mã lỗi riêng |
|---|---|---|---|---|
| N8 | `POST /api/auth/password-reset` | `PasswordResetRequestSchema {email: .min(1).email()}` | **luôn 204** (C27) | — |
| N9 | `POST /api/auth/password-reset/confirm` | `PasswordResetConfirmSchema {token: 1–512, newPassword: .min(1).min(8)}` | 204; thu hồi mọi phiên và cookie luồng; không đặt cookie | `PASSWORD_RESET_TOKEN_INVALID` 422 |
| N10 | `POST /api/auth/invitations/accept` | `AcceptInvitationSchema {token: 1–512, fullName: trim min 1, password: .min(1).min(8)}` | 204; đặt cookie refresh + cookie luồng; `pending` → `active` | `INVITATION_TOKEN_INVALID` 422 |
| N10b | `POST /api/auth/invitations/preview` *(UIFIX-B2)* | `InvitationPreviewRequestSchema {token: 1–512}` | 200 `{status: "valid", email, role, inviterName?, expiresAt}`; chỉ đọc, không tiêu token | `INVITATION_TOKEN_INVALID` / `INVITATION_TOKEN_EXPIRED` / `INVITATION_TOKEN_USED` 422 |

- **K7 = B:** không có đăng ký công khai; N10 là đường vào duy nhất cho người mới.
- **Link trong thư** (B1-03 dựng từ `PUBLIC_BASE_URL`): lời mời `{PUBLIC_BASE_URL}/login/invitation#token=<token>`; đặt lại `{PUBLIC_BASE_URL}/login/reset-password#token=<token>`.
- **N8:** đã có token đặt lại còn hiệu lực tạo chưa tới 15 phút → vẫn 204, không tạo token, không gửi thư (C28). Mật khẩu (N9, N10, N13) được server chuẩn hoá NFC.
- **F-09a:**
  - tắt tab đăng ký;
  - thêm hai route công khai `/login/invitation` và `/login/reset-password` vào `S:routes/paths.ts`; **không** chuyển hướng khi đang đăng nhập (mất token);
  - đọc token từ `#token=`, rồi xoá fragment bằng `history.replaceState` **trước** mọi `await`;
  - phân loại lỗi theo **`code`**, không chỉ theo status: hiện màn đọc 403 là "tài khoản bị vô hiệu" (`S:screens/auth/AuthScreen/useAuthScreen.ts:219-230`), nên `ORIGIN_MISMATCH` sẽ bị báo sai.

### N11–N14 — `me.ts`

**Màn:** `S:screens/account/AccountSettings/useAccountPreferences.ts:106-110,140-146,283-297,396-420`, `S:screens/account/AccountSettings/ProfileSection.tsx:59,124`, `S:screens/account/AccountSettings/accountAuthGateway.ts:28,62-64,92-95,134,147-171,212-214,244-255`.

**`MeSchema`** (chỉ hồ sơ; giao diện và tuỳ chọn thông báo vẫn giữ trong bộ nhớ ở v1):

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `email` | string | có | email; chỉ đọc |
| `fullName` | string | có | trim, 1–120 |
| `jobTitle` | string | không | 1–120 |
| `phone` | string | không | 1–32 |
| `language` | `vi \| en` | có | |
| `avatarUrl` | string | không | `.url()` |

| # | Đường | Request (strict) | Response | Mã lỗi riêng |
|---|---|---|---|---|
| N11 | `GET /api/me` | — | `MeSchema` | — |
| N12 | `PATCH /api/me` | `UpdateMeSchema`: `fullName?` (trim 1–120), `jobTitle?` (0–120, `''` = xoá), `phone?` (0–32, `''` = xoá), `language?`; ≥ 1 khoá | `MeSchema` | — |
| N13 | `POST /api/me/password` | `ChangePasswordSchema {currentPassword: min 1, newPassword: .min(1).min(8)}` | 204; thu hồi phiên khác | `CURRENT_PASSWORD_INCORRECT` 422 (không 401, W10) |
| N14 | `PUT /api/me/avatar` | `UploadAvatarSchema {mimeType: image/png \| image/jpeg, contentBase64: ≤ 699052 ký tự}` | `MeSchema` | `AVATAR_TYPE_UNSUPPORTED` 422 · `AVATAR_DIMENSIONS_EXCEEDED` 422 (> 4096×4096) |

**F-09b:**
- gỡ `'matkhau123'`;
- bỏ data URL trong bản nháp; ô chọn tệp chỉ nhận PNG/JPEG;
- đặt `supported:false` cho `listSessions`, `revokeSession`, `deleteAccount` (hiện là bộ nhớ giả);
- `isManagedExternally = false`.

Xoá tài khoản và danh sách phiên để v2.

---

## 4. Không gian (F-00a)

### 4.1 Thực thể mới — thêm vào `S:api/schemas/spatial.ts`

Đặt trong `spatial.ts`, vì `entityId`, `reviewMetadataShape`, `humanOnlyReview` không export (`S:api/schemas/spatial.ts:133-184`); **không** export thêm ba mảnh này. Cả 5 schema khai `z.ZodType<Miền, z.ZodTypeDef, unknown>`, **có đủ `confidence`, `source`, `reviewed`** (trộn `reviewMetadataShape`) và refine A5 (`S:domain/spatial/types.ts:95-237`). Sửa khối import đầu file và docblock `S:api/schemas/spatial.ts:96-101`.

| schema | trường ngoài trường duyệt | ràng buộc |
|---|---|---|
| `BuildingSchema` | `name`, `address?`, `datumElevationMm`, `grossFloorAreaM2?` | chuỗi min 1; mm int; m² ≥ 0 |
| `LevelSchema` | `id`, `name`, `order`, `elevationMm`, `heightMm`, `areaM2?`, `scaleMillimetresPerPixel?` | id `entityId`; `order` int; `heightMm` int > 0; tỉ lệ `.positive().finite()`, gán nhãn bằng `millimetresPerPixel()` (`S:domain/units/scale.ts:85-88`) |
| `AxisSchema` | `id`, `levelId`, `label`, `direction`, `line` | `label` min 1; `horizontal \| vertical` |
| `DimensionSchema` | `id`, `levelId`, `kind`, `referenceIds`, `line`, `valueMm`, `overrideValueMm?` | `kind` 5 giá trị; `referenceIds` **được rỗng** (`S:lib/commands/business/wallCommands.ts:1138`); `valueMm`, `overrideValueMm` int; refine `kind !== 'elevation'` ⇒ `valueMm > 0` |
| `NoteSchema` | `id`, `entityId`, `body`, `createdAt`, `authorId` | chuỗi min 1; `createdAt` `isoInstantSchema` |

**Luật giá trị trường duyệt** (BE ghi, H1 không đoán được):

| Thực thể | `source` | `confidence` | `reviewed` |
|---|---|---|---|
| `Building` | `human` | 1 | có ≥ 1 tầng và mọi tầng `reviewed` |
| `Level` | `human` | 1 | có ≥ 1 tường **và** mọi mục `walls`, `openings`, `rooms`, `furniture` `reviewed:true` (v1 không tính `Axis`, `Dimension`) **và** không có `scaleStatus` (`scale_source ∈ {human, none}`) |
| `Axis`, `Dimension` do pipeline sinh | `ai` | theo mô hình | `false` |
| `Note` | `human` | 1 | `true` |

- v1 **không** sinh `Dimension` loại `angular`.
- B3-03 gỡ id thực thể đã xoá khỏi `referenceIds` trong cùng giao dịch.
- Bộ mẫu A14 (`S:domain/spatial/__fixtures__/sampleBuilding.ts:200`) có `createdAt` dạng `+07:00`, nên **hỏng** `isoInstantSchema`. Test không giải mã thẳng bộ A14; seed của B3-02 đổi sang `Z` 3 chữ số.

### 4.2 Tỉ lệ và "pixel"

- **"Pixel"** là pixel của **trang đã nắn** (đầu ra B2-05a). `Drawing.url` (tầng), `sourceImageUrl` (N7) và ảnh của quality đều là **cùng ảnh đó**.
- **Thứ tự ưu tiên tỉ lệ của tầng:** người hiệu chỉnh (#35 có `scaleMillimetresPerPixel`) > pipeline suy được > `defaultScaleMmPerPx` của dự án. Hai nguồn sau là **tạm** và kèm `scaleStatus: 'unresolved'` ở N16.
- `SCALE_UNRESOLVED` chỉ là mã nội bộ của pipeline; trên dây chỉ có `scaleStatus`.
- **F-04a:** giữ `scaleStatus` trong store theo tầng. ScaleCalibration coi tỉ lệ của tầng `unresolved` là `null` (`S:screens/pipeline/ScaleCalibration/useScaleCalibration.ts:515-523`), tức là chưa hiệu chỉnh. Dải cảnh báo đọc `scaleStatus`.

### N15 — `GET /api/projects/{project_id}/spatial` → `spatialGraph.ts`

- **Màn:** `S:screens/export/SpatialJsonViewer/SpatialJsonViewer.container.tsx:134-155`, `S:screens/viewer/ExplodedView/explodedViewGateway.ts:175-291`, kho `S:store/spatialSlice.ts:19-22`.
- **`SpatialGraphDocumentSchema`:**

| trường | kiểu | ràng buộc |
|---|---|---|
| `graph` | `SpatialGraphSchema {building, levels, walls, openings, furniture, rooms, axes, dimensions, notes}` | v1 `axes: []`, `notes: []` |
| `floorRevisions` | `FloorRevisionSchema[]` = `{floorId: entityId, revision: int ≥ 0}` strict | refine 1–1 với `levels` |

**F-04a:** `versionId` của store = băm tất định các cặp `floorId:revision` đã sắp. F-04b cập nhật lại sau mỗi PUT.

### N16 — `GET /api/projects/{project_id}/floors/{floor_id}/spatial/layer` → `spatialLayer.ts`

- **Màn:**
  - `S:screens/qc/ObjectLayerReview/objectLayerReviewGateway.ts:1948-1953`;
  - `S:screens/qc/WallLayerReview/wallLayerReviewGateway.ts:347-357,803`;
  - `S:screens/qc/DimensionOcrReview/dimensionOcrReviewGateway.ts:505,994-996`;
  - `S:screens/qc/RoomLabelReview/roomLabelReviewGateway.ts:631`;
  - `S:screens/qc/AxisGridManager/axisGridManagerGateway.ts:405`;
  - `S:screens/pipeline/ScaleCalibration/useScaleCalibration.ts:522`.
- **Bọc** `SpatialLayerSchema`, không `.extend()` (`S:api/schemas/spatial.ts:306`).
- **`FloorLayerDocumentSchema`:**

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `revision` | int ≥ 0 | có | |
| `level` | `LevelSchema` | có | H1 ngữ cảnh: `id === {floor_id}` |
| `scaleStatus` | literal `unresolved` | không | refine: có ⇒ có tỉ lệ |
| `layer` | `SpatialLayerSchema` | có | H1 ngữ cảnh: mọi `levelId` bằng `level.id` |
| `axes` | `AxisSchema[]` | có | trục đã lưu của tầng *(UIFIX-B4; trước là luôn `[]`)* |
| `origin` | `{x, y}` mm nguyên | không | `null`/vắng khi chưa đặt gốc *(UIFIX-B4)* |
| `dimensions` | `DimensionSchema[]` | có | ghi qua #35 `dimensions?` (bản 11) |

### Ghi lớp — `PUT /api/projects/{project_id}/floors/{floor_id}/spatial/layer` (#35) → `spatialLayer.ts`

| schema | hình dạng |
|---|---|
| `FloorLayerWriteBodySchema` | `{layer?: SpatialLayerSchema, scaleMillimetresPerPixel?: number > 0 hữu hạn, dimensions?: DimensionSchema[], axes?: AxisSchema[], origin?: {x, y}}` strict, refine ≥ 1 khoá *(axes/origin: UIFIX-B4 — `axes` thay trọn danh sách, cùng luật `REVIEW_BY_AI_FORBIDDEN`/`LAYER_LEVEL_MISMATCH`/id trùng như `dimensions`)* |
| `FloorLayerWriteSchema` | `VersionedWriteSchema(FloorLayerWriteBodySchema)` |
| `FloorLayerWriteResultSchema` | `{revision: int ≥ 0, layer: SpatialLayerSchema}` strict |

- **Có `scaleMillimetresPerPixel`:** B3-03 ghi tỉ lệ tầng, gỡ `scaleStatus`, và tính lại mm cho **mọi thực thể chưa `reviewed`** (của AI lẫn do người vẽ) theo tỉ số tỉ lệ mới/cũ, gồm `line` của `Dimension` chưa duyệt (v1 `axes` luôn rỗng). Mục đã duyệt giữ nguyên. Thân có cả `layer` lẫn tỉ lệ → `layer` hiểu ở hệ tỉ lệ **cũ**, server tính lại. **Tỉ lệ gắn với trang:** server gắn tỉ lệ với trang của lớp đang có (trang lượt pipeline ghi gần nhất; chưa có thì bản vẽ hiện tại); kết quả AI của trang khác không nhận tỉ lệ cũ (hạng coi như chưa có, N16 hiện `scaleStatus: "unresolved"`).
- *(bản 11)* **Có `dimensions`:** thay **trọn** danh sách kích thước của tầng (`[]` xoá hết; vắng = không đụng). Mọi `levelId` bằng tầng (`LAYER_LEVEL_MISMATCH`); `source: "ai"` kèm `reviewed: true` → `REVIEW_BY_AI_FORBIDDEN` (`field: body.dimensions.<i>.reviewed`); id trùng trong thân → 422 `VALIDATION`. Nhật ký §1.3 ghi theo id `M-…`: kích thước mới (mỗi trường một mục), xoá (`__deleted__`), đổi `value_mm` / `override_value_mm` / `reference_ids`; chỉ đổi `reviewed` hay `confidence` thì ghi dấu chạm như thực thể của lớp. Nhờ vậy hai người cùng sửa kích thước với `baseVersion` cũ nhận 409 `VERSION_CONFLICT`, không ghi đè im lặng. Thân có cả tỉ lệ: `dimensions` hiểu ở hệ tỉ lệ **của thân**; tỉ lệ đó không được nhận (nguồn hạng thấp hơn tỉ lệ đang có) thì server quy đổi chúng về tỉ lệ của tầng (`writer.py:472-478`).
- **`Room.areaM2` do server tính** từ `outline` (W18), kể cả phòng đã duyệt. Thân tối đa 8 MiB (quá → 413).
- **Response `layer` là nguồn thay store** (F-04b).
- **"Áp cho mọi tầng"** (`S:screens/pipeline/ScaleCalibration/scaleCalibrationGateway.ts:155-161`): F-04c đọc `floorRevisions` từ N15, rồi gửi mỗi tầng một PUT **chỉ mang tỉ lệ**, với `baseVersion` riêng của tầng đó. Tầng đang có sửa chưa lưu gửi **một** PUT lớp + tỉ lệ với `baseVersion` của chính lớp đó (ống F-04b), **không** nâng theo N15; tầng đang bị chặn (409 chưa tải lại, 413, 422) không gửi và được nêu tên (kiểm áp 6b, KL-1). Trước khi gửi có hộp thoại A9: tỉ lệ nguồn `human` làm `has_human_geometry` đúng, nên các tầng đó không nắn hay cắt lại bản vẽ được nữa (KL-8).
- **Mã lỗi:** `LAYER_LEVEL_MISMATCH` 422 · `REVIEW_BY_AI_FORBIDDEN` 422 · `LAYER_INTEGRITY_BROKEN` 422.

---

## 5. Phiên bản theo tầng → `versions.ts` (F-00a)

**`FloorVersionSummarySchema`.** Màn: `S:lib/versioning/restore.ts:4-20`, `S:screens/export/VersionHistory/types.ts:132-153`, `S:screens/export/VersionHistory/versionHistoryModel.ts:184-202`.

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `id` | string | có | `ver_`+ULID |
| `sequence` | int > 0 | có | tăng theo tầng |
| `floorRevision` | int ≥ 0 | có | `revision` của tài liệu tầng **tại lúc chụp** |
| `createdAt` | `isoInstantSchema` | có | |
| `creatorId` | string | có | `usr_`+ULID hoặc `system:pipeline` |
| `creatorName` | string | có | min 1; server ghép; `system:pipeline` → "hệ thống AI" |
| `note` | string | không | min 1 |
| `label` | string | không | 1–60 |
| `hasSnapshot` | boolean | có | `false` khi nằm ngoài 50 bản còn giữ nội dung |

| # | Đường | Request | Response | Mã lỗi riêng | Quyền |
|---|---|---|---|---|---|
| N17 | `GET /api/projects/{project_id}/versions?floorId=` | `floorId` bắt buộc, `cursor`, `limit` | `FloorVersionPageSchema` (H1 ngữ cảnh: `sequence` giảm dần) | 422 `field:"floorId"`; 404 `resource:"floor"` | thành viên |
| N18 | `GET /api/projects/{project_id}/versions/{version_id}/snapshot?floorId=` | `floorId` | `FloorVersionSnapshotSchema {versionId, layer: SpatialLayerSchema, dimensions: DimensionSchema[]}` | `VERSION_SNAPSHOT_PURGED` 422 · `VERSION_FLOOR_MISMATCH` 422 | thành viên |
| N19 | `POST /api/projects/{project_id}/versions/{version_id}/restore` (GV) | `RestoreVersionSchema = VersionedWriteSchema(z.object({floorId}).strict())`; `baseVersion` = `revision` hiện tại của tầng | 201 `FloorVersionSummarySchema` của phiên bản mới, có `floorRevision` mới | `VERSION_SNAPSHOT_PURGED` 422 · `VERSION_FLOOR_MISMATCH` 422 · 409 (`remoteChanges` ≥ 1) | `layer.edit` |
| N20 | `PATCH /api/projects/{project_id}/versions/{version_id}/label` | `LabelVersionSchema {label: trim 0–60}`; `''` = gỡ | `FloorVersionSummarySchema` | — | `layer.edit` |

**N19 khôi phục** `layer` và `dimensions` của ảnh chụp, tính lại theo tỉ lệ hiện tại của tầng; `level`, `axes`, tỉ lệ giữ hiện trạng. *(bản 10)* Ảnh chụp trùng hiện trạng (tài liệu không đổi) → 201 trả bản mới nhất: bản sẵn có, hoặc bản "trước" vừa sinh khi tầng đã đổi (tự lưu) kể từ bản mới nhất. FE nhận ra bằng `floorRevision` của phản hồi **bằng** `baseVersion` đã gửi (ghi không đổi gì thì không tăng `revision`; phục hồi thật trả `floorRevision` mới), không đề nghị hoàn tác (kiểm áp 6c, KL-7). Ảnh chụp có lược đồ khác `DOCUMENT_SCHEMA_VERSION` → N18, N19 trả 422 `VERSION_SNAPSHOT_PURGED`.

**Khi nào sinh phiên bản:**
- **trước** lượt ghi của pipeline (`note` = "trạng thái trước khi ghi kết quả AI"), và sau khi pipeline ghi xong;
- **trước** khi khôi phục, nếu `revision` hiện tại khác `floorRevision` của bản mới nhất;
- sau khi khôi phục.

Gắn nhãn **không** sinh phiên bản. `note` của bản khôi phục do server ghép.

**F-08:**
- `baseVersion` = `revision`;
- `isCurrent ⇔ floorRevision === revision hiện tại` (thay cho `index === 0`);
- cập nhật `revision` trong store sau N19 và sau hoàn tác;
- thêm `label`;
- bộ đổi snapshot theo §1.3;
- đọc N18 dần cho hàng đang hiện;
- hoàn tác = restore ngược;
- hiện `creatorName` và `changedByName`.

---

## 6. Cấu hình luật → `ruleConfig.ts` (F-00c)

**Màn:** `S:screens/rules/RuleSettings/ruleSettingsGateway.ts:139-185`, `S:screens/rules/RuleSettings/useRuleSettings.ts:343,371`, `S:domain/rules/config.ts:147-261`, `S:screens/rules/RuleReport/useRuleReport.ts:144-145`.

- **`RuleOverrideSchema`** (strict; refine ≥ 1 khoá; `thresholds` không rỗng):
  - `enabled?: boolean`;
  - `severity?: critical|warning|suggestion`;
  - `thresholds?: record<khoá ^[a-zA-Z]+(\.[a-zA-Z0-9]+)+$, number hữu hạn>`.
- **`ProjectRuleConfigSchema`** = `{revision: int ≥ 0, overrides: record<mã ^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$, RuleOverrideSchema>}`, với `superRefine` cấm `enabled`/`severity` ở `GENERAL`.
- Tập đúng 25 mã + `GENERAL` và 26 khoá ngưỡng **không** kiểm trong zod; H4 kiểm.

| # | Đường | Request | Response | Mã lỗi riêng | Quyền |
|---|---|---|---|---|---|
| N21 | `GET /api/projects/{project_id}/rule-config` | — | `ProjectRuleConfigSchema`; chưa sửa → `{revision: 0, overrides: {}}` | — | thành viên |
| N22 | `PUT /api/projects/{project_id}/rule-config` (GV) | `UpdateRuleConfigSchema = VersionedWriteSchema(z.object({overrides}).strict())`, cùng `superRefine` | `ProjectRuleConfigSchema` | `RULE_CODE_UNKNOWN` · `RULE_THRESHOLD_UNKNOWN` · `RULE_THRESHOLD_OUT_OF_RANGE` · `RULE_GENERAL_NOT_TOGGLEABLE` (đều 422) · 409 `[]` | `ruleset.edit` |

- `RuleConfig.version` là bộ đếm **phía client** (`S:domain/rules/config.ts:247-254`), **không** phải `baseVersion`.
- **F-10:**
  - `canEdit` theo `ruleset.edit`;
  - 409 xử lý bằng `kind` (rào C1);
  - **nạp N21 vào `ruleConfigSlice` và truyền cấu hình vào `runRules`** của RuleReport. Hiện RuleReport chạy luật không có cấu hình, nên cấu hình đã lưu không có tác dụng: lưu mà không tác dụng là lưu giả (K22).

---

## 7. Hợp đồng cũ chưa có schema (F-00a)

### 7.1 `measurements.ts` — #16–#18

**Màn:** `S:screens/viewer/MeasurementTool/measurementToolGateway.ts:17-23,438-497,540-554`, `S:lib/mutations/measurement.ts:55-100,162-176`, `S:types/measurement.ts:12-32`, `S:domain/measure/measure.ts:61-99,437-533`.

**`MeasurementRecordSchema`** (hai chiều: strict + transform):

| trường | kiểu | bắt buộc | ràng buộc |
|---|---|---|---|
| `id` | string | có | `^MS-\d{4,}$`, **client sinh**; BE chỉ nhận `^MS-[0-9]{4,15}$` (vượt → 422 `field:"id"`; F-02 bỏ id vượt `Number.MAX_SAFE_INTEGER`) |
| `name` | string | có | min 1 |
| `mode` | `pointToPoint \| perpendicular \| height \| floorArea` | có | |
| `points` | `{x, y, z?}[]` strict | có | số **thực** hữu hạn; refine số điểm |
| `rawValueMm` | number | có | hữu hạn ≥ 0; với `floorArea` là **mm²** |

- **GET:** mảng trọn (danh sách cũ), trần 1000, thứ tự `id`.
- **POST** theo (`projectId`, `id`):
  - chưa có → tạo, 201;
  - có và cùng thân → 200;
  - có và khác thân → 409 `MEASUREMENT_ID_TAKEN`.
- **DELETE:** **xoá cứng** (ngoại lệ của luật xoá mềm, BE-00 §6), trả 204. Hoàn tác xoá gửi lại cùng thân → tạo mới, 201.
- **Miễn trừ W3/W4 có ghi:** id do client sinh; toạ độ là số thực.
- **FE (F-02):**
  - gặp 409 `MEASUREMENT_ID_TAKEN` thì đọc lại danh sách, sinh id mới, gửi lại **một** lần;
  - sửa chú thích "không tái dùng id" (`S:screens/viewer/MeasurementTool/measurementToolGateway.ts:438-441`, `S:domain/measure/measure.ts:529-533`): id **được** tái dùng sau khi xoá id lớn nhất;
  - *(bản 10)* FE **không** tái dùng id đã xoá **trong phiên** (gateway nhớ trong closure), để lượt "xoá → ghim → hoàn tác" không tự gây 409;
  - *(bản 10)* vé hoàn tác xoá gặp 409 `MEASUREMENT_ID_TAKEN` (thẻ khác vừa lấy id) → đọc lại danh sách, id + tên mới, gửi lại **một** lần; hỏng thì báo "chưa hoàn tác được", không nuốt lỗi (A8).

### 7.2 `propertyTemplates.ts` — #28–#29

**Màn:** `S:api/client.ts:341-401,857-874`, `S:screens/viewer/PropertyInspector/propertyInspectorGateway.ts:617-654,803-819`.

**`PropertyTemplateSchema`** = `z.discriminatedUnion('objectKind', [...4 nhánh ZodObject strict])`, transform đặt **sau** union. Mỗi nhánh có `id` (`tpl_`+ULID), `name` (1–120), `projectId` (`prj_`+ULID), `createdAt` (`isoInstantSchema`), `scope: 'project'`, `objectKind` literal, và `fields` strict (mọi khoá tuỳ chọn):

| `objectKind` | `fields` |
|---|---|
| `wall` | `{heightMm: int > 0, kind: 3 giá trị, thicknessMm: int > 0}` |
| `opening` | `{heightMm, widthMm: int > 0, sillHeightMm: int ≥ 0, swing: 5 giá trị}` |
| `room` | `{usage: 8 giá trị}` |
| `furniture` | `{kind: 8 giá trị, rotationDeg: [0,360)}` |

- **`PropertyTemplateDraftSchema`** (gửi đi): mỗi nhánh dựng bằng `branch.pick({objectKind, name, fields})`, bỏ transform.
- **GET:** mảng trọn, trần 500.
- **POST:** 201. Quyền `layer.edit`.

### 7.3 `featureFlags.ts` — #9

**`FeatureFlagsSchema`:** object strict, 6 khoá boolean tuỳ chọn:
- `scene.instanced-walls`
- `scene.soft-shadows`
- `rules.parallel-run`
- `export.pdf-vector`
- `qc.live-collaboration`
- `scene.pascal-viewer`

Nguồn: `S:lib/telemetry/flags.ts:86-93`. Schema này **chỉ H1 và test dùng**; client cố ý không giải mã (`flags.ts:60-72`).

### 7.4 Bảy mã chất lượng ảnh — thêm vào `quality.ts`

- Thêm `IMAGE_QUALITY_FINDING_CODES = ['RESOLUTION_TOO_LOW','SKEW_DETECTED','FRAME_NOT_FOUND','LOW_CONTRAST','HIGH_NOISE','SKEW_NOT_MEASURED','PERSPECTIVE_DETECTED'] as const` (hai mã cuối: UIFIX-B6 — không đủ đoạn để đo nghiêng; méo phối cảnh/hình thang) và `ImageQualityFindingCodeSchema = z.enum(IMAGE_QUALITY_FINDING_CODES)`.
- Nguồn: `S:screens/upload/InputQualityGate/useInputQualityGate.ts:275-351`.
- **Không** siết `ImageQualityFindingSchema.code`.

---

## 8. Quản trị ML → `adminMl.ts` (F-00b)

FE chưa có màn. Mọi đường qua `require_admin`; mỗi lượt ghi vào `activity_log`.

**Hằng export:**

| hằng | giá trị |
|---|---|
| `ML_MODEL_FAMILIES` | `wallSegmentation`, `openingAndFurnitureDetection`, `dimensionReading`; khai **tại chỗ**, có test so với `S:lib/realtime/pipeline.ts:50-52` |
| `TRAINABLE_MODEL_FAMILIES` | 2 họ đầu |
| `TRAINING_BASE_MODELS` | `wallSegmentation: mitB0, mitB1` · `openingAndFurnitureDetection: yolov8n, yolov8s` |
| `MODEL_WEIGHTS_FORMATS` | `safetensors`, `onnx` |
| `MODEL_EVALUATION_STATUSES` | `pending`, `running`, `completed`, `failed` |
| `DATASET_VERSION_STATUSES` | `building`, `ready`, `failed` |
| `DATASET_VERSION_SOURCES` | `approvedFloors`, `cubicasa5k` |
| `TRAINING_JOB_STATUSES` | `queued`, `running`, `succeeded`, `failed`, `cancelling`, `cancelled` |

**Schema:**

| schema | trường | ràng buộc |
|---|---|---|
| `ModelMetricsSchema` | `iou?`, `map50?`, `cer?` | `iou`, `map50` ∈ [0,1]; `cer` ≥ 0 |
| `ModelFamilySchema` | `family`, `revision`, `activeVersionId?` | `revision` int ≥ 0; `mdl_`+ULID |
| `ModelVersionSchema` | `id`, `family`, `label`, `weightsFormat`, `checksumSha256`, `trainingJobId?`, `datasetVersionId?`, `evaluationStatus`, `metrics?`, `createdAt`, `creatorId` | `label` 1–80; checksum `^[0-9a-f]{64}$`; `metrics` ⇔ `completed`, và chứa **đúng** khoá của họ (`iou` / `map50` / `cer`); `trainingJobId` ⇔ `datasetVersionId` |
| `DatasetSchema` | `id`, `name`, `family`, `createdAt` | `dst_`+ULID; `name` 1–80; `family` ∈ **3 họ** |
| `DatasetVersionSchema` | `id`, `datasetId`, `sequence`, `status`, `source`, `manifestSha256?`, `splitCounts?`, `failureCode?`, `createdAt` | `dsv_`+ULID; `sequence` int > 0; `splitCounts {train, validation, test}` (int ≥ 0) và `manifestSha256` ⇔ `ready`; `failureCode` (`^[A-Z][A-Z0-9_]{2,63}$`) ⇔ `failed` |
| `TrainingJobSchema` | `id`, `family`, `datasetVersionId`, `baseModel`, `epochs`, `status`, `currentEpoch?`, `resultModelVersionId?`, `failureCode?`, `createdAt`, `startedAt?`, `endedAt?`, `creatorId` | `job_`+ULID; `family` là `z.enum(TRAINABLE_MODEL_FAMILIES)`; `baseModel` thuộc họ; `epochs` 1–300; `currentEpoch` int 0…`epochs`; `resultModelVersionId` ⇔ `succeeded`; `failureCode` ⇔ `failed`; `queued` ⇒ vắng `startedAt`; `running \| succeeded` ⇒ có `startedAt`; `endedAt` ⇔ `succeeded \| failed \| cancelled`; `endedAt ≥ startedAt` khi có cả hai |
| `TrainingMetricPointSchema` | `step`, `epoch`, `recordedAt`, `split`, `loss?`, `iou?`, `map50?` | `step` int ≥ 0; `epoch` int ≥ 1; `split` = `train \| validation`; ≥ 1 số đo; `loss` ≥ 0 hữu hạn |
| `TrainingLogLineSchema` | `seq`, `at`, `level`, `message` | `seq` int ≥ 0; `level` = `info \| warning \| error`; `message` 1–2000 |

**Bản gốc của nhà cung cấp:**
- B6-01 **seed sẵn** một `ModelVersion` cho `openingAndFurnitureDetection` và `dimensionReading`: `label: "gốc"`, `weightsFormat: onnx` (trọng số ghim, chuyển sang ONNX lúc build), `evaluationStatus: pending`, kích hoạt sẵn; worker `ml` đánh giá trên **tập kiểm tổng hợp** cố định (B5-01, B6-04b) ngay khi lên → `completed` + `metrics` (số trên tập tổng hợp; v2 đo thêm split `test` của dataset ghim).
- `wallSegmentation` **không** có bản gốc: pipeline dùng đường lùi cổ điển tới khi kích hoạt bản huấn luyện; "quay về" = N24 với `versionId: null` (chỉ họ này).
- N24 chỉ kích hoạt bản `onnx` (`safetensors` → `MODEL_FORMAT_UNSUPPORTED`).
- Kích hoạt lại bản gốc là cách **quay về**; không cần trường mới.

**Log:**
- `message` chỉ dựng từ mẫu câu có tham số (B6-03a); không chèn văn bản ngoại lệ thô.
- Che URL có query, đường dẫn tuyệt đối và biến môi trường.

| # | Đường | Request | Response | Mã lỗi riêng | FE |
|---|---|---|---|---|---|
| N23 | `GET /api/admin/ml/model-families` | — | `ModelFamilyPageSchema` (H1 ngữ cảnh: đủ 3) | — | F-11 |
| N24 | `PUT /api/admin/ml/model-families/{family}/active` (GV) | `SetActiveModelVersionSchema = VersionedWriteSchema(z.object({versionId: string \| null}).strict())`; `null` chỉ hợp lệ khi `{family}` = `wallSegmentation` (khác → 422 `MODEL_VERSION_FAMILY_MISMATCH`) | `ModelFamilySchema` | `MODEL_VERSION_FAMILY_MISMATCH` 422 · `MODEL_VERSION_NOT_EVALUATED` 422 · 409 `[]` | F-11, hộp thoại A9 |
| N25 | `GET /api/admin/ml/model-versions?family=` | query | `ModelVersionPageSchema`, mới nhất trước | — | F-11 |
| N26 | `POST /api/admin/ml/model-versions` | `multipart/form-data`: phần `metadata` = `CreateModelVersionMetadataSchema {family, label, weightsFormat, checksumSha256}`; phần `weights` stream ra storage tạm, băm SHA-256 trong lúc đọc; trần riêng 512 MiB | 201 `ModelVersionSchema` | `MODEL_CHECKSUM_MISMATCH` 422 · `MODEL_FORMAT_UNSUPPORTED` 422 | — (CLI/API) |
| N27 | `GET /api/admin/ml/model-versions/{model_version_id}` | — | `ModelVersionSchema` | — | F-11 |
| N28 | `GET /api/admin/ml/datasets?family=` | query | `DatasetPageSchema` | — | F-12 |
| N29 | `POST /api/admin/ml/datasets` | `CreateDatasetSchema {name, family}` | 201 `DatasetSchema` | `DATASET_NAME_TAKEN` 409 | — (CLI) |
| N30 | `GET /api/admin/ml/datasets/{dataset_id}/versions` | query | `DatasetVersionPageSchema`, `sequence` giảm dần | — | F-12 tab chỉ xem |
| N31 | `POST /api/admin/ml/datasets/{dataset_id}/versions` | `BuildDatasetVersionSchema {projectIds?: prj_[] ≥ 1}` | 202 `DatasetVersionSchema` (`building`) | `DATASET_BUILD_IN_PROGRESS` 409 | — |
| N32 | `GET /api/admin/ml/training-jobs?family=&status=` | query | `TrainingJobPageSchema` | — | F-12 |
| N33 | `POST /api/admin/ml/training-jobs` | `CreateTrainingJobSchema {family, datasetVersionId, baseModel, epochs}` | 202 `TrainingJobSchema` (`queued`) | `DATASET_VERSION_NOT_READY` 422 · `DATASET_FAMILY_MISMATCH` 422 · `TRAINING_BASE_MODEL_MISMATCH` 422 | F-12 |
| N34 | `GET /api/admin/ml/training-jobs/{job_id}` | — | `TrainingJobSchema` | — | F-12 |
| N35 | `POST /api/admin/ml/training-jobs/{job_id}/cancel` | `{}` | `TrainingJobSchema` (`cancelling \| cancelled`) | `TRAINING_JOB_NOT_CANCELLABLE` 409 | F-12, hộp thoại A9 |
| N36 | `GET /api/admin/ml/training-jobs/{job_id}/metrics?since=` | `since` = bước (loại trừ), `limit` | `TrainingMetricPageSchema`, `step` tăng dần | — | F-12 polling |
| N37 | `GET /api/admin/ml/training-jobs/{job_id}/logs?since=` | `since` = `seq` | `TrainingLogPageSchema` | — | F-12 |

- **N36, N37:** `nextCursor` **luôn có** khi job chưa kết thúc (bằng `since` cho lượt kế tiếp); job chưa kết thúc, **hoặc** đã kết thúc chưa quá cửa sổ muộn mà bước lớn nhất chưa đủ `train` và `validation`, thì N36 chỉ trả bước nhỏ hơn bước lớn nhất đã ghi. Chỉ vắng khi job đã kết thúc quá cửa sổ muộn 600 s **và** đã đọc hết. F-12 không dừng polling chỉ vì trang rỗng.
- **Bảy trạng thái F-11/F-12:** `loading` · `empty` (`items: []`) · `error` · `partial` (có bản `pending|running` hoặc job `queued|running|cancelling`) · `forbidden` (403) · `collapsed` (< 1024 px) · `success`.

---

## 9. File và export (chỉ export giá trị; không `export *` qua `index.ts`)

**F-00a (W00):**
```
common.ts            isoInstantSchema · CursorPageSchema · CursorEnvelopeSchema · VersionedWriteSchema
errors.ts            ApiErrorBodySchema · RemoteFieldChangeSchema · VersionConflictBodySchema · VERSION_ENTITY_KINDS
spatial.ts (thêm)    BuildingSchema · LevelSchema · AxisSchema · DimensionSchema · NoteSchema
spatialGraph.ts      SpatialGraphSchema · FloorRevisionSchema · SpatialGraphDocumentSchema
spatialLayer.ts      FloorLayerDocumentSchema · FloorLayerWriteBodySchema · FloorLayerWriteSchema · FloorLayerWriteResultSchema
versions.ts          FloorVersionSummarySchema · FloorVersionPageSchema · FloorVersionSnapshotSchema · RestoreVersionSchema · LabelVersionSchema
measurements.ts      MEASUREMENT_RECORD_MODES · MeasurementPointSchema · MeasurementRecordSchema
propertyTemplates.ts PROPERTY_TEMPLATE_OBJECT_KINDS · PropertyTemplateSchema · PropertyTemplateDraftSchema
featureFlags.ts      FeatureFlagsSchema
quality.ts (thêm)    IMAGE_QUALITY_FINDING_CODES · ImageQualityFindingCodeSchema
```

**F-00b (W01):**
```
adminMl.ts  ML_MODEL_FAMILIES · TRAINABLE_MODEL_FAMILIES · TRAINING_BASE_MODELS · MODEL_WEIGHTS_FORMATS ·
            MODEL_EVALUATION_STATUSES · DATASET_VERSION_STATUSES · DATASET_VERSION_SOURCES · TRAINING_JOB_STATUSES ·
            ModelMetricsSchema · ModelFamilySchema · ModelFamilyPageSchema · SetActiveModelVersionSchema ·
            ModelVersionSchema · ModelVersionPageSchema · CreateModelVersionMetadataSchema ·
            DatasetSchema · DatasetPageSchema · CreateDatasetSchema ·
            DatasetVersionSchema · DatasetVersionPageSchema · BuildDatasetVersionSchema ·
            TrainingJobSchema · TrainingJobPageSchema · CreateTrainingJobSchema ·
            TrainingMetricPointSchema · TrainingMetricPageSchema · TrainingLogLineSchema · TrainingLogPageSchema
```

**F-00c (W01):**
```
projectSummaries.ts PROJECT_SUMMARY_STATUSES · ProjectSummaryMemberSchema · ProjectSummarySchema · ProjectSummaryPageSchema
members.ts          AddProjectMemberSchema
projectSettings.ts  PROJECT_BUILDING_TYPES · PROJECT_LENGTH_UNITS · ProjectSettingsBodySchema · ProjectSettingsSchema · UpdateProjectSettingsSchema
uploads.ts          LatestFloorUploadSchema · LatestFloorUploadPageSchema
auth.ts             PasswordResetRequestSchema · PasswordResetConfirmSchema · AcceptInvitationSchema
me.ts               ACCOUNT_LANGUAGES · AVATAR_MIME_TYPES · MeSchema · UpdateMeSchema · ChangePasswordSchema · UploadAvatarSchema
ruleConfig.ts       RULE_OVERRIDE_SEVERITIES · RuleOverrideSchema · ProjectRuleConfigSchema · UpdateRuleConfigSchema
```

---

## 10. Quyết định đã chốt

| Mã | Câu hỏi | Chốt | Vì sao |
|---|---|---|---|
| K7 | Đăng ký công khai? | **Không** (người dùng chọn) | đóng lỗ chiếm email |
| M1 | Membership có `invited`? | Không; thêm là thành viên ngay; accept-invite idempotent | FE không biểu diễn được `invited` |
| M2 | Quyền thêm/gỡ thành viên | `project.settings.edit`; kỹ sư được gỡ admin, chỉ chặn người sửa cuối (`MEMBER_LAST_EDITOR`) | vai v1 là vai hệ thống; gỡ khỏi dự án không hạ vai |
| M3 | Lộ trạng thái tài khoản ở N3 | một mã `MEMBER_USER_UNAVAILABLE`; người `pending` được thêm | bớt lộ thông tin |
| N2 | Danh sách thành viên riêng | **v2**; dùng #24 | trùng #24; không màn nào cần email riêng |
| C1 | 409 không phải không gian | một schema, `remoteChanges` được rỗng ở N6/N22/N24, **kèm rào** §1.1 | giữ một hợp đồng xung đột |
| C2 | Màn tính trên toàn danh sách | gateway đọc tới hết `nextCursor` | giữ W22 |
| C3 | Độ chính xác ngày giờ | `isoInstantSchema` (3 chữ số, `Z`) | H1 bắt lệch W3 |
| C4 | Chỗ đặt hàm phân trang/GV | `common.ts` | |
| EXP | `export *` qua `index.ts` | **không** | chunk dùng chung đang 264,8/280 KiB; tiền lệ nhập thẳng file |
| ID | Luật id schema mới | §0.1 | bắt BE lộ khoá chính nội bộ; không vỡ mock cũ |
| RF | Kiểm liên tham chiếu | "H1 ngữ cảnh" ở B0-07, không vào zod | zod mà hỏng thì hỏng cả lượt giải mã FE |
| G1 | Chỗ đặt thực thể mới | `spatial.ts` | mảnh lá không export |
| RV | Trường duyệt của Level/Building | có trong schema; giá trị theo luật §4.1 | ExplodedView, ExportPanel đọc `reviewed` |
| L1 | Mở rộng hay bọc `SpatialLayerSchema` | bọc | `ZodType` |
| L2 | `notes` ở N16 | bỏ | không màn QC nào đọc |
| L3 | Ghi tỉ lệ | `layer?` + `scaleMillimetresPerPixel?`; tính lại mm cho **mọi** thực thể chưa duyệt | áp mọi tầng không phải gửi trọn lớp |
| L4 | Ghi kích thước | `dimensions?` ở #35, thay trọn danh sách, nhật ký theo id | màn Đọc kích thước OCR báo "Đã lưu" mà không gửi gì (BUG-577); người dùng chọn cho ghi (QD-001) |
| SC | `scaleStatus` ở N15 | **không** | chưa màn nào đọc (W1) |
| V1 | `creatorName`, `changedByName` | có | màn in thô id khi thiếu tên |
| V4 | `baseVersion` của restore | `revision` của tầng; thêm `floorRevision` | `sequence` đè im lặng; `isCurrent` sai |
| V5 | Nhãn phiên bản | `label` riêng; gắn nhãn không sinh phiên bản | |
| R1 | Quyền sửa luật | `ruleset.edit` | ma trận FE |
| R2 | Cấu hình luật có tác dụng ở v1 | **có**: F-10 nạp N21 vào RuleReport | lưu mà không tác dụng là lưu giả |
| P-ĐO | Phép đo trùng id, xoá | xoá cứng; khác thân → 409; FE thử lại một lần | xoá mềm + unique làm kẹt 409 và hoàn tác giả |
| N1 | N1 cài ở W07 | bảng đếm theo tầng `project_floor_summaries` cập nhật trong giao dịch ghi | bảng nguồn ra đời ở W08–W11; 500 dự án trong 15 s |
| Q1 | Siết mã chất lượng | không; hằng riêng | |
| A1 | Confirm reset đặt cookie | không | |
| A2 | Nhận lời mời đặt cookie | có | |
| AC1 | `me` mang giao diện/thông báo | không; FE giữ ở bộ nhớ module, chỉ báo lưu của hai khối này nói "chỉ giữ trong phiên này", **không** nói "đã lưu" (bản 10) | nợ v2; người dùng có thể chọn lưu `localStorage` theo máy |
| AC2 | Ảnh đại diện | JSON base64 ≤ 512 KiB | |
| ML1 | Tải trọng số | multipart, CLI/API, 512 MiB, băm khi đọc | |
| ML2 | Kích hoạt bản chưa đánh giá | từ chối (422 `MODEL_VERSION_NOT_EVALUATED`); bản gốc seed `pending`, chỉ `completed` khi dịch vụ `ml` đã đánh giá | bản 9 |
| ML3 | Số đo/log huấn luyện | polling `since`; `nextCursor` luôn có tới hết cửa sổ muộn 600 s sau khi job kết thúc | |
| ML4 | Quay về model gốc | họ tường: `versionId: null` (đường cổ điển); họ khác: kích hoạt lại bản gốc đã seed khi bản đó `completed` | không thêm trường |

## 11. Nướng ánh sáng → `lightingBake.ts` (LB-01)

- `LIGHTING_BAKE_STATUSES = ['queued', 'running', 'done', 'failed']`.
- `LightingBakeSchema` (strict): `id` (`bak_` + ULID), `status`, `createdAt`; `startedAt` có khi và chỉ khi `status ≠ queued`;
  `endedAt` khi và chỉ khi `done | failed`; `failureCode` (UPPER_SNAKE) khi và chỉ khi `failed`. Không trả URL (W1): FE ghép
  đường N44 từ `id` và tên tệp `baked.glb | lightmap.png | manifest.json`.
- N42 gửi byte GLB thô (`Content-Type: model/gltf-binary`), trả 202 `LightingBake`; 409 `LIGHTING_BAKE_IN_PROGRESS`;
  422 `FILE_TYPE_MISMATCH`. N43 404 `resource: "lightingBake"` khi dự án chưa nướng lần nào. N44 tải bằng client HTTP có
  Bearer (không dùng `<img src>`), đổi sang blob URL cho bản xem thử.
- Mã hỏng (`failureCode`): `LIGHTING_BAKE_FAILED`, `LIGHTING_BAKE_TIMEOUT`, `LIGHTING_BAKE_OUTPUT_INVALID`,
  `LIGHTING_BAKE_STALLED`.

## 12. AI chọn phong cách nội thất → `designConcept.ts` (AID-01)

- Kết quả AI **không ghi vào tài liệu tầng**: mỗi concept là một bản JSON riêng (bảng `design_concepts`). FE dựng
  `SceneStyle` từ `result`; trường người dùng đã đặt (`floorMaterial`, `paint`, `model`, `slots`) vẫn thắng phong cách.
  `additions` là đồ sinh ở FE như `furnishEmptyRooms` (không lưu; sửa một món thì cả bộ phòng thành đồ thật).
- `DESIGN_STYLE_IDS` (12): `japandi scandinavian modern-min modern-lux indochine wabi-sabi industrial tropical korean-min
  neoclassic midcentury rustic`. `DESIGN_CONCEPT_STATUSES = ['queued', 'running', 'done', 'failed']`.
- `DesignConceptSchema` (strict): `id` (`dcn_` + ULID), `suggested`, `status`, `createdAt`; `styleId` khi đã có kiểu;
  `endedAt` khi `done | failed`; `failureCode` khi `failed`; `result` khi `done`. `DesignConceptStateSchema`: `enabled`,
  `suggestedStyleId?`, `active?`. Vắng, không `null` (W2); bên trong `result` giữ `null` của `variant`, `modelId`.
- `result`: `nameVi`, `summaryVi`, `baseStyle` (5 kiểu cũ), `wall {interior, exterior?}`, `floor` (khoá `RoomFinish`),
  `models` (`FurnitureRole` → id `library_items`), `additions[] {levelId, roomId, kind, variant, centre, widthMm, depthMm,
  rotationDeg, modelId}`.
- N48 thân `{"styleId": <mã> | null}` hoặc `{}` (= quay về đề xuất AI) → 202 `DesignConcept`; cùng `input_key` (kiểu,
  revision mọi tầng, phiên bản thư viện, vật liệu, prompt) thì dùng lại, không gọi AI. Lỗi 503 `DESIGN_DIRECTOR_DISABLED`,
  429 `DESIGN_DIRECTOR_QUOTA` (cả hai `Retry-After: 10`). Mã hỏng: `DESIGN_DIRECTOR_FAILED`, `DESIGN_DIRECTOR_OUTPUT_INVALID`,
  `DESIGN_DIRECTOR_STALLED`. FE coi mọi lỗi là "dùng cách bày mặc định", không báo lỗi chặn.
