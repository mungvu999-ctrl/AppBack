"""Bộ dữ liệu bench TỔNG HỢP cho test: ảnh nhiễu giả, đáp án và kết quả hệ thống do mã dựng ra.

Không có dữ liệu thật hay của bên thứ ba. Mỗi "ca" là một biến thể của một căn hộ giả (hai phòng, 5 tường, 3 ô mở) với
một lỗi/nhánh cố ý để chạm từng nhánh của bộ chấm. `build_bench(root)` ghi MANIFEST.csv, plans/, answers/ và
lượt `run/`.
Bản vàng `data/golden-score.json` do BỘ CHẤM GỐC (score.py trước khi chuyển vào repo) chấm chính bộ dữ liệu này: test
đòi bộ chấm mới ra đúng từng ô.
"""

from __future__ import annotations

import copy
import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

Spec = dict[str, Any]

SC = 40.0  # mm/px của hệ thống AI (và của đáp án) ở ca chuẩn
PLAN = (200, 150)  # ảnh gốc (rộng, cao) px
PAGE = (170, 125)  # page.png = một mẩu của ảnh gốc
OFF = (12, 9)  # độ lệch thật của page.png trong ảnh gốc


def noise(w: int, h: int, seed: int) -> Any:
    """Ảnh nhiễu xám xác định (seed cố định): đủ chi tiết để dò độ lệch trang."""
    return np.random.RandomState(seed).randint(0, 255, (h, w)).astype(np.uint8)


def ai(px: float, py: float, sc: float = SC, off: tuple[int, int] = OFF) -> dict[str, float]:
    """Điểm theo px ảnh gốc -> mm của AI (điểm AI = (px - lệch) x sc)."""
    return {"x": (px - off[0]) * sc, "y": (py - off[1]) * sc}


def wall(wid: str, a: tuple[float, float], b: tuple[float, float], thick: float, sc: float = SC, **kw: Any) -> Spec:
    """Một tường theo px ảnh gốc."""
    return {
        "id": wid, "centreline": {"start": ai(*a, sc), "end": ai(*b, sc)}, "thicknessMm": thick,
        "heightMm": 3300, "openingIds": kw.get("openings", []),
    }  # fmt: skip


def opening(oid: str, wid: str, kind: str, off: float, width: float, **kw: Any) -> Spec:
    """Một ô mở (cửa/cửa sổ) trên tường `wid`."""
    door = kind == "door"
    return {
        "id": oid, "wallId": wid, "kind": kind, "offsetMm": off, "widthMm": width,
        "heightMm": kw.get("height", 2100 if door else 1200), "sillHeightMm": kw.get("sill", 0 if door else 900),
        "swing": kw.get("swing", "single"),
    }  # fmt: skip


def room(rid: str, usage: str, pts: list[tuple[float, float]], area: float, walls: list[str], sc: float = SC) -> Spec:
    """Một phòng: viền theo px ảnh gốc."""
    return {"id": rid, "usage": usage, "outline": [ai(x, y, sc) for x, y in pts], "areaM2": area, "wallIds": walls}


def apartment(sc: float = SC) -> Spec:
    """Thân `GET layer` của căn hộ giả: 5 tường, 3 ô mở, 2 phòng; tỉ lệ AI = `sc`."""
    walls = [
        wall("w1", (20, 15), (150, 15), 200, sc, openings=["o3"]),
        wall("w2", (150, 15), (150, 105), 200, sc),
        wall("w3", (150, 105), (20, 105), 200, sc, openings=["o2"]),
        wall("w4", (20, 105), (20, 15), 200, sc),
        wall("w5", (85, 15), (85, 105), 100, sc, openings=["o1"]),
    ]
    ops = [
        opening("o1", "w5", "door", 1200, 900),
        opening("o2", "w3", "door", 1000, 900),
        opening("o3", "w1", "window", 1500, 1200),
    ]
    rooms = [
        room("r1", "bedroom", [(20, 15), (85, 15), (85, 105), (20, 105)], 9.36, ["w1", "w4", "w3", "w5"], sc),
        room("r2", "livingRoom", [(85, 15), (150, 15), (150, 105), (85, 105)], 9.36, ["w1", "w2", "w3", "w5"], sc),
    ]
    return {
        "revision": 1, "level": {"id": "L1", "scaleMillimetresPerPixel": sc, "heightMm": 3300},
        "scaleStatus": "resolved", "readiness": {"state": "ready", "reasons": []},
        "layer": {"walls": walls, "openings": ops, "rooms": rooms},
    }  # fmt: skip


def base_key() -> Spec:
    """Đáp án chuẩn của căn hộ giả (khớp `apartment()`)."""
    return {
        "mm_per_px": SC, "plan_bbox_px": [20, 15, 150, 105],
        "overall": [{"axis": "x", "mm": 5200}, {"axis": "y", "mm": 3600}],
        "rooms": [
            {"name": "NGỦ", "usage": "bedroom", "area_m2": 9.0, "center_px": [52, 60]},
            {"name": "KHÁCH", "usage": "living", "area_m2": 9.0, "center_px": [117, 60]},
        ],
        "counts": {
            "doors": 2, "doors_confident": True, "windows": 1, "windows_confident": False, "bedrooms": 1, "wc": 0,
        },
        "openings": [
            {"kind": "door", "center_px": [85, 56.25]}, {"kind": "door", "center_px": [113.75, 105]},
            {"kind": "window", "center_px": [72.5, 15]},
        ],
        "total_area_m2": {"tim_tuong": 18.72},
    }  # fmt: skip


