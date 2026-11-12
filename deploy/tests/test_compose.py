"""Test tĩnh cho `deploy/compose/{base,dev,ci,prod}.yml` (hợp đồng §2, prompt [6]/[8]).

Đọc trực tiếp bằng `compose_reader` (tự giải `extends`) — container verify không có
`docker` CLI nên không gọi được `docker compose config`.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from deploy.tests.compose_reader import load_yaml, resolve_all_services
from deploy.tests.support import require_path

ENVS = ("dev", "ci", "prod")


def _compose_path(env: str) -> Path:
    """`deploy/compose/<env>.yml`; `fail` rõ nếu chưa có."""
    return require_path(f"deploy/compose/{env}.yml")


@cache
def _resolved_services(env: str) -> dict[str, dict[str, Any]]:
    """Dịch vụ của `<env>.yml` sau khi giải `extends` từ `base.yml`.

    File compose không đổi trong một lượt pytest; ~50 test gọi hàm này nên nhớ theo
    `env` thay vì đọc + giải `extends` lại mỗi lần (đo trước/sau ở `do-truoc-sau.md`)."""
    require_path("deploy/compose/base.yml")
    return resolve_all_services(_compose_path(env))


@cache
def _raw_doc(env: str) -> dict[str, Any]:
    """Nội dung thô (chưa giải `extends`) của `<env>.yml` — dùng để đọc `networks`/
    `volumes` cấp cao, phần `extends` không mang theo."""
    return load_yaml(_compose_path(env))


def _cmd_text(value: Any) -> str:
    """`command`/`entrypoint` có thể là chuỗi hoặc danh sách token; gộp về một chuỗi
    để tìm cờ dòng lệnh bằng `in`."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def _volume_targets(volumes: Any) -> list[str]:
    """Đích mount (`target`) của mọi entry `volumes`, chấp nhận cả dạng ngắn
    `"src:dst[:mode]"` lẫn dạng dài `{type, source, target}`."""
    targets: list[str] = []
    for item in volumes or []:
        if isinstance(item, dict):
            if item.get("target"):
                targets.append(str(item["target"]))
            continue
        parts = str(item).split(":")
        if len(parts) >= 2:
            targets.append(parts[1])
    return targets


def _network_names(value: Any) -> list[str]:
    """Tên mạng một dịch vụ nối vào, bất kể `networks` là list tên hay map tên→cấu hình."""
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.keys())
    return list(value)


def _is_ml_service(service: dict[str, Any]) -> bool:
    """Dịch vụ chạy ảnh `ml` (image chứa `appback-ml`, hoặc `build.dockerfile` là
    `ml.Dockerfile`) — bao gồm cả `ml-gpu` dưới profile `gpu` (hợp đồng B0-08 §2)."""
    image = str(service.get("image") or "")
    build = service.get("build")
    return "appback-ml" in image or (
        isinstance(build, dict) and str(build.get("dockerfile", "")).endswith("ml.Dockerfile")
    )


@pytest.mark.parametrize("env", ENVS)
def test_compose_every_service_has_logging(env: str) -> None:
    """Mọi dịch vụ đã giải extends có `logging` `json-file`, `max-size: 10m`, `max-file: 5`."""
    for name, service in _resolved_services(env).items():
        logging = service.get("logging")
        assert logging, f"{env}/{name}: thiếu logging"
        assert logging.get("driver") == "json-file", f"{env}/{name}: logging.driver phải là json-file"
        options = logging.get("options") or {}
        assert options.get("max-size") == "10m", f"{env}/{name}: logging max-size phải là 10m"
        assert str(options.get("max-file")) == "5", f"{env}/{name}: logging max-file phải là 5"


@pytest.mark.parametrize("env", ENVS)
def test_compose_redis_broker_tuning(env: str) -> None:
    """`redis-broker`: `--maxmemory-policy noeviction` và `--appendonly yes`."""
    services = _resolved_services(env)
    cmd = _cmd_text(services["redis-broker"].get("command"))
    assert "--maxmemory-policy noeviction" in cmd, f"{env}: redis-broker thiếu noeviction"
    assert "--appendonly yes" in cmd, f"{env}: redis-broker thiếu appendonly yes"


