"""H4 — 25 mã luật và khoá ngưỡng của FE, gương ở `apps.api.rules.catalog` (B0-07 [6].7).

FE: `ALL_RULES` (`src/domain/rules/defaults.ts:47-52`; `BUILT_IN_RULES` chỉ là nhóm con) và
`RULE_THRESHOLD_SPECS` (`src/domain/rules/thresholdSpecs.ts`). Gương B3-05: `RULE_CODES`
(tuple mã) và `THRESHOLD_SPECS` (`Mapping[khoá, ThresholdSpec(rule_code, min, max)]`).
`min`/`max` so bằng `!=` với số JSON của FE, nên gương giữ chúng bằng `int`/`float`: `Decimal`
thì `0.1 != Decimal("0.1")` và H4 hỏng giả.
"""

from collections.abc import Mapping
from types import ModuleType
from typing import Any, Final

EXPECTED_RULE_COUNT: Final = 25
"""25 mã luật. FE đổi con số này là việc của hiến chương, không của harness."""
REQUIRED_NAMES: Final = ("RULE_CODES", "THRESHOLD_SPECS")


def fe_count_problems(fe: Mapping[str, Any]) -> list[str]:
    """Số mã của FE khác 25 → một dòng hỏng; kiểm này chạy cả khi chưa có gương."""
    count = len(fe["codes"])
    if count == EXPECTED_RULE_COUNT:
        return []
    return [f"FE có {count} mã luật, không phải {EXPECTED_RULE_COUNT}: FE đổi số luật, cập nhật hiến chương"]


def _set_problems(label: str, fe_values: set[str], be_values: set[str]) -> list[str]:
    """Phần tử chỉ có ở một bên của hai tập."""
    problems = []
    if fe_values - be_values:
        problems.append(f"{label} chỉ có ở FE: {sorted(fe_values - be_values)}")
    if be_values - fe_values:
        problems.append(f"{label} chỉ có ở BE: {sorted(be_values - fe_values)}")
    return problems


def compare_rules(fe: Mapping[str, Any], catalog: ModuleType) -> list[str]:
    """Mọi chỗ lệch giữa luật FE (`{codes, thresholds}` của runner) và danh mục của B3-05."""
    missing = [name for name in REQUIRED_NAMES if not hasattr(catalog, name)]
    if missing:
        return [f"{catalog.__name__} thiếu {', '.join(missing)}"]
    fe_specs = {spec["key"]: spec for spec in fe["thresholds"]}
    be_specs: Mapping[str, Any] = catalog.THRESHOLD_SPECS
    problems = _set_problems("mã luật", set(fe["codes"]), set(catalog.RULE_CODES))
    problems += _set_problems("khoá ngưỡng", set(fe_specs), set(be_specs))
    for key in sorted(fe_specs.keys() & be_specs.keys()):
        fe_spec, be_spec = fe_specs[key], be_specs[key]
        be_values = {"ruleCode": be_spec.rule_code, "min": be_spec.min, "max": be_spec.max}
        for name, be_value in be_values.items():
            if fe_spec[name] != be_value:
                problems.append(f"{key}.{name}: FE {fe_spec[name]!r} ≠ BE {be_value!r}")
    return problems
