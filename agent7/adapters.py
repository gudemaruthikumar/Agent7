from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .domain import (
    ChangeRecord,
    Comparison,
    EmittedEvent,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvidenceArtefact,
    EvidencePackResult,
    EvidenceRecord,
    ImpactNarrative,
    MetricDefinition,
    PackStorage,
    PinnedContext,
    PinnedTest,
    RmtePointer,
    ScoreResult,
    Severity,
    TaskRecord,
    TestDefinition,
    Threshold,
    ThresholdComparison,
    TriggerCode,
    TrustFactor,
)


# Map product_id -> fixture filename (without .json)
_PRODUCT_FIXTURE_MAP: dict[str, str] = {
    "rag-demo": "rag_product",
    "agentic-summariser": "agentic_summariser",
    "multimodal-classifier": "multimodal_classifier",
    "askmd-rewards": "askmd_rewards",
}


def load_fixture(path: Path | None = None, product_id: str = "rag-demo") -> dict[str, Any]:
    """Load the fixture JSON for a given product_id.

    Resolution order:
    1. Explicit ``path`` argument (testing / overrides).
    2. ``fixtures/<filename>.json`` relative to the current working directory.
    3. Two parents up from this file (repo root / fixtures/).
    4. One parent up from this file (package-level fixtures/).
    """
    filename = _PRODUCT_FIXTURE_MAP.get(product_id, "rag_product") + ".json"
    candidates = [
        path,
        Path.cwd() / "fixtures" / filename,
        Path(__file__).resolve().parents[1] / "fixtures" / filename,
        Path(__file__).resolve().parent / "fixtures" / filename,
    ]
    for fixture_path in candidates:
        if fixture_path and fixture_path.exists():
            with fixture_path.open(encoding="utf-8") as handle:
                return json.load(handle)
    raise FileNotFoundError(f"Fixture for product '{product_id}' not found (looked for {filename})")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDGenerator:
    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"


def default_tenant_id() -> str:
    return os.environ.get("AGENT7_TENANT_ID", "tlai-demo")


def worm_bucket() -> str:
    return os.environ.get("MINIO_WORM_BUCKET", "tlai-evidence-worm")


