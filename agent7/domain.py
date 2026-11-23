from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RawEventName(StrEnum):
    PROMPT_CHANGED = "prompt.changed"
    CORPUS_UPDATED = "retrieval.corpus_updated"


class TriggerCode(StrEnum):
    TD_06 = "TD-06"
    TD_11 = "TD-11"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DeltaScope(StrEnum):
    DELTA = "DELTA"


class ChangeStatus(StrEnum):
    DETECTED = "detected"
    IMPACT_READY = "impact_ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    KILLED = "killed"
    DEBOUNCED = "debounced"
    FAILED = "failed"


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


class Comparison(StrEnum):
    GTE = "gte"
    LTE = "lte"
    LT = "lt"
    GT = "gt"


class Posture(StrEnum):
    GO = "GO"
    CONDITIONAL = "CONDITIONAL"
    RISK = "RISK"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class Criticality(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvaluationOutcome(StrEnum):
    PASS = "pass"
    QUALITY_FAIL = "quality_fail"
    INFRA_FAIL = "infra_fail"
    OVER_BUDGET = "over_budget"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RegistryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str | None = None
    name: RawEventName
    product_id: str = "rag-demo"
    resource_version: str
    product_version: str = "rag-product-v1"
    occurred_at: datetime | None = None
    criticality: Criticality = Criticality.HIGH
    simulation: EvaluationOutcome = EvaluationOutcome.PASS
    estimated_cost: float | None = Field(default=None, ge=0)
    detail: str | None = None


class EventClassification(BaseModel):
    trigger_code: TriggerCode
    scope: DeltaScope = DeltaScope.DELTA
    severity: Severity


class Threshold(BaseModel):
    value: float
    comparison: Comparison = Comparison.GTE
    band_min: float | None = None
    band_max: float | None = None

    def passes(self, actual: float) -> bool:
        if self.band_min is not None and self.band_max is not None:
            return self.band_min <= actual <= self.band_max
        if self.comparison == Comparison.GTE:
            return actual >= self.value
        if self.comparison == Comparison.LTE:
            return actual <= self.value
        if self.comparison == Comparison.GT:
            return actual > self.value
        return actual < self.value


class MetricDefinition(BaseModel):
    id: str
    name: str
    category: str
    threshold: Threshold
    approved_test_ids: list[str]
    risk_ids: list[str]


class TestDefinition(BaseModel):
    id: str
    name: str
    metric_ids: list[str]
    version: str
    hash: str
    g2_approved: bool = True
    estimated_cost: float
    estimated_duration_seconds: float


class AffectedRisk(BaseModel):
    risk_id: str
    name: str
    category: str
    metric_ids: list[str]


class ThresholdComparison(BaseModel):
    metric_id: str
    metric_name: str
    previous: float | None
    actual: float
    threshold: float
    comparison: Comparison
    passed: bool
    delta: float | None = None


class PinnedTest(BaseModel):
    test_id: str
    version: str
    hash: str


class PinnedContext(BaseModel):
    pin_reference: str
    product_version: str
    prompt_version: str
    corpus_version: str
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    tests: list[PinnedTest]
    memory_snapshot_hash: str


class EvaluationResult(BaseModel):
    test_id: str
    metric_id: str
    outcome: EvaluationOutcome
    value: float | None = None
    run_reference: str
    error_reference: str | None = None


class EvaluationRun(BaseModel):
    reference: str
    results: list[EvaluationResult]


class EvidenceRecord(BaseModel):
    evidence_id: str
    job_id: str
    metric_id: str
    test_id: str
    outcome: EvaluationOutcome
    value: float | None
    run_reference: str
    created_at: datetime


class RmtePointer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_id: str | None = None
    metric_id: str | None = None
    test_id: str | None = None


class EvidenceArtefact(BaseModel):
    """Index row only. The immutable file lives in MinIO Object Lock, not in the pack."""

    model_config = ConfigDict(extra="forbid")
    artefact_id: str
    tenant_id: str
    product_id: str
    kind: str
    rmte: RmtePointer
    sha256: str
    worm_key: str
    worm_uri: str
    source: str
    uri: str


class PackStorage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: str = "index"
    immutable_file: str = "worm"


class EvidencePackResult(BaseModel):
    """Join document: artefact pointers + gaps. Files are not copied into the pack."""

    model_config = ConfigDict(extra="ignore")
    pack_id: str
    tenant_id: str = ""
    product_id: str = ""
    release: str = ""
    artefacts: list[EvidenceArtefact] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    storage: PackStorage = Field(default_factory=PackStorage)
    integrity: str = "HASH_ONLY"
    q6_label: str = "Pending Q6"
    sha256: str = ""
    status: str = "draft"
    change_id: str | None = None
    created_at: datetime | None = None


class ScoreResult(BaseModel):
    opaque_score: str
    reference: str


class TrustFactor(BaseModel):
    """Trust Factor TFa from the GaaS enforcement formula (Algorithm 1).

    TFa = α(1 − Vnorm/N) + β(1 − Vcoer/N) + γ(1 − Vmim/N) − δ·Ssum

    Governance modes mapped to Agent7 metric categories:
    • Normative  — quality/retrieval/generation metrics (GTE comparison)
    • Coercive   — safety-critical upper-bound metrics (LT/LTE comparison)
    • Mimetic    — drift/regression/corpus metrics (Drift, Lifecycle, Corpus & Data)

    The score is clamped to [0.0, 1.0]. A value of 1.0 means full compliance.
    """

    score: float  # TFa — clamped to [0.0, 1.0]
    reference: str  # q6://tfa/<change_id> — used as last_score_ref in AssuranceState
    vnorm: int  # normative violation count
    vcoer: int  # coercive violation count
    vmim: int  # mimetic violation count
    n_actions: int  # total metric evaluations (N)
    ssum: float  # recency-weighted severity sum
    alpha: float
    beta: float
    gamma: float
    delta: float
    weighted_avg_violation: float  # V̄ = (α·Vnorm + β·Vcoer + γ·Vmim) / (α+β+γ)
    normative_metric_ids: list[str] = Field(default_factory=list)
    coercive_metric_ids: list[str] = Field(default_factory=list)
    mimetic_metric_ids: list[str] = Field(default_factory=list)


class ImpactNarrative(BaseModel):
    summary: str
    risk_rationale: dict[str, str] = Field(default_factory=dict)
    review_focus: list[str] = Field(default_factory=list)
    source: str = "fallback"


class ApprovalRecord(BaseModel):
    decision: Decision
    reviewer: str
    notes: str = ""
    decided_at: datetime


class TaskRecord(BaseModel):
    task_id: str
    job_id: str
    kind: str
    channel: str = "ticket"
    status: str = "open"
    references: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class EmittedEvent(BaseModel):
    event_id: str
    type: str
    payload: dict[str, Any]
    created_at: datetime


class LedgerEntry(BaseModel):
    ledger_id: str
    job_id: str
    stage: str
    action: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AssuranceState(BaseModel):
    product_id: str
    posture: Posture
    metric_freshness: dict[str, Freshness]
    active_version: str
    last_pack_hash: str | None = None
    last_pack_id: str | None = None
    last_score_ref: str | None = None
    updated_at: datetime


class ChangeRecord(BaseModel):
    change_id: str
    event: RegistryEvent
    classification: EventClassification
    status: ChangeStatus
    detail: str
    affected_risks: list[AffectedRisk] = Field(default_factory=list)
    affected_metric_ids: list[str] = Field(default_factory=list)
    selected_test_ids: list[str] = Field(default_factory=list)
    previous_pack: EvidencePackResult | None = None
    previous_scores: dict[str, float] = Field(default_factory=dict)
    narrative: ImpactNarrative | None = None
    estimated_cost: float = 0
    estimated_duration_seconds: float = 0
    pinned_context: PinnedContext | None = None
    eval_run: EvaluationRun | None = None
    evidence_pack: EvidencePackResult | None = None
    comparisons: list[ThresholdComparison] = Field(default_factory=list)
    trust_factor: TrustFactor | None = None
    posture_before: Posture
    posture_after: Posture | None = None
    approval: ApprovalRecord | None = None
    task_reference: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DashboardSummary(BaseModel):
    product_id: str
    posture: Posture
    completed_revalidations: int
    pending_changes: int
    awaiting_approval: int
    evidence_packs: int
    notifications: int
    stale_metrics: list[str]
    last_pack_id: str | None = None


class KillSwitch(BaseModel):
    enabled: bool


class RegistryEntry(BaseModel):
    id: str
    version: str
    prompt_hash: str
    tools: list[str]
    output_schema: dict[str, Any]


class DecisionRequest(BaseModel):
    reviewer: str = "product-owner"
    notes: str = ""
