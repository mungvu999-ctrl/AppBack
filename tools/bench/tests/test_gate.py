"""gate.py / gate_ci.py: so baseline, ngưỡng tụt, công tắc dữ liệu, CLI; quét tĩnh `bench.yml`. Dữ liệu tổng hợp."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.bench import gate, gate_ci
from tools.bench.replay import replay

REPO = Path(__file__).resolve().parents[3]
BENCH_YML = REPO / ".github" / "workflows" / "bench.yml"


def write_score(run: Path, cases: dict[str, tuple[str, bool]]) -> Path:
    """Ghi `<run>/score.json` tối giản: {id: (verdict, silent)}."""
    run.mkdir(parents=True, exist_ok=True)
    rows = [{"id": k, "verdict": v, "silent": s, "c": {}, "info": {}} for k, (v, s) in cases.items()]
    (run / "score.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return run


BASE = {"a": ("ĐẠT", False), "b": ("ĐẠT", False), "c": ("ĐẠT", False), "d": ("TRƯỢT", True), "e": ("TRƯỢT", False)}


def baseline_of(tmp_path: Path, cases: dict[str, tuple[str, bool]] = BASE, **source: Any) -> dict[str, Any]:
    """Baseline một tập `bench` từ `cases`."""
    return gate.snapshot({"bench": write_score(tmp_path / "base", cases)}, {"pipeline_sha": "abc", **source})


# ---------------------------------------------------------------- so sánh


def test_compare_set_lists_every_change(tmp_path: Path) -> None:
    """Mất/thêm ĐẠT, im lặng mới/hết, ca thiếu; ca lạ ở lượt mới không tính."""
    new = {"a": ("TRƯỢT", True), "b": ("ĐẠT", False), "d": ("ĐẠT", False), "e": ("TRƯỢT", False), "z": ("ĐẠT", False)}
    base = baseline_of(tmp_path)["sets"]["bench"]
    d = gate.compare_set("bench", base, gate.load_scores(write_score(tmp_path / "n", new)))
    assert (d.base_pass, d.new_pass, d.base_silent, d.new_silent) == (3, 2, 1, 1)
    assert d.lost == ["a", "c"]
    assert d.gained == ["d"]
    assert d.new_silent_ids == ["a"]
    assert d.fixed_silent_ids == ["d"]
    assert d.missing == ["c"]


@pytest.mark.parametrize(
    ("new", "code"),
    [
        (BASE, 0),  # bằng → xanh
        ({**BASE, "a": ("TRƯỢT", False), "b": ("TRƯỢT", False)}, 0),  # tụt 2 = ngưỡng → xanh
        ({**BASE, "a": ("TRƯỢT", False), "b": ("TRƯỢT", False), "c": ("TRƯỢT", False)}, 1),  # tụt 3 → đỏ
        ({**BASE, "e": ("TRƯỢT", True)}, 1),  # im lặng +1 → đỏ
        ({**BASE, "d": ("TRƯỢT", False), "e": ("TRƯỢT", True)}, 0),  # đổi chỗ, tổng không tăng → xanh
    ],
)
def test_compare_thresholds(tmp_path: Path, new: dict[str, tuple[str, bool]], code: int) -> None:
    """Q8: đỏ khi tụt > 2 ca ĐẠT hoặc lỗi im lặng tăng ≥ 1 (mỗi tập)."""
    got, lines = gate.compare(baseline_of(tmp_path), {"bench": write_score(tmp_path / "n", new)}, (2, 0), None)
    assert got == code
    assert lines[-1].startswith("Kết luận: **ĐỎ**" if code else "Kết luận: **XANH**")


def test_compare_rejects_mismatched_baseline(tmp_path: Path) -> None:
    """Sai format hay khác tập → mã 2, không so."""
    base = baseline_of(tmp_path)
    run = write_score(tmp_path / "n", BASE)
    assert gate.compare(base, {"F": run}, (2, 0), None)[0] == 2
    assert gate.compare({**base, "format": 99}, {"bench": run}, (2, 0), None)[0] == 2


def test_compare_checks_ml_cache_digest(tmp_path: Path) -> None:
    """ml-cache khác bản lúc chụp → mã 2; cùng bản → so bình thường; baseline không ghim → bỏ kiểm."""
    cache = tmp_path / "cache"
    (cache / "x").mkdir(parents=True)
    (cache / "x" / "ctx.json").write_text("{}", encoding="utf-8")
    base = baseline_of(tmp_path, ml_cache_sha256=gate.cache_digest(cache))
    run = write_score(tmp_path / "n", BASE)
    assert gate.compare(base, {"bench": run}, (2, 0), cache)[0] == 0
    (cache / "x" / "ctx.json").write_text('{"đổi": 1}', encoding="utf-8")
    code, lines = gate.compare(base, {"bench": run}, (2, 0), cache)
    assert code == 2
    assert "ml-cache" in lines[0]
    assert gate.compare(baseline_of(tmp_path), {"bench": run}, (2, 0), cache)[0] == 0


def test_parse_sets() -> None:
    """`tên=thư-mục`; thiếu `=` hay phần rỗng → ValueError."""
    assert gate.parse_sets(["bench=a/b", "F=c"]) == {"bench": Path("a/b"), "F": Path("c")}
    for bad in ("bench", "=a", "bench="):
        with pytest.raises(ValueError, match="tên=thư-mục"):
            gate.parse_sets([bad])


# ---------------------------------------------------------------- CLI


def test_cli_snapshot_then_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """snapshot ghi baseline LF có SHA + digest; compare ra 0/1 và thêm báo cáo vào `--report`."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a.json").write_text("1", encoding="utf-8")
    run = write_score(tmp_path / "run", BASE)
    out = tmp_path / "bl" / "main.json"
    argv = ["snapshot", str(out), "--set", f"bench={run}", "--source-sha", "f00", "--ml-cache", str(cache)]
    assert gate.main(argv) == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    data = json.loads(raw)
    assert data["source"]["pipeline_sha"] == "f00"
    assert data["source"]["ml_cache_sha256"] == gate.cache_digest(cache)
    report = tmp_path / "summary.md"
    assert gate.main(["compare", str(out), "--set", f"bench={run}", "--report", str(report)]) == 0
    worse = write_score(tmp_path / "worse", {**BASE, "e": ("TRƯỢT", True)})
    assert gate.main(["compare", str(out), "--set", f"bench={worse}", "--max-silent-rise", "1"]) == 0
    assert gate.main(["compare", str(out), "--set", f"bench={worse}"]) == 1
    assert "XANH" in report.read_text(encoding="utf-8")
    assert "| bench | 3 | 3 | 1 | 2 |" in capsys.readouterr().out