def probe() -> Spec:
    """Kết quả đo cảnh 3D (probe.json) khớp căn hộ: dựng đủ 5/2/1, vài món đồ trong phòng."""

    def item(name: str, px: float, py: float, y: float = 0.0, src: str = "x.glb") -> Spec:
        pos = [(px - OFF[0]) * SC / 1000, y, (py - OFF[1]) * SC / 1000]
        return {"id": name, "position": pos, "src": src}

    return {
        "ready": True, "counts": {"wall": 5, "door": 2, "window": 1}, "errors": [], "load_ms": 5000,
        "items": [
            item("item_bed", 50, 60), item("item_sofa", 120, 60), item("item_LIGHT1", 120, 60, 2.4),
            item("item_rug", 120, 80, 0, "rug.glb"), item("item_DRESS-sink", 120, 90), item("item_tv", 130, 40),
        ],
    }  # fmt: skip


def quality(*findings: tuple[str, str]) -> Spec:
    """Thân quality.json: các phát hiện (mã, mức) của tầng L1."""
    return {"body": {"floors": [{"floorId": "L1", "findings": [{"code": c, "severity": s} for c, s in findings]}]}}


def base_case(cid: str, group: str = "A", file: str | None = None) -> Spec:
    """Một ca chuẩn: đạt gần hết tiêu chí."""
    return {
        "cid": cid, "group": group, "file": file or f"{cid}.png", "key": base_key(), "body": apartment(),
        "meta": {"status": "completed", "process_s": 60.0}, "progress": {"status": "completed"},
        "quality": quality(("LOW_CONTRAST", "poor")), "probe": probe(), "page": "crop", "calibrated": None,
        "plan": "main",
    }  # fmt: skip


# ---------------------------------------------------------------- biến thể: mỗi hàm sửa một ca chuẩn


def _layer(s: Spec) -> Spec:
    """Phần `layer` của ca."""
    layer: Spec = s["body"]["layer"]
    return layer


def v_no_walls(s: Spec) -> None:
    """Không có tường: W1-W4 đều T."""
    _layer(s)["walls"], _layer(s)["openings"], _layer(s)["rooms"] = [], [], []


def v_big_extent(s: Spec) -> None:
    """Khung tường quá lớn: lưới vỏ vượt 4e7 ô -> W1 là "?"."""
    _layer(s)["walls"].append(wall("w9", (20, 15), (150000, 90000), 200))


def v_fragments(s: Spec) -> None:
    """Một mẩu tường ngắn lẻ loi (W4 frag) và hai tường trùng nhau (W4 over)."""
    _layer(s)["walls"] += [
        wall("w6", (300, 300), (304, 300), 200),
        wall("w7", (20, 15), (150, 15), 200),
        wall("w8", (20, 15), (100, 15), 200),
    ]


def v_zero_len(s: Spec) -> None:
    """Hai tường có đoạn dài < 1 mm: bị bỏ qua khi xét trùng."""
    _layer(s)["walls"] += [wall("w6", (300, 300), (300, 300), 200), wall("w7", (310, 300), (310, 300), 200)]


def v_unknown_scale(s: Spec) -> None:
    """AI chưa có tỉ lệ (scale = 1): S1 T, W2 "-", tm = mm/px đáp án."""
    s["body"] = apartment(sc=1.0)
    s["body"]["scaleStatus"] = "unresolved"


def v_no_key_scale(s: Spec) -> None:
    """Đáp án không có mm/px, AI có tỉ lệ: tm lấy scale AI, S1 "-", S2 theo scaleStatus."""
    s["key"]["mm_per_px"] = None
    s["body"]["scaleStatus"] = "resolved"


def v_no_key_unknown(s: Spec) -> None:
    """Không có tỉ lệ nào: tm = 20, ghi `scale_note`; S2 Đ (khai chưa rõ)."""
    v_unknown_scale(s)
    s["key"]["mm_per_px"] = None


def v_scale_off(s: Spec) -> None:
    """AI lệch tỉ lệ 7 %: S1 T, S3 và P5/P6 lệch (lỗi tỉ lệ; readiness `scaleUnresolved` thì không im lặng)."""
    s["body"] = apartment(sc=SC * 1.07)
    s["body"]["readiness"] = {"state": "ready", "reasons": ["scaleUnresolved"]}


def v_scale_off_silent(s: Spec) -> None:
    """Như `v_scale_off` nhưng không báo gì: lỗi im lặng."""
    s["body"] = apartment(sc=SC * 1.07)
    s["quality"] = quality()


def v_scale_slight(s: Spec) -> None:
    """AI lệch tỉ lệ 3 %: S1 C."""
    s["body"] = apartment(sc=SC * 1.03)


def v_overall_ref(s: Spec) -> None:
    """Đáp án "tổng" ngắn hơn khung: dùng `mm_ref`; thêm kích thước tổng 0 (bỏ qua)."""
    s["key"]["overall"] = [
        {"axis": "x", "mm": 3000, "px0": 20, "px1": 70},
        {"axis": "y", "mm": 0, "px0": 15, "px1": 105},
    ]


