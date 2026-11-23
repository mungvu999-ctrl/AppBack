# CASE — Ma trận case chuẩn

> **Bản 7 · 2026-10-03.** DEBT-02 (NO-256, NO-294, NO-338): test task nhận case J/U/M và id tham số `[...]`; người dùng duyệt câu chữ.
>
> **Bản 6 · 2026-09-17.** Tranh luận lô 4c: C09b so với base đã lưu của lượt ghi hiện hành.
>
> **Bản 5 · 2026-09-17.** Tranh luận lô 4b: trùng tên hàm task → `case_gate` hỏng; T-complete bỏ "+ J-case của pipeline" (J-case thuộc task); U07 định nghĩa cách kiểm tệp cụt.
>
> **Bản 4 · 2026-09-17.** Thay đổi so với bản 3 (tranh luận lô 4a phần 4 chặng 3): C10, C22 và luật `+ngoài` chỉ áp cho route **được bảo vệ**; thêm **C28** (yêu cầu công khai gửi lặp trong cửa sổ chờ); `G*` chỉ đòi C08, C23 khi đường có `{`; C07 dùng vai mạnh nhất vẫn không có quyền; C19 gồm chuỗi xoay; C27 phải khẳng định status; `Operation` thêm `permission_key`.
>
> **Bản 3 · 2026-09-17.** Thay đổi so với bản 2 (tranh luận phần 3 chặng 3): §2.1 tính case chung từ metadata route, không từ Loại; thêm §2.3 thuật toán `case_gate` (vết status/mã, miễn chỉ cho C16, task lấy từ sổ); C11 thêm cho N3, N13, #44, #37.
>
> **Bản 2 · 2026-09-17.** `tools/case_gate.py` đọc file này.
> **Thay đổi so với bản 1:**
> - bảng loại mới: tách T theo pha, thêm P và B, thêm nhóm case chung tham số hoá;
> - thêm C09b, C23–C27; C14 chỉ còn ở GV, A và các case thêm;
> - C22 trả 503;
> - bỏ `invited` ở C06; J02 ghim N; S04 tiêm được.
>
> **Tên test:** `test_<operationId>__<caseid>` (hai dấu gạch dưới; `operationId` theo cột BE-BIND, không chứa `__`). Cần nhiều test cho một case thì thêm hậu tố: `test_floors_create_floor__C09_missing`.
> **Việc nền và ML:** `test_<tên hàm task>__J06`.
> **Case chung (§2.1):** một test tham số hoá theo route ở B0-06, đánh dấu `@pytest.mark.case("C04")`; id tham số là `operationId`.
> **Nguồn danh sách bắt buộc:** cột Loại của BE-BIND + bảng §2, cộng **thêm** và trừ **miễn kèm lý do** khai trong `apps/*/<module>/cases.toml`:
> ```toml
> [[endpoint]]
> op = "floors_create_floor"
> extra = ["C14"]
> waive = { C16 = "thân không có chuỗi người nhập" }
> ```

## 1. Case HTTP

