# Nguyên văn thay đổi hiến chương AppBack cho M2-QL (lớp đề xuất) — **chờ chủ dự án duyệt**

Trạng thái: **CHƯA DUYỆT.** Mã trên nhánh `feature/m2-ql-suggestions` (worktree `F:/worktrees/AppBack/m2-ql`) đã chạy theo
các dòng dưới đây và **không** sửa `docs/charter/*`, nhưng **có** sửa `docs/contracts/openapi.json` (sinh lại theo route
N52 và các trường M2), `tools/contract/*` (`APPFRONT_SHA` ghim FE M2, `schema-map.ts` thêm N52, `check.py` và
`tests/test_check.py` đọc dòng chờ duyệt qua shim `tools.charter.pending_bind_rows`) và `docs/contracts.toml` (revision
contract `r20261108_m2_ql`, xem §2.3 "nhật ký"). Nguồn thiết kế:
`F:/App/docs/mo-hinh-dep-chuyen-nghiep/M2/HOP-DONG-DU-LIEU-LOP-DE-XUAT.md` (§2.1, §3, §4, §8). Không ai được ghi
"đã duyệt" cho tới khi bạn trả lời; khi bạn duyệt, người điều phối thêm trailer
`Charter-Approved: <đường tệp này + ngày bạn duyệt>` vào commit hiến chương theo luật `/merge-review` §2.

Cách trả lời đủ: "duyệt nguyên văn", hoặc liệt kê dòng cần sửa.

---

## 1. `docs/charter/BE-BIND.md` — 1 dòng sửa, 1 dòng thêm

**Ghi chú đầu bảng:** `kiem_bo_prompt.py` đòi thêm **1** dòng (N52).

**Dòng #35 (bảng §1), cột Ghi chú — thêm vế cuối:**
> **đổi PATCH → PUT** (F-04b); `remoteChanges` ≥ 1; **M2-QL: thân thêm `suggestionState?`, `suggestionsEnabled?`
> (bỏ qua `suggestions` client gửi), kết quả thêm `suggestions?`, `suggestionsMeta?`, `suggestionsEnabled?`**

**Thao tác mới (thêm sau dòng cuối của §2):**

| N | Đường | operationId | Schema FE | Loại | Khoá | Prompt | Ghi chú |
|---|---|---|---|---|---|---|---|
| N52 | `GET /api/suggestion-rules` | `suggestion_rules_read` | `suggestionRules.ts` (`SuggestionRulesWireSchema`) | Đ\* | — | M2-QL | editor: bảng tham số luật lớp đề xuất; `ETag` = `"<rulesetSha>"`, `If-None-Match` khớp → 304 |

## 2. `docs/charter/HOP-DONG-MOI.md`

### 2.1 §4 #35 — thân và kết quả (thêm vào cuối phần #35)

> - *(M2-QL)* Thân thêm hai khoá tuỳ chọn, đều tính là "khoá" cho luật ≥ 1 khoá:
>   - `suggestionState?: {id, state, override?}[]` (≤ 2 000 dòng): toàn bộ trạng thái **thưa** lớp đề xuất của tầng
>     (bản ghi `state ≠ active` hoặc có `override`); bản không có trong danh sách về `active`, bỏ `override`. `id`
>     khớp `^DRESS-[A-Za-z0-9_.:-]{1,160}$`; `state ∈ active | disabled | overridden | adopted`; `override` là
>     `Record<khoá camelCase, string | number | boolean>` 1–16 khoá. `params`, `stale`, `createdRevision` trong dòng
>     được nhận và bỏ qua (W18).
>   - `suggestionsEnabled?: boolean`: bật/tắt lớp đề xuất của tầng.
>   - `suggestions` client gửi: nhận và bỏ qua (W18), không tính là khoá.
> - Lỗi mới (422, kèm `field` trỏ dòng đầu tiên): `SUGGESTION_UNKNOWN` (id không có trong lớp đã lưu,
>   `field: body.suggestionState.<i>.id`), `SUGGESTION_OVERRIDE_INVALID` (khoá ngoài `overridable` của luật,
>   `field: body.suggestionState.<i>.override`), `SUGGESTION_CAP` (vượt 2 000 bản/tầng sau khi dọn bản `stale` không
>   có dữ liệu người dùng; kèm `count`). Id trùng trong `suggestionState` → `VALIDATION`.
> - Lớp đề xuất do máy chủ sinh trong **cùng** revision với lượt ghi: khi lớp bật **và** `layer` đổi (người sửa,
>   trộn pipeline, đổi tỉ lệ) hoặc lớp vừa được bật. `suggestionState` áp **trước** lượt sinh lại. Gửi lại cùng thân
>   (C09b) hay thân không đổi gì không sinh lại, không tăng revision. Tắt lớp giữ nguyên các bản đã lưu.
> - Kết quả 200 thêm ba trường tuỳ chọn như N16 (§14).
> - Giới hạn v1: `remoteChanges` của 409 **chưa** liệt kê đổi `state`/`override` của đề xuất (nhật ký `floor_change_log`
>   chưa có loại thực thể `suggestion`); xem §14 "Việc còn mở".

