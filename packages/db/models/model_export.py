"""Bảng `model_exports`: các lượt xuất GLB dựng phía máy chủ của một dự án (M1-01, N49-N51).

`status` là vòng đời job (`queued|running|done|failed|superseded`); `quality` là chất lượng
kết quả (`ready|degraded|blocked`), chỉ có khi `done`. Chỉ mục duy nhất một phần
`uq_model_exports_fingerprint` giữ tối đa một dòng `queued|running|done` cho mỗi
`(project_id, fingerprint)`: hai yêu cầu đua cùng dấu vân chỉ tạo một job.
Tệp nằm trong kho object dưới `model_export_prefix(project_id, export_id)`.
"""

from datetime import datetime
from typing import Any, Final

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin
from packages.db.models.projects import PROJECTS

MODEL_EXPORTS: Final = "model_exports"
MODEL_EXPORT_STATUSES: Final = ("queued", "running", "done", "failed", "superseded")
MODEL_EXPORT_QUALITIES: Final = ("ready", "degraded", "blocked")


class ModelExportRow(Base, TimestampMixin):
    """Một lượt xuất; xoá cứng dự án thì dòng đi theo (CASCADE)."""

    __tablename__ = MODEL_EXPORTS

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    snapshot_sha: Mapped[str] = mapped_column(Text)
    snapshot_revisions: Mapped[Any] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    compiler_version: Mapped[str] = mapped_column(Text)
    pack_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(Text, default=None)
    failure_code: Mapped[str | None] = mapped_column(Text, default=None)
    report_summary: Mapped[Any | None] = mapped_column(JSONB, default=None)
    glb_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'superseded')", name="status_values"),
        CheckConstraint("quality IS NULL OR quality IN ('ready', 'degraded', 'blocked')", name="quality_values"),
        Index(f"ix_{MODEL_EXPORTS}_project_id_created_at", "project_id", "created_at"),
        Index(
            f"uq_{MODEL_EXPORTS}_fingerprint",
            "project_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'done')"),
        ),
    )
