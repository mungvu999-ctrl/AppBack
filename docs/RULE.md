# Code Review Checklist – Hệ thống Production tải cao

> **Nguyên tắc:** Review theo rủi ro, không review theo cảm tính. Mọi finding phải có **mã hạng mục**, **mức độ rủi ro** và **bằng chứng trong code**.

---

## Thông tin PR

| Trường | Giá trị |
|---|---|
| PR / Ticket | |
| Tác giả | |
| Reviewer | |
| Loại task (xem mục 6) | |
| Hot path / Luồng tiền / Auth / PII? | ☐ Có  ☐ Không |
| Có thể rollback? | ☐ Có  ☐ Không |
| Ngày review | |

---

## 1. Phân loại mức độ rủi ro

| Mức | Tên | Định nghĩa | Hành động bắt buộc |
|---|---|---|---|
| **P0** | Blocker | Lỗ hổng bảo mật khai thác được, mất/hỏng dữ liệu, sai lệch tài chính, có thể gây sập hệ thống | **Chặn merge.** Không ngoại lệ. Security Lead / Architect sign-off lại sau khi sửa |
| **P1** | Critical | Lỗi dưới tải cao hoặc điều kiện biên: race condition, thiếu timeout, N+1 trên hot path, migration khóa bảng | **Chặn merge.** Chỉ waiver khi có ticket, owner, deadline ≤ 1 sprint, Architect duyệt |
| **P2** | Major | Ảnh hưởng vận hành/bảo trì: thiếu log, metric, test; xử lý lỗi yếu; vi phạm contract nội bộ | Sửa trước merge hoặc tạo ticket có lý do hợp lệ |
| **P3** | Minor | Style, đặt tên, refactor nhỏ, comment | Tác giả tự quyết, không chặn merge |
| **Nit** | Gợi ý | Ý kiến cá nhân, lựa chọn thay thế | Không bắt buộc |

**Quy tắc nâng mức:** Finding tự động tăng **một bậc** nếu nằm trên **hot path** (>1k RPS), trong **luồng tiền / auth / PII**, hoặc **không thể rollback** (migration, xóa dữ liệu, gửi message ra ngoài).

---

## 2. Gate 0 – Cổng tự động (phải xanh trước khi review thủ công)

| ☐ | ID | Kiểm tra | Công cụ gợi ý | Fail thì |
|---|---|---|---|---|
| ☐ | G-01 | Build, lint, format | CI pipeline | Không review |
| ☐ | G-02 | Test pass, coverage code mới ≥ 80% | Jest / JUnit / pytest + coverage gate | Không review |
| ☐ | G-03 | SAST | Semgrep, SonarQube, CodeQL | P0/P1 tùy rule |
| ☐ | G-04 | SCA – dependency có CVE High/Critical | Dependabot, Snyk, Trivy | P1 |
| ☐ | G-05 | Secret scanning | Gitleaks, TruffleHog | **P0** + rotate secret ngay |
| ☐ | G-06 | Container & IaC scan | Trivy, Checkov | P1 |
| ☐ | G-07 | License compliance | FOSSA, license-checker | P2 |

---

## 3. Checklist chi tiết theo miền

Cột **KQ** ghi: `✅` Đạt · `❌` Không đạt (ghi finding) · `N/A` Không áp dụng