| Id | Case | Kỳ vọng |
|---|---|---|
| C01 | Đúng | 2xx; response ghi golden và **qua H1** |
| C02 | Thân sai kiểu hoặc giá trị | 422 `VALIDATION`, có `field` |
| C03 | Thân có khoá lạ | 422 `VALIDATION` |
| C04 | Không có token | 401 `UNAUTHENTICATED` |
| C05 | Token hết hạn, sai chữ ký, sai `aud` | 401 `UNAUTHENTICATED` |
| C06 | Người ngoài dự án (kể cả admin hệ thống) | **404** `NOT_FOUND`, `resource:"project"` |
| C07 | Thành viên thiếu quyền; test dùng vai **mạnh nhất** vẫn không có quyền theo ma trận (ví dụ `engineer` với `ruleset.edit`, `user.manage`) | 403 `FORBIDDEN` |
| C08 | Tài nguyên không tồn tại hoặc đã xoá mềm | 404 `NOT_FOUND` |
| C09 | GV: `baseVersion` cũ / thiếu | 409 `VERSION_CONFLICT` đúng thân W20 / 428 `PRECONDITION_REQUIRED` |
| C09b | GV: cùng người gửi lặp cùng thân với `baseVersion` = base đã lưu của lượt ghi hiện hành (thường là revision ngay trước; W20) | 200, trả kết quả hiện tại, không tạo revision mới |
| C10 | `Idempotency-Key` lặp cùng thân / khác thân | trả lại **đúng** response cũ và **không** lặp tác dụng ngoài giao dịch (thư, thông báo, job, SSE) / 422 `IDEMPOTENCY_KEY_REUSED` |
| C11 | Vượt giới hạn tốc độ | 429 `RATE_LIMITED` + `Retry-After` |
| C12 | Thân quá lớn | 413 `PAYLOAD_TOO_LARGE` trước khi đọc hết thân |
| C13 | Phụ thuộc hỏng (DB, Redis, storage) | 503 `DEPENDENCY_UNAVAILABLE` + `Retry-After`; không lộ stacktrace |
| C14 | Hai request song song | không 500; không mất bản ghi; bất biến giữ nguyên (duy nhất, admin cuối); với GV thì đúng một bên thắng, bên kia 409 |
| C15 | Biên danh sách: rỗng, 1 phần tử, chạm trần (trần là setting, test đặt = 3) | danh sách cũ trả trọn, đúng trần, thứ tự cố định; danh sách mới có `nextCursor` ổn định |
| C16 | Chuỗi tiếng Việt NFD; email khác hoa thường | lưu và trả NFC; email trùng sau chuẩn hoá bị coi là trùng |
| C17 | Trường tuỳ chọn không có giá trị | **vắng khoá** (trừ `KeepNull`) |
| C18 | Nhật ký hoạt động | một dòng `activity_log`, actor lấy từ token, đúng `kind` và `objectCode` |
| C19 | N lượt refresh cùng cookie trong 30 s, kể cả chuỗi: cookie cũ, cookie kế tiếp, rồi lại cookie cũ | tất cả 200, cùng `sid`, cùng một cookie hiện hành (không xoay lần hai trong 30 s); phiên còn sống |
| C20 | Dùng lại refresh ngoài 30 s | 401; cả họ phiên bị thu hồi |
| C21 | Id trên đường khác id trong thân (W21) | 422 `PATH_BODY_MISMATCH`; không ghi gì |
| C22 | Cùng `Idempotency-Key` khi lượt trước **chưa xong** | 503 `IDEMPOTENCY_IN_PROGRESS` + `Retry-After: 1` |
| C23 | Id tài nguyên thuộc người dùng khác (thông báo, phiên…) | 404 |
| C24 | `Origin` thiếu hoặc lệch ở endpoint ghi dùng cookie | 403 `ORIGIN_MISMATCH` |
| C25 | Phiên đã thu hồi, người dùng vừa bị vô hiệu hoặc hạ vai | ≤ 5 s sau: 401 `SESSION_REVOKED` hoặc 403 theo vai mới |
| C26 | Token một lần bị dùng lại hoặc hết hạn | 422 mã token của endpoint |
| C27 | Chống dò: email lạ và email có thật | cùng status, cùng thân (trừ `requestId`); thời gian lệch < 50 ms (trung vị 20 lượt). Test nâng hạn mức và **khẳng định** mọi lượt đúng status mong đợi (không đo trên 429); với hạn mức thật, hai nhánh bị 429 ở **cùng** lượt |
| C28 | Yêu cầu công khai có tác dụng ngoài (thư) gửi lặp trong cửa sổ chờ | cùng status như lượt đầu; không sinh token, thư hay task thứ hai |

## 2. Loại endpoint → case bắt buộc

### 2.1 Case chung — mọi route đã xác thực, một test tham số hoá ở B0-06

`C04 C05 C12 C13 C25` cho mọi route được bảo vệ. `C10` và `C22` chung khi route **được bảo vệ** có idempotency (metadata `protected`, `idempotency ≠ "off"`, method ≠ GET, không `versioned`). Route công khai luôn `idempotency="off"` và không bị đòi C10, C22. Route **được bảo vệ** có `+ngoài` ở cột Loại còn cần **C10 riêng** `test_<op>__C10*`, kiểm không lặp thư/thông báo/job/SSE; test chung **không** thoả case này. Route bảo vệ `+ngoài` mà `idempotency="off"` → cổng hỏng.

