"""Kiểm chuỗi cốt sàn — các ca của `levels_chain.selfcheck()` trong nhân nghiên cứu."""

import pytest

from packages.domain.drawing_set.chain import solve_chain


def test_yard_and_side_only_values_do_not_become_levels() -> None:
    # sân -0,45 trên mặt bằng trệt, bậu 0,9 / lanh tô 2,7 chỉ trên một mặt cắt, mái 10,5, tường chắn 11,4
    sides = [[0.0, 0.9, 2.7, 3.6, 7.2, 10.5, 11.4], [0.0, 3.6, 7.2, 10.5], [-0.45, 0.0, 3.6, 7.2]]
    ch = solve_chain([[-0.45] * 3 + [0.0] * 2, [3.6, 3.6], [7.2], []], sides)
    assert [c.level_m for c in ch][:3] == [0.0, 3.6, 7.2]
    assert ch[1].confidence >= 0.9
    assert ch[0].plans == (0,)
    assert ch[2].plans == (2,)


def test_plans_alone_give_one_source_confidence() -> None:
    ch = solve_chain([[0.0, 0.0], [3.3], [6.6, 6.6]], [])
    assert [c.level_m for c in ch] == [0.0, 3.3, 6.6]
    assert all(0.7 <= c.confidence < 0.9 for c in ch)
    assert all(c.sources == ("plan",) for c in ch)


def test_mezzanine_between_storeys() -> None:
    ch = solve_chain([[0.0], [2.0], [3.8]], [[0.0, 2.0, 3.8]] * 2)
    assert [c.level_m for c in ch] == [0.0, 2.0, 3.8]
    assert ch[1].mezzanine
    assert not ch[0].mezzanine


def test_empty_input() -> None:
    assert solve_chain([], []) == ()


def test_oversized_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="quá nhiều"):
        solve_chain([[0.0]] * 201, [])
    with pytest.raises(ValueError, match="cốt"):
        solve_chain([[0.0] * 1001], [])
