"""`DesignDirectorSettings.available`: cần công tắc, khoá service account và xác nhận đã thông báo (L6-N01)."""

import logging

import pytest
from pydantic import SecretStr

from apps.api.design_director import settings as module
from apps.api.design_director.settings import DesignDirectorSettings

KEY = '{"type": "service_account"}'


def _settings(*, enabled: bool = True, key: str | None = KEY, ack: bool = False) -> DesignDirectorSettings:
    return DesignDirectorSettings(
        design_director_enabled=enabled,
        design_director_vertex_sa_json=SecretStr(key) if key is not None else None,
        design_director_disclosure_ack=ack,
    )


def test_available_requires_disclosure_ack__l6n01(caplog: pytest.LogCaptureFixture) -> None:
    """Bật + có khoá nhưng chưa ack -> tắt và log `design_director_disclosure_missing` đúng một lần."""
    module._warn_disclosure_missing.cache_clear()
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        assert _settings(ack=False).available is False
        assert _settings(ack=False).available is False
    assert [r.getMessage().split(":")[0] for r in caplog.records] == ["design_director_disclosure_missing"]


def test_available_true_with_ack__l6n01() -> None:
    assert _settings(ack=True).available is True


def test_ack_default_is_false_and_does_not_enable__l6n01() -> None:
    """Mặc định chưa ack; ack một mình (công tắc tắt hoặc thiếu khoá) không bật tính năng."""
    assert DesignDirectorSettings().design_director_disclosure_ack is False
    assert _settings(enabled=False, ack=True).available is False
    assert _settings(key=None, ack=True).available is False
    assert _settings(key="  ", ack=True).available is False