### A. Bảo mật (SEC)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | SEC-01 | Injection (SQL, NoSQL, OS command, LDAP, template) | P0 | Tìm nối chuỗi vào query/command: `+`, f-string, `${}`, `exec`, `Runtime.exec`, `child_process`. Bắt buộc parameterized query |
| | SEC-02 | Broken Access Control / IDOR / BOLA | P0 | Endpoint nhận `id` phải kiểm tra resource **thuộc về user/tenant hiện tại**. Không tin `userId`/`tenantId` từ request body |
| | SEC-03 | Authentication | P0 | JWT verify chữ ký, `alg`, `exp`, `aud`, `iss`. Chặn `alg: none`. Rotate session sau login |
| | SEC-04 | Mass assignment | P1 | Không bind thẳng body vào entity. Dùng DTO/allowlist, đặc biệt `role`, `isAdmin`, `balance`, `status` |
| | SEC-05 | Input validation | P1 | Validate kiểu, độ dài, range, format tại biên. Giới hạn kích thước payload, array, file |
| | SEC-06 | SSRF | P0 | URL từ user phải qua allowlist, chặn IP nội bộ & metadata (`169.254.169.254`), chặn redirect |
| | SEC-07 | Path traversal & file upload | P0 | Không dùng filename từ user để ghi file. Kiểm tra MIME bằng magic bytes. Lưu ngoài webroot |
| | SEC-08 | Deserialization không an toàn | P0 | Không dùng `pickle`, `ObjectInputStream`, `yaml.load`, `BinaryFormatter` với dữ liệu không tin cậy |
| | SEC-09 | Secret & credential | P0 | Không hardcode. Đọc từ Vault/Secret Manager. Không log ra |
| | SEC-10 | Mật mã | P1 | Mật khẩu: bcrypt/scrypt/argon2. Cấm MD5, SHA1, ECB. Không tự viết crypto. CSPRNG cho token |
| | SEC-11 | XSS & CSRF | P1 | Output encoding theo ngữ cảnh. Không `innerHTML`/`dangerouslySetInnerHTML` với dữ liệu user. CSRF token / SameSite cookie |
| | SEC-12 | Rò rỉ thông tin | P1 | Response lỗi không chứa stack trace, SQL, đường dẫn nội bộ, version |
| | SEC-13 | PII & dữ liệu nhạy cảm | P1 | Log/event không chứa mật khẩu, token, số thẻ, CCCD. Có masking. Mã hóa at-rest |
| | SEC-14 | Rate limiting & anti-abuse | P1 | Rate limit cho login, OTP, reset password, API tốn tài nguyên. Chống enumeration |
| | SEC-15 | So sánh an toàn | P2 | So sánh constant-time cho token, chữ ký HMAC |

### B. Tính đúng đắn & logic nghiệp vụ (LOG)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | LOG-01 | Tính tiền bằng số thực | P0 | Cấm `float`/`double` cho tiền. Dùng `Decimal`/`BigDecimal`/integer đơn vị nhỏ nhất. Quy tắc làm tròn rõ ràng |
| | LOG-02 | Edge case | P1 | null, rỗng, 0, số âm, overflow, Unicode, list 1 phần tử, giá trị max |
| | LOG-03 | Thời gian | P1 | Lưu UTC, timezone rõ ràng, inject clock thay vì `now()` rải rác. Tính DST, năm nhuận |
| | LOG-04 | Xử lý lỗi | P1 | Không nuốt exception (`catch {}`), không catch-all rồi trả success. Phân biệt lỗi retry được / không |
| | LOG-05 | State machine | P1 | Chuyển trạng thái có điều kiện, chặn chuyển bất hợp lệ (vd `REFUNDED` → `PAID`) |
| | LOG-06 | So khớp yêu cầu | P1 | Đúng acceptance criteria? Có làm thêm ngoài phạm vi không? |

### C. Concurrency & nhất quán dữ liệu (CON)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | CON-01 | Race condition (read-modify-write) | P0 | Mẫu `SELECT` → tính trong app → `UPDATE`. Dùng atomic update, optimistic lock (version) hoặc `SELECT ... FOR UPDATE` |
| | CON-02 | Idempotency | P0 | API tạo giao dịch/thanh toán và consumer MQ chịu được xử lý lặp. Idempotency key + unique constraint |
| | CON-03 | Ranh giới transaction | P1 | Không gọi HTTP/queue trong DB transaction. Transaction ngắn. Isolation level phù hợp |
| | CON-04 | Dual write | P1 | Ghi DB rồi publish event không có Outbox/CDC → mất đồng bộ |
| | CON-05 | Distributed lock | P1 | Lock có TTL và fencing token. Xử lý lock hết hạn khi tác vụ chưa xong |
| | CON-06 | Shared mutable state | P1 | Static/global, singleton có state trong đa luồng. Collection không thread-safe |
| | CON-07 | Deadlock | P1 | Thứ tự lấy lock nhất quán. Không lồng lock |
| | CON-08 | Thứ tự message | P2 | Consumer có giả định thứ tự? Partition key đúng chưa? |

