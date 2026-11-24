"""Công tắc, hạn mức và mốc tuổi lịch quét của AI chọn phong cách (mẫu `apps/api/lighting_bake/settings.py`).

"Hàm worker nhập": `state.enqueue_auto` (worker `pipeline_quality`) và lịch quét đọc module này, nên chỉ
`pydantic_settings`. `DESIGN_DIRECTOR_VERTEX_SA_JSON` chỉ được đọc để biết "có khoá hay không";
`packages.genai` (agent G) mới dùng nội dung.
"""

import logging
from functools import cache
from typing import Final

from pydantic import PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_log: Final = logging.getLogger(__name__)


@cache
def _warn_disclosure_missing() -> None:
    """Một dòng log cho mỗi tiến trình: công tắc bật + có khoá nhưng chưa xác nhận đã thông báo (L6-N01)."""
    _log.warning(
        "design_director_disclosure_missing: DESIGN_DIRECTOR_ENABLED=true nhung chua dat "
        "DESIGN_DIRECTOR_DISCLOSURE_ACK=true; anh ban ve se gui toi Google Vertex AI nen tinh nang van tat"
    )


class DesignDirectorSettings(BaseSettings):
    """`enabled` mặc định tắt (công tắc quản trị); hạn mức tính theo chủ dự án mỗi ngày UTC."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    design_director_enabled: bool = False
    design_director_disclosure_ack: bool = False
    design_director_daily_quota: PositiveInt = 30
    design_director_vertex_sa_json: SecretStr | None = None
    design_director_resend_after_s: PositiveInt = 600
    design_director_queued_max_age_s: PositiveInt = 2 * 3600
    design_director_running_max_age_s: PositiveInt = 15 * 60

    @property
    def available(self) -> bool:
        """Bật công tắc, có khoá service account **và** đã xác nhận thông báo người dùng (L6-N01).

        Ảnh trang bản vẽ được gửi tới Google Vertex AI (Gemini); `DESIGN_DIRECTOR_DISCLOSURE_ACK=true`
        nghĩa là người vận hành đã báo người dùng. Thiếu một trong ba thì coi như tắt.
        """
        key = self.design_director_vertex_sa_json
        configured = self.design_director_enabled and key is not None and bool(key.get_secret_value().strip())
        if configured and not self.design_director_disclosure_ack:
            _warn_disclosure_missing()
            return False
        return configured


@cache
def get_design_director_settings() -> DesignDirectorSettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return DesignDirectorSettings()


def reset_design_director_settings_cache() -> None:
    """Chỉ cho test: đọc lại biến môi trường ở lần gọi sau."""
    get_design_director_settings.cache_clear()
