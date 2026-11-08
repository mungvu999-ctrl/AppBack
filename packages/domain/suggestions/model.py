"""Suggestion records (contract §2.2): stored under `suggestions` / `suggestionsMeta` of the floor document.

Wire form is camelCase, optional keys absent (never `null`), same as the spatial layer (W1, W2). `params` is kept as
the canonical dict of its per-kind schema; `override` holds scalars only, so it still applies after geometry changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Final, Literal

from pydantic import Field, JsonValue, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from packages.domain.spatial.model import (
    DomainModel,
    FurnitureIdStr,
    FurnitureVariant,
    MaterialRef,
    Mm,
    NonNegativeMm,
    OpeningIdStr,
    Point,
    PositiveMm,
    RoomIdStr,
    WallIdStr,
)
from packages.domain.suggestions.ruleset import Kind, ParamKey, RuleId

SUGGESTION_ID_PATTERN: Final = r"^DRESS-[A-Za-z0-9_.:-]{1,160}$"
SuggestionId = Annotated[StrictStr, Field(pattern=SUGGESTION_ID_PATTERN)]
State = Literal["active", "disabled", "overridden", "adopted"]
Scalar = StrictStr | StrictBool | StrictInt | StrictFloat
Span = tuple[NonNegativeMm, NonNegativeMm]
SillSpan = tuple[NonNegativeMm, NonNegativeMm, PositiveMm]
OVERRIDE_MAX: Final = 16
OVERRIDE_MM_MAX: Final = 10_000
"""Trần một giá trị mm điều chỉnh (10 m, cao hơn mọi tầng): lớn hơn là lỗi nhập, không phải ý người dùng (R-17)."""


class SkirtingParams(DomainModel):
    """R1: spans along the wall (mm from `centreline.start`) on one face; `left` = side of normal (-dy, dx)."""

    side: Literal["left", "right"]
    spans_mm: tuple[Span, ...]
    height_mm: PositiveMm
    thickness_mm: PositiveMm
    material: MaterialRef


class WallTileParams(DomainModel):
    """R2: wet-room wall tile on one face of a wall, full height on `spansMm` (doors and windows cut out).

    `sillSpansMm` = `(from, to, topMm)` pieces under windows, tiled from the floor up to the sill.
    """

    side: Literal["left", "right"]
    spans_mm: tuple[Span, ...]
    sill_spans_mm: tuple[SillSpan, ...] | None = None
    height_mm: PositiveMm
    thickness_mm: PositiveMm
    tile_mm: PositiveMm
    material: MaterialRef


class CorniceParams(DomainModel):
    """R3: ceiling cornice around a room; material absent = the room ceiling material."""

    height_mm: PositiveMm
    depth_mm: PositiveMm
    material: MaterialRef | None = None


class CasingParams(DomainModel):
    """C1: inner window casing (both sides and top)."""

    width_mm: PositiveMm
    thickness_mm: PositiveMm
    material: MaterialRef


class SashParams(DomainModel):
    """C2: sash frame around each glass pane of a window."""

    frame_width_mm: PositiveMm
    frame_depth_mm: PositiveMm


class RailingParams(DomainModel):
    """R4: railing on the open part of room outline edge `edgeIndex` (spans in mm from `start`)."""

    edge_index: Annotated[StrictInt, Field(ge=0)]
    start: Point
    end: Point
    spans_mm: tuple[Span, ...]
    height_mm: PositiveMm
    handrail_mm: PositiveMm
    glass_mm: PositiveMm
    post_spacing_max_mm: PositiveMm
    metal_material: MaterialRef
    glass_material: MaterialRef


class CabinetRunParams(DomainModel):
    """F2/F3: parametric kitchen run on the back line `start`→`end` (against the wall); the run lies on the side
    of the left normal (-dy, dx) of that line, `depthMm` deep. `mergedIds` = duplicate boxes folded into this run
    (their models are not built either). `gapMm` (door gap), and for a base run `plinthMm`/`countertopMm`, are
    copied from the rule table so the compiler builds from the record, not from its own constants (review F7)."""

    slot: Literal["base", "upper"]
    start: Point
    end: Point
    length_mm: PositiveMm
    depth_mm: PositiveMm
    bottom_mm: NonNegativeMm
    top_mm: PositiveMm
    module_mm: PositiveMm
    gap_mm: NonNegativeMm
    plinth_mm: NonNegativeMm | None = None
    countertop_mm: PositiveMm | None = None
    material: MaterialRef | None = None
    countertop_material: MaterialRef | None = None
    merged_ids: tuple[FurnitureIdStr, ...] | None = None
    degraded_vi: Annotated[StrictStr, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def _base_has_plinth_and_top(self) -> CabinetRunParams:
        """A base run is built with a plinth and a countertop; an upper run has neither."""
        if (self.slot == "base") != (self.plinth_mm is not None and self.countertop_mm is not None):
            raise ValueError("plinthMm/countertopMm chỉ (và luôn) đi cùng tủ dưới")
        return self


class CurtainParams(DomainModel):
    """D2: one curtain panel; `L` lies towards the host wall start. `extMm` = overhang past the opening."""

    side: Literal["L", "R"]
    width_mm: PositiveMm
    ext_mm: NonNegativeMm
    top_mm: PositiveMm
    offset_mm: NonNegativeMm
    pleat_mm: PositiveMm
    material: MaterialRef
    degraded_vi: Annotated[StrictStr, Field(min_length=1)] | None = None


class RugParams(DomainModel):
    """D1: flat rug."""

    centre: Point
    size_mm: tuple[PositiveMm, PositiveMm]
    rotation_deg: Annotated[StrictFloat | StrictInt, Field(ge=0, lt=360)]
    thickness_mm: PositiveMm
    material: MaterialRef | None = None


class CeilingLightParams(DomainModel):
    """L1: ceiling light at the room's pole of inaccessibility; `modelId` only for `pendant`."""

    centre: Point
    fixture: Literal["pendant", "panel"]
    model_id: Annotated[StrictStr, Field(min_length=1)] | None = None
    size_mm: tuple[PositiveMm, PositiveMm, PositiveMm] | None = None


