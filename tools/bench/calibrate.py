"""M5: bước người dùng "Đặt tỷ lệ" (ScaleCalibration -> saveScale -> PUT spatial/layer) áp lên ca đã chạy.

Ca xong mà readiness có `scaleUnresolved` và đáp án có mm/px: gửi tỉ lệ đáp án như màn Đặt tỷ lệ, rồi lấy lại
layer/spatial/quality vào `<id>/calibrated/` (+ `cal.json`: tỉ lệ cũ/mới, hệ thống co giãn hay dựng lại). Máy chủ chỉ co
giãn hình học theo k = mới/cũ (packages/domain/scale/rescale.py), không chạy lại pipeline.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from tools.bench.api import expect
from tools.bench.score.io import Bench, Json

Api = Callable[..., tuple[int, Any]]
_DERIVED_LOSSY = "bản dẫn xuất không giữ tỉ lệ"


def _width(p: Path) -> int:
    """Bề rộng ảnh (px)."""
    with Image.open(p) as img:
        return int(img.width)


def _derived_scale(bench: Bench, cid: str, k: Json) -> tuple[float | None, str]:
    """Nhóm C: chỉ bản phóng/thu/nén của ảnh gốc giữ được tỉ lệ (đáp án bản gốc chia hệ số bề rộng)."""
    f = bench.manifest[cid]["file"]
    base = json.loads((bench.root / "answers" / f"{k['derived_from']}.json").read_text(encoding="utf-8"))
    if not (f.endswith((".png", ".jpg")) and not f.startswith("photo") and base.get("mm_per_px")):
        return None, _DERIVED_LOSSY
    plans = bench.root / "plans"
    r = _width(plans / f) / _width(plans / bench.manifest[k["derived_from"]]["file"])
    return base["mm_per_px"] / r, "từ " + k["derived_from"]


def key_scale(bench: Bench, cid: str, d: Path) -> tuple[float | None, str]:
    """mm/px đáp án theo pixel của page.png (cùng quy đổi như `Bench.key_of`); (None, lý do) khi không đặt được."""
    k = json.loads((bench.root / "answers" / f"{cid}.json").read_text(encoding="utf-8"))
    if k.get("mm_per_px_suspect"):
        return None, "đáp án nghi sai tỉ lệ"
    if "viewBox" in k:  # nhóm B: px = px/mm của ảnh tổng hợp
        return 1 / k["px"], "nhóm B"
    if "derived_from" in k:
        return _derived_scale(bench, cid, k)
    mpp = k.get("mm_per_px")
    if not mpp:
        return None, "đáp án không có mm_per_px"
    if k.get("pdf_raster_px") and k.get("image_px"):  # PDF gói raster: đáp án đo trên raster, hệ thống dựng page.png
        if not (d / "page.png").exists():
            return None, "PDF thiếu page.png"
        return mpp * k["image_px"][0] / _width(d / "page.png"), "PDF quy về page.png"
    return mpp, "đáp án"


def _fetch_calibrated(api: Api, pid: str, fid: str, out: Path) -> Json:
    """Lấy lại layer/spatial/quality sau khi đặt tỉ lệ vào `out`; trả thân layer."""
    out.mkdir(exist_ok=True)
    layer: Json = {}
    for name, path in (
        ("layer", f"/floors/{fid}/spatial/layer"),
        ("spatial", f"/floors/{fid}/spatial"),
        ("quality", f"/floors/{fid}/quality"),
    ):
        s, body = api("GET", f"/projects/{pid}{path}")
        expect(s == 200, f"{name} {s}")
        (out / f"{name}.json").write_text(
            json.dumps({"status": s, "body": body}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if name == "layer":
            layer = body
    return layer


def _max_wall_deviation(before: Json, after: Json, k: float | None) -> float | None:
    """Lệch lớn nhất (mm) giữa toạ độ tường sau và (toạ độ trước x k) cho các tường còn id; None khi không có k."""
    if not k:
        return None
    wb = {w["id"]: w for w in before["layer"]["walls"]}
    devs = [
        abs(w["centreline"][e][a] - wb[w["id"]]["centreline"][e][a] * k)
        for w in after["layer"]["walls"]
        if w["id"] in wb
        for e in ("start", "end")
        for a in ("x", "y")
    ]
    return max(devs, default=None)


def _rebuild_info(before: Json, after: Json, ratio: float, old: float | None) -> Json:
    """Dựng lại hay chỉ co giãn: cùng id + toạ độ = toạ độ cũ x k (+-1 mm làm tròn) thì chỉ co giãn."""
    k = ratio / old if old else None
    lb, la = before["layer"], after["layer"]
    ids = {n: ({x["id"] for x in lb[n]}, {x["id"] for x in la[n]}) for n in ("walls", "openings", "rooms")}
    dev = _max_wall_deviation(before, after, k)
    same = all(a == b for a, b in ids.values())
    return {
        "k": k, "same_ids": same, "counts": {n: [len(a), len(b)] for n, (a, b) in ids.items()},
        "max_wall_dev_mm": dev, "rebuilt": not (same and dev is not None and dev <= 1),
    }  # fmt: skip


def calibrate(api: Api, bench: Bench, row: dict[str, str], run_out: Path) -> str:
    """Làm bước "Đặt tỷ lệ" cho ca `row`; trả một dòng kết quả (bỏ qua/lỗi/đã đặt) để in."""
    cid = row["id"]
    d = run_out / cid
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    first = json.loads((d / "layer.json").read_text(encoding="utf-8"))["body"] or {}
    if "scaleUnresolved" not in ((first.get("readiness") or {}).get("reasons") or []):
        return "bỏ: không có scaleUnresolved"
    ratio, why = key_scale(bench, cid, d)
    if not ratio:
        return f"bỏ: {why}"
    pid, fid = meta["projectId"], meta["floorId"]
    s, before = api("GET", f"/projects/{pid}/floors/{fid}/spatial/layer")
    if s != 200:
        return f"bỏ: GET layer {s} (dự án đã bị xoá?)"
    old = before["level"].get("scaleMillimetresPerPixel")
    s, put = api(
        "PUT",
        f"/projects/{pid}/floors/{fid}/spatial/layer",
        {"baseVersion": before["revision"], "body": {"scaleMillimetresPerPixel": round(ratio, 6)}},
    )
    if s != 200:
        return f"LỖI PUT {s}: {put}"
    after = _fetch_calibrated(api, pid, fid, d / "calibrated")
    info = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "scale_before": old,
        "scale_after": after["level"].get("scaleMillimetresPerPixel"), "ratio_sent": round(ratio, 6),
        "ratio_source": why, **_rebuild_info(before, after, ratio, old),
        "revision": [before["revision"], after["revision"]],
        "scaleSource": [before.get("scaleSource"), after.get("scaleSource")], "readiness_after": after.get("readiness"),
    }  # fmt: skip
    (d / "calibrated" / "cal.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    mode = "DỰNG LẠI" if info["rebuilt"] else "chỉ co giãn"
    return f"đặt {old} → {info['scale_after']} ({why}); {mode}, lệch {info['max_wall_dev_mm']} mm"
