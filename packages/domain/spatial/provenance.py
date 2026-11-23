"""Suy cờ `edited` (H1): người đã đổi một thực thể AI thì đánh dấu, để lượt chạy lại giữ nó.

BE tự suy, không tin client: mục `source="ai"` trong lớp gửi lên có `edited=True` khi
client đã gửi cờ, khi id mới (người tạo mà gắn `ai`), khi mục cũ đã mang cờ (client
làm rơi trường), khi mục cũ do người vẽ (client hạ về `ai`, Quyết định 3A), hoặc khi
một trường theo dõi đổi. Mục `source="human"` giữ nguyên. Chỉ trường theo dõi của
nhật ký mới tính: đổi `reviewed`/`confidence` không phải sửa. Người gọi so **trước**
khi co giãn tỉ lệ, để co giãn không bị nhận nhầm là sửa.
"""

from collections.abc import Collection, Mapping, Sequence
from typing import Final

from packages.domain.spatial.diff import diff_layers
from packages.domain.spatial.kinds import vertex_id
from packages.domain.spatial.model import Dimension, Furniture, Opening, Reviewed, Roof, Room, SpatialLayer, Wall

_DIMENSION_FIELDS: Final = ("value_mm", "reference_ids", "override_value_mm")
"""Ba trường theo dõi của kích thước (HOP-DONG-MOI §1.3), cùng tập `writer._dimension_values` ghi nhật ký."""


def _marked[E: (Wall, Opening, Room, Furniture, Roof, Dimension)](
    entities: Sequence[E], before: Mapping[str, Reviewed], changed: Collection[str]
) -> tuple[E, ...]:
    """Các mục `entities` kèm cờ đã suy; mục người vẽ hay đã mang cờ trả nguyên."""

    def mark(entity: E) -> E:
        """Một mục: `edited=True` khi luật ở đầu tệp thoả, không thì trả nguyên."""
        if entity.source != "ai" or entity.edited:
            return entity
        previous = before.get(entity.id)
        if previous is None or previous.edited or previous.source == "human" or entity.id in changed:
            return entity.model_copy(update={"edited": True})
        return entity

    return tuple(mark(entity) for entity in entities)


def mark_edited(current: SpatialLayer, sent: SpatialLayer) -> SpatialLayer:
    """Lớp `sent` với cờ `edited` suy từ `current`; mục `vertex` của `diff_layers` quy về tường chủ."""
    owners = {key: wall.id for wall in sent.walls for key in (vertex_id(wall.id, "start"), vertex_id(wall.id, "end"))}
    # Nhận một gợi ý (gỡ `suggested`) là duyệt, không phải sửa (B3).
    changed = {
        owners.get(change.entity_id, change.entity_id)
        for change in diff_layers(current, sent)
        if change.field != "suggested"
    }
    before = {entity.id: entity for entity in current.entities()}
    return sent.model_copy(
        update={
            "walls": _marked(sent.walls, before, changed),
            "openings": _marked(sent.openings, before, changed),
            "rooms": _marked(sent.rooms, before, changed),
            "furniture": _marked(sent.furniture, before, changed),
            "roofs": _marked(sent.roofs, before, changed),
        }
    )


def clear_reviewed(layer: SpatialLayer) -> SpatialLayer:
    """Lớp `layer` với mọi thực thể bỏ dấu `reviewed` (L2-N03: thay bản vẽ khác → duyệt lại toàn tầng).

    Không đổi hình học, `source` hay `edited`: mục đã duyệt luôn là `source="human"` (A5) nên vẫn thuộc `K` của trộn
    (`merge_pipeline_result`), không bị lượt AI thay. Kích thước/trục không tính vào "tầng đã duyệt" nên giữ nguyên.
    """

    def plain[E: (Wall, Opening, Room, Furniture, Roof)](entities: Sequence[E]) -> tuple[E, ...]:
        """Bản không dấu duyệt của một danh sách; mục chưa duyệt trả nguyên."""
        return tuple(item.model_copy(update={"reviewed": False}) if item.reviewed else item for item in entities)

    return layer.model_copy(
        update={
            "walls": plain(layer.walls),
            "openings": plain(layer.openings),
            "rooms": plain(layer.rooms),
            "furniture": plain(layer.furniture),
            "roofs": plain(layer.roofs),
        }
    )


def _tracked(item: Dimension) -> tuple[object, ...]:
    """Giá trị các trường theo dõi của một kích thước; `line` không theo dõi (co giãn tỉ lệ đổi nó)."""
    return tuple(getattr(item, name) for name in _DIMENSION_FIELDS)


def mark_edited_dimensions(current: Sequence[Dimension], sent: Sequence[Dimension]) -> tuple[Dimension, ...]:
    """Kích thước `sent` với cờ `edited`, cùng luật `mark_edited` (đặt `overrideValueMm` là sửa)."""
    before = {item.id: item for item in current}
    changed = {item.id for item in sent if item.id in before and _tracked(item) != _tracked(before[item.id])}
    return _marked(sent, before, changed)