class ItemParams(DomainModel):
    """P1-P3, D3-D5, AID: a furniture item not yet touched by the user.

    `centre` is the footprint (model bbox) centre and `sizeMm` the placed bbox (model x scale), not the model origin:
    the compiler anchors the model by its bbox (`anchor: "bbox"`). `liftMm` is above the finish floor (floor items 0).
    """

    furniture_kind: Annotated[StrictStr, Field(min_length=1)]
    variant: FurnitureVariant | None = None
    model_id: Annotated[StrictStr, Field(min_length=1)]
    centre: Point
    size_mm: tuple[PositiveMm, PositiveMm, PositiveMm]
    rotation_deg: Annotated[StrictFloat | StrictInt, Field(ge=0, lt=360)]
    lift_mm: Mm = 0


class ModelPickParams(DomainModel):
    """F1/AID: model for a host furniture item; `kindAs`/`variantAs` only when the rule may reclassify it."""

    model_id: Annotated[StrictStr, Field(min_length=1)]
    scale: Annotated[StrictFloat | StrictInt, Field(gt=0, le=1)]
    reason_vi: Annotated[StrictStr, Field(min_length=1)]
    kind_as: Annotated[StrictStr, Field(min_length=1)] | None = None
    variant_as: FurnitureVariant | None = None


PARAMS_BY_KIND: Final[Mapping[Kind, type[DomainModel]]] = {
    "skirting": SkirtingParams,
    "wallTile": WallTileParams,
    "cornice": CorniceParams,
    "casing": CasingParams,
    "sash": SashParams,
    "railing": RailingParams,
    "cabinetRun": CabinetRunParams,
    "curtain": CurtainParams,
    "rug": RugParams,
    "ceilingLight": CeilingLightParams,
    "item": ItemParams,
    "modelPick": ModelPickParams,
}


def params_dict(model: DomainModel) -> dict[str, JsonValue]:
    """Canonical stored form of a params model (camelCase, optional keys absent)."""
    dumped: dict[str, JsonValue] = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


