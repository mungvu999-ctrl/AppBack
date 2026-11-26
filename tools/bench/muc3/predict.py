"""Đệm đọc trang → dự đoán của **đường sản phẩm** cho thước Mức 3 (NHA-00).

`readings.py` dựng đệm một lần (tách khung + OCR từng tờ, chậm). Ở đây chỉ còn phần thuần, chạy lại bao nhiêu lần
cũng ra cùng số: `analysis._to_domain` → `set_analysis.solve_set` → JSON theo `SCHEMA.md`, mỗi bộ một tệp.
Khung dự đoán được gán mã hình đáp án bằng chính luật ghép của bộ chấm (`sheets.match_boxes`, IoU >= 0,5): nhãn
tờ chỉ dùng để **đặt tên** hình, loại và tầng vẫn là của máy.

Kèm `split_clean.json` / `split_exposed.json` cạnh dự đoán: 27 bộ `EXPOSED` đã bị dùng để chỉnh luật tách hình
(K-32a) nên số của chúng không dùng để kết luận; chấm riêng bằng `--split-file`.

CLI: `BENCH_MUC3_ROOT=... python -X utf8 -m tools.bench.muc3.predict --readings <đệm> --out <thư mục dự đoán>`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

from apps.api.drawing_sets.analysis import _to_domain
from packages.domain.drawing_set.proposal import FloorProposal
from packages.domain.drawing_set.set_analysis import PageReading, ViewGuess, solve_set
from packages.ml_contracts.drawing_sets import reading_from_json
from tools.bench.muc3.config import muc3_root
from tools.bench.muc3.io import load_json
from tools.bench.muc3.metrics import KINDS, Json
from tools.bench.muc3.sheets import match_boxes

EXPOSED: Final = frozenset(
    f"DS-{n}"
    for n in (
        *(248, 250, 252, 257, 280, 302, 303, 340, 346, 348),  # 10 tờ `test` dùng để chỉnh luật tách hình
        *(254, 261, 264, 266, 267, 292, 396, 466, 571),  # 9 tờ `dev` cùng lượt chỉnh
        *(334, 391, 400, 439, 452, 454, 576, 836),  # 8 tờ kiểm chéo đã bị nhìn số
    )
)
"""Bộ đã lộ (`changes/K-32a.md`, mục "Nâng độ đúng tách hình"): luật được chỉnh theo hoặc số đã bị nhìn."""
PARTS: Final = ("test", "dev")

type Sheet = tuple[str, PageReading, Json]
"""Một tờ: tên tệp (không đuôi), kết quả đọc, nhãn tờ (`size` khổ gốc + `views` có `id` và `box`)."""


def split_by_exposure(split: Mapping[str, Any]) -> tuple[Json, Json]:
    """Tách `split.json` thành (sạch, đã lộ), giữ nguyên tên phần và thứ tự bộ; phần vắng coi là rỗng."""
    parts = {p: list(split.get(p) or []) for p in PARTS}
    return (
        {p: [s for s in sets if s not in EXPOSED] for p, sets in parts.items()},
        {p: [s for s in sets if s in EXPOSED] for p, sets in parts.items()},
    )


def _kind(kind: str) -> str:
    """Loại trên dây → loại của thước: `site` và bốn loại được chấm giữ nguyên, còn lại là `unknown`."""
    return kind if kind in (*KINDS, "site") else "unknown"


def _box(guess: ViewGuess, size: Sequence[float]) -> list[float]:
    """Khung của đề xuất (tỉ lệ trang) ra điểm ảnh của tờ khổ `size`: ảnh trang đã bị co về 24 Mpx lúc đọc."""
    x, y, w, h = guess.crop
    return [x * size[0], y * size[1], (x + w) * size[0], (y + h) * size[1]]


def _heights(floors: Mapping[int, FloorProposal]) -> list[Json]:
    """Chiều cao giữa hai số tầng liền nhau có đề xuất.

    Hai mặt bằng trùng số tầng: lấy hình **có chiều cao** (đại diện `solve_house` chọn; hình cốt lệch đại diện
    không mang chiều cao), rồi mới tới hình đứng trước.
    shortcut: chiều cao tầng trên cùng bị bỏ vì thước chỉ chấm cặp `(from, to)`; cần thì chấm theo cốt chân mái.
    """
    by_no: dict[int, FloorProposal] = {}
    for _, floor in sorted(floors.items(), key=lambda item: (item[1].height_mm is None, item[0])):
        if floor.storey_no is not None:
            by_no.setdefault(floor.storey_no, floor)
    return [
        {
            "from": low,
            "to": high,
            "height_m": height_mm / 1000,
            "source": by_no[low].height_source,
            "confidence": by_no[low].height_confidence,
        }
        for low, high in pairwise(sorted(by_no))
        if (height_mm := by_no[low].height_mm) is not None
    ]


def _floors(floors: Mapping[int, FloorProposal], ids: Mapping[int, str]) -> list[Json]:
    """Bản ghi tầng của mọi mặt bằng có số tầng; `view_id` là mã hình đáp án khớp khung (`None` khi không khớp)."""
    return [
        {
            "floor_no": floor.storey_no,
            "view_id": ids.get(i),
            "level_m": None if floor.elevation_mm is None else floor.elevation_mm / 1000,
            "level_source": floor.elevation_source,
            "confidence": floor.elevation_confidence,
        }
        for i, floor in sorted(floors.items())
        if floor.storey_no is not None
    ]


def predict_set(sheets: Sequence[Sheet]) -> Json:
    """Dự đoán của một bộ: mọi tờ vào `solve_set` một lần (tầng giải chung cả bộ), rồi đặt tên hình theo nhãn tờ."""
    guesses, floors = solve_set([reading for _, reading, _ in sheets])
    sheet_views: dict[str, list[Json]] = {}
    ids: dict[int, str] = {}
    for page, (stem, _reading, label) in enumerate(sheets):
        mine = [i for i, guess in enumerate(guesses) if guess.page == page]
        size, truth_views = label["size"], label["views"]
        boxes = [
            {"box": _box(guesses[i], size), "kind": _kind(guesses[i].kind), "confidence": guesses[i].kind_confidence}
            for i in mine
        ]
        sheet_views[stem] = boxes
        for t, j in match_boxes(truth_views, boxes):
            ids[mine[j]] = truth_views[t]["id"]
    views = [
        {
            "id": ids[i],
            "kind": _kind(guess.kind),
            "floor_no": guess.storey_no,
            "confidence": guess.kind_confidence,
            "floor_confidence": guess.storey_confidence,
        }
        for i, guess in enumerate(guesses)
        if i in ids
    ]
    return {
        "views": views,
        "floors": _floors(floors, ids),
        "storey_heights": _heights(floors),
        "sheet_views": sheet_views,
    }


def load_sheets(readings_dir: Path, labels_dir: Path) -> dict[str, list[Sheet]]:
    """Các tờ của `MANIFEST.json`, gom theo bộ, theo thứ tự tên tờ.

    Chỉ đọc đệm đã dựng xong (manifest ghi sau cùng) và đủ tờ: bộ thiếu một tờ sẽ giải ra số tầng khác mà không
    ai thấy. Thiếu manifest, thiếu nhãn tờ, đệm hỏng hay sai hợp đồng → `ValueError`; thiếu tệp đệm → `OSError`.
    Tờ không nhãn không được coi là "không dự đoán": gõ nhầm `--sheets` sẽ trông như mô hình tụt điểm.
    """
    manifest = load_json(readings_dir / "MANIFEST.json")
    if manifest is None:
        raise ValueError(f"đệm chưa dựng xong: không đọc được {readings_dir / 'MANIFEST.json'}")
    out: dict[str, list[Sheet]] = {}
    for stem in sorted(manifest):
        label = load_json(labels_dir / f"{stem}.json")
        if label is None:
            raise ValueError(f"tờ {stem} không có nhãn ở {labels_dir}")
        reading = _to_domain(reading_from_json((readings_dir / f"{stem}.json").read_bytes()))
        out.setdefault(stem.split("__")[0], []).append((stem, reading, label))
    return out


def _dump(path: Path, data: object) -> None:
    """Ghi JSON UTF-8, xuống dòng `\\n` trên mọi hệ (hai lượt chạy cho tệp trùng từng byte)."""
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Ghi dự đoán mọi bộ có đệm và hai tệp chia sạch / đã lộ; trả 0, hoặc 2 khi không đọc được `split.json`."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.muc3.predict", description=__doc__)
    ap.add_argument("--readings", type=Path, required=True, help="thư mục đệm đọc trang (`readings.py` dựng)")
    ap.add_argument("--out", type=Path, required=True, help="thư mục dự đoán, mỗi bộ một <bộ>.json")
    ap.add_argument("--sheets", type=Path, help="nhãn tờ; mặc định <BENCH_MUC3_ROOT>/sheets")
    ap.add_argument("--split-file", type=Path, help="mặc định <BENCH_MUC3_ROOT>/split.json")
    a = ap.parse_args(argv)
    root = muc3_root()
    split = load_json(a.split_file or root / "split.json")
    if split is None:
        sys.stderr.write("không đọc được split.json\n")
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    sets = load_sheets(a.readings, a.sheets or root / "sheets")
    for name, sheets in sets.items():
        _dump(a.out / f"{name}.json", predict_set(sheets))
    clean, exposed = split_by_exposure(split)
    _dump(a.out / "split_clean.json", clean)
    _dump(a.out / "split_exposed.json", exposed)
    sys.stdout.write(f"{len(sets)} bộ -> {a.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
