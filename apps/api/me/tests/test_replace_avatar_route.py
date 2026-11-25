"""`PUT /api/me/avatar` — N14 `me_replace_avatar` (B1-04 [6], [8]; CASE T-avatar: C01 C02 C03 U03 U07 U08)."""

import io
from contextlib import suppress

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.tests.support import user_row
from apps.api.core.app import create_app
from apps.api.core.auth import FakeTokenVerifier
from apps.api.me.router import AVATAR_RATE_LIMIT
from apps.api.me.tests.support import b64, headers_of, jpeg_bytes, png_16bit_bytes, png_bytes, principal_of
from packages.core.settings import get_core_settings
from packages.db.models.auth import User
from packages.storage.settings import reset_storage_settings_cache
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.services import ephemeral_minio

pytestmark = pytest.mark.usefixtures("api_env")

PATH = "/api/me/avatar"


@pytest.fixture(scope="module")
def png_16bit_4096_b64() -> str:
    """Base64 của ảnh 4096x4096 16-bit — dựng một lần cho cả module (nén nhanh vì một màu)."""
    return b64(png_16bit_bytes((4096, 4096)))


async def _put(client: httpx.AsyncClient, user: User, body: dict[str, object]) -> httpx.Response:
    """Gửi N14 `PUT /api/me/avatar` với thân `body`, xác thực bằng phiên của `user`."""
    return await client.put(PATH, json=body, headers=headers_of(principal_of(user)))


async def test_me_replace_avatar__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """PNG 64x64 hợp lệ → 200, `avatarUrl` tuyệt đối, object mới ghi bền vững.

    Catalog: B07-G-050.
    """
    user = await make_user(db_session)
    response = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": b64(png_bytes((64, 64)))})
    assert response.status_code == 200
    body = response.json()
    assert body["avatarUrl"].startswith("https://")

    reread = await api_client.get("/api/me", headers=headers_of(principal_of(user)))
    assert reread.json()["avatarUrl"] == body["avatarUrl"]


