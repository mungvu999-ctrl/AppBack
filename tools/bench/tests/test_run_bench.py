"""`run_bench` và `calibrate` với API giả: chạy một ca, tiếp tục lượt dở, bước "Đặt tỷ lệ". Không mạng, không docker."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from tools.bench import calibrate, run_bench
from tools.bench.api import BenchError
from tools.bench.score.io import Bench

LAYER = {
    "revision": 3, "level": {"id": "L1", "scaleMillimetresPerPixel": 10.0},
    "readiness": {"state": "ready", "reasons": ["scaleUnresolved"]},
    "layer": {
        "walls": [{"id": "w1", "centreline": {"start": {"x": 0, "y": 0}, "end": {"x": 100, "y": 50}}}],
        "openings": [{"id": "o1"}], "rooms": [{"id": "r1"}],
    },
}  # fmt: skip


class FakeApi:
    """API giả theo (phương thức, hậu tố đường dẫn); ghi lại mọi lời gọi. `fail` ép một hậu tố trả mã khác."""

    def __init__(self, progress: list[dict[str, Any]] | None = None, fail: dict[str, int] | None = None) -> None:
        """`progress`: các lần trả lời hỏi tiến độ (lần cuối lặp lại)."""
        self.calls: list[tuple[str, str]] = []
        self.progress = progress or [{"status": "completed"}]
        self.fail = fail or {}
        self.layer: dict[str, Any] = json.loads(json.dumps(LAYER))
        self.floors: Any = {"items": [{"id": "f1"}]}
        self.bodies: list[Any] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Trả lời theo đường dẫn."""
        self.calls.append((method, path))
        self.bodies.append(body)
        for suffix, code in self.fail.items():
            if path.endswith(suffix):
                return code, {"error": suffix}
        return self._route(method, path)

    def _route(self, method: str, path: str) -> tuple[int, Any]:
        """Bảng đường đi của stack giả."""
        if method == "POST" and path == "/projects":
            return 201, {"id": "p1"}
        if path.endswith("/floors"):
            return 200, self.floors
        if method == "POST" and path.endswith("/drawings/uploads"):
            return 201, {"id": "u1"}
        if method == "POST":
            return 200, {}
        if method == "PUT":
            self.layer["revision"] += 1
            return 200, {}
        if path.endswith("/progress"):
            return 200, self.progress.pop(0) if len(self.progress) > 1 else self.progress[0]
        if path.endswith("/spatial/layer"):
            return 200, self.layer
        if path.endswith("/spatial"):
            return 200, {"drawings": [{"url": "http://img/p.png"}]}
        return 200, {"floors": []}


class Clock:
    """Đồng hồ giả: mỗi lần hỏi tăng `step` giây."""

    def __init__(self, step: float = 1.0) -> None:
        """`step` giây mỗi lần gọi."""
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        """Giờ hiện tại giả."""
        self.t += self.step
        return self.t


def make_root(tmp_path: Path) -> Path:
    """Gốc dữ liệu nhỏ: MANIFEST với 3 ca (A, D, E), ảnh PNG/PDF, đáp án."""
    root = tmp_path / "root"
    (root / "plans").mkdir(parents=True)
    (root / "answers").mkdir()
    rows = [("a-1", "a-1.png", "A"), ("d-1", "d-1.jpg", "D"), ("e-1", "e-1.pdf", "E")]
    with (root / "MANIFEST.csv").open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "file", "group", "source", "expect", "note"])
        for cid, file, g in rows:
            wr.writerow([cid, file, g, "tổng hợp", "", ""])
            (root / "answers" / f"{cid}.json").write_text(json.dumps({"mm_per_px": 5.0}), encoding="utf-8")
    Image.new("L", (30, 20), 7).save(root / "plans" / "a-1.png")
    Image.new("L", (30, 20), 7).save(root / "plans" / "d-1.jpg")
    (root / "plans" / "e-1.pdf").write_bytes(b"%PDF-1.4 " + b"x" * 11)
    return root


def make_ctx(root: Path, fetch: Any = None, step: float = 1.0) -> run_bench.RunContext:
    """Ngữ cảnh lượt với giờ/ngủ/tải giả."""
    out = root / "runs" / "r"
    out.mkdir(parents=True, exist_ok=True)
    ctx = run_bench.RunContext("r", root, out, sleep=lambda s: None, now=Clock(step))
    ctx.fetch = fetch or (lambda url: b"PNG")
    return ctx


