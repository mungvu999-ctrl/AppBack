"""Task `run_model_export`: J01-J07 trên lõi `compile_model_export` (M1-01).

Chỉ ranh giới tiến trình con (Blender → `fake_blender.py`) và `build_plan` (plan cố định, phần của agent A
có test riêng) là giả; Postgres, kho local, gói trong kho, cache gói đều thật.
"""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.model_export import state
from apps.worker.model_export import pack as pack_module
from apps.worker.model_export import tasks
from apps.worker.model_export.blender import COMPILE_SCRIPT, blender_argv
from apps.worker.model_export.pack import Pack, fetch_files, load_pack, prune_cache
from apps.worker.model_export.tasks import check_glb, compile_model_export, fail_model_export
from apps.worker.model_export.tests.fake_blender import argv_for, glb
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.db.engine import session_scope
from packages.messaging import PermanentError
from packages.messaging.payloads.model_export import MODEL_EXPORT_SNAPSHOT
from packages.storage.keys import model_export_object, model_export_pack_object, model_export_prefix
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.model_export import QueueExport

Maker = async_sessionmaker[AsyncSession]
VERSION = "pack-t1"
PACK_FILES: dict[str, bytes] = {
    "materials.json": b"{}",
    "scene-materials.json": b"{}",
    "models.json": b'{"chair": {"file": "models/chair.glb"}}',
    "furniture-defaults.json": b"{}",
    "editor-defaults.json": b"{}",
    "textures/oak.jpg": b"\xff\xd8\xff jpeg",
    "models/chair.glb": glb(),
}