@pytest.mark.parametrize("env", ENVS)
def test_compose_redis_cache_tuning_and_no_volume(env: str) -> None:
    """`redis-cache`: `--maxmemory 256mb`, `--maxmemory-policy allkeys-lru`, không volume."""
    cache = _resolved_services(env)["redis-cache"]
    cmd = _cmd_text(cache.get("command"))
    assert "--maxmemory 256mb" in cmd, f"{env}: redis-cache thiếu maxmemory 256mb"
    assert "--maxmemory-policy allkeys-lru" in cmd, f"{env}: redis-cache thiếu allkeys-lru"
    assert not cache.get("volumes"), f"{env}: redis-cache không được có volume"


def test_compose_prod_only_web_publishes_ports() -> None:
    """`prod`: chỉ `web` được công bố `ports` (Postgres/Redis/MinIO không lộ ra host)."""
    for name, service in _resolved_services("prod").items():
        if name == "web":
            continue
        assert not service.get("ports"), f"prod/{name}: không được công bố ports"


def test_compose_dev_ports_bind_localhost_only() -> None:
    """`dev`: mọi `ports` công bố ra chỉ bind `127.0.0.1:`, không mở ra LAN."""
    for name, service in _resolved_services("dev").items():
        for port in service.get("ports") or []:
            assert str(port).startswith("127.0.0.1:"), f"dev/{name}: port {port!r} phải bind 127.0.0.1"


@pytest.mark.parametrize("env", ["dev", "ci"])
@pytest.mark.parametrize("service_name", ["api", "worker", "beat"])
def test_compose_waits_for_migrate_completed(env: str, service_name: str) -> None:
    """`api`/`worker`/`beat` (dev, ci) đợi `migrate` chạy xong (`service_completed_successfully`)."""
    depends = _resolved_services(env)[service_name].get("depends_on") or {}
    migrate_dep = depends.get("migrate")
    assert isinstance(migrate_dep, dict), f"{env}/{service_name}: thiếu depends_on.migrate"
    assert migrate_dep.get("condition") == "service_completed_successfully", (
        f"{env}/{service_name}: depends_on.migrate.condition sai"
    )


@pytest.mark.parametrize("env", ENVS)
def test_compose_beat_command_has_schedule_path(env: str) -> None:
    """`beat` chạy `celery beat -s /tmp/…` (lịch bền trong volume, không ghi vào ảnh)."""
    services = _resolved_services(env)
    if "beat" not in services:
        pytest.fail(f"{env}: thiếu dịch vụ beat")
    assert "-s /tmp/" in _cmd_text(services["beat"].get("command")), f"{env}: beat thiếu -s /tmp/"


@pytest.mark.parametrize("env", ENVS)
def test_compose_beat_healthcheck_checks_schedule_file(env: str) -> None:
    """`beat` có healthcheck thật (tệp lịch `-s`), không `disable` — `compose up --wait` hỏng khi thiếu."""
    healthcheck = _resolved_services(env)["beat"].get("healthcheck") or {}
    assert not healthcheck.get("disable"), f"{env}: beat tắt healthcheck"
    assert "celerybeat-schedule" in _cmd_text(healthcheck.get("test")), f"{env}: beat không kiểm tệp lịch"


@pytest.mark.parametrize("env", ENVS)
def test_compose_no_container_name_for_api_and_worker(env: str) -> None:
    """Không `container_name` ở `api`/`worker` (B0-10 chạy hai bản song song lúc deploy)."""
    services = _resolved_services(env)
    for name in ("api", "worker"):
        assert "container_name" not in services[name], f"{env}/{name}: không được có container_name"


@pytest.mark.parametrize("env", ENVS)
def test_compose_no_latest_or_missing_image_tag(env: str) -> None:
    """Không dịch vụ nào dùng `image` thiếu tag hay tag `latest`."""
    for name, service in _resolved_services(env).items():
        image = service.get("image")
        if not image:
            continue
        assert ":" in image, f"{env}/{name}: image {image!r} thiếu tag"
        tag = image.rsplit(":", 1)[1]
        assert tag != "latest", f"{env}/{name}: image {image!r} dùng tag latest"


@pytest.mark.parametrize("env", ENVS)
def test_compose_worker_concurrency_and_mem_limit(env: str) -> None:
    """`worker`: `--concurrency ${WORKER_CONCURRENCY:-2}` và `mem_limit: ${WORKER_MEM_LIMIT:-3g}`."""
    worker = _resolved_services(env)["worker"]
    assert "--concurrency ${WORKER_CONCURRENCY:-2}" in _cmd_text(worker.get("command")), (
        f"{env}: worker thiếu --concurrency ${{WORKER_CONCURRENCY:-2}}"
    )
    assert worker.get("mem_limit") == "${WORKER_MEM_LIMIT:-3g}", f"{env}: worker mem_limit sai"


