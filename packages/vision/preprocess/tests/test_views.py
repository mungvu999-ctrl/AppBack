"""Tách tờ thành hình (`views.split_views`, K-32a PR(3)): ca của nhân nghiên cứu và từng nhánh của bản chuyển."""

from typing import Final

import cv2
import numpy as np
import pytest

from packages.vision.preprocess.views import INK_SPLIT_SCORE, VIEW_SCORE, WHOLE_SCORE, ViewBox, split_views

TOL: Final = 4


def _near(box: ViewBox, want: tuple[int, int, int, int], tol: int = TOL) -> bool:
    """Khung trùng `want` trong `tol` px ở cả bốn cạnh."""
    return all(abs(a - b) <= tol for a, b in zip((box.x0, box.y0, box.x1, box.y1), want, strict=True))


def _panels(truth: list[tuple[int, int, int, int]], size: tuple[int, int] = (900, 1400)) -> np.ndarray:
    """Tờ trắng tinh dán các tấm giấy xám (mỗi tấm một mức xám) có nét mực ngẫu nhiên — ca `selfcheck` của nhân."""
    sheet = np.full(size, 255, np.uint8)
    rng = np.random.default_rng(0)
    for i, (x0, y0, x1, y1) in enumerate(truth):
        sheet[y0:y1, x0:x1] = 225 + 6 * i
        for _ in range(25):
            a, b = rng.integers(x0 + 10, x1 - 60), rng.integers(y0 + 10, y1 - 50)
            cv2.rectangle(sheet, (int(a), int(b)), (int(a) + 40, int(b) + 30), 40, 2)
    return sheet


def test_kernel_selfcheck_three_panels() -> None:
    """Ba tấm giấy rời → đúng ba khung trong 4 px, độ chắc 0,9, theo thứ tự đọc (trên xuống, trái sang)."""
    truth = [(50, 50, 450, 400), (500, 60, 1300, 420), (80, 500, 700, 850)]
    got = split_views(_panels(truth))
    assert len(got) == len(truth)
    assert all(_near(box, want) for box, want in zip(got, truth, strict=True))
    assert {box.score for box in got} == {VIEW_SCORE}


def test_touching_panels_cut_at_grey_step() -> None:
    """Hai tấm sát nhau chỉ khác mức xám (bậc >= 7) → cắt ở bậc, ra hai khung."""
    sheet = np.full((800, 1600), 255, np.uint8)
    sheet[40:760, 40:800] = 228
    sheet[40:760, 800:1560] = 244
    for x in (100, 900):
        cv2.rectangle(sheet, (x, 100), (x + 500, 600), 30, 3)
    got = split_views(sheet)
    assert len(got) == 2
    assert abs(got[0].x1 - 800) <= 2 * TOL
    assert abs(got[1].x0 - 800) <= 2 * TOL


def test_frame_cells_replace_one_panel() -> None:
    """Một tấm giấy chứa hai ô khung kín (đường kẻ dài) → hai khung, ranh nằm trong khe giữa hai ô."""
    sheet = np.full((900, 1600), 255, np.uint8)
    sheet[20:880, 20:1580] = 236
    cv2.rectangle(sheet, (40, 40), (780, 860), 0, 3)
    cv2.rectangle(sheet, (820, 40), (1560, 860), 0, 3)
    for x in (120, 900):
        cv2.line(sheet, (x, 300), (x + 400, 500), 20, 2)
    got = split_views(sheet)
    assert len(got) == 2
    assert got[0].x0 <= 40
    assert got[1].x1 >= 1560
    assert got[0].x1 <= 830
    assert got[1].x0 >= 770


def test_white_page_falls_back_to_whole_drawing() -> None:
    """Nền trắng tinh, mực mảnh (tờ dựng từ PDF): mặt nạ giấy vụn, khung giữ lại không phủ mực → một khung bao mực."""
    sheet = np.full((1000, 1400), 255, np.uint8)
    for i in range(12):
        cv2.line(sheet, (100 + 90 * i, 120), (100 + 90 * i, 880), 0, 1)
    cv2.line(sheet, (100, 120), (1090, 120), 0, 1)
    (box,) = split_views(sheet)
    assert box.score == WHOLE_SCORE
    assert _near(box, (100, 120, 1091, 881), tol=8)


