# Review merge feature/m2-ql-suggestions → main

- Ngày: 2026-11-08 · Reviewer: phiên /merge-review (độc lập, không phải tác giả) · Commit đầu nhánh: 6194c8087cd0 · base
  origin/main d1deb23c0294 · nhánh chứa M1 (`9be254c2` gộp `feature/m1-01-model-export` @ `07a957d9`, đã review riêng:
  `2026-11-08-feature-m1-01-model-export*.md`, REQUEST CHANGES lượt 2). Lượt này soát M2 = `git diff 07a957d9 HEAD`
  (72 tệp, ~8.490 dòng logic ngoài test/json/md) và **không** nhắc lại finding M1, trừ chỗ M2 kéo dài chúng.
- Cổng: phạm vi **đầy đủ** (lượt 1 của nhánh, R-33b điều kiện 1; diff còn chạm `tools/**`, `openapi.json`,
  `APPFRONT_SHA`, `uv.lock`). **Chưa có lượt cổng đầy đủ nào thoát 0.** Bốn log cổng của người điều phối đều thoát 1 ở
  bước 5, nên bước 3 (mypy chạy nền, không thu kết quả), 5b, 6, 7 (có junit), 8 **chưa chạy**, độ phủ không in:
  `F:/runs/m2/gates/be-full-m2.log` (@109cc4b8, 1 hỏng), `be-full-m2-r2.log` (@fbebd42a, 5 hỏng), `be-full-m2-r3.log`
  (@a2aa281d, 1 hỏng `test_real_map_covers_every_contract_row`, 10 385 đạt). `6194c808` sửa con số đó;
  `be-contract-m2.log` @HEAD: `pytest tools/contract/tests` 106 đạt. `be-steps78-m2.log` @HEAD (`--steps 7,8`) thoát 1:
  H1/H5 "0 mẫu" vì chạy bước 7 không có junit bước 5 (đã biết, phải `--steps 5,7`), bước 8 chưa chạy. Không phải bằng
  chứng xanh, cũng không phải lỗi của nhánh. Đối chiếu riêng của reviewer (`run.sh shell` @6194c808):
  `python -m apps.api.core.openapi --out /tmp/o.json --compare docs/contracts/openapi.json` thoát **0**; `mypy` trên
  `packages/domain/{suggestions,model_export,spatial}`, `apps/api/{spatial_write,spatial_read,suggestion_rules,model_export}`,
  `apps/worker/model_export`, `packages/testing/factories/suggestions.py` thoát **0** (172 tệp). Một fork reviewer chạy riêng
  `test_reconcile_p95__golden_layers_within_budget`: p95 ch-04 39,7 ms, ch-06 23,6 ms, syn-penthouse 65,6 ms (< 150 ms).
- Độ phủ: **không đo** (bước 5 hỏng ở cả bốn lượt, không có bảng coverage). Không chép số của tác giả (K25).
- Nguồn luật: `RULE.md`, `RULE-CODE.md` đọc ở `6b2e4d52^` (như review M1); sổ nợ trong `changes/M2-QL.md`; hợp đồng
  `F:/App/docs/mo-hinh-dep-chuyen-nghiep/M2/HOP-DONG-DU-LIEU-LOP-DE-XUAT.md` (§2–§8).

## Phạm vi và điều kiện dừng sớm (§2)

- Cây sạch (`git status --porcelain` rỗng). 27 commit không gộp sau `07a957d9`: dòng đầu đúng mẫu
  `<type>(<scope>): …`, cả 27 có `Prompt: M2-QL`. Có `changes/M2-QL.md`.
- Không `pragma: no cover|no branch`, `type: ignore` trần, `noqa` trần, `skip`/`xfail` mới (grep dòng `+` của diff M2: rỗng).
- `docs/charter/*`: `git diff origin/main HEAD -- docs/charter/` **rỗng**. Câu chữ N52, #35, N15/N16, HOP-DONG-MOI §14
  nằm ở `docs/reviews/pending/charter-M2-QL.md`, tự ghi "**CHƯA DUYỆT**". Không có commit nào của M2 chạm charter →
  không dừng sớm. (Lịch sử còn `f4ae5ac6`/`031ba618` của M1 → chỉ merge squash, như M1 Nit 6.)
