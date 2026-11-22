"""Cốt trên mặt cắt: chép `selfcheck` của `register_v.py` và ca cốt 0 của `heights3.selfcheck()`."""

import time
from dataclasses import dataclass

from packages.domain.drawing_set.sections import _register, side_levels

_SIZE = (2000, 2000)
_Y0, _K = 1500.0, 100.0  # y_px = 1500 - 100·cốt_m


@dataclass(frozen=True)
class _Text:
    """Chữ OCR giả: nội dung, hộp, độ tin cậy."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float = 0.9


def _at(text: str, y: float, x: float = 100.0) -> _Text:
    """Chữ có tâm ở `(x, y)`."""
    return _Text(text, (x - 40, y - 5, x + 40, y + 5))


def _mark(value: float, y: float | None = None) -> _Text:
    """Ký hiệu cốt "+3.600" nằm đúng trên đường khớp, hoặc ở `y` cho trước."""
    sign = "-" if value < 0 else "+"
    return _at(f"{sign}{abs(value):.3f}", _Y0 - _K * value if y is None else y)


def test_clean_line_fits_exactly() -> None:
    fit = _register([(v, _Y0 - _K * v) for v in (0.0, 3.6, 7.2, 10.8)])
    assert fit is not None
    assert abs(fit.y0 - _Y0) < 1e-6
    assert abs(fit.k - _K) < 1e-9
    assert fit.confidence == 0.8
    assert side_levels([_mark(v) for v in (0.0, 3.6, 7.2, 10.8)], _SIZE) == (0.0, 3.6, 7.2, 10.8)


def test_misread_mark_is_outlier() -> None:
    clean = [(v, _Y0 - _K * v) for v in (0.0, 3.6, 7.2, 10.8)]
    fit = _register([(0.6 if v == 3.6 else v, y) for v, y in clean])
    assert fit is not None
    assert abs(fit.y0 - _Y0) < 1e-6
    texts = [_mark(0.0), _mark(0.6, y=_Y0 - _K * 3.6), _mark(7.2), _mark(10.8)]
    assert side_levels(texts, _SIZE) == (0.0, 7.2, 10.8)


def test_one_value_is_empty() -> None:
    assert _register([(3.6, 640.0), (3.6, 640.0)]) is None
    assert _register([(3.6, 640.0)]) is None
    assert side_levels([_mark(3.6), _mark(3.6, y=640.0)], _SIZE) == ()
    assert side_levels([], _SIZE) == ()


def test_two_values_fit() -> None:
    assert side_levels([_mark(0.0), _mark(3.3)], _SIZE) == (0.0, 3.3)


def test_inverted_marks_are_empty() -> None:
    # cốt cao hơn nằm thấp hơn trên trang: không phải mặt cắt dựng đứng
    assert side_levels([_mark(0.0, y=500.0), _mark(3.3, y=900.0)], _SIZE) == ()


def test_zero_text_recovered_only_at_zero() -> None:
    # "0.00" không qua bộ đọc cốt; chỉ thêm 0.0 khi khớp đặt nó ở 0,0 m
    at_zero = [_mark(3.6), _mark(7.2), _at("0.00", _Y0), _at("00.00", 505.0)]
    assert side_levels(at_zero, _SIZE) == (0.0, 3.6, 7.2)
    elsewhere = [_mark(3.6), _mark(7.2), _at("0.00", 505.0)]
    assert side_levels(elsewhere, _SIZE) == (3.6, 7.2)


def test_heights3_zero_variants() -> None:
    # ca của heights3: "+/-0.00" ở cốt 0 được nhận, "+-0.000" ở y = 505 là ngoại lai
    texts = [_mark(3.9), _mark(7.2), _at("+/-0.00", _Y0), _at("+-0.000", 505.0)]
    assert side_levels(texts, _SIZE) == (0.0, 3.9, 7.2)


def test_duplicate_reading_and_off_page_text() -> None:
    texts = [
        _mark(0.0),
        _mark(3.6),
        _mark(3.6, y=_Y0 - _K * 3.6 + 2),
        _mark(7.2),
        _at("+9.900", _Y0 - _K * 9.9, x=5000.0),
    ]
    assert side_levels(texts, _SIZE) == (0.0, 3.6, 7.2)


def test_junk_is_fast() -> None:
    junk = [_at(f"PHÒNG NGỦ {i} " + "x" * 400, 50.0 + i) for i in range(100)]
    junk += [_at(f"{3000 + i}", 20.0 + i * 9) for i in range(100)]  # kích thước không dấu
    texts = junk + [_mark(v) for v in (0.0, 3.6, 7.2)]
    start = time.perf_counter()
    assert side_levels(texts, _SIZE) == (0.0, 3.6, 7.2)
    many = [_mark(round(0.05 * i, 2), y=float(1900 - i * 9)) for i in range(200)]
    assert side_levels(many, _SIZE) == ()  # quá trần ký hiệu: dè dặt, trả rỗng
    assert time.perf_counter() - start < 1.0