@pytest.mark.parametrize("env", ENVS)
def test_compose_ml_concurrency_runtime_and_train_on_cpu(env: str) -> None:
    """`ml`: không ghi cứng `--concurrency` (celery_main đọc `ML_INFER_CONCURRENCY` lúc chạy, nên
    override `environment:` có tác dụng); `ml-train`: `ML_DEVICE=cpu` để không tranh VRAM với `ml` (PERF-03)."""
    services = _resolved_services(env)
    assert "--concurrency" not in _cmd_text(services["ml"].get("command")), f"{env}: ml ghi cứng --concurrency"
    assert services["ml"]["environment"].get("ML_INFER_CONCURRENCY") == "${ML_INFER_CONCURRENCY:-3}"
    assert services["ml-train"]["environment"].get("ML_DEVICE") == "cpu", f"{env}: ml-train phải ML_DEVICE=cpu"


def test_compose_dev_build_services_tagged_local_dev() -> None:
    """`dev`: dịch vụ có `build` phải có `image: appback-*:dev` (build một lần, dùng lại tag)."""
    pattern = re.compile(r"^appback-[a-z0-9_-]+:dev$")
    for name, service in _resolved_services("dev").items():
        if "build" not in service:
            continue
        image = service.get("image", "")
        assert pattern.match(image), f"dev/{name}: build service cần image appback-*:dev, có {image!r}"


@pytest.mark.parametrize("env", ENVS)
def test_compose_ml_services_are_hardened(env: str) -> None:
    """Mọi dịch vụ chạy ảnh `ml` (kể cả `ml-gpu` dưới profile `gpu`): `init: true`,
    `shm_size`, `mem_limit`, volume đích `/tmp`, mọi mạng nối vào là `internal: true`,
    và khoá MinIO riêng (`S3_ACCESS_KEY`/`S3_SECRET_KEY` trỏ biến `_ML_`)."""
    services = _resolved_services(env)
    raw_networks = _raw_doc(env).get("networks") or {}
    ml_services = {name: svc for name, svc in services.items() if _is_ml_service(svc)}
    assert ml_services, f"{env}: không thấy dịch vụ nào chạy ảnh ml"
    for name, svc in ml_services.items():
        assert svc.get("init") is True, f"{env}/{name}: thiếu init: true"
        assert svc.get("shm_size"), f"{env}/{name}: thiếu shm_size"
        assert svc.get("mem_limit"), f"{env}/{name}: thiếu mem_limit"
        assert "/tmp" in _volume_targets(svc.get("volumes")), (  # noqa: S108 — kiểm đích mount, không tạo file
            f"{env}/{name}: thiếu volume đích /tmp"
        )
        joined_networks = _network_names(svc.get("networks"))
        assert joined_networks, f"{env}/{name}: không nối mạng nào"
        for net_name in joined_networks:
            net_def = raw_networks.get(net_name) or {}
            assert net_def.get("internal") is True, f"{env}/{name}: mạng {net_name} phải internal: true"
        env_vars = svc.get("environment") or {}
        assert env_vars.get("S3_ACCESS_KEY") == "${S3_ML_ACCESS_KEY}", f"{env}/{name}: S3_ACCESS_KEY phải là khoá ml"
        assert env_vars.get("S3_SECRET_KEY") == "${S3_ML_SECRET_KEY}", f"{env}/{name}: S3_SECRET_KEY phải là khoá ml"


_PROD_ONE_SHOT_SERVICES = {"migrate", "minio-init"}
_APP_IMAGE_SERVICES = {"api", "worker", "beat", "ml", "ml-train", "ml-gpu", "bake", "web"}
_PROD_IMAGE_TAG_RE = re.compile(r":\$\{IMAGE_TAG(:[?-][^}]*)?\}$")
_APPBACK_ENV_FILE = "/etc/appback/appback.env"
_ML_ENV_FILE = "/etc/appback/ml.env"


def _env_file_paths(service: dict[str, Any]) -> list[str]:
    """Đường của mọi entry `env_file`, nhận cả dạng ngắn (chuỗi) lẫn dạng dài `{path, required}`."""
    raw = service.get("env_file")
    items = [raw] if isinstance(raw, (str, dict)) else list(raw or [])
    return [str(item["path"]) if isinstance(item, dict) else str(item) for item in items]


