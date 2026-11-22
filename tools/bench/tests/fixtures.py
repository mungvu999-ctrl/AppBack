"""Fixture dùng chung (nạp qua `pytest_plugins`): bộ dữ liệu tổng hợp dựng một lần cho cả phiên test."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.bench.tests import synth


@pytest.fixture(scope="session")
def synth_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Gốc dữ liệu tổng hợp (MANIFEST.csv, plans/, answers/, run/); chỉ đọc trong test."""
    root = tmp_path_factory.mktemp("bench")
    synth.build_bench(root)
    return root
