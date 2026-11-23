"""L2-N03: luật readiness trên tầng **đã duyệt** khi pipeline chạy lại (chủ dự án chọn phương án 2).

`level_reviewed` không phải cờ lưu: nó suy lúc đọc từ lớp (mọi mục `reviewed`) và hạng tỉ lệ (`human`). Thay bản vẽ
khác: `upsert_drawing` gắn tỉ lệ chưa gắn trang vào trang cũ (`_bind_scale_page`), rồi lượt trộn trên trang khác bỏ dấu
duyệt mọi mục (`write_layer` → `clear_reviewed`) và hạ tỉ lệ về của pipeline. Ở đây ghép đúng ba luật ấy với trộn
(`merge_pipeline_result`) và `readiness_of` — hàm thuần, không DB; phần DB kiểm ở `test_drawings.py`
(`unbound`) và `test_writer_scale.py` (`test_merge_on_other_page_clears_review_marks`).
"""

from collections.abc import Mapping
from dataclasses import replace

from apps.api.spatial_read.assemble import effective_scale_source, level_reviewed
from apps.api.spatial_read.documents import EMPTY_LAYER, ScaleSource, empty_document
from apps.api.spatial_read.readiness import RunFacts, readiness_of
from packages.domain.rules_ai.merge import merge_pipeline_result
from packages.domain.spatial import SpatialLayer, clear_reviewed
from packages.testing.factories.spatial import sample_floor_layer

LEVEL_ID = "L-LEVEL000000"
REVIEWED = sample_floor_layer(0, level_id=LEVEL_ID, reviewed=True)


def _as_ai(layer: SpatialLayer) -> SpatialLayer:
    """Lớp mẫu thành đầu ra pipeline hợp lệ cho trộn: mọi mục `source="ai"`, chưa duyệt, chưa sửa."""
    raw = layer.model_dump(mode="json", by_alias=True, exclude_none=True)
    plain = {"source": "ai", "reviewed": False, "edited": False}
    return SpatialLayer.model_validate({name: [item | plain for item in items] for name, items in raw.items()})


SAME_AI = _as_ai(sample_floor_layer(0, level_id=LEVEL_ID, id_suffix="AI"))
"""Lượt mới đọc ra đúng hình học đã duyệt: mọi mục AI trùng mục đã duyệt nên bị bỏ."""
OTHER_AI = _as_ai(sample_floor_layer(1, level_id=LEVEL_ID, id_suffix="AI"))
"""Lượt mới đọc ra hình học khác: có mục AI không trùng, được thêm vào lớp ở dạng chưa duyệt."""
EMPTY_AI = EMPTY_LAYER
"""Lượt hỏng không đọc ra gì (vd mất ảnh trang, L2-N02)."""
CRITICAL_RUN = RunFacts(pinned={}, quality_summary={"hasCritical": True, "criticalCount": 1, "lowConfidence": {}})
CLEAN_RUN = RunFacts(pinned={}, quality_summary={"hasCritical": False, "criticalCount": 0, "lowConfidence": {}})
OLD_PAGE, NEW_PAGE = "drawings/old/page-1.png", "drawings/new/page-1.png"


def _readiness(layer: SpatialLayer, source: ScaleSource, run: RunFacts = CRITICAL_RUN) -> Mapping[str, object]:
    """Readiness dạng dây của một lớp với nguồn tỉ lệ đã chấm hạng."""
    return readiness_of(run=run, scale_source=source, layer=layer).model_dump(mode="json")


def _after_rerun(ai: SpatialLayer, *, scale_page_key: str | None, page_key: str) -> tuple[SpatialLayer, ScaleSource]:
    """Tầng đã duyệt (tỉ lệ người đặt, gắn `scale_page_key`) nhận lượt mới trên trang `page_key`: (lớp, nguồn tỉ lệ).

    Bản vẽ khác (`page_key` khác trang cũ): tỉ lệ chưa gắn trang được gắn vào trang cũ; trộn trên trang khác bỏ dấu
    duyệt rồi tỉ lệ thành của pipeline. Cùng trang: trộn bình thường, tỉ lệ người đặt giữ hạng.
    """
    bound = OLD_PAGE if scale_page_key is None and page_key != OLD_PAGE else scale_page_key
    other_page = bound is not None and bound != page_key
    base = clear_reviewed(REVIEWED) if other_page else REVIEWED
    merged = merge_pipeline_result(base, ai).layer
    if other_page:
        return merged, "pipeline"
    doc = replace(empty_document(1), layer=merged, scale_source="human", scale_page_key=bound)
    return merged, effective_scale_source(doc, page_key)


