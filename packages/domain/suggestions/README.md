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
| `rules/placement.py` | vết chân theo bbox mẫu, `Site` (tường, mặt sàn phòng, vùng cửa, vật cản), `place`, `check_collisions`, `model_origin` |
| `rules/kitchen.py`, `rules/items.py` | F2/F3 dãy tủ bếp; P1–P3, D1, D3–D5 đồ đạc |
| `data/model_bbox.json` | bbox 84 mẫu của gói (mét, Blender Z-up, mặt trước về −y). Chép nguyên từ thí nghiệm B2 `F:/runs/abc/rules/compiler/packages/domain/model_export/model_bbox.json` (đo bằng `F:/runs/abc/rules/scripts/dbg/model_bbox.py`, 08/11/2026); nguồn ghi trong khoá `source`. Nên chuyển thành `bboxMm` của gói |
| `messages.py` | câu tiếng Việt cho lý do `stale` và nhật ký |
| `tests/data/*.layer.json`, `*.golden.json` | ca vàng từ bench `r5/ch-04`, `r5/ch-06`, `r5/syn-penthouse` (bỏ khoá `clearAreaM2` ngoài lược đồ) |

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

check_collisions(layer, result.suggestions)  # list[CollisionNote(label: doc|rule, type, item, other, area_mm2)]
model_origin("toilet-sf", centre, rotation_deg, lift_mm, scale)  # gốc mẫu (mm) từ tâm vết chân + đáy
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

## Luật editor chép lại (lát đầu)

| Mã | Ra gì | Quy ước số (FE chép lại phải theo đúng) |
|---|---|---|
| R1 len | một bản ghi mỗi (phòng khô, tường); id `DRESS-R1-<phòng>-<tường>` | cạnh phòng song song (sin < 0,05) và cách tim ≤ dày/2 + 60 mm; `side`: `left` = phía pháp tuyến (−dy, dx) của hướng start→end; `spansMm` tính từ `centreline.start`, trừ cửa đi `[offset − 70, offset + rộng + 70]`, bỏ đoạn < 100 mm, làm tròn kiểu `Math.round`. Tường cong: bỏ qua, có ghi chú. Phòng tắm không có len (R2) |
| R4 lan can | `DRESS-R4-<phòng>-e<k>` cho cạnh `k` của đường bao lô gia | phần cạnh không nằm trong hình chữ nhật tường nới 60 mm, dài ≥ 400 mm; `spansMm` tính từ đỉnh `k`. Không có cạnh hở → ghi chú |
| C1 nẹp, C2 khung cánh | `DRESS-C1-<ô mở>`, `DRESS-C2-<ô mở>` cho mỗi cửa sổ | chép tham số bảng |
| D2 rèm | `DRESS-D2-<ô mở>-L`/`-R` (`L` về phía đầu tường) | phòng phía trong dò ở tim ô ± (dày/2 + 150); chỉ `bedroom/livingRoom/other`; thanh treo rộng hơn ô 300 → 200 → 100 mm (lùi thì `degradedVi` + ghi chú), không vừa thì bỏ; tấm rộng `max(350, round(300 + 150 · rộng_m))`, `topMm` = cao tường − 70 |
| F1 chọn mẫu | `DRESS-F1-<đồ>` | dòng đầu khớp trong `params.table` (cạnh dài/ngắn của hộp, `variant`, công năng phòng); co quá 10 % thì bỏ (ghi chú); `scale` = 1 hoặc tỉ lệ làm tròn 3 số; `kindAs/variantAs` chỉ với đồ AI chưa duyệt/chưa sửa; đồ có `model` hay B3 `suggested` thì bỏ qua |
| L1 đèn trần | `DRESS-L1-<phòng>` | tâm = polylabel (thuật toán mapbox/polylabel, độ chính xác 50 mm) làm tròn mm; `pendant` + `modelId` khi công năng thuộc `lampUsages` và `areaM2 ≥ 6`, còn lại `panel` 300 × 300 × 40; lô gia không có |