def v_overall_full(s: Spec) -> None:
    """Đáp án "tổng" phủ gần hết khung: không có `mm_ref`."""
    s["key"]["overall"] = [{"axis": "x", "mm": 5200, "px0": 20, "px1": 150}]


def v_overall_axes(s: Spec) -> None:
    """Đáp án có mm/px riêng từng trục."""
    s["key"]["mm_per_px_axes"] = {"x": SC, "y": SC}
    s["key"]["overall"] = [{"axis": "y", "mm": 3600, "px0": 15, "px1": 60}]


def v_frame_out(s: Spec) -> None:
    """Một tường nằm ngoài khung căn (> 500 mm): S4 T; phòng ngoài khung cũng T."""
    _layer(s)["walls"].append(wall("w6", (185, 140), (195, 140), 200))


def v_frame_small_out(s: Spec) -> None:
    """Tường ngoài khung 400 mm: S4 C."""
    _layer(s)["walls"].append(wall("w6", (185, 140), (195, 140), 100))
    s["key"]["plan_bbox_px"] = [20, 15, 150, 105]


def v_room_out(s: Spec) -> None:
    """Một phòng có tâm ngoài khung: S4 T."""
    _layer(s)["rooms"].append(room("r3", "other", [(180, 130), (190, 130), (190, 140), (180, 140)], 0.16, ["w1"]))


def v_wall_thickness(s: Spec) -> None:
    """Tường dày lệch bậc chuẩn: W2 C/T; tường > 450 mm không tính."""
    for i, w in enumerate(_layer(s)["walls"]):
        w["thicknessMm"] = [175, 200, 200, 200, 100][i]


def v_wall_thickness_bad(s: Spec) -> None:
    """Nhiều tường lệch bậc: W2 T."""
    for w in _layer(s)["walls"]:
        w["thicknessMm"] = 175
    _layer(s)["walls"].append(wall("wbig", (300, 300), (330, 300), 600))


def v_height_bad(s: Spec) -> None:
    """Một tường khác chiều cao tầng: W3 T."""
    _layer(s)["walls"][0]["heightMm"] = 2900


def v_open_envelope(s: Spec) -> None:
    """Hở vỏ: bỏ tường dưới và phải -> phòng bị lũ, W1 T, leaked."""
    _layer(s)["walls"] = [w for w in _layer(s)["walls"] if w["id"] in ("w1", "w5")]
    _layer(s)["openings"] = [o for o in _layer(s)["openings"] if o["wallId"] in ("w1", "w5")]
    for w in _layer(s)["walls"]:
        w["openingIds"] = [o["id"] for o in _layer(s)["openings"] if o["wallId"] == w["id"]]
    for r in _layer(s)["rooms"]:
        r["wallIds"] = [i for i in r["wallIds"] if i in ("w1", "w5")]


def v_half_open(s: Spec) -> None:
    """Một trong hai phòng hở vỏ: W1 ở mức C."""
    v_open_envelope(s)
    _layer(s)["walls"].append(wall("w2", (150, 15), (150, 105), 200))
    _layer(s)["walls"].append(wall("w3", (150, 105), (85, 105), 200))
    _layer(s)["walls"].append(wall("w4", (20, 105), (20, 15), 200))


def v_room_pts_outside_grid(s: Spec) -> None:
    """Điểm nhãn phòng nằm ngoài lưới: bị loại (cells rỗng nếu tất cả)."""
    s["key"]["rooms"] = [{"name": "X", "usage": "bedroom", "area_m2": 9.0, "center_px": [-5000, -5000]}]


def v_group_c(s: Spec) -> None:
    """Nhóm C: ảnh xấu; cảnh báo `poor` tính là có báo."""
    s["group"] = "C"
    s["quality"] = quality(("LOW_CONTRAST", "poor"))


def v_silent_c(s: Spec) -> None:
    """Nhóm C: không có cảnh báo mức poor và lỗi tỉ lệ: im lặng."""
    s["group"] = "C"
    s["quality"] = quality(("LOW_CONTRAST", "fair"))
    s["body"] = apartment(sc=SC * 1.07)


def v_specific_finding(s: Spec) -> None:
    """Có mã cảnh báo riêng: tính là có báo."""
    s["quality"] = quality(("OPENING_MISSING", "fair"))
    s["body"] = apartment(sc=SC * 1.07)


def v_failed(s: Spec) -> None:
    """Lượt thất bại: R1 T."""
    s["meta"] = {"status": "failed", "process_s": 5.0}


def v_no_meta(s: Spec) -> None:
    """Không có meta.json: trạng thái lấy từ progress; thiếu cả hai -> "missing"."""
    s["meta"] = None
    s["progress"] = {"status": "completed", "error": "SOME_ERROR"}


def v_no_progress(s: Spec) -> None:
    """Thiếu meta và progress: trạng thái "missing"."""
    s["meta"] = None
    s["progress"] = None


def v_slow(s: Spec) -> None:
    """Xử lý chậm: R1 C (180-360 s), U1 theo thời gian."""
    s["meta"]["process_s"] = 250.0


def v_very_slow(s: Spec) -> None:
    """Xử lý rất chậm: R1 T."""
    s["meta"]["process_s"] = 900.0
    s["probe"]["load_ms"] = 400000


def v_no_time(s: Spec) -> None:
    """Không có process_s: R1 "-"; U1 "-"."""
    s["meta"] = {"status": "completed"}


