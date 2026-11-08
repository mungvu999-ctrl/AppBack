# `packages/domain/suggestions` — lớp đề xuất (M2)

Gói miền thuần theo hợp đồng `docs/mo-hinh-dep-chuyen-nghiep/M2/HOP-DONG-DU-LIEU-LOP-DE-XUAT.md` (ngoài repo,
`F:/App/docs/…`). Không IO, không DB; chỉ nhập `pydantic`, `shapely`, `packages.domain.spatial` và
`canonical_json` của `packages.domain.model_export.snapshot`.

Gói **chưa** được nối vào `apps/api/spatial_write/writer.py`, compiler hay FE. Phần dưới ghi các lát sau phải gọi gì.

## Thành phần

| Tệp | Nội dung |
|---|---|
| `rule_params.json` | Bảng tham số dùng chung (§5): mỗi luật `{version, majorSince, kind, nameVi, editor, params (mm), overridable, migrate?}` |
| `ruleset.py` | `load_ruleset()` (có cache), `ruleset_from_text()`, `Ruleset.sha` = sha256 của JSON chuẩn hoá (khoá sắp xếp, không khoảng trắng, UTF-8): CRLF/LF hay thụt lề không đổi sha |
| `model.py` | `Suggestion`, `SuggestionsMeta`, `HostRef`, `Stale`, lược đồ `params` theo từng `kind` (`PARAMS_BY_KIND`), `effective_params`, `suggestions_to_json/from_json`, `document_keys` |
| `reconcile.py` | `reconcile(...)`, `apply_state(...)`, `SuggestionCapError`, `UnknownSuggestionError`, `SUGGESTIONS_CAP = 2000` |
| `rules/` | sổ luật `RULES` (thứ tự chạy cố định) và các luật; `rules/geom.py` là hình học nhỏ để FE chép lại được |
| `messages.py` | câu tiếng Việt cho lý do `stale` và nhật ký |
| `tests/data/*.layer.json`, `*.golden.json` | ca vàng cho FE (R1, R4, C2, F1, L1) từ bench `r5/ch-04`, `r5/ch-06` |

## API

```python
from packages.domain.suggestions import (
    load_ruleset,
    reconcile,
    apply_state,
    document_keys,
    suggestions_from_json,
    effective_params,
    SuggestionCapError,
    UnknownSuggestionError,
)

rs = load_ruleset()
result = reconcile(prev, layer, rs, concept=None, revision=new_revision, adopted=ids, prev_meta=meta)
result.suggestions  # list[Suggestion], sắp theo id, tất định
result.meta  # SuggestionsMeta(ruleset, rulesetSha, basisRevision=new_revision)
result.notes  # list[Note(rule, host, status: skipped|degraded|log, reason_vi)]
```

- `reconcile` làm đúng §3 bước 1–6: chạy luật theo thứ tự `RULES`; id có ở cả hai bên thì thay `params`,
  `ruleVersion`, giữ `state`/`override`/`createdRevision`/`adoptedAs`, bỏ `stale`; id mới thì `active` với
  `createdRevision = revision`; id chỉ còn ở `prev` thì đặt `stale {since, reasonVi}` (giữ `since` cũ nếu đã stale),
  lý do lấy theo thứ tự: luật bị bỏ khỏi bảng → đổi bản chính → chủ bị xoá → ghi chú `skipped` của luật cho chủ đó
  (vd F1 "đồ chủ đã có mẫu người chọn") → "luật không còn đề xuất cho chủ này"; `adopted` không có trong `adopted`
  thì về `disabled` (ghi nhật ký).
- **Đổi bản phụ** (`ruleVersion < version`, `≥ majorSince`): sinh lại `params`, bỏ khoá `override` không còn trong
  `overridable` và ghi `Note(status="log")`; hết khoá thì `overridden` → `active`.
- **Đổi bản chính** (`ruleVersion < majorSince`): `migrate` là `{regex: mẫu thay}` áp lên id cũ (`re.fullmatch` +
  `expand`); khớp thì chuyển `state`/`override` sang id mới (ghi nhật ký nếu id đổi). Không khớp mà luật vẫn sinh
  cùng id thì bản ghi về `active` mới (có dữ liệu người dùng thì ghi nhật ký); không sinh lại thì `stale`
  "luật đã đổi, xem lại".
- **Dọn:** bản `stale` không có dữ liệu người dùng chỉ bị xoá khi `prev_meta.rulesetSha ≠ rs.sha`. Vượt `cap` thì
  xoá bản `stale` không dữ liệu, cũ trước (`stale.since`, rồi id); vẫn vượt thì `SuggestionCapError`.
- `apply_state(current, rows, rs)`: áp `suggestionState` thưa của thân #35 (`[{id, state, override?}]`). Bản không
  có trong `rows` về `active`, không `override`. Id lạ hoặc khoá `override` ngoài `overridable` của luật →
  `UnknownSuggestionError`. `adoptedAs` chỉ giữ khi vẫn `adopted`.
- `effective_params(s)` = `{**params, **override}`; `Suggestion.is_effective()` = `active|overridden` và không `stale`.
- `document_keys(items, meta)` trả `{}` khi danh sách rỗng, nên document cũ ra đúng jsonb cũ.

## Luật đã làm (lát này)

