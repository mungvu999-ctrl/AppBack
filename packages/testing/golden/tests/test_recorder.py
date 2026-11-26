"""Bộ ghi golden (B0-07 [8] "Bộ ghi", "Stash").

Test có tên dạng `test_<op>__<case>` đi qua **client thật** (`make_api_client`, cùng
`ObservedClient` và sổ observer của `api_client`) tới một app thử dựng bằng `create_app` thật
với router mẫu `/api/golden-probe/...`. Bộ ghi khớp thao tác trên bảng của app thử (thay
`resolve_operation`) và ghi vào thư mục tạm — mẫu của app thử không bao giờ lọt vào
`CONTRACT_SAMPLES_DIR` thật của lượt verify (H1 sẽ báo "không có trong bản đồ").
"""

import ast
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from starlette.responses import JSONResponse, Response, StreamingResponse

from apps.api.core.app import create_app
from apps.api.core.auth import FakeTokenVerifier
from apps.api.core.openapi import operations
from apps.api.core.routing import public_router
from packages.core.settings import get_core_settings
from packages.testing.fixtures import api as api_fixtures
from packages.testing.fixtures import golden as golden_fixtures
from packages.testing.fixtures.api import API_RESPONSE_OBSERVERS, make_api_client
from packages.testing.fixtures.clock import FakeClock
from packages.testing.golden import attach_context, record_stream_event, recorder
from tools.contract import check
from tools.contract.samples import HttpSample
from tools.contract.tests import wire

PROBE_PREFIX: Final = "/api/golden-probe"
probe = public_router(prefix="/golden-probe", tags=["golden-probe"])


@probe.get("/items/{item_id}", name="golden_read_item")
async def golden_read_item(item_id: str) -> dict[str, str]:
    """Route mẫu có tham số đường."""
    return {"id": item_id, "at": wire.AT}


@probe.get("/other", name="golden_read_other")
async def golden_read_other() -> dict[str, str]:
    """Thao tác khác trong cùng test: không được ghi."""
    return {"id": "khac"}


@probe.get("/boom", name="golden_boom", response_model=None)
async def golden_boom() -> Response:
    """500: không ghi."""
    return JSONResponse({"code": "INTERNAL", "requestId": "rid-1"}, status_code=500)


@probe.get("/stream", name="golden_open_stream", response_model=None)
async def golden_open_stream() -> Response:
    """`text/event-stream`: không ghi, không đọc."""
    return StreamingResponse(iter([b"id: 1\ndata: {}\n\n"]), media_type="text/event-stream")


@probe.post("/token", name="golden_issue_token")
async def golden_issue_token() -> dict[str, Any]:
    """Thân W16 có `accessToken` thật (dựng lúc chạy)."""
    return wire.refresh()


@pytest.fixture
def samples_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`CONTRACT_SAMPLES_DIR` của riêng test."""
    root = tmp_path / "mau"
    monkeypatch.setenv(recorder.SAMPLES_ENV, str(root))
    return root


@pytest.fixture
def probe_app(api_env: None, fake_clock: FakeClock, samples_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """App thử của `create_app` thật; bộ ghi khớp thao tác trên bảng của chính app này."""
    app = create_app(
        get_core_settings(),
        token_verifier=FakeTokenVerifier(),
        clock=fake_clock,
        routers=[("apps.api.golden_probe.router", probe)],
    )
    monkeypatch.setattr(recorder, "resolve_operation", recorder.operation_resolver(operations(app)))
    return app


@pytest_asyncio.fixture(loop_scope="function")
async def probe_client(probe_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client ASGI thật (observer của B0-06), đã chạy `lifespan`."""
    async with make_api_client(probe_app) as client:
        yield client


