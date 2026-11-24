"""Biên từng trường của `RunStepPayload`, `BuildStepPayload` (B5-05 [2], [8] "Test payload")."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.messaging.payloads.pipeline import BuildStepPayload, RunStepPayload

_RUN = "run_01M3Q24A9CTVC9TRWSV62CAR9D"
"""Id cố định, không sinh lúc thu thập: `parametrize` phải cho cùng nhãn ở mọi worker `xdist`."""
_LEVEL = "L-ABCDEFGHIJ"
_PREFIX = f"projects/prj_00000000000000000000000000/runs/{_RUN}/"


def _kwargs(**overrides: object) -> dict[str, object]:
    """Bộ trường hợp lệ tối thiểu của `BuildStepPayload`, ghi đè theo test."""
    base: dict[str, object] = {
        "run_id": _RUN,
        "level_id": _LEVEL,
        "run_prefix": _PREFIX,
        "width_px": 1600,
        "height_px": 1200,
        "fallback_mm_per_px": Decimal("10"),
    }
    base.update(overrides)
    return base


def test_run_step_payload_accepts_run_id() -> None:
    """`RunStepPayload(run_id=...)` hợp lệ đạt `is_id("run", …)`."""
    assert RunStepPayload(run_id=_RUN).run_id == _RUN


def test_run_step_payload_rejects_bad_run_id() -> None:
    """Id sai tiền tố → `ValidationError`."""
    with pytest.raises(ValidationError):
        RunStepPayload(run_id="upl_" + _RUN.removeprefix("run_"))


def test_build_step_payload_accepts_minimum() -> None:
    """Bộ trường hợp lệ tối thiểu dựng được, `schema_version` mặc định 1."""
    payload = BuildStepPayload(**_kwargs())
    assert payload.schema_version == 1


def test_build_step_payload_detect_furniture_legacy_default_is_true__l2n05() -> None:
    """L2-N05 (B): thông điệp cũ vắng `detect_furniture` -> `True` (dựng đồ như trước QT-FURN-TOGGLE), còn cài đặt dự
    án mặc định `False`. Lệch này CÓ CHỦ Ý ghi nhận (nơi tạo payload luôn truyền giá trị); đổi thì làm theo K-20."""
    assert BuildStepPayload(**_kwargs()).detect_furniture is True
    assert BuildStepPayload(**_kwargs(detect_furniture=False)).detect_furniture is False


@pytest.mark.parametrize("size", [0, -1])
def test_build_step_payload_rejects_size_below_one(size: int) -> None:
    """`width_px`/`height_px` < 1 → `ValidationError`."""
    with pytest.raises(ValidationError):
        BuildStepPayload(**_kwargs(width_px=size))


def test_build_step_payload_accepts_size_one() -> None:
    """Biên dưới `width_px == 1` là hợp lệ."""
    assert BuildStepPayload(**_kwargs(width_px=1)).width_px == 1


@pytest.mark.parametrize("scale", ["0", "-1", "1000.000001", "NaN"])
def test_build_step_payload_rejects_bad_scale(scale: str) -> None:
    """`fallback_mm_per_px` phải `0 < s ≤ 1000`, hữu hạn."""
    with pytest.raises(ValidationError):
        BuildStepPayload(**_kwargs(fallback_mm_per_px=scale))


def test_build_step_payload_accepts_scale_boundaries() -> None:
    """Biên `fallback_mm_per_px` ở 1000 (hợp lệ) là chuỗi Decimal trên dây."""
    payload = BuildStepPayload(**_kwargs(fallback_mm_per_px="1000"))
    assert payload.model_dump(mode="json")["fallback_mm_per_px"] == "1000"


def test_build_step_payload_rejects_bad_level_id() -> None:
    """`level_id` sai mẫu `L-<base36 HOA>` → `ValidationError`."""
    with pytest.raises(ValidationError):
        BuildStepPayload(**_kwargs(level_id="prj_not_a_level"))


@pytest.mark.parametrize(
    "prefix",
    [
        f"other/prj_x/runs/{_RUN}/",
        f"projects/prj_x/runs/{_RUN}",
        f"projects/prj_x/../runs/{_RUN}/",
        "projects/prj_x/runs/run_other/",
    ],
)
def test_build_step_payload_rejects_bad_run_prefix(prefix: str) -> None:
    """`run_prefix` phải bắt đầu `projects/`, kết thúc `/runs/{run_id}/`, không `..`."""
    with pytest.raises(ValidationError):
        BuildStepPayload(**_kwargs(run_prefix=prefix))


def test_build_step_payload_rejects_unknown_field() -> None:
    """`TaskPayload` cấm khoá lạ (`extra=\"forbid\"`)."""
    with pytest.raises(ValidationError):
        BuildStepPayload.model_validate({**_kwargs(), "extra_field": 1})
