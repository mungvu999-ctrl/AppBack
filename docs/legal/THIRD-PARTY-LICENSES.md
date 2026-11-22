# Giấy phép thành phần bên thứ ba — AppBack

Bảng này trả lời câu hỏi "mỗi mô hình/bộ dữ liệu/thư viện lớn của dự án dùng theo giấy phép nào, có dùng
thương mại được không". Dự án là **đồ án phi thương mại** (xem `README.md`, mục "Giới hạn đề tài"). Đây là tóm tắt
điều khoản công bố, **không phải ý kiến pháp lý**; trước khi bán hoặc cho thuê dịch vụ phải có tư vấn pháp lý.

Kiểm nguồn lần cuối: **22/11/2026** (trang chính thức của mô hình/bộ dữ liệu, ghi ở cột "Nguồn điều khoản").
Chưa có cổng kỹ thuật chặn kích hoạt mô hình theo giấy phép (xem `docs/security/threat-model.md`, mục "lỗ có chủ ý").

Trọng số mô hình **tải về khi chạy** (`packages/ml_contracts/pinned.py`, kèm SHA-256), **không phân phối trong repo**.

## Mô hình và dữ liệu

| Thành phần | Dùng ở đâu | Giấy phép | Nguồn điều khoản | Thương mại |
|---|---|---|---|---|
| ultralytics (YOLOv8n/s, mã + trọng số `ultralytics/assets` v8.3.0) | họ `openingAndFurnitureDetection`; `apps/ml/pyproject.toml`; `pinned.py` | AGPL-3.0 (có bản Enterprise trả phí) | https://github.com/ultralytics/ultralytics — Ultralytics nêu hai loại giấy phép: AGPL-3.0 và Enterprise dùng cho sản phẩm doanh nghiệp | **Có điều kiện**: công bố mã nguồn dịch vụ (AGPL-3.0 §13) hoặc mua giấy phép Enterprise |
| CubiCasa5K (bộ dữ liệu) và trọng số huấn luyện từ nó | `apps/worker/datasets_cubicasa/`; họ `wallSegmentation` | **CC BY-NC-SA 4.0** theo bản ghi Zenodo (mã nhập ghi `CC-BY-NC-4.0`, thiếu "SA" — xem ghi chú 1) | https://zenodo.org/records/2613548 | **Không** (phi thương mại; trọng số huấn luyện từ dữ liệu này kế thừa) |
| SegFormer `nvidia/mit-b0`, `nvidia/mit-b1` (trọng số khởi tạo) | họ `wallSegmentation`; `pinned.py` (`license="other"`) | "other" = NVIDIA Source Code License của repo NVlabs/SegFormer | model card https://huggingface.co/nvidia/mit-b0 và `.../mit-b1` trỏ tới https://github.com/NVlabs/SegFormer/blob/master/LICENSE; README của repo ghi chỉ dùng cho nghiên cứu/đánh giá, dùng kinh doanh phải liên hệ NVIDIA Research Licensing | **Không** (nghiên cứu/đánh giá). Chưa đọc nguyên văn tệp LICENSE — xem ghi chú 2 |
| rapidocr-onnxruntime (mô hình nhận dạng chữ) | họ `dimensionReading`; `pinned.py` | Apache-2.0 | `pinned.py` (trường `license`); gói PyPI `rapidocr-onnxruntime` | Có |

## Thư viện chạy lõi

| Thành phần | Giấy phép | Nguồn điều khoản | Thương mại |
|---|---|---|---|
| pypdfium2 (gói + PDFium) | `BSD-3-Clause, Apache-2.0, dependency licenses` (trường `license` trên PyPI, không có `license_expression`) | https://pypi.org/project/pypdfium2/ | Có (giữ thông báo bản quyền; kiểm tệp `LICENSES` kèm wheel khi phân phối) |
| torch | trường `license_expression` PyPI: Apache-2.0 AND BSD-3-Clause AND BSD-2-Clause AND MIT AND (một số giấy phép khác của phụ thuộc đóng gói) | https://pypi.org/project/torch/ | Có |
| transformers | Apache-2.0 | https://pypi.org/project/transformers/ | Có |
| torchvision | BSD-3-Clause (theo kho mã torchvision; chưa mở trang trong lượt kiểm này) | https://github.com/pytorch/vision | Có (cần xác nhận) |

Các thư viện còn lại (FastAPI, Celery, SQLAlchemy, numpy, opencv, …) là giấy phép mở thông dụng; danh sách và
phiên bản chính xác nằm ở `uv.lock`. Chưa lập bảng đầy đủ từng gói: xem nợ NO-463.

## Dịch vụ ngoài

| Dịch vụ | Điều khoản | Ghi chú |
|---|---|---|
| Google Vertex AI (Gemini), `packages/genai/vertex.py` | Điều khoản dịch vụ Google Cloud (ngoài repo) | Ảnh bản vẽ có thể gửi tới dịch vụ này; việc báo người dùng là mục riêng L6-N01 |

## Mã của chính dự án

Chưa có tệp `LICENSE` ở gốc repo (mặc định giữ mọi quyền). Dự thảo "bảo lưu mọi quyền" đang chờ chủ dự án duyệt
câu chữ (mục L6-N03).

## Ghi chú

1. Bản ghi Zenodo của CubiCasa5K ghi CC BY-NC-SA 4.0 (có điều khoản chia sẻ tương tự). `README.md` và
   `apps/worker/datasets_cubicasa/importer.py` (`SOURCE_LICENSE`) ghi CC BY-NC 4.0. Cả hai đều cấm thương mại nên kết
   luận không đổi; nên sửa chuỗi trong mã cho khớp nguồn (nợ NO-464).
2. Trang chính thức chỉ cho thấy tên "other" và liên kết tới LICENSE của NVlabs; công cụ dùng để tra không đọc được
   nguyên văn điều khoản. Người duyệt nên mở https://github.com/NVlabs/SegFormer/blob/master/LICENSE và chép điều khoản
   vào bảng trước khi dùng cho việc nào ngoài đồ án (nợ NO-463).
3. Phần frontend và tài sản 3D: xem `THIRD-PARTY-LICENSES.md` trong repo AppFront (thư mục `docs/`).
