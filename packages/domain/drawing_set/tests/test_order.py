"""Số tầng theo thứ tự dự án (`order.follow_order`): neo, điền, mâu thuẫn và độ tin."""

from packages.domain.drawing_set.order import OrderConflict, Slot, follow_order
from packages.domain.drawing_set.storeys import StoreyChoice, StoreyLabel


def _c(label: StoreyLabel | None, number: int | None, confidence: float = 0.85) -> StoreyChoice:
    return StoreyChoice(label, number, confidence, ("text",) if label else (), None, ())


def _numbers(*choices: StoreyChoice | None) -> tuple[list[int | None], dict[str, OrderConflict]]:
    slots = [Slot(f"v{i}", c) for i, c in enumerate(choices)]
    out, conflicts = follow_order(slots)
    return [out[s.view_id].number if s.view_id in out else None for s in slots], conflicts


def test_skipped_number_is_kept_and_gap_is_loose() -> None:
    numbers, conflicts = _numbers(_c("ground", 1), _c("floor", 2), _c("floor", 4))
    assert (numbers, conflicts) == ([1, 2, 4], {})
    out, _ = follow_order([Slot("a", _c("ground", 1)), Slot("b", _c(None, None, 0.0)), Slot("c", _c("floor", 4))])
    assert (out["b"].number, out["b"].confidence) == (2, 0.6)  # tầng 2 hay 3 đều hợp


def test_confidence_by_evidence() -> None:
    slots = [Slot("a", _c("ground", 1)), Slot("b", _c(None, None, 0.0)), Slot("c", _c("floor", 3))]
    out, _ = follow_order([*slots, Slot("d", _c(None, None, 0.0))])
    assert [out[k].confidence for k in "abcd"] == [
        0.95,
        0.8,
        0.95,
        0.75,
    ]  # hai neo xác nhận nhau; kẹp giữa; đếm một phía
    out, _ = follow_order([Slot("a", _c("ground", 1)), Slot("b", _c("floor", 2))])
    assert [out[k].confidence for k in "ab"] == [0.95, 0.95]  # hai tên kề nhau xác nhận nhau qua thứ tự
    out, _ = follow_order([Slot("a", _c(None, None, 0.0)), Slot("b", _c(None, None, 0.0))])
    assert [(out[k].number, out[k].confidence) for k in "ab"] == [(1, 0.6), (2, 0.6)]  # không neo


def test_basement_and_roof_follow_order() -> None:
    numbers, conflicts = _numbers(_c("basement", 0, 0.8), _c("ground", 1), _c("floor", 2), _c("roof_terrace", 3, 0.7))
    assert (numbers, conflicts) == ([0, 1, 2, 3], {})
    numbers, conflicts = _numbers(_c("roof_terrace", 3, 0.7), _c("ground", 1), _c("floor", 2))
    assert numbers == [0, 1, 2]
    assert conflicts == {"v0": OrderConflict("title")}  # sân thượng nằm dưới tầng thường
    numbers, conflicts = _numbers(_c("ground", 1), _c("basement", 0, 0.8))
    assert conflicts == {"v1": OrderConflict("title", 0)}  # hầm nằm trên trệt
    assert numbers == [1, 2]


def test_heaviest_consistent_numbers_win() -> None:
    numbers, conflicts = _numbers(_c("floor", 2), _c("ground", 1), _c("floor", 3))
    assert numbers == [0, 1, 3]
    assert conflicts == {"v0": OrderConflict("title", 2)}


def test_level_against_order() -> None:
    slots = [Slot("a", _c("ground", 1), 0), Slot("b", _c("floor", 2), 6600), Slot("c", _c("floor", 3), 6500)]
    assert follow_order(slots)[1] == {}  # lệch trong 0,25 m: bỏ qua
    slots[2] = Slot("c", _c("floor", 3), 3300)
    out, conflicts = follow_order(slots)
    assert conflicts == {"b": OrderConflict("level"), "c": OrderConflict("level")}
    assert [out[k].confidence for k in "abc"] == [0.95, 0.65, 0.65]


