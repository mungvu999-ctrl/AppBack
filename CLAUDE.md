# AppBack — hướng dẫn cho worker

**Đọc trước khi viết một dòng mã, theo đúng thứ tự:**
0. `docs/RULE-CODE.md` — luật viết mã bắt buộc cho mọi phiên (docstring mỗi hàm, không
   trùng lặp, phủ hết case, tối ưu ngay lúc viết). `docs/RULE.md` là bảng chấm review sau đó.
1. `docs/charter/BE-00.md` — hiến chương: stack, ranh giới import, quy ước dây W1–W24, lỗi, xác thực, migration, việc nền, cổng verify.
2. `docs/charter/BE-KFM.md` — lỗi agent hay mắc (K01–K34); khối [9] của prompt trích lại phần liên quan.
3. `docs/charter/ENV.md` — môi trường đã đo (mount, biến môi trường, trần chạy song song).
4. `docs/charter/CASE.md` §2.3 — thuật toán `case_gate.py` (nguồn của case bắt buộc).
5. `docs/charter/BE-BIND.md`, `docs/charter/HOP-DONG-MOI.md` — hợp đồng FE, chỉ đọc phần khối [2] của prompt trỏ tới.

Mâu thuẫn giữa prompt và hiến chương → làm theo hiến chương, ghi vào "Lệch khỏi prompt" của báo cáo.

## Chạy cổng

Máy Windows **không có `just`, không có uv dùng được**. Lệnh thật:

```
bash tools/verify/run.sh <việc>
```

| Việc | Làm gì | Ai chạy |
|---|---|---|
| `verify [--steps 1,2,4]` | 8 bước (BE-00 §12) trong container Linux. **Một lượt một lúc trên cả máy**: lượt sau tự xếp hàng ở khoá cổng (`tools/verify/gate_lock.sh`, in "khoá cổng: chờ…") — ~8,4 GiB/lượt, hai lượt chồng từng làm treo Docker. **Không tự viết vòng chờ** `docker ps \| grep verify`: cứ gọi `run.sh`, nó tự chờ đúng lượt (PERF-11) | trước khi báo xong; đầy đủ hay chỉ bước/test đích theo đánh giá phạm vi kiểm (`docs/RULE-CODE.md` R-33b) |
| `verify --fast` | **cổng nhanh**: ruff, lint-imports, mypy cache ấm, pytest các **tệp test bị ảnh hưởng** so với `main` (đồ thị import + fixture, PERF-04), độ phủ đơn vị bị chạm, bước 6/7/8 chỉ khi đầu vào đổi, không 5b | mỗi lần sửa; `verify` đầy đủ **chỉ trước merge** (PERF-03) |
| `lock` | `uv lock` (không `--upgrade`), chép `uv.lock` ra worktree | worker khi thêm thư viện ngoài bảng BE-00 §13.1 |
| `shell` | bash trong container, để gỡ lỗi (`run.sh shell < lệnh.sh` chạy không tương tác). `--fast` và `shell` giữ khoá cổng **dùng chung**: chạy cùng nhau được, không chạy cạnh cổng đầy đủ — đừng giữ shell tương tác lâu, nó chặn cổng của phiên khác | worker |
| `hold -- <lệnh>` | chạy lệnh trên host khi giữ khoá cổng một mình (dựng ảnh web f14, build nặng), xếp hàng chung với `verify` | mọi phiên |
| `openapi` | xuất `openapi.json` hợp nhất | **chỉ người điều phối** |
| `merge-heads <tên>` | tạo revision merge hai head | **chỉ người điều phối** |
| `gc` | dọn venv/mypy cache của worktree đã xoá | người điều phối |

`justfile` chỉ là bí danh cho Linux/CI, gọi lại đúng `run.sh`.

## Gốc import

`PYTHONPATH` = gốc repo. `packages/`, `apps/` là gói Python thường (có
`__init__.py`), import bằng `packages.<gói>...`, `apps.<app>.<module>...`.
Không cài mã nội bộ thành wheel, không cần `pip install -e`.

## Sở hữu file

Mỗi prompt sở hữu **thư mục một cấp của riêng nó** dưới `apps/api/`,
`apps/worker/`, `apps/ml/` (BE-00 §2). File của prompt khác: chỉ đọc, gọi qua
hàm công khai, không sửa — cần sửa thì ghi "Nợ và việc chưa làm", người điều
phối giao FIX cho đúng chủ (K27).

**Không được sửa** (B0-01 [7]):
- `docs/charter/*`, `docs/contracts.toml`, `uv.lock` (trừ khi tự thêm dòng rồi
  `just lock`), `openapi.json`, `tools/contract/APPFRONT_SHA` — chỉ người
  điều phối.
- `pyproject.toml` gốc, `.importlinter`, `conftest.py` gốc, `tools/verify/*`,
  `tools/charter.py` — không prompt nào khác B0-01 sửa các file này.
- Không tạo `conftest.py` lồng, không tạo file cấu hình công cụ riêng
  (`.ruff.toml`, `pytest.ini`, `.coveragerc`, …) — `coverage_gate.py` cấm
  (BE-00 §12).

## Thêm fixture, factory

