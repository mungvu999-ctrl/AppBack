"""H1 và H1 ngữ cảnh trên mẫu dựng tay, giải bằng runner Node thật và schema FE thật (B0-07 [8]).

Mọi mẫu giải trong **một** lượt runner (fixture `verdicts`), mỗi test khẳng định một mẫu:
Node khởi động ~0,5 s mỗi lượt, một lượt mỗi mẫu là hàng chục giây vô ích.
"""

from pathlib import Path
from typing import Any, Final

import pytest

from tools.contract import check
from tools.contract.samples import EventSample, HttpSample
from tools.contract.tests import wire

SAMPLES: Final[dict[str, HttpSample]] = {
    "project": HttpSample("project", "projects_read_project", 200, wire.project()),
    "project_extra_key": HttpSample("x", "projects_read_project", 200, wire.project(ghostKey=wire.USER_ID)),
    "project_null_optional": HttpSample("x", "projects_read_project", 200, wire.project(address=None)),
    "project_unknown_enum": HttpSample("x", "projects_read_project", 200, wire.project(status="archived")),
    "error_known_resource": HttpSample(
        "x", "projects_read_project", 404, {"code": "NOT_FOUND", "requestId": "rid-1", "resource": "project"}
    ),
    "error_unknown_resource": HttpSample(
        "x", "projects_read_project", 404, {"code": "NOT_FOUND", "requestId": "rid-1", "resource": "file"}
    ),
    "conflict": HttpSample("x", "settings_replace_settings", 409, wire.conflict()),
    "conflict_without_version": HttpSample(
        "x", "settings_replace_settings", 409, {k: v for k, v in wire.conflict().items() if k != "currentVersion"}
    ),
    "no_content_empty": HttpSample("x", "measurements_delete_record", 204, None),
    "no_content_with_body": HttpSample("x", "measurements_delete_record", 204, {}),
    "empty_shape_with_body": HttpSample("x", "auth_logout", 200, {"ok": True}),
    "redirect": HttpSample("x", "projects_read_project", 307, None),
    "unmapped_operation": HttpSample("x", "ghost_read_thing", 200, {}),
    "array_shape": HttpSample("x", "floors_list_floors", 200, []),
    "refresh": HttpSample("x", "auth_refresh", 200, wire.refresh()),
    "refresh_owner_role": HttpSample("x", "auth_refresh", 200, wire.refresh(roles=["owner"])),
    "refresh_no_role": HttpSample("x", "auth_refresh", 200, wire.refresh(roles=[])),
    "refresh_two_roles": HttpSample("x", "auth_refresh", 200, wire.refresh(roles=["admin", "viewer"])),
    "refresh_missing_roles": HttpSample(
        "x", "auth_refresh", 200, {k: v for k, v in wire.refresh().items() if k != "roles"}
    ),
    "refresh_six_digits": HttpSample("x", "auth_refresh", 200, wire.refresh(expiresAt="2026-01-01T00:00:00.000000Z")),
    "house": HttpSample("x", "drawing_sets_read_house_proposal", 200, wire.house_proposal()),
    "house_off": HttpSample(
        "x", "drawing_sets_read_house_proposal", 200, {"enabled": False, "floors": [], "runIds": []}
    ),
    "house_conflict": HttpSample(
        "x",
        "drawing_sets_read_house_proposal",
        200,
        wire.house_proposal(
            wire.house_floor(
                isSection=True, questions=[{"code": "ORDER_TEXT_CONFLICT", "reason": "title", "textStoreyNo": 8}]
            )
        ),
    ),
    "house_extra_key": HttpSample(
        "x", "drawing_sets_read_house_proposal", 200, wire.house_proposal(wire.house_floor(ghostKey=1))
    ),
    "house_unknown_code": HttpSample(
        "x", "drawing_sets_read_house_proposal", 200, wire.house_proposal(wire.house_floor(questions=[{"code": "NEW"}]))
    ),
    "house_reason_elsewhere": HttpSample(
        "x",
        "drawing_sets_read_house_proposal",
        200,
        wire.house_proposal(wire.house_floor(questions=[{"code": "STOREY_UNSURE", "reason": "level"}])),
    ),
    "house_elevation_out_of_range": HttpSample(
        "x", "drawing_sets_read_house_proposal", 200, wire.house_proposal(wire.house_floor(elevationMm=400000))
    ),
    "house_section_false": HttpSample(
        "x", "drawing_sets_read_house_proposal", 200, wire.house_proposal(wire.house_floor(isSection=False))
    ),
    "house_off_with_rows": HttpSample("x", "drawing_sets_read_house_proposal", 200, wire.house_proposal(enabled=False)),
    "progress_completed": HttpSample(
        "x", "drawings_read_progress", 200, wire.progress("completed", endedAt=wire.AT, progressPercent=100)
    ),
    "progress_failed_ended": HttpSample(
        "x", "drawings_read_progress", 200, wire.progress("failed", endedAt=wire.AT, error="PIPELINE_SUPERSEDED")
    ),
    "n16_ok": HttpSample("x", "spatial_read_layer", 200, wire.layer_document(), {"floor_id": wire.LEVEL_ID}),
    "n16_other_floor": HttpSample("x", "spatial_read_layer", 200, wire.layer_document(), {"floor_id": "L-KHAC000000"}),
    "n16_stray_level": HttpSample(
        "x", "spatial_read_layer", 200, wire.layer_document(wall_level=wire.LEVEL_2_ID), {"floor_id": wire.LEVEL_ID}
    ),
    "n15_ok": HttpSample(
        "x", "spatial_read_graph", 200, wire.graph_document([wire.level(), wire.level(wire.LEVEL_2_ID, 1)])
    ),
    "n15_unsorted": HttpSample(
        "x", "spatial_read_graph", 200, wire.graph_document([wire.level(wire.LEVEL_2_ID, 1), wire.level()])
    ),
    "n15_dangling": HttpSample(
        "x", "spatial_read_graph", 200, wire.graph_document([wire.level()], wall_level="L-KHONGCO000")
    ),
    "n17_ok": HttpSample("x", "versions_list_versions", 200, wire.page([wire.version(3), wire.version(2)])),
    "n17_rising": HttpSample("x", "versions_list_versions", 200, wire.page([wire.version(1), wire.version(2)])),
    "n1_ok": HttpSample("x", "projects_list_summaries", 200, wire.page([wire.summary(12.5)])),
    "n1_three_decimals": HttpSample("x", "projects_list_summaries", 200, wire.page([wire.summary(12.345)])),
    "n7_ok": HttpSample(
        "x",
        "drawings_list_latest_uploads",
        200,
        wire.page([wire.latest_upload(wire.LEVEL_ID), wire.latest_upload(wire.LEVEL_2_ID)]),
        context={"floorOrder": [wire.LEVEL_ID, wire.LEVEL_2_ID]},
    ),
    "n7_reversed": HttpSample(
        "x",
        "drawings_list_latest_uploads",
        200,
        wire.page([wire.latest_upload(wire.LEVEL_2_ID), wire.latest_upload(wire.LEVEL_ID)]),
        context={"floorOrder": [wire.LEVEL_ID, wire.LEVEL_2_ID]},
    ),
    "n7_unknown_floor": HttpSample(
        "x",
        "drawings_list_latest_uploads",
        200,
        wire.page([wire.latest_upload("L-KHAC000000")]),
        context={"floorOrder": [wire.LEVEL_ID]},
    ),
    "n7_without_context": HttpSample(
        "x", "drawings_list_latest_uploads", 200, wire.page([wire.latest_upload(wire.LEVEL_ID)])
    ),
    "n23_ok": HttpSample(
        "x", "ml_list_families", 200, wire.page([{"family": name, "revision": 0} for name in wire.FAMILIES])
    ),
    "n23_two_families": HttpSample(
        "x", "ml_list_families", 200, wire.page([{"family": name, "revision": 0} for name in wire.FAMILIES[:2]])
    ),
}

