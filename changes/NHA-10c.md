# NHA-10c — "mái bằng" suy từ độ dốc thoát nước không còn được coi là chắc

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.C Câu 6. Người dùng duyệt GATE 1 ngày 2026-11-27 ("Duyệt, bắt đầu"). Nhánh
xếp chồng lên `feature/nha-10a-roof-bench` (PR 125) vì thước đo mái chưa có trong `main`; chỉ gộp sau PR 125.

- `packages/domain/drawing_set/building.py` (`_form_and_pitch`): khi độ dốc đọc được dưới 5 %, dạng `flat` lấy độ tin
  `min(độ tin của trị số, 0,6)` thay cho độ tin của trị số (0,8). Trị số độ dốc giữ nguyên nguồn và độ tin. Lý do: một
  độ dốc thoát nước chỉ chứng minh có một sàn mái bằng, không loại trừ phần mái dốc khác của nhà.
- Test sửa trước mã: `packages/domain/drawing_set/tests/test_building.py`
  (`test_two_percent_drainage_slope_is_a_flat_roof`) khẳng định dạng 0,6 và trị số 0,8.
- Không đổi hợp đồng, không migration. Giá trị và nguồn của mọi trường giữ nguyên; chỉ một con số độ tin đổi.

## Tác động

- Trên dây: `roofs[].form.confidence` của `GET …/building` từ 0,8 xuống 0,6 trong trường hợp này. Bản nháp mái của
  thước (`predict.roof_draft`, lấy min của các trường) đổi theo; đó là thứ bảng số đo dưới đây ghi lại.
- Mái sau khi xác nhận: `assembly.py` lấy min độ tin của dạng, độ dốc và hướng mái. Hướng mái máy đề xuất vốn ở 0,6
  nên thường không đổi. **Đổi một trường hợp:** người duyệt tự nhập hướng mái (độ tin 1) cho mái bằng loại này thì độ
  tin của mái dựng ra từ 0,8 xuống 0,6. Chưa có test ghim trường hợp đó.
- Luật hỏi: không đổi. T-07 và M-18 xét theo nguồn, không theo độ tin, nên người duyệt vẫn **không** được hỏi ở
  trường hợp này (NO-618).

## Số đo (chỉ tập dev, cùng đệm đọc trang với NHA-10a)

Ô ghi sai/trả lời trong các câu về loại mái có độ tin >= 0,7.

| Ảnh tờ | Loại mái đúng/trả lời/tổng | Sai mà chắc >= 0,7, trước | Sau |
|---|---|---|---|
| `sheets_v3` (đúng phông), 10 bộ dev | 3/10/10, không đổi | 2/3 | 0/0 |
| tờ ghép cũ, 10 bộ dev | 2/8/10, không đổi | 0/0 | 0/0 |

Trên `sheets_v3` đúng ba tệp dự đoán đổi, mỗi tệp một dòng độ tin: DS-254 và DS-261 (đáp án hỗn hợp, trước đây sai
mà chắc), DS-267 (đáp án mái bằng, nay đúng ở 0,6). Trên tờ ghép cũ 50 tệp trùng với lượt trước, kể cả phần test.
Cốt chân, cốt đỉnh và mọi chỉ số tầng không đổi.

Lát này không làm máy đúng thêm bộ nào; nó chỉ bỏ hai câu trả lời sai được ghi là chắc. Mười bộ là quá ít để coi
0,6 là mức đã hiệu chỉnh: nó là mức "đoán" sẵn có của §2.E, không phải số ước lượng từ dữ liệu.

## Phạm vi kiểm

- Phạm vi kiểm: fast.
- Cổng nhanh (`bash tools/verify/run.sh verify --fast`): **đạt** sáu bước đã chạy — ruff format, ruff check,
  mypy --strict, lint-imports, pytest bị ảnh hưởng (63 tệp test, 2136 test đạt; độ phủ tập file bị chạm dòng 100 % ·
  nhánh 100 %), openapi. Bước migration và hợp đồng không chạy vì đầu vào của chúng không đổi. Cổng đầy đủ để CI chạy.
- Review (python-reviewer): không có CRITICAL / HIGH. Hai MEDIUM đều về ghi chú này và đã sửa ở mục "Tác động"
  (trường hợp người duyệt nhập hướng mái; bản nháp của thước đổi theo). Một LOW đã xử lý: thêm ca `0,2:5` (4 %, nhập
  nhằng, 0,5) để ghim `min` — mức đoán là trần chứ không ghi đè. LOW để nguyên: chưa ghim mâu thuẫn độ dốc giải ra
  trị số dưới 5 % (bị chặn ở 0,65 rồi xuống 0,6) và độ dốc người duyệt nhập dưới 5 % (dạng vẫn suy từ số máy đọc,
  hành vi sẵn có).

## Nợ và việc chưa làm

- NO-612 (`changes/NHA-10a.md`) còn mở: lát này chỉ xử lý phần "sai mà chắc". Từ khoá vật liệu và mái nhiều dạng
  (NHA-11) chưa làm, nên loại mái vẫn ngang mốc "luôn đoán mái bằng".
- NO-618 — Bộ có độ dốc thoát nước trên hình mái nhưng thực ra là mái hỗn hợp (DS-254, DS-261): đề xuất ra `flat`
  nguồn `roofPlan` và **không** có câu hỏi nào cho người duyệt (T-07 chỉ hỏi nguồn mặc định / quy ước, M-18 chỉ hỏi
  dạng quy ước). Hạ độ tin không tự sinh câu hỏi. Cần một luật hỏi khi bộ có dấu hiệu mái dốc ở nơi khác, hoặc để
  NHA-11 giải bằng mái nhiều dạng. Chủ: NHA-11. P2. Mở.
