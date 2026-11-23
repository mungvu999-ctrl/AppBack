"""Bằng chứng chữ, phủ quyết tên phòng, lọc phát hiện, đánh dấu mô hình, gợi ý ô trống (K-32e)."""

import pytest

from packages.domain.drawing_set.cores import (
    CorePlan,
    Detection,
    PlanText,
    YoloBox,
    build_wells,
    combine,
    evidence_near,
    has_room_label,
    is_dropped,
    void_texts,
    voids,
)

BOX = (1000.0, 1000.0, 2000.0, 2100.0)


def t(text: str, x: float, y: float) -> PlanText:
    """Chữ có tâm (x, y) mm."""
    return PlanText(text, (x, y))


# ---- evidence_near ---------------------------------------------------------------------------------


def test_stair_word_within_pad_only() -> None:
    far = [t("LÊN", 3000, 3000)]  # xa giếng hơn 0,6 m
    assert evidence_near(far, BOX) == frozenset()
    near = [*far, t("UP", 2500, 1500)]
    assert evidence_near(near, BOX) == {"stair_word"}


@pytest.mark.parametrize("word", ["XUỐNG", "Lên", "THANG", "DN", "down", "stair"])
def test_each_stair_word(word: str) -> None:
    assert evidence_near([t(word, 1500, 1500)], BOX) == {"stair_word"}


def test_tread_numbers_every_other_step() -> None:
    nums = [t(str(n), 1500, 1000 + 40 * n) for n in (15, 17, 19)]
    assert evidence_near(nums, BOX) == {"tread_numbers"}
    assert evidence_near(nums[:2], BOX) == frozenset()  # chỉ hai số: chưa đủ


def test_tread_numbers_ignore_out_of_range_and_non_ascii_digits() -> None:
    big = [t(str(n), 1500, 1500) for n in (41, 42, 43, 0, 1)]
    assert evidence_near(big, BOX) == frozenset()
    assert evidence_near([t("²", 1500, 1500)], BOX) == frozenset()  # isdigit() đúng nhưng không phải số bậc


def test_legacy_font_text_is_decoded() -> None:
    """Chữ phông TCVN3 sau OCR (LEN viết bằng phông cũ) vẫn nhận ra qua `titles.fold`."""
    assert evidence_near([t("Lªn", 1500, 1500)], BOX) == {"stair_word"}


# ---- has_room_label --------------------------------------------------------------------------------


def test_room_label_needs_letters_and_position() -> None:
    inside = [t("PHÒNG NGỦ", 1500, 1500)]
    assert has_room_label(inside, BOX)
    assert not has_room_label([t("PHÒNG NGỦ", 5000, 5000)], BOX)
    assert not has_room_label([t("12", 1500, 1500)], BOX)  # số trần
    assert not has_room_label([t("LÊN 12", 1500, 1500)], BOX)  # chữ thang
    assert not has_room_label([t("WC", 1500, 1500)], BOX)  # < 3 ký tự
    assert not has_room_label([t("WC 1", 1500, 1500)], BOX)  # phòng dưới thang
    assert not has_room_label([t("Kho chứa", 1500, 1500)], BOX)


def test_room_label_letter_threshold() -> None:
    two = [t("BẾP 1", 1500, 1500)]
    assert has_room_label(two, BOX, 2)
    assert has_room_label(two, BOX, 3)
    assert not has_room_label([t("AB1", 1500, 1500)], BOX, 3)


# ---- combine / is_dropped --------------------------------------------------------------------------


def det(x: float, reason: str = "ok") -> Detection:
    """Phát hiện 8 bậc ở x, rộng 100."""
    return Detection((x, 0, x + 100, 100), 8, reason=reason)


@pytest.mark.parametrize(
    ("reason", "dropped"),
    [
        ("ok", False),
        ("", False),
        ("hatch: pitch 120 mm", True),
        ("continues: 3+3 tread lines; grid: 6 perpendicular lines", True),
        ("continues: 3+3 tread lines at pitch beyond both ends; wide: tread 2000 mm", False),
        ("table: ruled", True),
    ],
)
def test_is_dropped(reason: str, dropped: bool) -> None:
    assert is_dropped(det(0, reason=reason)) is dropped


