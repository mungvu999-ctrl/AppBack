"""`build_pack` và CLI `build-pack` (M15): gói mẫu từ nhiều thư mục nguồn, chỉ ghi khi mọi id khớp sha."""

import functools
import hashlib
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from apps.api.library import cli
from apps.api.library.pack import build_pack
from packages.domain.library import FILE_CATALOGUE, FileItem


def _item(item_id: str, data: bytes) -> FileItem:
    """Mục tệp tổng hợp ghim sha của `data`."""
    return replace(FILE_CATALOGUE[0], id=item_id, model_sha256=hashlib.sha256(data).hexdigest())


def _write(directory: Path, name: str, data: bytes) -> None:
    """Ghi một tệp nguồn (tạo thư mục nếu chưa có)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)


def _sources(tmp_path: Path) -> tuple[tuple[Path, Path], tuple[FileItem, ...]]:
    """Hai nguồn: `a` ở nguồn 1; `b` (kèm ảnh) ở nguồn 2; `c` lệch sha ở nguồn 1, đúng ở nguồn 2."""
    first, second = tmp_path / "src1", tmp_path / "src2"
    _write(first, "a.glb", b"glb-a")
    _write(second, "b.glb", b"glb-b")
    _write(second, "b.png", b"png-b")
    _write(first, "c.glb", b"cu")
    _write(second, "c.glb", b"glb-c")
    return (first, second), (_item("a", b"glb-a"), _item("b", b"glb-b"), _item("c", b"glb-c"))


def test_build_pack_gathers_from_several_sources(tmp_path: Path) -> None:
    """Mỗi id lấy từ nguồn đầu tiên khớp sha (bỏ bản lệch ở nguồn trước); ảnh đi kèm nếu có."""
    sources, catalogue = _sources(tmp_path)
    out = tmp_path / "pack"
    report = build_pack(out, sources, catalogue)
    assert (report.copied, report.previews, report.missing, report.mismatched) == (3, 1, (), ())
    assert sorted(path.name for path in out.iterdir()) == ["a.glb", "b.glb", "b.png", "c.glb"]
    assert (out / "c.glb").read_bytes() == b"glb-c"
    assert (out / "b.png").read_bytes() == b"png-b"


def test_build_pack_refuses_incomplete_sources(tmp_path: Path) -> None:
    """Một id thiếu tệp, một id chỉ có bản lệch sha → báo cả hai, không tạo `out`."""
    sources, catalogue = _sources(tmp_path)
    _write(sources[1], "d.glb", b"ban-cu")
    catalogue = (*catalogue, _item("gone", b"x"), _item("d", b"ban-moi"))
    out = tmp_path / "pack"
    report = build_pack(out, sources, catalogue)
    assert (report.copied, report.missing, report.mismatched) == (0, ("gone",), ("d",))
    assert not out.exists()


def test_cli_build_pack_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Đủ → thoát 0 và bảng đếm; nguồn rỗng với danh mục thật → thoát 1, liệt kê 10 id đầu rồi `…`."""
    sources, catalogue = _sources(tmp_path)
    args = ["build-pack", str(tmp_path / "pack"), "--from", str(sources[0]), "--from", str(sources[1])]
    with monkeypatch.context() as patch:
        patch.setattr(cli, "build_pack", functools.partial(build_pack, catalogue=catalogue))
        assert cli.main(args) == cli.EXIT_OK
    assert capsys.readouterr().out == "copied=3 previews=1 missing=0 mismatched=0\n"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["build-pack", str(tmp_path / "out2"), "--from", str(empty)]) == cli.EXIT_FAIL
    captured = capsys.readouterr()
    assert captured.out == f"copied=0 previews=0 missing={len(FILE_CATALOGUE)} mismatched=0\n"
    assert captured.err.startswith(f"thiếu tệp: {FILE_CATALOGUE[0].id}, ")
    assert " …\n" in captured.err
    assert not (tmp_path / "out2").exists()