### 2.2 Theo loại

| Loại | Dùng cho | Bắt buộc riêng |
|---|---|---|
| Đ | đọc trong dự án | C01 C06 C08 C15¹ C17² |
| Đ\* | đọc cần đăng nhập, không gắn dự án | C01 C15¹ C17² |
| G | ghi trong dự án | C01 C02 C03 C06 C07³ C08 C16⁴ C17² C18⁵ C21⁶ |
| G\* | ghi cần đăng nhập, không gắn dự án | C01 C02 C03 C08⁹ C17² C21⁶ C23⁹ |
| GV | ghi có version | loại G + C09 C09b C14 (C18 thay bằng "revision ghi `changedBy` = `sub`") |
| C | công khai: N8, N9, N10, login | C01 C02 C03 C11 C16⁴ C24⁷ C27⁸ + C26 cho N9, N10 |
| R | refresh | C01 C11 C19 C20 C24 |
| P | logout | C01 C24 |
| B | beacon telemetry | C01 C12 |
| T-init | #5 | C01 C02 C03 C06 C07 C11 C21 U05 U08 U09 U12 |
| T-chunk | #6, N55 | C01 C02 C06 C08 C21 U10 |
| T-complete | #7, N56 | C01 C06 C08 C14 C21 U07 U10 U11 |
| T-avatar | N14 | C01 C02 C03 U03 U07 U08 |
| A | admin | C01 C02 C03 C07 C17² C18 C21⁶ (+C09 C09b C14 nếu có version, +C15 nếu là danh sách, +C08 nếu có id trên đường) |
| S | luồng SSE | S01–S05 S07 S09 · S06 chỉ luồng gắn dự án · S08 chỉ luồng tiến độ |

Chú thích:
1. C15 chỉ áp cho endpoint trả danh sách.
2. C17 chỉ áp khi response có trường tuỳ chọn.
3. C07 miễn khi cả ba vai đều được phép (cột Khoá của BE-BIND là `—`).
4. C16 chỉ áp khi thân có chuỗi người nhập.
5. C18 chỉ áp cho dòng có cột Nhật ký trong BE-BIND.
6. C21 chỉ áp khi thân mang id trùng nghĩa với id trên đường.
7. C24 chỉ áp cho endpoint đặt hoặc đọc cookie.
8. C27 cho login và N8.
9. C08 và C23 của `G*` chỉ áp khi đường có `{` (`/api/me`, `POST /api/projects`, `POST /api/notifications/read` không có id trên đường). #20 bỏ qua id của người khác trong thân bằng test đặt tên theo việc, không phải C23.

**Case thêm cố định** (không áp cả loại):
- C14 ở #10 (`FLOOR_ID_TAKEN`), #17 (`MEASUREMENT_ID_TAKEN`), #41 (admin cuối), N3 (thêm cùng email song song → cả hai 2xx, đúng một bản ghi), N4 (gỡ song song hai người sửa cuối → đúng một thành công, bên kia `MEMBER_LAST_EDITOR`), N60 (hai xác nhận song song → đúng một bộ tầng và lượt).
- C11 ở N3, N13, N14, #44, #37 (rate limit của BE-00 §11 mà loại không đòi). Test hạn mức luôn đặt tên `test_<operationId>__C11` (vd `test_me_replace_avatar__C11`), không tên theo việc như `…_rate_limited`: `case_gate` chỉ đếm theo hậu tố.
- C28 ở N8 (cửa sổ chờ của yêu cầu đặt lại mật khẩu).

U01, U02, U04, U06 nằm trong task tiền xử lý: `test_<task>__U01`.

### 2.3 Thuật toán `case_gate` (B0-01 cài, nguồn duy nhất)

**Đầu vào:** BE-BIND §1–§2 (`tools/charter.py`), bảng §2.1–§2.2 và "Case thêm cố định", `apps.api.core.openapi.operations()`, `apps/*/*/cases.toml`, junit, vết case (`CASE_TRACE_FILE`), sổ task (`packages.messaging.registered_tasks()`).

**Metadata của mỗi thao tác** (`operations()` trả `Operation`, B0-06 cài): `op, method, path, protected, versioned, idempotency, body_limit, has_body, has_query, returns_list, has_optional_response_fields, body_mirrors_path, permission_key`. Trường thiếu (trước B0-06) coi là "áp dụng". `permission_key` không dùng để tính case; B0-06 so nó với cột Khoá của BE-BIND.