def v_big_file(s: Spec) -> None:
    """Tệp lớn (huge*): hạn giờ 360 s."""
    s["file"] = "huge-" + s["cid"] + ".png"
    s["meta"]["process_s"] = 300.0


def v_broken_refs(s: Spec) -> None:
    """Tham chiếu treo: ô mở trỏ tường không có, tường trỏ ô mở không có, phòng trỏ tường không có."""
    _layer(s)["openings"].append(opening("o9", "w404", "door", 0, 900))
    _layer(s)["walls"][1]["openingIds"] = ["o404"]
    _layer(s)["rooms"][0]["wallIds"].append("w404")


def v_two_page(s: Spec) -> None:
    """Ca `two-page-ch04`: R3 xem tay."""
    s["cid"] = "two-page-ch04"
    s["file"] = "two-page-ch04.png"


def v_no_page(s: Spec) -> None:
    """Không có page.png: không dò được độ lệch (off None)."""
    s["page"] = None


def v_page_equal(s: Spec) -> None:
    """page.png cùng cỡ ảnh gốc: độ lệch (0, 0); toạ độ AI theo px ảnh gốc."""
    s["page"] = "full"
    s["body"] = shifted_apartment(SC, OFF[0] * SC, OFF[1] * SC)
    s["probe"] = None


def v_page_bigger(s: Spec) -> None:
    """page.png lớn hơn ảnh gốc: không khớp (off None)."""
    s["page"] = "bigger"


def v_page_mismatch(s: Spec) -> None:
    """page.png là ảnh khác hẳn: dò không khớp (off None)."""
    s["page"] = "other"


def v_no_probe(s: Spec) -> None:
    """Không có probe.json: M0 và F1-F4 là "-"; U1 "-"."""
    s["probe"] = None


def v_probe_bad(s: Spec) -> None:
    """Cảnh 3D lỗi: M0 T; đồ ngoài phòng, chắn cửa, trùng chỗ."""
    s["probe"]["ready"] = False
    s["probe"]["items"] = [
        {"id": "item_a", "position": [100, 0, 100], "src": "x"},
        {"id": "item_b", "position": [100.1, 0, 100.1], "src": "x"},
        {"id": "item_c", "position": [1.5, 0, 2.0], "src": "x"},
        {"id": "item_d", "position": [1.55, 0, 2.05], "src": "x"},
    ]


def v_probe_gap(s: Spec) -> None:
    """Cảnh 3D dựng thiếu vài ô mở: M0 theo độ lệch số đếm."""
    s["probe"]["counts"] = {"wall": 5, "door": 1, "window": 0}


def v_probe_errors(s: Spec) -> None:
    """Cảnh 3D có lỗi: M0 T."""
    s["probe"]["errors"] = ["x"]


def v_probe_no_items(s: Spec) -> None:
    """Probe không có món đồ nào: F2 "-", F1 T (phòng ngủ không có đồ)."""
    s["probe"]["items"] = []


def v_probe_item_no_pos(s: Spec) -> None:
    """Một món không có vị trí: bỏ qua."""
    s["probe"]["items"].append({"id": "item_np", "src": "x"})


def v_group_e(s: Spec) -> None:
    """Nhóm E: không dò độ lệch trang."""
    s["group"] = "E"


def v_group_f(s: Spec) -> None:
    """Nhóm F."""
    s["group"] = "F"


def v_group_d_plain(s: Spec) -> None:
    """Nhóm D, đáp án "mo-hinh-day-du": chấm như mô hình thường."""
    s["group"] = "D"
    s["key"] = {"expect": "mo-hinh-day-du"}


def v_vector(s: Spec) -> None:
    """Căn vẽ vector: S5 theo kích thước tổng, không mm/px."""
    s["group"] = "C"
    s["key"] = {
        "overall_mm": [5200, 3600], "rooms": [{"name": "NGỦ", "usage": "bedroom", "area_m2": 9.0}],
        "counts": {"doors": 2, "windows": 1, "bedrooms": 1, "wc": 0},
    }  # fmt: skip


def v_vector_noext(s: Spec) -> None:
    """Căn vector mà AI chưa có tỉ lệ: S5 T."""
    v_vector(s)
    s["body"] = apartment(sc=1.0)


def v_vector_bad(s: Spec) -> None:
    """Căn vector lệch kích thước tổng: S5 C/T."""
    v_vector(s)
    s["key"]["overall_mm"] = [5400, 3700]


def v_exclude(s: Spec) -> None:
    """Đáp án loại hai tiêu chí (M4)."""
    s["key"]["exclude"] = ["O1", "ZZ"]
    s["key"]["counts"]["doors"] = 7


def v_counts(s: Spec) -> None:
    """Số cửa/cửa sổ sai nhiều, không có nhãn phòng/vị trí ô mở trong đáp án."""
    s["key"]["counts"].update(doors=5, windows=4, bedrooms=3, wc=2)
    s["key"]["rooms"] = []
    s["key"]["openings"] = []
    s["key"]["total_area_m2"] = {"thong_thuy": 15.0}


def v_counts_none(s: Spec) -> None:
    """Đáp án không có số đếm."""
    s["key"]["counts"] = {}
    s["key"]["total_area_m2"] = {}
    s["key"]["rooms_extra"] = 1


