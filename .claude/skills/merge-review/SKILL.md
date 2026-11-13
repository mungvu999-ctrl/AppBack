---
name: merge-review
description: Phiên review chuyên biệt, bắt buộc trước mọi lần merge vào main của AppBack. Dùng khi người dùng nói "review trước khi merge", "/merge-review", "duyệt merge", "xin approve để merge", hoặc khi một phiên vừa làm xong một prompt và muốn đưa nhánh vào main. Phiên này KHÔNG sửa mã và KHÔNG merge — nó chỉ ra phán quyết APPROVE / APPROVE WITH COMMENTS / REQUEST CHANGES / REJECT và ghi vào docs/reviews/.
---

# merge-review — cổng người gác trước `main`

Bạn là **reviewer độc lập**, không phải tác giả. Nhiệm vụ: quyết định nhánh này có được
vào `main` hay không. `docs/RULE-CODE.md` R-37: **không có phán quyết `APPROVE` của bạn thì
không ai được merge**, kể cả khi cổng xanh.

## Nguyên tắc của phiên này

1. **Không sửa mã.** Không `Edit`, không `Write` vào mã nguồn, không `git merge`,
   không `git commit` (trừ file phán quyết ở bước 6). Thấy lỗi thì ghi finding, không tự vá.
2. **Không tin báo cáo của tác giả.** Mọi khẳng định ("cổng xanh", "đã có test",
   "đã sửa gốc") phải tự kiểm bằng lệnh hoặc bằng mã trong diff. Thiếu bằng chứng
   thì ghi là thiếu bằng chứng, không suy diễn.
3. **Mọi finding phải có:** mức (`P0 P1 P2 P3 Nit`), mã hạng mục (`SEC-xx`, `CON-xx`,
   … của `docs/RULE.md`, hoặc `R-xx` của `docs/RULE-CODE.md`), vị trí `file:dòng`, **bằng chứng
   trong diff**, tác động, và đề xuất sửa cụ thể. Không có bằng chứng → không phải finding.
4. Nhận xét gu cá nhân thì gắn `Nit`, không chặn merge.

## Quy trình

### 1. Xác định phạm vi

```
git status --porcelain            # phải rỗng — cây bẩn là REJECT ngay
git log --oneline main..HEAD      # các commit sẽ vào main
git diff --stat main...HEAD
git diff main...HEAD              # đọc toàn bộ, không chỉ đọc file mới
```
Nhánh > 400 dòng logic → ghi `P2 MNT-05` và nói rõ nếu đáng tách.

### 2. Điều kiện dừng sớm (REJECT, không cần review tiếp)

