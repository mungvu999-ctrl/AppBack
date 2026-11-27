"""Quyết định của người duyệt (NHA-06b): đề xuất tính kèm quyết định, và các lý do chặn xác nhận."""

from packages.domain.drawing_set.building import BuildingProposal, Measured, propose_building
from packages.domain.drawing_set.decisions import Decisions, LevelDecision, RoofDecision, blockers, decide, hit_key
from packages.domain.drawing_set.proposal import HouseView
from packages.domain.drawing_set.rules import RuleHit
from packages.domain.drawing_set.tests.test_proposal import _SECTION, _section, _stack, _view

_TWO = _stack("±0.000", "+3.300")  # hai tầng, không mặt cắt: chiều cao tầng trên cùng là mặc định 3,3 m
_SECTIONS = [_SECTION, _SECTION]
_ROOF_PLAN = [_view("r0", "MẶT BẰNG MÁI", "30°")]
_REVIEWED_F1 = ("f1",)


def _decided(decisions: Decisions | None, *, roofs: list[HouseView] | None = None) -> BuildingProposal:
    """Nhà hai tầng tính kèm `decisions`; `roofs` là các hình mặt bằng mái."""
    return decide(decisions, _TWO, roofs=roofs or [])


def _hits(proposal: BuildingProposal, code: str) -> list[tuple[tuple[str, ...], tuple[float | str, ...]]]:
    """Hình liên quan và số liệu của mọi vi phạm mang mã `code`."""
    return [(hit.floor_refs, hit.values) for hit in proposal.violations if hit.code == code]


def _reviewed(value: float | str) -> Measured:
    """Giá trị người duyệt nhập cho hình `f1`."""
    return Measured(value=value, confidence=1.0, source="reviewer", view_ids=_REVIEWED_F1)


def test_no_decisions_is_the_machine_proposal() -> None:
    """Không có quyết định (bộ cũ, chưa ai duyệt): đúng đề xuất của máy."""
    assert _decided(None) == _decided(Decisions()) == propose_building(_TWO)


def test_decisions_survive_the_json_round_trip() -> None:
    """Hình dạng lưu JSONB (`building.decisions`): khoá dây camelCase, đọc lại được đúng."""
    decisions = Decisions(
        levels=(LevelDecision(level_ref="f1", height_mm=3000),),
        roofs=(RoofDecision(level_ref="f1", form="hip", pitch_deg=35.0),),
        picks={"elevationMm:f1": "plan"},
        seen=("T-07|f1|heightMm|default",),
    )
    dumped = decisions.model_dump(mode="json", by_alias=True)
    assert dumped["levels"][0] == {"levelRef": "f1", "elevationMm": None, "heightMm": 3000}
    assert Decisions.model_validate(dumped) == decisions


def test_effective_levels_are_the_machine_values_until_the_reviewer_types() -> None:
    """`levels` là cốt và chiều cao đang hiệu lực của từng hình: của máy, hoặc số người duyệt nhập (`reviewer`)."""
    machine = _decided(None).levels
    assert [(level.level_ref, level.elevation_mm and level.elevation_mm.value) for level in machine] == [
        ("f0", 0),
        ("f1", 3300),
    ]
    assert machine[1].height_mm == Measured(value=3300, confidence=0.4, source="default", view_ids=("f1",))

    typed = _decided(Decisions(levels=(LevelDecision(level_ref="f1", height_mm=3000),)))
    assert typed.levels[1].height_mm == _reviewed(3000)
    assert (("f1",), ("heightMm", "default")) not in _hits(typed, "T-07")  # đã nhập thì hết "giá trị mặc định"
    assert typed.roofs[0].eave_elevation_mm == _reviewed(6300)  # diềm theo đỉnh tường mới, thừa kế nguồn chiều cao
    assert typed.floors == _decided(None).floors  # `floors` vẫn là giá trị máy đề xuất


def test_typed_level_settles_the_conflict_and_shows_the_gap_it_makes() -> None:
    """Mặt bằng ghi +3.900, mặt cắt 3,3 m: người duyệt nhập 3900 thì hết mâu thuẫn, nhưng T-01 báo hở 600 mm."""
    views = _stack("±0.000", "+3.900")
    assert decide(None, views, _SECTIONS).conflicts
    typed = decide(Decisions(levels=(LevelDecision(level_ref="f1", elevation_mm=3900),)), views, _SECTIONS)
    assert (typed.conflicts, _hits(typed, "T-05")) == ((), [])
    assert _hits(typed, "T-01") == [(("f0", "f1"), (600,))]


def test_picked_source_comes_from_the_decisions() -> None:
    """Nguồn đã chọn cho một mâu thuẫn nằm trong `picks`."""
    picked = decide(Decisions(picks={"elevationMm:f1": "plan"}), _stack("±0.000", "+3.900"), _SECTIONS)
    assert [(conflict.key, conflict.chosen_source) for conflict in picked.conflicts] == [("elevationMm:f1", "plan")]
    assert picked.levels[1].elevation_mm is not None
    assert picked.levels[1].elevation_mm.value == 3900


