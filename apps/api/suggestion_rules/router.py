"""`GET /api/suggestion-rules` — bảng tham số luật đề xuất M2 (HOP-DONG-DU-LIEU-LOP-DE-XUAT §5).

FE đọc bảng này thay cho hằng tự giữ (G1) để luật "tự chạy nhẹ" ra đúng số của máy chủ. Mọi người đã đăng nhập
đọc được (Loại Đ*, khoá `—`), không gắn dự án. `ETag` mạnh **là** `rulesetSha`: bảng chỉ đổi khi deploy, nên FE hỏi
lại bằng `If-None-Match` và nhận 304 không thân. Thân dựng một lần mỗi tiến trình (`functools.cache`), vì
`load_ruleset` đã cache và `rule_params.json` không đổi khi tiến trình đang chạy.
"""

from functools import cache
from typing import Annotated, Final, Literal

from pydantic import Field, JsonValue
from starlette.requests import Request
from starlette.responses import Response

from apps.api.core.routing import protected_router
from apps.api.core.wire import WireModel
from apps.api.spatial_read.http_cache import CACHE_CONTROL, etag_matches
from packages.domain.suggestions import load_ruleset

router = protected_router(tags=["suggestions"])
ROUTERS: Final = (router,)


class SuggestionRuleOut(WireModel):
    """Một luật của bảng: phiên bản, loại đề xuất, tên tiếng Việt, cách editor dùng, tham số (mm) và khoá chỉnh được."""

    id: str
    version: Annotated[int, Field(ge=1)]
    major_since: Annotated[int, Field(ge=1)]
    kind: str
    name_vi: str
    editor: Literal["render", "mirror", "list"]
    params: dict[str, JsonValue]
    overridable: list[str]


class SuggestionRulesOut(WireModel):
    """Cả bảng: tên bộ luật, sha của JSON chuẩn hoá (cũng là `ETag`) và các luật theo thứ tự tệp."""

    ruleset: str
    ruleset_sha: str
    rules: list[SuggestionRuleOut]


@cache
def _body() -> tuple[bytes, str]:
    """Thân JSON đã serialize và `ETag` của bảng đang nạp."""
    ruleset = load_ruleset()
    out = SuggestionRulesOut(
        ruleset=ruleset.ruleset,
        ruleset_sha=ruleset.sha,
        rules=[
            SuggestionRuleOut(
                id=rule_id,
                version=spec.version,
                major_since=spec.major_since,
                kind=spec.kind,
                name_vi=spec.name_vi,
                editor=spec.editor,
                params=spec.params,
                overridable=list(spec.overridable),
            )
            for rule_id, spec in ruleset.rules.items()
        ],
    )
    return out.model_dump_json(by_alias=True).encode(), f'"{ruleset.sha}"'


@router.get("/suggestion-rules", response_model=SuggestionRulesOut)
async def suggestion_rules_read(request: Request) -> Response:
    """Bảng luật đề xuất (router bảo vệ nên chỉ cần đã đăng nhập); `If-None-Match` khớp `rulesetSha` → 304."""
    body, etag = _body()
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
