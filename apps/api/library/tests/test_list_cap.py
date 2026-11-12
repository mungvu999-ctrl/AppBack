"""Trần #14 phải chứa hết danh mục: mục xếp sau trần không bao giờ tới editor (12/11/2026: 978 mẫu, trần cũ 500)."""

from apps.api.library.settings import LibrarySettings
from packages.domain.library import CATALOGUE, FILE_CATALOGUE


def test_library_list_max__default_covers_whole_catalogue() -> None:
    assert len(CATALOGUE) + len(FILE_CATALOGUE) <= LibrarySettings().library_list_max
