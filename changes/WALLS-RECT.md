# WALLS-RECT — vector hoá tường thẳng trục bằng dải chữ nhật trước xương

- `packages/vision/walls/rect_pass.py` (mới): run-length theo hàng/cột → chữ nhật → đoạn tim + bề dày = cạnh ngắn; gộp
  đoạn thẳng hàng; bản vẽ xoay (góc trội Hough) xoay về trục rồi xoay ngược đầu mút; có ảnh xám thì tim/bề dày theo
  nét mực (`refine_line` như bước 8c). Trả thêm phần mặt nạ còn lại (chéo, cong) cho xương.
- `vectorize_with_stats(mask, gray, rect=True)`: chữ nhật trước, xương trên phần còn lại; `dropped["rect"]`. Mặc định
  `rect=False` nên mọi test vector hoá cũ giữ nguyên. `apps/ml/walls/step.py` bật `rect=True` trên đường mặt nạ.
- Lý do (đo ngoài repo, `F:/App/docs/ml-accuracy-investigation/phase-3/bu-tuong/BAO-CAO.md` §5–6): trên 345 trang held-out
  (305 FloorPlanCAD + 40 CVC) mặt nạ a124 IoU 0,807, xương 0,671, chữ nhật 0,771 (+0,100 [+0,091; +0,109]); đường tâm R/P
  và số phòng kín không đổi; không thêm tường nên không sinh tường giả.
- Không đổi API, DB, migration, hợp đồng FE–BE (`walls.json` cùng schema).
- Cổng áp dụng: lint/type, test `packages/vision/walls`, `apps/ml/walls`. Migration/hợp đồng: không áp dụng.
- Đo lại với mã trong repo (`F:/runs/vector-loss/measure_rect.py`, 345 trang, đường mặt nạ): IoU không mực 0,720 → 0,775
  (+0,056 [+0,050; +0,062]), có mực 0,671 → 0,718 (+0,047); phòng kín 140 → 137–139/186; đoạn/trang CAD 19,8 → 18,4, scan
  56 → 87 (mép răng cưa còn sinh dải nhỏ — nợ). 0,16 s/trang.
- Cổng nhanh không chạy được vì container verify `m1-01` của phiên khác treo pytest > 5 h giữ khoá; thay bằng ruff/format/mypy/
  lint-imports/pytest (walls + ml/walls + vectorize, 145 test) + coverage `rect_pass` 99 % chạy thẳng trong `appback-verify:local`.
- Sửa sau CI (`tests/e2e/test_pipeline_e2e.py::test_spatial_read_layer__C01_pipeline`, tỉ lệ lệch 32 %): run-length chạy
  tới mặt ngoài tường vuông góc nên dải dài thêm nửa bề dày ở góc/nút T → nhịp ghép chữ kích thước sai, mẩu vách cạnh
  cửa bị coi là lát cắt. `_trim_ends` kéo đầu dải về tim dải vuông góc (như nút xương) trước khi lọc lát cắt; đo lại
  trang tổng hợp seed 7/5: tỉ lệ 10,0 / 12,502 (đáp án 10 / 12,5), bằng xương cũ.