def pack_join_sha256(pack: EvidencePackResult) -> str:
    payload = {
        "pack_id": pack.pack_id,
        "tenant_id": pack.tenant_id,
        "product_id": pack.product_id,
        "release": pack.release,
        "artefacts": [item.model_dump(mode="json") for item in pack.artefacts],
        "gaps": pack.gaps,
        "coverage": pack.coverage,
        "storage": pack.storage.model_dump(mode="json"),
        "integrity": pack.integrity,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def index_artefact(
    *,
    artefact_id: str,
    tenant_id: str,
    product_id: str,
    kind: str,
    risk_id: str | None,
    metric_id: str | None,
    test_id: str | None,
    file_sha256: str,
    source: str,
    uri: str,
) -> EvidenceArtefact:
    worm_key = f"{tenant_id}/{product_id}/{artefact_id}"
    return EvidenceArtefact(
        artefact_id=artefact_id,
        tenant_id=tenant_id,
        product_id=product_id,
        kind=kind,
        rmte=RmtePointer(risk_id=risk_id, metric_id=metric_id, test_id=test_id),
        sha256=file_sha256,
        worm_key=worm_key,
        worm_uri=f"s3://{worm_bucket()}/{worm_key}",
        source=source,
        uri=uri,
    )


class SyntheticRMTE:
    def __init__(
        self,
        fixture: dict[str, Any] | None = None,
        product_id: str = "rag-demo",
    ) -> None:
        self.product_id = product_id
        self.fixture = fixture or load_fixture(product_id=product_id)
        self._metrics = {
            metric_id: MetricDefinition(
                id=metric_id,
                name=data["name"],
                category=data["category"],
                threshold=Threshold.model_validate(data["threshold"]),
                approved_test_ids=data["approved_test_ids"],
                risk_ids=data["risk_ids"],
            )
            for metric_id, data in self.fixture["metrics"].items()
        }
        self._tests = {
            test_id: TestDefinition(id=test_id, **data)
            for test_id, data in self.fixture["tests"].items()
        }

    def impact_for(self, trigger: TriggerCode) -> dict[str, Any]:
        return dict(self.fixture["impacts"][trigger.value])

    def metrics(self) -> dict[str, MetricDefinition]:
        return self._metrics

    def tests(self) -> dict[str, TestDefinition]:
        return self._tests

    def risks(self) -> dict[str, dict[str, Any]]:
        return dict(self.fixture["risks"])

    def product_context(self) -> dict[str, Any]:
        return dict(self.fixture["product"])

    def baseline_scores(self) -> dict[str, float]:
        return dict(self.fixture["baseline_scores"])

    def previous_pack(self) -> EvidencePackResult:
        product = self.product_context()
        tenant = default_tenant_id()
        pack_id = product["baseline_pack_id"]
        artefacts: list[EvidenceArtefact] = []
        for metric_id, metric in self.metrics().items():
            test_id = metric.approved_test_ids[0] if metric.approved_test_ids else None
            risk_id = metric.risk_ids[0] if metric.risk_ids else None
            file_sha256 = hashlib.sha256(
                f"worm|{pack_id}|{metric_id}|{test_id}|baseline".encode()
            ).hexdigest()
            artefacts.append(
                index_artefact(
                    artefact_id=f"art_baseline_{metric_id}",
                    tenant_id=tenant,
                    product_id=product["id"],
                    kind="evaluation_trace",
                    risk_id=risk_id,
                    metric_id=metric_id,
                    test_id=test_id,
                    file_sha256=file_sha256,
                    source="baseline-index",
                    uri=f"eval://baseline/{metric_id}",
                )
            )
        pack = EvidencePackResult(
            pack_id=pack_id,
            tenant_id=tenant,
            product_id=product["id"],
            release=product.get("active_version") or product.get("product_version") or "",
            artefacts=artefacts,
            gaps=[],
            coverage={
                "artefacts": len(artefacts),
                "metrics": len(self.metrics()),
                "risks": len(self.risks()),
                "gaps": 0,
                "complete": True,
            },
            storage=PackStorage(),
            integrity="HASH_ONLY",
            q6_label="Pending Q6",
            status="approved",
        )
        pack.sha256 = pack_join_sha256(pack)
        return pack

    def seed_changes(self) -> list[dict[str, Any]]:
        return list(self.fixture.get("seed_changes", []))

    def change_detail(self, event: Any) -> str:
        key = "prompt" if event.name.value == "prompt.changed" else "corpus"
        return str(self.fixture["registry"].get(key, {}).get("summary") or event.name.value)


class SyntheticPinner:
    def __init__(self, rmte: SyntheticRMTE):
        self.rmte = rmte

    def pin(self, event: Any, test_ids: list[str]) -> PinnedContext:
        product = self.rmte.product_context()
        tests = self.rmte.tests()
        pinned_tests = [
            PinnedTest(test_id=test_id, version=tests[test_id].version, hash=tests[test_id].hash)
            for test_id in test_ids
        ]
        prompt_version = (
            event.resource_version
            if event.name.value == "prompt.changed"
            else product["prompt_version"]
        )
        corpus_version = (
            event.resource_version
            if event.name.value == "retrieval.corpus_updated"
            else product["corpus_version"]
        )
        seed = "|".join(
            [
                event.product_version,
                prompt_version,
                corpus_version,
                product["dataset"]["hash"],
                *[item.hash for item in pinned_tests],
            ]
        )
        pin_hash = hashlib.sha256(seed.encode()).hexdigest()
        return PinnedContext(
            pin_reference=f"pin://{pin_hash[:20]}",
            product_version=event.product_version,
            prompt_version=prompt_version,
            corpus_version=corpus_version,
            dataset_id=product["dataset"]["id"],
            dataset_version=product["dataset"]["version"],
            dataset_hash=product["dataset"]["hash"],
            tests=pinned_tests,
            memory_snapshot_hash=product["memory_snapshot_hash"],
        )


class SyntheticEvaluation:
    def __init__(self, rmte: SyntheticRMTE):
        self.rmte = rmte

    def start_delta_run(
        self,
        job_id: str,
        test_ids: list[str],
        context: PinnedContext,
        simulation: EvaluationOutcome,
    ) -> EvaluationRun:
        values_key = "pass_values" if simulation == EvaluationOutcome.PASS else "fail_values"
        values = self.rmte.fixture["evaluation"][values_key]
        tests = self.rmte.tests()
        results: list[EvaluationResult] = []
        for test_id in test_ids:
            for metric_id in tests[test_id].metric_ids:
                value = (
                    values[metric_id]
                    if simulation in {EvaluationOutcome.PASS, EvaluationOutcome.QUALITY_FAIL}
                    else None
                )
                results.append(
                    EvaluationResult(
                        test_id=test_id,
                        metric_id=metric_id,
                        outcome=simulation,
                        value=value,
                        run_reference=f"eval://{job_id}/{metric_id}",
                        error_reference=(
                            f"error://{job_id}/{metric_id}"
                            if simulation
                            in {EvaluationOutcome.INFRA_FAIL, EvaluationOutcome.OVER_BUDGET}
                            else None
                        ),
                    )
                )
        return EvaluationRun(reference=f"eval://{job_id}", results=results)


class SyntheticEvidenceWriter:
    def __init__(self, rmte: Any | None = None) -> None:
        self.rmte = rmte

    def write(self, job_id: str, records: list[EvidenceRecord]) -> EvidencePackResult:
        tenant = default_tenant_id()
        product_id = (
            self.rmte.product_context()["id"] if self.rmte is not None else "rag-demo"
        )
        release = ""
        if self.rmte is not None:
            product = self.rmte.product_context()
            release = str(product.get("active_version") or product.get("product_version") or "")
        metrics = self.rmte.metrics() if self.rmte is not None else {}
        artefacts: list[EvidenceArtefact] = []
        for item in records:
            metric = metrics.get(item.metric_id)
            risk_id = metric.risk_ids[0] if metric and metric.risk_ids else None
            file_sha256 = hashlib.sha256(
                f"worm|{job_id}|{item.metric_id}|{item.test_id}|{item.outcome.value}|{item.value}".encode()
            ).hexdigest()
            artefacts.append(
                index_artefact(
                    artefact_id=item.evidence_id,
                    tenant_id=tenant,
                    product_id=product_id,
                    kind="evaluation_trace",
                    risk_id=risk_id,
                    metric_id=item.metric_id,
                    test_id=item.test_id,
                    file_sha256=file_sha256,
                    source="evaluation-pipeline",
                    uri=item.run_reference,
                )
            )
        pack = EvidencePackResult(
            pack_id=f"pack_{job_id}",
            tenant_id=tenant,
            product_id=product_id,
            release=release,
            artefacts=artefacts,
            gaps=[],
            coverage={"artefacts": len(artefacts), "gaps": 0, "complete": False},
            storage=PackStorage(),
            integrity="HASH_ONLY",
            q6_label="Pending Q6",
            change_id=job_id,
            status="draft",
        )
        pack.sha256 = pack_join_sha256(pack)
        return pack


# ---------------------------------------------------------------------------
# Governance mode classification constants
# ---------------------------------------------------------------------------
_MIMETIC_CATEGORIES: frozenset[str] = frozenset(
    {"Drift", "Lifecycle", "Corpus & Data", "Corpus Quality", "Monitoring & Lifecycle"}
)

_SEVERITY_WEIGHTS: dict[str, float] = {
    Severity.CRITICAL: 1.5,
    Severity.HIGH: 1.0,
    Severity.MEDIUM: 0.5,
    Severity.LOW: 0.25,
}


class SyntheticQ6:
    """Q6 Trust Factor scorer — GaaS Algorithm 1.

    Q6 IS the trust factor. This class computes TFa from every completed
    revalidation run and is the single authoritative scorer in Agent7.

    TFa = alpha*(1 - Vnorm/N) + beta*(1 - Vcoer/N) + gamma*(1 - Vmim/N) - delta*Ssum

    Metric governance mode routing:
      Coercive  - LT/LTE threshold metrics (non-negotiable safety ceilings).
      Mimetic   - Drift / Lifecycle / Corpus & Data category metrics.
      Normative - all remaining GTE quality/retrieval/generation metrics.

    Default hyperparameters (tunable at construction):
      alpha=0.40  beta=0.40  gamma=0.20  delta=0.10
    """

    def __init__(
        self,
        alpha: float = 0.40,
        beta: float = 0.40,
        gamma: float = 0.20,
        delta: float = 0.10,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.delta = delta
        self.call_count = 0

    @staticmethod
    def _governance_mode(metric: MetricDefinition) -> str:
        """Route a metric into its GaaS governance mode."""
        if metric.threshold.comparison in {Comparison.LT, Comparison.LTE}:
            return "coercive"
        if metric.category in _MIMETIC_CATEGORIES:
            return "mimetic"
        return "normative"

    def score(
        self,
        pack_reference: str,
        comparisons: list[ThresholdComparison],
        severity: Severity,
        metric_definitions: dict[str, MetricDefinition],
    ) -> TrustFactor:
        """Compute TFa (Q6 score) for a completed revalidation run."""
        self.call_count += 1
        alpha, beta, gamma, delta = self.alpha, self.beta, self.gamma, self.delta
        n_actions = len(comparisons)
        token = str(sum(ord(c) for c in pack_reference) % 100_000)
        reference = f"q6://tfa/{token}"

        if n_actions == 0:
            return TrustFactor(
                score=round(alpha + beta + gamma, 4),
                reference=reference,
                vnorm=0, vcoer=0, vmim=0,
                n_actions=0, ssum=0.0,
                alpha=alpha, beta=beta, gamma=gamma, delta=delta,
                weighted_avg_violation=0.0,
            )

        norm_ids: list[str] = []
        coer_ids: list[str] = []
        mim_ids: list[str] = []
        for mid, mdef in metric_definitions.items():
            mode = self._governance_mode(mdef)
            if mode == "coercive":
                coer_ids.append(mid)
            elif mode == "mimetic":
                mim_ids.append(mid)
            else:
                norm_ids.append(mid)

        coer_set = frozenset(coer_ids)
        mim_set = frozenset(mim_ids)

        vnorm = sum(
            1 for c in comparisons
            if not c.passed and c.metric_id not in coer_set and c.metric_id not in mim_set
        )
        vcoer = sum(1 for c in comparisons if not c.passed and c.metric_id in coer_set)
        vmim = sum(1 for c in comparisons if not c.passed and c.metric_id in mim_set)

        severity_w = _SEVERITY_WEIGHTS.get(severity, 1.0)
        ssum = round(
            sum(abs(c.actual - c.threshold) * severity_w for c in comparisons if not c.passed),
            4,
        )

        tfa = (
            alpha * (1.0 - vnorm / n_actions)
            + beta * (1.0 - vcoer / n_actions)
            + gamma * (1.0 - vmim / n_actions)
            - delta * ssum
        )
        total_weight = alpha + beta + gamma
        weighted_avg = round(
            (alpha * vnorm + beta * vcoer + gamma * vmim) / total_weight, 4
        ) if total_weight > 0 else 0.0

        return TrustFactor(
            score=round(max(0.0, min(1.0, tfa)), 4),
            reference=reference,
            vnorm=vnorm, vcoer=vcoer, vmim=vmim,
            n_actions=n_actions, ssum=ssum,
            alpha=alpha, beta=beta, gamma=gamma, delta=delta,
            weighted_avg_violation=weighted_avg,
            normative_metric_ids=sorted(norm_ids),
            coercive_metric_ids=sorted(coer_ids),
            mimetic_metric_ids=sorted(mim_ids),
        )


def _metrics_for_risks(
    risk_ids: list[str],
    metrics: dict[str, MetricDefinition],
    allowed_metric_ids: list[str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    allowed = set(allowed_metric_ids) if allowed_metric_ids else None
    metric_ids = [
        metric_id
        for metric_id, metric in metrics.items()
        if any(risk_id in metric.risk_ids for risk_id in risk_ids)
        and (allowed is None or metric_id in allowed)
    ]
    by_risk = {
        risk_id: [metric_id for metric_id in metric_ids if risk_id in metrics[metric_id].risk_ids]
        for risk_id in risk_ids
    }
    by_risk = {risk_id: ids for risk_id, ids in by_risk.items() if ids}
    return metric_ids, by_risk


class FallbackExplainer:
    def explain(self, change: ChangeRecord) -> ImpactNarrative:
        risk_ids = [item.risk_id for item in change.affected_risks]
        rationale = {
            item.risk_id: f"{item.name} is linked to {', '.join(item.metric_ids)}."
            for item in change.affected_risks
        }
        return ImpactNarrative(
            summary=(
                f"{change.classification.trigger_code.value} affects "
                f"{', '.join(risk_ids) or 'no mapped risks'} and requires a delta revalidation."
            ),
            risk_rationale=rationale,
            review_focus=[
                "Compare new metric scores with the previous evidence pack.",
                "Approve only after reviewing failed thresholds.",
            ],
            source="fallback",
        )

    def map_risks_and_metrics(
        self,
        change: ChangeRecord,
        risks: dict[str, dict[str, Any]],
        metrics: dict[str, MetricDefinition],
        default_risk_ids: list[str],
        allowed_metric_ids: list[str] | None = None,
    ) -> tuple[list[str], dict[str, list[str]], ImpactNarrative]:
        risk_ids = [risk_id for risk_id in default_risk_ids if risk_id in risks]
        metric_ids, by_risk = _metrics_for_risks(risk_ids, metrics, allowed_metric_ids)
        risk_ids = [risk_id for risk_id in risk_ids if by_risk.get(risk_id)]
        narrative = ImpactNarrative(
            summary=(
                f"{change.classification.trigger_code.value} mapped to risks "
                f"{', '.join(risk_ids)} and metrics {', '.join(metric_ids)}."
            ),
            risk_rationale={
                risk_id: f"{risks[risk_id]['name']} maps to {', '.join(ids)}."
                for risk_id, ids in by_risk.items()
            },
            review_focus=["Stale the mapped metrics, then run evaluation from that set."],
            source="fallback",
        )
        return risk_ids, by_risk, narrative


class GroqExplainer:
    def __init__(self, fallback: FallbackExplainer | None = None):
        self.fallback = fallback or FallbackExplainer()
        self.api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        self.model = os.environ.get("GROQ_MODEL") or os.environ.get(
            "OPENAI_MODEL", "llama-3.3-70b-versatile"
        )

    def explain(self, change: ChangeRecord) -> ImpactNarrative:
        allowed_risks = {item.risk_id for item in change.affected_risks}
        if not self.api_key:
            return self.fallback.explain(change)
        try:
            parsed = self._complete(
                system=(
                    "Explain RMTE impact using only provided risk, metric, and test IDs. "
                    "Return JSON with summary, risk_rationale map, and review_focus list. "
                    "Do not invent IDs, thresholds, or a verdict."
                ),
                payload={
                    "change": change.event.name.value,
                    "version": change.event.resource_version,
                    "detail": change.detail,
                    "risks": [item.model_dump() for item in change.affected_risks],
                    "metrics": change.affected_metric_ids,
                    "tests": change.selected_test_ids,
                },
            )
            rationale = {
                key: value
                for key, value in dict(parsed.get("risk_rationale") or {}).items()
                if key in allowed_risks
            }
            fallback = self.fallback.explain(change)
            return ImpactNarrative(
                summary=str(parsed.get("summary") or fallback.summary),
                risk_rationale=rationale or fallback.risk_rationale,
                review_focus=list(parsed.get("review_focus") or [])[:4] or fallback.review_focus,
                source="groq",
            )
        except Exception:
            return self.fallback.explain(change)

    def map_risks_and_metrics(
        self,
        change: ChangeRecord,
        risks: dict[str, dict[str, Any]],
        metrics: dict[str, MetricDefinition],
        default_risk_ids: list[str],
        allowed_metric_ids: list[str] | None = None,
    ) -> tuple[list[str], dict[str, list[str]], ImpactNarrative]:
        fallback = self.fallback.map_risks_and_metrics(
            change, risks, metrics, default_risk_ids, allowed_metric_ids
        )
        self.api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY") or self.api_key
        if not self.api_key:
            return fallback
        scoped_metrics = {
            metric_id: metric
            for metric_id, metric in metrics.items()
            if not allowed_metric_ids or metric_id in set(allowed_metric_ids)
        }
        scoped_risk_ids = [
            risk_id
            for risk_id in default_risk_ids
            if any(risk_id in metric.risk_ids for metric in scoped_metrics.values())
        ]
        catalog = {
            "risks": {
                risk_id: {"name": data["name"], "category": data["category"]}
                for risk_id, data in risks.items()
                if risk_id in set(scoped_risk_ids)
            },
            "metrics": {
                metric_id: {
                    "name": metric.name,
                    "category": metric.category,
                    "risk_ids": [
                        rid for rid in metric.risk_ids if rid in set(scoped_risk_ids)
                    ],
                }
                for metric_id, metric in scoped_metrics.items()
            },
        }
        try:
            parsed = self._complete(
                system=(
                    "You are Agent 7. From the product change, select which RMTE risk IDs apply, "
                    "then map each selected risk only to metric IDs listed in the catalog. "
                    "Return JSON with risk_ids, metrics_by_risk, summary, and risk_rationale. "
                    "Use only catalog IDs. Do not invent IDs, tests, thresholds, or a verdict. "
                    "Do not select every risk unless the change truly affects them all."
                ),
                payload={
                    "change": change.event.name.value,
                    "trigger": change.classification.trigger_code.value,
                    "version": change.event.resource_version,
                    "detail": change.detail,
                    "catalog": catalog,
                    "hint_risk_ids": scoped_risk_ids,
                },
            )
            allowed_risks = set(catalog["risks"])
            risk_ids = [
                risk_id
                for risk_id in list(parsed.get("risk_ids") or scoped_risk_ids)
                if risk_id in allowed_risks
            ]
            if not risk_ids:
                return fallback
            _, linked = _metrics_for_risks(risk_ids, metrics, allowed_metric_ids)
            raw_by_risk = dict(parsed.get("metrics_by_risk") or {})
            by_risk: dict[str, list[str]] = {}
            for risk_id in risk_ids:
                allowed = set(linked.get(risk_id) or [])
                chosen = [mid for mid in list(raw_by_risk.get(risk_id) or []) if mid in allowed]
                ids = chosen or linked.get(risk_id) or []
                if ids:
                    by_risk[risk_id] = ids
            risk_ids = [risk_id for risk_id in risk_ids if risk_id in by_risk]
            if not risk_ids:
                return fallback
            rationale = {
                key: value
                for key, value in dict(parsed.get("risk_rationale") or {}).items()
                if key in allowed_risks
            }
            return (
                risk_ids,
                by_risk,
                ImpactNarrative(
                    summary=str(parsed.get("summary") or fallback[2].summary),
                    risk_rationale=rationale or fallback[2].risk_rationale,
                    review_focus=["Stale the mapped metrics, then run evaluation from that set."],
                    source="groq",
                ),
            )
        except Exception as exc:
            _risk_ids, _by_risk, narrative = fallback
            narrative = narrative.model_copy(
                update={
                    "review_focus": [
                        f"LLM call failed ({type(exc).__name__}). Using the RMTE table.",
                        *narrative.review_focus,
                    ][:4]
                }
            )
            return _risk_ids, _by_risk, narrative

    def _complete(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        completion = client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        return json.loads(completion.choices[0].message.content or "{}")


class SyntheticNotificationTicket:
    def __init__(self, repository: Any):
        self.repository = repository

    def create(self, notification: TaskRecord) -> str:
        self.repository.save_task(notification)
        return f"{notification.channel}://{notification.task_id}"


class RepositoryPublisher:
    def __init__(self, repository: Any):
        self.repository = repository

    def publish(self, event: EmittedEvent) -> None:
        self.repository.append_event(event)


class DefaultDenyGateway:
    def execute(self, tool: str, allowlist: set[str], call: Callable[[], Any]) -> Any:
        if tool not in allowlist:
            raise PermissionError(f"Tool is not allowed for this run: {tool}")
        return call()
