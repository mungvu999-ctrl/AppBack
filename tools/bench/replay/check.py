"""Kiểm trung thực replay so với lượt thật: python -X utf8 -m tools.bench.replay.check <ra-replay>

So từng ca (tập bench <-> lượt `BENCH_RUN_BENCH`, F <-> `BENCH_RUN_F`; xem `config.replay_sets`):
 - bước dựng: built-layer.json replay <-> spatialDataBuild/layer.json thật (ml-cache): số tường/ô mở/phòng/đồ,
   tỉ lệ, gốc;
 - API: layer.json replay <-> layer.json thật: số tường/cửa/cửa sổ/phòng, scaleMillimetresPerPixel, scaleStatus,
   readiness;
 - quality: tóm tắt replay <-> pipeline_run_models.quality_summary thật (checks/criticalChecks/hasCritical/scaleChecks);
 - chấm: chấm bản sao lượt thật bỏ probe/calibrated (ref/) và replay, so verdict + từng tiêu chí.
Ghi `<ra-replay>/check.json` và in các ca lệch.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tools.bench import config
from tools.bench.console import say
from tools.bench.score.io import Bench
from tools.bench.score.report import score_run

FILES = ("layer.json", "spatial.json", "quality.json", "progress.json", "meta.json", "page.png")
Diffs = list[str]


def js(p: Path) -> Any:
    """Đọc JSON của `p`; thiếu tệp -> None."""
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def built_sig(b: dict[str, Any] | None) -> dict[str, Any] | None:
    """Chữ ký bước dựng: số tường/ô mở/phòng/đồ, tỉ lệ, gốc tỉ lệ."""
    if b is None:
        return None
    layer = b.get("layer") or {}
    return {
        "walls": len(layer.get("walls", [])), "openings": len(layer.get("openings", [])),
        "rooms": len(layer.get("rooms", [])), "furniture": len(layer.get("furniture", [])),
        "scale": b.get("scaleMmPerPx"), "basis": b.get("scaleBasis"), "source": b.get("scaleSource"),
    }  # fmt: skip


def api_sig(x: dict[str, Any] | None) -> dict[str, Any]:
    """Chữ ký thân API layer: số tường/cửa/cửa sổ/phòng, tỉ lệ, trạng thái, readiness."""
    body = (x or {}).get("body") or {}
    layer, lv, rd = body.get("layer") or {}, body.get("level") or {}, body.get("readiness") or {}
    ops = layer.get("openings") or []
    return {
        "walls": len(layer.get("walls") or []), "doors": sum(o.get("kind") == "door" for o in ops),
        "windows": sum(o.get("kind") == "window" for o in ops), "rooms": len(layer.get("rooms") or []),
        "scale": lv.get("scaleMillimetresPerPixel"), "scaleStatus": body.get("scaleStatus"),
        "scaleBasis": body.get("scaleBasis"), "state": rd.get("state"), "reasons": rd.get("reasons"),
        "checks": rd.get("checks"),
    }  # fmt: skip


def q_sig(s: dict[str, Any] | None) -> dict[str, Any] | None:
    """Chữ ký tóm tắt quality."""
    if s is None:
        return None
    return {
        k: s.get(k) for k in ("hasCritical", "checks", "criticalChecks", "scaleBasis", "scaleChecks", "lowConfidence")
    }


def _built_diff(cache: Path, cid: str, rep: Path, replayed: bool) -> Diffs:
    """Lệch ở bước dựng (chỉ khi ca được replay)."""
    bo, br = built_sig(js(cache / cid / "run/spatialDataBuild/layer.json")), built_sig(js(rep / "built-layer.json"))
    return [f"dựng {bo} ≠ {br}"] if bo != br and replayed else []


def _api_diff(orig: Path, rep: Path) -> Diffs:
    """Lệch ở thân API layer."""
    ao, ar = api_sig(js(orig / "layer.json")), api_sig(js(rep / "layer.json"))
    return ["API " + ", ".join(f"{k}: {ao[k]}→{ar[k]}" for k in ao if ao[k] != ar[k])] if ao != ar else []


def _quality_diff(cache: Path, cid: str, rep: Path, replayed: bool) -> Diffs:
    """Lệch ở tóm tắt quality (chỉ khi ca được replay và lượt thật có tóm tắt)."""
    qo = q_sig((js(cache / cid / "ctx.json") or {}).get("qualitySummary"))
    qr = q_sig(js(rep / "replay.json").get("summary"))
    if qo != qr and replayed and qo is not None:
        return ["quality " + ", ".join(f"{k}: {qo[k]}→{(qr or {}).get(k)}" for k in qo if qo[k] != (qr or {}).get(k))]
    return []


def _score_diff(vo: dict[str, Any] | None, vr: dict[str, Any] | None) -> Diffs:
    """Lệch ở kết luận và từng tiêu chí chấm giữa lượt thật (vo) và replay (vr)."""
    if vo and vr:
        out = []
        if vo["verdict_auto"] != vr["verdict_auto"]:
            out.append(f"score {vo['verdict_auto']}→{vr['verdict_auto']}")
        keys = set(vo["c"]) | set(vr["c"])
        cd = {k: (vo["c"].get(k), vr["c"].get(k)) for k in keys if vo["c"].get(k) != vr["c"].get(k)}
        if cd:
            out.append("tiêu chí " + ", ".join(f"{k}:{x}→{y}" for k, (x, y) in sorted(cd.items())))
        return out
    return [f"score có/không {bool(vo)}/{bool(vr)}"] if bool(vo) != bool(vr) else []


def _ref_copy(orig_run: Path, ref: Path) -> None:
    """Bản sao lượt thật với cùng bộ tệp replay có (không probe/calibrated) để chấm công bằng."""
    for m in orig_run.glob("*/meta.json"):
        d = ref / m.parent.name
        d.mkdir(parents=True, exist_ok=True)
        for f in FILES:
            if (m.parent / f).exists():
                shutil.copy(m.parent / f, d / f)


def _scores(run: Path, root: Path) -> dict[str, Any]:
    """Chấm một lượt -> {id: kết quả}."""
    return {r["id"]: r for r in score_run(Bench(root, run))}


def compare_set(rep: Path, ref: Path, orig_run: Path, score_root: Path, cache: Path) -> list[tuple[str, bool, Diffs]]:
    """So replay `rep` với lượt thật `orig_run` (và bản sao chấm `ref`); trả [(ca, đã replay, các lệch)]."""
    if not ref.exists():
        _ref_copy(orig_run, ref)
    sr, sp = _scores(ref, score_root), _scores(rep, score_root)
    rows = []
    for d in sorted(p for p in rep.iterdir() if (p / "replay.json").exists()):
        cid = d.name
        replayed = bool(js(d / "replay.json").get("replayed"))
        diffs = (
            _built_diff(cache, cid, d, replayed) + _api_diff(orig_run / cid, d) + _quality_diff(cache, cid, d, replayed)
        )
        rows.append((cid, replayed, diffs + _score_diff(sr.get(cid), sp.get(cid))))
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    """Điểm vào: so mọi tập có trong `<ra-replay>`; in số ca khớp và các ca lệch; ghi `check.json`."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.replay.check", description=__doc__)
    ap.add_argument("replay", type=Path)
    a = ap.parse_args(argv)
    root = config.bench_root()
    cache = config.ml_cache(root)
    rows: list[tuple[str, str, bool, Diffs]] = []
    for s, (orig_run, score_root) in config.replay_sets(root).items():
        if (a.replay / s).exists():
            ref = a.replay.parent / f"ref-{s}"
            rows += [(s, c, rp, d) for c, rp, d in compare_set(a.replay / s, ref, orig_run, score_root, cache)]
    ok = sum(not d for _, _, _, d in rows)
    say(f"KHỚP {ok}/{len(rows)} ca ({100 * ok / max(len(rows), 1):.1f} %)")
    for s, cid, replayed, diffs in rows:
        if diffs or not replayed:
            say(f"- {s}/{cid}{'' if replayed else ' (không replay: chép lượt gốc)'}: " + (" | ".join(diffs) or "khớp"))
    data = [{"set": s, "id": c, "replayed": rp, "diffs": d} for s, c, rp, d in rows]
    (a.replay / "check.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
