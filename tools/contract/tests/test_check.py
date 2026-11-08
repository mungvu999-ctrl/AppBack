"""Các kiểm của `tools.contract.check` (B0-07 [8] "Cấu trúc", H3, H4, H5, test khói).

Dữ liệu FE luôn lấy từ runner Node thật trên AppFront thật; phía BE (BE-BIND tạm, repo tạm,
module gương tạm) dựng trong test để chạm từng nhánh mà không đợi prompt khác hợp nhất.
"""

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from tools.charter import merged_prompts
from tools.contract import check
from tools.contract.runner_client import lock_digest, node_dir_from_env, run_command
from tools.contract.samples import EventSample, HttpSample
from tools.contract.tests import wire
from tools.verify.steps import STATUS_FAIL, STATUS_NA, STATUS_OK

ROWS: Final = check.contract_rows()
ABSENT_MODULE: Final = "packages.domain.khong_co_guong_b0_07"
GHOST_BIND: Final = """
| # | Đường v1 | operationId | Schema (file) | Loại | Khoá | Nhật ký | Chủ BE | Nối FE |
|---|---|---|---|---|---|---|---|---|
| N99 | `GET /api/ghosts` | `ghost_list_items` | `ghosts.ts` | Đ\\* | — | — | B9-99 | — |
"""


def mounted(*ops: str) -> list[check.Mounted]:
    """Thao tác đã mount đúng đường của BE-BIND."""
    return [check.Mounted(op, ROWS[op].method, ROWS[op].path) for op in ops]


def inputs(*ops: str, **overrides: Any) -> check.Inputs:
    """Đầu vào với BE-BIND thật, `ops` đã mount, chưa prompt chủ nào hợp nhất."""
    return check.Inputs(rows=ROWS, mounted=mounted(*ops), merged=frozenset(), **overrides)


def repo_with_changes(root: Path, *codes: str) -> frozenset[str]:
    """`merged_prompts` của một repo tạm có `changes/<mã>.md`."""
    (root / "changes").mkdir()
    for code in codes:
        (root / "changes" / f"{code}.md").write_text("mảnh changelog\n", encoding="utf-8")
    return frozenset(merged_prompts(root))


@pytest.fixture(scope="module")
def shapes(contract_build: Path) -> dict[str, str]:
    """`shape` của mọi mục `schema-map.ts` (lệnh `smoke` thật)."""
    smoke = run_command(contract_build, "smoke", {"schemaModules": check.schema_modules(contract_build)})
    assert smoke["problems"] == []
    result: dict[str, str] = smoke["shapes"]
    return result


def h1(build: Path, shapes: dict[str, str], data: check.Inputs) -> check.CheckResult:
    """H1 trên mẫu của `data`, giải bằng runner thật."""
    verdicts, _ = check.decode(build, data.samples, [])
    return check.check_h1(data.samples, verdicts, shapes, data)


# -- Bản đồ đủ, thao tác đã mount --------------------------------------------------------------


def test_real_map_covers_every_contract_row(shapes: dict[str, str]) -> None:
    """103 mục = 48 HTTP + 2 SSE của §1 + 49 của §2 (không v2) + 4 dòng chờ duyệt (N49-N52).

    N41 ở UX41 U05, N42-N44 nướng ánh sáng ở LB-01, N45-N46 màn tỉ lệ ở QT-SCALE-API,
    N47-N48 AI chọn phong cách ở AID-01, N49-N51 xuất GLB dựng trên máy chủ ở M1-01.
    N52 bảng luật lớp đề xuất ở M2-QL.
    """
    result = check.check_map(shapes, ROWS)
    assert result.status == STATUS_OK, result.problems
    assert len(shapes) == len(ROWS) == 103


def test_map_missing_a_bind_row_fails(tmp_path: Path, shapes: dict[str, str]) -> None:
    """BE-BIND tạm có thêm một dòng mà bản đồ không có mục → hỏng."""
    bind = tmp_path / "BE-BIND.md"
    bind.write_text(GHOST_BIND, encoding="utf-8")
    result = check.check_map(shapes, {**ROWS, **check.contract_rows(bind)})
    assert result.status == STATUS_FAIL
    assert result.problems == ("ghost_list_items: dòng BE-BIND không có mục trong schema-map.ts",)


