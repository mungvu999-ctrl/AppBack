"""Đơn vị nhỏ của bộ chấm: hình học, mức Đ/C/T, đọc đáp án, báo cáo, CLI. Dữ liệu tổng hợp."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from tools.bench.score import __main__ as score_cli
from tools.bench.score import io as score_io
from tools.bench.score import report
from tools.bench.score.geometry import band, count_crit, inside, sample, seg_dist, self_cross
from tools.bench.score.openings import _host_length
from tools.bench.score.reliability import _broken
from tools.bench.score.verdict import finalize, verdict_of


def test_band_thresholds() -> None:
    """Đ trong ngưỡng tốt, C trong ngưỡng cảnh báo, T ngoài; None = không đo được."""
    assert [band(v, 1, 2) for v in (0.5, 1, 1.5, 2, 3)] == ["Đ", "Đ", "C", "C", "T"]
    assert band(None, 1, 2) == "—"


def test_count_crit() -> None:
    """Lệch ≤ 1 và ≥ 70 % -> Đ; đáp án không chắc nới thêm 1; không đáp án -> "—"."""
    assert count_crit(3, None, True) == "—"
    assert count_crit(3, 3, True) == "Đ"
    assert count_crit(1, 3, True) == "C"  # lệch 2: quá 1 nhưng chưa quá 2
    assert count_crit(0, 4, True) == "T"
    assert count_crit(4, 3, True) == "Đ"
    assert count_crit(5, 3, True) == "C"
    assert count_crit(2, 3, False) == "C"  # đáp án không chắc nới ngưỡng nhưng thiếu quá 30 %
    assert count_crit(8, 3, True) == "T"


def test_seg_dist_and_sample() -> None:
    """Khoảng cách điểm-đoạn; không có đoạn -> vô cực; lấy mẫu cách ≤ step."""
    segs = np.array([[0.0, 0.0, 10.0, 0.0]])
    d = seg_dist(np.array([[5.0, 3.0], [-4.0, 0.0]]), segs)
    assert d.tolist() == [3.0, 4.0]
    assert np.isinf(seg_dist(np.array([[1.0, 1.0]]), np.zeros((0, 4)))).all()
    assert len(sample(segs, 5)) == 3
    assert sample(np.zeros((0, 4))).shape == (0, 2)


def test_polygon_helpers() -> None:
    """Điểm trong đa giác, đa giác tự cắt hay không."""
    sq = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    assert inside((2, 2), sq)
    assert not inside((5, 2), sq)
    assert not self_cross(sq)
    assert self_cross([(0.0, 0.0), (4.0, 4.0), (4.0, 0.0), (0.0, 4.0)])


def test_verdict_rules() -> None:
    """ĐẠT khi không T và ≤ 2 C."""
    assert verdict_of(["Đ", "C", "C"]) == "ĐẠT"
    assert verdict_of(["C", "C", "C"]) == "TRƯỢT"
    assert verdict_of(["Đ", "T"]) == "TRƯỢT"


def test_finalize_outcomes() -> None:
    """Outcome: tự động / 1 bước / trượt / "?"; chỉ ca có bước 1 mang `auto`."""
    ok, bad, unknown = {"verdict": "ĐẠT"}, {"verdict": "TRƯỢT", "c": {}, "silent": True}, {"verdict": "?"}
    assert finalize(ok, ok)["outcome"] == "tự động"
    assert "auto" not in finalize(ok, ok)
    assert finalize(bad, dict(ok))["outcome"] == "1 bước"
    assert finalize(bad, dict(ok))["auto"] == {"c": {}, "silent": True}
    assert finalize(bad, dict(bad))["outcome"] == "trượt"
    assert finalize(bad, dict(unknown))["outcome"] == "?"


def test_broken_detects_non_finite_numbers() -> None:
    """R2: số không hữu hạn trong viền phòng là hỏng (qua bộ chấm đầy đủ thì sập sớm hơn nên test trực tiếp)."""
    case: Any = SimpleNamespace(
        ops=[], walls=[], rooms=[{"wallIds": [], "outline": [{"x": float("nan"), "y": 0.0}]}], wsegs=np.zeros((0, 4))
    )
    assert _broken(case)
    case.rooms = [{"wallIds": [], "outline": [{"x": 1.0, "y": 0.0}]}]
    assert not _broken(case)


def test_host_length_without_host_wall() -> None:
    """Ô mở không có tường chủ: chiều dài 0."""
    case: Any = SimpleNamespace(wall_by={}, k=1.0)
    assert _host_length(case, {"wallId": "x"}) == 0.0


def test_scaled_helpers_skip_missing_centres() -> None:
    """Đổi pixel bỏ qua phòng/ô mở không có tâm và đáp án tổng thiếu px."""
    key: dict[str, Any] = {
        "mm_per_px": 2.0, "plan_bbox_px": None, "rooms": [{"center_px": None}], "openings": [{"center_px": [2, 4]}],
        "overall": [{"axis": "x", "px0": None, "px1": 4}],
    }  # fmt: skip
    score_io._scaled(key, 2.0)
    assert key["mm_per_px"] == 1.0
    assert key["openings"][0]["center_px"] == [4, 8]
    assert key["overall"][0] == {"axis": "x", "px0": None, "px1": 8}
    base: dict[str, Any] = {"mm_per_px": None, "plan_bbox_px": None, "rooms": [{"center_px": None}], "openings": []}
    score_io._scaled_base(base, 2.0)
    assert base["rooms"] == [{"center_px": None}]


def test_match_offset_edge_cases() -> None:
    """Ảnh trang lớn hơn ảnh gốc -> None; cùng cỡ -> (0, 0); mẩu khớp -> đúng độ lệch; không khớp -> None."""
    rs = np.random.RandomState(3)
    big = rs.randint(0, 255, (120, 140)).astype(np.float32)
    assert score_io.match_offset(big, big[:100, :100]) is None
    assert score_io.match_offset(big, big) == (0, 0)
    assert score_io.match_offset(big[7:107, 5:125], big) == (5, 7)
    other = rs.randint(0, 255, (100, 120)).astype(np.float32)
    assert score_io.match_offset(other, big) is None


def test_group_rows_skip_empty_groups() -> None:
    """Bảng nhóm chỉ in nhóm có ca."""
    res = [{"group": "A", "outcome": "tự động", "silent": False}, {"group": "A", "outcome": "trượt", "silent": True}]
    rows = report._group_rows(res)
    assert len(rows) == 1
    assert rows[0].startswith("| A | 2 | 1 | 0 | 1 | 1 | 1 |")


def test_cli_writes_outputs(synth_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI chấm: ghi score.md/json và phiếu thẩm mỹ (không ghi đè phiếu đã có), in bảng ra stdout."""
    run = tmp_path / "run"
    run.mkdir()
    ids = ["v-base", "v-no-walls"]
    for cid in ids:
        (run / cid).mkdir()
        for f in (synth_root / "run" / cid).iterdir():
            (run / cid / f.name).write_bytes(f.read_bytes())
    offsets = tmp_path / "off.json"
    offsets.write_text(json.dumps({"v-base": [12, 9]}), encoding="utf-8")
    assert score_cli.main([str(run), "--root", str(synth_root), "--offsets", str(offsets)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Chấm run")
    res = json.loads((run / "score.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in res] == ids
    sheet = run / "tham-my.csv"
    assert "v-base" in sheet.read_text(encoding="utf-8-sig")
    sheet.write_text("giữ nguyên", encoding="utf-8")
    assert score_cli.main([str(run), "--root", str(synth_root)]) == 0
    assert sheet.read_text(encoding="utf-8") == "giữ nguyên"


def test_cli_requires_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không có `--root` lẫn BENCH_ROOT: báo lỗi cấu hình rõ ràng."""
    from tools.bench.config import ConfigError

    monkeypatch.delenv("BENCH_ROOT", raising=False)
    with pytest.raises(ConfigError, match="BENCH_ROOT"):
        score_cli.main([str(tmp_path)])