def written(root: Path) -> list[str]:
    """Mọi file dưới thư mục mẫu, tương đối, đã sắp."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def load(root: Path, rel: str) -> dict[str, Any]:
    """Một mẫu đã ghi."""
    data: dict[str, Any] = json.loads((root / rel).read_text(encoding="utf-8"))
    return data


# -- Qua client thật -----------------------------------------------------------------------------


async def test_golden_read_item__C01_named(probe_client: httpx.AsyncClient, samples_dir: Path) -> None:
    """Đúng tên file theo thứ tự trong test; thao tác khác trong cùng test không ghi; không ghi header."""
    first = await probe_client.get(f"{PROBE_PREFIX}/items/abc", params={"q": "1"}, headers={"X-Client-Version": "9"})
    await probe_client.get(f"{PROBE_PREFIX}/other")
    await probe_client.get(f"{PROBE_PREFIX}/items/xyz")
    attach_context(first, floorOrder=["L-A"])
    assert written(samples_dir) == ["golden_read_item/C01_named-1.json", "golden_read_item/C01_named-2.json"]
    assert load(samples_dir, "golden_read_item/C01_named-1.json") == {
        "operationId": "golden_read_item",
        "case": "C01",
        "method": "GET",
        "pathTemplate": "/api/golden-probe/items/{item_id}",
        "pathParams": {"item_id": "abc"},
        "query": {"q": "1"},
        "status": 200,
        "contentType": "application/json",
        "body": {"id": "abc", "at": wire.AT},
        "context": {"floorOrder": ["L-A"]},
    }


async def test_golden_boom__C01(probe_client: httpx.AsyncClient, samples_dir: Path) -> None:
    """Status ≥ 500 không ghi."""
    assert (await probe_client.get(f"{PROBE_PREFIX}/boom")).status_code == 500
    assert written(samples_dir) == []


async def test_golden_open_stream__C01(probe_client: httpx.AsyncClient, samples_dir: Path) -> None:
    """`text/event-stream` không ghi; `attach_context` trên response không ghi → lỗi rõ ràng."""
    response = await probe_client.get(f"{PROBE_PREFIX}/stream")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert written(samples_dir) == []
    with pytest.raises(ValueError, match="không được bộ ghi golden ghi"):
        attach_context(response, floorOrder=[])


@pytest.mark.parametrize("op", ["golden_read_item"])
async def test_common__C04(probe_client: httpx.AsyncClient, samples_dir: Path, op: str) -> None:
    """`test_common__C04[op]` ghi vào `op/C04-1.json` (test chung tham số hoá theo route)."""
    await probe_client.get(f"{PROBE_PREFIX}/items/abc")
    assert written(samples_dir) == [f"{op}/C04-1.json"]


async def test_golden_read_item__C08_outside_gate(
    probe_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không có `CONTRACT_SAMPLES_DIR` (chạy pytest tay) → không ghi, `attach_context` không làm gì."""
    monkeypatch.delenv(recorder.SAMPLES_ENV)
    response = await probe_client.get(f"{PROBE_PREFIX}/items/abc")
    attach_context(response, floorOrder=[])
    assert recorder._written.get(response) is None


async def test_golden_issue_token__C01(
    probe_client: httpx.AsyncClient, samples_dir: Path, contract_build: Path
) -> None:
    """`accessToken` bị thay bằng chuỗi giả cùng dạng: token thật không chạm đĩa, mẫu vẫn qua `refresh.ts`."""
    response = await probe_client.post(f"{PROBE_PREFIX}/token")
    real_token = response.json()["accessToken"]
    text = (samples_dir / "golden_issue_token" / "C01-1.json").read_text(encoding="utf-8")
    assert real_token not in text
    body = json.loads(text)["body"]
    assert [len(part) for part in body["accessToken"].split(".")] == [len(part) for part in real_token.split(".")]
    http, _ = check.decode(contract_build, [HttpSample("t", "auth_refresh", 200, body)], [])
    assert http[0]["decode"] == "ok", http[0]