def test_map_extra_entry_fails(shapes: dict[str, str]) -> None:
    """Mục ngoài BE-BIND và ngoài danh sách miễn → hỏng."""
    result = check.check_map({**shapes, "ghost_list_items": "object"}, ROWS)
    assert result.problems == ("ghost_list_items: mục thừa, không có dòng BE-BIND",)


def test_exempt_and_contract_mounts_pass() -> None:
    """Các route miễn và thao tác đúng đường BE-BIND qua được; chưa chủ nào hợp nhất."""
    # Miễn có dòng BE-BIND (N44) mount ở đường thật của nó; ở đây chỉ dựng đường giả cho miễn ngoài BE-BIND.
    exempt = [check.Mounted(op, "GET", f"/api/{op}") for op in sorted(check.H1_EXEMPT - ROWS.keys())]
    data = check.Inputs(rows=ROWS, mounted=[*exempt, *mounted("projects_read_project")], merged=frozenset())
    assert check.check_mounted(data).status == STATUS_OK


def test_unknown_mount_fails() -> None:
    """K06: `operationId` không thuộc BE-BIND hay miễn → hỏng."""
    data = check.Inputs(
        rows=ROWS, mounted=[check.Mounted("ghost_list_items", "GET", "/api/ghosts")], merged=frozenset()
    )
    result = check.check_mounted(data)
    assert result.status == STATUS_FAIL
    assert "ghost_list_items: đã mount mà không có dòng BE-BIND" in result.problems[0]


def test_mount_on_another_path_fails() -> None:
    """K06: đúng `operationId` mà khác đường BE-BIND ("đổi cho đẹp") → hỏng."""
    data = check.Inputs(
        rows=ROWS, mounted=[check.Mounted("floors_list_floors", "GET", "/api/levels")], merged=frozenset()
    )
    assert "BE-BIND ghi GET /api/projects/{project_id}/floors" in check.check_mounted(data).problems[0]


def test_require_all_reports_every_unmounted_row() -> None:
    """`CONTRACT_REQUIRE_ALL=1`: mọi dòng không v2 chưa mount → hỏng."""
    result = check.check_mounted(inputs("projects_read_project", require_all=True))
    assert result.status == STATUS_FAIL
    assert len(result.problems) == len(ROWS) - 1
    assert "auth_login: chưa mount (CONTRACT_REQUIRE_ALL=1)" in result.problems


def test_merged_owner_without_mount_fails(tmp_path: Path) -> None:
    """Chủ đã có `changes/<mã>.md` (repo tạm) mà thao tác của nó chưa mount → hỏng."""
    merged = repo_with_changes(tmp_path, "B0-06", "B1-04")
    result = check.check_mounted(check.Inputs(rows=ROWS, mounted=mounted("me_read_profile"), merged=merged))
    assert sorted(result.problems) == [
        f"{op}: chủ B1-04 đã hợp nhất (changes/B1-04.md) mà chưa mount"
        for op in ("me_change_password", "me_replace_avatar", "me_update_profile")
    ]


# -- H1 cấu trúc --------------------------------------------------------------------------------


def test_mounted_operation_without_success_sample_fails(contract_build: Path, shapes: dict[str, str]) -> None:
    """Thao tác đã mount, có thân, không phải Loại S, mà không có mẫu 2xx → hỏng."""
    error = HttpSample(
        "e", "projects_read_project", 404, {"code": "NOT_FOUND", "requestId": "r", "resource": "project"}
    )
    result = h1(contract_build, shapes, inputs("projects_read_project", samples=[error]))
    assert result.problems == ("projects_read_project: đã mount mà không có mẫu 2xx nào (test C01 ghi golden)",)


def test_empty_shape_and_stream_need_no_success_sample(contract_build: Path, shapes: dict[str, str]) -> None:
    """Mục `empty` (204) không cần mẫu 2xx; luồng S1 do H5 kiểm, không phải H1."""
    result = h1(contract_build, shapes, inputs("measurements_delete_record", "streams_open_progress"))
    assert result.status == STATUS_OK, result.problems


def test_success_sample_satisfies_mounted_operation(contract_build: Path, shapes: dict[str, str]) -> None:
    """Một mẫu 2xx giải đạt là đủ cho thao tác đã mount."""
    sample = HttpSample("p", "projects_read_project", 200, wire.project())
    assert h1(contract_build, shapes, inputs("projects_read_project", samples=[sample])).status == STATUS_OK


