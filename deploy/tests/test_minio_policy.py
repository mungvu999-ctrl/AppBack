"""Test tĩnh cho `deploy/minio/{ml,app}-policy.json` (hợp đồng §4, prompt [7]/[8])."""

from __future__ import annotations

import json
import re

import pytest

from deploy.tests.support import require_path

_EXPECTED_PAIRS = {
    ("s3:GetObject", "projects/*/floors/*/uploads/*/pages/*"),
    ("s3:GetObject", "projects/*/floors/*/uploads/*/runs/*/preprocess/vectors.json"),  # UX41-B1
    ("s3:GetObject", "projects/*/drawing-sets/*/files/*/pages/*"),  # K-32a: ảnh trang bộ hồ sơ
    ("s3:GetObject", "ml/models/*"),
    ("s3:GetObject", "ml/datasets/*"),
    ("s3:PutObject", "projects/*/floors/*/uploads/*/runs/*"),
    ("s3:PutObject", "projects/*/drawing-sets/*/files/*/readings/*"),  # K-32a: kết quả đọc trang
    ("s3:PutObject", "ml/models/*"),
}
_MULTIPART_ACTIONS = {"s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"}
_ARN_RE = re.compile(r"^arn:aws:s3:::([^/]+)/?(.*)$")

# F3: quyền tối thiểu đủ cho `packages/storage/s3.py` (bucket_exists, list_objects,
# stat_object, get_object, put_object kể cả multipart, remove_object(s)) — không
# `s3:*`, không bucket `*`, không quyền admin (review 2026-09-22 #15, khối [2] F3).
_APP_BUCKET_ACTIONS = {"s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"}
_APP_OBJECT_ACTIONS = {
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:AbortMultipartUpload",
    "s3:ListMultipartUploadParts",
}