def test_cli_build_pack_lists_mismatched_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Chỉ có bản lệch sha → dòng `lệch sha: <id>` (ít id: không `…`), thoát 1."""
    _write(tmp_path / "src", "a.glb", b"khac")
    monkeypatch.setattr(cli, "build_pack", functools.partial(build_pack, catalogue=(_item("a", b"glb-a"),)))
    assert cli.main(["build-pack", str(tmp_path / "out"), "--from", str(tmp_path / "src")]) == cli.EXIT_FAIL
    assert "lệch sha: a\n" in capsys.readouterr().err


def _built(tmp_path: Path) -> tuple[tuple[Path, Path], tuple[FileItem, ...], Path]:
    """Một gói đã dựng xong từ `_sources` (để thử dựng lại đè lên)."""
    sources, catalogue = _sources(tmp_path)
    out = tmp_path / "pack"
    assert build_pack(out, sources, catalogue).copied == 3
    return sources, catalogue, out


def _listing(directory: Path) -> dict[str, bytes]:
    """Tên → nội dung mọi tệp trong thư mục."""
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def test_build_pack_failure_midway_keeps_old_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đĩa đầy ở tệp thứ hai: lỗi nổi lên, `out` cũ nguyên vẹn, không sót thư mục tạm."""
    sources, catalogue, out = _built(tmp_path)
    before = _listing(out)
    real = shutil.copyfile
    calls: list[str] = []

    def full_disk(src: Path, dst: Path) -> Path:
        """Tệp thứ hai ném `OSError` như đĩa đầy."""
        calls.append(str(dst))
        if len(calls) == 2:
            raise OSError(28, "no space left")
        return real(src, dst)

    monkeypatch.setattr(shutil, "copyfile", full_disk)
    with pytest.raises(OSError, match="no space"):
        build_pack(out, sources, catalogue)
    assert _listing(out) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["pack", "src1", "src2"]


def test_build_pack_rejects_corrupted_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bản chép lệch sha (nguồn đổi giữa lúc kiểm và chép) → `mismatched`, `out` cũ giữ nguyên, thư mục tạm bị xoá."""
    sources, catalogue, out = _built(tmp_path)
    before = _listing(out)

    def corrupt(_src: Path, dst: Path) -> Path:
        """Ghi byte khác nguồn."""
        Path(dst).write_bytes(b"hong")
        return Path(dst)

    monkeypatch.setattr(shutil, "copyfile", corrupt)
    report = build_pack(out, sources, catalogue)
    assert (report.copied, report.mismatched) == (0, ("a", "b", "c"))
    assert _listing(out) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["pack", "src1", "src2"]


def test_build_pack_rebuild_drops_stale_preview(tmp_path: Path) -> None:
    """Dựng lại sau khi nguồn mất `b.png`: gói mới không còn ảnh cũ, chỉ đúng các tệp được chọn."""
    sources, catalogue, out = _built(tmp_path)
    (sources[1] / "b.png").unlink()
    report = build_pack(out, sources, catalogue)
    assert (report.copied, report.previews) == (3, 0)
    assert sorted(_listing(out)) == ["a.glb", "b.glb", "c.glb"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["pack", "src1", "src2"]


def test_build_pack_second_rename_fails_restores_old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đổi tên thư mục tạm thành `out` hỏng: bản cũ trở về `out`, lỗi nổi lên, không sót thư mục tạm hay bản lưu."""
    sources, catalogue, out = _built(tmp_path)
    before = _listing(out)
    real = Path.rename

    def flaky(self: Path, target: Path) -> Path:
        """Hỏng khi thư mục tạm (không phải bản `.old-`) đổi tên thành `out`."""
        if Path(target) == out and ".old-" not in self.name:
            raise OSError(13, "bị khoá")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError, match="bị khoá"):
        build_pack(out, sources, catalogue)
    assert _listing(out) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["pack", "src1", "src2"]


def test_build_pack_cleanup_failure_only_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Xoá bản cũ hỏng sau khi đã xuất bản: lượt dựng vẫn đạt, chỉ cảnh báo; bản cũ sót lại không chặn lần sau."""
    sources, catalogue, out = _built(tmp_path)
    (sources[1] / "b.png").unlink()
    real = shutil.rmtree

    def stuck(path: Path, *args: object, **kwargs: object) -> None:
        """Bản `.old-` không xoá được (tệp đang mở)."""
        if ".old-" in Path(path).name:
            raise OSError(16, "đang dùng")
        real(path)

    monkeypatch.setattr(shutil, "rmtree", stuck)
    assert build_pack(out, sources, catalogue).copied == 3
    assert sorted(_listing(out)) == ["a.glb", "b.glb", "c.glb"]
    assert "library_pack_old_left" in caplog.text
    assert build_pack(out, sources, catalogue).copied == 3
    assert len(list(tmp_path.glob(".pack.old-*"))) == 2


def test_build_pack_recovers_interrupted_swap(tmp_path: Path) -> None:
    """Lượt trước chết giữa hai lần đổi tên (`out` vắng, còn `.old-`): lần dựng sau, kể cả lượt hỏng, trả `out` về."""
    sources, catalogue, out = _built(tmp_path)
    before = _listing(out)
    out.rename(tmp_path / ".pack.old-cat")
    report = build_pack(out, sources, (*catalogue, _item("gone", b"x")))
    assert report.missing == ("gone",)
    assert _listing(out) == before
    assert not list(tmp_path.glob(".pack.old-*"))