@pytest.mark.parametrize("value", ["2026-01-01T00:00:00.000000Z", "2026-01-01T07:00:00.000+07:00"])
def test_datetime_outside_w3_fails_at_any_depth(contract_build: Path, shapes: dict[str, str], value: str) -> None:
    """Chuỗi ngày giờ sâu trong thân phải kết thúc `.sssZ`, kể cả khi schema cũ của FE nhận nó."""
    floor = {"drawings": [], "elevationMm": 0, "heightMm": 3000, "id": "L-1", "name": "Tầng", "order": 0}
    body = wire.project(floors=[floor], createdAt=value)
    body["floors"][0]["drawings"] = [
        {
            "heightMm": 1,
            "id": "d",
            "name": "b",
            "uploadedAt": value,
            "uploaderId": "u",
            "url": "https://x/y",
            "widthMm": 1,
        }
    ]
    result = h1(contract_build, shapes, inputs(samples=[HttpSample("p", "projects_read_project", 200, body)]))
    assert result.status == STATUS_FAIL
    assert any("body.floors[0].drawings[0].uploadedAt" in problem for problem in result.problems)
    assert not [problem for problem in result.problems if "safeParse" in problem]


def test_progress_needs_all_four_branches(contract_build: Path, shapes: dict[str, str]) -> None:
    """`drawings_read_progress` đã mount thiếu nhánh `failed` → hỏng (BE-BIND §4); đủ 4 → đạt."""
    branches = {
        "pending": wire.progress("pending"),
        "running": wire.progress("running", progressPercent=5, startedAt=wire.AT),
        "completed": wire.progress("completed", progressPercent=100, endedAt=wire.AT),
        "failed": wire.progress("failed", error="PIPELINE_SUPERSEDED"),
    }
    samples = [HttpSample(name, "drawings_read_progress", 200, body) for name, body in branches.items()]
    short = h1(contract_build, shapes, inputs("drawings_read_progress", samples=samples[:3]))
    assert short.problems == ("drawings_read_progress: thiếu mẫu 2xx nhánh failed (BE-BIND §4)",)
    assert h1(contract_build, shapes, inputs("drawings_read_progress", samples=samples)).status == STATUS_OK


# -- H3 ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fe_permissions(contract_build: Path) -> dict[str, Any]:
    """`{roles, keys, matrix}` của `src/lib/auth/permissions.ts`."""
    result: dict[str, Any] = run_command(contract_build, "permissions", {})
    return result


def mirror(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: Any) -> str:
    """Module gương tạm trong `sys.modules` (khôi phục sau test)."""
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return name


def permission_mirror(monkeypatch: pytest.MonkeyPatch, fe: dict[str, Any], **overrides: Any) -> str:
    """Gương H3 chép đúng FE, rồi đè từng tên theo `overrides`."""
    attrs = {"ROLES": tuple(fe["roles"]), "PERMISSION_KEYS": tuple(fe["keys"]), "PERMISSION_MATRIX": fe["matrix"]}
    return mirror(monkeypatch, "guong_quyen_b0_07", **{**attrs, **overrides})


def test_fe_permission_matrix_has_three_roles_and_ten_keys(fe_permissions: dict[str, Any]) -> None:
    """Runner xuất đúng `AUTH_ROLES` và 10 `PermissionKey` theo thứ tự khoá của FE."""
    assert fe_permissions["roles"] == ["admin", "engineer", "viewer"]
    assert len(fe_permissions["keys"]) == 10
    assert fe_permissions["matrix"]["ruleset.edit"] == {"admin": True, "engineer": False, "viewer": False}


def test_h3_mirror_matching_fe_passes(monkeypatch: pytest.MonkeyPatch, fe_permissions: dict[str, Any]) -> None:
    """Gương đúng từng ô → đạt."""
    name = permission_mirror(monkeypatch, fe_permissions)
    assert check.check_h3(fe_permissions, name, frozenset()).status == STATUS_OK


def test_h3_one_cell_off_fails(monkeypatch: pytest.MonkeyPatch, fe_permissions: dict[str, Any]) -> None:
    """Lệch một ô → hỏng, in khoá, vai và hai giá trị."""
    matrix = {key: dict(row) for key, row in fe_permissions["matrix"].items()}
    matrix["ruleset.edit"]["engineer"] = True
    name = permission_mirror(monkeypatch, fe_permissions, PERMISSION_MATRIX=matrix)
    result = check.check_h3(fe_permissions, name, frozenset())
    assert result.problems == ("ruleset.edit / engineer: FE False ≠ BE True",)


