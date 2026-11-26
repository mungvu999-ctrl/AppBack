"""Dựng đệm đọc trang cho thước Mức 3 bằng **đúng đường sản phẩm** (NHA-00).

Mỗi tờ `<gốc>/sheets/<bộ>__sNN.png` đi qua `jobs._render_page` (co về 24 Mpx) → `drawing_set_read._read_one`
(tách khung + OCR) — đúng chuỗi `changes/K-32a.md` đã đo — rồi ghi `PageReadingDoc` ra `<out>/<tờ>.json`.
OCR mất 4-110 s/tờ nên chỉ chạy một lần; `predict.py` chấm lại trên đệm bao nhiêu lần cũng được. `MANIFEST.json`
ghi sha256 của ảnh nguồn và của kết quả đọc: số đo chỉ so được giữa hai lượt khi hai manifest trùng nhau.

Bộ đọc là `RapidOcrReader` trên model nhận dạng ghim của wheel, nạp qua `load_onnx` như lúc chạy thật. Phải
chạy trong ảnh có `onnxruntime` + `rapidocr-onnxruntime` (ảnh verify/ml), không chạy bằng Python của máy.

CLI: `BENCH_MUC3_ROOT=... python -X utf8 -m tools.bench.muc3.readings --out <thư mục đệm> [--part test|dev]`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from apps.api.drawing_sets.jobs import _render_page
from apps.ml.drawing_set_read.tasks import _read_one
from apps.ml.runtime.loader import load_onnx
from apps.ml.text.reader import RapidOcrReader
from apps.ml.text.tasks import STEP
from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.ml_contracts.drawing_sets import PageRef, reading_to_json
from packages.ml_contracts.payloads import ModelRef
from packages.ml_contracts.pinned import PINNED
from packages.ml_contracts.ports import TextReader
from packages.storage.local import LocalDiskStorage
from tools.bench.muc3.config import muc3_root
from tools.bench.muc3.io import load_json
from tools.bench.muc3.predict import PARTS

PINNED_REC: Final = "rapidocrRec"
WHEEL_REC_FILE: Final = "ch_PP-OCRv4_rec_infer.onnx"
"""Model nhận dạng nằm sẵn trong wheel đã khoá ở `uv.lock` (không tải mạng); không dấu tiếng Việt (NO-564)."""


def wheel_reader(models_dir: Path) -> TextReader:
    """Bộ đọc của bước `dimensionReading` trên model ghim của wheel, chép vào `models_dir` rồi nạp qua `load_onnx`.

    Checksum lệch bản ghim → `load_onnx` ném `PermanentError` (wheel đã đổi model: số đo không còn so được).
    """
    import rapidocr_onnxruntime  # type: ignore[import-untyped]  # nạp trễ: kéo onnxruntime, chỉ lệnh dựng đệm cần

    clock = SystemClock()
    models_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        Path(str(rapidocr_onnxruntime.__file__)).parent / "models" / WHEEL_REC_FILE, models_dir / f"{PINNED_REC}.onnx"
    )
    ref = ModelRef(
        version_id=new_id("mdl", clock),
        family=STEP,
        weights_key=None,
        pinned_name=PINNED_REC,
        checksum_sha256=str(PINNED[PINNED_REC].onnx_sha256),
    )
    storage = LocalDiskStorage(models_dir, clock, "https://x.test")
    session = asyncio.run(load_onnx(storage, ref, models_dir=models_dir))
    return RapidOcrReader.from_session(session, pinned_name=PINNED_REC)


def sheet_stems(sheets_dir: Path, sets: Iterable[str]) -> list[str]:
    """Tên các tờ ghép `<bộ>__sNN` của những bộ được chọn (bỏ bản `_photo`), theo thứ tự bộ rồi số tờ."""
    return [p.stem for s in sets for p in sorted(sheets_dir.glob(f"{s}__s[0-9][0-9].png"))]


def _sha(data: bytes) -> str:
    """SHA-256 dạng hex."""
    return hashlib.sha256(data).hexdigest()


def build(
    sheets_dir: Path, out_dir: Path, stems: Sequence[str], reader: TextReader | None
) -> dict[str, dict[str, str]]:
    """Đọc từng tờ chưa có đệm, ghi `MANIFEST.json`, trả manifest `{tờ: {png_sha256, reading_sha256}}`.

    Tờ đã có tệp đệm thì không đọc lại (lượt dựng bị ngắt chạy tiếp được); muốn đọc lại thì xoá tệp đó. Ảnh
    hỏng hay quá trần ném `VisionError`: đệm thiếu tờ sẽ cho số sai mà không ai thấy, nên không bỏ qua.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    page = PageRef(file_id=new_id("dwf", SystemClock()), page_index=0)
    manifest: dict[str, dict[str, str]] = {}
    for stem in stems:
        png, target = (sheets_dir / f"{stem}.png").read_bytes(), out_dir / f"{stem}.json"
        if not target.exists():
            # Ghi qua tệp tạm rồi đổi tên: lượt bị giết giữa chừng không để lại đệm cụt mà lượt sau tin là xong.
            part = target.with_suffix(".part")
            part.write_bytes(reading_to_json(_read_one(_render_page("png", png, 0), reader, page)))
            part.replace(target)
        manifest[stem] = {"png_sha256": _sha(png), "reading_sha256": _sha(target.read_bytes())}
    text = json.dumps(manifest, indent=1, sort_keys=True)
    (out_dir / "MANIFEST.json").write_text(text, encoding="utf-8", newline="\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    """Dựng đệm cho các bộ của `split.json`; trả 0, hoặc 2 khi không đọc được `split.json`."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.muc3.readings", description=__doc__)
    ap.add_argument("--out", type=Path, required=True, help="thư mục đệm (ngoài repo)")
    ap.add_argument("--part", choices=PARTS, help="một phần; mặc định test và dev")
    ap.add_argument("--split-file", type=Path, help="mặc định <BENCH_MUC3_ROOT>/split.json")
    a = ap.parse_args(argv)
    root = muc3_root()
    split = load_json(a.split_file or root / "split.json")
    if split is None:
        sys.stderr.write("không đọc được split.json\n")
        return 2
    sets = [s for p in ([a.part] if a.part else PARTS) for s in split.get(p) or []]
    stems = sheet_stems(root / "sheets", sets)
    manifest = build(root / "sheets", a.out, stems, wheel_reader(a.out / "_models"))
    sys.stdout.write(f"{len(manifest)} tờ của {len(sets)} bộ -> {a.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