EVENTS: Final[dict[str, EventSample]] = {
    "event_progress": EventSample("e", "streams_open_progress", wire.progress("running", progressPercent=5)),
    "event_failed_ended": EventSample("e", "streams_open_progress", wire.progress("failed", endedAt=wire.AT)),
    "event_bad_body": EventSample("e", "streams_open_progress", {"id": wire.UPLOAD_ID}),
}


@pytest.fixture(scope="module")
def verdicts(contract_build: Path) -> dict[str, dict[str, Any]]:
    """Kết quả runner cho mọi mẫu của module, theo nhãn."""
    http, events = check.decode(contract_build, list(SAMPLES.values()), list(EVENTS.values()))
    return dict(zip([*SAMPLES, *EVENTS], [*http, *events], strict=True))


def _decode_fails(verdict: dict[str, Any], fragment: str) -> None:
    """Mẫu hỏng ở bước giải, lý do chứa `fragment`."""
    assert verdict["decode"] == "fail", verdict
    assert fragment in verdict["reason"], verdict


@pytest.mark.parametrize(
    "label",
    [
        "project",
        "error_known_resource",
        "conflict",
        "no_content_empty",
        "array_shape",
        "refresh",
        "house",
        "house_off",
        "house_conflict",
    ],
)
def test_valid_samples_decode(verdicts: dict[str, dict[str, Any]], label: str) -> None:
    """Mẫu đúng schema FE (và đúng luật chọn schema theo status/`code`) giải đạt."""
    assert verdicts[label]["decode"] == "ok", verdicts[label]


def test_extra_key_fails_strict_schema(verdicts: dict[str, dict[str, Any]]) -> None:
    """K01: khoá lạ bị `.strict()` của FE từ chối."""
    _decode_fails(verdicts["project_extra_key"], "unrecognized_keys")


def test_null_optional_field_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """K02: trường tuỳ chọn là `null` (thay vì vắng) hỏng."""
    _decode_fails(verdicts["project_null_optional"], '["address"]')


def test_unknown_enum_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """Giá trị enum ngoài FE hỏng."""
    _decode_fails(verdicts["project_unknown_enum"], "invalid_enum_value")