def test_h3_order_and_extra_key_fail(monkeypatch: pytest.MonkeyPatch, fe_permissions: dict[str, Any]) -> None:
    """So hai chiều: thứ tự vai khác, khoá thừa ở BE đều hỏng."""
    keys = (*fe_permissions["keys"], "share.revoke")
    matrix = {**fe_permissions["matrix"], "share.revoke": {"admin": True}}
    roles = tuple(reversed(fe_permissions["roles"]))
    name = permission_mirror(monkeypatch, fe_permissions, ROLES=roles, PERMISSION_KEYS=keys, PERMISSION_MATRIX=matrix)
    problems = check.check_h3(fe_permissions, name, frozenset()).problems
    assert problems[0].startswith("ROLES ['viewer', 'engineer', 'admin'] ≠ AUTH_ROLES")
    assert problems[1].startswith("PERMISSION_KEYS")
    assert "share.revoke / admin: FE None ≠ BE True" in problems


def test_h3_module_missing_a_name_fails(monkeypatch: pytest.MonkeyPatch, fe_permissions: dict[str, Any]) -> None:
    """Module có mà thiếu một trong ba tên → hỏng, không `không áp dụng`."""
    name = mirror(monkeypatch, "guong_thieu_b0_07", ROLES=("admin",))
    assert check.check_h3(fe_permissions, name, frozenset()).problems == (
        "guong_thieu_b0_07 thiếu PERMISSION_KEYS, PERMISSION_MATRIX",
    )


def test_h3_without_module_is_not_applicable(fe_permissions: dict[str, Any]) -> None:
    """Chưa có module và B1-02 chưa hợp nhất → `không áp dụng`."""
    result = check.check_h3(fe_permissions, ABSENT_MODULE, frozenset())
    assert (result.status, result.summary) == (STATUS_NA, f"B1-02 chưa hợp nhất — chưa có {ABSENT_MODULE}")


def test_h3_without_module_after_merge_fails(tmp_path: Path, fe_permissions: dict[str, Any]) -> None:
    """Repo tạm có `changes/B1-02.md` mà không có module → hỏng."""
    merged = repo_with_changes(tmp_path, "B1-02")
    assert check.check_h3(fe_permissions, ABSENT_MODULE, merged).status == STATUS_FAIL


