"""Dựng quan sát cho test chồng tầng: ca thật rút từ bộ kiểm nghiên cứu và ca tổng hợp của nhân (`_synth`)."""

import json
from pathlib import Path
from typing import Any, Final

import cv2
import numpy as np

from packages.domain.drawing_set.stacking import Box, Bubble, FloorObservation
from packages.domain.drawing_set.stacking.model import Gray, Mask

DATA: Final = Path(__file__).parent / "data"


def _png(name: str) -> Mask:
    """Đọc PNG xám 8 bit của thư mục dữ liệu."""
    img = cv2.imread(str(DATA / name), cv2.IMREAD_GRAYSCALE)
    assert img is not None, name
    return np.asarray(img, dtype=np.uint8)


def load_view(name: str) -> FloorObservation:
    """Một mặt bằng thật: `<tên>.json` (tỉ lệ, bong bóng, thang, hộp chữ) cùng mặt nạ/ảnh xám PNG đi kèm nếu có.

    Mặt nạ tường/cột ở 0,5 ảnh dựng 300 dpi; ảnh xám (`ink`) đã là 0,5 ảnh dựng (trùng bước co của nhân nghiên cứu).
    """
    meta: dict[str, Any] = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    ink: Gray | None = _png(meta["ink"]) if "ink" in meta else None
    return FloorObservation(
        mm_per_px=meta["mm_per_px"],
        bubbles=[Bubble(b["label"], b["x"], b["y"], b["r"]) for b in meta["bubbles"]],
        stairs=[Box(*s) for s in meta["stairs"]],
        texts=[Box(*t) for t in meta["texts"]],
        wall_mask=_png(meta["wall"]) if "wall" in meta else None,
        col_mask=_png(meta["col"]) if "col" in meta else None,
        mask_scale=meta["mask_scale"],
        ink=ink,
        ink_scale=0.5,
    )


def golden() -> list[dict[str, Any]]:
    """Kết quả của nhân nghiên cứu `stacking2.stack_pair2` trên các cặp thật (chạy gốc, không qua bản chuyển)."""
    rows: list[dict[str, Any]] = json.loads((DATA / "golden.json").read_text(encoding="utf-8"))
    return rows


def synth(shift: tuple[float, float], labels: str = "ABCD") -> tuple[FloorObservation, FloorObservation]:
    """Hai mặt bằng tổng hợp của cùng một toà nhà; B là A dời -shift, nên đáp án đúng là +shift (mm).

    Lưới 4x3 trục có bong bóng, tường dọc theo mọi trục, cột tại giao điểm (thiếu một cột), một ô thang. Tỉ lệ
    10 mm/px, ảnh dựng 2000x1500, mặt nạ ở 0,5.
    """
    mm, w, h = 10.0, 2000, 1500
    pts = [(300 + 300 * i, 200 + 250 * j) for i in range(4) for j in range(3)]
    wall = np.zeros((h // 2, w // 2), np.uint8)
    for x, y in pts:
        wall[y // 2 - 3 : y // 2 + 3, :] = 255
        wall[:, x // 2 - 3 : x // 2 + 3] = 255
    col = np.zeros_like(wall)
    for i, (x, y) in enumerate(pts):
        if i != 5:
            col[y // 2 - 4 : y // 2 + 4, x // 2 - 4 : x // 2 + 4] = 255

    def view(dx: float, dy: float) -> FloorObservation:
        """Mặt bằng dời (dx, dy) px ảnh dựng."""
        d = np.array([[1, 0, dx / 2], [0, 1, dy / 2]], dtype=np.float32)
        moved = [np.asarray(cv2.warpAffine(m, d, (m.shape[1], m.shape[0])), dtype=np.uint8) for m in (wall, col)]
        bub = [Bubble(lab, 300 + 300 * i + dx, 100 + dy, 20) for i, lab in enumerate(labels)]
        bub += [Bubble(str(j + 1), 150 + dx, 200 + 250 * j + dy, 20) for j in range(3)]
        return FloorObservation(
            mm, bub, stairs=[Box(800 + dx, 500 + dy, 900 + dx, 700 + dy)], wall_mask=moved[0], col_mask=moved[1]
        )

    return view(0, 0), view(-shift[0] / mm, -shift[1] / mm)


def conflict_case() -> tuple[FloorObservation, FloorObservation]:
    """Hai quan sát có mặt nạ tường + cột GIỐNG HỆT (không đều) nhưng nhãn lưới lệch nhau 1000 mm ở cả hai trục."""
    a, _ = synth((0, 0))
    assert a.wall_mask is not None
    rng = np.random.default_rng(3)
    wall, col = np.zeros_like(a.wall_mask), np.zeros_like(a.wall_mask)
    for _ in range(25):
        x, y, n = rng.integers(20, 900), rng.integers(20, 700), rng.integers(60, 250)
        if rng.random() < 0.5:
            wall[y : y + 3, x : x + n] = 255
        else:
            wall[y : y + n, x : x + 3] = 255
    for _ in range(7):
        x, y = rng.integers(30, 900), rng.integers(30, 700)
        col[y : y + 8, x : x + 8] = 255
    same = FloorObservation(a.mm_per_px, a.bubbles, a.stairs, wall_mask=wall, col_mask=col)
    moved = [Bubble(b.label, b.x + 100, b.y + 100, b.r) for b in a.bubbles]
    return same, FloorObservation(a.mm_per_px, moved, a.stairs, wall_mask=wall, col_mask=col)