### D. Hiệu năng & khả năng mở rộng (PERF)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | PERF-01 | N+1 query | P1 | Query DB / gọi API trong vòng lặp → batch, `JOIN`, `IN (...)` |
| | PERF-02 | Query không giới hạn | P1 | `findAll()`, `SELECT *` không `LIMIT`. Bắt buộc phân trang, ưu tiên cursor-based cho bảng lớn |
| | PERF-03 | Index | P1 | `EXPLAIN` query mới. Tránh full scan bảng lớn. Composite index đúng thứ tự cột |
| | PERF-04 | Blocking I/O trong async/event loop | P1 | Hàm sync hoặc CPU nặng trong Node, asyncio, WebFlux làm nghẽn cả service |
| | PERF-05 | Độ phức tạp thuật toán | P1 | O(n²) trên dữ liệu user kiểm soát kích thước. ReDoS với regex |
| | PERF-06 | Bộ nhớ | P1 | Load toàn bộ file/result set vào RAM thay vì stream. Cache không giới hạn size/TTL |
| | PERF-07 | Cache | P2 | Invalidation, chống stampede (lock / jitter TTL), hot key, cache penetration |
| | PERF-08 | Connection pool | P1 | Kích thước hợp lý. Trả connection ở mọi nhánh (`try-with-resources` / `finally`) |
| | PERF-09 | Payload | P2 | Response quá lớn, over-fetching, thiếu nén |

### E. Khả năng chịu lỗi (RES)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | RES-01 | Timeout | P1 | **Mọi** lời gọi ra ngoài (HTTP, DB, Redis, gRPC) có connect & read timeout rõ ràng. Không dựa vào mặc định |
| | RES-02 | Retry | P1 | Exponential backoff + jitter, giới hạn số lần. Chỉ retry thao tác idempotent. Tránh retry storm |
| | RES-03 | Circuit breaker & bulkhead | P2 | Dependency chậm không kéo sập service. Tách pool theo dependency |
| | RES-04 | Graceful degradation | P2 | Dependency phụ chết, luồng chính vẫn chạy, có fallback |
| | RES-05 | Backpressure | P2 | Queue/buffer có giới hạn. Load shedding khi quá tải |
| | RES-06 | Graceful shutdown | P2 | Xử lý SIGTERM, drain request, commit offset trước khi thoát |

### F. Database & migration (DB)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | DB-01 | Tương thích ngược | P0 | Expand → migrate → contract. Không rename/drop cột cùng release với code mới (rolling deploy chạy song song 2 phiên bản) |
| | DB-02 | Khóa bảng | P1 | `ALTER` bảng lớn dùng online DDL (gh-ost, pt-osc, `CREATE INDEX CONCURRENTLY`). Cẩn thận cột `NOT NULL` + default |
| | DB-03 | Backfill | P1 | Batch nhỏ, có throttle, chạy lại được. Không `UPDATE` toàn bảng một lần |
| | DB-04 | Rollback | P1 | Có script down / kế hoạch rollback đã test |
| | DB-05 | Ràng buộc dữ liệu | P2 | Unique, FK, check constraint ở tầng DB, không chỉ validate trong app |

### G. API & contract (API)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | API-01 | Breaking change | P0 | Xóa/đổi tên field, đổi kiểu, đổi ngữ nghĩa → versioning hoặc thêm field mới |
| | API-02 | HTTP semantics | P2 | Status code đúng, `GET` không side effect, format lỗi nhất quán (RFC 7807) |
| | API-03 | Event schema | P1 | Tương thích qua schema registry. Consumer cũ đọc được |
| | API-04 | Tài liệu | P3 | Cập nhật OpenAPI / Proto |

### H. Observability (OBS)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | OBS-01 | Log có cấu trúc | P2 | JSON, có `trace_id`/`correlation_id`, level đúng. Không log trong vòng lặp nóng |
| | OBS-02 | Metric | P2 | RED (Rate, Errors, Duration) cho endpoint mới, metric nghiệp vụ. Tránh label cardinality cao (vd `user_id`) |
| | OBS-03 | Tracing | P2 | Truyền trace context qua HTTP, queue, async |
| | OBS-04 | Alert & runbook | P2 | Tính năng quan trọng có alert và hướng dẫn xử lý sự cố |
| | OBS-05 | Audit log | P1 | Thao tác nhạy cảm (phân quyền, tiền, xóa dữ liệu): ai, làm gì, lúc nào |

