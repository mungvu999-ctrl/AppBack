"""Luật khoá object (BE-00 §8, K13) — nguồn duy nhất cho `packages.storage` và `packages.ml_contracts`.

Khoá là chuỗi ASCII `[A-Za-z0-9._-]` chia đoạn bằng `/`, không đoạn rỗng, `.` hay `..`,
tối đa `MAX_KEY_BYTES` byte, không trùng đuôi metadata của kho đĩa. Nằm ở lõi vì
`ml_contracts` không được nhập `storage` ([9] B5-01) mà vẫn phải kiểm khoá trong payload
không tin (NO-060). Cùng lý do, các tiền tố mà payload ML phải kiểm cũng ở đây: dự án, lượt
tải lên (NO-077), artifact của một bước trong lượt chạy và phiên bản mô hình (NO-081); các
hàm dựng khoá đầy đủ vẫn ở `packages.storage.keys`. Sai luật → `ValueError`; người gọi đổi
thành lỗi của tầng mình.
"""

import re
from typing import Final

from packages.core.ids import check_id, is_spatial_id
from packages.core.pipeline import PIPELINE_STEPS

MAX_KEY_BYTES: Final = 1024
META_SUFFIX: Final = ".meta.json"
"""Đuôi file metadata của `LocalDiskStorage`; khoá object không được trùng."""
MODELS_PREFIX: Final = "ml/models/"
"""Gốc mọi phiên bản mô hình ML (BE-00 §8); payload kết thúc huấn luyện chỉ biết gốc này, chưa có id."""
MODEL_EXPORT_PACKS_PREFIX: Final = "model-export-packs/"
"""Gốc các gói vật liệu/mẫu của bộ xuất GLB (M1-01): `model-export-packs/{version}/…`."""

_DOT_SEGMENTS: Final = frozenset({"", ".", ".."})
_SEGMENT_RE: Final = re.compile(r"[A-Za-z0-9._-]+")
_STEP_IDS: Final = frozenset(step for step, _ in PIPELINE_STEPS)


def is_segment(value: str) -> bool:
    """`value` là đúng một đoạn khoá: `[A-Za-z0-9._-]+`, không phải `.` hay `..`, không chứa `/`."""
    return value not in _DOT_SEGMENTS and _SEGMENT_RE.fullmatch(value) is not None


def check_key(key: str) -> str:
    """Khoá hợp lệ → trả lại chính nó; sai → `ValueError` (không bao giờ ra ngoài kho).

    Đo độ dài theo byte UTF-8 (trần của S3), không theo ký tự.
    """
    if not key:
        raise ValueError("khoá rỗng")
    if len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise ValueError(f"khoá dài hơn {MAX_KEY_BYTES} byte")
    if key.endswith(META_SUFFIX):
        raise ValueError(f"khoá không được kết thúc bằng {META_SUFFIX}")
    for segment in key.split("/"):
        if is_segment(segment):
            continue
        if segment in _DOT_SEGMENTS:
            raise ValueError(f"khoá có đoạn rỗng, '.' hay '..': {key!r}")
        raise ValueError(f"đoạn khoá chỉ nhận [A-Za-z0-9._-]: {key!r}")
    return key


def check_prefix(prefix: str) -> str:
    """Tiền tố luôn kết thúc bằng `/` — nhờ vậy `projects/prj_A/` không chạm `projects/prj_AB/`."""
    if not prefix.endswith("/"):
        raise ValueError(f"tiền tố phải kết thúc bằng '/': {prefix!r}")
    check_key(prefix[:-1])
    return prefix


def project_prefix(project: str) -> str:
    """Tiền tố mọi object của một dự án — dùng khi dọn rác dự án xoá mềm."""
    return check_prefix(f"projects/{check_id('prj', project)}/")


def upload_prefix(project: str, floor: str, upload: str) -> str:
    """Tiền tố mọi object của một lượt tải lên (bản gốc, trang, artifact).

    Bố cục `projects/{prj}/floors/{L-…}/uploads/{upl}/` — nguồn duy nhất cho `storage` (dựng khoá)
    và `ml_contracts` (kiểm payload), NO-077. Nằm dưới `project_prefix` nên dọn rác dự án phủ mọi
    lượt tải lên. Id sai → `ValueError` nêu đúng trường.
    """
    if not is_spatial_id("level", floor):
        raise ValueError(f"id tầng sai mẫu L-<base36 HOA>: {floor!r}")
    return check_prefix(f"{project_prefix(project)}floors/{floor}/uploads/{check_id('upl', upload)}/")


def upload_prefix_of(key: str) -> str:
    """Tiền tố lượt tải lên đứng đầu `key` (khoá không tin trong payload ML); sai → `ValueError`.

    Kiểm cả khoá bằng `check_key` trước (fail-closed, NO-088): đuôi `../x` hay `//` không được ra
    tiền tố. Đọc id ở vị trí đoạn của bố cục rồi dựng lại bằng `upload_prefix`: chỉ nhận khi bản
    dựng lại là đầu của `key` từng byte, nên bố cục không có bản tách thứ hai (NO-077).
    """
    parts = check_key(key).split("/", 6)
    if len(parts) == 7:
        prefix = upload_prefix(parts[1], parts[3], parts[5])
        if key.startswith(prefix):
            return prefix
    raise ValueError(f"khoá không nằm dưới một lượt tải lên: {key!r}")


def run_prefix(upload_root: str, run: str, step: str) -> str:
    """Tiền tố artifact của bước `step` trong lượt chạy `run`: `{upload_root}runs/{run}/{step}/` (BE-00 §8).

    Nguồn duy nhất cho `storage` (dựng khoá) và `ml_contracts` (kiểm `artifact_prefix`), NO-081.
    `upload_root` là tiền tố do `upload_prefix`/`upload_prefix_of` trả — hàm không kiểm lại bố cục
    của nó. Bước ngoài `PIPELINE_STEPS` hay id lượt chạy sai → `ValueError` nêu đúng trường.
    """
    if step not in _STEP_IDS:
        raise ValueError(f"bước pipeline lạ: {step!r}")
    return check_prefix(f"{upload_root}runs/{check_id('run', run)}/{step}/")


def model_prefix(model: str) -> str:
    """Tiền tố artifact của một phiên bản mô hình `ml/models/{mdl}/` — nguồn duy nhất, NO-081."""
    return check_prefix(f"{MODELS_PREFIX}{check_id('mdl', model)}/")