| Mã | Ra gì | Quy ước số (FE chép lại phải theo đúng) |
|---|---|---|
| R1 len | một bản ghi mỗi (phòng khô, tường); id `DRESS-R1-<phòng>-<tường>` | cạnh phòng song song (sin < 0,05) và cách tim ≤ dày/2 + 60 mm; `side`: `left` = phía pháp tuyến (−dy, dx) của hướng start→end; `spansMm` tính từ `centreline.start`, trừ cửa đi `[offset − 70, offset + rộng + 70]`, bỏ đoạn < 100 mm, làm tròn kiểu `Math.round`. Tường cong: bỏ qua, có ghi chú. Phòng tắm không có len (R2) |
| R4 lan can | `DRESS-R4-<phòng>-e<k>` cho cạnh `k` của đường bao lô gia | phần cạnh không nằm trong hình chữ nhật tường nới 60 mm, dài ≥ 400 mm; `spansMm` tính từ đỉnh `k`. Không có cạnh hở → ghi chú |
| C1 nẹp, C2 khung cánh | `DRESS-C1-<ô mở>`, `DRESS-C2-<ô mở>` cho mỗi cửa sổ | chép tham số bảng |
| D2 rèm | `DRESS-D2-<ô mở>-L`/`-R` (`L` về phía đầu tường) | phòng phía trong dò ở tim ô ± (dày/2 + 150); chỉ `bedroom/livingRoom/other`; thanh treo rộng hơn ô 300 → 200 → 100 mm (lùi thì `degradedVi` + ghi chú), không vừa thì bỏ; tấm rộng `max(350, round(300 + 150 · rộng_m))`, `topMm` = cao tường − 70 |
| F1 chọn mẫu | `DRESS-F1-<đồ>` | dòng đầu khớp trong `params.table` (cạnh dài/ngắn của hộp, `variant`, công năng phòng); co quá 10 % thì bỏ (ghi chú); `scale` = 1 hoặc tỉ lệ làm tròn 3 số; `kindAs/variantAs` chỉ với đồ AI chưa duyệt/chưa sửa; đồ có `model` hay B3 `suggested` thì bỏ qua |
| L1 đèn trần | `DRESS-L1-<phòng>` | tâm = polylabel (thuật toán mapbox/polylabel, độ chính xác 50 mm) làm tròn mm; `pendant` + `modelId` khi công năng thuộc `lampUsages` và `areaM2 ≥ 6`, còn lại `panel` 300 × 300 × 40; lô gia không có |

R2, R3, F2, F3, P1–P3, D1, D3–D5, AID đã đăng ký trong `RULES` và bảng nhưng chưa sinh gì (chờ mô hình va chạm).
`rules.furniture.missing_primary_slots(layer, room, spec)` là điều kiện "phòng đã có đồ vai này chưa" P1 sẽ dùng.

## Lát writer (`apps/api/spatial_write`) phải làm

1. Thêm `suggestions?`/`suggestionsMeta?` vào document và wire (khoá vắng khi rỗng), `Furniture.origin?`.
2. Trong `_next_layer`, sau `mark_edited` và đổi tỉ lệ, trước `_unchanged`:
   `current = apply_state(suggestions_from_json(doc["suggestions"]), body.suggestion_state or [], rs)` (chỉ khi thân
   có `suggestionState`), rồi nếu `layer` đổi hoặc `refreshSuggestions`:
   `reconcile(current, new_layer, rs, concept, revision=base + 1, adopted={f.origin.suggestion_id for f in
   new_layer.furniture if f.origin}, prev_meta=doc_meta)`. Gửi lại cùng thân (C09b) thì không gọi.
3. Ghép `document_keys(result.suggestions, result.meta)` vào document; ghi `result.notes` vào log;
   `UnknownSuggestionError`/`SuggestionCapError` → 422 `VALIDATION`; `_write_log` chỉ ghi đổi `state`/`override`.
4. Đo `reconcile` trên 66 ca bench (≤ 150 ms p95), theo quyết định tạm §8.6.

## Lát compiler (`model_export`)

Đọc `snapshot.floors[i].suggestions`; dựng bản `s.is_effective()` với `effective_params(s)`; bỏ `disabled`, `stale`,
`adopted`. Không đọc `rule_params.json`.

## Lát FE

Đọc bảng qua `GET /api/v1/suggestion-rules` (ETag = `rulesetSha`). Luật tự chạy nhẹ (R1, R4, C2, F1, L1) phải ra
đúng `tests/data/<ca>.golden.json` từ `tests/data/<ca>.layer.json` (`id`, `rule`, `ruleVersion`, `kind`, `hostRef`,
`params`); `test_golden.py` khoá phía BE. Làm tròn bằng `Math.round`, polylabel dùng gói `polylabel` của mapbox.

## Lệch khỏi hợp đồng

- `reconcile` cần thêm `revision`, `adopted`, `prev_meta`, `cap` (keyword) và trả `Reconciled` thay cho
  `list[Suggestion]`: hợp đồng cần revision cho `createdRevision`/`stale.since`, cần biết `origin` mà `Furniture`
  chưa có trường này, và cần sha cũ để biết có đổi bộ luật.
- Mỗi luật có thêm `majorSince` để phân biệt bản phụ/bản chính; `migrate` là mẫu regex vì id cụ thể không biết trước.
- `stale` lấy lý do từ ghi chú `skipped` của luật cho cùng chủ; ghi chú `skipped` cũng là nhật ký cho writer.