def check_override(kind: Kind, params: Mapping[str, Any], override: Mapping[str, Any]) -> None:
    """Validate an override against its kind (review F9): keys must be params of `kind`, the values must keep
    `{**params, **override}` valid for the kind schema (type, sign, pattern) and an mm value stays ≤ `OVERRIDE_MM_MAX`.

    Raises `ValueError` (pydantic `ValidationError` included); the writer turns it into 422
    `SUGGESTION_OVERRIDE_INVALID`, so a bad value never reaches the compiler as a negative or huge prism.
    """
    schema = PARAMS_BY_KIND[kind]
    allowed = {f.alias or name for name, f in schema.model_fields.items()}
    unknown = sorted(set(override) - allowed)
    if unknown:
        raise ValueError(f"override có khoá không thuộc {kind}: {', '.join(unknown)}")
    schema.model_validate({**params, **override})
    huge = sorted(k for k, v in override.items() if k.endswith("Mm") and isinstance(v, int) and v > OVERRIDE_MM_MAX)
    if huge:
        raise ValueError(f"override vượt {OVERRIDE_MM_MAX} mm: {', '.join(huge)}")


class HostRef(DomainModel):
    """W4 ids of the host entity (or the host suggestion, e.g. a nightstand next to a P1 bed)."""

    wall: WallIdStr | None = None
    room: RoomIdStr | None = None
    opening: OpeningIdStr | None = None
    furniture: FurnitureIdStr | None = None
    suggestion: SuggestionId | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> HostRef:
        """A suggestion always has a host."""
        if not any((self.wall, self.room, self.opening, self.furniture, self.suggestion)):
            raise ValueError("hostRef rỗng")
        return self

    def ids(self) -> tuple[str, ...]:
        """Layer entity ids referenced (not the host suggestion)."""
        return tuple(x for x in (self.room, self.wall, self.opening, self.furniture) if x is not None)


class Stale(DomainModel):
    """Set by the server when the rule no longer produces the record; separate from the user's `state`."""

    since: Annotated[StrictInt, Field(ge=0)]
    reason_vi: Annotated[StrictStr, Field(min_length=1)]


class Suggestion(DomainModel):
    """One stored suggestion. The user owns `state` and `override`; everything else is the server's."""

    id: SuggestionId
    rule: RuleId
    rule_version: Annotated[StrictInt, Field(ge=1)]
    kind: Kind
    host_ref: HostRef
    params: dict[str, JsonValue]
    state: State = "active"
    override: Annotated[dict[ParamKey, Scalar], Field(min_length=1, max_length=OVERRIDE_MAX)] | None = None
    created_revision: Annotated[StrictInt, Field(ge=0)]
    stale: Stale | None = None
    adopted_as: FurnitureIdStr | None = None

    @model_validator(mode="after")
    def _check(self) -> Suggestion:
        """`params` must match the schema of `kind`; the override must pass `check_override`."""
        PARAMS_BY_KIND[self.kind].model_validate(self.params)
        if self.override:
            check_override(self.kind, self.params, self.override)
        if self.adopted_as is not None and self.state != "adopted":
            raise ValueError("adoptedAs chỉ đi cùng state=adopted")
        return self

    def has_user_data(self) -> bool:
        """The user touched it: non-default state or an override. Such records are never purged."""
        return self.state != "active" or self.override is not None

    def is_effective(self) -> bool:
        """Built by the compiler/editor: active or overridden and not stale (adopted lives in `layer`)."""
        return self.state in ("active", "overridden") and self.stale is None


class SuggestionsMeta(DomainModel):
    """`suggestionsMeta`: which rule table produced the list, and from which layer revision."""

    ruleset: Annotated[StrictStr, Field(min_length=1)]
    ruleset_sha: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    basis_revision: Annotated[StrictInt, Field(ge=0)]


def effective_params(suggestion: Suggestion) -> dict[str, JsonValue]:
    """Value precedence below the document: `override` > `params` (contract §3)."""
    return {**suggestion.params, **(suggestion.override or {})}


def suggestions_to_json(items: Iterable[Suggestion]) -> list[dict[str, Any]]:
    """Wire/jsonb form of a list (camelCase, optional keys absent)."""
    return [s.model_dump(mode="json", by_alias=True, exclude_none=True) for s in items]


def suggestions_from_json(raw: Sequence[Mapping[str, Any]]) -> list[Suggestion]:
    """Parse the stored list (jsonb or wire)."""
    return [Suggestion.model_validate(item) for item in raw]


def document_keys(items: Sequence[Suggestion], meta: SuggestionsMeta) -> dict[str, Any]:
    """Keys to merge into the floor document: none when the list is empty (old documents stay byte-identical)."""
    if not items:
        return {}
    return {
        "suggestions": suggestions_to_json(items),
        "suggestionsMeta": meta.model_dump(mode="json", by_alias=True),
    }
