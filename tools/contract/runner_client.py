"""Dựng chỗ chạy runner Node của bước 7 và gọi nó (B0-07 [6] "Dựng runner").

Bố cục (thư mục dựng chưa có hay rỗng; `check.py` mặc định một thư mục tạm mới):

- `src/` — bản chép `$APPFRONT_DIR/src` (AppFront @ SHA ghim, chỉ đọc);
- `runner.ts`, `schema-map.ts`, `context.ts`, `strict/`, `package.json` — chép từ `tools/contract`;
- `node_modules` — symlink tới `$CONTRACT_NODE_DIR/<băm package-lock>/node_modules`;
- `tsconfig.json` — `paths @/* → ./src/*`, `resolveJsonModule`.

Vì sao chép chứ không chạy thẳng trên `/appfront`: `zod` mà file AppFront nhập phải giải
được **lên thư mục cha** tới `node_modules` của harness (AppFront không được `pnpm install`,
B0-07 [9]), và mọi file phải thấy cùng một bản `zod` để `instanceof ZodType` đúng.

`node_modules` cài một lần cho mỗi băm lock, trên volume: `npm ci` vào thư mục tạm **cùng
volume**, kiểm `node --import tsx -e 1`, rồi đổi tên nguyên tử — nhiều lượt verify song song
lúc lạnh không làm hỏng nhau. Kho npm (`npm-cache`) cũng nằm trên volume: lượt lạnh tải mạng
một lần, mọi lượt sau chạy từ kho.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

PACKAGE_DIR: Final = Path(__file__).resolve().parent
RUNNER_FILES: Final = (
    "runner.ts",
    "schema-map.ts",
    "context.ts",
    "strict/refresh.ts",
    "strict/houseProposal.ts",
    "package.json",
)
F00A_MARKER: Final = Path("src/api/schemas/common.ts")
"""File F-00a mà mọi schema mới nhập; thiếu nó thì AppFront @ SHA chưa có hợp đồng mới."""

APPFRONT_ENV: Final = "APPFRONT_DIR"
DEFAULT_APPFRONT_DIR: Final = "/appfront"
NODE_ENV: Final = "CONTRACT_NODE_DIR"
DEFAULT_NODE_DIR: Final = "/work/contract-node"

NPM_TIMEOUT_S: Final = 600.0
"""Lượt lạnh tải ~10 MB (esbuild); trần rộng cho mạng chậm, lượt ấm chạy từ kho trong vài giây."""
NODE_TIMEOUT_S: Final = 120.0
"""Một lệnh runner giải hàng trăm mẫu mất vài giây; trần chỉ để cổng không treo."""
COPY_WORKERS: Final = 16

TSCONFIG: Final = {
    "compilerOptions": {
        "baseUrl": ".",
        "paths": {"@/*": ["./src/*"]},
        "resolveJsonModule": True,
        "module": "ESNext",
        "moduleResolution": "bundler",
        "target": "ES2022",
    }
}


class RunnerError(RuntimeError):
    """Runner hay `npm` hỏng (thoát khác 0, quá trần, stdout không phải JSON) — bước 7 hỏng."""


class AppFrontMissingError(RuntimeError):
    """`$APPFRONT_DIR` không có file F-00a — AppFront @ SHA chưa đủ hợp đồng mới."""


def appfront_dir_from_env() -> Path:
    """Thư mục AppFront @ SHA: `APPFRONT_DIR`, mặc định mount của container verify (ENV §2)."""
    return Path(os.environ.get(APPFRONT_ENV, DEFAULT_APPFRONT_DIR))


def node_dir_from_env() -> Path:
    """Thư mục đệm `node_modules`: `CONTRACT_NODE_DIR`, mặc định trên volume `/work` (ENV §2)."""
    return Path(os.environ.get(NODE_ENV, DEFAULT_NODE_DIR))


def check_appfront(appfront: Path) -> Path:
    """Trả lại `appfront` khi có file F-00a; không thì `AppFrontMissingError` (hỏng, không bỏ qua)."""
    if not (appfront / F00A_MARKER).is_file():
        raise AppFrontMissingError(f"AppFront @ SHA thiếu F-00a: không có {appfront / F00A_MARKER}")
    return appfront


def executable(name: str) -> str:
    """Đường tuyệt đối của một lệnh trên `PATH`; thiếu thì `RunnerError` (container phải có Node 20)."""
    found = shutil.which(name)
    if found is None:
        raise RunnerError(f"không tìm thấy lệnh {name!r} trên PATH")
    return found


def run_process(cmd: list[str], *, cwd: Path, timeout_s: float) -> str:
    """Chạy một lệnh cố định, trả stdout; thoát khác 0 hay quá trần → `RunnerError` kèm stderr."""
    try:
        done = subprocess.run(  # noqa: S603 — lệnh do harness dựng, không lấy từ đầu vào
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"{Path(cmd[0]).name} quá {timeout_s:g} s: {' '.join(cmd[1:])}") from exc
    if done.returncode != 0:
        raise RunnerError(f"{Path(cmd[0]).name} thoát {done.returncode}: {done.stderr.strip()[-4000:]}")
    return done.stdout


def lock_digest(package_dir: Path = PACKAGE_DIR) -> str:
    """Băm `package-lock.json`: đổi lock là một thư mục `node_modules` mới, không đè bản cũ."""
    return hashlib.sha256((package_dir / "package-lock.json").read_bytes()).hexdigest()[:16]


def publish(staging: Path, target: Path) -> None:
    """Đổi tên `staging` → `target` nguyên tử; bên khác đã đổi trước thì dùng bản của họ.

    `rename` lên một thư mục đích đã có nội dung luôn hỏng (Linux `ENOTEMPTY`, Windows
    `FileExistsError`), và đích chỉ xuất hiện sau khi một lượt đã cài **và** kiểm xong, nên
    thấy đích tồn tại là đủ tin. Lỗi khác (đích không có) thì ném lại.
    """
    try:
        staging.rename(target)
    except OSError:
        if not target.is_dir():
            raise


def ensure_node_modules(node_dir: Path, *, cache_dir: Path | None = None, package_dir: Path = PACKAGE_DIR) -> Path:
    """Thư mục có `node_modules` đã kiểm cho lock hiện tại; cài bằng `npm ci` khi chưa có.

    `cache_dir` là kho npm (mặc định `<node_dir>/npm-cache`, cùng volume) — test song song
    trỏ nó về kho thật để không tải mạng.
    """
    target = node_dir / lock_digest(package_dir)
    if target.is_dir():
        return target
    node_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=node_dir))
    try:
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(package_dir / name, staging / name)
        cache = cache_dir if cache_dir is not None else node_dir / "npm-cache"
        npm_ci = [executable("npm"), "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefer-offline"]
        run_process([*npm_ci, "--cache", str(cache)], cwd=staging, timeout_s=NPM_TIMEOUT_S)
        run_process([executable("node"), "--import", "tsx", "-e", "1"], cwd=staging, timeout_s=NODE_TIMEOUT_S)
        publish(staging, target)
    finally:
        # Sau `publish` thành công `staging` không còn; còn thì là bản thua cuộc hay bản hỏng.
        shutil.rmtree(staging, ignore_errors=True)
    return target


def copy_tree(source: Path, target: Path) -> None:
    """Chép cây `source` → `target` (chỉ nội dung), nhiều file cùng lúc.

    `$APPFRONT_DIR` trên máy Windows là bind mount: chi phí nằm ở độ trễ **mỗi file**, không ở
    dung lượng (đo 1 355 file, 16 MB: `copytree` 15 s, `cp -r` 9 s, 16 luồng 2,8 s). Trần
    `COPY_WORKERS` luồng; AppFront lớn gấp nhiều lần thì đổi sang đệm `src` theo SHA trên volume.
    """
    files: list[Path] = []
    for root, _dirs, names in os.walk(source):
        relative = Path(root).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
        files += [relative / name for name in names]
    with ThreadPoolExecutor(max_workers=COPY_WORKERS) as pool:
        # `list` để ngoại lệ của một luồng chép nổi lên ở đây, không bị nuốt.
        list(pool.map(lambda relative: shutil.copyfile(source / relative, target / relative), files))


def build_layout(appfront: Path, node_dir: Path, build_dir: Path) -> Path:
    """Dựng bố cục (docstring module) vào `build_dir` chưa có hay rỗng, và trả nó.

    Không xoá thư mục có sẵn: gõ nhầm `--build-dir` không làm mất gì, và không file cũ nào của
    lượt trước (schema FE đã bỏ ở SHA mới) lọt vào bố cục mới.
    """
    check_appfront(appfront)
    build_dir.mkdir(parents=True, exist_ok=True)
    if any(build_dir.iterdir()):
        raise RunnerError(f"thư mục dựng {build_dir} không rỗng: chỉ dựng vào thư mục mới")
    modules = ensure_node_modules(node_dir)
    copy_tree(appfront / "src", build_dir / "src")
    for name in RUNNER_FILES:
        (build_dir / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE_DIR / name, build_dir / name)
    (build_dir / "node_modules").symlink_to(modules / "node_modules", target_is_directory=True)
    (build_dir / "tsconfig.json").write_text(json.dumps(TSCONFIG, indent=2) + "\n", encoding="utf-8")
    return build_dir


def run_command(build_dir: Path, command: str, payload: object) -> Any:
    """`node --import tsx runner.ts <command> <file>` trong `build_dir`; trả JSON runner in ra.

    Đầu vào đi qua file tạm (không qua argv: hàng trăm mẫu vượt trần dòng lệnh).
    """
    fd, name = tempfile.mkstemp(prefix=f"{command}-", suffix=".json", dir=build_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    try:
        cmd = [executable("node"), "--import", "tsx", str(build_dir / "runner.ts"), command, name]
        stdout = run_process(cmd, cwd=build_dir, timeout_s=NODE_TIMEOUT_S)
    finally:
        Path(name).unlink()
    try:
        return json.loads(stdout)
    except ValueError as exc:
        raise RunnerError(f"runner {command} in ra không phải JSON: {stdout[:500]!r}") from exc
