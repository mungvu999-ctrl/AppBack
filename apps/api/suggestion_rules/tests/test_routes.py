"""`suggestion_rules_read` qua app thật: C01 (thân, `ETag` = `rulesetSha`) và 304 khi `If-None-Match` khớp."""

from typing import Final

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.projects.tests.test_routes_common import headers_of
from packages.domain.suggestions import load_ruleset
from packages.testing.factories.auth import make_user

PATH: Final = "/api/suggestion-rules"
RULE_KEYS: Final = {"id", "version", "majorSince", "kind", "nameVi", "editor", "params", "overridable"}


async def test_suggestion_rules_read__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bất kỳ ai đã đăng nhập: cả bảng theo thứ tự tệp, đúng khoá dây; `ETag` mạnh là `rulesetSha`."""
    user = await make_user(db_session)
    ruleset = load_ruleset()

    response = await api_client.get(PATH, headers=headers_of(user))

    assert response.status_code == 200
    assert response.headers["etag"] == f'"{ruleset.sha}"'
    assert response.headers["cache-control"] == "private, no-cache"
    body = response.json()
    assert (body["ruleset"], body["rulesetSha"]) == (ruleset.ruleset, ruleset.sha)
    assert [rule["id"] for rule in body["rules"]] == list(ruleset.rules)
    assert all(set(rule) == RULE_KEYS for rule in body["rules"])
    skirting = body["rules"][0]
    assert (skirting["id"], skirting["kind"], skirting["params"]["heightMm"]) == ("R1", "skirting", 90)
    assert skirting["overridable"] == ["heightMm", "thicknessMm", "material"]


async def test_suggestion_rules_read__not_modified(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`If-None-Match` khớp (kể cả dạng yếu `W/`) → 304 không thân, giữ `ETag`; sha khác → 200 đủ thân."""
    user = await make_user(db_session)
    etag = f'"{load_ruleset().sha}"'

    for tag in (etag, f"W/{etag}", f'"other", {etag}'):
        response = await api_client.get(PATH, headers={**headers_of(user), "If-None-Match": tag})
        assert (response.status_code, response.content, response.headers["etag"]) == (304, b"", etag)
    fresh = await api_client.get(PATH, headers={**headers_of(user), "If-None-Match": '"0"'})
    assert fresh.status_code == 200
    assert fresh.json()["rulesetSha"] == load_ruleset().sha