def test_broken_mirror_import_is_not_hidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Module có mà nhập hỏng vì phụ thuộc thiếu: lỗi nổi lên, không thành `không áp dụng`."""
    (tmp_path / "guong_hong_b0_07.py").write_text("import khong_ton_tai_b0_07\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError, match="khong_ton_tai_b0_07"):
        check.mirror_module("guong_hong_b0_07")


# -- H4 ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """Hình dạng `ThresholdSpec` của B3-05 (`rule_code`, `min`, `max`)."""

    rule_code: str
    min: float
    max: float


@pytest.fixture(scope="module")
def fe_rules(contract_build: Path) -> dict[str, Any]:
    """`{codes, thresholds}` của `ALL_RULES` và `RULE_THRESHOLD_SPECS`."""
    result: dict[str, Any] = run_command(contract_build, "rules", {})
    return result


def rules_mirror(monkeypatch: pytest.MonkeyPatch, fe: dict[str, Any], **overrides: Any) -> str:
    """Danh mục H4 chép đúng FE, rồi đè từng tên theo `overrides`."""
    specs = {item["key"]: Spec(item["ruleCode"], item["min"], item["max"]) for item in fe["thresholds"]}
    attrs = {"RULE_CODES": tuple(fe["codes"]), "THRESHOLD_SPECS": specs}
    return mirror(monkeypatch, "danh_muc_luat_b0_07", **{**attrs, **overrides})


def test_fe_has_25_rules_and_27_thresholds(fe_rules: dict[str, Any]) -> None:
    """`ALL_RULES` (không phải `BUILT_IN_RULES`) có 25 mã; 27 khoá ngưỡng."""
    assert len(fe_rules["codes"]) == 25
    assert len(fe_rules["thresholds"]) == 27


def test_h4_without_catalog_is_not_applicable(fe_rules: dict[str, Any]) -> None:
    """25 mã FE, chưa có danh mục, B3-05 chưa hợp nhất → `không áp dụng`."""
    assert check.check_h4(fe_rules, ABSENT_MODULE, frozenset()).status == STATUS_NA


def test_h4_without_catalog_after_merge_fails(tmp_path: Path, fe_rules: dict[str, Any]) -> None:
    """B3-05 đã hợp nhất (repo tạm) mà không có danh mục → hỏng."""
    assert check.check_h4(fe_rules, ABSENT_MODULE, repo_with_changes(tmp_path, "B3-05")).status == STATUS_FAIL


def test_h4_24_rules_fail(fe_rules: dict[str, Any]) -> None:
    """FE còn 24 mã → hỏng "FE đổi số luật", kể cả khi chưa có danh mục."""
    result = check.check_h4({**fe_rules, "codes": fe_rules["codes"][:-1]}, ABSENT_MODULE, frozenset())
    assert result.status == STATUS_FAIL
    assert "FE đổi số luật" in result.problems[0]


def test_h4_catalog_matching_fe_passes(monkeypatch: pytest.MonkeyPatch, fe_rules: dict[str, Any]) -> None:
    """Danh mục đúng tập mã, tập khoá và `rule_code`/`min`/`max` → đạt."""
    assert check.check_h4(fe_rules, rules_mirror(monkeypatch, fe_rules), frozenset()).status == STATUS_OK


def test_h4_threshold_key_mismatch_fails(monkeypatch: pytest.MonkeyPatch, fe_rules: dict[str, Any]) -> None:
    """Khoá ngưỡng lệch (một khoá đổi tên) và mã thiếu → hỏng hai chiều."""
    specs = {item["key"]: Spec(item["ruleCode"], item["min"], item["max"]) for item in fe_rules["thresholds"]}
    specs["wall.maxThicknessMmX"] = specs.pop("wall.minThicknessMm")
    name = rules_mirror(monkeypatch, fe_rules, THRESHOLD_SPECS=specs, RULE_CODES=tuple(fe_rules["codes"][1:]))
    problems = check.check_h4(fe_rules, name, frozenset()).problems
    assert problems == (
        f"mã luật chỉ có ở FE: {[fe_rules['codes'][0]]}",
        "khoá ngưỡng chỉ có ở FE: ['wall.minThicknessMm']",
        "khoá ngưỡng chỉ có ở BE: ['wall.maxThicknessMmX']",
    )


def test_h4_min_max_or_rule_code_mismatch_fails(monkeypatch: pytest.MonkeyPatch, fe_rules: dict[str, Any]) -> None:
    """`min`, `max` hay `rule_code` của một khoá lệch → hỏng, in khoá và hai giá trị."""
    specs = {item["key"]: Spec(item["ruleCode"], item["min"], item["max"]) for item in fe_rules["thresholds"]}
    old = specs["wall.minThicknessMm"]
    specs["wall.minThicknessMm"] = Spec("DOOR-WIDTH", old.min + 1, old.max - 1)
    problems = check.check_h4(
        fe_rules, rules_mirror(monkeypatch, fe_rules, THRESHOLD_SPECS=specs), frozenset()
    ).problems
    assert [problem.split(":")[0] for problem in problems] == [
        "wall.minThicknessMm.ruleCode",
        "wall.minThicknessMm.min",
        "wall.minThicknessMm.max",
    ]


def test_h4_catalog_missing_a_name_fails(monkeypatch: pytest.MonkeyPatch, fe_rules: dict[str, Any]) -> None:
    """Danh mục thiếu `THRESHOLD_SPECS` → hỏng."""
    name = mirror(monkeypatch, "danh_muc_thieu_b0_07", RULE_CODES=tuple(fe_rules["codes"]))
    assert check.check_h4(fe_rules, name, frozenset()).problems == ("danh_muc_thieu_b0_07 thiếu THRESHOLD_SPECS",)


# -- H5 ------------------------------------------------------------------------------------------


def h5(build: Path, data: check.Inputs) -> check.CheckResult:
    """H5 trên khung của `data`, giải bằng runner thật."""
    _, verdicts = check.decode(build, [], data.events)
    return check.check_h5(data, verdicts)


def test_h5_not_applicable_before_streams_exist(contract_build: Path) -> None:
    """S1/S2 chưa mount, không khung nào, B4-01 chưa hợp nhất → `không áp dụng`."""
    assert h5(contract_build, inputs()).status == STATUS_NA


def test_h5_fails_when_owner_merged_without_streams(contract_build: Path, tmp_path: Path) -> None:
    """B4-01 đã hợp nhất mà S1/S2 chưa mount → hỏng."""
    data = check.Inputs(rows=ROWS, mounted=[], merged=repo_with_changes(tmp_path, "B4-01"))
    assert h5(contract_build, data).status == STATUS_FAIL


def test_h5_mounted_stream_without_events_fails(contract_build: Path) -> None:
    """S1 đã mount mà không có mẫu luồng → hỏng."""
    result = h5(contract_build, inputs("streams_open_progress"))
    assert result.problems == ("streams_open_progress: đã mount mà không có mẫu luồng (record_stream_event)",)


def test_h5_decodes_received_frames(contract_build: Path) -> None:
    """Khung đúng schema → đạt; khung hỏng, khung `failed` có `endedAt`, khung của thao tác không phải S → hỏng."""
    good = EventSample("a", "streams_open_progress", wire.progress("running", progressPercent=5))
    assert h5(contract_build, inputs("streams_open_progress", events=[good])).status == STATUS_OK
    bad = [
        EventSample("b", "streams_open_progress", {"id": wire.UPLOAD_ID}),
        EventSample("c", "streams_open_progress", wire.progress("failed", endedAt=wire.AT)),
        EventSample("d", "projects_read_project", wire.project()),
    ]
    problems = h5(contract_build, inputs("streams_open_progress", events=bad)).problems
    assert problems[0] == "d: projects_read_project không phải luồng Loại S"
    assert problems[1].startswith("b: safeParse hỏng")
    assert problems[2].startswith("c: K33")


def test_h5_rejects_non_w3_datetimes(contract_build: Path) -> None:
    """Khung có ngày giờ `+07:00` → H5 hỏng như H1 (W3; `ProgressSchema` cũ của FE nhận nó)."""
    at = "2026-01-01T07:00:00.000+07:00"
    frame = EventSample("e", "streams_open_progress", wire.progress("running", startedAt=at))
    problems = h5(contract_build, inputs("streams_open_progress", events=[frame])).problems
    assert problems == (f"e: event.startedAt = {at!r} không kết thúc .sssZ (W3)",)


# -- Smoke, runner đầy đủ, test khói trên repo thật ---------------------------------------------


def test_smoke_reports_missing_export_and_bad_entries(contract_build: Path, tmp_path: Path) -> None:
    """Export không tồn tại, mục thiếu module, `shape` lạ, module không nhập được → hỏng từng dòng."""
    build = tmp_path / "build"
    shutil.copytree(contract_build, build, symlinks=True)
    source = (build / "schema-map.ts").read_text(encoding="utf-8")
    source = source.replace("'FeatureFlagsSchema'", "'KhongCoSchema'")
    source = source.replace("object(INDEX, 'VersionSchema')", "object('', 'VersionSchema')")
    source = source.replace("{ shape: 'empty' }", "{ shape: 'rong' }")
    (build / "schema-map.ts").write_text(source, encoding="utf-8")
    (build / "src" / "api" / "schemas" / "hong.test.ts").write_text("throw new Error('test');\n", encoding="utf-8")
    modules = [*check.schema_modules(build), "@/api/schemas/khongco"]
    assert "@/api/schemas/hong" not in modules
    problems = run_command(build, "smoke", {"schemaModules": modules})["problems"]
    assert any('không export schema zod "KhongCoSchema"' in problem for problem in problems)
    assert "versions_read_version: thiếu module hay exportName" in problems
    assert 'auth_login: shape "rong" không thuộc object|array|empty' in problems
    assert any(problem.startswith("nhập @/api/schemas/khongco hỏng") for problem in problems)


def test_run_checks_passes_on_consistent_inputs(contract_build: Path) -> None:
    """Tám kiểm chạy trọn qua runner: mẫu đúng + thao tác đã mount có mẫu → không kiểm nào hỏng."""
    exempt = HttpSample("h", "health_live", 200, {"status": "ok", "at": "không phải ngày giờ"})
    sample = HttpSample("p", "projects_read_project", 200, wire.project())
    results = check.run_checks(contract_build, inputs("projects_read_project", samples=[exempt, sample]))
    assert [item.name for item in results] == [
        "Smoke",
        "Bản đồ đủ",
        "Thao tác đã mount",
        "H1",
        "H1 ngữ cảnh",
        "H3",
        "H4",
        "H5",
    ]
    assert {item.status for item in results} <= {STATUS_OK, STATUS_NA}
    assert results[3].summary == "1 mẫu response"


def test_real_repo_check_runs_through(
    appfront_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test khói: `check.main` chạy trọn trên repo hiện tại, in đủ tám kiểm; ba kiểm cấu trúc `đạt`.

    Lệch khỏi B0-07 [8] ("mọi kiểm `đạt` hoặc `không áp dụng`"): test dùng thư mục mẫu rỗng của
    riêng nó, mẫu thật chỉ có trong `CONTRACT_SAMPLES_DIR` của lượt verify. Đòi H1 tới H5 `đạt` ở đây
    thì prompt đầu tiên mount route có thân đỏ bước 5 vì file này (cùng lớp FIX-003); H1 tới H5 trên
    mẫu thật do chính bước 7 đòi.
    """
    monkeypatch.setenv(check.SAMPLES_ENV, str(tmp_path / "mau"))
    monkeypatch.delenv(check.REQUIRE_ALL_ENV, raising=False)
    assert check.main(["--build-dir", str(tmp_path / "build")]) in {0, 1}
    table = capsys.readouterr().out.splitlines()
    lines = [line.split(" | ") for line in table if line.count(" | ") == 2][1:]
    rows = {name.strip(): status.strip() for name, status, _ in lines}
    assert len(rows) == 8
    assert set(rows.values()) <= {STATUS_OK, STATUS_FAIL, STATUS_NA}
    assert [rows[name] for name in ("Smoke", "Bản đồ đủ", "Thao tác đã mount")] == [STATUS_OK] * 3
    assert table[-1].startswith("thời gian:")
    assert (tmp_path / "build" / "runner.ts").is_file(), "--build-dir phải được giữ lại để gỡ lỗi"


