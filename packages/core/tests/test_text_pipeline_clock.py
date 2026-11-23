import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args

import pytest

from packages.core.clock import Clock, SystemClock
from packages.core.errors import ERRORS
from packages.core.pipeline import PIPELINE_STEPS, PipelineCode, PipelineStep
from packages.core.text import nfc, normalize_email
from packages.testing.fixtures.clock import FakeClock

# --- text ----------------------------------------------------------------------


def test_nfc_composes() -> None:
    decomposed = unicodedata.normalize("NFD", "Ánh Việt")
    assert decomposed != "Ánh Việt"
    assert nfc(decomposed) == "Ánh Việt"


def test_normalize_email_case_and_nfd_equal() -> None:
    composed = "  Ánh@Example.COM "
    decomposed = unicodedata.normalize("NFD", composed)
    assert normalize_email(composed) == normalize_email(decomposed) == "ánh@example.com"


def test_normalize_email_casefold() -> None:
    assert normalize_email("STRASSE@x.de") == normalize_email("straße@x.de")


# --- pipeline ------------------------------------------------------------------


def test_pipeline_steps_match_fe() -> None:
    assert PIPELINE_STEPS == (
        ("preprocess", 5),
        ("wallSegmentation", 30),
        ("openingAndFurnitureDetection", 20),
        ("dimensionReading", 15),
        ("spatialDataBuild", 20),
        ("qualityCheck", 10),
    )
    assert sum(weight for _, weight in PIPELINE_STEPS) == 100
    assert tuple(step for step, _ in PIPELINE_STEPS) == get_args(PipelineStep)


def test_pipeline_codes_not_http_codes() -> None:
    assert [c.value for c in PipelineCode] == ["PIPELINE_SUPERSEDED", "PIPELINE_CANCELLED", "SCALE_UNRESOLVED"]
    registered = {c.code for c in ERRORS.all()}
    assert not registered & {c.value for c in PipelineCode}


# --- clock ---------------------------------------------------------------------


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)


def test_fake_clock_start_and_advance(fake_clock: FakeClock) -> None:
    clock: Clock = fake_clock
    assert clock.now() == datetime(2026, 1, 1, tzinfo=UTC)
    fake_clock.advance(timedelta(hours=1, milliseconds=5))
    assert clock.now() == datetime(2026, 1, 1, 1, 0, 0, 5000, tzinfo=UTC)
    fake_clock.advance(timedelta(days=-1))
    assert clock.now() == datetime(2025, 12, 31, 1, 0, 0, 5000, tzinfo=UTC)


def test_fake_clock_set_converts_to_utc(fake_clock: FakeClock) -> None:
    fake_clock.set(datetime(2026, 5, 1, 7, tzinfo=timezone(timedelta(hours=7))))
    assert fake_clock.now() == datetime(2026, 5, 1, tzinfo=UTC)
    assert fake_clock.now().tzinfo is UTC


def test_fake_clock_rejects_naive(fake_clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="múi giờ"):
        fake_clock.set(datetime(2026, 1, 1))  # noqa: DTZ001 — kiểm datetime không múi giờ bị từ chối
    with pytest.raises(ValueError, match="múi giờ"):
        FakeClock(start=datetime(2026, 1, 1))  # noqa: DTZ001 — như trên
