"""`replay/inner.py` (chạy trong container): luồng dựng lại sau-ML với các bước pipeline GIẢ, mỗi nhánh một test."""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from tools.bench.replay import inner

CALLS: dict[str, Any] = {}  # lời gọi quan trọng của bộ giả gần nhất
NAMES = ("wallSegmentation/walls.json", "openingAndFurnitureDetection/objects.json", "dimensionReading/text.json")


class Model:
    """Giả mô hình pydantic: `model_dump` trả dict đã cho."""

    def __init__(self, data: Any) -> None:
        """Giữ dữ liệu."""
        self.data = data

    def model_dump(self, **kw: Any) -> Any:
        """Như pydantic: trả dữ liệu (kiểm các cờ được truyền)."""
        assert kw == {"mode": "json", "by_alias": True, "exclude_none": True}
        return self.data


class Built:
    """Kết quả `build_layer` giả."""

    scale_mm_per_px = Decimal("12.5")
    scale_basis = "ocr"
    label_orientation: ClassVar[dict[str, int]] = {"a": 1}
    scale_source = "dimension"
    dimensions: ClassVar[list[str]] = ["d1"]
    layer = "LAYER"

    def __init__(self, area_notes: bool) -> None:
        """`area_notes`: có thuộc tính `area_notes` (mã mới) hay không (mã cũ)."""
        if area_notes:
            self.area_notes = (3.0,)

    def to_json(self) -> bytes:
        """Tuần tự hoá."""
        return b'{"built": 1}'

    @classmethod
    def from_json(cls, raw: bytes) -> Built:
        """Đọc lại (giữ cờ area_notes của lớp giả)."""
        return cls(hasattr(cls, "with_notes"))


class NewBuilt(Built):
    """Bản có `area_notes`."""

    with_notes = True


def make_steps(**over: Any) -> inner.Steps:
    """`Steps` giả; `over` ghi đè từng hàm. Ghi các lời gọi quan trọng vào `calls`."""
    calls = CALLS
    calls.clear()

    def build_layer(**kw: Any) -> Any:
        calls["build"] = kw
        return NewBuilt(True)

    def make_merge(layer: Any, scale: Any, removed: Any) -> Any:
        calls["merge_args"] = (layer, scale, removed)
        return lambda empty, sc: SimpleNamespace(layer="MERGED")

    def build_report(**kw: Any) -> Any:
        calls["report"] = kw
        return SimpleNamespace(to_json_bytes=lambda: b"{}")

    vals: dict[str, Any] = {
        "walls_from_json": lambda raw: ("walls", raw), "objects_from_json": lambda raw: ("objs", raw),
        "text_from_json": lambda raw: ("text", raw),
        "decode_page_raster": lambda page, mask, width_px, height_px: ("page", width_px),
        "build_layer": build_layer, "built_layer_cls": NewBuilt, "not_a_floor_plan": lambda layer: None,
        "make_merge": make_merge, "with_floor_height": lambda layer, h: (layer, h), "removed_cls": lambda: "removed",
        "spatial_layer_cls": lambda **kw: kw, "check_candidate": lambda layer, level_id: None,
        "with_areas": lambda layer: f"areas({layer})", "build_report": build_report,
        "summary": lambda report, built: {"checks": 2}, "run_facts_cls": lambda **kw: kw,
        "readiness_of": lambda run, scale_source, layer, scale_basis: Model({"state": "ready"}),
        "scale_basis_of": lambda facts, a, b: None, "scale_status": lambda src: "resolved",
        "provisional_scale_source": lambda src: "prov", "layer_out": lambda layer: Model({"walls": []}),
        "dimensions_out": lambda dims: [Model({"d": 1})], "system_clock_cls": lambda: "clock",
        "default_settings": SimpleNamespace(
            default_scale_mm_per_px=Decimal("20"), detect_furniture=True, confidence_threshold=Decimal("0.4")
        ),
        "page_vectors": ("pageVectors", "vectors.json"),
    }  # fmt: skip
    vals.update(over)
    return inner.Steps(**vals)