- Fixture dùng chung của module bạn: **một file mới**
  `packages/testing/fixtures/<việc>.py` — `conftest.py` gốc tự dò và nạp theo
  tên, không sửa `conftest.py`.
- Factory dữ liệu test: `packages/testing/factories/<module>.py`.
- Không nhập `packages.testing` từ mã không phải test (K, BE-01 [9]).

## Trước lượt verify đầu tiên

Tạo `changes/<mã prompt>.md` (3–10 dòng) — các cổng có điều kiện đọc file
này để biết bước nào "không áp dụng" (BE-00 §13.2, §12).

## Nợ và merge (`docs/RULE-CODE.md` §6)

- Nợ phát hiện trong phiên → một dòng `NO-<nnn>` ở mục "Nợ và việc chưa làm" của
  `changes/<mã prompt>.md` trước khi báo xong (R-34). Không còn sổ nợ tập trung:
  `DEBT.md` đã bỏ 18/10/2026 (`6b2e4d52`); mã kế tiếp =
  `git grep -ohE 'NO-[0-9]+' -- changes/ | sort -t- -k2 -n | tail -1` cộng một.
- Xong task mà còn nợ mở → giao sub-agent tìm nguyên nhân gốc và sửa (R-35).
- Vào `main` **chỉ** qua nhánh + phiên review riêng: `/merge-review` (skill ở
  `.claude/skills/merge-review/`). Phán quyết lưu ở `docs/reviews/`. Không `APPROVE`
  thì không merge, kể cả khi cổng xanh (R-37).

## Git: commit và nhánh (BE-00 §13.2)

- Commit: `<type>(<scope>): <tóm tắt tiếng Anh>` ≤ 72 ký tự, cuối thân có
  trailer `Prompt: <mã>` (FIX thêm `Fix: FIX-<nnn>`). Câu `git commit -m "<mã>: …"`
  trong khối [11] của prompt: viết lại theo mẫu này.
- Hook `.githooks/commit-msg` chặn dòng đầu sai mẫu; người điều phối bật một lần
  bằng `git config core.hooksPath .githooks`. Không `--no-verify` (K24).
- Nhánh `<loại>/<mã viết thường>-<mô tả>`, ví dụ `feature/b0-02-core-errors-logging-ids`.
  Người điều phối gộp vào `main` bằng squash.

## Báo cáo trạng thái cổng (E.10)

Bảng chỉ in trạng thái lấy từ **mã thoát thật**: `đạt`, `hỏng`, `chưa chạy`
(bước sau một bước hỏng), `không áp dụng` (thiếu file điều kiện của prompt
chưa hợp nhất). **Cấm** báo "đạt" cho bước chưa chạy (K25).

## Case, coverage, migration

- Test tên `test_<operationId>__<caseid>`; task/lịch: `test_<tên hàm>__J0x`.
- Vết case (`CASE_TRACE_FILE`, B0-06 ghi): JSON Lines `{"test", "op", "status", "code"}` — docstring `tools/case_gate.py`.
- Độ phủ: mỗi gói/app bị chạm và tổng đều ≥ 90% dòng **và** ≥ 90% nhánh
  (hai số riêng). Không `# pragma: no cover`, không hạ ngưỡng (K24).
- Migration theo BE-00 §6.1 (expand/contract); `python -m packages.db.new_revision`
  để tạo revision mới (B0-03).

## Cấm tuyệt đối (BE-01 [9], nhắc lại)

Mock/fake đúng tầng (K23, luật lai): dịch vụ thật cho SQL, constraint, khoá,
đồng thời, after-commit, migration, hợp đồng; fake được cho logic phía trên;
không SQLite; test chạy được song song; không tải mạng trong test; không dùng Python/uv/Node của máy Windows trong cổng; không viết
mã nghiệp vụ của prompt khác.

## Viết test nhanh (PERF-01, đo 2026-10-10)

Mỗi dòng là một chỗ đã đo được làm bộ test chậm; viết test mới theo đúng cách nhanh, phép kiểm không đổi (K23 giữ nguyên).

- Fixture/factory tạo người dùng phải chạy dưới hồ sơ argon2 `test` (`api_env`, `auth_env` đã đặt `ARGON2_PROFILE=test`): hồ sơ sản
  phẩm 61,8 ms/lần băm so với 1,6 ms — từng là phần lớn của "call TB 211 ms" ở test api/db.
- Huấn luyện/suy luận ML thật: dữ liệu và mô hình nhỏ nhất vẫn đi đúng nhánh, kết quả dùng chung bằng fixture `scope="module"`
  (yolo 469 → 37 s); mô hình nạp một lần (`packages/testing/fixtures/ml_ocr.py`).
- Không import torch/transformers ở cấp module của mã chỉ một nhánh cần — nạp trong hàm (`training_segformer/model.py` 4,7 → 0,5 s).
- Chờ/hạn thời gian (watchdog, khoá, debounce): tiêm trần nhỏ qua tham số có sẵn, không ngủ thật dài.
- Quét toàn repo/đọc cấu hình lặp: cache trong tệp test (`functools.cache`), không đọc lại mỗi test.
- Tệp test mới chậm (> 5 s) → cập nhật `packages/testing/fixtures/test_durations.json` (lệnh sinh ở docstring `ordering.py`) để
  xdist xếp nó chạy sớm.