### 2.2 §4.1 / N16 — `FurnitureSchema` và tài liệu tầng

> - `FurnitureSchema` thêm `origin?: {suggestionId, rule, ruleVersion}` (món người dùng nhận nuôi từ đề xuất;
>   `suggestionId` khớp mẫu id đề xuất, `rule` `^[A-Z][A-Z0-9]{0,5}$`, `ruleVersion` int ≥ 1). Vắng khoá với đồ thường.
>   Áp cho N15, N16, #35 (vào và ra), phiên bản.
> - `FloorLayerDocumentSchema` (N16) thêm `suggestions?`, `suggestionsMeta?`, `suggestionsEnabled?` (§14).
> - N15: mỗi phần tử `floorRevisions[]` (`FloorRevisionSchema`, strict) thêm cùng ba khoá tuỳ chọn ấy của tầng
>   đó, cùng luật vắng khi rỗng/tắt như N16 (editor nạp tầng qua N15). Áp cho cả N15 qua liên kết chia sẻ (cùng
>   `spatial_graph`). zod refine song ánh `levels` ↔ `floorRevisions` không đổi.
> - `adoptedAs`/`adopted` do máy chủ đặt: #35 mang món có `origin.suggestionId` → đề xuất đó `state: "adopted"`,
>   `adoptedAs` = id món (thắng `suggestionState` client gửi cho id ấy); món biến mất ở lượt sau → `disabled`, bỏ
>   `adoptedAs` (hợp đồng M2 §3 bước 6). Chỉ chạy ở lượt đổi `layer` khi lớp bật.

### 2.3 Mục mới §14

> ## 14. Lớp đề xuất → `suggestions.ts` (M2-QL)
>
> - Lưu trong jsonb `floor_documents.document`, không thêm bảng, không migration: khoá `suggestions` +
>   `suggestionsMeta` chỉ có khi danh sách không rỗng; khoá `suggestionsEnabled: true` chỉ có khi lớp bật. Tài liệu
>   cũ giữ nguyên jsonb.
> - **Bật/tắt theo tầng** (quyết định tạm 08/11 §8.1): tầng chưa từng ghi (revision 0) bật mặc định; tầng đã có dữ
>   liệu trước M2 tắt cho tới khi người dùng gửi `suggestionsEnabled: true`.
> - `SuggestionSchema` (strict): `id`, `rule`, `ruleVersion` (int ≥ 1), `kind` ∈ `skirting | wallTile | cornice |
>   casing | sash | railing | cabinetRun | curtain | rug | ceilingLight | item | modelPick`, `hostRef` (`{wall?, room?,
>   opening?, furniture?, suggestion?}`, ≥ 1 khoá), `params` (`Record<string, Json>`, mm nguyên, lược đồ theo `kind`),
>   `state` ∈ `active | disabled | overridden | adopted`, `createdRevision` (int ≥ 0); tuỳ chọn `override`
>   (`Record<string, string | number | boolean>`), `stale` (`{since, reasonVi}`, BE đặt), `adoptedAs` (id `F-…`, chỉ
>   khi `adopted`). Danh sách sắp theo `id`.
> - `SuggestionsMetaSchema` (strict): `ruleset`, `rulesetSha` (64 hex), `basisRevision` (int ≥ 0).
> - `suggestionsEnabled`: `true` hoặc vắng khoá (vắng = tắt).
> - N52 `SuggestionRulesWireSchema` (strict, tệp `suggestionRules.ts`): `ruleset`, `rulesetSha`, `rules: {id, version, majorSince, kind, nameVi,
>   editor: render|mirror|list, params: Record<string, Json>, overridable: string[]}[]` theo thứ tự tệp
>   `packages/domain/suggestions/rule_params.json`. `ETag: "<rulesetSha>"`, `Cache-Control: private, no-cache`;
>   `If-None-Match` khớp → 304 không thân.
> - Bộ luật mới (đổi `rulesetSha`) chỉ áp ở lượt ghi đổi `layer` đầu tiên của tầng đã bật lớp; máy chủ ghi nhật ký sha
>   cũ → mới; không tự tăng revision khi không ai lưu (§8.4).
> - **Nhật ký (§1.3, dòng mới):** `| Đề xuất M2 | suggestion | DRESS-… | state, override |`. Mỗi lượt ghi đổi
>   `state`/`override` của đề xuất để lại dòng nhật ký (gỡ điều chỉnh = trường bị gỡ); `params` sinh lại không ghi. Nên
>   lượt ghi cầm `baseVersion` cũ nhận 409 có `remoteChanges[].entityType = "suggestion"` (W20). Máy chủ đã làm
>   (migration `r20261108_m2_ql`); FE phải thêm `suggestion` vào `VERSION_ENTITY_KINDS` (NO-M2-2).
> - Ngân sách `reconcile` ≤ 150 ms p95 (§8.6): đo 08/11 trên penthouse 81,8 ms (ch-04 27,2, ch-06 30,1).
> - Câu chữ mục này, dòng N52 và phần thêm ở #35/N16/`FurnitureSchema` **chờ chủ dự án duyệt** (tiền lệ M1-01).

