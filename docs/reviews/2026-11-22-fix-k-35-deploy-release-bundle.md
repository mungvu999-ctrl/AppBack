# Review merge fix/k-35-deploy-release-bundle → main

- Ngày: 2026-11-22 · Reviewer: phiên /merge-review · Commit đầu nhánh: 1877ee3e
- Cổng: Phạm vi kiểm: đầy đủ — cổng đầy đủ trên nhánh tích hợp integ/d1-be (gồm nhánh này); `run.sh verify` mã thoát 0 (EXIT=0, 9 bước 0–8 đạt; log: C:/Users/mxuan/AppData/Local/Temp/claude/ae7f68c39ac4980e/9b83f78c-54ce-4350-8787-de9ce46ccdbf/scratchpad/vf/d1-be-full3.log)
- Độ phủ: cổng đầy đủ đạt ngưỡng 90/90 trên nhánh tích hợp; không đo riêng cho nhánh này
- Trailer `Prompt: K-35` có trên mọi commit; tên nhánh khớp mẫu và không dính mẫu mã prompt; mã nợ NO-xxx không trùng giữa các nhánh.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | RES | Lần deploy K-35 đầu tiên: previous_tag cũ chưa có releases/<tag> nên select_release trả 2, `|| true` nuốt lỗi, không tự rollback | deploy/scripts/deploy.sh:~42-55 | ghi vào README/changes hoặc báo rõ |
| 2 | P2 | SEC | tag nối thẳng vào lệnh ssh trong workflow, chỉ kiểm sau ở validate_tag | .github/workflows/deploy.yml (staging, production) | kiểm regex tag trước ssh |

## Điểm
Không P0/P1. Tổng: 4 / 5. Soát SEC, CON, RES, TEST.

## PHÁN QUYẾT: APPROVE WITH COMMENTS
Diff đọc hết, có `changes/K-35.md`, không chạm tệp cấm; cổng đầy đủ xanh.