def _plan(
    records: list[dict[str, str]] | None = None, blender_records: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """Plan cố định: một tường, một vật liệu có ảnh và giấy phép, hai đồ dùng chung một mẫu."""
    lic = {"license": "CC0", "author": "Tác giả", "source": "nguồn"}
    return {
        "schemaVersion": 1,
        "levels": [{"id": "L-1", "name": "Tầng 1", "elevation": 0.0, "height": 3.3, "hidden": False}],
        "materials": {"library:oak": {"color": [1, 1, 1], "maps": {"base": "textures/oak.jpg"}, **lic}},
        "prisms": [{"name": "Wall_W-1", "kind": "wall"}],
        "instances": [
            {"name": "Furniture_F-1", "level": "L-1", "model": "models/chair.glb", "props": lic},
            {"name": "Furniture_F-2", "level": "L-1", "model": "models/chair.glb", "props": lic},
        ],
        "records": records or [],
        "blenderRecords": blender_records or [],
        "stamps": {"snapshotSha": "s" * 64, "fingerprint": "f" * 64, "compiler": "m1-01.t", "pack": VERSION},
    }


def _stub(plan: dict[str, Any]) -> Any:
    """`build` giả trả `plan`, kiểm rằng nó nhận đủ bốn bảng của gói."""

    def build(snapshot: Mapping[str, Any], tables: Mapping[str, Any]) -> dict[str, Any]:
        """Plan cố định."""
        assert set(tables) == set(pack_module.PACK_TABLES)
        assert snapshot["versions"]["pack"] == VERSION
        return plan

    return build


async def _put(storage: LocalDiskStorage, key: str, data: bytes) -> None:
    """Ghi một object nhỏ."""
    await storage.put(key, data, content_type="application/octet-stream", max_bytes=len(data) + 1)


async def _seed_pack(
    storage: LocalDiskStorage, files: Mapping[str, bytes] = PACK_FILES, *, manifest: bool = True
) -> None:
    """Gói `VERSION` trong kho, manifest có sha256 từng tệp."""
    for rel, data in files.items():
        await _put(storage, model_export_pack_object(VERSION, rel), data)
    if manifest:
        sha = {rel: hashlib.sha256(data).hexdigest() for rel, data in PACK_FILES.items()}
        await _put(storage, model_export_pack_object(VERSION, "manifest.json"), json.dumps({"files": sha}).encode())


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cache gói của worker dưới `tmp_path`."""
    monkeypatch.setenv("MODEL_EXPORT_PACK_CACHE_DIR", str(tmp_path / "packs"))
    return tmp_path / "packs"


@pytest.fixture
def queue(queue_model_export: QueueExport, local_storage: LocalDiskStorage) -> QueueExport:
    """`queue_model_export` + snapshot trỏ tới gói `VERSION` + gói trong kho."""

    async def factory(**kwargs: Any) -> Any:
        """Một lượt `queued` sẵn sàng chạy."""
        queued = await queue_model_export(**kwargs)
        snapshot = json.dumps({"schemaVersion": 1, "floors": [], "versions": {"pack": VERSION}}).encode()
        await _put(
            local_storage, model_export_object(queued.project_id, queued.export_id, MODEL_EXPORT_SNAPSHOT), snapshot
        )
        await _seed_pack(local_storage)
        return queued

    return factory


async def _keys(storage: LocalDiskStorage, project_id: str, export_id: str) -> list[str]:
    """Tên object dưới tiền tố của một lượt, sắp tăng."""
    return sorted(
        [i.key.rsplit("/", 1)[-1] async for i in storage.list_prefix(model_export_prefix(project_id, export_id))]
    )


async def _row(maker: Maker, export_id: str) -> dict[str, Any]:
    """Dòng `model_exports` đọc lại."""
    sql = text("SELECT status, quality, failure_code, glb_bytes, report_summary FROM model_exports WHERE id = :id")
    async with maker() as db:
        return dict((await db.execute(sql, {"id": export_id})).mappings().one())


async def _json(storage: LocalDiskStorage, key: str) -> Any:
    """Object JSON đã lưu."""
    return json.loads(b"".join([c async for c in storage.open_read(key)]))


async def test_run_model_export__J01(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Lượt hợp lệ → `done/ready`, ba tệp + snapshot, report đủ trường, CREDITS gom mẫu dùng chung, cache đúng gói."""
    job = await queue()

    await compile_model_export(
        db_sessionmaker,
        local_storage,
        fake_clock,
        export_id=job.export_id,
        argv_for=argv_for("ok"),
        build=_stub(_plan()),
    )

    row = await _row(db_sessionmaker, job.export_id)
    assert (row["status"], row["quality"], row["glb_bytes"]) == ("done", "ready", len(glb()))
    assert await _keys(local_storage, job.project_id, job.export_id) == [
        "CREDITS.txt",
        "model.glb",
        "report.json",
        "snapshot.json",
    ]
    report = await _json(local_storage, model_export_object(job.project_id, job.export_id, "report.json"))
    assert report["quality"] == "ready"
    assert report["pack"] == VERSION
    assert report["stats"]["glbBytes"] == len(glb())
    credits = b"".join(
        [c async for c in local_storage.open_read(model_export_object(job.project_id, job.export_id, "CREDITS.txt"))]
    )
    assert credits.decode().splitlines() == [
        "Nguồn mẫu và vật liệu trong tệp:",
        "- library:oak: Tác giả · CC0 · nguồn",
        "- models/chair.glb: Tác giả · CC0 · nguồn",
    ]
    assert (cache_dir / VERSION / "textures" / "oak.jpg").read_bytes() == PACK_FILES["textures/oak.jpg"]


async def test_run_model_export__J01_degraded(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Bản ghi `major` từ plan/Blender + hộp cửa suy biến → `degraded`; đồ bị bỏ không vào CREDITS."""
    job = await queue()
    skipped = {
        "level": "L-1",
        "kind": "furniture",
        "id": "Furniture_F-2",
        "status": "skipped",
        "severity": "major",
        "reason": "mẫu chưa có",
    }
    plan = _plan(
        records=[{"level": "L-1", "kind": "wall", "id": "W-1", "status": "built", "severity": "info", "reason": ""}],
        blender_records=[skipped],
    )
    plan["instances"][1]["model"] = "models/khac.glb"

    await compile_model_export(
        db_sessionmaker,
        local_storage,
        fake_clock,
        export_id=job.export_id,
        argv_for=argv_for("degenerate"),
        build=_stub(plan),
    )

    row = await _row(db_sessionmaker, job.export_id)
    assert (row["status"], row["quality"]) == ("done", "degraded")
    assert row["report_summary"]["counts"] == {"furniture:skipped": 1, "geometry:degraded": 1, "wall:built": 1}
    assert [r["severity"] for r in row["report_summary"]["records"]] == ["major", "major", "info"]
    credits_key = model_export_object(job.project_id, job.export_id, "CREDITS.txt")
    credits = b"".join([c async for c in local_storage.open_read(credits_key)])
    assert "khac" not in credits.decode()


async def test_run_model_export__J01_blocked(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Tường hở → `critical` → `blocked`: không lưu `model.glb`, `glb_bytes` rỗng, report vẫn có."""
    job = await queue()

    await compile_model_export(
        db_sessionmaker,
        local_storage,
        fake_clock,
        export_id=job.export_id,
        argv_for=argv_for("open"),
        build=_stub(_plan()),
    )

    row = await _row(db_sessionmaker, job.export_id)
    assert (row["status"], row["quality"], row["glb_bytes"]) == ("done", "blocked", None)
    assert await _keys(local_storage, job.project_id, job.export_id) == ["CREDITS.txt", "report.json", "snapshot.json"]
    report = await _json(local_storage, model_export_object(job.project_id, job.export_id, "report.json"))
    assert report["geometry"]["open"] == ["Wall_W-1"]
    assert report["records"][0]["severity"] == "critical"


async def test_run_model_export__J02(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Blender bị `SIGKILL` không do cha (OOM) → `DEPENDENCY_UNAVAILABLE` (thử lại), dòng vẫn `running`."""
    job = await queue()

    with pytest.raises(AppError) as caught:
        await compile_model_export(
            db_sessionmaker,
            local_storage,
            fake_clock,
            export_id=job.export_id,
            argv_for=argv_for("killed"),
            build=_stub(_plan()),
        )

    assert caught.value.code is DEPENDENCY_UNAVAILABLE
    assert (await _row(db_sessionmaker, job.export_id))["status"] == "running"


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("fail", state.MODEL_EXPORT_FAILED),
        ("badglb", state.MODEL_EXPORT_OUTPUT_INVALID),
        ("nogeom", state.MODEL_EXPORT_OUTPUT_INVALID),
    ],
)
async def test_run_model_export__J03(
    db_sessionmaker: Maker,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    cache_dir: Path,
    queue: QueueExport,
    mode: str,
    code: str,
) -> None:
    """Blender thoát 1 / GLB sai / thiếu `geometry.json` → lỗi vĩnh viễn; `on_failed` → `failed` + tiền tố rỗng."""
    job = await queue()
    with pytest.raises(PermanentError, match=code):
        await compile_model_export(
            db_sessionmaker,
            local_storage,
            fake_clock,
            export_id=job.export_id,
            argv_for=argv_for(mode),
            build=_stub(_plan()),
        )

    await fail_model_export(db_sessionmaker, local_storage, fake_clock, export_id=job.export_id, code=code)

    row = await _row(db_sessionmaker, job.export_id)
    assert (row["status"], row["failure_code"]) == ("failed", code)
    assert await _keys(local_storage, job.project_id, job.export_id) == []


async def test_run_model_export__J04(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Quá `timeout_s` → `MODEL_EXPORT_TIMEOUT`; `TASK_TIMEOUT` của Celery cũng chốt thành mã đó."""
    job = await queue()
    with pytest.raises(PermanentError, match=state.MODEL_EXPORT_TIMEOUT):
        await compile_model_export(
            db_sessionmaker,
            local_storage,
            fake_clock,
            export_id=job.export_id,
            argv_for=argv_for("sleep"),
            timeout_s=1,
            build=_stub(_plan()),
        )

    await fail_model_export(db_sessionmaker, local_storage, fake_clock, export_id=job.export_id, code="TASK_TIMEOUT")

    assert (await _row(db_sessionmaker, job.export_id))["failure_code"] == state.MODEL_EXPORT_TIMEOUT


async def test_run_model_export__J04_cancelled(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Huỷ giữa lượt Blender (giờ mềm) → giết nhóm tiến trình, lỗi đi tiếp; mã lạ chốt `MODEL_EXPORT_FAILED`."""
    job = await queue()
    run = asyncio.create_task(
        compile_model_export(
            db_sessionmaker,
            local_storage,
            fake_clock,
            export_id=job.export_id,
            argv_for=argv_for("sleep"),
            build=_stub(_plan()),
        )
    )
    await asyncio.sleep(2)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    await fail_model_export(db_sessionmaker, local_storage, fake_clock, export_id=job.export_id, code="INTERNAL")

    assert (await _row(db_sessionmaker, job.export_id))["failure_code"] == state.MODEL_EXPORT_FAILED


async def test_run_model_export__J05(
    db_sessionmaker: Maker,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    cache_dir: Path,
    queue: QueueExport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lượt bị `superseded` giữa chừng → `finish` False: không ghi đè dòng, object của lượt bị xoá."""
    job = await queue()
    real_load = pack_module.load_pack

    async def load_then_supersede(*args: Any) -> Pack:
        """N49 mới thay lượt này trong lúc worker đang dựng."""
        async with session_scope(db_sessionmaker) as db:
            await state.supersede(db, job.project_id, fake_clock)
        return await real_load(*args)

    monkeypatch.setattr(tasks, "load_pack", load_then_supersede)
    await compile_model_export(
        db_sessionmaker,
        local_storage,
        fake_clock,
        export_id=job.export_id,
        argv_for=argv_for("ok"),
        build=_stub(_plan()),
    )

    row = await _row(db_sessionmaker, job.export_id)
    assert (row["status"], row["quality"]) == ("superseded", None)
    assert await _keys(local_storage, job.project_id, job.export_id) == []


async def test_run_model_export__J06(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Lượt đã `superseded`/`done` → `claim` None, no-op; `fail` muộn trên lượt đã xong không xoá gì."""
    job = await queue(status="done")

    await compile_model_export(
        db_sessionmaker,
        local_storage,
        fake_clock,
        export_id=job.export_id,
        argv_for=argv_for("fail"),
        build=_stub(_plan()),
    )
    await fail_model_export(
        db_sessionmaker, local_storage, fake_clock, export_id=job.export_id, code=state.MODEL_EXPORT_FAILED
    )

    assert (await _row(db_sessionmaker, job.export_id))["status"] == "done"
    assert await _keys(local_storage, job.project_id, job.export_id) == ["snapshot.json"]


async def test_run_model_export__J07(
    db_sessionmaker: Maker,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    cache_dir: Path,
    queue_model_export: QueueExport,
) -> None:
    """Gói không có manifest (hoặc snapshot không ghi phiên bản gói) → `MODEL_EXPORT_PACK_MISSING`."""
    for pack_version in (VERSION, ""):
        job = await queue_model_export()
        snapshot = json.dumps({"versions": {"pack": pack_version}}).encode()
        await _put(local_storage, model_export_object(job.project_id, job.export_id, MODEL_EXPORT_SNAPSHOT), snapshot)
        with pytest.raises(PermanentError, match=state.MODEL_EXPORT_PACK_MISSING):
            await compile_model_export(
                db_sessionmaker,
                local_storage,
                fake_clock,
                export_id=job.export_id,
                argv_for=argv_for("ok"),
                build=_stub(_plan()),
            )


async def test_load_pack_missing_table_and_bad_files(local_storage: LocalDiskStorage, tmp_path: Path) -> None:
    """Bảng thiếu → bảng rỗng + bản ghi `major`; tệp sai sha256, ngoài manifest, thoát khỏi thư mục → không có mặt."""
    files = {k: v for k, v in PACK_FILES.items() if k != "models.json"}
    files["textures/oak.jpg"] = b"bi sua"
    await _seed_pack(local_storage, files)

    pack = await load_pack(local_storage, VERSION, tmp_path)

    assert pack.tables["models"] == {}
    assert [r["id"] for r in pack.records] == ["models.json"]
    missing = await fetch_files(local_storage, pack, ["textures/oak.jpg", "models/chair.glb", "x.jpg", "../../etc"])
    assert missing == ["../../etc", "textures/oak.jpg", "x.jpg"]
    assert not (tmp_path / VERSION / "textures" / "oak.jpg").exists()
    assert await fetch_files(local_storage, pack, ["models/chair.glb"]) == []  # lần hai: trúng cache


def test_prune_cache_drops_other_versions(tmp_path: Path) -> None:
    """Vượt trần → xoá phiên bản khác, giữ phiên bản đang dùng; dưới trần → giữ hết."""
    for name in ("old", "cur"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "a.bin").write_bytes(b"x" * 10)
    prune_cache(tmp_path, "cur", 100)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cur", "old"]
    prune_cache(tmp_path, "cur", 15)
    assert [p.name for p in tmp_path.iterdir()] == ["cur"]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        glb(1),
        glb()[:-4] + b"!!!!",
        glb() + b"tail",
        b"glTF"
        + (2).to_bytes(4, "little")
        + (24).to_bytes(4, "little")
        + (4).to_bytes(4, "little")
        + b"JSON"
        + b"{{{{",
    ],
)
def test_check_glb_rejects_bad_files(tmp_path: Path, data: bytes) -> None:
    """Rỗng, sai version, JSON hỏng, độ dài header lệch tệp → `MODEL_EXPORT_OUTPUT_INVALID`."""
    path = tmp_path / "model.glb"
    path.write_bytes(data)
    with pytest.raises(PermanentError, match=state.MODEL_EXPORT_OUTPUT_INVALID):
        check_glb(path, 1 << 20)


def test_check_glb_respects_byte_cap(tmp_path: Path) -> None:
    """GLB hợp lệ nhưng vượt trần byte → lỗi; trong trần → trả kích thước."""
    path = tmp_path / "model.glb"
    path.write_bytes(glb())
    assert check_glb(path, 1 << 20) == len(glb())
    with pytest.raises(PermanentError):
        check_glb(path, 10)


def test_report_is_capped_at_one_mib() -> None:
    """Quá nhiều bản ghi → cắt dần (giữ bản nặng trước) cho tới ≤ 1 MiB."""
    rec = {"level": "L", "kind": "wall", "id": "W", "status": "degraded", "severity": "major", "reason": "x" * 500}
    report = tasks._report(_plan(), [rec] * 5000, {"open": []}, {})
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= tasks.REPORT_MAX_BYTES
    assert report["counts"] == {"wall:degraded": 5000}
    assert report["quality"] == "degraded"


def test_blender_argv_shape() -> None:
    """argv chạy `compile_plan.bpy` với ba đối số sau `--`."""
    argv = blender_argv("/opt/blender/blender", Path("p.json"), Path("pack"), Path("out"))
    assert argv[:2] == ["/opt/blender/blender", "-b"]
    assert argv[-4:] == ["--", "p.json", "pack", "out"]
    assert str(COMPILE_SCRIPT) in argv
    assert COMPILE_SCRIPT.is_file()


def test_geometry_records__J01_suggestion_minor() -> None:
    """Hình đề xuất M2 hở/suy biến → `suggestion:<rule>` `minor` (không kéo chất lượng); kiến trúc giữ mức cũ."""
    geometry = {
        "open": [
            {
                "name": "Skirting_ab",
                "appId": "DRESS-R1-x",
                "kind": "skirting",
                "boundary": 2,
                "nonManifold": 0,
                "suggestionId": "DRESS-R1-x",
                "rule": "R1",
            },
            {"name": "Wall_W-1", "appId": "W-1", "kind": "wall", "boundary": 1, "nonManifold": 0},
        ],
        "degenerateObjects": [
            {"name": "Rug_cd", "kind": "rug", "faces": 1, "suggestionId": "DRESS-D1-y", "rule": "D1"}
        ],
    }
    out = tasks.geometry_records(geometry)
    assert [(r["kind"], r["id"], r["severity"]) for r in out] == [
        ("suggestion:R1", "DRESS-R1-x", "minor"),
        ("geometry", "W-1", "critical"),
        ("suggestion:D1", "DRESS-D1-y", "minor"),
    ]


async def test_run_model_export__mit_licence(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, cache_dir: Path, queue: QueueExport
) -> None:
    """Có mẫu MIT (Pascal) được dựng → CREDITS.txt kèm nguyên văn `LICENSE-PASCAL-MIT.txt` của gói."""
    job = await queue()
    licence = b"MIT License\n\nCopyright (c) Pascal\n"
    files = {**PACK_FILES, tasks.PASCAL_LICENSE: licence}
    await _put(local_storage, model_export_pack_object(VERSION, tasks.PASCAL_LICENSE), licence)
    sha = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}
    await _put(local_storage, model_export_pack_object(VERSION, "manifest.json"), json.dumps({"files": sha}).encode())
    plan = _plan()
    plan["instances"][0]["props"] = {"license": "MIT (Pascal)", "author": "Pascal", "source": "github"}

    await compile_model_export(
        db_sessionmaker, local_storage, fake_clock, export_id=job.export_id, argv_for=argv_for("ok"), build=_stub(plan)
    )

    key = model_export_object(job.project_id, job.export_id, "CREDITS.txt")
    credits = b"".join([c async for c in local_storage.open_read(key)]).decode()
    assert "- models/chair.glb: Pascal · MIT (Pascal) · github" in credits
    assert credits.endswith(f"Giấy phép MIT của mẫu Pascal ({tasks.PASCAL_LICENSE}):\n\n{licence.decode()}")


def test_credits__mit_text_only_when_mit_model_built() -> None:
    """Mẫu MIT bị bỏ, hay gói không có tệp giấy phép → không nối văn bản MIT."""
    plan = _plan()
    plan["instances"][0]["props"] = {"license": "MIT (Pascal)", "author": "Pascal", "source": "github"}
    assert "MIT License" not in tasks._credits(plan, {"Furniture_F-1"}, "MIT License")
    assert tasks._credits(plan, set(), None).endswith("· CC0 · nguồn\n")
    assert tasks._credits(_plan(), set(), "MIT License").endswith("· CC0 · nguồn\n")