- Không migration (đúng hợp đồng §4: jsonb, expand-only). `uv.lock` diff so `main` chỉ là `shapely` của M1 (đã duyệt
  thủ tục ở review M1); M2 không đổi lock.
- `openapi.json`, `APPFRONT_SHA`, `schema-map.ts`: người điều phối sinh/ghim trong nhánh (tiền lệ LB-01/AID-01/M1) —
  đạt thủ tục; nội dung xem finding 2, 3.

## Đối chiếu luật bắt buộc của chủ dự án

| Luật | Kết quả | Bằng chứng |
|---|---|---|
| Không âm thầm đổi lựa chọn của người dùng (document > override > params > default) | **Vi phạm 2 chỗ** | finding 4 (mất `suggestionState` khi nền cũ), 5 (tắt lớp vẫn xuất); latent: finding 8 (bản chính reset trạng thái) |
| Luật chỉ chọn lại khi chưa xác định/được phép | Đạt | `modelPick` bỏ khi đồ chủ có `model.src`; phân loại lại chỉ `source='ai'`, chưa duyệt/sửa; test `test_precedence.py` |
| Id ổn định `DRESS-<rule>-<hostId>[-slot]` | Đạt | khe tên ngữ nghĩa (`L/R`, `bed`, `base/upper`); số duy nhất là `e<k>` của R4, hợp đồng §5 cho phép |
| Nguồn gốc + phiên bản luật + liên kết chủ | Đạt | `Suggestion.rule/ruleVersion/hostRef`; extras nút `{prov, rule, ruleVersion, suggestionId, hostRef, state}` |
| `reconcile` thuần/tất định/lặp được, cùng revision | Đạt | `revision=current.revision + 1` (`writer.py:515`) = revision của `_commit`; test xáo đầu vào, byte giống nhau |
| Nhận nuôi → `furniture` `source='human'` + `origin` | **Một phần** | writer suy `adopted` từ `origin`, nhưng không ép `origin ⇒ source='human'` (finding 10) |
| Không bộ dữ liệu ẩn chỉ trong compiler | **Đạt cho đề xuất; lệch ở hằng dãy tủ** | `suggestions_plan` chỉ dựng từ `floor["suggestions"]`; không tệp `model_export` nào đọc `rule_params.json`; nhưng F2 nhân bản tham số (finding 7) |
| Không kéo giãn mẫu (±10 %, ngoài → degraded + lý do) | Đạt | co giãn đều, kẹp ±10 %; `item` đề xuất ngoài trần bị bỏ có lý do; đồ document kẹp + `degraded minor` có % cần; lỗ hổng phép thử khi không xoay (finding 12) |
| Bản xuất mặc định PBR sạch | Đạt | không nướng ánh sáng vào màu; nhiễu độ nhám tất định (`crc32(key)`), lặp được |
| Va chạm phân loại doc/rule, va chạm rule = 0 | **Chỉ đúng trên 3 ca vàng** | `check_collisions` không có nơi gọi ngoài test (finding 6) |
| Bộ luật mới chỉ áp ở lượt lưu đổi `layer` đầu tiên, tầng đã bật | Đạt | `writer.py:503-506`; log `suggestions_ruleset_changed`; tầng revision 0 suy là bật, tầng cũ tắt tới khi bật (có test) |

## Finding

| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | **P1** | §3 bằng chứng cổng / R-33b | Không có lượt `run.sh verify` đầy đủ nào thoát 0. Cả bốn log dừng ở bước 5 → mypy (toàn repo), 5b (case gate, gồm case bắt buộc của N52), 6, 7 với junit, 8 chưa từng chạy; độ phủ mỗi gói chạm (≥ 90/90) không có số. Khẳng định "xanh trừ một con số" không đúng: ngoài con số, 5 bước chưa chạy. | `F:/runs/m2/gates/be-full-m2-r3.log` (bảng bước: 3, 5b, 6, 7, 8 "chưa chạy"); `be-steps78-m2.log` | Sau khi sửa các finding dưới: `docker stop f14-ml-1`, `bash tools/verify/run.sh verify` @HEAD mới, thoát 0, đọc độ phủ `packages/domain` (suggestions), `apps/api/{spatial_write,spatial_read,suggestion_rules,model_export}`, `apps/worker/model_export`. |
| 2 | **P1** | K24 / R-12 / BE-00 §2 / R-38 (kéo dài M1 lượt 2 F1) | NO-M2-1 **[merge] mở**: N52 `suggestion_rules_read` qua bước 7 và 5b nhờ shim `pending_bind_rows` đọc bảng máy đọc của tệp chờ duyệt; `test_check.py` khoá con số 103 = "4 dòng chờ duyệt (N49-N52)". Merge bây giờ đưa route N52 và các trường #35/N15/N16 chưa duyệt vào `main` với cổng xanh. Tệp chờ còn ghi sai "không sửa … `openapi.json` hay `tools/contract/*`" trong khi nhánh đã sửa cả ba. | `changes/M2-QL.md:40-50`; `docs/reviews/pending/charter-M2-QL.md:1-7,108-121`; `tools/charter.py:150`; `tools/contract/tests/test_check.py:69-78` | Chủ dự án duyệt `charter-M2-QL.md` (cùng lúc `charter-M1-01.md`); người điều phối commit charter riêng chỉ tài liệu, trailer `Prompt: M2-QL` + `Charter-Approved: <dòng duyệt thật>`; chép N52 vào BE-BIND, gỡ shim (xem M1 F1), xoá tệp chờ, đổi docstring/con số test về BE-BIND. Không duyệt → gỡ route N52 + mục `schema-map.ts`. |
| 3 | **P1** | API (hợp đồng FE) / OPS (kéo dài M1 lượt 2 F2) | `APPFRONT_SHA` = `14efdd78`: `git branch -r --contains` → chỉ `origin/feature/m2-ql-suggestions`; `merge-base --is-ancestor 14efdd78 origin/master` → 1 (`origin/master` = `a2238c11`). `main` sẽ ghim và deploy (`deploy.yml` dựng web từ SHA này) nhánh FE chưa vào `master`. | `tools/contract/APPFRONT_SHA:1` | FE M2 (và M1) vào `origin/master` AppFront; ghim lại `APPFRONT_SHA` = commit trên `origin/master`; chạy lại bước 5,7 (+5b). |
| 4 | **P1** | CON (read-modify-write) / luật "không âm thầm đổi lựa chọn" | Mất cập nhật `suggestionState`. `apply_state` coi danh sách là **toàn bộ** trạng thái thưa: món không có trong danh sách về `active`, bỏ override (`reconcile.py:201,219-220`). `_write_log` chỉ ghi khác biệt `layer`/`dimensions`, nên lượt chỉ đổi trạng thái không để dòng `floor_change_log` nào; `_settle_base` với `base < current` và `remote_changes_since` rỗng thì `return current.revision` → cho ghi. Kịch bản: tab/người A tắt hay điều chỉnh đề xuất (rev 5→6); B cầm nền 5 gửi `suggestionState` của mình → không 409, lựa chọn của A bị đặt lại `active`. Test `test_conflict_409__lists_suggestion_state_changes` chỉ phủ ca bên kia **cũng** đổi `layer`; docstring tự nhận `remoteChanges` không liệt kê đổi đề xuất. | `apps/api/spatial_write/writer.py:607-633` (`_settle_base`), `:490-506`; `packages/domain/suggestions/reconcile.py:198-225`; `apps/api/spatial_write/tests/test_writer_suggestions.py:321` | Rẻ nhất: khi `request.base_revision < current.revision` và thân có `suggestionState` mà trạng thái thưa hiện tại khác trạng thái ở nền → `VersionConflictError` (hoặc 409 luôn khi nền cũ + có `suggestionState`). Bền: migration mở CHECK `entity_type` + `VersionEntityType` để `_write_log` ghi thực thể `suggestion` (nợ tác giả đã nêu). Thêm test: A đổi chỉ trạng thái, B nền cũ gửi `suggestionState` → 409, trạng thái A còn nguyên. |
| 5 | **P1** | LOG / luật "không âm thầm đổi lựa chọn" | Tắt lớp đề xuất vẫn xuất đề xuất. Writer "tắt lớp giữ nguyên các bản đã lưu" (`writer.py:496,505-506`), còn `suggestion_keys` của snapshot trả `suggestions` khi danh sách không rỗng, không đọc `suggestionsEnabled`; test còn khẳng định `suggestionsEnabled` bị bỏ khỏi tầng snapshot. Người dùng tắt lớp cho tầng → GLB vẫn dựng len, rèm, đồ, mẫu chọn lại. Lớp tắt cũng không `reconcile` khi sửa `layer`, nên compiler còn dựng đề xuất có chủ đã xoá. | `packages/domain/model_export/snapshot.py:52-60`; `apps/api/model_export/snapshot_source.py:84`; `apps/api/model_export/tests/test_snapshot_source.py:50-55` | `suggestion_keys` trả `{}` khi `doc.get("suggestionsEnabled") is not True` (danh sách đã lưu luôn đi kèm khoá này). Test: tầng bật có đề xuất → tắt → snapshot không có `suggestions`, `snapshotSha` bằng bản không đề xuất. |
| 6 | P2 | LOG / TEST — "va chạm rule = 0" | `check_collisions` không có nơi gọi ngoài test (`grep -rn check_collisions apps packages` → chỉ `__init__` + test); docstring `placement.py:439` "The writer logs them" sai. Bất biến chỉ giữ nhờ `place()/fits()` và kiểm trên 3 ca vàng. Lỗ cụ thể: tắt giường P1 X → lượt sửa `layer` sau cho D3/D5 đặt vào chỗ X (luật sau chỉ thấy món hiệu lực, `reconcile.py:170`) → bật lại X bằng lượt chỉ đổi trạng thái (không `reconcile`) → X trở lại với `params` cũ, chồng món mới; compiler dựng cả hai. | `packages/domain/suggestions/rules/placement.py:437-451`; `apps/api/spatial_write/writer.py:505` | Giữ vết chân món `disabled` làm vật cản cho luật sau, hoặc `reconcile` khi một `item`/`rug` đổi giữa tắt/bật. Gọi `check_collisions` trong writer, log cả hai nhãn; va chạm `rule` → log lỗi (hoặc đánh `stale` món sau). Test writer: tắt giường → sửa layer → bật lại → 0 va chạm `rule`. |
| 7 | P2 | R-07 / "không dữ liệu ẩn trong compiler" | Tham số F2 nhân bản trong compiler: `CABINET_PLINTH_M=0.1`, `CABINET_COUNTERTOP_M=0.04`, `CABINET_GAP_M=0.004`, `CABINET_DUP_OVERLAP=0.5` chép từ `rule_params.json` F2 (`plinthMm 100, countertopMm 40, gapMm 4, dupOverlapPct 50`) nhưng `CabinetRunParams` không mang chúng. Đổi F2 → đổi `rulesetSha` và editor, **không** đổi GLB; compiler còn tự lọc dãy trùng, bỏ một đề xuất `active` đã lưu. | `packages/domain/model_export/defaults.py:113-118`; `packages/domain/model_export/plan/suggestions.py:480,489,498`; `packages/domain/suggestions/rule_params.json:68-69`; `packages/domain/suggestions/model.py:101-117` | Thêm `plinthMm/countertopMm/gapMm` vào `CabinetRunParams`, luật chép từ spec, compiler đọc từ `effective(s)`; xoá bốn hằng; lọc trùng chỉ còn là lưới an toàn có log (hoặc bỏ). |
| 8 | P2 | LOG / hợp đồng §3 "bản chính" | Đổi bản chính không có `migrate` → `base = None`: món người dùng đã tắt/điều chỉnh sống lại `active`, chỉ có dòng log `MAJOR_RESET`. Hợp đồng §3: bản cũ phải `stale` "luật đã đổi, xem lại". Test `test_major_bump__migrate_map_carries_state` khoá hành vi này (`state == "active"` sau khi người dùng `disabled`). Hiện ẩn vì mọi luật `majorSince=1`. | `packages/domain/suggestions/reconcile.py:163-167` | Bản có dữ liệu người dùng → đánh `stale` (`MAJOR_STALE`), giữ `state/override`; không đặt lại. Sửa test theo hợp đồng. |
| 9 | P2 | SEC (validate ở biên) / LOG | Giá trị `override` không kiểm theo lược đồ `kind`: `_check` chỉ kiểm `params` và **khoá** override; giá trị là `Scalar` bất kỳ → `{"heightMm": -500}`, `1e12`, `{"material": "x"}` được lưu; compiler dựng `{**params, **override}` ra lăng trụ âm/khổng lồ, kiểu sai chỉ hiện "skipped minor". | `packages/domain/suggestions/model.py:243-248`; `apps/api/spatial_write/schemas.py` (`SuggestionStateRowSchema.override`) | Trong `_check`: `schema.model_validate({**self.params, **self.override})`; lỗi → 422 `SUGGESTION_OVERRIDE_INVALID` ở `_applied_state`. Test giá trị âm/sai kiểu. |
| 10 | P3 | LOG — nhận nuôi | Không ép `origin ⇒ source == "human"` ở miền; client gửi `state:"adopted"` cho đề xuất không có món khớp → khi `layer` không đổi thì không `reconcile`, bản ghi ở `adopted` không `adoptedAs`, compiler bỏ qua → món biến mất khỏi bản xuất mà không có trong `layer`. Hai món cùng `origin.suggestionId`: món sau thắng im lặng. | `packages/domain/spatial/model.py:297-306,339`; `apps/api/spatial_write/writer.py:508` | Validator `origin ⇒ source=='human'`; `_applied_state` từ chối `adopted` khi `layer` không có món `origin` khớp; trùng `suggestionId` → 422. |
| 11 | P3 | LOG — `SUGGESTION_CAP` | `count=len(items)` là số **trước** `reconcile`; số thật (sau sinh + dọn) chỉ có trong chuỗi ngoại lệ. Test khẳng định đúng con số sai. | `apps/api/spatial_write/writer.py:519-520` | Mang `count` trên `SuggestionCapError`, dùng nó. |
| 12 | P3 | LOG — phép thử vừa hộp | `footprint_ratio` luôn ghép cạnh dài–cạnh dài, còn `long_axis_rotation` chỉ xoay khi cả hai ≥ 1,2 lần dài hơn rộng. Hộp 1000×900, mẫu 880×1000 (1,14): không xoay, tỉ lệ = 1,0 "vừa", dựng không xoay → sâu 1000 trong hộp 900, lấn 100 mm, sổ ghi `built`. | `packages/domain/model_export/assets.py:53-62,151-154`; `packages/domain/suggestions/rules/placement.py:78-82` | Khi không xoay, tính theo hướng thật `min(bw/mw, bd/md)`. |
| 13 | P2 | LOG — editor ≠ GLB | Compiler xoay ±90° đồ **của document** khi trục dài mẫu cắt trục dài hộp; sổ chỉ ghi `built info`. Editor (AppFront @14efdd78 `toPascal.ts:805-840`) không xoay → `rotationDeg` người dùng/AI đã duyệt khác giữa editor và GLB, không báo người dùng. | `packages/domain/model_export/assets.py:70-90,151-154` | Hoặc đưa phép xoay thành đầu ra luật (`rotationDeg` của F1/`modelPick`, nằm trong `suggestions[]`, editor cũng thấy), hoặc chỉ xoay đồ `source='ai'` chưa duyệt/sửa và ghi `degraded minor` có lý do. |
| 14 | P3 | CON — cache bản xuất | `compiler_version()` chỉ băm `model_export/**/*.py`; `assets.py` nay dùng `suggestions.rules.placement.footprint_ratio` → sửa luật tỉ lệ vẫn trả GLB cache tỉ lệ cũ. Tác giả tự ghi "Giới hạn" nhưng không có dòng nợ; thiếu test `test_compiler_version_covers_data_files` (hợp đồng §6). | `packages/domain/model_export/version.py:27-38`; `changes/M2-QL.md:82` | Đưa `suggestions/rules/placement.py` vào phép băm (hoặc chuyển `footprint_ratio` vào `model_export`), thêm test; hoặc dòng NO-M2-x. |
| 15 | P3 | R-07 | Hai bảng cỡ mẫu không ai so: luật đọc `suggestions/data/model_bbox.json`, compiler đọc `sizeMm` của `models.json` gói. Lệch → luật đặt món mà compiler bỏ "ngoài ±10 %". Phụ thuộc vòng gói: `suggestions/ruleset.py:17` nhập `model_export.snapshot.canonical_json`, `model_export/assets.py:29` nhập `suggestions.rules.placement`. | `packages/domain/suggestions/data/model_bbox.json`; `packages/domain/suggestions/ruleset.py:17`; `packages/domain/model_export/assets.py:29` | Test so hai bảng ở bước dựng gói (hoặc ghi `modelSizeMm` vào `params`); chuyển `canonical_json`/`footprint_ratio` vào một mô-đun chung không vòng. |
| 16 | P2 | R-34 sổ nợ | Chỉ NO-M2-1 có mã + phân loại. Các nợ tác giả tự nêu không có dòng phân loại `[merge]/[mặc định]/[phát hành]/[sau]`: 409 không liệt kê đổi `state/override` (finding 4), concept AI truyền `None` (hợp đồng §8 quyết định 5), `versions` không chụp lớp đề xuất, `compiler_version` không băm luật tỉ lệ, `reconcile` lỗi chặn mọi lượt lưu trên tầng bật (kể cả `merge` của pipeline; không bọc, không lùi về danh sách cũ). | `changes/M2-QL.md:51-53,82,97-101`; `apps/api/spatial_write/writer.py:509-520` | Đánh số NO-M2-2… với phân loại và lý do; nợ đi kèm finding 4/5 không được `[sau]`. |
| 17 | P3 | TEST | N52 không có ca âm (401 không phiên) trong `suggestion_rules/tests/test_routes.py`; 5b chưa chạy nên chưa biết case gate có đòi. Test hiệu năng chỉ hỏng trên 3 000 ms, nên trần 150 ms (§8 quyết định 6) không được cổng giữ (đo hiện 65,6 ms p95). | `apps/api/suggestion_rules/tests/test_routes.py`; `apps/api/spatial_write/tests/test_writer_suggestions.py:55-58,470-476` | Thêm ca 401; để trần test gần ngân sách (vd. 3× = 450 ms) hoặc ghi rõ trong nợ vì sao không. |
| 18 | P2 | MNT-05 | ~8.490 dòng logic (ngoài test/json/md) trong M2, > 400; gom 4 việc tách được (gói miền thuần, writer/wire/N52, compiler, tích hợp). | toàn nhánh M2 | Lần này không bắt tách; merge squash. |
| 19 | Nit | RULE-CODE / thông điệp tiếng Việt | Lý do người dùng đọc nhúng chữ ngoại lệ tiếng Anh: `f"tham số đề xuất không dựng được ({type(exc).__name__}: {exc})"`. | `packages/domain/model_export/plan/suggestions.py:792` | Lý do chỉ tiếng Việt; ngoại lệ vào log. |
| 20 | Nit | R-07 | Hai mẫu "vải" khác nhau: `materials.py:50` và `compile_plan.bpy:315` (bpy còn khớp `sheet` → `metal_sheet` được sheen). Ảnh nhiễu `roughVar` gieo theo khoá có `@roomId` → mỗi phòng đóng gói một ảnh 128 px riêng, trái ghi chú "dùng chung ảnh". | `packages/domain/model_export/materials.py:50`; `apps/worker/model_export/compile_plan.bpy:315` | Một danh sách, truyền cờ qua `plan.json`, bỏ `sheet`; gieo theo `instanceOf or key`. |
| 21 | Nit | R-10 | `placement.model_origin` (F0) không có nơi gọi thật (compiler dùng `anchor:"bbox"`), vẫn xuất ở `__init__` và được `model.py:156` mô tả là phép đổi. | `packages/domain/suggestions/rules/placement.py:85-99` | Xoá hoặc nối vào. |