## Luật chỉ chạy ở máy chủ (lát va chạm)

Thứ tự chạy: R1 R2 R3 R4 C1 C2 F1 F2 F3 P1 P2 D1 P3 D2 D3 D4 D5 L1 AID. Luật sau thấy mọi đề xuất **đang hiệu lực**
của luật trước (mọi `kind`, không chỉ `item`): P2 thấy giường P1, P3 thấy thảm D1, D3 thấy tab P2, F3 thấy dãy F2.
AID vẫn chưa sinh gì (chờ nối concept).

**Khung toạ độ đồ:** vết chân = bbox mẫu (rộng theo x cục bộ, sâu theo y cục bộ) xoay `rotationDeg` quanh tâm theo
công thức chuẩn trên toạ độ layer (y xuống); mặt trước mẫu là +y cục bộ, nên đồ quay mặt về n có
`rotationDeg = atan2(−n.x, n.y)`. Khớp compiler (`rotZ = −rotationDeg`) và `boundingBox` của document (vết chân trước
khi xoay). `item.centre` là tâm bbox, `liftMm` tính từ sàn hoàn thiện (20 mm); `model_origin` đổi ra gốc mẫu (F0).

**Va chạm** (`placement.check_collisions`, port KET-QUA-LOI §4): vết chân thu 20 mm (chạm không tính), ngưỡng
2 000 mm²; loại `wall`, `outside-room`, `opening-clearance` (0,8 m mỗi phía cửa đi), `window` (0,4 m, chỉ khi đồ cao
hơn bậu + 50 mm), `item-item`. Chỉ đồ đứng sàn (`liftMm < 300`) và dãy tủ dưới. `doc` = có sẵn giữa các hộp document
(writer chỉ ghi nhật ký, không sửa document); `rule` = có đề xuất tham gia. Dãy F2 thừa hưởng va chạm `doc` của hộp chủ
và không xét với hộp chủ/hộp trùng. Luật không bao giờ để lại `rule`: đồ trượt dọc tường, không được thì bỏ kèm lý do.
Hộp document thiếu `heightMm` lấy 800 mm (tủ áo 2 000 mm).

| Mã | Ra gì | Quy ước |
|---|---|---|
| F2 tủ dưới | `DRESS-F2-<hộp>` `cabinetRun` `slot=base` | hộp `kitchenCabinet` cùng phòng chồng ≥ `dupOverlapPct` hộp nhỏ → một dãy theo hộp lớn (`mergedIds`, `degradedVi`, ghi chú `degraded` "hộp trùng" cho hộp nhỏ). Cổng: không có `model`; công năng ∈ `roomUsages`; cạnh dài ≥ `minRunMm`; cạnh ngắn ≤ `maxBoxDepthMm`; lưng (cạnh dài gần tường nhất) có ≥ `wallCoverPct` chiều dài cách tường ≤ `wallMaxMm`. `start`→`end` = lưng, dãy nằm phía pháp tuyến trái, sâu `depthMm`; dãy thêm va chạm mới → bỏ |
| F3 tủ trên | `DRESS-F3-<hộp>`, `hostRef.suggestion` = F2 | cùng lưng với F2 đang hiệu lực, sâu 350; bỏ khi vết (nới `openingClearMm`) chạm ô mở |
| P1 đồ chính | `DRESS-P1-<phòng>-<khe>` | khe của `roles[usage]` chưa có đồ thật (theo `kinds`/`variants`/hộp `boxKinds` đủ cỡ — ghế ≥ 1,4 × 0,6 m lấp khe sofa); `place`: lưng áp đoạn tường thật (chỉ phần cạnh có tường phía sau, ≥ 300 mm), `prefer` centre/ends, `solid` cần đoạn ≥ rộng + `solidMarginMm` và không ô mở trong 150 mm sau lưng; tỉ lệ 1 → −5 % → −10 % rồi bỏ |
| P2 tab đầu giường | `DRESS-P2-<giường>-L/R` | giường document (`furniture`) hoặc P1 (`suggestion`, id bỏ `DRESS-`), chỉ phòng ngủ; đầu giường = cạnh ngắn gần tường nhất; cách giường `gapMm` |
| D1 thảm | `DRESS-D1-<sofa>` | tâm cách tâm sofa `offsetMm` về phía trước; giữ khi ≥ `minInsidePct` nằm trong phòng thu `insetMm` (renderer tự cắt phần ngoài) |
| P3 bàn trà | `DRESS-P3-<sofa>` | cách mép trước sofa `distanceMm`; `liftMm` = dày thảm khi D1 của cùng sofa đang hiệu lực |
| D3 bình | `DRESS-D3-<tab>` | trên mỗi tab P2 hiệu lực, `liftMm` = cao tab |
| D4 tranh | `DRESS-D4-<phòng>` | đoạn tường dài nhất ≥ rộng + 2 × `sideMarginMm`, không ô mở sau, không đồ cao > `tallItemMm` trước; tâm ở `centreHeightMm` |
| D5 cây | `DRESS-D5-<phòng>` | `place` từ đầu đoạn tường (góc) |
| R2 gạch ốp | `DRESS-R2-<phòng>-<tường>` | như R1 cho phòng `usages`; cao min(`heightMm`, cao tường); cắt cửa đi và cửa sổ có bậu thấp hơn chiều cao ốp; `sillSpansMm` `(từ, đến, bậu)` khi bậu ≥ `minSillMm` |
| R3 phào | `DRESS-R3-<phòng>` | mọi phòng ngoài `skipUsages`, vật liệu trần |