def v_opening_sizes(s: Spec) -> None:
    """Ô mở sai kích thước: cửa hẹp, cửa trượt rộng, cửa sổ sát trần/quá rộng; ô mở chồng nhau và vượt tường."""
    ops = _layer(s)["openings"]
    ops[0].update(widthMm=300)
    ops[1].update(widthMm=3000, swing="sliding")
    ops.append(opening("o4", "w1", "window", 3000, 4500, height=1200, sill=2500))
    ops.append(opening("o5", "w1", "window", 3200, 1200))
    ops.append(opening("o6", "w2", "door", 3000, 900, height=1000))


def v_opening_long_window(s: Spec) -> None:
    """Cửa sổ dài hơn 3,6 m nhưng không vượt tường chủ: hợp lệ."""
    _layer(s)["openings"][2].update(offMm=0, widthMm=4000)
    _layer(s)["openings"][2]["offsetMm"] = 100


def v_no_doors(s: Spec) -> None:
    """Không có cửa: phòng ngủ thiếu cửa, không có cửa ra ngoài (O5 T)."""
    _layer(s)["openings"] = [o for o in _layer(s)["openings"] if o["kind"] != "door"]


def v_door_missing_room(s: Spec) -> None:
    """Cửa chỉ ở ngoài, WC không cửa: O5 theo số phòng thiếu cửa."""
    _layer(s)["rooms"].append(room("r3", "bathroom", [(30, 20), (40, 20), (40, 30), (30, 30)], 0.16, ["w1"]))
    s["key"]["counts"]["wc"] = 1


def v_door_in_room(s: Spec) -> None:
    """Cửa nằm trong phòng WC (inside): có cửa."""
    v_door_missing_room(s)
    _layer(s)["rooms"][2]["outline"] = [ai(*p) for p in [(80, 50), (95, 50), (95, 60), (80, 60)]]


def v_room_bad(s: Spec) -> None:
    """Phòng đa giác tự cắt, phòng nhỏ dưới 1 m2, hai phòng chồng nhau nhiều."""
    _layer(s)["rooms"].append(room("r3", "other", [(30, 20), (60, 50), (60, 20), (30, 50)], 5.0, ["w1"]))
    _layer(s)["rooms"].append(room("r4", "utility", [(30, 30), (32, 30), (32, 32), (30, 32)], 0.2, ["w1"]))
    _layer(s)["rooms"].append(room("r5", "kitchen", [(25, 20), (80, 20), (80, 100), (25, 100)], 15.0, []))


def v_room_overlap_only(s: Spec) -> None:
    """Hai phòng chồng nhau nhiều nhưng không tự cắt và không nhỏ."""
    _layer(s)["rooms"].append(room("r3", "kitchen", [(25, 20), (80, 20), (80, 100), (25, 100)], 15.0, ["w1"]))


def v_labels(s: Spec) -> None:
    """Nhãn phòng: một nhãn rơi vào hai phòng, một rơi ngoài, một công năng lạ; sai công năng."""
    s["key"]["rooms"] = [
        {"name": "A", "usage": "kitchen", "area_m2": 9.0, "center_px": [52, 60]},
        {"name": "B", "usage": "weird", "area_m2": None, "center_px": [117, 60]},
        {"name": "C", "usage": "bedroom", "area_m2": 9.0, "center_px": [300, 300]},
    ]
    _layer(s)["rooms"].append(room("r3", "bedroom", [(100, 40), (130, 40), (130, 80), (100, 80)], 4.8, ["w5"]))


def v_labels_none_good(s: Spec) -> None:
    """Mọi nhãn rơi ngoài phòng: không có nhãn tốt (P4 = 0)."""
    s["key"]["rooms"] = [{"name": "C", "usage": "bedroom", "area_m2": 9.0, "center_px": [300, 300]}]


def v_thong_thuy(s: Spec) -> None:
    """Tổng diện tích đáp án là thông thủy."""
    s["key"]["total_area_m2"] = {"thong_thuy": 17.0}


def v_no_rooms_in_layer(s: Spec) -> None:
    """Có tường nhưng không có phòng: O5 T, P7 T."""
    _layer(s)["rooms"] = []


def v_many_fixes(s: Spec) -> None:
    """Nhiều chỗ cần sửa tay: U3 T."""
    s["key"]["counts"].update(doors=9, windows=9)
    s["body"]["scaleStatus"] = "unresolved"


def v_calibrated_good(s: Spec) -> None:
    """Tự động trượt (lệch tỉ lệ), sau bước đặt tỉ lệ đạt: "1 bước"."""
    s["body"] = apartment(sc=SC * 1.07)
    s["body"]["readiness"] = {"state": "ready", "reasons": ["scaleUnresolved"]}
    s["calibrated"] = {"body": apartment(sc=SC), "quality": quality(("LOW_CONTRAST", "poor")), "probe": probe()}


def v_calibrated_fail(s: Spec) -> None:
    """Tự động trượt, sau bước vẫn trượt: "trượt" nhưng có `auto`."""
    s["body"] = apartment(sc=SC * 1.07)
    s["calibrated"] = {"body": apartment(sc=SC * 1.07), "quality": None, "probe": None}


def v_calibrated_ok(s: Spec) -> None:
    """Tự động đạt và còn bản calibrated: kết luận tự động."""
    s["calibrated"] = {"body": apartment(sc=SC), "quality": quality(), "probe": probe()}


