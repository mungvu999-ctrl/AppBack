"""Kết luận ĐẠT/TRƯỢT của một ca và cờ "lỗi im lặng" (ca trượt mà hệ thống không báo gì cho người dùng)."""

from __future__ import annotations

from tools.bench.score.case import Case
from tools.bench.score.io import Json

# Tiêu chí thuộc nhóm tỉ lệ/diện tích: màn "Đặt tỷ lệ" sửa được bằng một bước
_SCALE_FIXABLE = ("S1", "S3", "P5", "P6")
_MAX_WARNINGS = 2


def verdict_of(marks: list[str]) -> str:
    """ĐẠT khi không có T và không quá 2 C; còn lại TRƯỢT."""
    if "T" in marks:
        return "TRƯỢT"
    return "ĐẠT" if marks.count("C") <= _MAX_WARNINGS else "TRƯỢT"


def is_silent(case: Case, c: dict[str, str]) -> bool:
    """Có tiêu chí T mà hệ thống không báo (không `signal`) và không phải lỗi tỉ lệ còn khai `scaleUnresolved`.

    Đích "báo đúng chỗ, sửa 1 bước": tỉ lệ còn là ước (`scaleUnresolved`) và mọi T đều thuộc nhóm tỉ lệ/diện tích
    → màn Đặt tỷ lệ sửa được, coi là đã báo đúng chỗ (không im lặng; kết luận vẫn theo tiêu chí).
    """
    scale_flag = "scaleUnresolved" in (case.ready.get("reasons") or []) and all(
        k in _SCALE_FIXABLE for k, v in c.items() if v == "T"
    )
    return "T" in c.values() and not (case.signal or scale_flag)


def behaviour_verdict(case: Case, c: dict[str, str], right: bool | None) -> tuple[str, bool]:
    """Kết luận ca hành vi (nhóm D) và cờ im lặng.

    Ca "chặn" tính theo D bất kể R1; ca khác thất bại (R1 = T) thì TRƯỢT. Đích "báo đúng chỗ": ca chặn/tách trượt
    mà báo sai lý do (vd PERSPECTIVE_DETECTED trên tờ 2 căn) = im lặng.
    """
    beh = case.key["behaviour"]
    d = c["D"] if c["R1"] != "T" or beh.startswith("chan") else "T"
    label = "ĐẠT" if d == "Đ" else "TRƯỢT" if d == "T" else "?"
    return label, d == "T" and not (right if right is not None else case.signal)


def finalize(outcome_auto: Json, outcome_final: Json) -> Json:
    """Gắn `verdict_auto`, `step1`, `outcome` (tự động / 1 bước / trượt / ?) và `auto` vào kết quả cuối (M5)."""
    r = outcome_final
    r["verdict_auto"] = outcome_auto["verdict"]
    r["step1"] = r is not outcome_auto
    if outcome_auto["verdict"] == "ĐẠT":
        r["outcome"] = "tự động"
    elif r["verdict"] == "ĐẠT":
        r["outcome"] = "1 bước"
    else:
        r["outcome"] = "?" if r["verdict"] == "?" else "trượt"
    if r["step1"]:
        r["auto"] = {"c": outcome_auto["c"], "silent": outcome_auto["silent"]}
    return r
