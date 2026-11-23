"""Khoá object (BE-00 §8) và kiểm khoá an toàn (K13).

Mọi hàm nhận khoá của gói này gọi `check_key`/`check_prefix` trước khi chạm kho:
khoá không bao giờ được nối thẳng từ chuỗi người dùng. Luật khoá nằm ở
`packages.core.object_keys` (một nguồn với `ml_contracts`, NO-060); ở đây xuất lại tên cũ
cho `local.py`, `s3.py`. Tiền tố dự án và lượt tải lên cũng là hàm của lõi, xuất lại giữ tên
(NO-077); tiền tố artifact của lượt chạy và của phiên bản mô hình dựng qua lõi (NO-081). Hàm
dựng khoá kiểm id bằng `packages.core.ids` (`check_id`, `is_ulid`), nên khoá sinh ra luôn nằm
trong cây đã khai.
"""

import re
from typing import Final

from packages.core.ids import check_id, is_ulid
from packages.core.object_keys import META_SUFFIX as META_SUFFIX
from packages.core.object_keys import check_key as check_key
from packages.core.object_keys import check_prefix as check_prefix
from packages.core.object_keys import is_segment, model_prefix, run_prefix
from packages.core.object_keys import project_prefix as project_prefix
from packages.core.object_keys import upload_prefix as upload_prefix
from packages.storage.sniff import ImageKind

MAX_ITEM_LEN: Final = 64

DATASETS_PREFIX: Final = "ml/datasets/"
"""Gốc khoá mọi phiên bản dataset: `ml/datasets/{dsv}/…` (BE-00 §8)."""

_ITEM_RE: Final = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
_EXT_RE: Final = re.compile(r"[a-z0-9]{1,8}")
_EXT_KIND: Final[dict[str, ImageKind]] = {"png": "png", "jpg": "jpeg"}


def _name(value: str) -> str:
    """Tên object là **một** đoạn khoá — chặn tên lồng đường dẫn."""
    if not is_segment(value):
        raise ValueError(f"tên object phải là một đoạn [A-Za-z0-9._-]: {value!r}")
    return value


def _path(value: str) -> str:
    """Tên object nhiều đoạn (đường mẫu `{split}/{sample_id}/{filename}`): **từng** đoạn qua `is_segment`."""
    if not all(is_segment(part) for part in value.split("/")):
        raise ValueError(f"tên object phải là các đoạn [A-Za-z0-9._-] nối bằng '/': {value!r}")
    return value


def _extension(value: str) -> str:
    """Đuôi tệp do server chọn: 1-8 ký tự thường, không dấu chấm."""
    if not _EXT_RE.fullmatch(value):
        raise ValueError(f"đuôi tệp phải là 1-8 ký tự [a-z0-9]: {value!r}")
    return value


def upload_original(project: str, floor: str, upload: str, ext: str) -> str:
    """Khoá tệp gốc người dùng tải lên."""
    return check_key(f"{upload_prefix(project, floor, upload)}original.{_extension(ext)}")


def upload_page(project: str, floor: str, upload: str, index: int) -> str:
    """Khoá ảnh PNG của một trang đã tách từ tệp gốc."""
    if index < 0:
        raise ValueError(f"số trang không âm: {index}")
    return check_key(f"{upload_prefix(project, floor, upload)}pages/{index}.png")


def upload_chunk(project: str, floor: str, upload: str, index: int, sha256: str) -> str:
    """Khoá một khúc của lượt tải: `<upload_prefix>chunks/{i}/{sha256}` (B2-04 [5]).

    Băm nằm **trong** khoá nên gửi lại đúng khúc ấy là ghi đè chính nó, còn gửi lại một nội dung
    khác là một object mới: `#7` so danh sách khoá trước và sau khi nối tệp để bắt đúng trường
    hợp thứ hai (409 `UPLOAD_CHUNKS_CHANGED`).
    """
    if index < 0:
        raise ValueError(f"số khúc không âm: {index}")
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"băm khúc phải là 64 ký tự hex thường: {sha256!r}")
    return check_key(f"{upload_prefix(project, floor, upload)}chunks/{index}/{sha256}")