def v_old_layout(s: Spec) -> None:
    """Lượt cũ: tệp <id>.layer.json, <id>.quality.json ở thư mục lượt, không có thư mục <id>/."""
    s["old_layout"] = True
    s["page"] = None


# ---- đáp án hình học (nhóm B)


def geometry_key(win: bool = True) -> Spec:
    """Đáp án nhóm B: tường và ô mở chính xác theo mm trong `viewBox`, khớp (xấp xỉ) căn hộ giả."""
    return {
        "viewBox": [0, 0, 5200, 3600], "px": 1 / SC, "W": 5200, "H": 3600,
        "rooms": [
            {"name": "PHÒNG NGỦ 1", "area_m2": 9.36, "rect_mm": [0, 0, 2600, 3600]},
            {"name": "BẾP", "area_m2": 9.36, "rect_mm": [2600, 0, 5200, 3600]},
            {"name": "KHU KHÁC", "area_m2": 1.0, "rect_mm": [0, 0, 100, 100]},
        ],
        "walls": [
            {"o": "h", "a": 0, "b": 5200, "c": 0, "t": 200}, {"o": "h", "a": 0, "b": 5200, "c": 3600, "t": 200},
            {"o": "v", "a": 0, "b": 3600, "c": 0, "t": 200}, {"o": "v", "a": 0, "b": 3600, "c": 5200, "t": 200},
            {"o": "v", "a": 0, "b": 3600, "c": 2600, "t": 100},
        ],
        "openings": [
            {"o": "v", "a": 1200, "b": 2100, "c": 2600, "type": "door"},
            {"o": "h", "a": 3300, "b": 4200, "c": 3600, "type": "door2"},
            {"o": "h", "a": 1500, "b": 2700, "c": 0, "type": "win" if win else "gap"},
            {"o": "h", "a": 100, "b": 200, "c": 0, "type": "gap"},
        ],
    }  # fmt: skip


def v_group_b(s: Spec) -> None:
    """Nhóm B: đáp án hình học; AI khớp (W5, W6, O2 đạt). Lệch toạ độ do `viewBox` gốc (0,0) = góc căn."""
    s["group"] = "B"
    s["key"] = geometry_key()
    s["page"] = "full"
    s["body"] = shifted_apartment(SC, OFF[0] * SC - 20 * SC, OFF[1] * SC - 15 * SC)
    s["probe"] = None


def shifted_apartment(sc: float, dx: float, dy: float) -> Spec:
    """Căn hộ giả có toạ độ AI dịch (dx, dy) mm: để góc căn trùng gốc `viewBox`."""
    body = apartment(sc)
    for w in body["layer"]["walls"]:
        for e in ("start", "end"):
            w["centreline"][e] = {"x": w["centreline"][e]["x"] + dx, "y": w["centreline"][e]["y"] + dy}
    for r in body["layer"]["rooms"]:
        r["outline"] = [{"x": p["x"] + dx, "y": p["y"] + dy} for p in r["outline"]]
    return body


def v_group_b_bad(s: Spec) -> None:
    """Nhóm B, AI thiếu tường, tường dày sai, ô mở lệch: W5 T, W6 T, O2 T."""
    v_group_b(s)
    layer = s["body"]["layer"]
    layer["walls"] = layer["walls"][:3]
    layer["walls"][0]["thicknessMm"] = 400
    layer["openings"] = [o for o in layer["openings"] if o["id"] == "o3"]


def v_group_b_mid(s: Spec) -> None:
    """Nhóm B, AI dư một tường dài: precision thấp (W5 C/T)."""
    v_group_b(s)
    extra = {"start": {"x": 0, "y": 1800}, "end": {"x": 5200, "y": 1800}}
    s["body"]["layer"]["walls"].append(
        {"id": "w6", "centreline": extra, "thicknessMm": 100, "heightMm": 3300, "openingIds": []}
    )


# ---- PDF raster và nhóm C dẫn xuất


def v_pdf(s: Spec) -> None:
    """Tệp PDF: không dò độ lệch bằng ảnh gốc; S4/O1c đo cỡ trên page.png."""
    s["file"] = s["cid"] + ".pdf"
    s["plan"] = None
    s["page"] = "crop"


def v_pdf_raster(s: Spec) -> None:
    """PDF gói ảnh raster: đáp án đo trên raster rộng gấp đôi page.png -> đổi pixel; độ lệch (0, 0)."""
    v_pdf(s)

    def raster(p: list[float]) -> list[float]:
        return [(p[0] - OFF[0]) * 2, (p[1] - OFF[1]) * 2]

    k = s["key"]
    k["pdf_raster_px"] = True
    k["image_px"] = [PAGE[0] * 2, PAGE[1] * 2]
    k["mm_per_px"] = SC / 2
    k["mm_per_px_axes"] = {"x": SC / 2, "y": SC / 2}
    bb = k["plan_bbox_px"]
    k["plan_bbox_px"] = raster(bb[:2]) + raster(bb[2:])
    k["rooms"] = [dict(r, center_px=raster(r["center_px"])) for r in k["rooms"]]
    k["openings"] = [dict(o, center_px=raster(o["center_px"])) for o in k["openings"]]
    k["overall"] = [
        {"axis": "x", "mm": 3000, "px0": raster([20, 0])[0], "px1": raster([45, 0])[0]},
        {"axis": "y", "mm": 3600, "px0": raster([0, 15])[1], "px1": raster([0, 105])[1]},
    ]


