"""Fixture bật AI chọn phong cách cho test (AID-01): công tắc + khoá giả, hạn mức nhỏ."""

from collections.abc import Iterator
from typing import Final

import pytest

from apps.api.design_director.settings import reset_design_director_settings_cache

TEST_QUOTA: Final = 3
"""Hạn mức ngày trong test — đủ nhỏ để chạm trần nhanh."""


@pytest.fixture
def design_director_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`DESIGN_DIRECTOR_ENABLED=true` + `DISCLOSURE_ACK=true` (L6-N01), khoá service account giả, hạn mức `TEST_QUOTA`.

    Không gọi mạng."""
    monkeypatch.setenv("DESIGN_DIRECTOR_ENABLED", "true")
    monkeypatch.setenv("DESIGN_DIRECTOR_DISCLOSURE_ACK", "true")
    monkeypatch.setenv("DESIGN_DIRECTOR_VERTEX_SA_JSON", '{"type": "service_account"}')
    monkeypatch.setenv("DESIGN_DIRECTOR_DAILY_QUOTA", str(TEST_QUOTA))
    reset_design_director_settings_cache()
    yield
    reset_design_director_settings_cache()


@pytest.fixture
def design_director_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Công tắc bật nhưng **thiếu** khoá: coi như tắt (N48 → 503)."""
    monkeypatch.setenv("DESIGN_DIRECTOR_ENABLED", "true")
    monkeypatch.delenv("DESIGN_DIRECTOR_VERTEX_SA_JSON", raising=False)
    reset_design_director_settings_cache()
    yield
    reset_design_director_settings_cache()