---

## Điểm tôi lưu ý khi bạn đọc (không phải đề xuất sửa)

- Hợp đồng M2 viết đường `GET /api/v1/suggestion-rules`; mọi route AppBack đặt dưới `/api/` không có `v1`, nên mã
  dùng `/api/suggestion-rules`.
- Hợp đồng M2 §4 ghi lỗi `VALIDATION` cho id lạ; mã dùng ba mã riêng theo prompt M2-QL vì FE xử lý khác nhau (id lạ =
  danh sách cũ, tải lại; khoá sai = lỗi form "Điều chỉnh"). Nếu bạn muốn gộp về `VALIDATION` thì nói.
- `refreshSuggestions?` của hợp đồng M2 §4 chưa làm: bật lớp hoặc sửa `layer` đã chạy lại; nút "Cập nhật gợi ý" cần
  khoá này thì thêm sau.
- FE strict (W1): trước khi FE thêm ba trường vào zod, tầng **mới** sẽ trả `suggestionsEnabled`/`suggestions` mà
  schema FE chưa khai — phải gộp FE cùng lúc với BE.

---

## Phụ lục — dòng BE-BIND máy đọc (khuôn BE-BIND §2)

Nguyên văn dòng N52 ở §1, đúng khuôn BE-BIND §2. `tools/charter.py` `pending_bind_rows` đọc bảng này để cổng hợp
đồng (bước 7) và cổng case (5b) vẫn kiểm N52 khi `docs/charter/BE-BIND.md` chưa có dòng. Cổng chỉ cần dòng BE-BIND
cho **thao tác mới**: phần thêm ở #35 (thân `suggestionState?`/`suggestionsEnabled?`, kết quả ba khoá) và khoá N15/N16
không đổi `operationId`, đường hay khoá quyền, nên không cần dòng; chúng được H1 kiểm qua schema FE strict của
#35/N15/N16 trên mẫu thật. `tools/contract/schema-map.ts` **chưa** có mục `suggestion_rules_read`: mục đúng
(`object('@/api/schemas/suggestionRules', 'SuggestionRulesWireSchema')`) trỏ tệp chỉ có ở nhánh FE M2, thêm vào khi
AppFront `6fbff729` thì Smoke hỏng và kéo đỏ mọi test dùng runner. Vì vậy bước 7 "Bản đồ đủ" (và
`test_real_repo_check_runs_through`) còn đỏ một dòng cho tới khi FE M2 được đẩy, `tools/contract/APPFRONT_SHA` ghim
tới đó và mục trên được thêm cùng commit ghim. Duyệt xong: chép dòng vào BE-BIND, xoá tệp này (`changes/M2-QL.md`
NO-M2-1).

| # | Đường v1 | operationId | Schema (file) | Loại | Khoá | Nhật ký | Chủ BE | Nối FE |
|---|---|---|---|---|---|---|---|---|
| N52 | `GET /api/suggestion-rules` | `suggestion_rules_read` | `suggestionRules.ts` (`SuggestionRulesWireSchema`) | Đ\* | — | — | M2-QL | editor: bảng tham số luật lớp đề xuất; `ETag` = `"<rulesetSha>"`, `If-None-Match` khớp → 304 |
