"""J09, J10: callback sau commit (BE-00 §7, cổng case)."""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from packages.db.engine import create_engine, create_sessionmaker, session_scope
from packages.db.hooks import (
    DEFAULT_WORKERS,
    DROP_ENV,
    INLINE_ENV,
    WORKERS_ENV,
    _workers,
    after_commit_idle,
    on_after_commit,
)
from packages.db.settings import DatabaseSettings
from packages.testing.fixtures.db import drop_after_commit


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ép `APP_ENV=test` cho mọi test để cổng `drop_after_commit` và chế độ thử được phép bật."""
    monkeypatch.setenv("APP_ENV", "test")


@pytest.fixture
def calls() -> list[str]:
    """Danh sách rỗng dùng chung để các callback ghi lại thứ tự chúng đã chạy."""
    return []


def _append(calls: list[str], name: str) -> Callable[[], None]:
    """Tạo callback đồng bộ ghi `name` vào `calls`, để test quan sát callback có chạy và theo thứ tự nào."""

    def callback() -> None:
        """Ghi tên đã gắn vào danh sách `calls`."""
        calls.append(name)

    return callback


async def _begin(session: AsyncSession) -> None:
    """Mở giao dịch thật để callback gắn vào đúng SessionTransaction."""
    await session.execute(text("SELECT 1"))


async def test_on_after_commit__J09_runs_once_after_commit(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Một callback đăng ký trong giao dịch chỉ chạy đúng một lần sau khi commit."""
    async with session_scope(db_sessionmaker) as session:
        await _begin(session)
        on_after_commit(session, _append(calls, "a"))
    await after_commit_idle(session)
    assert calls == ["a"]