Đã soát, không thành finding: mã 422 `SUGGESTION_UNKNOWN`/`SUGGESTION_OVERRIDE_INVALID`/`SUGGESTION_CAP` có tên, thông điệp
tiếng Việt, `field` chỉ số; id trùng → `VALIDATION`; `params`/`stale`/`createdRevision`/`suggestions` client gửi bị bỏ (có
test); `suggestionState` tối đa 2 000, override 1–16 khoá, regex id; `overridable` trong `rule_params.json` chỉ khoá vô hướng.
C09b: `body_sha256` gồm hai khoá mới → gửi lại không sinh lại, không tăng revision; lượt không đổi đi `_unchanged`. Trần
2 000: chỉ dọn `stale` không dữ liệu người dùng, cũ trước; bản có dữ liệu người dùng không bao giờ xoá (có test).
`adopted` mất món → `disabled`, bỏ `adoptedAs` (test cả qua API). N52 gắn `protected_router` (Đ\*, cần đăng nhập, không
dự án), ETag mạnh `"<rulesetSha>"` dùng `http_cache.etag_matches`, `W/`/danh sách/lệch có test, 304 không thân, giữ ETag;
`load_ruleset` `@cache`. Cache hình học: một ô theo định danh `layer`, giữ tham chiếu mạnh, không `await` trong
`reconcile`. Compiler: chỉ dựng từ `floor["suggestions"]` sắp theo id; `disabled/stale` không dựng và vào
`appSuggestions.disabled`; `adopted` chỉ đếm; mọi bản ghi đề xuất kẹp ≤ `minor` (`_cap`, cả phía Blender); thứ tự mẫu
`model.src` > override `modelId` > `params.modelId` > mặc định; `snapshotSha` giữ nguyên cho document không đề xuất (test ghim
giá trị M1); `EXECUTOR_SHA` đổi cùng `.bpy`; mọi `type: ignore` có mã. Wire N15 `floorRevisions` và N16 cùng ba khoá (có
test), `FurnitureOut.origin`. openapi `--compare` = 0.

