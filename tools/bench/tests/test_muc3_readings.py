"""Dựng đệm đọc trang cho thước Mức 3 (NHA-00): mỗi tờ đọc đúng một lần qua đường sản phẩm, manifest ghi sha256."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from apps.ml.text.reader import RapidOcrReader
from packages.ml_contracts.artifacts import BoxPx, TextPx
from packages.ml_contracts.drawing_sets import reading_from_json
from packages.vision.preprocess import RgbImage, encode_png
from tools.bench.muc3 import readings
from tools.bench.muc3.readings import build, main, sheet_stems, wheel_reader
from tools.bench.tests.test_muc3_score import _write


class _Reader:
    """Bộ đọc giả: một dòng chữ cố định, đếm số lượt gọi."""

    def __init__(self) -> None:
        """Chưa đọc tờ nào."""
        self.calls = 0

    def read(self, image: np.ndarray) -> tuple[TextPx, ...]:
        """Một dòng `A1` ở góc trên trái."""
        self.calls += 1
        return (TextPx(text="A1", box=BoxPx(x_min=1, y_min=1, x_max=20, y_max=10), confidence=0.9),)


def _png() -> bytes:
    """Tờ trắng 600x400 có một khung mực."""
    sheet = np.full((400, 600), 255, np.uint8)
    sheet[50:350, 50:54] = sheet[50:350, 546:550] = sheet[50:54, 50:550] = sheet[346:350, 50:550] = 40
    return encode_png(RgbImage(np.repeat(sheet[..., None], 3, axis=2)))


def _sheets_dir(root: Path, *stems: str) -> Path:
    """Thư mục `sheets/` với các tờ PNG tổng hợp."""
    folder = root / "sheets"
    folder.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (folder / f"{stem}.png").write_bytes(_png())
    return folder


def test_build_reads_each_sheet_once_and_records_hashes(tmp_path: Path) -> None:
    """Lượt hai dùng lại đệm (không gọi bộ đọc); manifest trên đĩa trùng kết quả trả về và khớp sha256 thật."""
    sheets, out, reader = _sheets_dir(tmp_path, "S__s00"), tmp_path / "cache", _Reader()
    first = build(sheets, out, ["S__s00"], reader)
    assert build(sheets, out, ["S__s00"], reader) == first
    assert reader.calls == 1
    doc = reading_from_json((out / "S__s00.json").read_bytes())
    assert (doc.width_px, doc.height_px, doc.read, doc.texts[0].text) == (600, 400, True, "A1")
    assert first["S__s00"] == {
        "png_sha256": hashlib.sha256(_png()).hexdigest(),
        "reading_sha256": hashlib.sha256((out / "S__s00.json").read_bytes()).hexdigest(),
    }
    assert json.loads((out / "MANIFEST.json").read_text(encoding="utf-8")) == first
    assert not list(out.glob("*.part"))


def test_build_without_reader_keeps_boxes_only(tmp_path: Path) -> None:
    """Không có bộ đọc: trang vẫn có kết quả, `read=False`, không chữ."""
    build(_sheets_dir(tmp_path, "S__s00"), tmp_path / "cache", ["S__s00"], None)
    doc = reading_from_json((tmp_path / "cache" / "S__s00.json").read_bytes())
    assert (doc.read, doc.texts) == (False, ())


def test_build_fails_on_missing_sheet(tmp_path: Path) -> None:
    """Tờ vắng phải nổ: đệm thiếu tờ cho số sai mà không ai thấy."""
    with pytest.raises(FileNotFoundError):
        build(_sheets_dir(tmp_path), tmp_path / "cache", ["S__s00"], None)


def test_sheet_stems_skips_photos_and_unselected_sets(tmp_path: Path) -> None:
    """Chỉ tờ `__sNN` của bộ được chọn, theo thứ tự bộ rồi số tờ; bản `_photo` và bộ khác bị bỏ."""
    sheets = _sheets_dir(tmp_path, "B__s01", "B__s00", "A__s00", "A__s00_photo", "C__s00")
    assert sheet_stems(sheets, ["B", "A", "Z"]) == ["B__s00", "B__s01", "A__s00"]


def test_wheel_reader_loads_the_pinned_wheel_model(tmp_path: Path) -> None:
    """Model nhận dạng của wheel khớp checksum bản ghim và nạp được qua `load_onnx` (không tải mạng)."""
    reader = wheel_reader(tmp_path / "models")
    assert isinstance(reader, RapidOcrReader)
    assert (tmp_path / "models" / "rapidocrRec.onnx").is_file()


def test_cli_builds_cache_for_one_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--part test` chỉ đọc tờ của phần test; bộ đọc lấy từ `wheel_reader` trên thư mục `_models` của đệm."""
    _write(tmp_path / "split.json", {"test": ["S"], "dev": ["T"]})
    _sheets_dir(tmp_path, "S__s00", "T__s00")
    asked: list[Path] = []

    def fake_reader(folder: Path) -> _Reader:
        """Ghi lại thư mục model được hỏi, trả bộ đọc giả."""
        asked.append(folder)
        return _Reader()

    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    monkeypatch.setattr(readings, "wheel_reader", fake_reader)
    out = tmp_path / "cache"
    assert main(["--out", str(out), "--part", "test"]) == 0
    assert "1 tờ của 1 bộ" in capsys.readouterr().out
    assert sorted(p.name for p in out.glob("*__s*.json")) == ["S__s00.json"]
    assert asked == [out / "_models"]
    assert main(["--out", str(out)]) == 0
    assert sorted(p.name for p in out.glob("*__s*.json")) == ["S__s00.json", "T__s00.json"]


def test_cli_fails_when_split_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Không đọc được `split.json` thì thoát mã 2 trước khi nạp model."""
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    assert main(["--out", str(tmp_path / "cache")]) == 2
    assert "split.json" in capsys.readouterr().err