def _load_policy(name: str) -> dict:
    """Nạp JSON của `deploy/minio/<name>`; `fail` rõ nếu file chưa có."""
    path = require_path(f"deploy/minio/{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _statement_pairs(statement: dict) -> list[tuple[str, str]]:
    """Trả (hành động, tiền tố sau `arn:aws:s3:::<bucket>/`) cho mọi tổ hợp
    action x resource trong một statement (`Action`/`Resource` có thể là chuỗi
    đơn hay danh sách)."""
    actions = statement["Action"]
    actions = [actions] if isinstance(actions, str) else list(actions)
    resources = statement["Resource"]
    resources = [resources] if isinstance(resources, str) else list(resources)
    pairs: list[tuple[str, str]] = []
    for action in actions:
        for resource in resources:
            match = _ARN_RE.match(resource)
            assert match, f"Resource {resource!r} không đúng dạng arn:aws:s3:::<bucket>/<tiền tố>"
            pairs.append((action, match.group(2)))
    return pairs


def test_minio_ml_policy_exact_read_write_pairs() -> None:
    """Tập cặp (GetObject/PutObject, tiền tố) đúng **bằng** 8 cặp của hợp đồng §4 (UX41-B1 thêm `vectors.json`,
    K-32a thêm ảnh trang và kết quả đọc của bộ hồ sơ)."""
    policy = _load_policy("ml-policy.json")
    all_pairs = {
        pair
        for statement in policy["Statement"]
        for pair in _statement_pairs(statement)
        if pair[0] in {"s3:GetObject", "s3:PutObject"}
    }
    assert all_pairs == _EXPECTED_PAIRS


def test_minio_ml_policy_other_actions_only_multipart_on_put_prefixes() -> None:
    """Hành động khác GetObject/PutObject chỉ được thuộc tập multipart, và chỉ trên
    tiền tố đã có `s3:PutObject` (kèm theo thao tác ghi, không tự đứng riêng)."""
    policy = _load_policy("ml-policy.json")
    put_prefixes = {prefix for action, prefix in _EXPECTED_PAIRS if action == "s3:PutObject"}
    for statement in policy["Statement"]:
        for action, prefix in _statement_pairs(statement):
            if action in {"s3:GetObject", "s3:PutObject"}:
                continue
            assert action in _MULTIPART_ACTIONS, f"hành động lạ {action!r} không thuộc chính sách ml"
            assert prefix in put_prefixes, f"{action} trên tiền tố {prefix!r} không phải tiền tố PutObject"


def test_minio_ml_policy_no_wildcard_action_no_deny_no_delete() -> None:
    """Không hành động nào chứa `*` (K không cấp `s3:*`); không `Effect: Deny` để lách
    quyền qua đường vòng; không `s3:DeleteObject` ở đâu cả (ml không cần xoá — least
    privilege, khác `app-policy.json`)."""
    policy = _load_policy("ml-policy.json")
    for statement in policy["Statement"]:
        assert statement.get("Effect") == "Allow", "chỉ dùng Allow, không Deny để lách quyền"
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else list(actions)
        for action in actions:
            assert "*" not in action, f"hành động {action!r} chứa ký tự đại diện"
            assert action != "s3:DeleteObject", "chính sách ml không được có s3:DeleteObject"


def test_minio_app_policy_bucket_level_actions() -> None:
    """`app-policy.json`: statement mức bucket (`Resource == arn:aws:s3:::__BUCKET__`,
    không `/*`) cấp đúng **bằng** `ListBucket`/`GetBucketLocation`/
    `ListBucketMultipartUploads` — đủ cho `list_objects`/`bucket_exists` của
    `packages/storage/s3.py` (F3, khối [2] của prompt)."""
    policy = _load_policy("app-policy.json")
    bucket_level_actions: set[str] = set()
    for statement in policy["Statement"]:
        resources = statement["Resource"]
        resources = [resources] if isinstance(resources, str) else resources
        if resources != ["arn:aws:s3:::__BUCKET__"]:
            continue
        actions = statement["Action"]
        bucket_level_actions |= {actions} if isinstance(actions, str) else set(actions)
    assert bucket_level_actions == _APP_BUCKET_ACTIONS, (
        f"app-policy.json: hành động mức bucket phải đúng {_APP_BUCKET_ACTIONS}, có {bucket_level_actions}"
    )


def test_minio_app_policy_object_level_actions() -> None:
    """`app-policy.json`: statement mức object (`Resource ==
    arn:aws:s3:::__BUCKET__/*`) cấp đúng **bằng** `GetObject`/`PutObject`/
    `DeleteObject`/`AbortMultipartUpload`/`ListMultipartUploadParts` — đủ cho
    `stat`/`open_read`/`put`/`delete`/`delete_prefix` của `packages/storage/s3.py`,
    không giới hạn tiền tố (khoá chung, không phải khoá `ml` least-privilege)."""
    policy = _load_policy("app-policy.json")
    object_level_actions: set[str] = set()
    for statement in policy["Statement"]:
        resources = statement["Resource"]
        resources = [resources] if isinstance(resources, str) else resources
        if resources != ["arn:aws:s3:::__BUCKET__/*"]:
            continue
        actions = statement["Action"]
        object_level_actions |= {actions} if isinstance(actions, str) else set(actions)
    assert object_level_actions == _APP_OBJECT_ACTIONS, (
        f"app-policy.json: hành động mức object phải đúng {_APP_OBJECT_ACTIONS}, có {object_level_actions}"
    )


def test_minio_app_policy_only_two_statements_no_wildcard_no_admin() -> None:
    """`app-policy.json`: đúng hai statement (bucket-level, object-level), chỉ
    `Allow`, không hành động nào chứa `*` (cấm `s3:*`), không thao tác quản trị
    (`s3:CreateBucket`/`s3:DeleteBucket`/`s3:Put*Policy`/`admin:*`…) — tập hành
    động mỗi statement đã bị khoá cứng bởi hai test trên, ở đây chỉ chốt số
    lượng statement và `Effect`."""
    policy = _load_policy("app-policy.json")
    assert len(policy["Statement"]) == 2, "app-policy.json: phải đúng 2 statement (bucket-level, object-level)"
    for statement in policy["Statement"]:
        assert statement.get("Effect") == "Allow", "chỉ dùng Allow, không Deny để lách quyền"
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else list(actions)
        for action in actions:
            assert "*" not in action, f"hành động {action!r} chứa ký tự đại diện"
            assert action.startswith("s3:"), f"hành động {action!r} không phải s3:* — nghi quyền quản trị"


@pytest.mark.parametrize("name", ["ml-policy.json", "app-policy.json"])
def test_minio_policy_version_field(name: str) -> None:
    """`Version` đúng `"2012-10-17"` (bắt buộc theo cú pháp IAM policy)."""
    policy = _load_policy(name)
    assert policy.get("Version") == "2012-10-17"


@pytest.mark.parametrize("name", ["ml-policy.json", "app-policy.json"])
def test_minio_policy_bucket_is_placeholder_not_wildcard(name: str) -> None:
    """Mỗi mẫu chính sách: bucket của mọi ARN là cùng một giữ chỗ (không phải
    `*` — least privilege), `deploy/minio/init.sh` thay bằng `S3_BUCKET` thật
    vào `/tmp` trước khi `mc admin policy create` (review 2026-09-22 #9: `*`
    cấp quyền trên **mọi** bucket của MinIO, không chỉ bucket của app)."""
    policy = _load_policy(name)
    buckets: set[str] = set()
    for statement in policy["Statement"]:
        resources = statement["Resource"]
        resources = [resources] if isinstance(resources, str) else resources
        for resource in resources:
            match = _ARN_RE.match(resource)
            assert match, f"Resource {resource!r} không đúng dạng arn:aws:s3:::<bucket>[/<tiền tố>]"
            buckets.add(match.group(1))
    assert buckets == {"__BUCKET__"}, f"{name}: bucket phải là một giữ chỗ duy nhất, có {buckets}"
    assert "*" not in buckets, f"{name}: bucket không được là '*' (mọi bucket của MinIO)"
