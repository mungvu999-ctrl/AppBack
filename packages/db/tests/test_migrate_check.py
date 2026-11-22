"""`migrate_check` — bước 6 của verify (BE-00 §6.1). Chạy Postgres thật (K23)."""

import ast
import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, CheckConstraint, Column, Enum, Integer, MetaData, Table, Text, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.types import TypeEngine

from packages.core import pinned_images
from packages.db import migrate_check
from packages.db.base import NAMING_CONVENTION
from packages.db.migrate_check import SCRIPT_LOCATION, alembic_config, run_checks
from packages.db.settings import reset_database_settings_cache
from packages.testing.fixtures import db as db_fixtures

REVISION = '''\
"""{slug}"""

from collections.abc import Sequence

from alembic import op

revision: str = "{rev}"
down_revision: str | None = {down}
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    {upgrade}


def downgrade() -> None:
    {downgrade}
'''

BASELINE = "r20260920_b0_03"
THING = 'op.execute("CREATE TABLE thing (id integer PRIMARY KEY, code text)")'
DROP_THING = 'op.execute("DROP TABLE thing")'


def thing_metadata(extra_table: bool = False) -> MetaData:
    """Metadata mẫu một bảng `thing` (thêm bảng phụ khi `extra_table`)."""
    metadata = MetaData()
    Table("thing", metadata, Column("id", Integer, primary_key=True), Column("code", Text))
    if extra_table:
        Table("missing", metadata, Column("id", Integer, primary_key=True))
    return metadata


async def no_seed(session: AsyncSession, env: str) -> tuple[str, ...]:
    """Seed rỗng."""
    return ()


def insert_rows(count: int) -> Callable[[AsyncSession, str], Awaitable[tuple[str, ...]]]:
    """Tạo seed chèn `count` hàng."""

    async def seed(session: AsyncSession, env: str) -> tuple[str, ...]:
        """Seed chèn hàng vào `thing`."""
        for _ in range(count):
            await session.execute(text("INSERT INTO thing (id, code) SELECT coalesce(max(id), 0) + 1, 'x' FROM thing"))
        return ("thing",)

    return seed


async def rows_with_note(session: AsyncSession, env: str) -> tuple[str, ...]:
    """Seed idempotent, chạy khi cột `note` đã có (head đang áp dụng)."""
    await session.execute(
        text("INSERT INTO thing (id, code, note) VALUES (1, 'a', 'n'), (2, 'b', 'n') ON CONFLICT DO NOTHING")
    )
    return ("thing",)


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    """Thư mục migrations tạm có `env.py` tối thiểu."""
    directory = tmp_path / "migrations"
    (directory / "versions").mkdir(parents=True)
    for name in ("env.py", "script.py.mako"):
        shutil.copy(SCRIPT_LOCATION / name, directory / name)
    shutil.copy(SCRIPT_LOCATION / "versions" / f"{BASELINE}_baseline.py", directory / "versions")
    return directory


def add_revision(migrations: Path, rev: str, down: str | None, upgrade: str, downgrade: str = "pass") -> None:
    """Ghi một revision alembic vào `migrations`."""
    body = REVISION.format(
        slug=rev, rev=rev, down=f'"{down}"' if down else "None", upgrade=upgrade, downgrade=downgrade
    )
    (migrations / "versions" / f"{rev}_x.py").write_text(body, encoding="utf-8")


