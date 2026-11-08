"""Default constants for M1 export. Source of every value: HOP-DONG-M1 §1 / §1.1 / §3 (07/11/2026).

All lengths in metres unless the name ends in `_MM`.
"""

STRUCTURAL_SLAB_M = 0.15
"""Sàn kết cấu dày 150 mm (§1, dòng "Sàn kết cấu")."""

FINISH_FLOOR_M = 0.02
"""Sàn hoàn thiện dày 20 mm (§1, dòng "Phòng")."""

CEILING_MAX_M = 2.7
CEILING_BELOW_TOP_M = 0.25
"""Trần cao `min(2700, height - 250)` mm (§1, dòng "Phòng")."""


def ceiling_height_m(level_height_m: float) -> float:
    """Default ceiling height: min(2.7, H - 0.25) (§1)."""
    return min(CEILING_MAX_M, level_height_m - CEILING_BELOW_TOP_M)


MIN_WALL_LENGTH_MM = 20
"""Tường ngắn hơn 20 mm → skipped major (§1)."""
MIN_OPENING_MM = 50
"""Ô mở rộng/cao ≤ 50 mm → skipped major (§1)."""
MIN_ROOM_AREA_M2 = 0.2
"""Phòng < 0,2 m² → skipped major (§1)."""
WALL_JOIN_TOL_MM = 30
"""Đầu tường chạm tường khác khi khoảng cách ≤ t/2 + 30 mm (§4.1)."""
ARC_SEGMENTS_PER_180 = 24
"""Tường cong: 24 đoạn mỗi 180° (§4.1)."""
SLIVER_AREA_M2 = 1e-6
"""Mảnh nhỏ hơn bỏ (§4.2)."""

STAIR_MAX_RISER_MM = 175
"""Số bậc = ceil(rise / 175) (§1, dòng "Cầu thang")."""
STAIR_LEVEL_TOL_MM = 10
"""Tầng trên khớp khi elevation = elev + height ± 10 mm (§1)."""

# Bộ cửa đi bản lề (§1.1)
DOOR_LINING_M = 0.045
"""Khung lót 45 mm x dày tường."""
DOOR_TRIM_W_M = 0.07
DOOR_TRIM_T_M = 0.016
"""Nẹp 70 x 16 mm hai mặt."""
DOOR_LEAF_T_M = 0.04
"""Cánh dày 40 mm."""
DOOR_THRESHOLD_M = 0.02
"""Ngưỡng 20 mm."""
SLIDING_DOOR_PANELS = 2
"""Cửa lùa / cửa xếp: 2 tấm (§1.1)."""

# Cửa sổ (§1.1)
WINDOW_FRAME_W_M = 0.05
WINDOW_FRAME_D_M = 0.07
"""Khung 50 x 70 mm."""
GLASS_T_M = 0.008
"""Kính 8 mm."""
WINDOW_SILL_T_M = 0.03
"""Bậu đá 30 mm phía trong."""

NEUTRAL_COLORS = {
    "wall_interior": "#f2ede3",
    "wall_exterior": "#f2ede3",
    "wall_cap": "#f2ede3",
    "floor": "#c8c8c4",
    "ceiling": "#ffffff",
    "slab": "#a9a9a4",
    "frame": "#e8e4dc",
    "glass": "#dfeef2",
    "metal": "#9a9ea3",
    "door_leaf": "#e8e4dc",
    "stone": "#e4e1da",
    "wall_partition": "#f2ede3",
    "window_frame": "#e8e4dc",
    "window_glass": "#dfeef2",
    "stair_tread": "#e4e1da",
    "stair_side": "#d6d3cc",
}
"""Vật liệu trung tính theo vai trò (§3): tường trắng ngà, sàn xám nhạt, trần trắng.

Không chọn gỗ/gạch theo công năng (đó là thiết kế, M4).
"""
NEUTRAL_LABELS = {
    "wall_interior": "Tường trong (mặc định)",
    "wall_exterior": "Tường ngoài (mặc định)",
    "wall_cap": "Đỉnh tường (mặc định)",
    "floor": "Sàn (mặc định)",
    "ceiling": "Trần (mặc định)",
    "slab": "Sàn kết cấu (mặc định)",
    "frame": "Khung cửa (mặc định)",
    "glass": "Kính (mặc định)",
    "metal": "Kim loại (mặc định)",
    "door_leaf": "Cánh cửa (mặc định)",
    "stone": "Đá (mặc định)",
    "wall_partition": "Tường ngăn (mặc định)",
    "window_frame": "Khung cửa sổ (mặc định)",
    "window_glass": "Kính cửa sổ (mặc định)",
    "stair_tread": "Mặt bậc (mặc định)",
    "stair_side": "Thành cầu thang (mặc định)",
}
NEUTRAL_PBR = {
    "glass": (0.05, 0.0, 0.9),
    "window_glass": (0.05, 0.0, 0.9),
    "metal": (0.35, 1.0, 0.0),
    "stone": (0.5, 0.0, 0.0),
}
"""(roughness, metallic, transmission) khác mặc định (0.9, 0, 0) cho kính và kim loại."""

# Lớp đề xuất (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.5): hằng dựng hình mà `params` không mang
SUGGESTION_SCALE_TOL = 0.10
"""Món `item` phải co giãn quá ±10 % để vừa hộp → bỏ (F4 của §5, không âm thầm kẹp)."""
CABINET_PLINTH_M = 0.1
CABINET_COUNTERTOP_M = 0.04
CABINET_GAP_M = 0.004
"""Chân tủ 100 mm, mặt đá 40 mm, khe cánh 4 mm (F2 trong `rule_params.json` 2026.11-1)."""
CABINET_DUP_OVERLAP = 0.5
"""Hai dãy tủ cùng tầng (dưới/trên) chồng ≥ 50 % hộp nhỏ hơn → một dãy (lỗi hai mặt đá lệch 4,5 mm, B2)."""
CURTAIN_AMP_M = 0.03
CURTAIN_T_M = 0.012
CURTAIN_BOTTOM_M = 0.04
CURTAIN_ROD_M = 0.022
CEILING_GAP_M = 0.07
"""Rèm: nếp lượn ±30 mm, vải 12 mm, cách sàn 40 mm, thanh treo 22 mm, đỉnh rèm cách trần ≥ 70 mm."""
RAILING_INSET_M = 0.03
RAILING_GLASS_BOTTOM_M = 0.1
RAILING_POST_M = 0.04
"""Lan can: lùi 30 mm vào trong mép sàn, kính cách sàn 100 mm, trụ 40 x 40 mm."""
CORNICE_TOP_GAP_M = 0.0005
"""Phào hạ 0,5 mm dưới mặt trần để hai mặt không trùng nhau."""

# Vật liệu toàn cục (M1-M3 của §5), theo `compiler_version`
GLASS_TRANSMISSION = 1.0
GLASS_MAX_ROUGHNESS = 0.05
FABRIC_SHEEN = 0.6
"""KHR_materials_sheen trong khoảng 0,5-0,7 cho vải."""
ROUGHNESS_NOISE = 0.06
ROUGHNESS_NOISE_MIN = 0.3
"""Nhiễu độ nhám ±0,06 (ảnh 128 px lặp được, theo khoá vật liệu) cho vật liệu phi kim, không có ảnh độ nhám,
độ nhám ≥ 0,3 (bóng hơn thì nhiễu lộ rõ, thí nghiệm `F:/runs/abc/rules`)."""
