"""replay.py, check.py, dump.py: lệnh docker, hoàn thiện thư mục ra, so với lượt thật, tải cache (docker/mạng giả)."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.bench.replay import check, dump, replay


def wjson(p: Path, data: Any) -> None:
    """Ghi JSON, tạo thư mục cha."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- replay


def test_cases_of_needs_ctx_in_cache(tmp_path: Path) -> None:
    """Chỉ ca có meta.json ở lượt gốc VÀ ctx.json trong cache."""
    orig, cache = tmp_path / "orig", tmp_path / "cache"
    for cid in ("b", "a", "c"):
        wjson(orig / cid / "meta.json", {})
    wjson(cache / "a" / "ctx.json", {})
    wjson(cache / "b" / "ctx.json", {})
    assert replay.cases_of(orig, cache) == ["a", "b"]


def test_docker_command_is_offline_and_readonly() -> None:
    """Không mạng, worktree và cache chỉ đọc, out ghi được, inner.py mount vào /inner.py."""
    cmd = replay.docker_command(Path("/w"), Path("/c"), Path("/o"), "img:1", ["x", "y"])
    assert cmd[:6] == ["docker", "run", "--rm", "--network", "none", "-v"]
    assert "/w:/tmp/w:ro" in cmd[6]
    assert "/c:/cache:ro" in cmd
    assert "/o:/out" in cmd
    assert cmd[-5:] == ["img:1", "python", "/inner.py", "x", "y"]
    assert any(a.endswith("inner.py:/inner.py:ro") for a in cmd)


