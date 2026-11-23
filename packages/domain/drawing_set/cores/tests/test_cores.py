"""Lõi thang xuyên tầng (K-32e): ca `--selfcheck` của `kernel/cores.py`, từng luật, ca biên và lỗi."""

from collections.abc import Callable

import pytest

from packages.domain.drawing_set.cores import (
    CorePlan,
    PlanText,
    StackShift,
    Well,
    stair_cores,
    stair_tracks,
)
from packages.domain.drawing_set.cores.extend import WALL_VETO
from packages.domain.drawing_set.cores.link import CONFIRM_CAP, CURVED_CAP, EVID_CAP
from packages.domain.drawing_set.cores.types import Box

GOOD = StackShift((-1000, 0), 0.93)  # tầng trên vẽ lệch phải 1 m so với tầng dưới
ZERO = StackShift((0, 0), 0.93)
WELL: Box = (1000, 1000, 2000, 2100)
EVID = frozenset({"stair_word"})


def plan(
    floor: int, *wells: Well | Box, texts: tuple[PlanText, ...] = (), wall_cover: Callable[[Box], float] | None = None
) -> CorePlan:
    """Mặt bằng tầng `floor`; hộp trần được bọc thành ô có chữ thang (bằng chứng đủ cho ≥ 0,9)."""
    ws = tuple(w if isinstance(w, Well) else Well(w, evidence=EVID) for w in wells)
    return CorePlan(floor, f"X__f{floor:02d}_plan", ws, texts, wall_cover)


def labels(*items: tuple[str, float, float]) -> tuple[PlanText, ...]:
    """Các dòng chữ (nội dung, x mm, y mm)."""
    return tuple(PlanText(t, (x, y)) for t, x, y in items)


# ---- ca selfcheck của nhân -------------------------------------------------------------------------


def _selfcheck_plans() -> list[CorePlan]:
    """Tầng 2 lệch 1 m; bàn ở tầng 1; tầng 3 sót thang; tầng 4 có lại."""
    return [
        plan(1, (1000, 1000, 2000, 2100), (6000, 6000, 7000, 6500)),
        plan(2, (2000, 1000, 3000, 2100)),
        plan(3),
        plan(4, (2000, 1000, 3000, 2100)),
    ]


def test_two_storeys_core_and_table_unlinked() -> None:
    cores, unlinked = stair_tracks(_selfcheck_plans()[:2], [GOOD])
    assert len(cores) == 1
    core = cores[0]
    assert core.confidence == 0.92
    assert core.centre_mm == (1500, 1550)
    assert core.floors == (1, 2)
    assert core.members == (("X__f01_plan", 0), ("X__f02_plan", 0))
    assert len(unlinked) == 1
    assert unlinked[0].confidence == 0.3
    assert unlinked[0].box_mm == (6000, 6000, 7000, 6500)


def test_gap_core_bridged_over_missing_storey() -> None:
    (core,) = stair_cores(_selfcheck_plans(), [GOOD, ZERO, ZERO])
    assert core.floors == (1, 2, 3, 4)
    assert core.confidence == 0.8
    assert "gap" in core.sources
    assert core.assumed_floors == (3,)
    assert core.observed_floors == (1, 2, 4)
    assert dict(core.floor_confidence) == {1: 0.8, 2: 0.8, 3: 0.65, 4: 0.8}


def test_weak_shift_gives_one_source_confidence() -> None:
    (core,) = stair_cores(_selfcheck_plans()[:2], [StackShift((-1000, 0), 0.6)])
    assert core.confidence == 0.8
    assert "weak_shift" in core.sources
    assert core.shift_conf == 0.6


def test_shift_too_large_or_missing_links_nothing() -> None:
    assert stair_cores(_selfcheck_plans()[:2], [StackShift((3000, 3000), 0.95)]) == []
    assert stair_cores(_selfcheck_plans()[:2], [StackShift(None, 0)]) == []
    assert stair_cores(_selfcheck_plans()[:2], [None]) == []


def test_three_storeys_confidence_095() -> None:
    q = [plan(f, WELL) for f in (1, 2, 3)]
    assert stair_cores(q, [ZERO, ZERO])[0].confidence == 0.95


def test_core_on_two_three_gets_ground_below_and_top_above() -> None:
    r = [plan(1), plan(2, WELL), plan(3, WELL), plan(4)]
    (core,) = stair_cores(r, [ZERO, ZERO, ZERO])
    assert core.floors == (1, 2, 3, 4)
    assert core.assumed_floors == (1, 4)
    assert core.confidence == 0.92
    assert core.observed_floors == (2, 3)
    # lỗi review: tầng giả định không bao giờ mang độ chắc của lõi
    assert dict(core.floor_confidence) == {1: 0.6, 2: 0.92, 3: 0.92, 4: 0.5}
    assert "assumed" in core.sources


