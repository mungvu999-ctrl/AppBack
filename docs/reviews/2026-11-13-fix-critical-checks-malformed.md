# Review merge fix/critical-checks-malformed → main

- Ngày: 2026-11-13 · Reviewer: phiên /merge-review · Commit đầu nhánh: `0088e5bd5f36`
- PR: mungvu999-ctrl/AppBack#72 · gốc nhánh `66cebe8e` (merge-base với `origin/main` = `6fedd32d`)
- Cổng: phạm vi **đầy đủ** (R-33b điều kiện 1 — lượt review đầu của nhánh). Bằng chứng là CI GitHub Actions
  chạy đúng `0088e5bd`: run `43418190105`, **24/24 check `SUCCESS`** (`lint`, `typecheck`, `unit`,
  `unit-shards 1–2`, `integration`, `integration-shards 1–5`, `ml`, `ml-shards 1–3`, `contract`, `perf`,
  `build`, `coverage`, `changes`, `commits`, CodeQL ×3). Không chạy cổng cục bộ: luật phiên là cổng đầy đủ
  chạy trên CI, không chạy 17 phút trong phiên.
  Log: https://github.com/mungvu999-ctrl/AppBack/actions/runs/43418190105
- Độ phủ (job `coverage`, `tools.coverage_gate` **đạt**): tổng dòng **99,32 %** · nhánh **97,54 %**;
  gói bị chạm `apps/api/spatial_read`: dòng **98,99 %** · nhánh **99,28 %**. Cả hai ≥ 90/90.
- Cây làm việc: `git status --porcelain` rỗng. Một commit vào `main`, không có commit lạ.
- Commit: `fix(spatial-read): malformed criticalChecks must not read as ready` (66 ký tự, đúng Conventional
  Commits), trailer `Prompt: ML-ACC-R10` có. Hồ sơ `changes/ML-ACC-R10-CRITICAL-CHECKS.md` có, không trùng
  tên với `changes/ML-ACC-R10-CHECKS.md` / `-ACCEPT.md` đã ở `main`.
- File cấm: **không chạm**. Diff đúng 3 tệp — `apps/api/spatial_read/readiness.py`,
  `apps/api/spatial_read/tests/test_readiness.py`, `changes/ML-ACC-R10-CRITICAL-CHECKS.md`.
  Không `docs/charter/*`, không `openapi.json`, không `tools/contract/APPFRONT_SHA`, không `uv.lock`.
- Không có `# pragma: no cover`, `pragma: no branch`, `# type: ignore` trần, `# noqa` trần, `skip`/`xfail`
  mới, không hạ ngưỡng cổng nào (K24).

## Năm câu hỏi được đặt riêng

**1. Đổi ca test đang xanh (`bad-list`) có hợp lý không?** — Hợp lý. Ca cũ
`{**CLEAN, "criticalChecks": "x"} → reasons ()` khẳng định chính hành vi hỏng: một lượt ghi nghi vấn ở dạng
không đọc được thì readiness trả `"ready"`. Không có nơi nào dựa vào `()` cho dữ liệu hỏng:
`_critical_checks` là hàm private, `grep -rn "_critical_checks"` toàn repo cho đúng **một** chỗ gọi là
`readiness_of` (`readiness.py:166`), không có import ra ngoài module. `test_routes_layer.py:244` dùng
`criticalChecks: ["MULTIPLE_PLANS"]` — list chuỗi hợp lệ, không bị ảnh hưởng. Ca `null-is-valid` mới giữ lại
đúng phần hành vi cũ đáng giữ (`null`/vắng khoá = `()`), nên việc đổi không nuốt mất ý đồ nào.

