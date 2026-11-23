"""Chạy TRONG container (mã worktree ở /tmp/w): dựng + trộn + quality + readiness cho mỗi ca của /cache -> /out.

python /inner.py <ca> [<ca> ...]    (PYTHONPATH=/tmp/w; /cache chỉ đọc, /out ghi được)

Chạy ngoài container (`replay --local`, CI): `BENCH_REPLAY_CACHE`/`BENCH_REPLAY_OUT` thay /cache, /out.

Đường gọi y như stack, bỏ DB/kho: build_layer (tasks._build_and_write) -> BuiltLayer to_json/from_json
(persist.read_built) -> with_floor_height + make_merge trên tài liệu tầng rỗng (persist._write_ai_layer, tầng mới)
-> _check_candidate + _with_areas (writer._next_layer) -> build_report + _summary (quality.run_quality)
-> readiness_of/scale_basis_of (N16).

Tệp này chỉ dùng thư viện chuẩn + `apps`/`packages` của worktree được mount (KHÔNG nhập `tools.*`: worktree cũ chưa có).
Mọi hàm của pipeline nằm trong `Steps` để test thay bằng giả; `load_steps()` mới nhập mã thật. Hàm mà worktree cũ có thể
chưa có (`not_a_floor_plan`, khoá `area_notes`) được dò tại chỗ để thước chạy được trên cả nhánh cũ.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

Json = dict[str, Any]
CACHE, OUT = Path("/cache"), Path("/out")
_NAMES = ("wallSegmentation/walls.json", "openingAndFurnitureDetection/objects.json", "dimensionReading/text.json")
_WHY_MISSING = "thiếu artifact ML/bản vẽ (lượt gốc hỏng trước bước dựng)"


@dataclass(frozen=True)
class Steps:
    """Các hàm/lớp của pipeline mà replay gọi; `load_steps()` nạp bản thật, test truyền bản giả."""

    walls_from_json: Any
    objects_from_json: Any
    text_from_json: Any
    decode_page_raster: Any
    build_layer: Any
    built_layer_cls: Any
    not_a_floor_plan: Any
    make_merge: Any
    with_floor_height: Any
    removed_cls: Any
    spatial_layer_cls: Any
    check_candidate: Any
    with_areas: Any
    build_report: Any
    summary: Any
    run_facts_cls: Any
    readiness_of: Any
    scale_basis_of: Any
    scale_status: Any
    provisional_scale_source: Any
    layer_out: Any
    dimensions_out: Any
    system_clock_cls: Any
    default_settings: Any
    page_vectors: tuple[str, str]


def _attr(module: str, name: str) -> Any:
    """`module.name`, hoặc None khi worktree cũ chưa có (ImportError/AttributeError)."""
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        return None


def load_steps() -> Steps:
    """Nhập mã thật của worktree (PYTHONPATH=/tmp/w). Chỉ chạy được trong ảnh worker."""
    m = importlib.import_module
    art = m("packages.ml_contracts.artifacts")
    pay = m("packages.ml_contracts.payloads")
    return Steps(
        walls_from_json=art.walls_from_json,
        objects_from_json=art.objects_from_json,
        text_from_json=art.text_from_json,
        decode_page_raster=m("apps.worker.pipeline_build.residual").decode_page_raster,
        build_layer=m("apps.worker.pipeline_build.build").build_layer,
        built_layer_cls=m("apps.worker.pipeline_build.build").BuiltLayer,
        not_a_floor_plan=_attr("apps.worker.pipeline_quality.checks", "not_a_floor_plan"),
        make_merge=m("apps.worker.pipeline_persist.merge").make_merge,
        with_floor_height=m("apps.worker.pipeline_persist.merge").with_floor_height,
        removed_cls=m("packages.domain.rules_ai.merge").Removed,
        spatial_layer_cls=m("packages.domain.spatial").SpatialLayer,
        check_candidate=m("apps.api.spatial_write.writer")._check_candidate,
        with_areas=m("apps.api.spatial_write.writer")._with_areas,
        build_report=m("apps.worker.pipeline_quality.report").build_report,
        summary=m("apps.worker.pipeline_quality.service")._summary,
        run_facts_cls=m("apps.api.spatial_read.readiness").RunFacts,
        readiness_of=m("apps.api.spatial_read.readiness").readiness_of,
        scale_basis_of=m("apps.api.spatial_read.readiness").scale_basis_of,
        scale_status=m("apps.api.spatial_read.assemble").scale_status,
        provisional_scale_source=m("apps.api.spatial_read.assemble").provisional_scale_source,
        layer_out=m("apps.api.spatial_read.wire").layer_out,
        dimensions_out=m("apps.api.spatial_read.wire").dimensions_out,
        system_clock_cls=m("packages.core.clock").SystemClock,
        default_settings=m("apps.api.project_settings.read").DEFAULT_SETTINGS,
        page_vectors=(pay.PAGE_VECTORS_STEP, pay.PAGE_VECTORS_NAME),
    )


def dump(m: Any) -> Json:
    """Mô hình pydantic -> dict JSON theo bí danh dây, bỏ trường None (đúng dạng API trả)."""
    data: Json = m.model_dump(mode="json", by_alias=True, exclude_none=True)
    return data


def _settings(ctx: Json, steps: Steps) -> Json:
    """Cài đặt dự án; chưa có dòng thì dùng DEFAULT_SETTINGS (như `read_settings`)."""
    st: Json | None = ctx["settings"]
    if st is not None:
        return st
    d = steps.default_settings
    return {
        "defaultScaleMmPerPx": str(d.default_scale_mm_per_px),
        "detectFurniture": d.detect_furniture,
        "confidenceThreshold": str(d.confidence_threshold),
    }


def _page_raster(steps: Steps, d: Path, dr: Json) -> Any:
    """Raster trang + mặt nạ tường nếu có đủ hai tệp và giải được; không thì None (dựng không dùng raster)."""
    page, mask = d / "page.png", d / "run/wallSegmentation/walls.png"
    if not (page.exists() and mask.exists()):
        return None
    try:
        return steps.decode_page_raster(
            page.read_bytes(), mask.read_bytes(), width_px=dr["width_px"], height_px=dr["height_px"]
        )
    except ValueError:
        return None


def _parse(steps: Steps, d: Path) -> tuple[Any, Any, Any]:
    """Đọc ba artifact ML (tường, đối tượng, chữ) của ca từ cache."""
    raw = [(d / "run" / n).read_bytes() for n in _NAMES]
    return steps.walls_from_json(raw[0]), steps.objects_from_json(raw[1]), steps.text_from_json(raw[2])


def _build(steps: Steps, d: Path, ctx: Json, st: Json, parsed: tuple[Any, Any, Any]) -> Any:
    """`build_layer` từ artifact ML đã đọc. Ném `ValueError` nếu dữ liệu dựng không hợp lệ."""
    dr = ctx["drawing"]
    pppm = dr["px_per_paper_mm"]
    step, name = steps.page_vectors
    vector_page = True if pppm is None else (d / "run" / step / name).exists()
    return steps.build_layer(
        level_id=ctx["levelId"],
        walls=parsed[0],
        objects=parsed[1],
        text=parsed[2],
        width_px=dr["width_px"],
        height_px=dr["height_px"],
        fallback_mm_per_px=Decimal(st["defaultScaleMmPerPx"]),
        clock=steps.system_clock_cls(),
        page=_page_raster(steps, d, dr),
        px_per_paper_mm=pppm,
        vector_page=vector_page,
        furniture=st["detectFurniture"],
    )


def _merged_layer(steps: Steps, built: Any, ctx: Json, out: Path) -> Any:
    """Như persist: ghi/đọc lại layer dựng, gắn chiều cao tầng, trộn vào tài liệu tầng rỗng; ném lỗi toàn vẹn nếu có."""
    raw_layer = built.to_json()
    (out / "built-layer.json").write_bytes(raw_layer)
    built = steps.built_layer_cls.from_json(raw_layer)
    merge = steps.make_merge(
        steps.with_floor_height(built.layer, ctx["floorHeightMm"]), built.scale_mm_per_px, steps.removed_cls()
    )
    empty = steps.spatial_layer_cls(walls=(), openings=(), rooms=(), furniture=())
    outcome = merge(empty, built.scale_mm_per_px)
    steps.check_candidate(outcome.layer, level_id=ctx["levelId"])
    return built, outcome.layer


def _quality(steps: Steps, built: Any, layer: Any, ctx: Json, st: Json, out: Path) -> Json:
    """Báo cáo chất lượng của pipeline (ghi `run-quality.json`) và bản tóm tắt như `quality.run_quality`."""
    # BENCH3D-24 `area_notes`, K-16 `area_labels`: mã cũ không nhận các khoá này
    extra = {key: getattr(built, key) for key in ("area_notes", "area_labels") if hasattr(built, key)}
    report = steps.build_report(
        run_id=ctx["run"]["id"], revision=1, layer=layer, level_id=ctx["levelId"],
        threshold=Decimal(st["confidenceThreshold"]), scale_basis=built.scale_basis,
        label_orientation=built.label_orientation, **extra,
    )  # fmt: skip
    (out / "run-quality.json").write_bytes(report.to_json_bytes())
    summary: Json = steps.summary(report, built)
    return summary


def _api_body(steps: Steps, built: Any, layer: Any, ctx: Json, cid: str, summary: Json) -> Json:
    """Thân `GET /spatial/layer` như API trả: level, scale*, readiness, layer, dimensions."""
    facts = steps.run_facts_cls(pinned=ctx["pinned"] or {}, quality_summary=summary)
    source = built.scale_source
    basis = steps.scale_basis_of(facts, source, source)
    body = {
        "revision": 1,
        "level": {
            "id": ctx["levelId"], "name": cid, "heightMm": ctx["floorHeightMm"], "confidence": 1.0, "source": "human",
            "reviewed": False, "scaleMillimetresPerPixel": float(built.scale_mm_per_px),
        },
        "scaleStatus": steps.scale_status(source), "scaleBasis": basis,
        "scaleSource": steps.provisional_scale_source(source),
        "readiness": dump(steps.readiness_of(run=facts, scale_source=source, layer=layer, scale_basis=basis)),
        "layer": dump(steps.layer_out(layer)),
        "axes": [],
        "dimensions": [dump(x) for x in steps.dimensions_out(built.dimensions)],
    }  # fmt: skip
    return {k: v for k, v in body.items() if v is not None}


def _failed(error: str, **extra: Any) -> Json:
    """Kết quả replay "lượt hỏng" với mã lỗi `error`."""
    return {"replayed": True, "status": "failed", "error": error, **extra}


def replay(cid: str, steps: Steps, cache: Path = CACHE, out: Path = OUT) -> Json:
    """Dựng lại phần sau-ML của một ca; ghi `layer.json`, `built-layer.json`, `run-quality.json` vào `out/<ca>/`.

    Trả {replayed, status, ...}: `replayed` False khi cache thiếu artifact (lượt gốc hỏng trước bước dựng).
    """
    d = cache / cid
    ctx: Json = json.loads((d / "ctx.json").read_text(encoding="utf-8"))
    st = _settings(ctx, steps)
    if ctx["drawing"] is None or ctx["run"] is None or not all((d / "run" / n).exists() for n in _NAMES):
        return {"replayed": False, "why": _WHY_MISSING}
    parsed = _parse(steps, d)
    t0 = time.monotonic()
    try:
        built = _build(steps, d, ctx, st, parsed)
    except ValueError as exc:
        return _failed("PIPELINE_BUILD_INVALID", detail=str(exc)[:200])
    build_s = time.monotonic() - t0
    # BENCH3D-19: tasks._build_and_write chặn ảnh phối cảnh sau build_layer (mã cũ không có hàm này)
    if steps.not_a_floor_plan is not None and steps.not_a_floor_plan(built.layer) is not None:
        return _failed("NOT_A_FLOOR_PLAN")
    try:
        built, candidate = _merged_layer(steps, built, ctx, out / cid)
    except Exception as exc:  # noqa: BLE001 - AppError LAYER_INTEGRITY_BROKEN... = lượt hỏng ở persist
        code = getattr(getattr(exc, "code", None), "code", None) or type(exc).__name__
        return _failed(str(code))
    layer = steps.with_areas(candidate)
    summary = _quality(steps, built, layer, ctx, st, out / cid)
    body = _api_body(steps, built, layer, ctx, cid, summary)
    (out / cid / "layer.json").write_text(
        json.dumps({"status": 200, "body": body}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {"replayed": True, "status": "completed", "build_s": round(build_s, 3), "summary": summary}


def run_cases(cases: list[str], steps: Steps, cache: Path = CACHE, out: Path = OUT) -> None:
    """Replay lần lượt từng ca; một ca lỗi không dừng cả lượt (REPLAY_CRASH). Ghi `replay.json` mỗi ca, in một dòng."""
    for cid in cases:
        (out / cid).mkdir(parents=True, exist_ok=True)
        t = time.monotonic()
        try:
            res = replay(cid, steps, cache, out)
        except Exception:  # noqa: BLE001 - một ca lỗi không dừng cả lượt
            res = {
                "replayed": True,
                "status": "failed",
                "error": "REPLAY_CRASH",
                "detail": traceback.format_exc()[-600:],
            }
        res["replay_s"] = round(time.monotonic() - t, 3)
        (out / cid / "replay.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.stdout.write(f"{cid} {res.get('status')} {res.get('error', '')} {res['replay_s']}\n")
        sys.stdout.flush()


def dirs_from_env(env: dict[str, str] | os._Environ[str]) -> tuple[Path, Path]:
    """(cache, out): `BENCH_REPLAY_CACHE`/`BENCH_REPLAY_OUT` khi chạy ngoài container, mặc định /cache, /out."""
    return Path(env.get("BENCH_REPLAY_CACHE") or CACHE), Path(env.get("BENCH_REPLAY_OUT") or OUT)


if __name__ == "__main__":
    run_cases(sys.argv[1:], load_steps(), *dirs_from_env(os.environ))