### I. Kiểm thử (TEST)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | TEST-01 | Negative test | P1 | Case lỗi, input xấu, truy cập trái phép – không chỉ happy path |
| | TEST-02 | Chất lượng test | P2 | Assert có ý nghĩa. Không phụ thuộc thời gian thật hay thứ tự chạy |
| | TEST-03 | Integration & contract test | P2 | Cho thay đổi DB, queue, API giữa service |
| | TEST-04 | Concurrency test | P1 | Luồng tiền, tồn kho: bắn song song để bắt race condition |
| | TEST-05 | Load test | P1 | Bắt buộc với hot path mới/thay đổi. So sánh p95, p99 với baseline |

### J. Khả năng bảo trì (MNT)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | MNT-01 | Độ phức tạp | P2 | Hàm > 50 dòng, cyclomatic > 10, lồng > 3 cấp → tách |
| | MNT-02 | Phân tầng | P2 | Business logic không nằm trong controller/repository. Không phụ thuộc vòng |
| | MNT-03 | Trùng lặp & dead code | P3 | Copy-paste, code bị comment bỏ, feature flag hết hạn |
| | MNT-04 | Đặt tên & comment | P3 | Tên thể hiện ý định. Comment giải thích **tại sao**, không phải **cái gì** |
| | MNT-05 | Kích thước PR | P2 | > 400 dòng logic → tách nhỏ |

### K. Triển khai & vận hành (OPS)

| KQ | ID | Hạng mục | Mức | Cách soi code |
|---|---|---|---|---|
| | OPS-01 | Feature flag | P1 | Thay đổi rủi ro cao bật/tắt được không cần deploy lại |
| | OPS-02 | Cấu hình | P2 | Không hardcode theo môi trường. Mặc định an toàn (fail-closed) |
| | OPS-03 | Kế hoạch rollback | P1 | Rollback code có an toàn với dữ liệu đã ghi theo format mới? |
| | OPS-04 | Tài nguyên | P2 | Resource request/limit, liveness ≠ readiness, HPA |

---

## 4. Mẫu Red Flag tham khảo

**SEC-01 – SQL Injection (P0)**
```python
# ❌
cursor.execute(f"SELECT * FROM orders WHERE id = '{order_id}'")
# ✅
cursor.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
```

**SEC-02 – IDOR (P0)**
```java
// ❌ Ai biết id cũng xem được đơn người khác
return orderRepo.findById(id);
// ✅ Trả 404 thay vì 403 để không lộ việc resource tồn tại
return orderRepo.findByIdAndOwnerId(id, currentUser.getId())
                .orElseThrow(NotFoundException::new);
```

**CON-01 – Race condition trừ tiền (P0)**
```sql
-- ❌ App: đọc balance → kiểm tra >= amount → UPDATE balance = newValue
--    Hai request song song cùng đọc balance cũ → âm tiền
-- ✅ Atomic, điều kiện nằm trong câu lệnh; kiểm tra affected_rows = 1
UPDATE accounts SET balance = balance - :amount
WHERE id = :id AND balance >= :amount;
```

**RES-01 – Thiếu timeout (P1)**
```javascript
// ❌ Có thể treo vô hạn, cạn connection pool
await axios.get(PARTNER_URL);
// ✅
await axios.get(PARTNER_URL, { timeout: 2000 });
```

**PERF-01 – N+1 (P1)**
```python
# ❌ 1 + N query
for order in orders:
    order.customer = Customer.get(order.customer_id)
# ✅ 2 query
customers = Customer.get_many({o.customer_id for o in orders})
```

**CON-04 – Dual write (P1)**
```java
// ❌ DB commit OK nhưng publish lỗi (hoặc ngược lại) → mất đồng bộ
orderRepo.save(order);
kafka.send("order-created", event);
// ✅ Ghi event vào bảng outbox trong cùng transaction; relay riêng publish sau
```

