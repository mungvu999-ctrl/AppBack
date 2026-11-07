"""Blender giả cho test: chỉ thay ranh giới tiến trình con, mọi thứ khác của task chạy thật.

    python -m apps.worker.model_export.tests.fake_blender --mode ok -- <plan.json> <pack_dir> <out_dir>

Chế độ qua argv (môi trường của con bị lọc): `ok` ghi GLB + `geometry.json` hợp lệ (bản ghi trong
`geometry.json` = bản ghi `blenderRecords` của plan), `open` thêm một tường hở, `degenerate` thêm một
hộp cửa có mặt suy biến, `badglb` ghi GLB sai version, `nogeom` bỏ `geometry.json`, `fail` thoát 1,
`sleep` ngủ lâu, `killed` tự `SIGKILL` (giả OOM). Chỉ dùng thư viện chuẩn.
"""

import json
import os
import signal
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path


def glb(version: int = 2) -> bytes:
    """GLB nhỏ có một khúc JSON `{"asset":{"version":"2.0"}}` đệm khoảng trắng."""
    chunk = b'{"asset":{"version":"2.0"}}'
    chunk += b" " * (-len(chunk) % 4)
    body = struct.pack("<I4s", len(chunk), b"JSON") + chunk
    return struct.pack("<4sII", b"glTF", version, 12 + len(body)) + body


def argv_for(mode: str) -> Callable[[Path, Path, Path], list[str]]:
    """`ArgvFor` cho task: chạy bộ giả này bằng trình Python hiện tại với chế độ `mode`."""

    def build(plan: Path, pack: Path, out: Path) -> list[str]:
        """argv như của Blender thật sau dấu `--`."""
        return [
            sys.executable,
            "-m",
            "apps.worker.model_export.tests.fake_blender",
            "--mode",
            mode,
            "--",
            str(plan),
            str(pack),
            str(out),
        ]

    return build


def main(argv: list[str]) -> int:
    """Đọc `--mode` và ba đối số sau `--`, làm đúng việc của chế độ."""
    mode = argv[argv.index("--mode") + 1]
    plan_path, _pack, out = (Path(v) for v in argv[argv.index("--") + 1 :][:3])
    if mode == "fail":
        print("Traceback: giả lỗi Python trong compile_plan", file=sys.stderr)
        return 1
    if mode == "sleep":
        time.sleep(60)
    if mode == "killed":
        os.kill(os.getpid(), signal.SIGKILL)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    (out / "model.glb").write_bytes(glb(1 if mode == "badglb" else 2))
    geometry: dict[str, object] = {
        "objects": len(plan.get("prisms", [])),
        "open": [],
        "degenerate": 0,
        "degenerateObjects": [],
        "tris": 12,
        "records": plan.get("blenderRecords", []),
    }
    if mode == "open":
        geometry["open"] = [{"name": "Wall_W-1", "appId": "W-1", "kind": "wall", "boundary": 4, "nonManifold": 4}]
    if mode == "degenerate":
        geometry["degenerate"] = 2
        geometry["degenerateObjects"] = [{"name": "Door_D-1_Leaf", "appId": None, "kind": "assembly-part", "faces": 2}]
    if mode != "nogeom":
        (out / "geometry.json").write_text(json.dumps(geometry), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