def test_one_wrong_level_flags_only_itself() -> None:
    """Một cốt đọc sai ở sàn trên cùng không kéo các sàn đứng giữa vào câu hỏi."""
    slots = [
        Slot("a", _c("ground", 1), 0),
        Slot("b", _c("floor", 2), 3300),
        Slot("c", _c("floor", 3), 6600),
        Slot("d", _c("floor", 4), 600),
    ]
    out, conflicts = follow_order(slots)
    assert conflicts == {"d": OrderConflict("level")}
    assert [out[k].confidence for k in "abc"] == [0.95, 0.95, 0.95]
    slots[0] = Slot("a", _c("ground", 1), 9000)  # sàn đầu sai: chỉ sàn đầu bị hỏi
    assert follow_order(slots[:3])[1] == {"a": OrderConflict("level")}


def test_level_only_numbers_never_reach_both_sources() -> None:
    """Không đọc được tên hình: số suy từ cốt có thể cùng lệch một tầng mà vẫn xác nhận nhau, nên tối đa 0,8."""
    slots = [
        Slot(key, StoreyChoice("floor", n, 0.85, ("level",), None, ())) for key, n in zip("abc", (1, 2, 3), strict=True)
    ]
    out, conflicts = follow_order(slots)
    assert conflicts == {}
    assert [out[k].confidence for k in "abc"] == [0.8, 0.8, 0.8]


def _house(*choices: StoreyChoice | None, size: int) -> tuple[dict[str, StoreyChoice], dict[str, OrderConflict]]:
    """`choices` ở các sàn đầu, phần còn lại tới `size` sàn là hình không đọc được tên (vẫn chiếm chỗ)."""
    rest = [_c(None, None, 0.0)] * (size - len(choices))
    return follow_order([Slot(f"v{i}", c) for i, c in enumerate([*choices, *rest])])


def test_misread_digit_above_ground_is_asked_not_kept() -> None:
    """K-30b: "TẦNG 8" ngay trên "TẦNG 1" ở nhà 5 sàn, không cốt phản bác -> không làm neo, hỏi, sàn sau đếm từ 1."""
    out, conflicts = _house(_c("ground", 1, 0.95), _c("floor", 8, 0.95), size=5)
    assert conflicts == {"v1": OrderConflict("title", 8)}
    assert [out[f"v{i}"].number for i in range(5)] == [1, 2, 3, 4, 5]
    assert out["v1"].confidence <= 0.65
    assert max(out[f"v{i}"].confidence for i in range(2, 5)) < 0.9


def test_plausible_numbers_keep_both_sources() -> None:
    """Số đúng chỗ (bỏ trống một sàn giữa) và nhà có lửng không bị gắn cờ."""
    out, conflicts = _house(_c("ground", 1), _c(None, None, 0.0), _c("floor", 3), size=5)
    assert conflicts == {}
    assert (out["v0"].confidence, out["v2"].confidence) == (0.95, 0.95)
    mezzanine = StoreyChoice("mezzanine", None, 0.85, ("text",), None, ())
    numbers, conflicts = _numbers(_c("ground", 1), mezzanine, _c("floor", 2), _c("floor", 3))
    assert (numbers, conflicts) == ([1, None, 2, 3], {})


def test_number_above_floor_count_is_asked() -> None:
    """Số lớn hơn số sàn + 1 không thể đúng, kể cả khi là neo duy nhất; số chỉ suy từ cốt hỏi theo cốt."""
    numbers, conflicts = _numbers(_c("floor", 9), _c(None, None, 0.0), _c(None, None, 0.0))
    assert (numbers, conflicts) == ([1, 2, 3], {"v0": OrderConflict("title", 9)})
    level_only = StoreyChoice("floor", 7, 0.85, ("level",), None, ())
    assert _numbers(_c("ground", 1), level_only)[1] == {"v1": OrderConflict("level", 7)}


def test_jump_drops_the_anchor_off_the_median() -> None:
    """Cặp neo nhảy bất thường: loại neo xa trung vị `n - s`; ngang nhau thì hỏi cả hai."""
    _, conflicts = _house(_c("ground", 1), _c("floor", 2), _c("floor", 3), _c("floor", 9), size=12)
    assert conflicts == {"v3": OrderConflict("title", 9)}
    out, conflicts = _house(_c("ground", 1), _c("floor", 8), _c("floor", 9), _c("floor", 10), size=12)
    assert conflicts == {"v0": OrderConflict("title", 1)}
    assert out["v0"].number == 7
    out, conflicts = _house(_c("ground", 1), _c("floor", 8), size=12)
    assert conflicts == {"v0": OrderConflict("title", 1), "v1": OrderConflict("title", 8)}
    assert [out[f"v{i}"].number for i in range(3)] == [1, 2, 3]