def test_no_upward_extension_when_next_plan_is_not_next_storey() -> None:
    r2 = [plan(1), plan(2, WELL), plan(3, WELL), plan(5)]
    assert stair_cores(r2, [ZERO, ZERO, ZERO])[0].floors == (1, 2, 3)


def test_room_name_over_projected_well_stops_upward_extension() -> None:
    r = [plan(1), plan(2, WELL), plan(3, WELL), plan(4, texts=labels(("PHÒNG NGỦ", 1500, 1500)))]
    assert stair_cores(r, [ZERO, ZERO, ZERO])[0].floors == (1, 2, 3)


def test_different_tread_direction_drops_below_09() -> None:
    s = [
        plan(1, Well(WELL, angle_deg=0.0, pitch_mm=250, evidence=EVID)),
        plan(2, Well(WELL, angle_deg=90.0, pitch_mm=250, evidence=EVID)),
    ]
    (core,) = stair_cores(s, [ZERO])
    assert core.confidence == 0.8
    assert not core.shape_ok
    assert "shape_differs" in core.sources


def test_similar_shape_tolerates_angle_mod_180_and_pitch() -> None:
    s = [
        plan(1, Well(WELL, angle_deg=2.0, pitch_mm=250, evidence=EVID)),
        plan(2, Well(WELL, angle_deg=178.0, pitch_mm=270, evidence=EVID)),
    ]
    (core,) = stair_cores(s, [ZERO])
    assert core.shape_ok
    assert core.confidence == 0.92


def test_unknown_angle_counts_as_similar() -> None:
    s = [
        plan(1, Well(WELL, angle_deg=0.0, evidence=EVID)),
        plan(2, Well(WELL, angle_deg=90.0, pitch_mm=250, evidence=EVID)),
    ]
    assert stair_cores(s, [ZERO])[0].shape_ok


def test_without_own_evidence_confidence_capped() -> None:
    e = [plan(2, Well(WELL)), plan(3, Well(WELL))]
    (core,) = stair_cores(e, [ZERO])
    assert core.confidence == EVID_CAP
    assert "no_stair_evidence" in core.sources


def test_evidence_on_one_storey_only() -> None:
    e = [plan(2, Well(WELL, evidence=frozenset({"tread_numbers"}))), plan(3, Well(WELL))]
    (core,) = stair_cores(e, [ZERO])
    assert core.confidence == EVID_CAP
    assert "tread_numbers" in core.sources
    assert dict(core.floor_confidence) == {2: 0.85, 3: 0.75}  # tầng 3 không có chứng cứ riêng


def test_evidence_on_both_storeys_reaches_092() -> None:
    e = [plan(2, Well(WELL, evidence=EVID)), plan(3, Well(WELL, evidence=EVID))]
    assert dict(stair_cores(e, [ZERO])[0].floor_confidence) == {2: 0.92, 3: 0.92}


def test_ground_storey_assumed_unless_room_under_well() -> None:
    top = [plan(2, WELL), plan(3, WELL)]
    assert stair_cores([plan(1), *top], [ZERO, ZERO])[0].floors == (1, 2, 3)
    room = plan(1, texts=labels(("PHÒNG KHÁCH", 1500, 1500)))
    assert stair_cores([room, *top], [ZERO, ZERO])[0].floors == (2, 3)


def test_short_room_names_under_stair_do_not_veto() -> None:
    """WC/KHO là phòng hay nằm dưới thang: không chặn mở rộng xuống."""
    wc = plan(1, texts=labels(("WC", 1500, 1500), ("KHO", 1500, 1700)))
    assert stair_cores([wc, plan(2, WELL), plan(3, WELL)], [ZERO, ZERO])[0].floors == (1, 2, 3)


def test_wall_cover_veto_stops_downward_extension() -> None:
    top = [plan(2, WELL), plan(3, WELL)]
    walled = plan(1, wall_cover=lambda _box: WALL_VETO + 0.01)
    open_ = plan(1, wall_cover=lambda _box: WALL_VETO)
    assert stair_cores([walled, *top], [ZERO, ZERO])[0].floors == (2, 3)
    assert stair_cores([open_, *top], [ZERO, ZERO])[0].floors == (1, 2, 3)


