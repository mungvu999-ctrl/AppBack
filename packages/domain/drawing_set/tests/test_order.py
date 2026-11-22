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