Đồ trùng chỗ (chồng nhau) loại khác `kitchenCabinet` chỉ được ghi `doc`.

## Lát writer (`apps/api/spatial_write`) phải làm

1. Thêm `suggestions?`/`suggestionsMeta?` vào document và wire (khoá vắng khi rỗng), `Furniture.origin?`.
2. Trong `_next_layer`, sau `mark_edited` và đổi tỉ lệ, trước `_unchanged`:
   `current = apply_state(suggestions_from_json(doc["suggestions"]), body.suggestion_state or [], rs)` (chỉ khi thân
   có `suggestionState`), rồi nếu `layer` đổi hoặc `refreshSuggestions`:
   `reconcile(current, new_layer, rs, concept, revision=base + 1, adopted={f.origin.suggestion_id for f in
   new_layer.furniture if f.origin}, prev_meta=doc_meta)`. Gửi lại cùng thân (C09b) thì không gọi.
3. Ghép `document_keys(result.suggestions, result.meta)` vào document; ghi `result.notes` vào log;
   `UnknownSuggestionError`/`SuggestionCapError` → 422 `VALIDATION`; `_write_log` chỉ ghi đổi `state`/`override`.
4. Đo `reconcile` trên 66 ca bench (≤ 150 ms p95), theo quyết định tạm §8.6. Trong container: ch-04 ~85 ms,
   ch-06 ~50 ms, penthouse ~140 ms (mỗi luật đặt đồ dựng lại `Site`; cache theo layer nếu vượt).
5. Ghi `check_collisions(new_layer, result.suggestions)` vào log (`doc` mức info); `rule` khác rỗng là lỗi luật.

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
- `stale` lấy lý do từ ghi chú `skipped` của luật cho cùng chủ hoặc cùng id đề xuất; ghi chú `skipped` cũng là nhật ký.
- Luật sau thấy mọi đề xuất đang hiệu lực của luật trước, không chỉ `item` (§3 bước 1 ghi `item`): F3 cần F2, P3 cần D1.
- Thêm vào `params`: `cabinetRun.start/end` (đường lưng, bắt buộc), `mergedIds`, `degradedVi`; `wallTile.sillSpansMm`;
  `item.variant` (để nhận nuôi giữ được kiểu đồ). D1 chạy trước P3 (bàn trà đứng trên thảm).
- Va chạm chỉ có hai nhãn `doc`/`rule`; nhãn `a-model` của thí nghiệm không còn vì lớp đề xuất không đặt mẫu A.
