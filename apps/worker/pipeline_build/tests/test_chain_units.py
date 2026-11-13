"""R9-O: đơn vị số kích thước trần trong chuỗi kích thước (`_chain_samples`, `_metric_mm`).

Cách đọc "mét-cm" (`2.25` = mét, `80` = cm) chỉ được chọn khi chính trang có căn cứ (cụm tỉ số đông hơn hẳn và có số
mét thập phân mà cách đọc mm bỏ); không có căn cứ hay hoà → giữ mm như prod. Bản vẽ mm thuần không bao giờ bị đổi.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest

from apps.worker.pipeline_build import build as build_module
from apps.worker.pipeline_build.tests.helpers import build_wrapped
from packages.ml_contracts.artifacts import BoxPx, DetectionPx, PointPx, TextPx, WallPx
from packages.testing.fixtures.clock import FakeClock
from packages.vision.dimensions import parse_length_mm

_R8_CASES: Final = json.loads((Path(__file__).parent / "data" / "r8_scale_cases.json").read_text(encoding="utf-8"))
"""cvcfp-2 thật (R8-S): chữ OCR + `span_px`, tường, hộp cửa — bản vẽ Pháp không ghi đơn vị."""


@pytest.mark.parametrize(
    ("text", "as_mm", "as_metric"),
    [
        ("3450 mm", 3450, 3450),  # đơn vị ghi rõ: hai cách đọc như nhau
        ("345 cm", 3450, 3450),
        ("3,45 m", 3450, 3450),
        ("3.45m", 3450, 3450),
        ("3.45", None, 3450),  # số mét thập phân: mm bỏ (3,45 mm không nguyên)
        ("1,20", None, 1200),  # dấu phẩy thập phân
        ("0.90", None, 900),
        ("345", 345, 3450),
        ("3450", 3450, 34500),
        ("120", 120, 1200),
        ("3.450", 3450, 3450),  # dấu chấm nhóm nghìn ở mm; mét 3,450 ở mét-cm: cùng 3450
        ("3,450", None, 3450),  # một dấu phẩy là thập phân ở mm (3,45 mm bỏ); mét-cm 3,45 m
        ("2 450", 2450, 2450),  # khoảng trắng nhóm nghìn
        ("12000", 12000, 12000),  # 5 chữ số: không phải cm, đọc như mm
        ("BUREAU", None, None),
        ("48OO", None, None),  # K19: không sửa chữ thành số
    ],
)
def test_metric_mm__reads_each_convention(text: str, as_mm: int | None, as_metric: int | None) -> None:
    """Một chữ đọc theo từng cách; cách nào dùng cho cả trang do `_chain_samples` quyết."""
    assert (parse_length_mm(text), build_module._metric_mm(text)) == (as_mm, as_metric)


def _text(value: str, span: float) -> TextPx:
    return TextPx(text=value, box=BoxPx(x_min=0, y_min=0, x_max=30, y_max=10), confidence=0.9, span_px=span)


def _page(*rows: tuple[str, float]) -> list[TextPx]:
    return [_text(value, span) for value, span in rows]


@pytest.mark.parametrize(
    ("name", "texts", "expected_mm"),
    [
        # mm thuần (VN/Nhật), 10 mm/px: giữ nguyên, kể cả số có dấu nhóm nghìn
        ("mm-thuan", _page(("3450", 345), ("900", 90), ("2 400", 240), ("1.200", 120)), [3450, 900, 2400, 1200]),
        # mm thuần ở 0,5 mm/px: mm ra ngoài dải 1-200 (bỏ hết); nhân 10 vào dải nhưng không có số mét lẻ → không đổi
        ("mm-thuan-ngoai-dai", _page(("3450", 6900), ("900", 1800), ("2400", 4800)), []),
        # mét-cm (cvcfp), 18 mm/px: số mét thập phân + số cm nguyên cùng một cụm → đọc mét-cm
        ("met-cm", _page(("3.45", 191.7), ("1,20", 66.7), ("2.25", 125), ("80", 44.4)), [3450, 1200, 2250, 800]),
        # mm có vài cao độ thập phân lạc ("2.70", "3.00"): cụm mm đông hơn → giữ mm
        (
            "mm-co-cao-do",
            _page(("3300", 330), ("1200", 120), ("4500", 450), ("2.70", 50), ("3.00", 80)),
            [3300, 1200, 4500],  # "3.00" đọc mm = 3 mm → 0,04 mm/px ngoài dải, bỏ như prod
        ),
        # chỉ một số mét lẻ: không đủ căn cứ → giữ mm (chữ đó bị bỏ như prod)
        ("mot-so-met", _page(("2.25", 125)), []),
        # hoà: 2 số mét (18 mm/px) và 2 số nguyên ở 10 mm/px → mm 2 = mét-cm 2 → giữ mm
        ("hoa", _page(("3.30", 183.3), ("2.25", 125), ("800", 80), ("900", 90)), [800, 900]),
        # nhiều quy ước trong cùng trang, có đơn vị: hai cách đọc như nhau → mm
        ("co-don-vi", _page(("3,30 m", 330), ("225 cm", 225), ("800mm", 80)), [3300, 2250, 800]),
    ],
)
def test_chain_samples__unit_follows_page_evidence(name: str, texts: list[TextPx], expected_mm: list[int]) -> None:
    """R9 2b: đổi sang mét-cm chỉ khi trang có căn cứ; còn lại (hoà, một chữ, mm thuần) đọc mm như prod."""
    assert [s.real_length_mm for s in build_module._chain_samples(texts)] == expected_mm, name


def test_build_layer__cvcfp2_unitless_metre_cm_reads_metric(fake_clock: FakeClock) -> None:
    """Hồi quy R8-S cvcfp-2 (chữ OCR thật): prod đọc cụm "70/60/80/90" là mm → 1,79 mm/px (sai 10 lần); đọc mét-cm
    → ~18,2 (tham chiếu hai AI 18,19), gốc `chain`."""
    case: dict[str, Any] = _R8_CASES["cvcfp-2"]
    texts = [
        TextPx(text=t, box=BoxPx(x_min=a, y_min=b, x_max=c, y_max=d), confidence=0.9, span_px=span)
        for t, a, b, c, d, span in case["texts"]
    ]
    doors = [
        DetectionPx(label="door", box=BoxPx(x_min=a, y_min=b, x_max=c, y_max=d), confidence=0.8)
        for a, b, c, d in case["doors"]
    ]
    walls = [
        WallPx(start=PointPx(x=x0, y=y0), end=PointPx(x=x1, y=y1), thickness_px=t, confidence=0.9)
        for x0, y0, x1, y1, t in case["walls"]
    ]
    built = build_wrapped(
        walls=walls,
        detections=doors,
        texts=texts,
        fallback_mm_per_px=Decimal(1),
        clock=fake_clock,
        width_px=1318,
        height_px=1229,
    )
    assert built.scale_basis == "chain"
    assert abs(float(built.scale_mm_per_px) / 18.19 - 1) <= 0.01