def v_pdf_raster_nokey(s: Spec) -> None:
    """PDF raster mà đáp án không có mm/px, bbox hay phòng."""
    v_pdf_raster(s)
    s["key"]["mm_per_px"] = None
    s["key"]["plan_bbox_px"] = None
    s["key"]["rooms"] = []
    s["key"]["mm_per_px_axes"] = None
    s["key"]["openings"] = []
    s["key"]["overall"] = []


def derived_case(cid: str, base: str, file: str, plan: str | None = "main", page: str | None = "crop") -> Spec:
    """Nhóm C dẫn xuất từ `base`; `plan`: kích thước ảnh gốc của chính ca."""
    s = base_case(cid, "C", file)
    s["key"] = {"derived_from": base}
    s["plan"] = plan
    s["page"] = page
    return s


# ---- nhóm D: hành vi


def behaviour_case(cid: str, expect: str, **kw: Any) -> Spec:
    """Ca hành vi nhóm D: layer/readiness/tiến độ tuỳ biến."""
    s = base_case(cid, "D")
    s["key"] = {"expect": expect}
    s["page"] = None
    s["probe"] = None
    if "body" in kw:
        s["body"] = kw["body"]
    if "meta" in kw:
        s["meta"] = kw["meta"]
    if "progress" in kw:
        s["progress"] = kw["progress"]
    if "quality" in kw:
        s["quality"] = kw["quality"]
    return s


def ready_with(checks: list[str] | None = None, state: str = "ready", reasons: list[str] | None = None) -> Spec:
    """Căn hộ với readiness tuỳ biến."""
    b = apartment()
    b["readiness"] = {"state": state, "reasons": reasons or [], "checks": checks or []}
    return b


def empty_body() -> Spec:
    """Mô hình không có tường."""
    b = apartment()
    b["layer"] = {"walls": [], "openings": [], "rooms": []}
    return b


VARIANTS: dict[str, Callable[[Spec], None]] = {
    "base": lambda s: None,
    "no-walls": v_no_walls, "big-extent": v_big_extent, "fragments": v_fragments, "zero-len": v_zero_len,
    "unknown-scale": v_unknown_scale, "no-key-scale": v_no_key_scale, "no-key-unknown": v_no_key_unknown,
    "scale-off": v_scale_off, "scale-off-silent": v_scale_off_silent, "scale-slight": v_scale_slight,
    "overall-ref": v_overall_ref, "overall-full": v_overall_full, "overall-axes": v_overall_axes,
    "frame-out": v_frame_out, "frame-small-out": v_frame_small_out, "room-out": v_room_out,
    "wall-thickness": v_wall_thickness, "wall-thickness-bad": v_wall_thickness_bad, "height-bad": v_height_bad,
    "open-envelope": v_open_envelope, "half-open": v_half_open, "pts-outside": v_room_pts_outside_grid,
    "group-c": v_group_c, "silent-c": v_silent_c, "specific-finding": v_specific_finding,
    "failed": v_failed, "no-meta": v_no_meta, "no-progress": v_no_progress, "slow": v_slow, "very-slow": v_very_slow,
    "no-time": v_no_time, "big-file": v_big_file, "broken-refs": v_broken_refs, "two-page": v_two_page,
    "no-page": v_no_page, "page-equal": v_page_equal, "page-bigger": v_page_bigger, "page-mismatch": v_page_mismatch,
    "no-probe": v_no_probe, "probe-bad": v_probe_bad, "probe-gap": v_probe_gap, "probe-errors": v_probe_errors,
    "probe-no-items": v_probe_no_items, "probe-item-no-pos": v_probe_item_no_pos,
    "group-e": v_group_e, "group-f": v_group_f, "group-d-plain": v_group_d_plain,
    "vector": v_vector, "vector-noext": v_vector_noext, "vector-bad": v_vector_bad, "exclude": v_exclude,
    "counts": v_counts, "counts-none": v_counts_none, "opening-sizes": v_opening_sizes,
    "opening-long-window": v_opening_long_window, "no-doors": v_no_doors, "door-missing-room": v_door_missing_room,
    "door-in-room": v_door_in_room, "room-bad": v_room_bad, "room-overlap": v_room_overlap_only, "labels": v_labels,
    "labels-none-good": v_labels_none_good, "thong-thuy": v_thong_thuy, "no-rooms": v_no_rooms_in_layer,
    "many-fixes": v_many_fixes, "cal-good": v_calibrated_good, "cal-fail": v_calibrated_fail, "cal-ok": v_calibrated_ok,
    "old-layout": v_old_layout, "group-b": v_group_b, "group-b-bad": v_group_b_bad, "group-b-mid": v_group_b_mid,
    "pdf": v_pdf, "pdf-raster": v_pdf_raster, "pdf-raster-nokey": v_pdf_raster_nokey,
}  # fmt: skip