def test_compose_prod_image_naming_restart_and_env_file() -> None:
    """`prod`: ảnh do B0-08 dựng (`api`/`worker`/`beat`/`ml`/`ml-gpu`/`web`) theo khuôn
    `${IMAGE_REGISTRY:+${IMAGE_REGISTRY}/}appback-<x>:${IMAGE_TAG}` (ảnh bên thứ ba như
    `postgres`/`redis`/`minio` không theo khuôn này) — `${IMAGE_TAG}` được kèm hậu tố
    `:?…`/`:-…` cũng đạt (bắt buộc/giá trị mặc định, vẫn dùng đúng biến); mọi dịch vụ
    trừ `ml`/`ml-gpu` có `env_file` đúng `/etc/appback/appback.env` (hai dịch vụ ml dùng
    tệp riêng — `test_compose_prod_ml_env_file_is_not_appback_env`, NO-085); dịch vụ dài
    hạn (không phải job một lần như `migrate`/`minio-init`) có thêm `restart: unless-stopped`."""
    services = _resolved_services("prod")
    for name, service in services.items():
        image = service.get("image")
        if image and name in _APP_IMAGE_SERVICES:
            assert image.startswith("${IMAGE_REGISTRY:+${IMAGE_REGISTRY}/}appback-"), (
                f"prod/{name}: image {image!r} sai khuôn registry"
            )
            assert _PROD_IMAGE_TAG_RE.search(image), f"prod/{name}: image {image!r} thiếu ${{IMAGE_TAG}}"
        if name not in _ML_SERVICES_BY_ENV["prod"]:
            assert _env_file_paths(service) == [_APPBACK_ENV_FILE], (
                f"prod/{name}: env_file phải đúng {_APPBACK_ENV_FILE}"
            )
        if name not in _PROD_ONE_SHOT_SERVICES:
            assert service.get("restart") == "unless-stopped", f"prod/{name}: thiếu restart: unless-stopped"


def test_compose_prod_no_mailpit_and_minio_gated_by_profile() -> None:
    """`prod`: không `mailpit`; `minio`/`minio-init` chỉ chạy dưới `profiles: [minio]`."""
    services = _resolved_services("prod")
    assert "mailpit" not in services, "prod: không được có mailpit"
    for name in ("minio", "minio-init"):
        if name in services:
            assert services[name].get("profiles") == ["minio"], f"prod/{name}: profiles phải là [minio]"


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_compose_minio_cors_allow_origin_is_public_base_url(env: str) -> None:
    """`minio` (dev, prod): `MINIO_API_CORS_ALLOW_ORIGIN` = `${PUBLIC_BASE_URL}` (không `*`)."""
    minio_env = _resolved_services(env)["minio"].get("environment") or {}
    assert minio_env.get("MINIO_API_CORS_ALLOW_ORIGIN") == "${PUBLIC_BASE_URL}", (
        f"{env}: minio CORS allow-origin phải là ${{PUBLIC_BASE_URL}}"
    )


_CI_DATA_SERVICES = ("postgres", "redis-broker", "minio")


def test_compose_ci_has_no_named_persistent_volumes() -> None:
    """`ci`: không volume có tên (bền) cho **dữ liệu** (Postgres/Redis/MinIO) — mỗi
    lượt chạy sạch từ đầu. `ml-tmp` (đĩa tạm, không phải dữ liệu bền) không bị luật
    này chi phối."""
    top_volumes = _raw_doc("ci").get("volumes") or {}
    forbidden = {"pg-data", "redis-broker-data", "minio-data", "local-storage"}
    leaked = forbidden & set(top_volumes)
    assert not leaked, f"ci: volume dữ liệu bền vẫn khai ở cấp cao ({sorted(leaked)})"
    services = _resolved_services("ci")
    for name in _CI_DATA_SERVICES:
        for item in services[name].get("volumes") or []:
            if isinstance(item, dict):
                continue
            source = str(item).split(":")[0]
            assert source.startswith(("/", ".")), f"ci/{name}: volume {item!r} dùng volume có tên (bền)"