def test_main_fails_without_f00a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AppFront @ SHA thiếu `common.ts` → bước 7 hỏng, không bỏ qua."""
    monkeypatch.setenv("APPFRONT_DIR", str(tmp_path))
    assert check.main(["--build-dir", str(tmp_path / "build")]) == 1
    assert "AppFront @ SHA thiếu F-00a" in capsys.readouterr().out


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Thư mục tạm mặc định của `tempfile` trỏ về một thư mục rỗng của riêng test."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def test_main_without_build_dir_leaves_no_scratch_on_early_failure(
    scratch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Không `--build-dir`, AppFront thiếu F-00a → hỏng mà không để lại `contract-*` (NO-052)."""
    monkeypatch.setenv("APPFRONT_DIR", str(tmp_path))
    assert check.main([]) == 1
    assert "AppFront @ SHA thiếu F-00a" in capsys.readouterr().out
    assert list(scratch.iterdir()) == []


@pytest.mark.usefixtures("appfront_dir")
def test_main_without_build_dir_removes_built_layout(
    scratch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mẫu hỏng sau khi đã dựng bố cục → thư mục tạm xoá sạch; kho `node_modules` sau symlink còn nguyên."""
    broken = tmp_path / "mau" / "health_live" / "C01-1.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{", encoding="utf-8")
    monkeypatch.setenv(check.SAMPLES_ENV, str(broken.parent.parent))
    assert check.main([]) == 1
    assert "mẫu health_live/C01-1.json hỏng" in capsys.readouterr().out
    assert list(scratch.iterdir()) == []
    assert (node_dir_from_env() / lock_digest() / "node_modules").is_dir()


def test_gather_inputs_reads_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không đặt thư mục mẫu → không mẫu; `CONTRACT_REQUIRE_ALL=1` bật đòi mount đủ."""
    monkeypatch.delenv(check.SAMPLES_ENV, raising=False)
    monkeypatch.setenv(check.REQUIRE_ALL_ENV, "1")
    data = check.gather_inputs()
    assert (data.samples, data.events, data.require_all) == ([], [], True)
    assert {"health_live", "health_ready", "files_read_object"} <= {item.op for item in data.mounted}
