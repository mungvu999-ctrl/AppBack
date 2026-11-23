"""L2-N03: luật readiness trên tầng **đã duyệt** khi pipeline chạy lại (kiểm trước của plan).

`level_reviewed` không phải cờ lưu: nó suy lúc đọc từ lớp (mọi mục `reviewed`) và hạng tỉ lệ (`human`). Chuỗi thật
của một lượt mới là trộn (`merge_pipeline_result`, giữ mục đã duyệt, thêm mục AI không trùng) → hạng tỉ lệ theo trang
(`effective_scale_source`) → `readiness_of`. Hàm thuần nên ghép thẳng, không DB.
"""

from collections.abc import Mapping
from dataclasses import replace

from apps.api.spatial_read.assemble import effective_scale_source, level_reviewed
from apps.api.spatial_read.documents import EMPTY_LAYER, empty_document
from apps.api.spatial_read.readiness import RunFacts, readiness_of
from packages.domain.rules_ai.merge import merge_pipeline_result
from packages.domain.spatial import SpatialLayer
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
OLD_PAGE, NEW_PAGE = "drawings/old/page-1.png", "drawings/new/page-1.png"


def _after_rerun(ai: SpatialLayer, *, scale_page_key: str | None, page_key: str) -> tuple[bool, Mapping[str, object]]:
    """Tầng đã duyệt (tỉ lệ người đặt trên `scale_page_key`) nhận lượt mới có lỗi critical trên trang `page_key`."""
    merged = merge_pipeline_result(REVIEWED, ai).layer
    doc = replace(empty_document(1), layer=merged, scale_source="human", scale_page_key=scale_page_key)
    source = effective_scale_source(doc, page_key)
    out = readiness_of(run=CRITICAL_RUN, scale_source=source, layer=merged)
    return level_reviewed(merged, source), out.model_dump(mode="json")


def test_baseline_reviewed_floor_hides_run_reasons() -> None:
    """Trước lượt mới: tầng đã duyệt, tỉ lệ người đặt → lý do cấp lượt tắt (thiết kế "tin người đã duyệt")."""
    out = readiness_of(run=CRITICAL_RUN, scale_source="human", layer=REVIEWED)
    assert (out.state, out.reasons) == ("ready", ())


def test_replaced_drawing_demotes_scale_and_reenables_rules() -> None:
    """Thay bản vẽ khác (khoá trang mới): tỉ lệ người đặt hết hiệu lực → tầng hết "đã duyệt" → luật bật lại."""
    for ai in (SAME_AI, EMPTY_AI, OTHER_AI):
        reviewed, out = _after_rerun(ai, scale_page_key=OLD_PAGE, page_key=NEW_PAGE)
        assert not reviewed
        assert out["state"] == "needsReview"
        assert out["reasons"] == ["scaleUnresolved", "criticalIssues"]


def test_new_unmatched_ai_entities_reenable_rules_on_same_page() -> None:
    """Cùng trang (chạy lại, hay tải lại đúng tệp cũ — tỉ lệ theo sang khoá mới): mục AI mới chưa duyệt → luật bật."""
    reviewed, out = _after_rerun(OTHER_AI, scale_page_key=OLD_PAGE, page_key=OLD_PAGE)
    assert not reviewed
    assert out["reasons"] == ["criticalIssues"]


def test_same_page_without_new_entities_stays_ready() -> None:
    """Cùng trang, lượt mới không thêm mục nào (trùng hết hay rỗng): lớp vẫn đúng là bản đã duyệt → vẫn "ready".

    Có chủ ý: hình học người dùng thấy không đổi, lỗi critical của lượt nằm ở phần AI đã bị bỏ.
    """
    for ai in (SAME_AI, EMPTY_AI):
        reviewed, out = _after_rerun(ai, scale_page_key=OLD_PAGE, page_key=OLD_PAGE)
        assert reviewed
        assert (out["state"], out["reasons"]) == ("ready", [])


def test_legacy_scale_without_page_key_keeps_ready_after_replacement() -> None:
    """Lỗ còn lại (chờ chủ dự án quyết): tỉ lệ người đặt chưa gắn trang (`scale_page_key` vắng — đặt trước khi có
    bản vẽ hay dữ liệu cũ) + thay bản vẽ khác mà lượt mới không thêm mục nào → vẫn "ready" dù lượt có lỗi critical."""
    for ai in (SAME_AI, EMPTY_AI):
        reviewed, out = _after_rerun(ai, scale_page_key=None, page_key=NEW_PAGE)
        assert reviewed
        assert (out["state"], out["reasons"]) == ("ready", [])