def _review_all(layer: SpatialLayer) -> SpatialLayer:
    """Người dùng duyệt lại toàn tầng: mọi mục `reviewed`, `source="human"` (A5)."""
    raw = layer.model_dump(mode="json", by_alias=True, exclude_none=True)
    human = {"source": "human", "reviewed": True}
    return SpatialLayer.model_validate({name: [item | human for item in items] for name, items in raw.items()})


def test_baseline_reviewed_floor_hides_run_reasons() -> None:
    """Trước lượt mới: tầng đã duyệt, tỉ lệ người đặt → lý do cấp lượt tắt (thiết kế "tin người đã duyệt")."""
    assert _readiness(REVIEWED, "human") == {"state": "ready", "reasons": []}


def test_replaced_drawing_loses_review_even_without_new_entities() -> None:
    """Thay bản vẽ khác: mọi mục mất dấu duyệt (kể cả khi lượt mới trùng hết hay rỗng), tỉ lệ thành của pipeline →
    tầng hết "đã duyệt", lý do của lượt hiện lại; tỉ lệ người đặt chưa gắn trang cũng vậy (NO-505)."""
    for scale_page_key in (OLD_PAGE, None):
        for ai in (SAME_AI, EMPTY_AI, OTHER_AI):
            layer, source = _after_rerun(ai, scale_page_key=scale_page_key, page_key=NEW_PAGE)
            assert not any(entity.reviewed for entity in layer.entities())
            assert {entity.id for entity in REVIEWED.entities()} <= {entity.id for entity in layer.entities()}
            assert not level_reviewed(layer, source)
            assert _readiness(layer, source)["reasons"] == ["scaleUnresolved", "criticalIssues"]


def test_replaced_drawing_before_new_run_completes_is_not_reviewed() -> None:
    """Lượt mới chưa xong (hay hỏng): lớp vẫn mang dấu duyệt nhưng tỉ lệ người đặt gắn trang cũ hết hiệu lực
    (`_bind_scale_page` gắn cả tỉ lệ chưa gắn trang) → tầng không còn "đã duyệt"."""
    doc = replace(empty_document(1), layer=REVIEWED, scale_source="human", scale_page_key=OLD_PAGE)
    source = effective_scale_source(doc, NEW_PAGE)
    assert source == "pipeline"
    assert not level_reviewed(REVIEWED, source)


def test_rerun_on_same_page_keeps_review() -> None:
    """Chạy lại cùng trang (hay tải lại đúng tệp, trang, vùng cắt — tỉ lệ theo sang khoá mới): lượt không thêm mục
    nào thì giữ duyệt và "ready"; lượt có mục AI mới chưa duyệt thì luật bật lại."""
    for ai in (SAME_AI, EMPTY_AI):
        layer, source = _after_rerun(ai, scale_page_key=OLD_PAGE, page_key=OLD_PAGE)
        assert level_reviewed(layer, source)
        assert _readiness(layer, source) == {"state": "ready", "reasons": []}
    layer, source = _after_rerun(OTHER_AI, scale_page_key=OLD_PAGE, page_key=OLD_PAGE)
    assert _readiness(layer, source)["reasons"] == ["criticalIssues"]


def test_re_review_after_replacement_is_ready() -> None:
    """Sau khi thay bản vẽ, người dùng duyệt lại toàn tầng và đặt tỉ lệ trên trang mới → "ready"."""
    layer, _ = _after_rerun(OTHER_AI, scale_page_key=OLD_PAGE, page_key=NEW_PAGE)
    again = _review_all(layer)
    doc = replace(empty_document(1), layer=again, scale_source="human", scale_page_key=NEW_PAGE)
    source = effective_scale_source(doc, NEW_PAGE)
    assert level_reviewed(again, source)
    assert _readiness(again, source, CLEAN_RUN) == {"state": "ready", "reasons": []}
