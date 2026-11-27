"""Phần thuần của tác vụ ráp nhà (NHA-07a): id mái, dấu vân, cốt diềm, và luật M-11 / M-16 trong `settle`."""

from typing import Any

import pytest

from packages.core.ids import is_spatial_id
from packages.domain.drawing_set.assembly import (
    FloorAssembly,
    eave_above_floor_mm,
    fingerprint,
    open_air_room,
    roof_id,
    settle,
)
from packages.domain.drawing_set.building import LevelSpec, Measured, RoofSpec
from packages.domain.spatial.model import Roof

LEVEL = "L-0000000001"
OWN = roof_id(7)
HUMAN_ID = "RF-HUMAN00001"


def _roof(roof_id_: str = OWN, *, width: float = 8.0, **marks: Any) -> Roof:
    """Một mái một node; `width` đổi thì dấu vân đổi; `marks` ghi đè `source`/`reviewed`/`edited`."""
    values: dict[str, Any] = {"confidence": 0.6, "source": "ai", "reviewed": False}
    nodes = ({"type": "roof", "width": width},)
    return Roof(id=roof_id_, level_id=LEVEL, nodes=nodes, **(values | marks))


def _previous(roof: Roof | None = None, **changes: Any) -> FloorAssembly:
    """Kết quả lượt ráp trước: đã dựng `roof` (hoặc chưa dựng gì khi `None`)."""
    values: dict[str, Any] = {"level_id": LEVEL, "run_id": "run_1", "revision": 1, "height_mm": 3000, "code": "BUILT"}
    if roof is not None:
        values |= {"fingerprint": fingerprint(roof.nodes), "roof_ids": (roof.id,)}
    return FloorAssembly(**(values | changes))


def _measured(value: float | str, source: str = "default") -> Measured:
    """Một giá trị đo với nguồn cho trước."""
    return Measured.model_validate({"value": value, "confidence": 0.5, "source": source})


def _spec(eave: Measured | None) -> RoofSpec:
    """`RoofSpec` hai dốc 30° với cốt diềm `eave`."""
    return RoofSpec(
        level_ref="V1",
        form=_measured("gable"),
        pitch_deg=_measured(30.0),
        quarter_turns=_measured(0),
        eave_elevation_mm=eave,
        overhang_mm=_measured(300),
    )


def test_roof_id_is_deterministic_per_floor_and_matches_the_id_rule() -> None:
    """Cùng tầng → cùng id (bộ mới thay được mái của bộ cũ); khác tầng → khác id; đúng mẫu `RF-` + 16 base36."""
    assert roof_id(7) == roof_id(7)
    assert roof_id(7) != roof_id(8)
    assert all(is_spatial_id("roof", roof_id(pk)) and len(roof_id(pk)) == 19 for pk in (0, 1, 7, 2**62))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Sân thượng", True),
        ("BAN CÔNG 2", True),
        ("roof terrace", True),
        ("Balcony", True),
        ("PATIO", True),
        ("Balcony 2", True),
        ("Phòng ngủ có ban công", False),  # chỉ nhắc tới ban công: vẫn là phòng trong nhà
        ("Balcony door lobby", False),
        ("Phòng ngủ", False),
        ("Sân", False),
        ("", False),
    ],
)
def test_open_air_room_reads_the_folded_name(name: str, expected: bool) -> None:
    """Sân thượng/ban công nhận theo tên đã bỏ dấu và viết hoa; "sân" một mình không tính (thiết kế §2.C Câu 5)."""
    assert open_air_room(name) is expected


def test_fingerprint_ignores_key_order_and_follows_the_geometry() -> None:
    """Thứ tự khoá không đổi dấu vân; một số đo đổi thì dấu vân đổi."""
    assert fingerprint([{"a": 1, "b": 2.5}]) == fingerprint([{"b": 2.5, "a": 1}])
    assert fingerprint([{"a": 1}]) != fingerprint([{"a": 2}])
    assert len(fingerprint([])) == 32


def test_floor_assembly_round_trips_as_camel_case_jsonb() -> None:
    """Hình dạng lưu JSONB là camelCase và đọc lại ra đúng giá trị; khoá lạ bị từ chối (JSONB hỏng không lọt)."""
    entry = _previous(_roof(), user_removed=False)
    stored = entry.model_dump(mode="json", by_alias=True)
    assert set(stored) >= {"levelId", "runId", "heightMm", "roofIds", "userRemoved", "fingerprint", "hits"}
    assert FloorAssembly.model_validate(stored) == entry
    with pytest.raises(ValueError, match="Extra inputs"):
        FloorAssembly.model_validate(stored | {"rac": 1})


@pytest.mark.parametrize(
    ("eave", "level", "expected"),
    [
        (None, None, 3200),  # tầng chưa có cốt: diềm ở đỉnh tường
        (_measured(3300, "plan"), LevelSpec(level_ref="V1", elevation_mm=_measured(0), height_mm=None), 3200),
        (_measured(6900, "reviewer"), LevelSpec(level_ref="V1", elevation_mm=_measured(3600), height_mm=None), 3300),
        (_measured(6900, "reviewer"), LevelSpec(level_ref="V1", elevation_mm=None, height_mm=None), 3200),
        (_measured(6900, "reviewer"), None, 3200),
        # Merge-review PR 121 (P1): người duyệt chỉ nhập CHIỀU CAO tầng thì diềm thừa kế nguồn `reviewer` của chiều
        # cao (`building._wall_top`) nhưng vẫn là diềm suy ra: mái đi theo đỉnh tường hiện tại, không ghim ở số đã nhập.
        (
            _measured(6900, "reviewer"),
            LevelSpec(level_ref="V1", elevation_mm=_measured(3600), height_mm=_measured(3300, "reviewer")),
            3200,
        ),
        # Người duyệt nhập cả chiều cao lẫn một cốt diềm khác đỉnh tường: giữ đúng cốt diềm họ nhập.
        (
            _measured(7100, "reviewer"),
            LevelSpec(level_ref="V1", elevation_mm=_measured(3600), height_mm=_measured(3300, "reviewer")),
            3500,
        ),
    ],
)
def test_eave_follows_the_current_wall_top_unless_the_reviewer_typed_it(
    eave: Measured | None, level: LevelSpec | None, expected: int
) -> None:
    """Diềm máy suy ra = chiều cao hiện tại của tầng; chỉ số người duyệt nhập mới tính từ cốt tầng đã duyệt."""
    assert eave_above_floor_mm(_spec(eave), level, 3200) == expected