**Tập bắt buộc** cho mỗi `op` đã mount và có dòng BE-BIND (đường so **không** kèm query):
1. Case chung §2.1 theo metadata (C10, C22 chỉ khi `protected`).
2. Case của Loại §2.2 (GV = G bỏ C18, thêm C09 C09b C14). Case có chú thích **tính từ metadata và cột BE-BIND**, không miễn:
   - C07 bỏ khi Khoá `—`; C18 chỉ khi Nhật ký `có`; C26 chỉ N9, N10; C27 chỉ login, N8; C24 chỉ Loại C, R, P;
   - C02 khi `has_body` hoặc `has_query`; C03, C21 cần `has_body` (C21 thêm `body_mirrors_path`);
   - C15 khi `returns_list`; C17 khi `has_optional_response_fields`;
   - loại A: C09 C09b C14 khi `versioned`; C08 khi đường có `{`;
   - loại `G*`: C08, C23 khi đường có `{`.
3. Case thêm cố định.
4. `cases.toml` `[[endpoint]] extra = [...]`.

**Miễn:** chỉ **C16** được miễn (`waive = {C16 = "lý do ≥ 10 ký tự"}`), vì "thân có chuỗi người nhập" không tính được. Miễn case khác → hỏng. Case nào sai với thực tế thì sửa hiến chương bằng FIX, không miễn.

**Một test được tính** khi:
- tên `test_<op>__<case>[_hậu tố]`, hoặc `test_common__<case>[<op>]` (chỉ cho case chung §2.1);
- kết quả `passed` trong junit;
- vết case có **ít nhất một** response của đúng `<op>` trong test đó; với case có mã cố định thì status/mã khớp: C02 → 422; C03 → 422 `VALIDATION`; C04, C05 → 401; C06, C08, C23 → 404; C07 → 403; C11 → 429; C12 → 413; C13 → 503; C21 → 422 `PATH_BODY_MISMATCH`; C22 → 503 `IDEMPOTENCY_IN_PROGRESS`; C24 → 403 `ORIGIN_MISMATCH`; C25 → 401 hoặc 403.

**Task:** (`cases.toml`: `[[task]]` `fn = "segment_walls"` `require = ["J03"]`; `fn` là tên hàm như `registered_tasks()` trả; task `pipeline.*` bắt buộc thêm J10) mọi task trong `registered_tasks()` và mọi `periodic` bắt buộc có `test_<tên hàm>__J01` và `__J06`, cộng `[[task]] require = [...]` của chủ. Task không có trong sổ mà có `[[task]]` → hỏng. **Hai task/`periodic` trùng tên hàm → hỏng** (một test không được thoả J-case của hai module). Test task đặt tên `test_<tên hàm>__<case>` với `<case>` là J, U hoặc M hai chữ số; được theo sau bởi id tham số `[...]` như test op, không được có hậu tố `_xxx` (bản 7, DEBT-02 NO-256/NO-294).

**Kết quả:** in bảng `op | bắt buộc | tìm thấy | miễn | thiếu`; thiếu một case → hỏng. `op` đã mount mà không có dòng BE-BIND → cảnh báo (B0-07 chặn).

## 3. Tệp đầu vào

