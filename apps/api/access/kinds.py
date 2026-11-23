"""Bảng mã `kind` của `activity_log` — 31 hằng, một cho mỗi dòng BE-BIND có Nhật ký `có`.

Chủ route **nhập hằng** từ đây khi gọi `record_activity` (E), không viết chuỗi tay:
sai chính tả một `kind` sẽ vượt qua kiểm `ActivityKind` của `record_activity` mà
không ai biết cho tới khi đọc lại nhật ký. `object_code`/`object_label` theo bảng
khối [2] của B1-02.md (không khai hằng ở đây, mỗi route tính riêng theo dữ liệu
của nó, ví dụ id/tên tầng hay email người dùng).

Không nhập `fastapi`, `sqlalchemy`: module này phải nạp được ở worker (K nhắc lại
BE-01 [9]).
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final


class ActivityKind(StrEnum):
    """`<miền>.<việc>`, đúng cách FE tách miền (`userManagementGateway.ts:292-296`).

    Miền chỉ chữ thường không gạch dưới (CHECK `kind_format` của bảng): bộ hồ sơ là `drawingset.*` (K-32a).
    """

    FLOOR_UPLOAD = "floor.upload"
    FLOOR_UPLOAD_COMPLETE = "floor.upload_complete"
    FLOOR_CREATE = "floor.create"
    FLOOR_DELETE = "floor.delete"
    FLOOR_EDIT = "floor.edit"
    FLOOR_REORDER = "floor.reorder"
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"
    PROJECT_SETTINGS_UPDATE = "project.settings_update"
    MEMBER_ADD = "member.add"
    MEMBER_REMOVE = "member.remove"
    VERSION_RESTORE = "version.restore"
    VERSION_LABEL = "version.label"
    RULES_CONFIG_UPDATE = "rules.config_update"
    USER_ROLE_CHANGE = "user.role_change"
    USER_DISABLE = "user.disable"
    USER_ENABLE = "user.enable"
    USER_DELETE = "user.delete"
    USER_INVITE = "user.invite"
    USER_INVITE_RESEND = "user.invite_resend"
    MODEL_ACTIVATE = "model.activate"
    MODEL_UPLOAD = "model.upload"
    DATASET_CREATE = "dataset.create"
    DATASET_BUILD = "dataset.build"
    TRAINING_CREATE = "training.create"
    TRAINING_CANCEL = "training.cancel"
    DRAWING_SET_CREATE = "drawingset.create"
    DRAWING_SET_SUBMIT = "drawingset.submit"
    DRAWING_SET_CONFIRM = "drawingset.confirm"
    DRAWING_SET_DELETE = "drawingset.delete"


KIND_OPERATIONS: Final[MappingProxyType[ActivityKind, str]] = MappingProxyType(
    {
        ActivityKind.FLOOR_UPLOAD: "drawings_init_upload",
        ActivityKind.FLOOR_UPLOAD_COMPLETE: "drawings_complete_upload",
        ActivityKind.FLOOR_CREATE: "floors_create_floor",
        ActivityKind.FLOOR_DELETE: "floors_delete_floor",
        ActivityKind.FLOOR_EDIT: "floors_patch_spatial_floor",
        ActivityKind.FLOOR_REORDER: "floors_reorder_floors",
        ActivityKind.PROJECT_CREATE: "projects_create_project",
        ActivityKind.PROJECT_UPDATE: "projects_update_project",
        ActivityKind.PROJECT_DELETE: "projects_delete_project",
        ActivityKind.PROJECT_SETTINGS_UPDATE: "settings_replace_settings",
        ActivityKind.MEMBER_ADD: "members_add_member",
        ActivityKind.MEMBER_REMOVE: "members_remove_member",
        ActivityKind.VERSION_RESTORE: "versions_restore_version",
        ActivityKind.VERSION_LABEL: "versions_label_version",
        ActivityKind.RULES_CONFIG_UPDATE: "rules_replace_config",
        ActivityKind.USER_ROLE_CHANGE: "users_change_role",
        ActivityKind.USER_DISABLE: "users_disable_user",
        ActivityKind.USER_ENABLE: "users_enable_user",
        ActivityKind.USER_DELETE: "users_delete_user",
        ActivityKind.USER_INVITE: "users_invite_users",
        ActivityKind.USER_INVITE_RESEND: "users_resend_invitation",
        ActivityKind.MODEL_ACTIVATE: "ml_activate_version",
        ActivityKind.MODEL_UPLOAD: "ml_upload_version",
        ActivityKind.DATASET_CREATE: "ml_create_dataset",
        ActivityKind.DATASET_BUILD: "ml_build_dataset_version",
        ActivityKind.TRAINING_CREATE: "ml_create_job",
        ActivityKind.TRAINING_CANCEL: "ml_cancel_job",
        ActivityKind.DRAWING_SET_CREATE: "drawing_sets_create_set",
        ActivityKind.DRAWING_SET_SUBMIT: "drawing_sets_complete_set",
        ActivityKind.DRAWING_SET_CONFIRM: "drawing_sets_confirm_set",
        ActivityKind.DRAWING_SET_DELETE: "drawing_sets_delete_set",
    }
)
"""hằng `kind` → `operationId` BE-BIND. `USER_INVITE` ghi một dòng mỗi người mời
(vẫn cùng một `op` `users_invite_users`)."""