**2. `checks or read is None` và `checks = read or ()` có đúng ở mọi nhánh không?** — Đúng cả ba nhánh.
`read = ()` (hợp lệ, không có mã) → `checks = ()`, `read is None` là `False` → **không** `geometrySuspect`,
giống hệt lượt cũ. `read = None` (hỏng dạng) → `checks = ()` nhưng `read is None` là `True` →
`geometrySuspect`. `read` khác rỗng → `checks = read` → `geometrySuspect`. `read or ()` an toàn vì giá trị
falsy hợp lệ duy nhất là `()`, mà `()` ánh xạ về chính `()`. `_integrity_critical(..., trusted=read is not
None)` cũng đúng: `trusted=False` → bỏ qua phép trừ `count > len(checks)` và theo `hasCritical`, nên tóm tắt
`CLEAN` (hasCritical `False`) không sinh `criticalIssues` thừa, còn `CRITICAL` thì sinh — ca
`bad-list-with-integrity` chốt đúng chỗ này.

**3. `ReadinessOut.checks` vẫn `None` khi hỏng dạng — client nào coi đó là bất thường không?** — Không,
đã kiểm tận nơi ở AppFront đúng SHA hợp đồng đang ghim (`tools/contract/APPFRONT_SHA` = `991caefe`):

- `src/api/schemas/spatialLayer.ts` @991caefe: `ReadinessSchema` có `'geometrySuspect'` trong enum và
  `checks: z.array(z.string().min(1)).optional()`; `.transform` còn **chủ động bỏ** `checks` khi rỗng. Nên
  `{state:"needsReview", reasons:["geometrySuspect"]}` không kèm `checks` parse qua bình thường.
- `src/screens/pipeline/ProcessingScreen/processingGate.ts:93` @991caefe:
  `if (reason === 'geometrySuspect' && checks.length > 0)` mới tách một dòng cho mỗi mã; không có mã thì rơi
  về câu chung `REVIEW_REASON_SENTENCE.geometrySuspect`. Suy biến êm, không phải trường hợp bất thường.
- Docstring `ReadinessOut.checks` (`wire.py:298`) viết "chỉ có khi có `geometrySuspect`" — chiều đó vẫn đúng
  (có `checks` ⇒ có `geometrySuspect`); nhánh này chỉ phá chiều ngược (trước đây `geometrySuspect` luôn kèm
  `checks`), mà FE không dựa vào chiều ngược. Xem Nit #5.

**4. Có đổi hợp đồng API hay số đo nào không?** — Không. `wire.py` và `docs/contracts/openapi.json` không
nằm trong diff; `ReadinessOut.checks` đã là `tuple[str, ...] | None` từ trước. Không thêm/bớt khoá, không đổi
enum lý do. Job `contract` của CI xanh. Mọi tóm tắt hợp lệ (list chuỗi, `[]`, `null`, vắng khoá) ra kết quả
y hệt trước — chỉ các giá trị mà pipeline không bao giờ sinh mới đổi hành vi, nên không có số đo nào dịch.

**5. Có lượt chạy thật nào từng ghi `criticalChecks` hỏng dạng không?** — **Không tìm thấy; đây là phòng ngừa.**
Hai bằng chứng độc lập:

- Chỉ có **một** chỗ ghi `quality_summary` (`record_quality_summary` ở `pipeline_orchestrate/pins.py:79`,
  gọi từ `pipeline_quality/service.py:231`), và `_summary` dựng `criticalChecks` bằng
  `[check.code for check in report.checks if check.critical]` với `GeometryCheck.code: str`
  (`pipeline_quality/checks.py:88`) → luôn là `list[str]`.
- Truy vấn **DB f14 đang chạy** (`f14-postgres-1`, chỉ đọc): 1 243 dòng `pipeline_run_models` có
  `quality_summary`, 497 dòng có khoá `criticalChecks`, **0** dòng `jsonb_typeof(...) <> 'array'`, **0** dòng
  mảng có phần tử không phải chuỗi. `scaleChecks` cũng 0 dòng hỏng.
- **Không kiểm được production**: phiên này không có quyền truy cập cơ sở dữ liệu production, nên không
  khẳng định được gì về dữ liệu ở đó — chỉ nói được là trên f14 không có.