def make_cache(
    tmp_path: Path,
    *,
    settings: Any = None,
    ppm: Any = 2.0,
    page: bool = False,
    vector: bool = False,
    artifacts: bool = True,
) -> Path:
    """Cache một ca `c1` với ctx.json và (tuỳ chọn) artifact ML, page.png, mặt nạ, vectors."""
    cache = tmp_path / "cache"
    d = cache / "c1"
    ctx = {
        "levelId": "L1", "floorHeightMm": 3000, "settings": settings, "pinned": None,
        "drawing": {"width_px": 40, "height_px": 30, "px_per_paper_mm": ppm}, "run": {"id": "run1"},
    }  # fmt: skip
    d.mkdir(parents=True)
    (d / "ctx.json").write_text(json.dumps(ctx), encoding="utf-8")
    for n in NAMES if artifacts else NAMES[:1]:
        (d / "run" / n).parent.mkdir(parents=True, exist_ok=True)
        (d / "run" / n).write_bytes(b"{}")
    if page:
        (d / "page.png").write_bytes(b"png")
        (d / "run/wallSegmentation/walls.png").write_bytes(b"mask")
    if vector:
        (d / "run/pageVectors").mkdir()
        (d / "run/pageVectors/vectors.json").write_bytes(b"{}")
    return cache


def run_replay(tmp_path: Path, steps: inner.Steps, **cache_kw: Any) -> tuple[dict[str, Any], Path]:
    """Chạy `replay("c1")` trên cache vừa dựng; trả (kết quả, thư mục ra)."""
    cache, out = make_cache(tmp_path, **cache_kw), tmp_path / "out"
    (out / "c1").mkdir(parents=True)
    return inner.replay("c1", steps, cache, out), out


def test_replay_completed_writes_api_shaped_layer(tmp_path: Path) -> None:
    """Luồng đủ: ghi built-layer/run-quality/layer.json đúng dạng API, bỏ trường None, mặc định cài đặt."""
    steps = make_steps()
    res, out = run_replay(tmp_path, steps, page=True, vector=True)
    assert res["status"] == "completed"
    assert res["summary"] == {"checks": 2}
    assert (out / "c1/built-layer.json").read_bytes() == b'{"built": 1}'
    assert (out / "c1/run-quality.json").read_bytes() == b"{}"
    body = json.loads((out / "c1/layer.json").read_text(encoding="utf-8"))["body"]
    assert body["level"]["scaleMillimetresPerPixel"] == 12.5
    assert body["level"]["heightMm"] == 3000
    assert body["readiness"] == {"state": "ready"}
    assert (body["scaleStatus"], body["scaleSource"]) == ("resolved", "prov")
    assert "scaleBasis" not in body  # trường None bị bỏ
    assert body["dimensions"] == [{"d": 1}]
    calls = CALLS
    assert calls["build"]["page"] == ("page", 40)
    assert calls["build"]["vector_page"] is True
    assert calls["build"]["fallback_mm_per_px"] == Decimal("20")
    assert calls["build"]["furniture"] is True
    assert calls["report"]["threshold"] == Decimal("0.4")
    assert calls["report"]["area_notes"] == (3.0,)
    assert calls["merge_args"][0] == ("LAYER", 3000)


def test_replay_old_worktree_without_optional_hooks(tmp_path: Path) -> None:
    """Worktree cũ: không có `not_a_floor_plan` và `area_notes` -> vẫn chạy; cài đặt dự án lấy từ ctx; không vector."""
    settings = {"defaultScaleMmPerPx": "10", "detectFurniture": False, "confidenceThreshold": "0.7"}
    steps = make_steps(not_a_floor_plan=None, built_layer_cls=Built, build_layer=lambda **kw: Built(False))
    res, _ = run_replay(tmp_path, steps, settings=settings, ppm=None)
    assert res["status"] == "completed"


def test_replay_vector_page_depends_on_file(tmp_path: Path) -> None:
    """Có px_per_paper_mm mà thiếu tệp vectors -> vector_page False; px_per_paper_mm None -> True."""
    steps = make_steps()
    run_replay(tmp_path / "a", steps)
    assert CALLS["build"]["vector_page"] is False
    steps = make_steps()
    run_replay(tmp_path / "b", steps, ppm=None)
    assert CALLS["build"]["vector_page"] is True


def test_replay_page_decode_failure_means_no_raster(tmp_path: Path) -> None:
    """Giải raster trang lỗi ValueError -> dựng không dùng raster; thiếu mặt nạ thì cũng không."""

    def bad(page: bytes, mask: bytes, width_px: int, height_px: int) -> Any:
        raise ValueError("hỏng")

    steps = make_steps(decode_page_raster=bad)
    run_replay(tmp_path / "a", steps, page=True)
    assert CALLS["build"]["page"] is None
    steps = make_steps()
    run_replay(tmp_path / "b", steps)
    assert CALLS["build"]["page"] is None


