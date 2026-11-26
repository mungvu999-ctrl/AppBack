"""Từ khoá đi kèm cốt trên mặt cắt/mặt đứng (NHA-01): cốt đó là sàn, diềm, đỉnh mái hay parapet."""

import pytest

from packages.domain.drawing_set.level_words import level_role


@pytest.mark.parametrize(
    ("text", "role"),
    [
        ("FFL", "floor"),
        ("F.F.L. +3.600", "floor"),
        ("FIN. FLR", "floor"),
        ("FIN FLR.", "floor"),
        ("FINISHED FLOOR", "floor"),
        ("2ND FIN. FLOOR", "floor"),
        ("CỐT SÀN", "floor"),
        ("cốt sàn tầng 2", "floor"),
        ("T.O. PLATE", "plate"),
        ("T.O.PLATE", "plate"),
        ("T/O PLATE", "plate"),
        ("TOP OF PLATE", "plate"),
        ("T.O. ROOF", "roof_top"),
        ("TOP OF ROOF", "roof_top"),
        ("RIDGE", "ridge"),
        ("ROOF RIDGE EL. 24'-6\"", "ridge"),
        ("ĐỈNH MÁI", "ridge"),
        ("T.O. PARAPET", "parapet"),
        ("TOP OF PARAPET WALL", "parapet"),
        ("SÀN MÁI", "roof_slab"),
        ("CỐT SÀN MÁI", "roof_slab"),  # "sàn mái" thắng "cốt sàn"
        ("SÊ NÔ", "gutter"),
        ("ĐÁY SÊNÔ", "gutter"),
    ],
)
def test_level_role_reads_keywords(text: str, role: str) -> None:
    """Mỗi từ khoá của thiết kế §2.D ra đúng một vai, không phân biệt hoa thường và dấu."""
    assert level_role(text) == role


@pytest.mark.parametrize(
    "text",
    [
        "",
        "+3.600",
        "BEDROOM",
        "ROOF",
        "PLATE",
        "PARAPET",
        "MÁI NGÓI",
        "SÀN GỖ",
        "BRIDGE",
        "CARTRIDGE",
        "SEN ONG",
        "TOROOF",
        "ACCESS TO ROOF",
    ],
)
def test_level_role_ignores_other_text(text: str) -> None:
    """Chữ không mang từ khoá cốt (tên cấu kiện trần, từ chứa "RIDGE", ghi chú "TO ROOF"): không có vai."""
    assert level_role(text) is None