def _framed_sheet(frame: int) -> np.ndarray:
    """Hai tờ có khung xám `frame` dán sát nhau trên cùng một tấm giấy (cùng mức xám, không bậc xám để cắt)."""
    sheet = np.full((1000, 1600), 255, np.uint8)
    sheet[20:980, 20:1580] = 236
    for x0, x1 in ((40, 800), (800, 1560)):
        cv2.rectangle(sheet, (x0, 40), (x1, 960), frame, 3)
        cv2.line(sheet, (x0 + 60, 300), (x1 - 300, 700), 20, 2)
        cv2.line(sheet, (x0 + 100, 500), (x1 - 200, 500), 20, 2)
    return sheet


def test_light_frames_touching_cut_at_shared_frame_line() -> None:
    """Khung xám nhạt (180 > ngưỡng mực 160) chung một cạnh dọc chạy suốt tấm → cắt ở cạnh chung, ra hai khung."""
    got = split_views(_framed_sheet(180))
    assert len(got) == 2
    assert abs(got[0].x1 - 800) <= 2 * TOL
    assert abs(got[1].x0 - 800) <= 2 * TOL
    assert {box.score for box in got} == {VIEW_SCORE}


def test_long_line_without_side_frames_is_not_a_cut() -> None:
    """Một hình có đường nền dài suốt hộp nhưng hai cạnh hộp không có khung → không cắt ở đường đó."""
    sheet = np.full((900, 1400), 255, np.uint8)
    sheet[50:850, 50:1350] = 236
    cv2.line(sheet, (60, 450), (1340, 450), 0, 3)
    for x in range(200, 1300, 150):
        cv2.line(sheet, (x, 200), (x + 60, 420), 0, 2)
    (box,) = split_views(sheet)
    assert _near(box, (50, 50, 1350, 850))


def test_white_page_two_drawings_split_at_white_gap() -> None:
    """Nền trắng tinh (PDF vector), hai hình cách nhau một khe trắng rộng → hai khung, độ chắc `INK_SPLIT_SCORE`."""
    sheet = np.full((1000, 1600), 255, np.uint8)
    for x0 in (100, 900):
        cv2.rectangle(sheet, (x0, 150), (x0 + 600, 850), 0, 1)
        cv2.line(sheet, (x0, 150), (x0 + 600, 850), 0, 1)
    got = split_views(sheet)
    assert [box.score for box in got] == [INK_SPLIT_SCORE, INK_SPLIT_SCORE]
    assert _near(got[0], (100, 150, 701, 851), tol=8)
    assert _near(got[1], (900, 150, 1501, 851), tol=8)


def test_small_and_inkless_panels_are_kept() -> None:
    """Tấm nhỏ (< 3 % tờ, không cắt tiếp) vẫn là một khung; tấm giấy không nét mực nào vẫn được giữ (độ phủ mực 1)."""
    sheet = np.full((1000, 1000), 255, np.uint8)
    sheet[100:250, 100:250] = 230
    got = split_views(sheet)
    assert len(got) == 1
    assert _near(got[0], (100, 100, 250, 250))


def test_blank_page_has_no_views() -> None:
    """Tờ trắng tinh → không khung nào."""
    assert split_views(np.full((300, 400), 255, np.uint8)) == []


def test_large_sheet_boxes_in_input_pixels() -> None:
    """Tờ lớn hơn bản làm việc (1600 px): khung trả theo điểm ảnh ảnh vào, kẹp trong khổ ảnh."""
    truth = [(100, 100, 1500, 1300), (1700, 120, 3100, 1300)]
    sheet = _panels(truth, size=(1500, 3300))
    got = split_views(sheet)
    assert len(got) == 2
    assert all(_near(box, want, tol=8) for box, want in zip(got, truth, strict=True))
    assert all(0 <= b.x0 < b.x1 <= 3300 and 0 <= b.y0 < b.y1 <= 1500 for b in got)


@pytest.mark.parametrize("shape", [(1, 1), (5, 3000), (3000, 5)])
def test_degenerate_shapes_do_not_raise(shape: tuple[int, int]) -> None:
    """Ảnh 1x1 hay rất dẹt không làm hỏng (kể cả hàng toàn mực)."""
    assert isinstance(split_views(np.zeros(shape, np.uint8)), list)
