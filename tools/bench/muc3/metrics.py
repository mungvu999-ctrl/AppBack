"""Chấm một bộ hồ sơ: mỗi chỉ tiêu cho ra danh sách `(độ chắc, đúng?)` (xem `SCHEMA.md` về dạng đầu ra).

Quy ước: đáp án thiếu dự đoán = `(None, False)`; mọi phép so dung sai là bao hàm hai đầu, trên mi-li-mét đã làm tròn
(3,6 so 3,55 m phải đạt dù số thực lệch 5e-17).
"""

from __future__ import annotations

from typing import Any

Json = dict[str, Any]
Item = tuple[float | None, bool]

KINDS = ("plan", "section", "elevation", "roof")
HEIGHT_TOL_MM, SHIFT_TOL_MM = 50, 100
ITEM_METRICS = ("kind", "storey", "h_plan_section", "h_plan", "stack")


def norm_kind(kind: object) -> object:
    """`plan?` (đáp án chưa chắc) tính là `plan`."""
    return "plan" if kind == "plan?" else kind


def mm(x: float) -> int:
    """Làm tròn mét*1000 hay mm về số nguyên để các ca biên so sánh chính xác."""
    return round(x)


def rows(pred: Json, key: str) -> list[Json]:
    """Các phần tử dạng dict của `pred[key]` (bỏ rác, thiếu khoá thì rỗng)."""
    return [r for r in pred.get(key) or [] if isinstance(r, dict)]


def by_key(items: list[Any] | None, key: str) -> dict[Any, Json]:
    """Lập chỉ mục các dict có khoá `key` theo giá trị khoá đó."""
    return {r[key]: r for r in items or [] if isinstance(r, dict) and key in r}


def kind_items(truth: Json, pred: Json) -> list[Item]:
    """Một mục mỗi hình đáp án có loại thuộc `KINDS`; thiếu dự đoán là `(None, False)`."""
    pv = by_key(pred.get("views"), "id")
    out: list[Item] = []
    for v in truth["views"]:
        k = norm_kind(v["kind"])
        if k not in KINDS:
            continue
        p = pv.get(v["id"])
        out.append((None, False) if p is None else (p.get("confidence"), norm_kind(p.get("kind")) == k))
    return out


def storey_items(truth: Json, pred: Json) -> list[Item]:
    """Một mục mỗi tầng đáp án: hình `view_id` của tầng phải mang cùng `floor_no`.

    Tầng không có hình (người soát thêm), `scored` = False hay số tầng không nguyên không được chấm.
    Độ chắc lấy `floor_confidence` của bản ghi hình nếu có, không thì `confidence`.
    """
    pv = by_key(pred.get("views"), "id")
    out: list[Item] = []
    for f in truth["floors"]:
        if f.get("view_id") is None or f.get("scored") is False or not isinstance(f.get("floor_no"), int):
            continue
        p = pv.get(f["view_id"])
        if p is None:
            out.append((None, False))
            continue
        conf = p["floor_confidence"] if p.get("floor_confidence") is not None else p.get("confidence")
        out.append((conf, p.get("floor_no") == f["floor_no"]))
    return out


def height_items(truth: Json, pred: Json, basis: str) -> list[Item]:
    """Chiều cao tầng đáp án theo một cơ sở; đạt khi lệch <= 50 mm. Khoá cặp là `(from, to)` số tầng.

    Chiều cao `reviewed` (người soát) là chỉ tiêu chính, tính vào `plan+section`.
    """
    ph = {(h.get("from"), h.get("to")): h for h in pred.get("storey_heights") or []}
    out: list[Item] = []
    for h in truth["storey_heights"]:
        b = "plan+section" if h["basis"] == "reviewed" else h["basis"]
        if h["height_m"] is None or b != basis or h.get("scored") is False:
            continue
        p = ph.get((h["from"], h["to"]))
        if p is None or p.get("height_m") is None:
            out.append((None, False))
        else:
            ok = abs(mm(1000 * p["height_m"]) - mm(1000 * h["height_m"])) <= HEIGHT_TOL_MM
            out.append((p.get("confidence"), ok))
    return out


def pred_floor_of(pred: Json) -> dict[Any, Any]:
    """Mã hình -> số tầng, lấy từ `views` của dự đoán rồi `floors` ghi đè."""
    m = {v["id"]: v.get("floor_no") for v in pred.get("views") or [] if "id" in v}
    m.update({f.get("view_id"): f.get("floor_no") for f in pred.get("floors") or []})
    return m


