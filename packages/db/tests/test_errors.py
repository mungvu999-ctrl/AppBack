"""C13: phụ thuộc hỏng → 503 `DEPENDENCY_UNAVAILABLE` (cổng case, BE-00 §4).

Chạy Postgres **thật** (K23); các lượt hỏng dùng `ephemeral_postgres()` để dừng được.
"""

import pytest
from asyncpg.exceptions import DeadlockDetectedError, SerializationError, UniqueViolationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.engine import create_engine, create_sessionmaker
from packages.db.errors import translate_db_error, unique_violation
from packages.db.settings import DatabaseSettings
from packages.testing.fixtures.services import ephemeral_postgres, refused_url

FAILURES = (SQLAlchemyError, OSError)


def _settings(url: str, statement_timeout_ms: int = 10000) -> DatabaseSettings:
    return DatabaseSettings(database_url=url, db_statement_timeout_ms=statement_timeout_ms)


async def test_translate_db_error__C13_connection_refused() -> None:
    engine = create_engine(_settings(f"{refused_url('postgresql+asyncpg')}/appback"))
    try:
        with pytest.raises(FAILURES) as excinfo:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
    error = translate_db_error(excinfo.value)
    assert error is not None
    assert (error.code.code, error.code.status, error.retry_after) == ("DEPENDENCY_UNAVAILABLE", 503, 5)


async def test_translate_db_error__C13_server_stopped_under_live_pool() -> None:
    container = ephemeral_postgres()
    engine = create_engine(_settings(str(container.get_connection_url())))
    stopped = False
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))  # pool đã có kết nối sống
        container.stop()
        stopped = True
        with pytest.raises(FAILURES) as excinfo:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
        if not stopped:
            container.stop()
    error = translate_db_error(excinfo.value)
    assert error is not None
    assert error.retry_after == 5


async def test_translate_db_error__C13_statement_timeout(db_url: str) -> None:
    engine = create_engine(_settings(db_url, statement_timeout_ms=200))
    try:
        async with create_sessionmaker(engine)() as session:
            with pytest.raises(SQLAlchemyError) as excinfo:
                await session.execute(text("SELECT pg_sleep(2)"))
    finally:
        await engine.dispose()
    error = translate_db_error(excinfo.value)
    assert error is not None
    assert error.retry_after == 5


async def test_translate_db_error__C13_syntax_error_is_not_dependency(db_session: AsyncSession) -> None:
    with pytest.raises(SQLAlchemyError) as excinfo:
        await db_session.execute(text("SELEC 1"))
    assert translate_db_error(excinfo.value) is None


@pytest.mark.parametrize("exc", [SerializationError("x"), DeadlockDetectedError("x")])
def test_translate_db_error__C13_serialization_retries_after_one(exc: Exception) -> None:
    error = translate_db_error(exc)
    assert error is not None
    assert (error.code.status, error.retry_after) == (503, 1)


async def test_unique_violation_returns_constraint_name(db_session: AsyncSession) -> None:
    await db_session.execute(text("CREATE TABLE dup (id integer PRIMARY KEY)"))
    await db_session.execute(text("INSERT INTO dup VALUES (1)"))
    with pytest.raises(SQLAlchemyError) as excinfo:
        await db_session.execute(text("INSERT INTO dup VALUES (1)"))
    assert unique_violation(excinfo.value) == "dup_pkey"
    assert translate_db_error(excinfo.value) is None


def test_unique_violation_none_for_other_errors() -> None:
    assert unique_violation(SerializationError("x")) is None
    assert unique_violation(RuntimeError("x")) is None


def test_unique_violation_falls_back_to_message() -> None:
    exc = UniqueViolationError('duplicate key value violates unique constraint "uq_floors_level_id"')
    assert unique_violation(exc) == "uq_floors_level_id"


def test_translate_db_error_invalidated_connection() -> None:
    exc = DBAPIError(statement=None, params=None, orig=Exception("mất kết nối"), connection_invalidated=True)
    error = translate_db_error(exc)
    assert error is not None
    assert error.retry_after == 5


def test_translate_db_error_ignores_business_errors() -> None:
    assert translate_db_error(ValueError("không phải lỗi DB")) is None