def test_compose_ci_storage_backend_is_s3() -> None:
    """`ci`: `STORAGE_BACKEND=s3` (dùng MinIO của hạ tầng CI, không đĩa cục bộ)."""
    api_env = _resolved_services("ci")["api"].get("environment") or {}
    assert api_env.get("STORAGE_BACKEND") == "s3", "ci: STORAGE_BACKEND phải là s3"


def test_compose_app_env_always_required_never_defaulted() -> None:
    """`APP_ENV` chỉ xuất hiện dạng `${APP_ENV:?…}` ở mọi file compose — không `:-`."""
    texts = [require_path(f"deploy/compose/{f}.yml").read_text(encoding="utf-8") for f in ("base", *ENVS)]
    combined = "\n".join(texts)
    assert "${APP_ENV:-" not in combined, "APP_ENV không được có giá trị mặc định (:-)"
    assert re.search(r"\$\{APP_ENV:\?", combined), "APP_ENV phải bắt buộc qua ${APP_ENV:?…} ở đâu đó"


_CELERY_TASK_STREAM_ENV = {
    "CELERY_VISIBILITY_TIMEOUT_S": "${CELERY_VISIBILITY_TIMEOUT_S:-7200}",
    "TASK_TIME_LIMIT_S": "${TASK_TIME_LIMIT_S:-3600}",
    "TASK_SOFT_TIME_LIMIT_S": "${TASK_SOFT_TIME_LIMIT_S:-3300}",
    "TASK_RETRY_BACKOFF_S": "${TASK_RETRY_BACKOFF_S:-10,60,300}",
    "STREAM_MAXLEN": "${STREAM_MAXLEN:-1000}",
}
# ml/ml-gpu không có trong mọi env (ci không chạy ml-gpu — không GPU trong CI).
_ML_SERVICES_BY_ENV = {
    "dev": ("ml", "ml-train", "ml-gpu"),
    "ci": ("ml", "ml-train"),
    "prod": ("ml", "ml-train", "ml-gpu"),
}


@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("service_name", ["api", "worker", "beat", "migrate"])
def test_compose_app_env_has_celery_task_stream_and_secret_previous(env: str, service_name: str) -> None:
    """`&app-env` truyền `CELERY_VISIBILITY_TIMEOUT_S`, `TASK_TIME_LIMIT_S`,
    `TASK_SOFT_TIME_LIMIT_S`, `TASK_RETRY_BACKOFF_S`, `STREAM_MAXLEN`,
    `SECRET_KEY_PREVIOUS` với mặc định đúng `packages/messaging/settings.py`,
    `packages/core/settings.py` (NO-021 phần còn lại, review 2026-09-22 #10)."""
    env_vars = _resolved_services(env)[service_name].get("environment") or {}
    for key, expected in _CELERY_TASK_STREAM_ENV.items():
        assert env_vars.get(key) == expected, f"{env}/{service_name}: thiếu {key}={expected}"
    assert env_vars.get("SECRET_KEY_PREVIOUS") == "${SECRET_KEY_PREVIOUS:-}"


@pytest.mark.parametrize("env", ENVS)
def test_compose_ml_env_has_celery_task_stream_vars(env: str) -> None:
    """`ml`/`ml-gpu` gọi `create_celery("ml")` (đọc cùng cấu hình
    `CELERY_VISIBILITY_TIMEOUT_S`/`TASK_*`/`STREAM_MAXLEN` như `worker` — BE-00 §7:
    giá trị NHỎ NHẤT giữa các app dùng chung broker thắng) nhưng thiếu 5 khoá này
    ở dev/ci trước đây (review 2026-09-22 #17, NO-021). `SECRET_KEY_PREVIOUS`
    không áp dụng cho `ml` (không xác thực JWT, chỉ `packages/core/keys.py` đọc)."""
    services = _resolved_services(env)
    for service_name in _ML_SERVICES_BY_ENV[env]:
        env_vars = services[service_name].get("environment") or {}
        for key, expected in _CELERY_TASK_STREAM_ENV.items():
            assert env_vars.get(key) == expected, f"{env}/{service_name}: thiếu {key}={expected}"


_ML_FORBIDDEN_ENV = ("SECRET_KEY", "PUBLIC_BASE_URL", "REDIS_CACHE_URL")