def test_stash_holds_exactly_one_golden_recorder(request: pytest.FixtureRequest) -> None:
    """Sổ observer của phiên test thật có đúng một bộ ghi golden, cạnh bộ ghi vết case."""
    observers = request.config.stash[API_RESPONSE_OBSERVERS]
    assert observers.count(recorder.record_response) == 1
    assert api_fixtures.trace_case in observers


@pytest.mark.parametrize("golden_first", [True, False], ids=["golden-trước", "api-trước"])
def test_both_plugin_orders_register_one_recorder(golden_first: bool) -> None:
    """Cả hai thứ tự nạp `api.py`/`golden.py`, kể cả gọi lặp, đều ra đúng một bộ ghi mỗi loại."""
    config: Any = SimpleNamespace(stash=pytest.Stash())
    hooks = [golden_fixtures.pytest_configure, api_fixtures.pytest_configure]
    for hook in (hooks if golden_first else hooks[::-1]) * 2:
        hook(config)
    observers = config.stash[API_RESPONSE_OBSERVERS]
    assert observers.count(recorder.record_response) == 1
    assert observers.count(api_fixtures.trace_case) == 1


# -- Hàm của bộ ghi ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test_projects_read_project__C01", ("projects_read_project", "C01", "C01")),
        ("test_projects_read_project__C09_missing", ("projects_read_project", "C09", "C09_missing")),
        ("test_projects_read_project__C09b", ("projects_read_project", "C09b", "C09b")),
        ("test_floors_list_floors__C15[3 tầng/limit]", ("floors_list_floors", "C15", "C15_3_t_ng_limit")),
        ("test_common__C04[auth_logout]", ("auth_logout", "C04", "C04")),
        ("test_health_live_is_public", None),
        ("test_segment_walls__J06", ("segment_walls", "J06", "J06")),
    ],
)
def test_split_test_name_follows_case_gate(name: str, expected: tuple[str, str, str] | None) -> None:
    """Tên test tách đúng như `split_case_test_name` (cổng case); hậu tố chỉ giữ ký tự an toàn cho tên file."""
    assert recorder.split_test_name(name) == expected


def test_recorder_imports_no_private_name_of_case_gate() -> None:
    """Bộ ghi chỉ nhập tên công khai của `packages.core.case_names` (NO-049, NO-337): đổi tên nội bộ không hỏng."""
    tree = ast.parse(Path(recorder.__file__).read_text(encoding="utf-8"))
    names = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "packages.core.case_names"
        for alias in node.names
    ]
    assert names
    assert [name for name in names if name.startswith("_")] == []


def test_mask_tokens_keeps_shape_at_any_depth() -> None:
    """Mọi `accessToken` chuỗi, ở mọi độ sâu, thành `A` cùng độ dài từng đoạn; phần khác giữ nguyên."""
    value = {"accessToken": "ab.c.de", "list": [{"accessToken": "xyz"}, {"accessToken": 5}], "n": 1}
    assert recorder.mask_tokens(value) == {
        "accessToken": "AA.A.AA",
        "list": [{"accessToken": "AAA"}, {"accessToken": 5}],
        "n": 1,
    }


def test_write_sample_never_overwrites(tmp_path: Path) -> None:
    """Tên đã có (tiến trình khác, test cùng tên ở file khác) → số kế tiếp; không để lại file `.part`."""
    (tmp_path / "C01-1.json").write_text("{}", encoding="utf-8")
    assert recorder.write_sample(tmp_path, "C01", {"a": 1}).name == "C01-2.json"
    assert recorder.write_sample(tmp_path, "C01", {"a": 2}).name == "C01-3.json"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["C01-1.json", "C01-2.json", "C01-3.json"]


def test_record_response_ignores_names_and_foreign_paths(samples_dir: Path) -> None:
    """Tên không phải test case, hay đường không thuộc app thật → không ghi."""
    request = httpx.Request("GET", "https://testserver/api/sample/public")
    response = httpx.Response(200, json={"id": "x"}, request=request)
    for name in ("test_khong_phai_case", "test_health_live__C01"):
        item: Any = SimpleNamespace(name=name)
        recorder.record_response(item, response)
    assert not samples_dir.exists()


