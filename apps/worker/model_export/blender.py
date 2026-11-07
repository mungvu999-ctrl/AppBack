"""Dòng lệnh Blender cho `compile_plan.bpy` (`-b --factory-startup --python-exit-code 1 -t 4`, như LB-01)."""

from pathlib import Path
from typing import Final

__all__ = ["COMPILE_SCRIPT", "blender_argv"]

COMPILE_SCRIPT: Final = Path(__file__).with_name("compile_plan.bpy")
BLENDER_THREADS: Final = 4


def blender_argv(blender_bin: str, plan: Path, pack_dir: Path, out: Path) -> list[str]:
    """argv đầy đủ: Blender nền, không addon người dùng, lỗi Python → mã thoát 1."""
    return [
        blender_bin,
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "-t",
        str(BLENDER_THREADS),
        "-P",
        str(COMPILE_SCRIPT),
        "--",
        str(plan),
        str(pack_dir),
        str(out),
    ]