@pytest.mark.parametrize("env", ENVS)
def test_compose_ml_env_has_no_signing_or_cache_vars(env: str) -> None:
    """`ml`/`ml-gpu` không nhận `SECRET_KEY`/`PUBLIC_BASE_URL`/`REDIS_CACHE_URL`
    (NO-085): tiến trình nạp trọng số ngoài không được cầm khoá gốc ký JWT (BE-00
    §2.1/§9) và không tới được `redis-cache`. `celery_main` đọc `APP_ENV` qua
    `MlEnvSettings` (FIX-089), `redis_cache_url` tuỳ chọn (FIX-091)."""
    services = _resolved_services(env)
    for service_name in _ML_SERVICES_BY_ENV[env]:
        env_vars = services[service_name].get("environment") or {}
        leaked = [key for key in _ML_FORBIDDEN_ENV if key in env_vars]
        assert not leaked, f"{env}/{service_name}: không được truyền {leaked}"


def test_compose_prod_ml_env_file_is_not_appback_env() -> None:
    """`prod` `ml`/`ml-gpu`: `env_file` đúng `/etc/appback/ml.env`, không bao giờ
    `appback.env` (NO-085). Gỡ biến khỏi `environment:` chưa đủ: `env_file` nạp NGUYÊN
    tệp vào container, trỏ tệp chung là `ml` lại cầm `SECRET_KEY`, `DATABASE_URL`, mật
    khẩu SMTP — trái tách quyền của BE-00 §2.1/§9."""
    services = _resolved_services("prod")
    for service_name in _ML_SERVICES_BY_ENV["prod"]:
        assert _env_file_paths(services[service_name]) == [_ML_ENV_FILE], (
            f"prod/{service_name}: env_file phải đúng {_ML_ENV_FILE}, không dùng tệp chung {_APPBACK_ENV_FILE}"
        )


def test_compose_web_healthcheck_single_source() -> None:
    """`web`: một nguồn healthcheck (review 2026-09-22 #3) — `base.yml` không khai
    (dev/ci thừa kế `HEALTHCHECK` của ảnh, cổng 8080 http); `prod` đè bằng cổng
    8443 https thật (8080 ở prod chỉ redirect, `curl -f` không coi 301 là lỗi nên
    HEALTHCHECK của ảnh "đạt" mà không kiểm gì, probe Q3)."""
    base_doc = load_yaml(require_path("deploy/compose/base.yml"))
    base_web = (base_doc.get("services") or {}).get("web") or {}
    assert "healthcheck" not in base_web, "base.yml: web không được khai healthcheck (một nguồn)"

    prod_web = _resolved_services("prod")["web"]
    test_cmd = _cmd_text((prod_web.get("healthcheck") or {}).get("test"))
    assert "http://" not in test_cmd, f"prod: healthcheck web không được gọi http:// ({test_cmd!r})"
    assert "8443" in test_cmd, "prod: healthcheck web thiếu cổng 8443"
    assert "https://127.0.0.1:8443/index.html" in test_cmd, "prod: healthcheck web sai đường"


@pytest.mark.parametrize("env", ENVS)
def test_compose_appback_network_has_fixed_subnet(env: str) -> None:
    """Mạng `appback` khai `ipam.config[].subnet` cố định (không để Docker tự chọn)."""
    appback_net = (_raw_doc(env).get("networks") or {}).get("appback") or {}
    configs = (appback_net.get("ipam") or {}).get("config") or []
    assert configs, f"{env}: mạng appback thiếu ipam.config[]"
    assert configs[0].get("subnet"), f"{env}: mạng appback thiếu ipam.config[].subnet"


_METRICS_PORT = "9464"
_SCRAPE_CONFIG = "deploy/observability/prometheus.yml"


def test_compose_metrics_port_is_never_published() -> None:
    """NO-162: cổng exporter 9464 chỉ dùng trong mạng compose. `METRICS_HOST` mặc
    định `0.0.0.0` (B7-01 [2], có `# noqa: S104` vì "compose không công bố nó") —
    khẳng định đó phải có test, nếu không một dòng `ports` lỡ tay là mở thẳng số
    liệu nội bộ ra host."""
    for env in ENVS:
        for name, service in _resolved_services(env).items():
            for published in service.get("ports") or []:
                assert _METRICS_PORT not in str(published), (
                    f"{env}/{name}: publish cổng metric {published!r} — 9464 phải ở trong mạng compose"
                )