def test_roof_edits_replace_the_machine_guess_and_its_questions() -> None:
    """Người duyệt chọn dạng và độ dốc: trường đó mang nguồn `reviewer`, hết M-18 và T-07 của nó."""
    edit = RoofDecision(level_ref="f1", form="hip", pitch_deg=35.0, quarter_turns=1)
    proposal = _decided(Decisions(roofs=(edit,)), roofs=_ROOF_PLAN)
    (roof,) = proposal.roofs
    assert (roof.form, roof.pitch_deg, roof.quarter_turns) == (_reviewed("hip"), _reviewed(35.0), _reviewed(1))
    assert roof.overhang_mm.source == "convention"  # trường không sửa giữ giá trị máy
    roof_asks = [values for _, values in _hits(proposal, "T-07") if str(values[0]).startswith("roof")]
    assert (_hits(proposal, "M-18"), roof_asks) == ([], [])
    assert _hits(_decided(None, roofs=_ROOF_PLAN), "M-18")  # không sửa thì máy vẫn hỏi dạng


def test_typed_pitch_settles_the_pitch_conflict() -> None:
    """Hình mái 30°, mặt cắt 40° là mâu thuẫn; người duyệt nhập độ dốc thì hết mâu thuẫn và hết M-03."""
    sections = [_section(("40°", 200.0))]
    assert decide(None, _TWO, sections, roofs=_ROOF_PLAN).conflicts
    typed = decide(Decisions(roofs=(RoofDecision(level_ref="f1", pitch_deg=33.0),)), _TWO, sections, roofs=_ROOF_PLAN)
    assert (typed.conflicts, _hits(typed, "M-03")) == ((), [])


def test_inconsistent_roof_numbers_are_caught() -> None:
    """Số người duyệt nhập được kiểm: đỉnh không cao hơn diềm là lỗi M-17; diềm lệch đỉnh tường quá 100 mm là M-02."""
    low_ridge = RoofDecision(level_ref="f1", form="gable", pitch_deg=30.0, ridge_elevation_mm=6000)
    assert _hits(_decided(Decisions(roofs=(low_ridge,))), "M-17") == [(("f1",), ("ridgeBelowEave",))]
    high_eave = RoofDecision(level_ref="f1", eave_elevation_mm=7000)
    assert _hits(_decided(Decisions(roofs=(high_eave,))), "M-02") == [(("f1",), (7000, 6600))]
    assert not _hits(_decided(None), "M-02") + _hits(_decided(None), "M-17")


def test_skipped_roof_is_not_proposed_and_not_asked_about() -> None:
    """ "Không dựng mái, tôi tự vẽ": không có mái cho tầng đó, và không câu hỏi nào về mái kể cả khi có hình mái."""
    proposal = _decided(Decisions(roofs=(RoofDecision(level_ref="f1", skip=True),)), roofs=_ROOF_PLAN)
    assert proposal.roofs == ()
    assert [hit.code for hit in proposal.violations if hit.code.startswith("M-") or "roof" in str(hit.values)] == []


def test_hit_key_names_the_rule_the_views_and_the_numbers() -> None:
    """Khoá của một vi phạm đổi khi số liệu đổi: cảnh báo đã xem mà số khác đi thì phải xem lại."""
    (hit,) = [hit for hit in _decided(None).violations if hit.values == ("heightMm", "default")]
    assert hit_key(hit) == "T-07|f1|heightMm|default"


def test_long_hit_key_is_hashed_so_it_can_always_be_sent_back() -> None:
    """Luật kèm hàng chục hình (M-18 với mọi hình mái) cho khoá dài: thay bằng băm cố định, vẫn đổi khi số liệu đổi."""
    views = tuple(f"dwv_{index:026d}" for index in range(40))
    hit = RuleHit(code="M-18", level="warning", floor_refs=views, values=("default",))
    key = hit_key(hit)
    assert (key.startswith("M-18|sha256:"), len(key)) == (True, len("M-18|sha256:") + 64)
    assert hit_key(hit) == key
    assert hit_key(hit.model_copy(update={"values": ("convention",)})) != key


def test_unseen_warnings_block_until_seen_and_hints_never_do() -> None:
    """Cảnh báo chưa xem chặn xác nhận; gợi ý (M-07) không chặn; xem hết thì không còn gì chặn."""
    proposal = _decided(None)
    assert blockers(proposal, None) == ["warning:T-07|f1|heightMm|default", "warning:T-07|f1|roofForm|default"]
    seen = Decisions(seen=("T-07|f1|heightMm|default", "T-07|f1|roofForm|default"))
    assert blockers(_decided(seen), seen) == []


def test_open_conflicts_errors_and_stale_decisions_block() -> None:
    """Mâu thuẫn chưa chọn, vi phạm mức lỗi, và quyết định trỏ tới hình không còn: mỗi thứ một lý do chặn."""
    conflicted = decide(None, _stack("±0.000", "+3.900"), _SECTIONS)
    assert "conflict:elevationMm:f1" in blockers(conflicted, None)

    bad = Decisions(roofs=(RoofDecision(level_ref="f1", form="gable", pitch_deg=30.0, ridge_elevation_mm=6000),))
    assert "error:M-17|f1|ridgeBelowEave" in blockers(_decided(bad), bad)

    stale = Decisions(levels=(LevelDecision(level_ref="gone", height_mm=3000),), roofs=(RoofDecision(level_ref="zz"),))
    assert blockers(_decided(stale), stale)[:2] == ["decision:gone", "decision:zz"]