| Id | Case | Kỳ vọng |
|---|---|---|
| U01 | Ảnh có EXIF xoay | xử lý theo hướng đã xoay |
| U02 | CMYK, 16-bit, kênh alpha | chuẩn hoá về RGB 8-bit |
| U03 | Bom giải nén / ảnh quá nhiều điểm ảnh | 422 `IMAGE_TOO_LARGE`, không cạn bộ nhớ |
| U04 | PDF mã hoá (mật khẩu người dùng) hoặc 0 trang | 422 `PDF_ENCRYPTED` (mật khẩu) · `PDF_UNREADABLE` (0 trang) |
| U05 | `pageIndex` ngoài số trang | 422 `VALIDATION`, `field:"pageIndex"` |
| U06 | Khổ trang cực lớn | dựng theo DPI có trần |
| U07 | Tệp cụt | 422 `FILE_CORRUPT`. Kiểm trên luồng đọc toàn tệp bằng `bytes.find` (giữ vài byte cuối của khúc trước): PNG có `IEND`; JPEG có `FF D9` sau 2 byte đầu; PDF có `%%EOF` trong 1 KiB cuối. Nhận nhầm JPEG cụt có ảnh thu nhỏ EXIF là chấp nhận được (tiền xử lý bắt tiếp) |
| U08 | Magic bytes lệch đuôi | 422 `FILE_TYPE_MISMATCH` |
| U09 | `File.type` rỗng | nhận diện bằng magic bytes |
| U10 | Khúc thiếu / trùng / sai thứ tự | khúc trùng ghi đè; `complete` khi thiếu → 422 `UPLOAD_INCOMPLETE` |
| U11 | `sizeBytes` khai lệch tổng thật | 422 `UPLOAD_SIZE_MISMATCH` ở `complete` |
| U12 | Tệp `.dwg` | 422 `CAD_NOT_SUPPORTED` ngay ở init |

## 4. Việc nền

| Id | Case | Kỳ vọng |
|---|---|---|
| J01 | Chạy đúng | trạng thái và sự kiện đúng thứ tự |
| J02 | Lỗi tạm thời | thử lại **3** lần, lùi 10/60/300 s; hết lượt → hỏng có mã. Thứ tự giao không được bảo đảm: task nhận chuỗi sự kiện chịu được lô tới muộn và lô tới sau trạng thái cuối |
| J03 | Lỗi vĩnh viễn | `failed` + mã lỗi + thông báo cho người dùng |
| J04 | Huỷ (job huấn luyện) | `cancelled`, dọn tài nguyên tạm |
| J05 | Quá thời gian | hỏng có mã, không treo; task thường < 1 giờ |
| J06 | Giao lặp (at-least-once) | idempotent: không nhân đôi phiên bản, thông báo, bản ghi |
| J07 | Worker chết giữa bước / job mất nhịp tim | lịch quét chuyển `running` quá hạn → `failed` |
| J08 | Thông điệp độc | loại, ghi log, worker vẫn sống |
| J09 | Xếp hàng trước commit | giao dịch rollback thì task **không** được gửi; chỉ gửi sau commit |
| J10 | Commit xong rồi chết trước khi phát sự kiện | task được giao lại và phát lại sự kiện; thông báo không bị XADD lần hai |

## 5. Luồng SSE

| Id | Case | Kỳ vọng |
|---|---|---|
| S01 | Không có hoặc hết hạn cookie luồng | 401 |
| S02 | Nối lại với `lastEventId` còn trong stream | phát lại đủ, không trùng, đúng thứ tự |
| S03 | Định dạng | mọi sự kiện **không tên**, có `id:`, `data:` là JSON đúng schema FE |
| S04 | Heartbeat | dòng `: ping` theo chu kỳ tiêm được (mặc định 15 s) |
| S05 | Client rớt | subscription được dọn, không rò kết nối Redis |
| S06 | Người ngoài dự án mở luồng tiến độ | 404 |
| S07 | Thu hồi phiên / vô hiệu người dùng / gỡ thành viên | server đóng luồng ≤ 5 s |
| S08 | Luồng tiến độ: mở mới hoặc `lastEventId` đã bị cắt | gửi **ảnh chụp** `Progress` hiện tại trước, `id:` = id đuôi stream |
| S09 | Refresh | cookie luồng được cấp lại; mở luồng mới thành công; GET không có `Origin` vẫn mở được |

## 6. ML

| Id | Case | Kỳ vọng |
|---|---|---|
| M01 | Bộ giả, cùng đầu vào | cùng đầu ra |
| M02 | Checksum model lệch | từ chối nạp, lỗi có mã |
| M03 | Artifact người dùng `.pt`/pickle; trọng số nhà cung cấp đã ghim | từ chối / nạp được, chỉ trong `apps/ml` |
| M04 | Không GPU / có GPU | chạy CPU / lấy khoá GPU (có TTL, gia hạn) trước khi chạy |
| M05 | Số đo huấn luyện | đơn điệu theo bước, có mốc thời gian |
| M06 | Đánh giá trên tập test cố định | số tái lập được; tập chia theo tầng/dự án |