def test_compose_scrape_job_targets_api_metrics_port() -> None:
    """NO-162: có cấu hình scrape nội bộ trỏ `api:9464` — cổng exporter mà không ai
    thu thập thì số liệu của B7-01 không tới được đâu cả. Target dùng TÊN DỊCH VỤ
    (không `127.0.0.1`) vì bộ thu thập phải chạy trong mạng `appback`."""
    raw = require_path(_SCRAPE_CONFIG).read_text(encoding="utf-8")
    config = load_yaml(require_path(_SCRAPE_CONFIG))
    targets = [
        target for job in config["scrape_configs"] for static in job["static_configs"] for target in static["targets"]
    ]
    assert f"api:{_METRICS_PORT}" in targets, f"{_SCRAPE_CONFIG}: thiếu target api:{_METRICS_PORT}, có {targets}"
    for loopback in ("127.0.0.1", "localhost"):
        assert loopback not in raw, f"{_SCRAPE_CONFIG}: target phải là tên dịch vụ trong mạng compose, không {loopback}"


def test_compose_ci_api_publishes_no_host_port() -> None:
    """NO-118: `api` của `ci.yml` không publish cổng host. Cổng cố định
    (`127.0.0.1:${API_HOST_PORT}:8000`) làm `docker compose up --scale api=2` hỏng
    vì trùng cổng, mà scale 2 là cách `deploy.sh` đổi phiên bản không gián đoạn
    (B0-10 [8]); trước đây B0-10 phải sinh override `ports: !reset []` ngoài repo.
    smoke/e2e gọi `/api/*` qua `web`, đúng đường đi thật của trình duyệt."""
    api = _resolved_services("ci")["api"]
    assert not api.get("ports"), f"ci: api không được publish cổng host, đang có {api.get('ports')}"
    web = _resolved_services("ci")["web"]
    assert web.get("ports"), "ci: web phải publish cổng host để smoke/e2e đi qua nó"


def _scrape_targets() -> set[str]:
    """Mọi target của `prometheus.yml`, dạng `host:port`."""
    config = load_yaml(require_path(_SCRAPE_CONFIG))
    return {t for job in config["scrape_configs"] for static in job["static_configs"] for t in static["targets"]}


def _celery_concurrency(service: dict[str, Any]) -> int:
    """`--concurrency` của lệnh celery trong dịch vụ; `${VAR:-N}` lấy mặc định N."""
    command = [str(part) for part in service["command"]]
    raw = command[command.index("--concurrency") + 1]
    m = re.fullmatch(r"\$\{\w+:-(\d+)\}|(\d+)", raw)
    assert m, f"--concurrency {raw!r} không đọc được"
    return int(m.group(1) or m.group(2))


@pytest.mark.parametrize("name", ["worker"])
def test_compose_scrape_covers_celery_worker_port_range__no201(name: str) -> None:
    """NO-201: worker Celery prefork phơi `/metrics` ở `METRICS_PORT … +N-1`
    (`metrics_port_for_process`, N = `--concurrency`); `prometheus.yml` phải có đủ
    target của dải đó. Cổng gốc đọc từ `ObservabilitySettings`, N từ `base.yml` —
    không chép số cứng (R-07)."""
    from packages.observability.settings import ObservabilitySettings

    base_port = ObservabilitySettings.model_fields["metrics_port"].default
    n = _celery_concurrency(_resolved_services("ci")[name])
    expected = {f"{name}:{base_port + i}" for i in range(n)}
    missing = expected - _scrape_targets()
    assert not missing, f"{_SCRAPE_CONFIG}: thiếu target {sorted(missing)}"


@pytest.mark.parametrize("env", ["dev", "ci"])
def test_compose_mailpit_disables_rdns__no212(env: str) -> None:
    """NO-212: Mailpit dev/ci tắt tra rDNS — máy có PTR chậm (~11s) làm chậm mọi thư."""
    environment = _resolved_services(env)["mailpit"].get("environment") or {}
    assert str(environment.get("MP_SMTP_DISABLE_RDNS")).lower() == "true", f"{env}: mailpit còn bật rDNS"


def test_compose_ml_backend_and_feature_flags_wired() -> None:
    """`api` nhận `FEATURE_FLAGS` (mặc định `{}`) ở mọi môi trường; `ml` nhận `ML_BACKEND` (mặc định onnx)
    ở `dev`/`ci` — prod đọc `/etc/appback/ml.env` nên `environment:` ở đó sẽ đè nó (NO-085)."""
    for env in ENVS:
        api_env = _resolved_services(env)["api"]["environment"]
        assert api_env.get("FEATURE_FLAGS") == "${FEATURE_FLAGS:-{}}", f"{env}: api thiếu FEATURE_FLAGS"
    for env in ("dev", "ci"):
        ml_env = _resolved_services(env)["ml"]["environment"]
        assert ml_env.get("ML_BACKEND") == "${ML_BACKEND:-onnx}", f"{env}: ml thiếu ML_BACKEND"