def test_record_response_keeps_non_json_and_empty_bodies(samples_dir: Path) -> None:
    """Thân rỗng → `null`; thân không phải JSON giữ nguyên chuỗi (H1 sẽ báo hỏng); khớp đường app thật."""
    item: Any = SimpleNamespace(name="test_health_live__C01")
    for content in (b"", b"van ban"):
        request = httpx.Request("GET", "https://testserver/api/health")
        recorder.record_response(item, httpx.Response(200, content=content, request=request))
    bodies = [load(samples_dir, rel)["body"] for rel in written(samples_dir)]
    assert bodies == [None, "van ban"]


def test_resolver_needs_the_same_method() -> None:
    """Cùng đường mà khác method → không phải thao tác đó."""
    assert recorder.resolve_operation("POST", "/api/health") is None
    assert recorder.resolve_operation("get", "/api/health") == recorder.OperationMatch("health_live", "/api/health", {})


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/health"), ("GET", "/api/files/token-bat-ky"), ("POST", "/api/health"), ("GET", "/api/khong-co")],
)
def test_case_trace_and_golden_resolve_the_same_operation(method: str, path: str) -> None:
    """FIX-028: vết case (B0-06) và bộ ghi golden quy một request về **cùng** thao tác."""
    matched = recorder.resolve_operation(method, path)
    assert api_fixtures.operation_of(method, path) == (None if matched is None else matched.op)


def test_case_trace_asks_the_golden_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-028: `operation_of` hỏi đúng bộ khớp của bộ ghi, không giữ bảng khớp riêng (R-07)."""
    monkeypatch.setattr(recorder, "_real_resolver", lambda: lambda _m, p: recorder.OperationMatch("golden_x", p, {}))
    assert api_fixtures.operation_of("GET", "/bat-ky") == "golden_x"


def test_probe_table_does_not_leak_into_case_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test bộ ghi thay bảng khớp bằng bảng app thử; vết case vẫn chỉ biết app thật (FIX-028)."""
    monkeypatch.setattr(recorder, "resolve_operation", lambda _m, p: recorder.OperationMatch("golden_x", p, {}))
    assert api_fixtures.operation_of("GET", "/api/khong-co") is None


def test_record_stream_event_writes_frames(samples_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Khung SSE → `<op>/<case>-event-<n>.json`; tên lạ → `ValueError`; ngoài cổng → không ghi."""
    record_stream_event("streams_open_progress", "S03", {"id": "u"})
    record_stream_event("streams_open_progress", "S03", {"id": "v"})
    assert written(samples_dir) == [
        "streams_open_progress/S03-event-1.json",
        "streams_open_progress/S03-event-2.json",
    ]
    assert load(samples_dir, "streams_open_progress/S03-event-2.json") == {
        "operationId": "streams_open_progress",
        "case": "S03",
        "event": {"id": "v"},
    }
    with pytest.raises(ValueError, match="op/case"):
        record_stream_event("../x", "S03", {})
    monkeypatch.delenv(recorder.SAMPLES_ENV)
    record_stream_event("streams_open_progress", "S03", {"id": "w"})
    assert len(written(samples_dir)) == 2


def test_appfront_fixture_fails_instead_of_skipping(tmp_path: Path) -> None:
    """Thư mục AppFront thiếu `common.ts` → test **hỏng** (`pytest.fail`), không bỏ qua."""
    with pytest.raises(pytest.fail.Exception, match="thiếu F-00a"):
        golden_fixtures.require_appfront(tmp_path)


def test_appfront_fixture_returns_the_directory(appfront_dir: Path) -> None:
    """AppFront @ SHA có F-00a."""
    assert golden_fixtures.require_appfront(appfront_dir) == appfront_dir