def test_error_body_with_unknown_resource_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """Status ≥ 400 giải bằng `ApiErrorBodySchema`: `resource` lạ hỏng."""
    _decode_fails(verdicts["error_unknown_resource"], '["resource"]')


def test_version_conflict_without_current_version_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """`code = VERSION_CONFLICT` chọn `VersionConflictBodySchema`: thiếu `currentVersion` hỏng."""
    _decode_fails(verdicts["conflict_without_version"], '["currentVersion"]')


@pytest.mark.parametrize("label", ["no_content_with_body", "empty_shape_with_body"])
def test_empty_response_with_body_fails(verdicts: dict[str, dict[str, Any]], label: str) -> None:
    """204, hay mục `empty` của bản đồ, mà có thân → hỏng."""
    _decode_fails(verdicts[label], "phải có thân rỗng")


def test_status_outside_contract_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """3xx không có trong hợp đồng W8 (vd chuyển hướng dấu `/` cuối)."""
    _decode_fails(verdicts["redirect"], "status 307")


def test_operation_missing_from_map_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """Mẫu của thao tác không có trong `schema-map.ts` hỏng."""
    _decode_fails(verdicts["unmapped_operation"], "không có trong schema-map.ts")


@pytest.mark.parametrize(
    ("label", "fragment"),
    [
        ("refresh_owner_role", '["roles",0]'),
        ("refresh_no_role", '["roles"]'),
        ("refresh_two_roles", '["roles"]'),
        ("refresh_missing_roles", '["roles"]'),
        ("refresh_six_digits", '["expiresAt"]'),
    ],
)
def test_strict_refresh_rejects_loose_bodies(verdicts: dict[str, dict[str, Any]], label: str, fragment: str) -> None:
    """K04: thân refresh giải bằng `strict/refresh.ts`, không bằng schema `.passthrough()` của FE."""
    _decode_fails(verdicts[label], fragment)


@pytest.mark.parametrize(
    ("label", "fragment"),
    [
        ("house_extra_key", "unrecognized_keys"),
        ("house_unknown_code", '["floors",0,"questions",0,"code"]'),
        ("house_reason_elsewhere", '["floors",0,"questions",0,"reason"]'),
        ("house_elevation_out_of_range", '["floors",0,"elevationMm"]'),
        ("house_section_false", '["floors",0,"isSection"]'),
        ("house_off_with_rows", 'path=["floors"] code=custom'),
    ],
)
def test_strict_house_proposal_rejects_drift(verdicts: dict[str, dict[str, Any]], label: str, fragment: str) -> None:
    """L4-N07: N52 giải bằng `strict/houseProposal.ts` — khoá lạ, mã lạ, số ngoài khoảng FE đều đỏ ở bước 7."""
    _decode_fails(verdicts[label], fragment)


@pytest.mark.parametrize(
    "label", ["progress_completed", "n16_ok", "n15_ok", "n17_ok", "n1_ok", "n7_ok", "n23_ok", "event_progress"]
)
def test_context_rules_pass_on_consistent_samples(verdicts: dict[str, dict[str, Any]], label: str) -> None:
    """Mỗi luật H1 ngữ cảnh có một mẫu đạt: giải đạt rồi luật chạy và đạt."""
    assert verdicts[label]["decode"] == "ok", verdicts[label]
    assert verdicts[label]["context"] == "ok", verdicts[label]


@pytest.mark.parametrize(
    ("label", "fragment"),
    [
        ("progress_failed_ended", "K33"),
        ("event_failed_ended", "K33"),
        ("n16_other_floor", "khác floor_id"),
        ("n16_stray_level", "khác level.id"),
        ("n15_unsorted", "không sắp theo order"),
        ("n15_dangling", "không trỏ tới level"),
        ("n17_rising", "không giảm dần"),
        ("n1_three_decimals", "hơn 2 chữ số"),
        ("n7_reversed", "không sắp theo Floor.order"),
        ("n7_unknown_floor", "không có trong context.floorOrder"),
        ("n7_without_context", "thiếu context.floorOrder"),
        ("n23_two_families", "N23"),
    ],
)
def test_context_rules_fail_on_inconsistent_samples(
    verdicts: dict[str, dict[str, Any]], label: str, fragment: str
) -> None:
    """Mỗi luật H1 ngữ cảnh có một mẫu hỏng: giải đạt nhưng luật báo lệch."""
    assert verdicts[label]["decode"] == "ok", verdicts[label]
    assert verdicts[label]["context"] == "fail", verdicts[label]
    assert fragment in verdicts[label]["contextReason"]


def test_error_samples_skip_context_rules(verdicts: dict[str, dict[str, Any]]) -> None:
    """Luật ngữ cảnh chỉ chạy trên thân 2xx; thân lỗi không có luật nào."""
    assert verdicts["error_known_resource"]["context"] == "none"


def test_event_with_bad_body_fails(verdicts: dict[str, dict[str, Any]]) -> None:
    """Khung SSE giải bằng schema của S1 trong bản đồ."""
    _decode_fails(verdicts["event_bad_body"], "safeParse")