async def test_me_replace_avatar_jpeg_ok(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """JPEG 64x64 hợp lệ → 200.

    Catalog: B07-G-053.
    """
    user = await make_user(db_session)
    response = await _put(api_client, user, {"mimeType": "image/jpeg", "contentBase64": b64(jpeg_bytes((64, 64)))})
    assert response.status_code == 200


async def test_me_replace_avatar_replaces_key_each_time(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Thay ảnh hai lần → hai `avatarUrl` khác nhau (khoá mới mỗi lần, không ghi đè tại chỗ).

    Catalog: B07-G-054.
    """
    user = await make_user(db_session)
    first = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": b64(png_bytes((64, 64)))})
    second = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": b64(png_bytes((32, 32)))})
    assert first.json()["avatarUrl"] != second.json()["avatarUrl"]


@pytest.mark.parametrize(
    "bad_body",
    [{"mimeType": "image/gif", "contentBase64": b64(png_bytes())}, {"contentBase64": b64(png_bytes())}],
)
async def test_me_replace_avatar__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, bad_body: dict[str, object]
) -> None:
    """`mimeType` ngoài enum hay thiếu khoá bắt buộc → 422 `VALIDATION`."""
    user = await make_user(db_session)
    response = await _put(api_client, user, bad_body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


async def test_me_replace_avatar__C03(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khoá lạ trong thân → 422 `VALIDATION`.

    Catalog: B07-G-075.
    """
    user = await make_user(db_session)
    body = {"mimeType": "image/png", "contentBase64": b64(png_bytes()), "extra": 1}
    response = await _put(api_client, user, body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


async def test_me_replace_avatar__U03_dimensions_bomb(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Ảnh quá nhiều điểm ảnh/kích thước → 422 `AVATAR_DIMENSIONS_EXCEEDED`, không cạn bộ nhớ.

    Catalog: B07-G-070.
    """
    user = await make_user(db_session)
    response = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": b64(png_bytes((5000, 10)))})
    assert response.status_code == 422
    assert response.json()["code"] == "AVATAR_DIMENSIONS_EXCEEDED"


async def test_me_replace_avatar__U07_truncated_file(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tệp cụt → 422 `FILE_CORRUPT`.

    Catalog: B07-G-074.
    """
    user = await make_user(db_session)
    truncated = jpeg_bytes((64, 64))[:16]
    response = await _put(api_client, user, {"mimeType": "image/jpeg", "contentBase64": b64(truncated)})
    assert response.status_code == 422
    assert response.json()["code"] == "FILE_CORRUPT"


async def test_me_replace_avatar__U08_magic_bytes_mismatch(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """PNG thật khai `mimeType: image/jpeg` → 422 `FILE_TYPE_MISMATCH` (không tin đuôi/`mimeType`).

    Catalog: B07-G-066.
    """
    user = await make_user(db_session)
    response = await _put(api_client, user, {"mimeType": "image/jpeg", "contentBase64": b64(png_bytes())})
    assert response.status_code == 422
    assert response.json()["code"] == "FILE_TYPE_MISMATCH"


async def test_me_replace_avatar_gif_is_avatar_type_unsupported(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """GIF → 422 `AVATAR_TYPE_UNSUPPORTED`.

    Catalog: B07-G-068.
    """
    user = await make_user(db_session)
    gif = b"GIF89a" + b"\x00" * 58
    response = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": b64(gif)})
    assert response.status_code == 422
    assert response.json()["code"] == "AVATAR_TYPE_UNSUPPORTED"


async def test_me_replace_avatar__C11(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """C11: 11 lượt trong 15 phút → 429 `RATE_LIMITED` (`AVATAR_RATE_LIMIT` = 10; `_FIXED_EXTRA` của cổng case).

    Catalog: B07-G-079.
    """
    user = await make_user(db_session)
    body: dict[str, object] = {"mimeType": "image/png", "contentBase64": b64(png_bytes((8, 8)))}
    for _ in range(AVATAR_RATE_LIMIT):
        response = await _put(api_client, user, body)
        assert response.status_code == 200
    blocked = await _put(api_client, user, body)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"


async def test_me_replace_avatar_session_revoked_when_user_deleted(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Người của `Principal` đã xoá mềm → 401 (object đã `put` thành mồ côi, lịch dọn xử lý — bước 7).

    Catalog: B07-G-042.
    """
    user = await make_user(db_session)
    user.deleted_at = user.created_at
    await db_session.commit()
    response = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": b64(png_bytes())})
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_REVOKED"


async def test_me_replace_avatar_png_4096_16bit_downsizes_to_8bit(
    api_client: httpx.AsyncClient,
    api_app: FastAPI,
    db_session: AsyncSession,
    png_16bit_4096_b64: str,
) -> None:
    """PNG 4096x4096 16-bit → 200, ảnh lưu ≤ 512x512, 8-bit `RGB`/`RGBA` (NO-167).

    Catalog: B07-G-058.
    """
    user = await make_user(db_session)
    response = await _put(api_client, user, {"mimeType": "image/png", "contentBase64": png_16bit_4096_b64})
    assert response.status_code == 200

    row = await user_row(db_session, user.id)
    assert row.avatar_key is not None
    chunks = [chunk async for chunk in api_app.state.storage.open_read(row.avatar_key)]
    with Image.open(io.BytesIO(b"".join(chunks))) as saved:
        assert saved.width <= 512
        assert saved.height <= 512
        assert saved.mode in ("RGB", "RGBA")


async def test_me_replace_avatar_idempotency_key_repeat_creates_one_object(
    api_client: httpx.AsyncClient, api_app: FastAPI, db_session: AsyncSession
) -> None:
    """Lặp cùng `Idempotency-Key` → response giống hệt, **một** object mới dưới `users/{u}/avatar/` (NO-167, CON-02).

    Catalog: B07-G-076.
    """
    user = await make_user(db_session)
    headers = {**headers_of(principal_of(user)), "Idempotency-Key": "test-avatar-idem-key-001"}
    body = {"mimeType": "image/png", "contentBase64": b64(png_bytes((16, 16)))}

    first = await api_client.put(PATH, json=body, headers=headers)
    second = await api_client.put(PATH, json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    prefix = f"users/{user.id}/avatar/"
    objects = [info async for info in api_app.state.storage.list_prefix(prefix)]
    assert len(objects) == 1


async def test_me_replace_avatar_storage_down_is_503_avatar_key_unchanged(
    fake_clock: FakeClock, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ephemeral_minio` (backend S3) dừng trước khi dùng → 503, `avatar_key` không đổi (NO-167, K23).

    Catalog: B07-G-084.
    """
    user = await make_user(db_session)
    container = ephemeral_minio()
    config = container.get_config()
    endpoint = f"http://{config['endpoint']}"
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT", endpoint)
    monkeypatch.setenv("S3_PUBLIC_ENDPOINT", endpoint)
    monkeypatch.setenv("S3_BUCKET", "me-avatars-test")
    monkeypatch.setenv("S3_ACCESS_KEY", config["access_key"])
    monkeypatch.setenv("S3_SECRET_KEY", config["secret_key"])
    reset_storage_settings_cache()
    container.stop()  # dừng trước khi dùng — không cần bucket, mọi lượt gọi đều phải hỏng
    try:
        app = create_app(get_core_settings(), token_verifier=FakeTokenVerifier(), clock=fake_clock)
        async with make_api_client(app) as client:
            response = await client.put(
                PATH,
                json={"mimeType": "image/png", "contentBase64": b64(png_bytes())},
                headers=headers_of(principal_of(user)),
            )
    finally:
        reset_storage_settings_cache()
        with suppress(Exception):  # có thể đã dừng ở trên; lượt dừng thứ hai ném NotFound
            container.stop()

    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"

    row = await user_row(db_session, user.id)
    assert row.avatar_key is None