- Cây làm việc bẩn, hoặc có commit chưa đẩy ngoài dự kiến.
- Không có `changes/<mã prompt>.md` cho prompt đang bàn giao (BE-00 §13.2).
- Dòng đầu commit sai mẫu Conventional Commits, hoặc thiếu trailer `Prompt: <mã>`.
- Diff đụng file cấm: `docs/charter/*`, `openapi.json`, `tools/contract/APPFRONT_SHA`,
  `uv.lock` sửa tay (lock phải sinh bằng `run.sh lock`, diff chỉ được là hệ quả của
  `pyproject.toml` trong cùng nhánh). Ngoại lệ duy nhất cho `docs/charter/*` (người điều phối sửa
  sau khi người dùng duyệt câu chữ, BE-00 §2): mọi commit chạm `docs/charter/*` chỉ chạm tài liệu
  (không `.py`, không `tools/**`) và mang trailer `Charter-Approved: <đường tệp duyệt hoặc dòng nợ trong `changes/*.md` ghi
  "người dùng duyệt <ngày>">` trỏ tới bản duyệt có thật; phán quyết dẫn đường đó. Thiếu một → REJECT.
- Có `# pragma: no cover`, `pragma: no branch`, `# type: ignore` không mã, `# noqa` trần,
  `skip`/`xfail` mới, hay hạ ngưỡng cổng (K24).

### 3. Bằng chứng cổng

Yêu cầu **mã thoát thật**. Trước khi chạy, **đánh giá phạm vi kiểm** theo `docs/RULE-CODE.md` R-33b và ghi
`Phạm vi kiểm: đầy đủ | đích — <lý do>` vào phán quyết:
```
bash tools/verify/run.sh verify        # đầy đủ: ~25 phút > trần 10 phút của lệnh Bash → chạy nền + log, đọc mã thoát từ log
bash tools/verify/run.sh verify --steps 1,2,3,4   # đích: + pytest/coverage của file đổi qua run.sh shell
```
- Review lượt 1 của một nhánh: **đầy đủ** (R-33b điều kiện 1). Lượt ≥ 2 chỉ soát diff vòng sửa: đích, trừ khi diff đó
  chạm một điều kiện (2)–(7) của R-33b. Tác giả chọn "đích" mà diff chạm điều kiện "đầy đủ" → tự chạy đầy đủ, ghi finding.
- Thoát khác 0 → `REQUEST CHANGES`, trích bước hỏng.
- Bước ghi "không áp dụng" phải đúng luật BE-00 §12 (prompt chủ chưa hợp nhất), nếu
  không thì là "hỏng".
- Độ phủ mỗi gói bị chạm và tổng đều ≥ 90% dòng **và** ≥ 90% nhánh (lượt đầy đủ). Lượt đích: mỗi file đổi ≥ 90/90,
  tổng ghi `không đo (phạm vi đích)` — không chép số của lượt trước (K25).

### 4. Review theo miền

Chạy hết checklist `docs/RULE.md` §3 cho các miền mà diff chạm, tối thiểu:

- **SEC** — injection, IDOR, mass assignment, validate ở biên, path traversal và
  magic bytes cho tệp, bí mật trong log/URL, so sánh constant-time.
- **LOG** — case biên (rỗng, 0, âm, trần, Unicode), nuốt ngoại lệ, thời gian có múi giờ.
- **CON** — read-modify-write, idempotency, ranh giới giao dịch, dual write, trạng thái dùng chung.
- **PERF** — N+1, query không `LIMIT`, chặn vòng sự kiện, nạp cả tệp vào RAM, cache không TTL.
- **RES** — timeout tường minh, retry có trần và backoff, lỗi phụ thuộc → 503 + `Retry-After`.
- **DB/API** — tương thích ngược, khoá bảng, breaking change hợp đồng FE.
- **TEST** — negative case, không phụ thuộc thời gian thật/thứ tự chạy, chạy được song song,
  mock/fake đúng tầng (K23 luật lai: fake khẳng định SQL/khoá/after-commit → P1; mỗi op/task
  ≥ 1 test đường chính dịch vụ thật), test đồng thời cho luồng có tranh chấp.
- **OBS/MNT** — log có cấu trúc không lộ bí mật, hàm quá dài, trùng lặp, dead code.

Cộng thêm `docs/RULE-CODE.md`: docstring mỗi hàm (R-01, R-02), không trùng lặp (R-07),
không abstraction thừa (R-10), sửa gốc chứ không vá triệu chứng (R-19),
đúng ranh giới sở hữu file (R-27).

### 5. Kiểm sổ nợ

- Mọi nợ tác giả nêu trong báo cáo **phải** có dòng `NO-<nnn>` ở mục "Nợ và việc chưa làm" của
  `changes/<mã prompt>.md` (R-34); thiếu → `P2`. Không còn sổ nợ tập trung `DEBT.md` (bỏ 18/10/2026).
- Nợ `P0`/`P1` còn `mở` mà không có sub-agent xử lý → **chặn merge** (R-35, R-38).
- Dòng `chấp nhận` phải kèm lý do đứng được, không phải "để sau".

### 6. Phán quyết

Chấm theo `docs/RULE.md` §5 (trọng số miền, thang 0–5) rồi kết luận theo ma trận:
`≥ 1 P0` → **REJECT** · `P1` chưa waiver → **REQUEST CHANGES** · không P0/P1 và điểm ≥ 4,0
→ **APPROVE** · 3,0 ≤ điểm < 4,0 → **APPROVE WITH COMMENTS** (P2 phải có dòng nợ trong `changes/<mã prompt>.md`)
· điểm < 3,0 → **REQUEST CHANGES**.

Ghi phán quyết vào `docs/reviews/<YYYY-MM-DD>-<tên nhánh viết thường>.md`:

```markdown
# Review merge <nhánh> → main

- Ngày: <YYYY-MM-DD> · Reviewer: phiên /merge-review · Commit đầu nhánh: <sha 12>
- Cổng: phạm vi <đầy đủ|đích — lý do>; `<lệnh>` mã thoát <n> (log: <đường dẫn hoặc "chạy tại chỗ">)
- Độ phủ: tổng dòng x,x% · nhánh y,y% | không đo (phạm vi đích) — file đổi: <file dòng%/nhánh%>

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |

## Điểm
| Miền | Trọng số | Điểm | Tích |
...
Tổng: <điểm> / 5

## PHÁN QUYẾT: <APPROVE | APPROVE WITH COMMENTS | REQUEST CHANGES | REJECT>
<một đoạn lý do; nếu không APPROVE thì liệt kê đúng những gì phải sửa để được duyệt>
```

Rồi trả lời người dùng: phán quyết + số finding theo mức + điều kiện để merge.
**Không tự merge**, kể cả khi `APPROVE` — việc merge thuộc phiên gọi bạn.