def test_run_case_writes_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Một ca: tạo dự án, tải bản vẽ theo khúc, chờ xong, lưu layer/spatial/quality/page.png, meta.json ghi sau cùng."""
    root = make_root(tmp_path)
    monkeypatch.setattr(run_bench, "CHUNK", 8)
    api = FakeApi(progress=[{"status": "processing"}, {"status": "completed", "p": 1}])
    ctx = make_ctx(root)
    row = Bench(root, ctx.out).manifest["e-1"]
    meta = run_bench.run_case(api, ctx, row)
    d = ctx.out / "e-1"
    assert meta["status"] == "completed"
    assert meta["projectId"] == "p1"
    assert meta["floorId"] == "f1"
    assert meta["uploadId"] == "u1"
    assert sorted(p.name for p in d.iterdir()) == [
        "layer.json",
        "meta.json",
        "page.png",
        "progress.json",
        "quality.json",
        "spatial.json",
    ]
    assert (d / "page.png").read_bytes() == b"PNG"
    chunks = [b for (m, p), b in zip(api.calls, api.bodies, strict=True) if p.endswith("/chunks")]
    assert [c["chunkIndex"] for c in chunks] == [0, 1, 2]  # 20 byte, khúc 8 byte
    assert json.loads((d / "layer.json").read_text(encoding="utf-8"))["status"] == 200


def test_run_case_list_floors_timeout_and_page_failure(tmp_path: Path) -> None:
    """Danh sách tầng dạng list; quá hạn chờ -> status timeout; tải ảnh trang hỏng chỉ cảnh báo."""
    root = make_root(tmp_path)
    api = FakeApi(progress=[{"status": "processing"}])
    api.floors = [{"id": "f1"}]

    def bad_fetch(url: str) -> bytes:
        raise OSError("mạng đứt")

    ctx = make_ctx(root, bad_fetch, step=400.0)
    meta = run_bench.run_case(api, ctx, Bench(root, ctx.out).manifest["a-1"])
    assert meta["status"] == "timeout"
    assert not (ctx.out / "a-1" / "page.png").exists()


def test_run_case_failures_raise(tmp_path: Path) -> None:
    """Mỗi bước API trả mã lạ thì ném BenchError (ca đó không có meta.json -> lần sau chạy lại)."""
    root = make_root(tmp_path)
    for suffix in ("/projects", "/drawings/uploads", "/chunks", "/complete", "/quality"):
        ctx = make_ctx(root)
        row = Bench(root, ctx.out).manifest["a-1"]
        with pytest.raises(BenchError):
            run_bench.run_case(FakeApi(fail={suffix: 500}), ctx, row)
        assert not (ctx.out / "a-1" / "meta.json").exists()


def test_download_checks_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chỉ tải http(s); URL lạ bị từ chối, URL hợp lệ đọc qua urlopen."""
    import io
    import urllib.request

    with pytest.raises(BenchError):
        run_bench._download("file:///x")
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(b"abc"))
    assert run_bench._download("http://x/p.png") == b"abc"


def test_select_cases_filters(tmp_path: Path) -> None:
    """Lọc theo nhóm và danh sách `--only`."""
    bench = Bench(make_root(tmp_path), tmp_path)
    assert [r["id"] for r in run_bench.select_cases(bench, "A,D", "")] == ["a-1", "d-1"]
    assert [r["id"] for r in run_bench.select_cases(bench, "A,D,E", "e-1,d-1")] == ["d-1", "e-1"]