## Sổ nợ

- NO-M2-1 (CHARTER) **[merge] mở** → chặn merge (R-38), cùng NO-M1-9 mà nhánh mang theo từ M1.
- Nợ chưa phân loại: finding 16 (P2, R-34).
- Finding 4–9 phải sửa hoặc (với P2) có dòng nợ trước merge.

## Điểm

| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| SEC | 25% | 4 | 1,00 |
| CON | 15% | 3 | 0,45 |
| LOG | 15% | 3 | 0,45 |
| PERF | 10% | 5 | 0,50 |
| RES | 10% | 4 | 0,40 |
| DB/API | 10% | 2 | 0,20 |
| TEST | 7% | 3 | 0,21 |
| OBS/OPS | 5% | 4 | 0,20 |
| MNT | 3% | 3 | 0,09 |

Tổng: 3,50 / 5 (không quyết định: còn 5 P1 chưa waiver)

## PHÁN QUYẾT: REQUEST CHANGES

Lý do: gói miền thuần tốt — `reconcile` tất định, id ổn định, compiler chỉ dựng từ `suggestions[]` đã lưu, trần ±10 % và
"đề xuất hỏng không quá minor" đúng, đo hiệu năng trong ngân sách, openapi khớp, mypy đích sạch, `docs/charter/*` không
bị sửa. Nhưng chưa được vào `main`: chưa có cổng đầy đủ thoát 0 (F1); hợp đồng N52/#35/N15/N16 chưa duyệt mà vẫn qua cổng
nhờ shim (F2) và `APPFRONT_SHA` ghim nhánh FE chưa vào `master` (F3) — cả hai kéo dài P1 của M1; và hai lỗ vi phạm luật
"không âm thầm đổi lựa chọn của người dùng" (F4 mất cập nhật trạng thái khi nền cũ, F5 tắt lớp vẫn xuất). Để được duyệt:

1. F4: 409 (hoặc ghi log thực thể `suggestion`) khi nền cũ + `suggestionState`; test hai người/hai tab.
2. F5: snapshot bỏ đề xuất khi `suggestionsEnabled` không bật; test.
3. F6–F9, F13: sửa (khuyến nghị: F6 vật cản `disabled`/gọi `check_collisions`, F7 đưa tham số F2 vào `params`, F8 `stale`
   thay reset, F9 kiểm giá trị override), hoặc ghi dòng NO-M2-x có phân loại và lý do (F16).
4. F2 + F3 (cùng điều kiện M1): chủ dự án duyệt `charter-M2-QL.md` (+ `charter-M1-01.md`), commit charter riêng có
   `Charter-Approved:`, gỡ shim, xoá tệp chờ; FE M1+M2 vào `origin/master`, ghim lại `APPFRONT_SHA`.
5. F1: `bash tools/verify/run.sh verify` đầy đủ @HEAD mới thoát 0 (mọi bước 0–8, độ phủ ≥ 90/90 mỗi gói chạm); merge squash.

Lượt 2: diff vòng sửa chạm `apps/api/spatial_write`, `packages/domain`, có thể migration (F4 phương án bền) và `tools/**`
(gỡ shim) → R-33b (2): phạm vi đầy đủ.