Kết luận: sửa phòng ngừa, cùng lớp với #70, không có sự cố thật đứng sau. Điều đó **không** làm nó thành
thừa — nó bịt một đường im lặng trả "ready", là hướng sai nguy hiểm nhất của màn Duyệt tầng.

## Finding

| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | MNT-05 / R-33b | **Nhánh chưa gộp được vào `origin/main` và bằng chứng cổng không phải cây sẽ vào `main`.** Nhánh dựng trên `66cebe8e`; `origin/main` đã đi tiếp tới `6fedd32d` với PR #70 và #71, cả hai đều sửa chính `readiness.py`. GitHub báo `mergeable: CONFLICTING`, `mergeStateStatus: DIRTY`; `git merge-tree origin/main HEAD` xác nhận **một** xung đột. CI xanh là xanh cho `0088e5bd`, không phải cho cây sau gộp. | vùng xung đột trong tree gộp `b4fb5168`, docstring `readiness_of` (`apps/api/spatial_read/readiness.py:200` của tree đó) | Rebase lên `origin/main` rồi đẩy lại, chờ CI xanh **lần nữa** trước khi merge. Xung đột là **một dòng docstring** của `readiness_of`, giải bằng cách **giữ cả hai dòng** (dòng `scale_accepted` (K4)… của #71 và dòng `criticalChecks` hỏng dạng… của nhánh này); phần mã `_critical_checks`/`_integrity_critical`/thân `readiness_of` tự gộp sạch và đúng (đã đọc tree `b4fb5168`). |
| 2 | P3 | R-19 | Cùng lớp lỗi "JSONB hỏng dạng" còn **một** chỗ chưa bịt trong chính tệp này, và nó tệ hơn: `_is_classic` gọi `ModelRef.model_validate(value)` thẳng trên `run.pinned[family]`, một dòng `pinned` hỏng dạng sẽ ném `ValidationError` không ai bắt → N16 trả 500 thay vì suy biến. (Trên f14 hiện 0 dòng `pinned` không phải object, nên chưa phải sự cố.) | `apps/api/spatial_read/readiness.py:67` | Ngoài phạm vi nhánh này — tác giả **đã** nêu đúng khoảng nợ này ở dòng "Nợ" của `changes/ML-ACC-R10-CRITICAL-CHECKS.md`. Giao một FIX riêng rà `readiness.py` + `assemble.py` theo cùng lớp lỗi. |
| 3 | Nit | R-10 | `_integrity_critical(..., *, trusted: bool = True)` — mặc định `True` không ai dùng: chỗ gọi duy nhất luôn truyền `trusted=read is not None` tường minh. Linh hoạt chết. | `apps/api/spatial_read/readiness.py:130` | Bỏ `= True`, để `trusted` là tham số keyword bắt buộc. Không chặn merge. |
| 4 | Nit | OBS-01 | Dữ liệu hỏng dạng giờ bị nuốt êm thành `geometrySuspect`, không có log nào để người vận hành biết DB đang có dòng hỏng. | `apps/api/spatial_read/readiness.py:166` | Không sửa trong nhánh này: `_checks_of` của #70 cũng không log, giữ hai hàm láng giềng cùng cách cư xử có giá trị hơn. Nếu muốn có dấu vết thì làm một lượt cho cả hai. |
| 5 | Nit | — | Docstring `ReadinessOut.checks` "chỉ có khi có `geometrySuspect`" giờ đọc dễ nhầm thành hai chiều. | `apps/api/spatial_read/wire.py:298` | Thêm nửa câu "`geometrySuspect` có thể không kèm `checks` khi lượt ghi mã hỏng dạng". `wire.py` không trong diff nên không đòi sửa ở đây. |

Không có P0. Không có P1.

## Ghi chú quy trình (không phải finding của nhánh)