def test_compose_api_runs_several_workers_with_small_db_pools() -> None:
    """`api` chạy `--workers ${API_WORKERS:-4}` và báo cùng số cho app (chia trần SSE); pool DB
    mỗi worker 5 + 3 (+ 5 claim) để 4 worker = 52 kết nối < 60 trên 100 `max_connections` (PERF-03 P4)."""
    for env in ENVS:
        api = _resolved_services(env)["api"]
        command = [str(part) for part in api["command"]]
        assert command[command.index("--workers") + 1] == "${API_WORKERS:-4}", f"{env}: api thiếu --workers"
        api_env = api["environment"]
        assert api_env.get("API_WORKERS") == "${API_WORKERS:-4}", f"{env}: api thiếu API_WORKERS"
        assert api_env.get("DB_POOL_SIZE") == "${API_DB_POOL_SIZE:-5}", f"{env}: api thiếu DB_POOL_SIZE"
        assert api_env.get("DB_MAX_OVERFLOW") == "${API_DB_MAX_OVERFLOW:-3}", f"{env}: api thiếu DB_MAX_OVERFLOW"


@pytest.mark.parametrize("env", ENVS)
def test_compose_bake_service_gated_single_gpu_worker(env: str) -> None:
    """LB-01 `bake`: chỉ chạy dưới `profiles: [bake]`, ảnh `appback-bake`, nghe đúng hàng
    `bake` với `--concurrency 1`, `init: true`, `mem_limit`, volume đích `/tmp`, cùng env app
    với `worker` (cần DB + kho) và đặt GPU nvidia như `ml-gpu`."""
    services = _resolved_services(env)
    bake = services["bake"]
    assert bake.get("profiles") == ["bake"], f"{env}/bake: profiles phải là [bake]"
    assert "appback-bake:" in str(bake.get("image", "")), f"{env}/bake: image phải là appback-bake"
    cmd = _cmd_text(bake.get("command"))
    assert "-Q bake --concurrency 1" in cmd, f"{env}/bake: lệnh phải nghe -Q bake --concurrency 1"
    assert bake.get("init") is True, f"{env}/bake: thiếu init: true"
    assert bake.get("mem_limit"), f"{env}/bake: thiếu mem_limit"
    assert "/tmp" in _volume_targets(bake.get("volumes")), f"{env}/bake: thiếu volume đích /tmp"  # noqa: S108
    assert bake["environment"].get("DATABASE_URL") == services["worker"]["environment"].get("DATABASE_URL")
    gpu = {"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}
    devices = ((bake.get("deploy") or {}).get("resources") or {}).get("reservations", {}).get("devices")
    assert devices == [gpu], f"{env}/bake: thiếu đặt GPU nvidia như ml-gpu"
    if env == "dev":
        assert bake["build"].get("target") == "bake", "dev/bake: build.target phải là bake"


def test_compose_migrate_imports_library_pack_only_when_mounted() -> None:
    """M15: `migrate` gọi `import-files "$LIBRARY_PACK_DIR"` sau seed, chỉ khi biến có giá trị; `library-pack.yml` đặt
    biến, gắn gói **chỉ đọc** từ `LIBRARY_PACK_HOST_DIR` bắt buộc và chờ `minio-init` (cần storage để `put`)."""
    command = _cmd_text(_resolved_services("dev")["migrate"]["command"])
    guard = '[ -z "$${LIBRARY_PACK_DIR:-}" ] || python -m apps.api.library.cli import-files "$$LIBRARY_PACK_DIR"'
    assert guard in command
    assert command.index("packages.db.seeds") < command.index("import-files")
    migrate = load_yaml(require_path("deploy/compose/library-pack.yml"))["services"]["migrate"]
    assert migrate["environment"]["LIBRARY_PACK_DIR"] == "/library-pack"
    pack = next(v for v in migrate["volumes"] if isinstance(v, dict) and v["target"] == "/library-pack")
    assert pack["read_only"] is True
    assert pack["source"].startswith("${LIBRARY_PACK_HOST_DIR:?")
    assert migrate["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
