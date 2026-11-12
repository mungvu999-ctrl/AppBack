"""Lịch `publish_library_assets`, CLI `publish` và ranh giới nhập (B2-06 [6], [8])."""

import asyncio
import errno
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final, NoReturn

import pytest
from sqlalchemy import func, select

from apps.api.library import cli
from apps.api.library.assets import ImportReport
from apps.api.library.jobs import TASK_NAME, publish_library_assets
from packages.core.clock import SystemClock
from packages.core.settings import reset_settings_cache
from packages.db.engine import create_engine, create_sessionmaker, session_scope
from packages.db.models.library import LibraryItemRow
from packages.db.seeds.library import seed
from packages.db.settings import get_database_settings, reset_database_settings_cache
from packages.domain.library import CATALOGUE, FILE_CATALOGUE, build_glb
from packages.messaging.schedules import schedule_entries
from packages.storage.local import LocalDiskStorage
from packages.storage.settings import reset_storage_settings_cache
from packages.testing.boundary import WORKER_BLOCKED

REPO_ROOT: Final = Path(__file__).resolve().parents[4]


@pytest.fixture
def process_env(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Môi trường của một tiến trình lịch/CLI: DB test riêng, kho local trong `tmp_path`."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://appback.test")
    monkeypatch.setenv("SECRET_KEY", "fixture-secret-for-library-jobs-0001")
    caches = (reset_settings_cache, reset_database_settings_cache, reset_storage_settings_cache)
    for reset in caches:
        reset()
    yield
    for reset in caches:
        reset()


async def _seed_process_db() -> None:
    """Seed thật vào DB của môi trường tiến trình (giống bước triển khai `seed`)."""
    engine = create_engine(get_database_settings())
    try:
        async with session_scope(create_sessionmaker(engine)) as db:
            await seed(db)
    finally:
        await engine.dispose()


async def _count_published() -> int:
    """Số dòng đã có `published_at` trong DB của môi trường tiến trình."""
    engine = create_engine(get_database_settings())
    try:
        async with create_sessionmaker(engine)() as db:
            stmt = select(func.count()).select_from(LibraryItemRow).where(LibraryItemRow.published_at.is_not(None))
            return (await db.execute(stmt)).scalar_one()
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("process_env")
def test_publish_library_assets_smoke() -> None:
    """Test khói: hàm lịch thật (`SystemClock`, `worker_sessionmaker`, kho local) phát hành 16 mục."""
    asyncio.run(_seed_process_db())
    publish_library_assets()
    reset_database_settings_cache()
    assert asyncio.run(_count_published()) == 16


def test_publish_library_assets_schedule_is_registered() -> None:
    """Lịch đăng ký đúng tên và hàm (`discover_jobs`)."""
    entries = {entry.name: entry for entry in schedule_entries()}
    assert entries[TASK_NAME].function == "publish_library_assets"


@pytest.mark.usefixtures("process_env")
def test_cli_publish_prints_counts_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`publish` in bảng đếm ra stdout và thoát 0; chạy lại thì 16 mục `skipped`."""
    asyncio.run(_seed_process_db())
    assert cli.main(["publish"]) == cli.EXIT_OK
    assert capsys.readouterr().out == "published=16 verified=0 skipped=0 failed=0\n"
    assert cli.main(["publish"]) == cli.EXIT_OK
    assert capsys.readouterr().out == "published=0 verified=0 skipped=16 failed=0\n"


@pytest.mark.usefixtures("process_env")
def test_cli_publish_exits_one_when_storage_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Đĩa đầy (ENOSPC → `DEPENDENCY_UNAVAILABLE`): mọi mục `failed`, lỗi ra stderr, thoát 1."""

    def full_disk(_path: Path) -> NoReturn:
        """Giả lập đĩa đầy."""
        raise OSError(errno.ENOSPC, "no space left")

    broken = LocalDiskStorage(tmp_path / "broken", SystemClock(), "https://appback.test", _open=full_disk)
    monkeypatch.setattr(cli, "open_storage", lambda _clock: broken)
    asyncio.run(_seed_process_db())

    assert cli.main(["publish"]) == cli.EXIT_FAIL
    captured = capsys.readouterr()
    assert captured.out == "published=0 verified=0 skipped=0 failed=16\n"
    assert "16" in captured.err


@pytest.mark.usefixtures("process_env")
def test_cli_import_files_strict_rejects_empty_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Gói rỗng: không cờ → mọi mục `skipped`, thoát 0 (nhập tay như cũ); `--strict` (migrate) → thoát 1."""
    asyncio.run(_seed_process_db())
    empty = tmp_path / "pack"
    empty.mkdir()
    assert cli.main(["import-files", str(empty)]) == cli.EXIT_OK
    assert capsys.readouterr().out == f"published=0 skipped={len(FILE_CATALOGUE)} failed=0\n"
    assert cli.main(["import-files", "--strict", str(empty)]) == cli.EXIT_FAIL
    assert str(len(FILE_CATALOGUE)) in capsys.readouterr().err


def test_cli_import_files_strict_rejects_incomplete_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gói thiếu một mục (một `skipped`, còn lại phát hành): `--strict` → thoát 1, không cờ → thoát 0."""

    async def partial(_directory: Path) -> ImportReport:
        """Kết quả nhập của gói thiếu một tệp."""
        return ImportReport(published=len(FILE_CATALOGUE) - 1, skipped=1, failed=0)

    monkeypatch.setattr(cli, "_import_files", partial)
    assert cli.main(["import-files", str(tmp_path)]) == cli.EXIT_OK
    assert cli.main(["import-files", "--strict", str(tmp_path)]) == cli.EXIT_FAIL
    assert "1 mục hỏng" in capsys.readouterr().err


@pytest.mark.parametrize(
    "module",
    ["apps.api.library.assets", "apps.api.library.jobs", "apps.api.library.cli", "packages.domain.library"],
)
def test_imports_without_web_or_crypto_packages(module: str) -> None:
    """Worker/CLI nhập được các module này khi 5 gói `WORKER_BLOCKED` (web, jwt, argon2) bị chặn trong `sys.modules`."""
    blocked = "; ".join(f"sys.modules[{name!r}] = None" for name in WORKER_BLOCKED)
    result = subprocess.run(  # noqa: S603 — trình thông dịch của chính tiến trình test, mã cố định
        [sys.executable, "-c", f"import sys; {blocked}; import {module}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.usefixtures("process_env")
def test_cli_import_files_counts_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`import-files`: thư mục rỗng → mọi mẫu `skipped`, thoát 0; một tệp sai sha → `failed=1`, thoát 1."""
    asyncio.run(_seed_process_db())
    pack = tmp_path / "pack"
    pack.mkdir()
    total = len(FILE_CATALOGUE)
    assert cli.main(["import-files", str(pack)]) == cli.EXIT_OK
    assert capsys.readouterr().out == f"published=0 skipped={total} failed=0\n"

    (pack / f"{FILE_CATALOGUE[0].id}.glb").write_bytes(build_glb(CATALOGUE[0]).data)
    assert cli.main(["import-files", str(pack)]) == cli.EXIT_FAIL
    assert capsys.readouterr().out == f"published=0 skipped={total - 1} failed=1\n"