def test_cli_errors_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Baseline không có, `--set` sai dạng → mã 2 kèm lý do ra stderr."""
    assert gate.main(["compare", str(tmp_path / "none.json"), "--set", f"bench={tmp_path}"]) == 2
    assert gate.main(["compare", str(tmp_path / "none.json"), "--set", "bench"]) == 2
    assert "gate:" in capsys.readouterr().err


# ---------------------------------------------------------------- ci


def ci_args(tmp_path: Path, baseline: Path, local: bool = True) -> argparse.Namespace:
    """Tham số `gate ci` như CLI dựng."""
    return argparse.Namespace(
        worktree=tmp_path / "wt", out=tmp_path / "out", baseline=baseline, jobs=2, local=local, max_pass_drop=2,
        max_silent_rise=0,
    )  # fmt: skip


def data_root(tmp_path: Path) -> Path:
    """Gốc dữ liệu đủ cho `missing_data`: ml-cache, hai lượt gốc, MANIFEST + answers của hai gốc chấm."""
    root = tmp_path / "data"
    for d in ("ml-cache", "runs/r4b", "tap-f/runs/f2", "answers", "tap-f/answers"):
        (root / d).mkdir(parents=True)
    for m in ("MANIFEST.csv", "tap-f/MANIFEST.csv"):
        (root / m).write_text("id\n", encoding="utf-8")
    return root


def test_gate_mode() -> None:
    """Mặc định auto; không phân biệt hoa thường; giá trị lạ → ValueError."""
    assert gate_ci.gate_mode({}) == "auto"
    assert gate_ci.gate_mode({"BENCH_GATE": " Required "}) == "required"
    with pytest.raises(ValueError, match="BENCH_GATE"):
        gate_ci.gate_mode({"BENCH_GATE": "maybe"})


def test_ci_switch_off_and_missing_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """off → 0; auto + thiếu dữ liệu → 0 với ::notice:: trên GitHub; required + thiếu → 2."""
    a = ci_args(tmp_path, tmp_path / "bl.json")
    assert gate_ci.run(a, {"BENCH_GATE": "off"}) == 0
    assert gate_ci.run(a, {"GITHUB_ACTIONS": "true"}) == 0
    assert "::notice title=bench-replay::bỏ qua" in capsys.readouterr().out
    env = {"BENCH_GATE": "required", "BENCH_ROOT": str(tmp_path / "không-có")}
    assert gate_ci.run(a, env) == 2
    assert "ml-cache" in capsys.readouterr().err
    assert gate_ci.missing_data({"BENCH_ROOT": str(data_root(tmp_path))}) == []


def test_gate_main_ci_bad_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`gate ci` qua CLI với công tắc lạ → mã 2."""
    monkeypatch.setenv("BENCH_GATE", "lạ")
    assert gate.main(["ci", str(tmp_path), str(tmp_path / "o")]) == 2