def test_run_container_starts_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_container` gộp stderr vào stdout và tắt chuyển đổi đường dẫn của MSYS."""
    seen: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kw: Any) -> str:
        seen.update(cmd=cmd, **kw)
        return "proc"

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    started: object = replay.run_container(["docker", "x"])
    assert started == "proc"
    assert seen["env"]["MSYS_NO_PATHCONV"] == "1"
    assert seen["stderr"] == subprocess.STDOUT


def finish_setup(tmp_path: Path, status: str | None, **files: bool) -> tuple[Path, Path, Path]:
    """Dựng lượt gốc/cache/ra cho một ca `c`; `status` None = replay không chạy được (replayed False)."""
    orig, cache, out = tmp_path / "orig" / "c", tmp_path / "cache", tmp_path / "out"
    wjson(orig / "meta.json", {"id": "c", "status": "completed"})
    wjson(orig / "layer.json", {"orig": 1})
    wjson(orig / "progress.json", {"status": "failed"})
    for n in ("spatial.json", "quality.json"):
        wjson(orig / n, {n: 1})
    if files.get("orig_page"):
        (orig / "page.png").write_bytes(b"orig")
    if files.get("cache_page"):
        (cache / "c").mkdir(parents=True)
        (cache / "c" / "page.png").write_bytes(b"cache")
    res: dict[str, Any] = {"replayed": status is not None, "replay_s": 1.5}
    if status:
        res.update(status=status)
        if status == "failed":
            res["error"] = "NOT_A_FLOOR_PLAN"
    wjson(out / "c" / "replay.json", res)
    if files.get("layer"):
        wjson(out / "c" / "layer.json", {"new": 1})
    return orig, cache, out


def test_finish_completed_uses_cache_page(tmp_path: Path) -> None:
    """Replay xong: chép spatial/quality gốc, page.png ưu tiên cache, progress `completed`, meta ghi dấu replay."""
    orig, cache, out = finish_setup(tmp_path, "completed", cache_page=True, orig_page=True, layer=True)
    replay.finish(out, "c", orig, cache)
    d = out / "c"
    assert (d / "page.png").read_bytes() == b"cache"
    assert json.loads((d / "progress.json").read_text())["step"] == "qualityCheck"
    meta = json.loads((d / "meta.json").read_text())
    assert meta["status"] == "completed"
    assert meta["replay"] == {"replayed": True, "status": "completed", "error": None, "replay_s": 1.5}
    assert json.loads((d / "layer.json").read_text()) == {"new": 1}
    assert (d / "spatial.json").exists()
    assert (d / "quality.json").exists()


def test_finish_failed_replay_writes_empty_layer(tmp_path: Path) -> None:
    """Replay hỏng mà chưa có layer.json: ghi layer rỗng `notEvaluated`; progress mang mã lỗi; page từ lượt gốc."""
    orig, cache, out = finish_setup(tmp_path, "failed", orig_page=True)
    replay.finish(out, "c", orig, cache)
    d = out / "c"
    prog = json.loads((d / "progress.json").read_text())
    assert prog == {"status": "failed", "step": "spatialDataBuild", "progressPercent": 100, "error": "NOT_A_FLOOR_PLAN"}
    assert json.loads((d / "layer.json").read_text())["body"]["readiness"]["state"] == "notEvaluated"
    assert (d / "page.png").read_bytes() == b"orig"


def test_finish_failed_replay_keeps_existing_layer(tmp_path: Path) -> None:
    """Replay hỏng nhưng đã có layer.json: giữ nguyên, không ghi layer rỗng; không có page nào thì không có page.png."""
    orig, cache, out = finish_setup(tmp_path, "failed", layer=True)
    replay.finish(out, "c", orig, cache)
    assert json.loads((out / "c" / "layer.json").read_text()) == {"new": 1}
    assert not (out / "c" / "page.png").exists()


def test_finish_not_replayed_copies_original(tmp_path: Path) -> None:
    """Lượt gốc hỏng ở ML (không replay): giữ layer/progress gốc."""
    orig, cache, out = finish_setup(tmp_path, None)
    replay.finish(out, "c", orig, cache)
    d = out / "c"
    assert json.loads((d / "layer.json").read_text()) == {"orig": 1}
    assert json.loads((d / "progress.json").read_text()) == {"status": "failed"}
    assert json.loads((d / "meta.json").read_text())["replay"]["replayed"] is False


class FakeProc:
    """Tiến trình docker giả: phát vài dòng và ghi `replay.json` cho các ca vào thư mục ra."""

    def __init__(self, cmd: list[str], skip: set[str]) -> None:
        """Lấy thư mục ra và ca từ lệnh (đối số sau /inner.py và nguồn mount /out)."""
        out = next(a for a in cmd if a.endswith(":/out")).rsplit(":/out", 1)[0]
        cases = cmd[cmd.index("/inner.py") + 1 :]
        for c in cases:
            if c not in skip:
                wjson(Path(out) / c / "replay.json", {"replayed": False})
        self.stdout = [f"{c} done\n" for c in cases]
        self.waited = False

    def wait(self) -> int:
        """Đợi xong."""
        self.waited = True
        return 0


def test_main_splits_cases_over_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main`: chia ca cho --jobs container, đợi xong, `finish` ca có replay.json và cảnh báo ca thiếu."""
    root = tmp_path / "root"
    for cid in ("a1", "a2", "a3"):
        wjson(root / "runs/r4b" / cid / "meta.json", {"status": "completed"})
        wjson(root / "ml-cache" / cid / "ctx.json", {})
    wjson(root / "tap-f/runs/f2/f1/meta.json", {"status": "completed"})
    wjson(root / "ml-cache/f1/ctx.json", {})
    monkeypatch.setenv("BENCH_ROOT", str(root))
    monkeypatch.delenv("BENCH_RUN_BENCH", raising=False)
    monkeypatch.delenv("BENCH_RUN_F", raising=False)
    monkeypatch.delenv("BENCH_ROOT_F", raising=False)
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str]) -> FakeProc:
        cmds.append(cmd)
        return FakeProc(cmd, skip={"a3"})

    monkeypatch.setattr(replay, "run_container", fake_run)
    wt = tmp_path / "wt"
    wt.mkdir()
    assert replay.main([str(wt), str(tmp_path / "ra"), "--jobs", "2"]) == 0
    captured = capsys.readouterr()
    assert len(cmds) == 3  # tập bench chia 2 container, tập F một
    assert "a1 done" in captured.out
    assert "4 ca," in captured.out
    assert "a3: không có replay.json" in captured.err
    assert (tmp_path / "ra" / "bench" / "a1" / "meta.json").exists()
    # một tập, đường ra thẳng, lọc theo ca
    cmds.clear()
    assert replay.main([str(wt), str(tmp_path / "rb"), "--set", "bench", "--cases", "a2"]) == 0
    assert len(cmds) == 1
    assert (tmp_path / "rb" / "a2" / "meta.json").exists()


# ---------------------------------------------------------------- check


def test_signatures() -> None:
    """Chữ ký bước dựng/API/quality: đếm đúng, None -> None, thiếu trường -> mặc định."""
    assert check.built_sig(None) is None
    built = {"layer": {"walls": [1, 2], "furniture": [1]}, "scaleMmPerPx": 5, "scaleBasis": "b", "scaleSource": "s"}
    assert check.built_sig(built) == {
        "walls": 2, "openings": 0, "rooms": 0, "furniture": 1, "scale": 5, "basis": "b", "source": "s",
    }  # fmt: skip
    ops = [{"kind": "door"}, {"kind": "window"}, {"kind": "door"}]
    body = {
        "body": {
            "layer": {"walls": [1], "openings": ops}, "level": {"scaleMillimetresPerPixel": 4},
            "readiness": {"state": "ready"}, "scaleStatus": "ok",
        }
    }  # fmt: skip
    sig = check.api_sig(body)
    assert (sig["walls"], sig["doors"], sig["windows"], sig["scale"], sig["state"]) == (1, 2, 1, 4, "ready")
    assert check.api_sig(None)["walls"] == 0
    assert check.q_sig(None) is None
    assert check.q_sig({"hasCritical": True, "x": 1}) == {
        "hasCritical": True, "checks": None, "criticalChecks": None, "scaleBasis": None, "scaleChecks": None,
        "lowConfidence": None,
    }  # fmt: skip
    assert check.js(Path("/không/có")) is None


def test_score_diff_cases() -> None:
    """So kết luận/tiêu chí giữa lượt thật và replay; chỉ một bên có kết quả cũng là lệch."""
    a = {"verdict_auto": "ĐẠT", "c": {"S1": "Đ", "W1": "Đ"}}
    b = {"verdict_auto": "TRƯỢT", "c": {"S1": "Đ", "W1": "T", "P1": "C"}}
    assert check._score_diff(a, a) == []
    diffs = check._score_diff(a, b)
    assert diffs[0] == "score ĐẠT→TRƯỢT"
    assert "P1:None→C" in diffs[1]
    assert "W1:Đ→T" in diffs[1]
    assert check._score_diff(a, None) == ["score có/không True/False"]
    assert check._score_diff(None, None) == []


def test_quality_diff_handles_missing_replay_summary(tmp_path: Path) -> None:
    """Tóm tắt replay thiếu (replay không có `summary`) vẫn so được, không sập."""
    cache, rep = tmp_path / "cache", tmp_path / "rep"
    wjson(cache / "c" / "ctx.json", {"qualitySummary": {"checks": 3}})
    wjson(rep / "replay.json", {})
    out = check._quality_diff(cache, "c", rep, True)
    assert out
    assert "checks: 3→None" in out[0]
    assert check._quality_diff(cache, "c", rep, False) == []


def make_replay_tree(tmp_path: Path, synth_root: Path, same: bool) -> tuple[Path, Path, Path]:
    """Lượt gốc (3 ca tổng hợp), replay giống/khác lượt gốc, cache có ctx."""
    import shutil

    orig, rep, cache = tmp_path / "orig", tmp_path / "ra" / "bench", tmp_path / "cache"
    for cid in ("v-base", "v-no-walls", "v-failed"):
        shutil.copytree(synth_root / "run" / cid, orig / cid)
        shutil.copytree(synth_root / "run" / cid, rep / cid)
        (rep / cid / "probe.json").unlink(missing_ok=True)  # replay không có cảnh 3D
        wjson(rep / cid / "replay.json", {"replayed": True, "summary": {"checks": 1}})
        wjson(rep / cid / "built-layer.json", {"layer": {"walls": [1]}})
        wjson(cache / cid / "ctx.json", {"qualitySummary": {"checks": 1}})
        wjson(cache / cid / "run/spatialDataBuild/layer.json", {"layer": {"walls": [1]}})
    if not same:
        wjson(
            rep / "v-base" / "layer.json",
            {"body": {"layer": {"walls": []}, "level": {"scaleMillimetresPerPixel": 1.0}}},
        )
        wjson(rep / "v-no-walls" / "built-layer.json", {"layer": {"walls": [1, 2]}})
        wjson(rep / "v-failed" / "replay.json", {"replayed": False})
    return orig, rep, cache


def test_compare_set_identical_and_different(tmp_path: Path, synth_root: Path) -> None:
    """Replay y hệt lượt thật: không lệch; sửa layer/built thì từng lệch được ghi; ca không replay được đánh dấu."""
    orig, rep, cache = make_replay_tree(tmp_path / "same", synth_root, same=True)
    rows = check.compare_set(rep, tmp_path / "same" / "ref", orig, synth_root, cache)
    assert [(c, rp, d) for c, rp, d in rows] == [(c, True, []) for c in ("v-base", "v-failed", "v-no-walls")]
    orig, rep, cache = make_replay_tree(tmp_path / "diff", synth_root, same=False)
    rows = check.compare_set(rep, tmp_path / "diff" / "ref", orig, synth_root, cache)
    by = {c: (rp, d) for c, rp, d in rows}
    assert any(x.startswith("API") for x in by["v-base"][1])
    assert any(x.startswith("score") for x in by["v-base"][1])
    assert any(x.startswith("dựng") for x in by["v-no-walls"][1])
    assert by["v-failed"][0] is False


def test_check_main_reports(
    tmp_path: Path, synth_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main`: in số ca khớp, liệt kê ca lệch/không replay, ghi check.json; bỏ qua tập không có thư mục ra."""
    orig, _rep, cache = make_replay_tree(tmp_path, synth_root, same=False)
    monkeypatch.setenv("BENCH_ROOT", str(synth_root))
    monkeypatch.setenv("BENCH_RUN_BENCH", str(orig))
    monkeypatch.setenv("BENCH_ROOT_F", str(synth_root))
    monkeypatch.setattr("tools.bench.config.ml_cache", lambda root: cache)
    assert check.main([str(tmp_path / "ra")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("KHỚP 1/3 ca")
    assert "- bench/v-failed (không replay: chép lượt gốc)" in out
    data = json.loads((tmp_path / "ra" / "check.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in data} == {"v-base", "v-no-walls", "v-failed"}


# ---------------------------------------------------------------- dump


def test_pending_cases(tmp_path: Path) -> None:
    """Ca cần tải: có floorId; bỏ ca đã có ctx.json trừ khi --force."""
    root, cache = tmp_path / "root", tmp_path / "cache"
    for cid, floor in (("a", "f1"), ("b", None), ("c", "f3")):
        wjson(root / "runs/x" / cid / "meta.json", {"projectId": "p", "floorId": floor, "uploadId": "u"})
    wjson(cache / "c" / "ctx.json", {})
    assert dump.pending_cases(root, ["runs/x"], cache, False) == [["a", "p", "f1", "u"]]
    assert [c[0] for c in dump.pending_cases(root, ["runs/x"], cache, True)] == ["a", "c"]


def test_store_and_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Ghi từng tệp JSON-lines base64 vào cache; bản ghi lỗi chỉ cảnh báo; `fetch` gửi danh sách ca qua stdin."""
    cache = tmp_path / "cache"
    rec = {"case": "a", "name": "run/x.json", "data": base64.b64encode(b"hi").decode()}
    assert dump._store(cache, rec) is True
    assert (cache / "a/run/x.json").read_bytes() == b"hi"
    assert dump._store(cache, {"case": "b", "err": "nofloor"}) is False
    assert "nofloor" in capsys.readouterr().err

    class Proc:
        returncode = 0

        def __init__(self, *a: Any, **k: Any) -> None:
            self.args = a
            self.sent: list[str] = []
            self.stdin = self
            self.stdout = [json.dumps(rec) + "\n", json.dumps({"case": "z", "err": "x"}) + "\n"]

        def write(self, s: str) -> None:
            self.sent.append(s)

        def close(self) -> None:
            self.closed = True

        def wait(self) -> int:
            return 0

    made: list[Proc] = []

    def popen(*a: Any, **k: Any) -> Proc:
        made.append(Proc(*a, **k))
        return made[0]

    monkeypatch.setattr(subprocess, "Popen", popen)
    n, rc = dump.fetch([["a", "p", "f", "u"]], tmp_path / "c2")
    assert (n, rc) == (1, 0)
    assert json.loads(made[0].sent[0]) == [["a", "p", "f", "u"]]
    assert made[0].args[0][:5] == ["docker", "exec", "-i", "f14-api-1", "python"]


def test_dump_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`main`: không ca nào cần tải -> 0 và không chạm docker; có ca -> gọi fetch và trả mã docker."""
    root = tmp_path / "root"
    wjson(root / "runs/r4b/a/meta.json", {"projectId": "p", "floorId": "f", "uploadId": "u"})
    monkeypatch.setenv("BENCH_ROOT", str(root))
    monkeypatch.setattr(dump, "fetch", lambda cases, cache: (len(cases), 3))
    assert dump.main(["--runs", "runs/r4b"]) == 3
    err = capsys.readouterr().err
    assert "1 ca cần tải" in err
    assert "ghi 1 tệp, mã thoát 3" in err
    wjson(root / "ml-cache/a/ctx.json", {})
    monkeypatch.setattr(dump, "fetch", lambda *a: pytest.fail("không được gọi"))
    assert dump.main(["--runs", "runs/r4b"]) == 0


def test_dump_inner_script_is_valid_python() -> None:
    """Script chạy trong f14-api-1 (dạng chuỗi) ít nhất phải là Python hợp lệ."""
    compile(dump.INNER, "<dump-inner>", "exec")
    assert sys.version_info >= (3, 11)
