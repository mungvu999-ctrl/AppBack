"""Tương tác với máy chạy stack f14: lệnh docker, RAM trống, hàng đợi Celery, chụp phiên bản stack."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

CONTAINERS = ["f14-api-1", "f14-worker-1", "f14-ml-1", "f14-web-1"]
QUEUES = ["default", "pipeline.cpu", "pipeline.gpu", "ml"]
Runner = Callable[..., str]
_GIB = 2**30


def sh(*cmd: str) -> str:
    """Chạy lệnh, trả stdout đã bỏ khoảng trắng hai đầu; lỗi chạy không ném (người gọi tự kiểm chuỗi rỗng)."""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)  # noqa: S603
    return r.stdout.strip()


class _MemoryStatus(ctypes.Structure):
    """`MEMORYSTATUSEX` rút gọn của Windows (GlobalMemoryStatusEx)."""

    _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
        (n, ctypes.c_ulonglong) for n in ("total", "avail", "ptotal", "pavail", "vtotal", "vavail", "ext")
    ]


def free_gb(platform: str = sys.platform) -> float:
    """RAM vật lý còn trống (GiB): Windows qua `GlobalMemoryStatusEx`, nơi khác qua `sysconf`."""
    if platform != "win32":
        sysconf = vars(os)["sysconf"]  # chỉ có ngoài Windows
        return float(sysconf("SC_AVPHYS_PAGES") * sysconf("SC_PAGE_SIZE")) / _GIB
    m = _MemoryStatus()
    m.len = ctypes.sizeof(_MemoryStatus)
    kernel32 = ctypes.__dict__["windll"].kernel32  # windll chỉ có trên Windows
    kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return float(m.avail) / _GIB


def queues_busy(run: Runner = sh) -> int:
    """Tổng số việc đang chờ ở các hàng đợi Celery của f14 (0 = rảnh)."""
    out = run("docker", "exec", "f14-redis-broker-1", "sh", "-c", " ; ".join(f"redis-cli llen {q}" for q in QUEUES))
    return sum(int(x) for x in out.split() if x.isdigit())


def _container_info(run: Runner, name: str) -> dict[str, Any]:
    """Ảnh, id ảnh, giờ khởi động, md5 các `.py` khác ảnh gốc (vá nóng) và `ML_BACKEND` của một container."""
    info = json.loads(run("docker", "inspect", name) or "[{}]")[0]
    changed = [
        ln[2:]
        for ln in run("docker", "diff", name).splitlines()
        if ln[:1] in "AC" and ln.endswith(".py") and "__pycache__" not in ln
    ]
    md5 = run("docker", "exec", name, "md5sum", *changed) if changed else ""
    return {
        "image": info.get("Config", {}).get("Image"),
        "imageId": info.get("Image"),
        "started": info.get("State", {}).get("StartedAt"),
        "hotpatch_md5": sorted(md5.splitlines()),
        "ML_BACKEND": run("docker", "exec", name, "printenv", "ML_BACKEND"),
    }


def snapshot(run: Runner = sh) -> dict[str, Any]:
    """Phiên bản stack: ảnh + tệp .py khác ảnh gốc (vá nóng) kèm md5, để biết kết quả ứng với mã nào."""
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "containers": {c: _container_info(run, c) for c in CONTAINERS},
    }