def test_replay_missing_inputs_is_not_replayed(tmp_path: Path) -> None:
    """Thiếu artifact ML, hoặc ctx không có bản vẽ/lượt: `replayed` False kèm lý do."""
    res, _ = run_replay(tmp_path / "a", make_steps(), artifacts=False)
    assert res["replayed"] is False
    assert "thiếu artifact" in res["why"]
    cache = make_cache(tmp_path / "b")
    ctx = json.loads((cache / "c1/ctx.json").read_text())
    ctx["run"] = None
    (cache / "c1/ctx.json").write_text(json.dumps(ctx))
    assert inner.replay("c1", make_steps(), cache, tmp_path / "out")["replayed"] is False


def test_replay_build_invalid_and_perspective(tmp_path: Path) -> None:
    """build_layer ném ValueError -> PIPELINE_BUILD_INVALID; ảnh phối cảnh -> NOT_A_FLOOR_PLAN."""

    def boom(**kw: Any) -> Any:
        raise ValueError("x" * 300)

    res, _ = run_replay(tmp_path / "a", make_steps(build_layer=boom))
    assert res["error"] == "PIPELINE_BUILD_INVALID"
    assert len(res["detail"]) == 200
    res, _ = run_replay(tmp_path / "b", make_steps(not_a_floor_plan=lambda layer: 0.9))
    assert (res["status"], res["error"]) == ("failed", "NOT_A_FLOOR_PLAN")


def test_replay_integrity_failures_use_error_code(tmp_path: Path) -> None:
    """`_check_candidate` ném lỗi có `.code.code` -> mã đó; lỗi thường -> tên lớp."""

    class AppError(Exception):
        def __init__(self) -> None:
            self.code = SimpleNamespace(code="LAYER_INTEGRITY_BROKEN")

    def raise_app(layer: Any, level_id: str) -> None:
        raise AppError

    def raise_plain(layer: Any, level_id: str) -> None:
        raise KeyError("k")

    res, _ = run_replay(tmp_path / "a", make_steps(check_candidate=raise_app))
    assert res["error"] == "LAYER_INTEGRITY_BROKEN"
    res, _ = run_replay(tmp_path / "b", make_steps(check_candidate=raise_plain))
    assert res["error"] == "KeyError"


def test_run_cases_records_each_case_and_survives_crash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`run_cases`: ghi replay.json từng ca (kèm giờ), in một dòng; ca sập -> REPLAY_CRASH, lượt vẫn chạy tiếp."""
    cache = make_cache(tmp_path)
    broken = cache / "c2"
    broken.mkdir()
    (broken / "ctx.json").write_text("không phải json", encoding="utf-8")
    out = tmp_path / "out"
    inner.run_cases(["c1", "c2"], make_steps(), cache, out)
    ok = json.loads((out / "c1/replay.json").read_text(encoding="utf-8"))
    crash = json.loads((out / "c2/replay.json").read_text(encoding="utf-8"))
    assert ok["status"] == "completed"
    assert "replay_s" in ok
    assert crash["error"] == "REPLAY_CRASH"
    assert "JSONDecodeError" in crash["detail"]
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("c1 completed")
    assert lines[1].startswith("c2 failed REPLAY_CRASH")


def test_optional_attr_lookup() -> None:
    """`_attr` trả None khi mô-đun hoặc tên không có (worktree cũ), ngược lại trả đối tượng."""
    assert inner._attr("json", "dumps") is json.dumps
    assert inner._attr("không_có_mô_đun_này", "x") is None
    assert inner._attr("json", "không_có") is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="mã apps/ dùng cú pháp Python 3.12")
def test_load_steps_binds_real_pipeline() -> None:
    """Trong ảnh worker, `load_steps` nạp đủ mọi hàm thật (chạy được khi `apps`/`packages` nhập được)."""
    pytest.importorskip("apps.worker.pipeline_build.build")
    steps = inner.load_steps()
    for f in fields(steps):
        assert getattr(steps, f.name) is not None or f.name == "not_a_floor_plan"
    assert steps.page_vectors[1].endswith(".json")