def stack_items(truth: Json, pred: Json) -> list[Item]:
    """Cặp chồng tầng đáp án màu `xanh`; đạt khi độ dời lệch <= 100 mm (khoảng cách Euclid, mm nguyên, bao hàm).

    `a`/`b` của đáp án là số tầng, của dự đoán là mã hình (tầng thấp trước) được đổi về số tầng.
    """
    fno = pred_floor_of(pred)
    ps = {(fno.get(s.get("a")), fno.get(s.get("b"))): s for s in pred.get("stack") or []}
    out: list[Item] = []
    for s in truth["stack"]:
        if s.get("colour") != "xanh" or s.get("shift_mm") is None:
            continue
        p = ps.get((s["a"], s["b"]))
        if p is None or p.get("shift_mm") is None:
            out.append((None, False))
        else:
            d2 = sum((mm(a) - mm(b)) ** 2 for a, b in zip(p["shift_mm"], s["shift_mm"], strict=False))
            out.append((p.get("confidence"), d2 <= SHIFT_TOL_MM**2))
    return out


def extra_counts(truth: Json, pred: Json) -> dict[str, int]:
    """Dự đoán thừa (không có đáp án đối ứng) theo chỉ tiêu.

    Hình không có trong đáp án; tầng có số không có trong đáp án; chiều cao/cặp chồng không có bản ghi đáp án
    (mọi màu, mọi cơ sở). Chiều cao tính vào `h_plan` khi `source` = `plan`, còn lại vào `h_plan_section`.
    """
    tv, tf = {v["id"] for v in truth["views"]}, {f["floor_no"] for f in truth["floors"]}
    th = {(h["from"], h["to"]) for h in truth["storey_heights"]}
    ts = {(s["a"], s["b"]) for s in truth["stack"]}
    fno = pred_floor_of(pred)
    ph = [
        h
        for h in rows(pred, "storey_heights")
        if h.get("height_m") is not None and (h.get("from"), h.get("to")) not in th
    ]
    return {
        "kind": sum(v.get("id") not in tv for v in rows(pred, "views")),
        "storey": sum(f.get("floor_no") not in tf for f in rows(pred, "floors")),
        "h_plan_section": sum(h.get("source") != "plan" for h in ph),
        "h_plan": sum(h.get("source") == "plan" for h in ph),
        "stack": sum((fno.get(s.get("a")), fno.get(s.get("b"))) not in ts for s in rows(pred, "stack")),
    }


def stair_pairs(truth: Json) -> list[tuple[int, int]]:
    """Cặp số tầng liên tiếp mà cả hai tầng đều có cầu thang trong đáp án (đáp án chỉ-soát không có `stairs`)."""
    fl = {s["floor_no"] for s in truth.get("stairs", []) if s["stairs"]}
    return [(f, f + 1) for f in sorted(fl) if f + 1 in fl]


def stairs_ok(truth: Json, pred: Json) -> bool | None:
    """`None` nếu đáp án không có hai tầng liền kề cùng cầu thang; không thì đúng khi một lõi thang dự đoán
    liệt kê đủ cả hai tầng của một cặp. Vị trí (`centre_mm`) CHƯA được chấm."""
    pairs = stair_pairs(truth)
    if not pairs:
        return None
    cores = [set(c.get("floors") or []) for c in pred.get("stair_cores") or []]
    return any(set(p) <= c for p in pairs for c in cores)


def set_exact(truth: Json, pred: Json) -> bool:
    """Mọi hình chấm có đúng loại và mọi tầng có đúng số tầng."""
    return all(ok for _, ok in kind_items(truth, pred) + storey_items(truth, pred))


def score_set(truth: Json, pred: Json) -> Json:
    """Mọi kết quả của một bộ hồ sơ; `pred` có thể là `{}` (thiếu tệp dự đoán)."""
    items = {
        "kind": kind_items(truth, pred),
        "storey": storey_items(truth, pred),
        "h_plan_section": height_items(truth, pred, "plan+section"),
        "h_plan": height_items(truth, pred, "plan"),
        "stack": stack_items(truth, pred),
    }
    return {
        "vn": truth["vn"],
        "part": truth["part"],
        "items": items,
        "extra": extra_counts(truth, pred),
        "set_exact": set_exact(truth, pred),
        "stairs": stairs_ok(truth, pred),
    }
