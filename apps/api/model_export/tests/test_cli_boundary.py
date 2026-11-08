"""CLI `import-pack` và ranh giới nhập của `state`/`jobs`/`settings` (worker nhập chúng, BE-00 §7)."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from apps.api.model_export import cli
from apps.api.model_export.settings import ModelExportSettings
from packages.core.settings import reset_settings_cache
from packages.storage.keys import model_export_pack_object
from packages.storage.local import LocalDiskStorage
from packages.storage.settings import reset_storage_settings_cache
from packages.testing.boundary import WORKER_BLOCKED

REPO_ROOT: Final = Path(__file__).resolve().parents[4]


def _pack(root: Path, *, corrupt: bool = False) -> None:
    """Gói nhỏ theo dạng thật của `scripts/export-pack` (§8): `files` → sha256, `missing[]`; `corrupt` ghi sai sha."""
    (root / "textures").mkdir()
    files = {"textures/a.jpg": b"jpeg-bytes", "editor-defaults.json": b"{}"}
    for rel, data in files.items():
        (root / rel).write_bytes(data)
    digests = {rel: "0" * 64 if corrupt else hashlib.sha256(data).hexdigest() for rel, data in files.items()}
    manifest = {"version": "v1", "createdAt": "2026-11-07T00:00:00Z", "sources": [], "files": digests}
    (root / "manifest.json").write_text(json.dumps({**manifest, "missing": ["models/sofa.glb"]}))


async def test_import_pack__ok(tmp_path: Path, local_storage: LocalDiskStorage) -> None:
    """sha khớp → ghi mọi tệp của `files` (gồm `editor-defaults.json`) + manifest dưới `model-export-packs/v1/`."""
    _pack(tmp_path)

    assert await cli.import_pack(tmp_path, local_storage) == ("v1", ["models/sofa.glb"])

    for rel in ("textures/a.jpg", "editor-defaults.json", "manifest.json"):
        assert await local_storage.stat(model_export_pack_object("v1", rel)) is not None


def test_default_pack_version__valid_key() -> None:
    """Gói mặc định `m1-01-pack-r1` là một đoạn khoá hợp lệ của `model-export-packs/{version}/`."""
    version = ModelExportSettings().model_export_pack_version
    assert version == "m1-01-pack-r1"
    assert model_export_pack_object(version, "manifest.json") == "model-export-packs/m1-01-pack-r1/manifest.json"


def test_main__bad_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """sha sai → thoát 1, không ghi gì (kho không bị gọi)."""
    _pack(tmp_path, corrupt=True)
    monkeypatch.setattr(cli, "_storage", lambda: None)

    assert cli.main(["import-pack", str(tmp_path)]) == 1
    assert "textures/a.jpg" in capsys.readouterr().err


def test_main__ok(
    tmp_path: Path, local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Đường thành công của `main`: in phiên bản gói và báo mục `missing`."""
    _pack(tmp_path)
    monkeypatch.setattr(cli, "_storage", lambda: local_storage)

    assert cli.main(["import-pack", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert "MODEL_EXPORT_PACK_VERSION=v1" in out.out
    assert "models/sofa.glb" in out.err


@pytest.mark.usefixtures("storage_env")
def test_storage__from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_storage` dựng kho theo biến môi trường của tiến trình."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))
    reset_settings_cache()
    reset_storage_settings_cache()
    try:
        assert isinstance(cli._storage(), LocalDiskStorage)
    finally:
        reset_settings_cache()
        reset_storage_settings_cache()


@pytest.mark.parametrize(
    "module",
    ["apps.api.model_export.state", "apps.api.model_export.jobs", "apps.api.model_export.settings"],
)
def test_imports_without_web_or_crypto_packages(module: str) -> None:
    """Nhập được khi gói web/crypto bị chặn, và không kéo `apps.api.core`."""
    blocked = "; ".join(f"sys.modules[{name!r}] = None" for name in WORKER_BLOCKED)
    check = "assert not [m for m in sys.modules if m.startswith('apps.api.core')]"
    result = subprocess.run(  # noqa: S603 — trình thông dịch của chính tiến trình test, mã cố định
        [sys.executable, "-c", f"import sys; {blocked}; import {module}; {check}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120.0,
    )

    assert result.returncode == 0, result.stderr