def upload_page_revision(project: str, floor: str, upload: str, index: int, ulid: str) -> str:
    """Khoá một bản trang đã nắn: `…/pages/{i}-{ULID}.png`, mỗi lượt một ULID mới (W23).

    URL ký đứng yên một giờ nên ghi đè cùng khoá sẽ để FE dùng ảnh cũ; ULID mới làm URL cũ chết
    cùng object cũ. Đuôi `.png` do server đặt sau khi đã kiểm magic bytes (`server_chosen_kind`).
    """
    if index < 0:
        raise ValueError(f"số trang không âm: {index}")
    if not is_ulid(ulid):
        raise ValueError(f"bản trang phải mang ULID: {ulid!r}")
    return check_key(f"{upload_prefix(project, floor, upload)}pages/{index}-{ulid}.png")


def run_artifact(project: str, floor: str, upload: str, run: str, step: str, name: str) -> str:
    """Khoá artifact của một bước pipeline trong một lượt chạy (bố cục và luật bước ở lõi, NO-081)."""
    return check_key(f"{run_prefix(upload_prefix(project, floor, upload), run, step)}{_name(name)}")


def lighting_bake_prefix(project_id: str, bake_id: str) -> str:
    """Tiền tố object của một lượt bake ánh sáng: `projects/{prj}/lighting-bakes/{bak}/` (id sai mẫu → `ValueError`)."""
    return check_prefix(f"{project_prefix(project_id)}lighting-bakes/{check_id('bak', bake_id)}/")


def lighting_bake_object(project_id: str, bake_id: str, name: str) -> str:
    """Khoá một tệp của lượt bake (`input.glb`, `baked.glb`, `lightmap.png`, `manifest.json`); `name` một đoạn."""
    return check_key(f"{lighting_bake_prefix(project_id, bake_id)}{_name(name)}")


def drawing_set_prefix(project_id: str, set_id: str) -> str:
    """Tiền tố object của một bộ hồ sơ (K-32a): `projects/{prj}/drawing-sets/{dws}/` — dọn rác dự án phủ nó."""
    return check_prefix(f"{project_prefix(project_id)}drawing-sets/{check_id('dws', set_id)}/")


def drawing_set_file_prefix(project_id: str, set_id: str, file_id: str) -> str:
    """Tiền tố object của một tệp trong bộ: `…/drawing-sets/{dws}/files/{dwf}/`."""
    return check_prefix(f"{drawing_set_prefix(project_id, set_id)}files/{check_id('dwf', file_id)}/")


def drawing_set_chunk(project_id: str, set_id: str, file_id: str, index: int, sha256: str) -> str:
    """Khoá một khúc của tệp trong bộ: `<tệp>chunks/{i}/{sha256}` — băm trong khoá như `upload_chunk`."""
    if index < 0:
        raise ValueError(f"số khúc không âm: {index}")
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"băm khúc phải là 64 ký tự hex thường: {sha256!r}")
    return check_key(f"{drawing_set_file_prefix(project_id, set_id, file_id)}chunks/{index}/{sha256}")


def drawing_set_original(project_id: str, set_id: str, file_id: str, ext: str) -> str:
    """Khoá tệp gốc đã nối của một tệp trong bộ: `<tệp>original.<đuôi>`."""
    return check_key(f"{drawing_set_file_prefix(project_id, set_id, file_id)}original.{_extension(ext)}")


def drawing_set_page(project_id: str, set_id: str, file_id: str, index: int) -> str:
    """Khoá ảnh PNG một trang đã dựng của tệp trong bộ (tác vụ phân tích ghi): `<tệp>pages/{i}.png`."""
    if index < 0:
        raise ValueError(f"số trang không âm: {index}")
    return check_key(f"{drawing_set_file_prefix(project_id, set_id, file_id)}pages/{index}.png")