def test_wait_for_stack_loops_until_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chờ khi RAM thiếu hoặc hàng đợi bận, tiếp tục khi đủ RAM và rảnh."""
    ram = iter([1.0, 5.0, 5.0])
    busy = iter([0, 3, 0, 0])
    monkeypatch.setattr(run_bench, "free_gb", lambda: next(ram))
    monkeypatch.setattr(run_bench, "queues_busy", lambda: next(busy))
    slept: list[float] = []
    run_bench._wait_for_stack(3.0, slept.append)
    assert slept == [10, 10]


def patch_stack(monkeypatch: pytest.MonkeyPatch, api: FakeApi, snaps: list[dict[str, Any]]) -> None:
    """Thay các phần chạm máy thật của `main`: Session, RAM, hàng đợi, chụp stack, config."""
    monkeypatch.setattr(run_bench, "Session", lambda *a, **k: api)
    monkeypatch.setattr(run_bench, "free_gb", lambda: 99.0)
    monkeypatch.setattr(run_bench, "queues_busy", lambda: 0)
    monkeypatch.setattr(run_bench, "snapshot", lambda: snaps.pop(0) if len(snaps) > 1 else snaps[0])
    monkeypatch.setattr(run_bench, "_download", lambda url: b"PNG")


def test_main_runs_resumes_and_calibrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main`: chạy ca, một ca hỏng không dừng lượt, chạy lại bỏ qua ca xong; cảnh báo khi stack đổi."""
    root = make_root(tmp_path)
    secrets = tmp_path / "s.env"
    secrets.write_text("E2E_ADMIN_EMAIL=a@b\nE2E_ADMIN_PASSWORD=pw \nrác\n", encoding="utf-8")
    monkeypatch.setenv("BENCH_ROOT", str(root))
    monkeypatch.setenv("BENCH_SECRETS", str(secrets))
    api = FakeApi()
    patch_stack(monkeypatch, api, [{"containers": {"a": 1}}, {"containers": {"a": 2}}])
    assert run_bench.main(["r", "--group", "A,D,E", "--calibrate"], sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "3 ca, còn 3" in out
    assert "[1/3] a-1: completed" in out
    assert "đặt tỉ lệ:" in out
    assert "CẢNH BÁO: stack đổi" in out
    assert (root / "runs/r/stack-end.json").exists()
    assert (root / "runs/r/stack.json").exists()
    # lỗi một ca (đuôi tệp lạ) không dừng cả lượt
    (root / "plans" / "d-1.jpg").rename(root / "plans" / "d-1.xyz")
    (root / "runs/r/d-1/meta.json").unlink()
    manifest = (root / "MANIFEST.csv").read_text(encoding="utf-8-sig").replace("d-1.jpg", "d-1.xyz")
    (root / "MANIFEST.csv").write_text(manifest, encoding="utf-8-sig")
    patch_stack(monkeypatch, FakeApi(), [{"containers": {}}])
    assert run_bench.main(["r", "--group", "D"], sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "1 ca, còn 1" in out
    assert "d-1: LỖI" in out
    assert "xong" in out
    # chạy lại: không còn ca nào
    patch_stack(monkeypatch, FakeApi(), [{"containers": {}}])
    run_bench.main(["r", "--group", "A,E"], sleep=lambda s: None)
    assert "2 ca, còn 0" in capsys.readouterr().out


def test_main_calibrate_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--calibrate-only`: chỉ ca đã có meta.json và chưa có calibrated/layer.json."""
    root = make_root(tmp_path)
    secrets = tmp_path / "s.env"
    secrets.write_text("E2E_ADMIN_EMAIL=a\nE2E_ADMIN_PASSWORD=b\n", encoding="utf-8")
    monkeypatch.setenv("BENCH_ROOT", str(root))
    monkeypatch.setenv("BENCH_SECRETS", str(secrets))
    for cid in ("a-1", "d-1"):
        d = root / "runs/r" / cid
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"projectId": "p", "floorId": "f"}), encoding="utf-8")
        (d / "layer.json").write_text(json.dumps({"body": LAYER}), encoding="utf-8")
    (root / "runs/r/d-1/calibrated").mkdir()
    (root / "runs/r/d-1/calibrated/layer.json").write_text("{}", encoding="utf-8")
    patch_stack(monkeypatch, FakeApi(), [{}])
    assert run_bench.main(["r", "--calibrate-only"], sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "a-1: đặt 10.0" in out
    assert "d-1" not in out
    assert (root / "runs/r/a-1/calibrated/cal.json").exists()


# ---------------------------------------------------------------- calibrate


def answer(root: Path, cid: str, data: dict[str, Any]) -> None:
    """Ghi đáp án `cid`."""
    (root / "answers" / f"{cid}.json").write_text(json.dumps(data), encoding="utf-8")


def add_case(root: Path, cid: str, file: str, group: str = "A") -> None:
    """Thêm một dòng vào MANIFEST của gốc thử."""
    with (root / "MANIFEST.csv").open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([cid, file, group, "tổng hợp", "", ""])


def test_key_scale_branches(tmp_path: Path) -> None:
    """Mọi nhánh quy đổi tỉ lệ đáp án: nghi sai, nhóm B, dẫn xuất (bản nhỏ / không giữ tỉ lệ), PDF raster, thường."""
    root = make_root(tmp_path)
    run = tmp_path / "run"
    (run / "x").mkdir(parents=True)
    answer(root, "sus", {"mm_per_px": 5.0, "mm_per_px_suspect": True})
    answer(root, "geo", {"viewBox": [0, 0, 1, 1], "px": 0.25})
    answer(root, "small", {"derived_from": "a-1"})
    answer(root, "photo", {"derived_from": "a-1"})
    answer(root, "nobase", {"derived_from": "nomm"})
    answer(root, "nomm", {})
    answer(root, "pdfr", {"mm_per_px": 5.0, "pdf_raster_px": True, "image_px": [60, 40]})
    answer(root, "plain", {"mm_per_px": 5.0})
    Image.new("L", (15, 10), 1).save(root / "plans" / "small.png")
    files = {"sus": "s.png", "geo": "g.png", "small": "small.png", "photo": "photo-1.jpg", "nobase": "nb.png",
             "nomm": "n.png", "pdfr": "p.pdf", "plain": "pl.png"}  # fmt: skip
    for cid, file in files.items():
        add_case(root, cid, file)
    bench = Bench(root, run)

    def ks(cid: str) -> tuple[float | None, str]:
        return calibrate.key_scale(bench, cid, run / "x")

    assert ks("sus") == (None, "đáp án nghi sai tỉ lệ")
    assert ks("geo") == (4.0, "nhóm B")
    assert ks("small") == (10.0, "từ a-1")
    assert ks("photo")[0] is None
    assert ks("nobase")[0] is None
    assert ks("nomm") == (None, "đáp án không có mm_per_px")
    assert ks("pdfr") == (None, "PDF thiếu page.png")
    Image.new("L", (30, 20), 1).save(run / "x" / "page.png")
    assert ks("pdfr") == (10.0, "PDF quy về page.png")
    assert ks("plain") == (5.0, "đáp án")


def test_calibrate_outcomes(tmp_path: Path) -> None:
    """Kết quả một bước đặt tỉ lệ: bỏ qua (không scaleUnresolved / không có tỉ lệ / GET lỗi), lỗi PUT."""
    root = make_root(tmp_path)
    out = tmp_path / "out"
    d = out / "a-1"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"projectId": "p", "floorId": "f"}), encoding="utf-8")
    bench = Bench(root, out)
    row = bench.manifest["a-1"]
    plain = json.loads(json.dumps(LAYER))
    plain["readiness"] = {"reasons": []}
    (d / "layer.json").write_text(json.dumps({"body": plain}), encoding="utf-8")
    assert calibrate.calibrate(FakeApi(), bench, row, out) == "bỏ: không có scaleUnresolved"
    (d / "layer.json").write_text(json.dumps({"body": None}), encoding="utf-8")
    assert calibrate.calibrate(FakeApi(), bench, row, out) == "bỏ: không có scaleUnresolved"
    (d / "layer.json").write_text(json.dumps({"body": LAYER}), encoding="utf-8")
    answer(root, "a-1", {})
    assert calibrate.calibrate(FakeApi(), bench, row, out).startswith("bỏ: đáp án không có")
    answer(root, "a-1", {"mm_per_px": 5.0})
    assert "GET layer 404" in calibrate.calibrate(FakeApi(fail={"/spatial/layer": 404}), bench, row, out)

    class PutFails(FakeApi):
        def _route(self, method: str, path: str) -> tuple[int, Any]:
            return (409, {"x": 1}) if method == "PUT" else super()._route(method, path)

    assert calibrate.calibrate(PutFails(), bench, row, out).startswith("LỖI PUT 409")


def test_calibrate_scale_only_vs_rebuilt(tmp_path: Path) -> None:
    """Sau khi đặt tỉ lệ: toạ độ = cũ x k và cùng id -> "chỉ co giãn"; id đổi hoặc toạ độ lệch -> "DỰNG LẠI"."""
    root = make_root(tmp_path)
    out = tmp_path / "out"
    d = out / "a-1"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"projectId": "p", "floorId": "f"}), encoding="utf-8")
    (d / "layer.json").write_text(json.dumps({"body": LAYER}), encoding="utf-8")
    answer(root, "a-1", {"mm_per_px": 5.0})
    bench = Bench(root, out)
    row = bench.manifest["a-1"]

    class Scaled(FakeApi):
        rebuilt = False

        def _route(self, method: str, path: str) -> tuple[int, Any]:
            s, body = super()._route(method, path)
            if method == "GET" and path.endswith("/spatial/layer") and self.layer["revision"] > 3:
                body = json.loads(json.dumps(self.layer))
                body["level"]["scaleMillimetresPerPixel"] = 5.0
                for w in body["layer"]["walls"]:  # co giãn k = 0,5
                    for e in ("start", "end"):
                        w["centreline"][e] = {
                            a: v * (0.5 + (1 if self.rebuilt else 0)) for a, v in w["centreline"][e].items()
                        }
                if self.rebuilt:
                    body["layer"]["rooms"].append({"id": "r2"})
            return s, body

    msg = calibrate.calibrate(Scaled(), bench, row, out)
    assert "chỉ co giãn" in msg
    assert "lệch 0.0 mm" in msg
    info = json.loads((d / "calibrated" / "cal.json").read_text(encoding="utf-8"))
    assert info["k"] == 0.5
    assert info["same_ids"]
    assert not info["rebuilt"]
    rebuilt = Scaled()
    rebuilt.rebuilt = True
    assert "DỰNG LẠI" in calibrate.calibrate(rebuilt, bench, row, out)
    # lớp cũ chưa có tỉ lệ (old None): không tính độ lệch, coi như dựng lại
    no_old = json.loads(json.dumps(LAYER))
    no_old["level"].pop("scaleMillimetresPerPixel")
    assert calibrate._rebuild_info(no_old, no_old, 5.0, None)["max_wall_dev_mm"] is None
    assert calibrate._max_wall_deviation(LAYER, LAYER, None) is None
