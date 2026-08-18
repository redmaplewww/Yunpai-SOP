from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RouteStatus = Literal["draft", "under_review", "approved", "deprecated"]
ApprovalScope = Literal["none", "formal_production", "demonstration_only"]
RouteSectionType = Literal[
    "product_identity",
    "bom_material",
    "equipment_fixture",
    "process_parameter",
    "quality_control",
    "packaging_label",
    "ie_timing",
    "release_signoff",
]


class UnknownItem(BaseModel):
    field_name: str
    reason: str
    owner_role: str
    required_evidence: str
    blocking: bool = True

    @field_validator("reason", "owner_role", "required_evidence")
    @classmethod
    def require_specific_text(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 4 or text in {"待确认", "unknown", "未知"}:
            raise ValueError("unknown must state a specific reason, owner role, and required evidence")
        return text


class EvidenceRef(BaseModel):
    source_type: str
    source_path: str
    page_or_sheet: str = ""
    excerpt: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    conflict_status: Literal["clear", "conflict", "unknown"] = "clear"


class ProductIdentity(BaseModel):
    product_code: str
    product_name: str
    aliases: list[str] = Field(default_factory=list)
    process_family_code: str
    description: str = ""
    conflicts: list[str] = Field(default_factory=list)


class ProductFeatureSet(BaseModel):
    product_code: str
    process_family_code: str
    features: dict[str, str]
    conflicts: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class RouteStepDraft(BaseModel):
    step_code: str
    sequence_no: float
    title: str
    parent_step_code: str | None = None
    action: str
    why: str
    work_image_slots: int = Field(default=6, ge=1, le=6)
    inputs: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    tool_equipment: list[str] = Field(default_factory=list)
    fixtures: list[str] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    method: list[str] = Field(default_factory=list)
    quality_check: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    record_output: list[str] = Field(default_factory=list)
    exception: list[str] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)
    evidence: dict[str, list[EvidenceRef]] = Field(default_factory=dict)
    review_state: Literal["unreviewed", "confirmed", "rejected", "needs_revision"] = "unreviewed"
    reviewer_comment: str = ""

    @model_validator(mode="after")
    def require_executable_content(self) -> "RouteStepDraft":
        required_text = [self.action, self.why]
        if any(len(value.strip()) < 4 for value in required_text):
            raise ValueError("each route step requires a concrete action and purpose")
        required_lists = {
            "method": self.method,
            "quality_check": self.quality_check,
            "acceptance_criteria": self.acceptance_criteria,
            "record_output": self.record_output,
            "exception": self.exception,
        }
        missing = [name for name, values in required_lists.items() if not values]
        if missing:
            raise ValueError("route step missing executable fields: " + ", ".join(missing))
        return self


class RouteDraft(BaseModel):
    product: ProductIdentity
    route_name: str
    route_summary: str
    source_kind: Literal["exact_approved", "similar_approved", "family_template", "manual", "legacy_candidate"]
    status: RouteStatus = "draft"
    approval_scope: ApprovalScope = "none"
    version: int = 1
    steps: list[RouteStepDraft]
    route_unknowns: list[UnknownItem] = Field(default_factory=list)
    similarity: float | None = None
    reuse_source_route_id: int | None = None
    match_basis: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_route_shape(self) -> "RouteDraft":
        if not self.steps:
            raise ValueError("route must contain at least one step")
        codes = [step.step_code for step in self.steps]
        if len(codes) != len(set(codes)):
            raise ValueError("route step codes must be unique")
        for step in self.steps:
            if step.parent_step_code and step.parent_step_code not in codes:
                raise ValueError(f"parent step does not exist: {step.parent_step_code}")
        return self


class RouteSectionDraft(BaseModel):
    section_type: RouteSectionType
    version: int = Field(default=1, ge=1)
    content: dict[str, Any]
    review_state: Literal["unreviewed", "confirmed", "rejected", "needs_revision"] = "unreviewed"
    reviewer_comment: str = ""
    sources: list[EvidenceRef] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)


class RouteSectionPatch(BaseModel):
    content: dict[str, Any]
    review_state: Literal["unreviewed", "confirmed", "rejected", "needs_revision"]
    reviewer_comment: str = ""
    sources: list[EvidenceRef] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)
    reviewer: str
    decision: Literal["confirmed", "rejected", "needs_revision"]


class RouteMatch(BaseModel):
    source_route_id: int
    source_product_code: str
    source_version: int
    approval_scope: ApprovalScope
    similarity: float
    match_basis: dict[str, Any]
    field_sources: dict[str, dict[str, Any]]


class ReviewFieldPatch(BaseModel):
    step_id: int
    field_name: str
    value: Any
    decision: Literal["confirmed", "rejected", "needs_revision"] = "confirmed"
    comment: str = ""


class RenderResult(BaseModel):
    product_code: str
    route_id: int
    route_version: int
    docx_path: str
    route_json_path: str
    validation_path: str
    page_count_expected_from_route: int
    media_count: int
    image_policy: str = "human_uploaded_and_confirmed_only"