def drawing_set_reading(project_id: str, set_id: str, file_id: str, index: int) -> str:
    """Khoá kết quả đọc một trang (khung gợi ý + chữ OCR, worker `ml` ghi — K-32a PR(3)): `<tệp>readings/{i}.json`."""
    if index < 0:
        raise ValueError(f"số trang không âm: {index}")
    return check_key(f"{drawing_set_file_prefix(project_id, set_id, file_id)}readings/{index}.json")


def library_object(item: str, name: str) -> str:
    """Khoá object của một mục thư viện (`.glb`, ảnh xem trước)."""
    if not _ITEM_RE.fullmatch(item) or len(item) > MAX_ITEM_LEN:
        raise ValueError(f"id thư viện phải khớp ^[a-z0-9]+(-[a-z0-9]+)*$ và ≤ {MAX_ITEM_LEN} ký tự: {item!r}")
    return check_key(f"library/{item}/{_name(name)}")


def model_artifact(model: str, name: str) -> str:
    """Khoá artifact của một phiên bản mô hình ML."""
    return check_key(f"{model_prefix(model)}{_name(name)}")


def dataset_version_prefix(dataset_version: str) -> str:
    """Tiền tố object của một phiên bản dataset: `ml/datasets/{dsv}/` (id sai mẫu → `ValueError`)."""
    return f"{DATASETS_PREFIX}{check_id('dsv', dataset_version)}/"


def dataset_object(dataset_version: str, name: str) -> str:
    """Khoá object của một phiên bản tập dữ liệu ML; `name` một hay nhiều đoạn (`train/s1/image.png`)."""
    return check_key(f"{dataset_version_prefix(dataset_version)}{_path(name)}")


def avatar(user: str, ulid: str, ext: str) -> str:
    """Khoá ảnh đại diện; đuôi do server chọn sau khi đã kiểm magic bytes (K14)."""
    if not is_ulid(ulid):
        raise ValueError(f"tên ảnh đại diện phải là ULID: {ulid!r}")
    if ext not in _EXT_KIND:
        raise ValueError(f"ảnh đại diện chỉ nhận đuôi {sorted(_EXT_KIND)}: {ext!r}")
    return check_key(f"users/{check_id('usr', user)}/avatar/{ulid}.{ext}")


def server_chosen_kind(key: str) -> ImageKind | None:
    """Loại ảnh suy từ đuôi khoá do server đặt tên (ảnh đại diện, trang `{i}.png`/`{i}-{ULID}.png` của lượt tải hay
    của tệp trong bộ hồ sơ K-32a); khoá khác → `None`.

    Chỉ khoá mà server chọn đuôi sau khi đã kiểm magic bytes mới được ký URL với `kind` truyền
    sẵn (W23, K15); `original.<đuôi>` mang đuôi người dùng khai nên không bao giờ khớp (NO-011).
    "Do server đặt" ⇔ chính `avatar`/`upload_page`/`upload_page_revision` dựng lại được đúng khoá đó, nên luật id
    (`packages.core.ids`) và bố cục chỉ có một nguồn với hàm dựng (NO-073). Hàm dựng ném
    `ValueError` nghĩa là khoá không do server đặt — kết quả `None`, không phải lỗi.
    """
    try:
        match key.split("/"):
            case ["users", user, "avatar", name]:
                ulid, _, ext = name.partition(".")
                avatar(user, ulid, ext)  # dựng được thì khoá dựng lại trùng từng byte với `key`
                return _EXT_KIND[ext]
            case ["projects", project, "floors", floor, "uploads", upload, "pages", name]:
                index, dash, ulid = name.removesuffix(".png").partition("-")
                # `int` chuẩn hoá `007`, chữ số Unicode: khoá đó server không bao giờ sinh ra.
                if index.isdecimal():
                    built = (
                        upload_page_revision(project, floor, upload, int(index), ulid)
                        if dash
                        else upload_page(project, floor, upload, int(index))
                    )
                    if built == key:
                        return "png"
            case ["projects", project, "drawing-sets", set_id, "files", file_id, "pages", name]:
                index = name.removesuffix(".png")
                if index.isdecimal() and drawing_set_page(project, set_id, file_id, int(index)) == key:
                    return "png"
    except ValueError:
        return None
    return None
