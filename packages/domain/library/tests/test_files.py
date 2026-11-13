"""`FILE_CATALOGUE`: manifest `files.json` hợp lệ, tách khỏi `CATALOGUE`; chuỗi ghi công."""

import json
from pathlib import Path

import pytest

from packages.domain.library import CATALOGUE, FILE_CATALOGUE, LIBRARY_GROUPS, FileItem, is_item_id
from packages.domain.library.files import MANIFEST, MODIFIED_NOTE, load_file_catalogue

SHA = "a" * 64
CC = "https://creativecommons.org/licenses/"


def _item(**overrides: str) -> FileItem:
    """Một `FileItem` mẫu; ghi đè từng trường."""
    values = {"id": "x-one", "name": "x", "group": "decor", "license": "CC0", "author": "", "source": "", **overrides}
    return FileItem(model_sha256=SHA, **values)


def test_file_catalogue_is_valid_and_disjoint() -> None:
    """978 mục (vòng 13: +78 ABO, +17 Objaverse), id hợp lệ, không trùng nhau hay `CATALOGUE`, nhóm đúng, có decor."""
    ids = [item.id for item in FILE_CATALOGUE]
    assert len(ids) == len(set(ids)) == 978
    assert all(is_item_id(i) for i in ids)
    assert not set(ids) & {item.id for item in CATALOGUE}
    assert {item.group for item in FILE_CATALOGUE} <= set(LIBRARY_GROUPS)
    assert "decor" in {item.group for item in FILE_CATALOGUE}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"author": "woodport", "license": "CC-BY 4.0", "source": 'Objaverse ← https://sketchfab.com/m/1 "Oak"'},
            f"“Oak” · woodport · CC-BY 4.0 · {CC}by/4.0/ · https://sketchfab.com/m/1 · {MODIFIED_NOTE}",
        ),
        (
            {"author": "a", "license": "CC-BY 3.0", "source": "https://x.test/a.zip (tách) ; https://y.test/b.glb"},
            f"a · CC-BY 3.0 · {CC}by/3.0/ · https://x.test/a.zip · https://y.test/b.glb · {MODIFIED_NOTE}",
        ),
        ({"author": "a", "license": "CC-BY 9.9", "source": ""}, f"a · CC-BY 9.9 · {MODIFIED_NOTE}"),
        ({"source": "https://polyhaven.com/a/x"}, "Poly Haven · CC0"),
        ({"source": "Pascal vendor/pascal/assets/x", "license": "MIT"}, "Pascal · MIT"),
        ({"source": "https://example.com/x"}, "CC0"),
    ],
)
def test_attribution(overrides: dict[str, str], expected: str) -> None:
    """Tác giả trước; không có thì Poly Haven / Pascal theo nguồn; không suy được thì chỉ giấy phép.

    CC BY (NO-437): thêm tên gốc trong ngoặc kép, URL giấy phép (nếu biết bản), mọi URL nguồn, ghi chú đã chỉnh sửa.
    """
    assert _item(**overrides).attribution == expected


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "Bad_Id"},
        {"id": CATALOGUE[0].id},
        {"group": "garden"},
        {"model_sha256": "xyz"},
    ],
)
def test_load_rejects_bad_manifest(tmp_path: Path, patch: dict[str, str]) -> None:
    """Id sai mẫu, trùng `CATALOGUE`, nhóm lạ, sha sai → `ValueError` lúc nạp."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries[0] |= patch
    path = tmp_path / "files.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(ValueError, match=r"id|nhóm|sha256"):
        load_file_catalogue(path)


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    """Hai mục cùng id → `ValueError`."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    path = tmp_path / "files.json"
    path.write_text(json.dumps([entries[0], entries[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="trùng"):
        load_file_catalogue(path)


def test_every_cc_by_item_credits_source_license_and_modification() -> None:
    """891 mẫu CC BY của manifest: ghi công có URL nguồn, URL giấy phép CC và ghi chú đã chỉnh sửa (TASL)."""
    cc_by = [item for item in FILE_CATALOGUE if item.license.startswith("CC-BY")]
    assert len(cc_by) == 891
    for item in cc_by:
        parts = item.attribution.split(" · ")
        assert parts[-1] == MODIFIED_NOTE, item.id
        assert any(p.startswith("https://creativecommons.org/") for p in parts), item.id
        assert any(p.startswith("http") and "creativecommons.org" not in p for p in parts), item.id
