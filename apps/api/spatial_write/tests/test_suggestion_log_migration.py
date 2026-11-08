"""Revision `r20261108_m2_ql`: CHECK `entity_type` của `floor_change_log` nhận `suggestion` và lùi được (BE-00 §6.1).

Postgres thật trên database rỗng (K23): `upgrade head` → CHECK có `suggestion`; lùi về revision trước → CHECK cũ;
`upgrade head` lại → có lại. Dòng nhật ký thật đi qua CHECK mới ở `test_writer_suggestions` (database mẫu đã
`upgrade head`).
"""

import asyncio
from typing import Final

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.db.migrate_check import alembic_config
from packages.db.settings import reset_database_settings_cache

PREVIOUS: Final = "r20261107_m1_01"


async def _entity_check(url: str) -> str:
    """Định nghĩa CHECK `entity_type` hiện tại của `floor_change_log`, đọc bằng kết nối riêng."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_floor_change_log_entity_type'"
                )
            )
            return str(result.scalar_one())
    finally:
        await engine.dispose()


def test_suggestion_entity_type__upgrade_downgrade_upgrade(blank_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`suggestion` có sau `upgrade head`, mất sau khi lùi một revision (vẫn còn `roof`), có lại khi nâng lại."""
    monkeypatch.setenv("DATABASE_URL", blank_db_url)
    try:
        reset_database_settings_cache()
        config = alembic_config()
        command.upgrade(config, "head")
        assert "'suggestion'" in asyncio.run(_entity_check(blank_db_url))
        command.downgrade(config, PREVIOUS)
        lowered = asyncio.run(_entity_check(blank_db_url))
        assert "'suggestion'" not in lowered
        assert "'roof'" in lowered
        command.upgrade(config, "head")
        assert "'suggestion'" in asyncio.run(_entity_check(blank_db_url))
    finally:
        monkeypatch.undo()
        reset_database_settings_cache()