def failed_steps(results: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Lọc các bước đã hỏng khỏi kết quả."""
    return [(name, detail) for name, detail in results if detail]


@pytest.fixture
def url(blank_db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """URL DB trống cho test, đặt vào môi trường."""
    monkeypatch.setenv("DATABASE_URL", blank_db_url)  # run_checks đặt lại; monkeypatch trả về sau test
    return blank_db_url


async def test_good_migrations_pass(migrations: Path, url: str) -> None:
    """Chuỗi migration hợp lệ qua mọi bước."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, THING, DROP_THING)
    results = await run_checks(alembic_config(migrations), url, metadata=thing_metadata(), seed_runner=no_seed)
    assert failed_steps(results) == []
    assert len(results) == 10


async def test_two_heads_fail_first_step(migrations: Path, url: str) -> None:
    """Hai head làm hỏng bước đầu."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, THING, DROP_THING)
    add_revision(migrations, "r20260921_b9_02", None, "pass")
    results = await run_checks(alembic_config(migrations), url, metadata=thing_metadata(), seed_runner=no_seed)
    assert len(results) == 1
    assert "2 head" in results[0][1]


async def test_downgrade_leaving_table_fails(migrations: Path, url: str) -> None:
    """Downgrade để sót bảng thì hỏng."""
    # `IF NOT EXISTS` để lượt `upgrade head` sau khi downgrade vẫn chạy: bước hỏng phải là bước dọn.
    upgrade = 'op.execute("CREATE TABLE IF NOT EXISTS thing (id integer PRIMARY KEY, code text)")'
    add_revision(migrations, "r20260921_b9_01", BASELINE, upgrade, "pass")
    results = await run_checks(alembic_config(migrations), url, metadata=thing_metadata(), seed_runner=no_seed)
    failed = failed_steps(results)
    assert [name for name, _ in failed] == ["chỉ còn alembic_version"]
    assert "thing" in failed[0][1]


async def test_model_without_migration_fails(migrations: Path, url: str) -> None:
    """Model không có migration thì hỏng."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, THING, DROP_THING)
    results = await run_checks(
        alembic_config(migrations), url, metadata=thing_metadata(extra_table=True), seed_runner=no_seed
    )
    failed = failed_steps(results)
    assert [name for name, _ in failed] == ["model khớp DB"]
    assert "missing" in failed[0][1]


def checked_metadata(*extra: Column[Any]) -> MetaData:
    """`thing` có `CHECK` mức bảng `code` và mức cột `id_positive`; quy ước của `Base` thêm `ck_thing_`."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, CheckConstraint("id > 0", name="id_positive"), primary_key=True),
        Column("code", Text),
        *extra,
        CheckConstraint("code <> ''", name="code"),
    )
    return metadata


def checked_thing(code_check: str, extra_sql: str = "") -> str:
    """Revision tạo `thing` với `CHECK` của `code` mang đúng tên `code_check` trong DB; `extra_sql` thêm cột."""
    sql = (
        "CREATE TABLE thing (id integer PRIMARY KEY CONSTRAINT ck_thing_id_positive CHECK (id > 0), "
        f"code text CONSTRAINT {code_check} CHECK (code <> ''){extra_sql})"
    )
    return f'op.execute("{sql}")'


# `create_constraint=True` gắn một `CHECK` vào kiểu; Postgres có boolean và enum native nên DDL không phát nó.
NATIVE_CHECKED_TYPES: dict[str, tuple[Callable[[], TypeEngine[Any]], str]] = {
    "boolean": (lambda: Boolean(create_constraint=True), "boolean"),
    "enum": (lambda: Enum("a", "b", name="kind", create_constraint=True), "kind"),
}


def typed_thing(migrations: Path, code_check: str, kind: str) -> MetaData:
    """Revision và model của `thing` như `checked_thing`, thêm cột `flag` kiểu `kind` của `NATIVE_CHECKED_TYPES`."""
    make_type, sql_type = NATIVE_CHECKED_TYPES[kind]
    # Kiểu `kind` tạo cho cả hai trường hợp: một revision, một đường lùi.
    create_type = """op.execute("CREATE TYPE kind AS ENUM ('a', 'b')")"""
    upgrade = f"{create_type}\n    {checked_thing(code_check, f', flag {sql_type}')}"
    add_revision(migrations, "r20260921_b9_01", BASELINE, upgrade, f'{DROP_THING}\n    op.execute("DROP TYPE kind")')
    return checked_metadata(Column("flag", make_type()))


async def test_check_names_matching_model_pass(migrations: Path, url: str) -> None:
    """Tên check constraint khớp model thì qua."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, checked_thing("ck_thing_code"), DROP_THING)
    results = await run_checks(alembic_config(migrations), url, metadata=checked_metadata(), seed_runner=no_seed)
    assert failed_steps(results) == []
    assert results[-1][0] == "tên CHECK khớp model"


async def test_check_name_with_doubled_prefix_fails(migrations: Path, url: str) -> None:
    """NO-057: tên đủ tiền tố truyền không bọc `op.f(...)` bị quy ước ghép lần hai; `compare_metadata` không so."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, checked_thing("ck_thing_ck_thing_code"), DROP_THING)
    results = await run_checks(alembic_config(migrations), url, metadata=checked_metadata(), seed_runner=no_seed)
    failed = failed_steps(results)
    assert [name for name, _ in failed] == ["tên CHECK khớp model"]
    assert "ck_thing_ck_thing_code" in failed[0][1]  # thừa trong DB
    assert "'ck_thing_code'" in failed[0][1]  # thiếu trong DB


@pytest.mark.parametrize("kind", sorted(NATIVE_CHECKED_TYPES))
async def test_check_bound_to_native_type_passes(migrations: Path, url: str, kind: str) -> None:
    """NO-070: `CHECK` gắn kiểu mà DDL Postgres không phát thì không bị đòi trong DB (trước: `boolean` ném)."""
    metadata = typed_thing(migrations, "ck_thing_code", kind)
    results = await run_checks(alembic_config(migrations), url, metadata=metadata, seed_runner=no_seed)
    assert failed_steps(results) == []
    assert results[-1][0] == "tên CHECK khớp model"


@pytest.mark.parametrize("kind", sorted(NATIVE_CHECKED_TYPES))
async def test_check_bound_to_native_type_keeps_table_check_drift(migrations: Path, url: str, kind: str) -> None:
    """Bỏ `CHECK` gắn kiểu không làm mù bước: `CHECK` mức bảng lệch tên vẫn hỏng, đúng hai tên đó."""
    metadata = typed_thing(migrations, "ck_thing_ck_thing_code", kind)
    results = await run_checks(alembic_config(migrations), url, metadata=metadata, seed_runner=no_seed)
    assert failed_steps(results) == [
        ("tên CHECK khớp model", "CHECK thừa trong DB: ['ck_thing_ck_thing_code']; thiếu trong DB: ['ck_thing_code']")
    ]


async def test_column_check_with_ddl_if_is_still_expected(migrations: Path, url: str) -> None:
    """`CREATE TABLE` phát mọi `CHECK` trong `Column(...)`, kể cả có `ddl_if` lệch dialect: bước vẫn đòi nó."""
    guarded = CheckConstraint("n > 0", name="n_positive").ddl_if(dialect="sqlite")
    metadata = checked_metadata(Column("n", Integer, guarded))
    dialect = make_url(url).get_dialect()()  # dialect của DB thử, không mở kết nối
    assert "ck_thing_n_positive" in str(CreateTable(metadata.tables["thing"]).compile(dialect=dialect))
    column = ", n integer CONSTRAINT ck_thing_n_positive CHECK (n > 0)"
    add_revision(migrations, "r20260921_b9_01", BASELINE, checked_thing("ck_thing_code", column), DROP_THING)
    results = await run_checks(alembic_config(migrations), url, metadata=metadata, seed_runner=no_seed)
    assert failed_steps(results) == []


async def test_non_idempotent_seed_fails(migrations: Path, url: str) -> None:
    """Seed không idempotent thì hỏng."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, THING, DROP_THING)
    results = await run_checks(alembic_config(migrations), url, metadata=thing_metadata(), seed_runner=insert_rows(1))
    failed = failed_steps(results)
    assert [name for name, _ in failed] == ["seed ci hai lần"]
    assert "không idempotent" in failed[0][1]


async def test_revision_failing_on_existing_data(migrations: Path, url: str) -> None:
    """Revision mới nhất phải chạy được trên DB **có dữ liệu**, không chỉ DB trống."""
    add_revision(migrations, "r20260921_b9_01", BASELINE, THING, DROP_THING)
    add_revision(
        migrations,
        "r20260921_b9_02",
        "r20260921_b9_01",
        'op.execute("ALTER TABLE thing ADD COLUMN note text NOT NULL")',
        'op.execute("ALTER TABLE thing DROP COLUMN note")',
    )
    results = await run_checks(alembic_config(migrations), url, metadata=thing_metadata(), seed_runner=rows_with_note)
    failed = failed_steps(results)
    assert [name for name, _ in failed] == ["upgrade head trên DB có dữ liệu"]


def test_main_on_repo_migrations(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`main` chạy được trên migrations thật của repo."""
    assert migrate_check.main() == 0
    out = capsys.readouterr().out
    assert "migrate_check: đạt" in out
    assert out.count("đạt") == 11  # 10 bước con + dòng kết


def test_main_rejects_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    """`main` từ chối đối số dòng lệnh."""
    assert migrate_check.main(["--fast"]) == 2
    assert "không nhận tham số" in capsys.readouterr().out


def test_main_starts_postgres_when_url_missing(
    blank_db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` dựng Postgres tạm khi thiếu URL."""
    stopped: list[bool] = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(migrate_check, "_start_postgres", lambda: (blank_db_url, lambda: stopped.append(True)))
    assert migrate_check.main() == 0
    assert stopped == [True]
    assert "migrate_check: đạt" in capsys.readouterr().out


def test_alembic_upgrade_restores_previous_database_url(blank_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture `db_template` chạy Alembic bằng biến môi trường; nó phải trả biến về như cũ."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://cũ:cũ@localhost:5432/cũ")
    db_fixtures._alembic_upgrade(blank_db_url)
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://cũ:cũ@localhost:5432/cũ"


def test_migrate_check__uses_shared_pinned_image() -> None:
    """NO-184/FIX-135: ảnh Postgres lấy từ `packages.core.pinned_images`, không chép tay literal `postgres:`."""
    tree = ast.parse(Path(migrate_check.__file__).read_text(encoding="utf-8"))
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [v for v in literals if v.startswith("postgres:")]
    assert migrate_check.POSTGRES_IMAGE == pinned_images.POSTGRES_IMAGE


async def test_run_checks__merge_revision_from_template_passes(migrations: Path, url: str) -> None:
    """NO-249: revision merge do `script.py.mako` thật sinh ra nạp lại được và qua đủ mười bước.

    Hai head cùng cha, `alembic merge heads` (đúng lệnh `merge-heads` của `tools/verify/steps.py`
    gọi) render bằng template của repo: `down_revision` phải là tuple hai head chứ không phải một
    chuỗi, và `downgrade -1` từ head là revision merge không được hỏng `Ambiguous walk`.
    """
    add_revision(migrations, "r20260921_b9_01", BASELINE, THING, DROP_THING)
    add_revision(migrations, "r20260921_b9_02", BASELINE, "pass")
    config = alembic_config(migrations)
    command.merge(config, "heads", message="merge", rev_id="r20260921_merge_w01_1")
    merged = next((migrations / "versions").glob("r20260921_merge_w01_1_*.py"))
    assigned = {
        node.target.id: ast.literal_eval(node.value)
        for node in ast.parse(merged.read_text(encoding="utf-8")).body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None
    }
    assert sorted(assigned["down_revision"]) == ["r20260921_b9_01", "r20260921_b9_02"]
    assert ScriptDirectory.from_config(config).get_heads() == ["r20260921_merge_w01_1"]
    results = await run_checks(config, url, metadata=thing_metadata(), seed_runner=no_seed)
    assert failed_steps(results) == []
    assert len(results) == 10


async def _execute(url: str, sql: str) -> list[Any]:
    """Chạy một câu SQL trên `url` bằng kết nối riêng (ngoài Alembic), trả các dòng nếu có."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(sql))
            return list(result) if result.returns_rows else []
    finally:
        await engine.dispose()


def test_hs_03_upgrade_tolerates_column_added_by_hand(url: str) -> None:
    """L4-N02: f14 đã có `floors.is_section` thêm bằng SQL tay; `r20261121_hs_03` chạy qua, không thêm lần hai,
    và đường lùi/tiến bình thường vẫn chạy (cột bị bỏ rồi được thêm lại)."""
    hs_03, parent = "r20261121_hs_03", "r20261103_bench3d_07"
    config = alembic_config()
    columns = (
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'floors' AND column_name = 'is_section'"
    )
    reset_database_settings_cache()  # env.py đọc `DatabaseSettings` có cache; `url` vừa đặt `DATABASE_URL`
    try:
        command.upgrade(config, parent)
        asyncio.run(_execute(url, "ALTER TABLE floors ADD COLUMN is_section boolean NOT NULL DEFAULT false"))
        command.upgrade(config, hs_03)
        assert asyncio.run(_execute(url, columns)) == [(1,)]
        command.downgrade(config, parent)
        assert asyncio.run(_execute(url, columns)) == [(0,)]
        command.upgrade(config, hs_03)
        assert asyncio.run(_execute(url, columns)) == [(1,)]
    finally:
        reset_database_settings_cache()
