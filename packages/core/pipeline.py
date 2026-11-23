"""Bước pipeline và mã pipeline không phải HTTP.

Thứ tự và trọng số khớp `PIPELINE_STAGES` của FE (`src/lib/realtime/pipeline.ts:48-55`).
"""

from enum import StrEnum
from typing import Literal

PipelineStep = Literal[
    "preprocess",
    "wallSegmentation",
    "openingAndFurnitureDetection",
    "dimensionReading",
    "spatialDataBuild",
    "qualityCheck",
]

PIPELINE_STEPS: tuple[tuple[str, int], ...] = (
    ("preprocess", 5),
    ("wallSegmentation", 30),
    ("openingAndFurnitureDetection", 20),
    ("dimensionReading", 15),
    ("spatialDataBuild", 20),
    ("qualityCheck", 10),
)


class PipelineCode(StrEnum):
    """Mã của pipeline, không đăng ký vào sổ lỗi HTTP (BE-00 §4)."""

    PIPELINE_SUPERSEDED = "PIPELINE_SUPERSEDED"  # chuỗi `error` của `Progress`
    PIPELINE_CANCELLED = "PIPELINE_CANCELLED"  # người dùng huỷ lượt (K-24); `error` của `Progress`
    SCALE_UNRESOLVED = "SCALE_UNRESOLVED"  # chỉ nội bộ; trên dây là `scaleStatus`