def test_settle_adds_the_roof_to_a_bare_floor() -> None:
    """Tầng chưa có mái nào: thêm mái máy dựng, ghi dấu vân và id."""
    built = _roof()
    settled = settle((), OWN, built, None)
    assert (settled.roofs, settled.code, settled.roof_ids, settled.user_removed) == ((built,), "BUILT", (OWN,), False)
    assert settled.fingerprint == fingerprint(built.nodes)


def test_settle_replaces_its_own_untouched_roof_in_place() -> None:
    """Tường đổi (M-16): mái máy dựng chưa ai chạm được thay tại chỗ, không thêm mái thứ hai."""
    old, new = _roof(width=8.0), _roof(width=9.5)
    settled = settle((old,), OWN, new, _previous(old))
    assert (settled.roofs, settled.code, settled.fingerprint) == ((new,), "BUILT", fingerprint(new.nodes))


def test_settle_is_a_no_op_when_nothing_changed() -> None:
    """Chạy lại với cùng tường: danh sách mái y nguyên (lượt ghi sẽ không tăng `revision`)."""
    roof = _roof()
    assert settle((roof,), OWN, roof, _previous(roof)).roofs == (roof,)


@pytest.mark.parametrize("refusal", ["NO_WALLS", "ROOF_SHAPE_COMPLEX"])
def test_settle_drops_its_untouched_roof_when_the_floor_can_no_longer_carry_one(refusal: Any) -> None:
    """Không dựng được nữa: gỡ mái máy dựng chưa sửa (đã lệch tường), và không để lại id cho lượt sau hiểu nhầm."""
    old = _roof()
    settled = settle((old,), OWN, refusal, _previous(old))
    assert (settled.roofs, settled.code, settled.roof_ids, settled.fingerprint) == ((), refusal, (), None)
    assert settle((), OWN, old, _previous(code=refusal)).code == "BUILT"  # tường dựng lại được thì mái quay lại


@pytest.mark.parametrize("marks", [{"source": "human", "reviewed": True}, {"reviewed": True}, {"edited": True}])
def test_m11_never_touches_a_roof_of_its_own_that_a_person_approved_or_edited(marks: dict[str, Any]) -> None:
    """M-11: mái máy dựng đã duyệt hoặc đã sửa giữ nguyên; tường chưa đổi → `BUILT`, đổi → chỉ ghi `ROOF_STALE`."""
    built = _roof()
    touched = _roof(**marks)
    same = settle((touched,), OWN, built, _previous(built))
    stale = settle((touched,), OWN, _roof(width=9.5), _previous(built))
    assert (same.roofs, same.code) == ((touched,), "BUILT")
    assert (stale.roofs, stale.code, stale.roof_ids) == ((touched,), "ROOF_STALE", (OWN,))
    assert stale.fingerprint == fingerprint(built.nodes)  # dấu vân lúc dựng được giữ để lượt sau vẫn thấy lệch
    assert settle((touched,), OWN, "NO_WALLS", _previous(built)).code == "ROOF_STALE"
    assert settle((touched,), OWN, built, None).code == "ROOF_STALE"  # mái đã sửa của bộ cũ: không rõ lúc dựng


def test_m11_writes_nothing_on_a_floor_with_a_hand_drawn_roof() -> None:
    """M-11: tầng có mái người vẽ thì máy không thêm mái của mình (`ROOF_KEPT`), kể cả mái AI cũ nằm cạnh."""
    drawn = _roof(HUMAN_ID, source="human", reviewed=False)
    kept = settle((drawn,), OWN, _roof(), None)
    assert (kept.roofs, kept.code, kept.roof_ids, kept.fingerprint) == ((drawn,), "ROOF_KEPT", (), None)
    old = _roof()
    beside = settle((old, drawn), OWN, _roof(width=9.5), _previous(old))
    assert (beside.roofs, beside.code) == ((old, drawn), "ROOF_STALE")


def test_m11_never_recreates_a_roof_the_user_removed() -> None:
    """M-11: id từng ghi mà vắng khỏi lớp = người đã xoá. Cờ dính: tường đổi hay người vẽ mái khác vẫn không tạo lại."""
    built = _roof()
    removed = settle((), OWN, built, _previous(built))
    assert (removed.roofs, removed.code, removed.user_removed, removed.roof_ids) == ((), "USER_REMOVED", True, ())
    sticky = _previous(code="USER_REMOVED", user_removed=True)
    drawn = _roof(HUMAN_ID, source="human", reviewed=True)
    for roofs in ((), (drawn,)):
        again = settle(roofs, OWN, _roof(width=9.5), sticky)
        assert (again.roofs, again.code, again.user_removed) == (roofs, "USER_REMOVED", True)