async def test_on_after_commit__J09_order_preserved(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Nhiều callback chạy theo đúng thứ tự đăng ký."""
    async with session_scope(db_sessionmaker) as session:
        await _begin(session)
        for name in ("a", "b", "c"):
            on_after_commit(session, _append(calls, name))
    await after_commit_idle(session)
    assert calls == ["a", "b", "c"]


async def test_on_after_commit__J09_rollback_skips(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Rollback ngoài cùng thì callback đã đăng ký bị bỏ, không chạy."""
    async with db_sessionmaker() as session:
        await _begin(session)
        on_after_commit(session, _append(calls, "a"))
        await session.rollback()
        await session.commit()
    await after_commit_idle(session)
    assert calls == []


async def test_on_after_commit__J09_exception_in_scope_skips(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Ngoại lệ thoát khỏi `session_scope` làm rollback nên callback không chạy."""
    opened: list[AsyncSession] = []

    async def fail_after_register() -> None:
        """Đăng ký callback rồi ném lỗi giữa chừng để mô phỏng phạm vi hỏng sau khi đã đăng ký."""
        async with session_scope(db_sessionmaker) as session:
            opened.append(session)
            await _begin(session)
            on_after_commit(session, _append(calls, "a"))
            raise RuntimeError("hỏng giữa chừng")

    with pytest.raises(RuntimeError, match="hỏng giữa chừng"):
        await fail_after_register()
    await after_commit_idle(opened[0])
    assert calls == []


async def test_on_after_commit__J09_savepoint_rollback_drops_inner(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Rollback savepoint chỉ bỏ callback đăng ký bên trong nó; callback ngoài vẫn chạy khi commit."""
    async with db_sessionmaker() as session:
        await _begin(session)
        on_after_commit(session, _append(calls, "ngoài"))
        nested = await session.begin_nested()
        on_after_commit(session, _append(calls, "trong"))
        await nested.rollback()
        await session.commit()
    await after_commit_idle(session)
    assert calls == ["ngoài"]


async def test_on_after_commit__J09_savepoint_commit_keeps_inner(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Nhả savepoint không chạy callback ngay; nó chờ tới commit ngoài cùng."""
    async with db_sessionmaker() as session:
        await _begin(session)
        nested = await session.begin_nested()
        on_after_commit(session, _append(calls, "trong"))
        await nested.commit()
        assert calls == []  # chờ tới commit ngoài cùng
        await session.commit()
    await after_commit_idle(session)
    assert calls == ["trong"]


async def test_on_after_commit__J09_savepoint_release_waits_for_the_outer_commit(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """NO-140: nhả savepoint **không** phải commit; rollback ngoài sau đó vẫn bỏ được callback.

    SQLAlchemy 2.0 bắn `after_commit` cả cho `SessionTransaction` lồng, nên bản trước
    chạy callback ngay lúc RELEASE SAVEPOINT — task hay thư đã đi trước khi giao dịch
    ngoài cùng bền vững, và rollback sau đó không thu hồi được (K17, J09).

    Phải có một lệnh thật trong savepoint: savepoint rỗng không giữ kết nối nào nên
    `after_commit` của nó không mang theo gì để lộ lỗi.
    """
    async with db_sessionmaker() as session:
        await session.execute(text("CREATE TEMP TABLE no140 (n integer)"))
        nested = await session.begin_nested()
        await session.execute(text("INSERT INTO no140 (n) VALUES (1)"))
        on_after_commit(session, _append(calls, "trong"))
        await nested.commit()
        await after_commit_idle(session)
        assert calls == []  # nhả savepoint chưa phải commit ngoài cùng
        await session.rollback()
    await after_commit_idle(session)
    assert calls == []


async def test_on_after_commit__J09_failing_callback_is_logged(
    db_sessionmaker: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    """Callback lỗi không làm hỏng commit mà được ghi log `after_commit_failed` kèm tên và lỗi."""

    def boom() -> None:
        """Callback luôn ném lỗi để thử đường log lỗi."""
        raise ValueError("broker chết")

    with caplog.at_level(logging.WARNING):
        async with session_scope(db_sessionmaker) as session:
            await _begin(session)
            on_after_commit(session, boom)
        await after_commit_idle(session)
    record = next(item for item in caplog.records if item.message == "after_commit_failed")
    assert record.__dict__["callback"] == "test_on_after_commit__J09_failing_callback_is_logged.<locals>.boom"
    assert "broker chết" in record.__dict__["error"]


async def test_on_after_commit__J09_slow_callback_times_out(
    db_sessionmaker: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    """Callback treo quá `CALLBACK_TIMEOUT_S` → log `after_commit_failed` kèm `TimeoutError`, idle không chờ nó.

    Tất định, không đồng hồ tường: callback chặn trên `release` (trần 10 s chỉ để luồng không treo mãi)
    và chỉ đặt `finished` khi thoát. `after_commit_idle` trả mà `finished` chưa đặt nghĩa là `wait_for`
    đã cắt — idle nào chờ callback xong thì phải ngồi hết 10 s rồi thấy `finished` đã đặt.
    """
    release = threading.Event()
    finished = threading.Event()

    def hang() -> None:
        """Treo tới khi test nhả (hay hết 10 s), rồi báo đã thoát."""
        release.wait(10)
        finished.set()

    try:
        with caplog.at_level(logging.WARNING):
            async with session_scope(db_sessionmaker) as session:
                await _begin(session)
                on_after_commit(session, hang)
            await after_commit_idle(session)
        assert not finished.is_set()
        record = next(item for item in caplog.records if item.message == "after_commit_failed")
        assert record.__dict__["error"].startswith("TimeoutError")
    finally:
        # Nhả luồng executor để test sau không mất một chỗ của nó
        release.set()


async def test_on_after_commit__J09_rejects_coroutine_function(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Hàm bất đồng bộ bị từ chối lúc đăng ký vì callback phải là hàm đồng bộ."""

    async def send() -> None:
        """Coroutine giả, chỉ để bị `on_after_commit` từ chối."""
        pass

    async with db_sessionmaker() as session:
        with pytest.raises(TypeError, match="đồng bộ"):
            on_after_commit(session, send)  # type: ignore[arg-type]  # kiểm lúc chạy


async def test_on_after_commit__J09_does_not_block_event_loop(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Callback chặn 1 s chạy ngoài vòng sự kiện: coroutine khác vẫn chạy, `calls` còn rỗng tới khi idle."""
    async with session_scope(db_sessionmaker) as session:
        await _begin(session)
        on_after_commit(session, lambda: time.sleep(1))
        on_after_commit(session, _append(calls, "sau"))
    # coroutine khác vẫn chạy; nếu vòng bị chặn thì "sau" đã vào calls
    await asyncio.sleep(0.01)
    assert calls == []
    await after_commit_idle(session)
    assert calls == ["sau"]


def test_on_after_commit__J09_inline_without_event_loop(calls: list[str]) -> None:
    """Không có vòng sự kiện (CLI, lịch đồng bộ) → chạy tại chỗ."""
    session = Session()
    on_after_commit(session, _append(calls, "a"))
    session.commit()
    assert calls == ["a"]


def test_on_after_commit__J09_inline_in_worker_runner(
    db_url: str, monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    """Trong runner của worker (chế độ inline), callback chạy xong trước khi vòng sự kiện nghỉ."""
    monkeypatch.setenv(INLINE_ENV, "1")

    async def work(maker: async_sessionmaker[AsyncSession]) -> None:
        """Việc của worker: đăng ký hai callback trong một giao dịch rồi thoát ngay."""
        async with session_scope(maker) as session:
            await _begin(session)
            on_after_commit(session, _append(calls, "a"))
            on_after_commit(session, _append(calls, "b"))
        # không await gì nữa: vòng của worker nghỉ ngay sau khi task trả

    engine = create_engine(DatabaseSettings(database_url=db_url))
    with asyncio.Runner() as runner:
        runner.run(work(create_sessionmaker(engine)))
        assert calls == ["a", "b"]
        runner.run(engine.dispose())


def test_on_after_commit__J09_inline_slow_callback_caps_at_timeout(
    db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Chạy tại chỗ (worker): callback chậm bị cắt ở `CALLBACK_TIMEOUT_S`, log `after_commit_failed`."""
    monkeypatch.setenv(INLINE_ENV, "1")

    async def work(maker: async_sessionmaker[AsyncSession]) -> None:
        """Việc của worker: đăng ký một callback chậm trong giao dịch rồi commit."""
        async with session_scope(maker) as session:
            await _begin(session)
            on_after_commit(session, lambda: time.sleep(3))

    engine = create_engine(DatabaseSettings(database_url=db_url))
    with caplog.at_level(logging.WARNING), asyncio.Runner() as runner:
        runner.run(work(create_sessionmaker(engine)))
        runner.run(engine.dispose())
    # `future.result(timeout=…)` cắt ở CALLBACK_TIMEOUT_S nên lỗi là TimeoutError, không chờ hết 3 s
    record = next(item for item in caplog.records if item.message == "after_commit_failed")
    assert record.__dict__["error"].startswith("TimeoutError")


async def test_on_after_commit__J10_drop_after_commit(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    """J10: `drop_after_commit` bỏ callback và ghi log `after_commit_dropped`, thoát khối thì chạy lại bình thường."""
    with caplog.at_level(logging.WARNING), drop_after_commit():
        async with session_scope(db_sessionmaker) as session:
            await _begin(session)
            on_after_commit(session, _append(calls, "bị bỏ"))
        await after_commit_idle(session)
    assert calls == []
    assert "after_commit_dropped" in caplog.text

    async with session_scope(db_sessionmaker) as session:
        await _begin(session)
        on_after_commit(session, _append(calls, "chạy lại"))
    await after_commit_idle(session)
    assert calls == ["chạy lại"]


def test_drop_after_commit_requires_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`drop_after_commit` chỉ dùng được khi `APP_ENV=test`; môi trường khác bị từ chối."""
    monkeypatch.setenv("APP_ENV", "dev")
    with pytest.raises(RuntimeError, match="APP_ENV=test"), drop_after_commit():
        pytest.fail("không vào tới thân khối")


def test_workers_env_is_read_at_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Số worker đọc từ biến môi trường: giá trị hợp lệ được dùng, thiếu hoặc sai thì về mặc định."""
    monkeypatch.setenv(WORKERS_ENV, "3")
    assert _workers() == 3
    monkeypatch.setenv(WORKERS_ENV, "không phải số")
    assert _workers() == DEFAULT_WORKERS
    monkeypatch.delenv(WORKERS_ENV)
    assert _workers() == DEFAULT_WORKERS


async def test_after_commit_idle_without_callbacks() -> None:
    """Chờ idle trên phiên chưa có callback thì trả về ngay, không lỗi."""
    await after_commit_idle(Session())  # không có gì để chờ


async def test_callback_registered_before_transaction_is_dropped_by_rollback(
    db_sessionmaker: async_sessionmaker[AsyncSession], calls: list[str]
) -> None:
    """Callback đăng ký trước khi có giao dịch vẫn bị bỏ khi giao dịch ngoài cùng rollback."""
    async with db_sessionmaker() as session:
        on_after_commit(session, _append(calls, "a"))  # chưa có giao dịch nào
        await _begin(session)
        await session.rollback()  # rollback ngoài cùng
        await session.commit()
    await after_commit_idle(session)
    assert calls == []


def test_callback_registered_before_transaction_survives_savepoint_rollback(
    db_url: str, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callback đăng ký trước giao dịch vẫn sống sau rollback savepoint và chạy khi commit ngoài cùng."""
    monkeypatch.setenv(INLINE_ENV, "1")

    async def work(maker: async_sessionmaker[AsyncSession]) -> None:
        """Việc của worker: đăng ký callback, mở savepoint rồi rollback nó, sau đó commit."""
        async with maker() as session:
            on_after_commit(session, _append(calls, "a"))  # chưa có giao dịch nào
            await _begin(session)
            nested = await session.begin_nested()
            await nested.rollback()
            await session.commit()

    engine = create_engine(DatabaseSettings(database_url=db_url))
    with asyncio.Runner() as runner:
        runner.run(work(create_sessionmaker(engine)))
        runner.run(engine.dispose())
    assert calls == ["a"]


def test_drop_after_commit_nested_restores_previous_value(calls: list[str]) -> None:
    """Hai `drop_after_commit` lồng nhau: thoát khối trong vẫn bỏ callback, thoát khối ngoài khôi phục biến cũ."""
    with drop_after_commit(), drop_after_commit():
        session = Session()
        on_after_commit(session, _append(calls, "a"))
        session.commit()
    assert calls == []
    assert DROP_ENV not in os.environ