def test_extension_stops_without_shift() -> None:
    r = [plan(1), plan(2, WELL), plan(3, WELL), plan(4)]
    assert stair_cores(r, [None, ZERO, None])[0].floors == (2, 3)
    assert stair_cores(r, [StackShift(None, 0), ZERO, StackShift(None, 0)])[0].floors == (2, 3)


def test_downward_extension_moves_centre_and_boxes() -> None:
    r = [plan(1), plan(2, WELL), plan(3, WELL)]
    (core,) = stair_cores(r, [StackShift((-1000, 0), 0.93), ZERO])
    assert core.assumed_floors == (1,)
    assert core.assumed_boxes == (("X__f01_plan", (0, 1000, 1000, 2100)),)
    assert core.centre_mm == (500, 1550)


def test_curved_only_core_capped_and_mixed_not() -> None:
    cu = [plan(f, Well(WELL, kind="curved", evidence=EVID)) for f in (1, 2, 3)]
    assert stair_cores(cu, [ZERO, ZERO])[0].confidence == CURVED_CAP
    mix = [cu[0], plan(2, WELL), plan(3, WELL)]
    (core,) = stair_cores(mix, [ZERO, ZERO])
    assert core.confidence == 0.95
    assert "curved" in core.sources


def test_confirm_detector_support_replaces_text_evidence() -> None:
    def y(floor: int, ok: bool) -> CorePlan:
        return plan(floor, Well(WELL, yolo_ok=ok))

    (core,) = stair_cores([y(1, True), y(2, True)], [ZERO])
    assert core.confidence == 0.92
    assert "yolo_confirm" in core.sources
    assert stair_cores([y(1, True), y(2, False)], [ZERO])[0].confidence == EVID_CAP
    (dis,) = stair_cores([y(1, False), y(2, False)], [ZERO])
    assert dis.confidence == CONFIRM_CAP
    assert "yolo_disagrees" in dis.sources


def test_confirm_does_not_cap_when_tread_numbers_present() -> None:
    nums = frozenset({"tread_numbers"})
    e = [plan(1, Well(WELL, evidence=nums, yolo_ok=False)), plan(2, Well(WELL, evidence=nums, yolo_ok=False))]
    assert stair_cores(e, [ZERO])[0].confidence == 0.92


def test_one_to_one_linking_best_overlap_first() -> None:
    """Hai ô gần nhau trên tầng 1, một ô trên tầng 2: chỉ ô trùng nhiều nhất nối, ô còn lại lẻ."""
    p = [plan(1, (1000, 1000, 2000, 2100), (1200, 1000, 2200, 2100)), plan(2, (1200, 1000, 2200, 2100))]
    cores, unlinked = stair_tracks(p, [ZERO])
    assert cores[0].members == (("X__f01_plan", 1), ("X__f02_plan", 0))
    assert [u.well_index for u in unlinked] == [0]


def test_link_by_centre_distance_when_overlap_small() -> None:
    """Hộp chỉ chạm cạnh (không giao) nhưng tâm cách đúng một bề rộng thang vẫn nối."""
    p = [plan(1, (1000, 1000, 2000, 2100)), plan(2, (2000, 1000, 3000, 2100))]
    assert len(stair_cores(p, [ZERO])) == 1


# ---- biên và lỗi -----------------------------------------------------------------------------------


def test_empty_and_single_plan() -> None:
    assert stair_tracks([], []) == ([], [])
    cores, unlinked = stair_tracks([plan(1, WELL)], [])
    assert cores == []
    assert len(unlinked) == 1


def test_plans_without_wells() -> None:
    assert stair_tracks([plan(1), plan(2)], [ZERO]) == ([], [])


@pytest.mark.parametrize(
    ("plans", "shifts"),
    [
        ([plan(1), plan(2)], []),  # thiếu dịch
        ([plan(1), plan(2)], [ZERO, ZERO]),  # thừa dịch
        ([plan(2), plan(1)], [ZERO]),  # không tăng dần
        ([plan(1), plan(1)], [ZERO]),  # trùng tầng
        ([], [ZERO]),
    ],
)
def test_bad_input_raises(plans: list[CorePlan], shifts: list[StackShift | None]) -> None:
    with pytest.raises(ValueError, match=r"dịch|tăng dần"):
        stair_tracks(plans, shifts)


def test_negative_floor_numbers_basement() -> None:
    """Tầng hầm số âm / 0 vẫn tăng dần và nối được."""
    p = [plan(-1, WELL), plan(0, WELL), plan(1, WELL)]
    (core,) = stair_cores(p, [ZERO, ZERO])
    assert core.floors == (-1, 0, 1)
    assert core.confidence == 0.95