def test_combine_filters_and_merges_curved() -> None:
    straight = [
        det(0),
        det(300, reason="hatch: pitch 120 mm"),
        det(600, reason="continues: 3+3; grid: 6 lines"),
        det(900, reason="continues: x; wide: y"),
        Detection((1200, 0, 1300, 100), 4),  # < 5 bậc
    ]
    curved = [Detection((50, 50, 150, 150), 9), Detection((600, 0, 700, 100), 7), Detection((2000, 0, 2100, 100), 4)]
    kept = combine(straight, curved)
    assert [d.box[0] for d in kept] == [0, 900, 600]  # ô lưới bị bỏ nên quạt cong 600 không đè ô nào
    assert [d.kind for d in kept] == ["stair", "stair", "curved"]
    assert kept[2].angle_deg is None
    assert kept[2].pitch_mm is None


def test_combine_building_box_filters_by_centre() -> None:
    straight = [det(0), det(900), det(5000)]
    assert [d.box[0] for d in combine(straight, [], (-10, -10, 950, 200))] == [0, 900]


# ---- build_wells -----------------------------------------------------------------------------------


def test_build_wells_marks_yolo_support_without_dropping() -> None:
    dets = [Detection((0, 0, 1000, 1000), 8), Detection((5000, 0, 6000, 1000), 8), Detection((9000, 0, 10000, 1000), 8)]
    yolo = [
        YoloBox((-100, -100, 1100, 1100), 0.8),
        YoloBox((4900, 0, 6000, 1000), 0.29),
        YoloBox((9000, 0, 10000, 1000), 0.3),
    ]
    wells = build_wells(dets, (), yolo)
    assert [w.yolo_ok for w in wells] == [True, False, True]  # 0,29 chưa tới ngưỡng, 0,3 đạt


def test_build_wells_without_yolo_layer_and_with_evidence() -> None:
    wells = build_wells([Detection(BOX, 8, angle_deg=88.0, pitch_mm=252.0)], [t("LÊN", 1500, 1500)])
    assert wells[0].yolo_ok is None
    assert wells[0].evidence == {"stair_word"}
    assert wells[0].angle_deg == 88.0
    assert wells[0].pitch_mm == 252.0
    assert wells[0].kind == "stair"


def test_build_wells_curved_kind() -> None:
    (well,) = build_wells(combine([], [Detection(BOX, 9)]))
    assert well.kind == "curved"


# ---- voids -----------------------------------------------------------------------------------------


def test_void_texts_folded() -> None:
    texts = [
        t("Ô THÔNG TẦNG", 0, 0),
        t("giếng trời", 0, 0),
        t("PHÒNG KHÁCH", 0, 0),
        t("VOID", 0, 0),
        t("Ô TRỐNG", 0, 0),
    ]
    assert [x.text for x in void_texts(texts)] == ["Ô THÔNG TẦNG", "giếng trời", "VOID", "Ô TRỐNG"]


def test_voids_text_only_and_with_cell() -> None:
    texts = (t("GIẾNG TRỜI", 1500.7, 2500.2), t("PHÒNG KHÁCH", 0, 0))
    only_text = CorePlan(2, "v2", texts=texts)
    (hint,) = voids([only_text])
    assert (hint.confidence, hint.source, hint.centre_mm, hint.box_mm) == (0.6, "text", (1500, 2500), None)
    cell = CorePlan(3, "v3", texts=texts, cell_of=lambda p: (p[0] - 500, p[1] - 500, p[0] + 500, p[1] + 500))
    (hint,) = voids([cell])
    assert (hint.floor_no, hint.confidence, hint.source, hint.box_mm) == (
        3,
        0.75,
        "text+cell",
        (1000, 2000, 2000, 3000),
    )
    assert hint.centre_mm is None


def test_voids_cell_none_falls_back_and_empty() -> None:
    plan = CorePlan(1, "v1", texts=(t("VOID", 10, 20),), cell_of=lambda _p: None)
    assert voids([plan])[0].source == "text"
    assert voids([]) == []
    assert voids([CorePlan(1, "v1")]) == []