---

## 5. Chấm điểm & ra quyết định

### Trọng số theo miền (điều chỉnh theo loại hệ thống)

Thang điểm mỗi miền: **5** không finding · **4** chỉ P3 · **3** có P2 · **1** có P1 · **0** có P0

| Miền | Trọng số | Điểm (0–5) | Điểm × Trọng số |
|---|---|---|---|
| SEC – Bảo mật | 25% | | |
| CON – Concurrency & dữ liệu | 15% | | |
| LOG – Tính đúng đắn | 15% | | |
| PERF – Hiệu năng | 10% | | |
| RES – Chịu lỗi | 10% | | |
| DB, API – Migration & contract | 10% | | |
| TEST – Kiểm thử | 7% | | |
| OBS, OPS – Vận hành | 5% | | |
| MNT – Bảo trì | 3% | | |
| **Tổng** | **100%** | | **/ 5** |

### Ma trận quyết định

| Điều kiện | Quyết định |
|---|---|
| Có ≥ 1 finding **P0** | ❌ **REJECT** – bất kể tổng điểm |
| Có **P1** chưa được waiver | 🔶 **REQUEST CHANGES** |
| Không P0/P1, điểm ≥ 4.0 | ✅ **APPROVE** |
| Không P0/P1, 3.0 ≤ điểm < 4.0 | ✅ **APPROVE WITH COMMENTS** – P2 phải có ticket |
| Điểm < 3.0 | 🔶 **REQUEST CHANGES** – xem lại thiết kế |

---

## 6. Độ sâu review theo loại task

● Bắt buộc · ○ Nên xem · – Không cần

| Loại task | SEC | LOG | CON | PERF | RES | DB | API | OBS | TEST | Reviewer |
|---|---|---|---|---|---|---|---|---|---|---|
| Thanh toán, tiền, tồn kho | ● | ● | ● | ● | ● | ● | ● | ● | ● | 2 (≥ 1 Senior) |
| Auth, phân quyền, PII | ● | ● | ○ | ○ | ○ | ○ | ● | ● | ● | 2 + Security |
| Endpoint mới trên hot path | ● | ● | ● | ● | ● | ○ | ● | ● | ● | 2 |
| Migration DB | ○ | ● | ● | ● | – | ● | ○ | ○ | ● | 2 + DBA |
| Tích hợp bên thứ ba | ● | ● | ○ | ○ | ● | – | ● | ● | ● | 2 |
| Consumer / job nền | ○ | ● | ● | ● | ● | ○ | ● | ● | ● | 1 |
| Hotfix | ● | ● | ○ | ○ | ○ | ○ | ● | ○ | ● | 1 + hậu kiểm 48h |
| Refactor, UI, docs | ○ | ● | – | ○ | – | – | – | – | ○ | 1 |

---

## 7. Template ghi finding

```
[P1][CON-01] Race condition khi trừ tồn kho
📍 Vị trí: InventoryService.java:142-150
🔍 Bằng chứng: Đọc stock → kiểm tra → save() không có lock. Hai request song song có thể bán vượt tồn kho.
💥 Tác động: Overselling khi flash sale (~5k RPS), phát sinh chi phí hoàn tiền.
✅ Đề xuất: UPDATE ... SET qty = qty - ? WHERE id = ? AND qty >= ?, kiểm tra affected rows.
🧪 Test cần thêm: Concurrency test 100 thread cùng mua sản phẩm có 10 tồn kho.
```

### Bảng tổng hợp finding

| # | Mức | ID | Mô tả ngắn | Vị trí | Trạng thái |
|---|---|---|---|---|---|
| 1 | | | | | ☐ Open ☐ Fixed ☐ Waived |
| 2 | | | | | ☐ Open ☐ Fixed ☐ Waived |
| 3 | | | | | ☐ Open ☐ Fixed ☐ Waived |

### Kết luận review

```
Quyết định: ____________ | Điểm: ___ / 5
P0: __ | P1: __ | P2: __ | P3: __
Miền đã review: ____________________
Waiver (ticket / owner / deadline): ____________________
Reviewer ký: ____________   Ngày: ____________
```