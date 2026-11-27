# Prediction format (one JSON per set: `<out_dir>/<set>.json`)

Scored by `python -m tools.bench.muc3 --pred <out_dir>`. Every key is optional; a missing item counts as wrong
(and as "no prediction" in the coverage numbers).

Units: lengths in metres (`level_m`, `height_m`), `shift_mm` and `centre_mm` in millimetres,
sheet `box` in sheet pixels `[x0,y0,x1,y1]`, y down. `floor_no` is the storey number: 1 = the ground floor
(trệt / tầng 1), increasing upward; basements are 0, -1, ...; mezzanines carry no integer number (`floor_no` null,
`storey_label` "mezzanine"). This is the convention of the reviewed reference files (changed 19/11/2026).

Confidence convention (0..1): **>= 0.9** two independent sources agree, **0.7-0.9** one source,
**< 0.7** conflict or assumed default.

| key | item fields |
|---|---|
| `views` | `id` (pre-cut view id, matched to truth) **or** `sheet` + `box` (view cut by the pipeline); `kind` (`plan`/`section`/`elevation`/`roof`/`site`/`unknown`); `floor_no` (int or null); `confidence` |
| `floors` | `floor_no`; `view_id`; `level_m` (float or null); `level_source` (`plan+section`/`plan`/`section`/`stairs`/`default`); `confidence` |
| `storey_heights` | `from`, `to` (floor_no, lower first); `height_m`; `source` (same names as `level_source`); `confidence` |
| `stack` | `a`, `b` (view ids, lower floor first); `shift_mm` `[dx,dy]` of b relative to a, or null; `cues` (names, e.g. `truc`, `thang`); `confidence` |
| `stair_cores` | `floors` (list of floor_no the core runs through); `centre_mm` `[x,y]` in the lowest floor's frame; `confidence` |
| `sheet_views` | optional. `{"<sheet file stem>": [{"box":[x0,y0,x1,y1], "kind", "confidence"}]}` for the sheet-splitting task |
| `roof` | optional, one object (not a list), scored by `python -m tools.bench.muc3.roof`: `type` (`flat`/`pitched`/`mixed`/`unknown`); `confidence`; `base_level_m`, `top_level_m` (float or null); `level_confidence`. `{}` = no roof proposed. A file with these fields at the top level (old `pred_roof/`) is read the same way |

Matching rules used by the scorer: views by `id`; storey heights by `(from,to)`; stack pairs by the floor
numbers of `a`/`b` (looked up in `floors[].view_id` then `views[].floor_no`); sheet boxes greedily one-to-one at IoU >= 0.5.

```json
{
  "views": [
    {"id": "DS-248__f00_plan", "kind": "plan", "floor_no": 1, "confidence": 0.95},
    {"id": "DS-248__f02_plan", "kind": "plan", "floor_no": 2, "confidence": 0.8},
    {"id": "DS-248__f07_section", "kind": "section", "floor_no": null, "confidence": 0.9}
  ],
  "floors": [
    {"floor_no": 1, "view_id": "DS-248__f00_plan", "level_m": -1.0, "level_source": "plan+section", "confidence": 0.95},
    {"floor_no": 2, "view_id": "DS-248__f02_plan", "level_m": 4.4, "level_source": "plan", "confidence": 0.8}
  ],
  "storey_heights": [{"from": 1, "to": 2, "height_m": 5.4, "source": "plan+section", "confidence": 0.95}],
  "stack": [{"a": "DS-248__f00_plan", "b": "DS-248__f02_plan", "shift_mm": [28, 0], "cues": ["truc", "thang"], "confidence": 0.9}],
  "stair_cores": [{"floors": [2, 3, 4], "centre_mm": [14673, 8464], "confidence": 0.8}],
  "sheet_views": {"DS-248__s00": [{"box": [173, 40, 2136, 2358], "kind": "plan", "confidence": 0.9}]}
}
```