def all_specs() -> list[Spec]:
    """Mọi ca của bộ tổng hợp, theo thứ tự cố định."""
    specs = []
    for name, fn in VARIANTS.items():
        s = base_case("v-" + name)
        fn(s)
        specs.append(s)
    # nhóm C dẫn xuất: gốc `v-base` (plan main) -> bản nhỏ, bản ảnh chụp, bản PDF
    specs += [
        derived_case("c-small", "v-base", "c-small.png", plan="half"),
        derived_case("c-photo", "v-base", "photo-c.jpg", plan="half"),
        derived_case("c-pdf", "v-base", "c-pdf.pdf", plan=None),
    ]
    specs += behaviour_specs()
    return specs


def behaviour_specs() -> list[Spec]:
    """Các ca nhóm D, mỗi ca chạm một nhánh của `behaviour_marks`/`behaviour_verdict`."""
    multi = ready_with(["MULTIPLE_PLANS"])
    out = [
        behaviour_case("d-tach-ok", "tach-2", body=multi),
        behaviour_case("d-tach-no", "tach-2"),
        behaviour_case("d-tach-slow", "tach-2", meta={"status": "failed"}),
        behaviour_case("d-chan-failed", "chan-1", meta={"status": "failed", "process_s": 3.0}),
        behaviour_case("d-chan-nowalls", "chan-2", body=empty_body()),
        behaviour_case("d-chan-blocked", "chan-3", body=ready_with(state="blocked")),
        behaviour_case("d-chan-code", "chan-4", progress={"status": "completed", "error": "NOT_A_FLOOR_PLAN"}),
        behaviour_case("d-chan-persp", "chan-5", quality=quality(("PERSPECTIVE_DETECTED", "poor"))),
        behaviour_case("d-chan-built", "chan-6"),
        behaviour_case("d-other", "khac-lạ"),
        behaviour_case("d-other-failed", "khac-lạ", meta={"status": "failed"}),
    ]
    out += [behaviour_case("nt-01-single", "mo-hinh-day-du")]
    out[-1]["key"] = {"expect": "mo-hinh-day-du"}
    same = behaviour_case("d-same-ok", "giong-01")
    same["body"] = apartment()
    diff = behaviour_case("d-same-diff", "giong-01")
    diff["body"] = empty_body()
    diff2 = behaviour_case("d-same-signal", "giong-01", body=empty_body(), progress={"status": "failed", "error": "X"})
    cal = behaviour_case("d-cal", "khac-lạ")
    cal["calibrated"] = {"body": apartment(), "quality": None, "probe": None}
    return [*out, same, diff, diff2, cal]


# ---------------------------------------------------------------- ghi ra đĩa


def _write(p: Path, data: Any) -> None:
    """Ghi JSON UTF-8."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _plan_image(root: Path, s: Spec) -> None:
    """Ghi ảnh gốc của ca (nếu có): `main` = PLAN, `half` = nửa kích thước, None = không ghi (PDF)."""
    if s["plan"] is None or s["file"].endswith(".pdf"):
        return
    w, h = (PLAN[0] // 2, PLAN[1] // 2) if s["plan"] == "half" else PLAN
    (root / "plans").mkdir(exist_ok=True)
    Image.fromarray(noise(w, h, 1)).save(root / "plans" / s["file"])


def _page_image(root: Path, run: Path, s: Spec) -> None:
    """Ghi page.png của ca theo kiểu `s["page"]`: crop (đúng một mẩu của ảnh gốc), full, bigger, other."""
    kind = s["page"]
    if kind is None:
        return
    full = noise(*PLAN, 1)
    img = {
        "crop": full[OFF[1] : OFF[1] + PAGE[1], OFF[0] : OFF[0] + PAGE[0]],
        "full": full,
        "bigger": noise(PLAN[0] + 40, PLAN[1] + 40, 1),
        "other": noise(PAGE[0], PAGE[1], 99),
    }[kind]
    d = run / s["cid"]
    d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(d / "page.png")


def _write_results(run: Path, s: Spec) -> None:
    """Ghi meta/progress/layer/quality/probe (+ calibrated/) của ca; `old_layout`: tệp `<id>.<tên>.json` ở lượt."""
    cid, d = s["cid"], run / s["cid"]

    def put(name: str, data: Any, sub: str = "") -> None:
        if data is None:
            return
        if s.get("old_layout") and not sub:
            _write(run / f"{cid}.{name}.json", data)
        else:
            _write(d / sub / f"{name}.json", data)

    put("meta", s["meta"])
    put("progress", s["progress"])
    put("layer", {"status": 200, "body": s["body"]})
    put("quality", s["quality"])
    put("probe", s["probe"])
    if s["calibrated"]:
        c = s["calibrated"]
        put("layer", {"status": 200, "body": c["body"]}, "calibrated")
        put("quality", c["quality"], "calibrated")
        put("probe", c["probe"], "calibrated")


def build_bench(root: Path) -> Path:
    """Ghi bộ dữ liệu tổng hợp dưới `root`; trả thư mục lượt `root/run`."""
    run = root / "run"
    run.mkdir(parents=True, exist_ok=True)
    (root / "answers").mkdir(exist_ok=True)
    specs = all_specs()
    with (root / "MANIFEST.csv").open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "file", "group", "source", "expect", "note"])
        for s in specs:
            wr.writerow([s["cid"], s["file"], s["group"], "tổng hợp", "", ""])
    for s in specs:
        _write(root / "answers" / f"{s['cid']}.json", copy.deepcopy(s["key"]))
        _plan_image(root, s)
        _page_image(root, run, s)
        _write_results(run, s)
    return run