def fake_scoring(monkeypatch: pytest.MonkeyPatch, verdicts: dict[str, tuple[str, bool]]) -> list[Any]:
    """Thay replay + chấm thật: replay ghi lại tham số và tạo thư mục ra, chấm trả `verdicts` cho mọi tập."""
    calls: list[Any] = []
    rows = [{"id": k, "group": "A", "verdict": v, "silent": s} for k, (v, s) in verdicts.items()]

    def fake_replay(argv: list[str]) -> int:
        calls.append(argv)
        for name in ("bench", "F"):
            (Path(argv[1]) / name).mkdir(parents=True, exist_ok=True)
        return 0

    def fake_score(bench: Any) -> list[dict[str, Any]]:
        calls.append(bench.root)
        return rows

    monkeypatch.setattr(replay, "main", fake_replay)
    monkeypatch.setattr(gate_ci, "score_run", fake_score)
    monkeypatch.setattr(gate_ci, "render_markdown", lambda name, res: [f"# {name}"])
    return calls


@pytest.mark.parametrize(("new", "code"), [(BASE, 0), ({**BASE, "e": ("TRƯỢT", True)}, 1)])
def test_ci_full_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new: dict[str, tuple[str, bool]], code: int
) -> None:
    """Đủ dữ liệu: replay (--local) → chấm hai tập (gốc tập F riêng) → so baseline → báo cáo vào step summary."""
    root = data_root(tmp_path)
    for k in ("BENCH_RUN_BENCH", "BENCH_RUN_F", "BENCH_ROOT_F"):
        monkeypatch.delenv(k, raising=False)
    sets = {"bench": write_score(tmp_path / "b1", BASE), "F": write_score(tmp_path / "b2", BASE)}
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps(gate.snapshot(sets, {"pipeline_sha": "x"})), encoding="utf-8")
    calls = fake_scoring(monkeypatch, new)
    summary = tmp_path / "summary.md"
    env = {"BENCH_ROOT": str(root), "GITHUB_STEP_SUMMARY": str(summary)}
    assert gate_ci.run(ci_args(tmp_path, bl), env) == code
    assert calls[0][-1] == "--local"
    assert calls[1:] == [root, root / "tap-f"]
    assert (tmp_path / "out" / "F" / "score.json").exists()
    assert "Cổng chống tụt" in summary.read_text(encoding="utf-8")
    calls.clear()
    assert gate_ci.run(ci_args(tmp_path, bl, local=False), {"BENCH_ROOT": str(root)}) == code
    assert "--local" not in calls[0]


# ---------------------------------------------------------------- bench.yml


def test_bench_workflow_is_safe_and_switchable() -> None:
    """`bench.yml`: action ghim SHA, quyền chỉ đọc, không pull_request_target, checkout không giữ token, có hạn giờ,
    `run:` không nội suy `${{ }}`, công tắc `BENCH_GATE` từ biến repo, lọc đường gồm `tools/bench`."""
    doc = yaml.safe_load(BENCH_YML.read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    assert "pull_request_target" not in on
    assert "tools/bench/**" in on["pull_request"]["paths"]
    assert doc["permissions"] == {"contents": "read"}
    (job,) = doc["jobs"].values()
    assert job["timeout-minutes"] <= 20
    assert "continue-on-error" not in job
    assert "vars.BENCH_GATE" in job["env"]["BENCH_GATE"]
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
            if step["uses"].startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
        else:
            assert "${{" not in step["run"]
    assert "tools.bench.gate ci" in job["steps"][-1]["run"]