`RULE.md`, `RULE-CODE.md` và `DEBT.md` mà CLAUDE.md và skill này trỏ tới **không có trong repo**
(`git ls-tree -r origin/main` không có, đĩa cũng không có ở `F:/App/AppBack`). Hệ quả cho lượt review này:
(a) bước 5 "kiểm sổ nợ" không kiểm được — dòng "Nợ" của hồ sơ tác giả không có sổ để đối chiếu, nên **không**
tính là thiếu sót của tác giả; (b) bảng điểm dưới đây dùng các miền của skill §4 với trọng số ghi rõ tại chỗ,
không phải trọng số của `RULE.md` §5. Nên khôi phục ba tệp đó vào repo.

## Điểm

| Miền | Trọng số | Điểm | Tích |
|---|---|---|---|
| LOG (case biên: chuỗi, số, dict, list lẫn, `null`, vắng khoá, `[]`) | 0,30 | 5,0 | 1,500 |
| TEST (6 ca mới, ca sai được sửa đúng hướng, nhánh `REVIEWED` vẫn chốt) | 0,25 | 4,5 | 1,125 |
| DB/API (không đổi hợp đồng; FE @991caefe suy biến êm, đã kiểm mã) | 0,20 | 5,0 | 1,000 |
| MNT (nhánh chưa rebase; `trusted=True` thừa; hàm ngắn, không trùng lặp, docstring đủ R-01/R-02) | 0,15 | 3,5 | 0,525 |
| OBS (docstring giải thích rõ luật; không có log cho dữ liệu hỏng) | 0,10 | 4,0 | 0,400 |

Tổng: **4,55 / 5**

SEC, CON, PERF, RES: diff không chạm (hàm thuần, không I/O, không truy vấn mới, không trạng thái dùng chung) —
không chấm.

## PHÁN QUYẾT: APPROVE (4,55 / 5)

Sửa đúng gốc, đúng chỗ, và đúng cái lỗi đáng sửa: trước đây một lượt CÓ ghi nghi vấn hình học nhưng ghi hỏng
dạng thì `geometrySuspect` biến mất và readiness trả thẳng `"ready"` — hướng sai nguy hiểm nhất, vì màn hình
nói tầng dùng được trong khi máy đã nêu nghi vấn. Cách sửa (`None` = "còn nghi vấn, không đọc được mã nào")
bám đúng luật `_checks_of` mà #70 đã đặt cho `scaleChecks` ở hàm láng giềng cùng tệp, nên hai hàm giờ cư xử
như nhau thay vì mỗi hàm một kiểu. Việc đổi ca test `bad-list` là **sửa một khẳng định sai**, không phải che
hành vi cố ý: `_critical_checks` chỉ có một chỗ gọi và không ai ngoài nó dựa vào `()`. Không đổi hợp đồng API,
không đổi số đo, không chạm file cấm, mọi tóm tắt hợp lệ giữ nguyên hành vi. Cổng đầy đủ là CI 24/24 xanh với
độ phủ tổng 99,32/97,54 và `apps/api/spatial_read` 98,99/99,28. Không có P0, không có P1.

**Điều kiện cơ học trước khi merge (finding #1, không phải lỗi mã):** nhánh đang `CONFLICTING` với
`origin/main` vì #70 và #71 đã vào trước. Phải rebase lên `origin/main`, giải **một** xung đột docstring bằng
cách giữ cả hai dòng, đẩy lại và chờ CI xanh trên cây sau rebase. Merge khi và chỉ khi lượt CI đó xanh —
phán quyết APPROVE này áp cho nội dung thay đổi, và nội dung đó không đổi qua rebase (phần mã tự gộp sạch,
đã kiểm bằng `git merge-tree`).

Nit #3 (bỏ `= True`) và Nit #5 (nửa câu docstring ở `wire.py`) làm hay không tuỳ tác giả, không chặn merge.
Finding #2 đi tiếp thành một FIX riêng theo đúng dòng "Nợ" tác giả đã ghi.
