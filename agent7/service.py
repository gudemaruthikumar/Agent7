from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .domain import (
    AffectedRisk,
    ApprovalRecord,
    AssuranceState,
    ChangeRecord,
    ChangeStatus,
    DashboardSummary,
    Decision,
    DeltaScope,
    EmittedEvent,
    EvaluationOutcome,
    EventClassification,
    EvidenceRecord,
    Freshness,
    LedgerEntry,
    Posture,
    RawEventName,
    RegistryEvent,
    Severity,
    TaskRecord,
    ThresholdComparison,
    TriggerCode,
    TrustFactor,
)
from .adapters import pack_join_sha256
from .ports import (
    Clock,
    DatasetPinner,
    EvaluationRunner,
    EventPublisher,
    EvidenceWriter,
    IDGenerator,
    ImpactExplainer,
    NotificationTicketPort,
    Persistence,
    Q6Scorer,
    RMTEReader,
    ToolGateway,
)


@dataclass
class Policy:
    budget_limit: float = 8.0


class AssuranceRevalidationService:
    """Agent work stops at change → RMTE risks → metrics.

    Staling those metrics, running evaluation from the stale set, and comparing
    new scores to the previous pack are pipeline steps, not agent work.
    """

    AGENT_ALLOWLIST = {"rmte.read", "llm.map"}
    PIPELINE_ALLOWLIST = {
        "dataset.pin",
        "evaluation.start_delta_run",
        "evidence.write",
        "q6.score",
        "events.publish",
        "notification.create",
        "ticket.create",
    }
    ALLOWLIST = AGENT_ALLOWLIST | PIPELINE_ALLOWLIST

    def __init__(
        self,
        repository: Persistence,
        rmte: RMTEReader,
        pinner: DatasetPinner,
        evaluation: EvaluationRunner,
        evidence: EvidenceWriter,
        scorer: Q6Scorer,
        notifier: NotificationTicketPort,
        publisher: EventPublisher,
        gateway: ToolGateway,
        clock: Clock,
        ids: IDGenerator,
        explainer: ImpactExplainer,
        policy: Policy | None = None,
    ):
        self.repo = repository
        self.rmte = rmte
        self.pinner = pinner
        self.evaluation = evaluation
        self.evidence = evidence
        self.scorer = scorer
        self.notifier = notifier
        self.publisher = publisher
        self.gateway = gateway
        self.clock = clock
        self.ids = ids
        self.explainer = explainer
        self.policy = policy or Policy()
        self.kill_switch = False

    @staticmethod
    def classify(event: RegistryEvent) -> EventClassification:
        if event.name == RawEventName.PROMPT_CHANGED:
            return EventClassification(trigger_code=TriggerCode.TD_06, severity=Severity.HIGH)
        return EventClassification(trigger_code=TriggerCode.TD_11, severity=Severity.MEDIUM)

    def _ledger(
        self, job_id: str, stage: str, action: str, data: dict[str, Any] | None = None
    ) -> None:
        self.repo.append_ledger(
            LedgerEntry(
                ledger_id=self.ids.new("ledger"),
                job_id=job_id,
                stage=stage,
                action=action,
                data=data or {},
                created_at=self.clock.now(),
            )
        )

    def _call_tool(self, job_id: str, tool: str, call: Callable[[], Any]) -> Any:
        self._ledger(job_id, "tool", "tool_requested", {"tool": tool})
        result = self.gateway.execute(tool, self.ALLOWLIST, call)
        self._ledger(job_id, "tool", "tool_completed", {"tool": tool})
        return result

    def _publish(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = EmittedEvent(
            event_id=self.ids.new("event"),
            type=event_type,
            payload=payload,
            created_at=self.clock.now(),
        )
        self._call_tool(job_id, "events.publish", lambda: self.publisher.publish(event))

    def initial_state(self, product_id: str) -> AssuranceState:
        raw = self.repo.get_state(product_id)
        if raw:
            return AssuranceState.model_validate(raw)
        product = self.rmte.product_context()
        return AssuranceState(
            product_id=product_id,
            posture=Posture.GO,
            metric_freshness={metric_id: Freshness.FRESH for metric_id in self.rmte.metrics()},
            active_version=product["active_version"],
            last_pack_hash=product["baseline_pack_hash"],
            last_pack_id=product["baseline_pack_id"],
            last_score_ref=product.get("baseline_score_ref"),
            updated_at=self.clock.now(),
        )

    def bootstrap(self) -> None:
        product = self.rmte.product_context()
        state = self.initial_state(product["id"])
        self.repo.save_state(state.model_dump(mode="json"))
        pack = self.rmte.previous_pack()
        pack.created_at = self.clock.now()
        self.repo.save_pack(pack)
        state.last_pack_hash = pack.sha256
        state.last_pack_id = pack.pack_id
        self.repo.save_state(state.model_dump(mode="json"))
        if self.repo.list_changes():
            return
        for seed in self.rmte.seed_changes():
            self.register_change(RegistryEvent.model_validate(seed))

    def register_change(self, incoming: RegistryEvent) -> ChangeRecord:
        now = self.clock.now()
        event = incoming.model_copy(
            update={
                "event_id": incoming.event_id or self.ids.new("changeevt"),
                "occurred_at": incoming.occurred_at or now,
            }
        )
        classification = self.classify(event)
        state = self.initial_state(event.product_id)
        change = ChangeRecord(
            change_id=self.ids.new("chg"),
            event=event,
            classification=classification,
            status=ChangeStatus.DETECTED,
            detail=event.detail or self.rmte.change_detail(event),
            previous_pack=self.rmte.previous_pack(),
            previous_scores=self.rmte.baseline_scores(),
            posture_before=state.posture,
            created_at=now,
            updated_at=now,
        )
        self.repo.save_change(change)
        self._ledger(
            change.change_id,
            "1.classify",
            "classified",
            {
                "raw_event_name": event.name.value,
                "trigger_code": classification.trigger_code.value,
                "scope": DeltaScope.DELTA.value,
            },
        )
        if self.kill_switch:
            change.status = ChangeStatus.KILLED
            change.updated_at = self.clock.now()
            self._ledger(change.change_id, "1.classify", "kill_switch_blocked")
            self.repo.save_change(change)
            return change

        duplicate = next(
            (
                old
                for old in self.repo.list_changes()
                if old.change_id != change.change_id
                and old.event.product_id == event.product_id
                and old.event.name == event.name
                and old.event.resource_version == event.resource_version
                and old.status not in {ChangeStatus.KILLED, ChangeStatus.DEBOUNCED}
            ),
            None,
        )
        if duplicate:
            change.status = ChangeStatus.DEBOUNCED
            change.recommendations = [f"Use existing change://{duplicate.change_id}"]
            change.updated_at = self.clock.now()
            self.repo.save_change(change)
            return change

        self._publish(
            change.change_id,
            "decay.trigger_fired",
            {"change_ref": f"change://{change.change_id}"},
        )
        self.repo.save_change(change)
        change = self._agent_map_risks_and_metrics(change)
        if change.status != ChangeStatus.IMPACT_READY:
            return change
        return self._stale_mapped_metrics(change, state)

    def _agent_map_risks_and_metrics(self, change: ChangeRecord) -> ChangeRecord:
        """Agent work: LLM (or fallback) finds RMTE risks, then maps metrics on those risks."""
        impact = self._call_tool(
            change.change_id,
            "rmte.read",
            lambda: self.rmte.impact_for(change.classification.trigger_code),
        )
        risks = self.rmte.risks()
        metrics = self.rmte.metrics()
        default_risk_ids = [risk_id for risk_id in impact["risk_ids"] if risk_id in risks]
        allowed_metric_ids = list(impact.get("metric_ids") or [])
        risk_ids, by_risk, narrative = self._call_tool(
            change.change_id,
            "llm.map",
            lambda: self.explainer.map_risks_and_metrics(
                change, risks, metrics, default_risk_ids, allowed_metric_ids
            ),
        )
        if allowed_metric_ids:
            allowed = set(allowed_metric_ids)
            by_risk = {
                risk_id: [metric_id for metric_id in ids if metric_id in allowed]
                for risk_id, ids in by_risk.items()
            }
            by_risk = {risk_id: ids for risk_id, ids in by_risk.items() if ids}
            risk_ids = [risk_id for risk_id in risk_ids if risk_id in by_risk]
        mapped_metric_ids: list[str] = []
        seen: set[str] = set()
        for ids in by_risk.values():
            for metric_id in ids:
                if metric_id not in seen and metric_id in metrics:
                    mapped_metric_ids.append(metric_id)
                    seen.add(metric_id)
        change.affected_metric_ids = mapped_metric_ids
        change.selected_test_ids = []
        change.affected_risks = [
            AffectedRisk(
                risk_id=risk_id,
                name=risks[risk_id]["name"],
                category=risks[risk_id]["category"],
                metric_ids=by_risk.get(risk_id, []),
            )
            for risk_id in risk_ids
            if risk_id in risks
        ]
        change.narrative = narrative
        if not change.affected_risks or not change.affected_metric_ids:
            change.status = ChangeStatus.FAILED
            change.recommendations = ["Unknown RMTE impact. Human review required."]
            self.repo.save_change(change)
            return change
        self.repo.save_impact_set(
            change.change_id, [item.model_dump(mode="json") for item in change.affected_risks]
        )
        change.status = ChangeStatus.IMPACT_READY
        change.updated_at = self.clock.now()
        self._ledger(
            change.change_id,
            "2.agent",
            "risks_and_metrics_mapped",
            {"risk_ids": risk_ids, "metric_ids": mapped_metric_ids},
        )
        self.repo.save_change(change)
        return change

    def _stale_mapped_metrics(
        self, change: ChangeRecord, state: AssuranceState
    ) -> ChangeRecord:
        """Pipeline: mark the agent-mapped metrics stale. Not agent work."""
        for metric_id in change.affected_metric_ids:
            state.metric_freshness[metric_id] = Freshness.STALE
        state.updated_at = self.clock.now()
        self.repo.save_state(state.model_dump(mode="json"))
        change.updated_at = self.clock.now()
        self._ledger(
            change.change_id,
            "3.stale",
            "stale_persisted",
            {"metric_ids": change.affected_metric_ids},
        )
        self._publish(
            change.change_id,
            "decay.metrics_marked_stale",
            {"change_ref": f"change://{change.change_id}"},
        )
        self.repo.save_change(change)
        return change

    def _tests_for_stale_metrics(self, change: ChangeRecord) -> list[str]:
        """Pipeline: choose G2-approved tests from the already-staled metrics."""
        metrics = self.rmte.metrics()
        tests = self.rmte.tests()
        selected: list[str] = []
        seen: set[str] = set()
        for metric_id in change.affected_metric_ids:
            for test_id in metrics[metric_id].approved_test_ids:
                if test_id in seen:
                    continue
                if tests[test_id].g2_approved:
                    selected.append(test_id)
                    seen.add(test_id)
        return selected

    def start_revalidation(self, change_id: str) -> ChangeRecord:
        change = self.repo.get_change(change_id)
        if not change:
            raise KeyError(change_id)
        if change.status in {ChangeStatus.KILLED, ChangeStatus.FAILED} and not self.kill_switch:
            change.status = (
                ChangeStatus.IMPACT_READY if change.affected_risks else ChangeStatus.DETECTED
            )
            change.recommendations = []
            self._ledger(change.change_id, "3.plan", "retry_accepted")
        if change.status == ChangeStatus.DETECTED:
            state = self.initial_state(change.event.product_id)
            change = self._agent_map_risks_and_metrics(change)
            if change.status == ChangeStatus.IMPACT_READY:
                change = self._stale_mapped_metrics(change, state)
        if change.status != ChangeStatus.IMPACT_READY:
            return change
        if self.kill_switch:
            change.status = ChangeStatus.KILLED
            self.repo.save_change(change)
            return change

        tests = self.rmte.tests()
        change.selected_test_ids = self._tests_for_stale_metrics(change)
        if not change.selected_test_ids:
            change.status = ChangeStatus.FAILED
            change.recommendations = ["No approved tests for the staled metrics."]
            self.repo.save_change(change)
            return change
        change.estimated_cost = (
            change.event.estimated_cost
            if change.event.estimated_cost is not None
            else sum(tests[test_id].estimated_cost for test_id in change.selected_test_ids)
        )
        change.estimated_duration_seconds = sum(
            tests[test_id].estimated_duration_seconds for test_id in change.selected_test_ids
        )
        self._ledger(
            change.change_id,
            "4.run",
            "run_from_stale_metrics",
            {
                "stale_metric_ids": change.affected_metric_ids,
                "test_ids": change.selected_test_ids,
            },
        )
        self._publish(
            change.change_id,
            "revalidation.planned",
            {"change_ref": f"change://{change.change_id}"},
        )
        if change.estimated_cost > self.policy.budget_limit:
            change.status = ChangeStatus.FAILED
            change.recommendations = ["Approve more budget before running."]
            self._create_notification(change, "budget_exceeded")
            self.repo.save_change(change)
            return change

        change.status = ChangeStatus.RUNNING
        change.pinned_context = self._call_tool(
            change.change_id,
            "dataset.pin",
            lambda: self.pinner.pin(change.event, change.selected_test_ids),
        )
        self._publish(
            change.change_id,
            "revalidation.started",
            {"change_ref": f"change://{change.change_id}"},
        )
        pinned = change.pinned_context
        assert pinned is not None
        change.eval_run = self._call_tool(
            change.change_id,
            "evaluation.start_delta_run",
            lambda: self.evaluation.start_delta_run(
                change.change_id, change.selected_test_ids, pinned, change.event.simulation
            ),
        )
        if any(
            item.outcome in {EvaluationOutcome.INFRA_FAIL, EvaluationOutcome.OVER_BUDGET}
            for item in change.eval_run.results
        ):
            change.status = ChangeStatus.FAILED
            change.posture_after = Posture.HOLD
            change.recommendations = ["Retry after evaluation infrastructure recovers."]
            self._create_notification(change, "execution_unavailable")
            self._publish(
                change.change_id,
                "revalidation.failed",
                {"change_ref": f"change://{change.change_id}"},
            )
            self.repo.save_change(change)
            return change

        metrics = self.rmte.metrics()
        records = [
            EvidenceRecord(
                evidence_id=self.ids.new("evidence"),
                job_id=change.change_id,
                metric_id=result.metric_id,
                test_id=result.test_id,
                outcome=result.outcome,
                value=result.value,
                run_reference=result.run_reference,
                created_at=self.clock.now(),
            )
            for result in change.eval_run.results
        ]
        pack = self._call_tool(
            change.change_id,
            "evidence.write",
            lambda: self.evidence.write(change.change_id, records),
        )
        pack.created_at = self.clock.now()
        pack.product_id = change.event.product_id
        pack.change_id = change.change_id
        pack.release = change.event.resource_version
        change.evidence_pack = pack
        self.repo.save_pack(pack)
        change.comparisons = []
        for result in change.eval_run.results:
            if result.value is None:
                continue
            metric = metrics[result.metric_id]
            previous = change.previous_scores.get(result.metric_id)
            change.comparisons.append(
                ThresholdComparison(
                    metric_id=result.metric_id,
                    metric_name=metric.name,
                    previous=previous,
                    actual=result.value,
                    threshold=metric.threshold.value,
                    comparison=metric.threshold.comparison,
                    passed=metric.threshold.passes(result.value),
                    delta=None if previous is None else round(result.value - previous, 4),
                )
            )
        failed = {item.metric_id for item in change.comparisons if not item.passed}
        evidenced = {item.rmte.metric_id for item in pack.artefacts if item.rmte.metric_id}
        pack.gaps = []
        for metric_id in change.affected_metric_ids:
            if metric_id not in evidenced:
                pack.gaps.append(
                    {
                        "kind": "missing_artefact",
                        "rmte": {"metric_id": metric_id},
                        "reason": "No WORM pointer for this mapped metric.",
                    }
                )
            elif metric_id in failed:
                pack.gaps.append(
                    {
                        "kind": "quality_fail",
                        "rmte": {"metric_id": metric_id},
                        "reason": "Evaluation file is indexed; threshold was not met.",
                    }
                )
        pack.coverage = {
            "artefacts": len(pack.artefacts),
            "metrics": len(change.affected_metric_ids),
            "risks": len(change.affected_risks),
            "gaps": len(pack.gaps),
            "complete": not pack.gaps,
        }
        pack.sha256 = pack_join_sha256(pack)
        self.repo.save_pack(pack)
        # Q6 Trust Factor — Algorithm 1. Q6 IS the trust factor.
        _metric_defs = self.rmte.metrics()
        _sev = change.classification.severity
        _comparisons = list(change.comparisons)
        _pack_ref = pack.sha256 or pack.pack_id
        change.trust_factor = self._call_tool(
            change.change_id,
            "q6.score",
            lambda: self.scorer.score(_pack_ref, _comparisons, _sev, _metric_defs),
        )
        self._ledger(
            change.change_id,
            "4.quality",
            "q6_trust_factor_computed",
            {
                "tfa": change.trust_factor.score,
                "vnorm": change.trust_factor.vnorm,
                "vcoer": change.trust_factor.vcoer,
                "vmim": change.trust_factor.vmim,
                "ssum": change.trust_factor.ssum,
                "weighted_avg_violation": change.trust_factor.weighted_avg_violation,
                "reference": change.trust_factor.reference,
            },
        )
        pack.q6_label = f"TFa {change.trust_factor.score:.4f}"
        pack.sha256 = pack_join_sha256(pack)
        self.repo.save_pack(pack)
        all_pass = bool(change.comparisons) and all(item.passed for item in change.comparisons)
        change.posture_after = Posture.GO if all_pass else Posture.RISK

        if not all_pass:
            # Identify which metrics failed so the reviewer has clear focus.
            failed_metric_ids = [
                item.metric_id for item in change.comparisons if not item.passed
            ]
            change.recommendations = [
                f"One or more metrics failed threshold: {', '.join(failed_metric_ids)}."
                " Review scorecard before approving.",
                "Human approval is required before assurance state can change.",
                "Review prior vs current metric scores and the evidence pack delta.",
                "If rejected, use the resubmit endpoint to re-escalate to RMTE for a fresh run.",
            ]
            # Explicit audit trail entry: revalidation did not pass quality gates.
            self._ledger(
                change.change_id,
                "4.quality",
                "revalidation.not_passed",
                {
                    "failed_metric_ids": failed_metric_ids,
                    "posture_after": Posture.RISK.value,
                },
            )
            self._publish(
                change.change_id,
                "revalidation.quality_fail",
                {
                    "change_ref": f"change://{change.change_id}",
                    "failed_metric_ids": failed_metric_ids,
                    "posture_after": Posture.RISK.value,
                },
            )
        else:
            change.recommendations = [
                "Human approval is required before assurance state can change.",
                "Review prior vs current metric scores and the evidence pack delta.",
            ]

        change.status = ChangeStatus.AWAITING_APPROVAL
        change.updated_at = self.clock.now()
        self._create_notification(change, "human_approval_required")
        self._publish(
            change.change_id,
            "revalidation.completed",
            {"change_ref": f"change://{change.change_id}", "pack_ref": pack.pack_id},
        )
        self.repo.save_change(change)
        return change

    def decide(
        self, change_id: str, decision: Decision, reviewer: str, notes: str = ""
    ) -> ChangeRecord:
        change = self.repo.get_change(change_id)
        if not change:
            raise KeyError(change_id)
        if change.status != ChangeStatus.AWAITING_APPROVAL:
            return change
        change.approval = ApprovalRecord(
            decision=decision,
            reviewer=reviewer,
            notes=notes,
            decided_at=self.clock.now(),
        )
        state = self.initial_state(change.event.product_id)
        if decision == Decision.APPROVE:
            change.status = ChangeStatus.APPROVED
            state.posture = change.posture_after or Posture.CONDITIONAL
            if change.evidence_pack:
                change.evidence_pack.status = "approved"
                self.repo.save_pack(change.evidence_pack)
                state.last_pack_hash = change.evidence_pack.sha256
                state.last_pack_id = change.evidence_pack.pack_id
            if change.trust_factor:
                state.last_score_ref = change.trust_factor.reference
            if state.posture == Posture.GO:
                state.active_version = change.event.resource_version
                for metric_id in change.affected_metric_ids:
                    state.metric_freshness[metric_id] = Freshness.FRESH
        else:
            change.status = ChangeStatus.REJECTED
            change.posture_after = Posture.HOLD
            state.posture = Posture.HOLD
            # Re-mark affected metrics as stale so a fresh revalidation is required.
            failed_metric_ids = [
                item.metric_id for item in change.comparisons if not item.passed
            ]
            for metric_id in (failed_metric_ids or change.affected_metric_ids):
                state.metric_freshness[metric_id] = Freshness.STALE
            change.recommendations = [
                "Revalidation rejected due to quality failure. Investigate and re-run.",
                "Use the resubmit endpoint to re-escalate this change to RMTE for a fresh evaluation.",
            ]
            self._publish(
                change.change_id,
                "revalidation.rejected_risk",
                {
                    "change_ref": f"change://{change.change_id}",
                    "re_staled_metric_ids": failed_metric_ids or change.affected_metric_ids,
                    "posture": Posture.HOLD.value,
                },
            )
            # Raise a dedicated blocked notification so the escalation is traceable.
            self._create_notification(change, "revalidation_blocked")
        state.updated_at = self.clock.now()
        change.updated_at = self.clock.now()
        self.repo.save_state(state.model_dump(mode="json"))
        self.repo.save_change(change)
        self._publish(
            change.change_id,
            "assurance.posture_updated",
            {"change_ref": f"change://{change.change_id}", "decision": decision.value},
        )
        return change

    def resubmit(self, change_id: str) -> ChangeRecord:
        """Re-escalate a REJECTED or FAILED change back through RMTE impact analysis.

        This provides an explicit path after a revalidation-not-passed scenario:
        a reviewer rejects the evidence pack, the change is set to REJECTED/HOLD,
        and the team can call this method to send the change back to RMTE for a
        completely fresh impact analysis and revalidation run.

        Steps:
        1. Validate the change exists and is in a re-runnable status.
        2. Clear prior evaluation artefacts (eval_run, evidence_pack, comparisons,
           score, posture_after) so the new run is independent.
        3. Reset status to DETECTED so _analyze_impact() will re-query RMTE.
        4. Emit a revalidation.resubmitted event for audit purposes.
        5. Delegate to start_revalidation() to re-run the full pipeline.
        """
        change = self.repo.get_change(change_id)
        if not change:
            raise KeyError(change_id)

        resubmittable = {
            ChangeStatus.REJECTED,
            ChangeStatus.FAILED,
        }
        if change.status not in resubmittable:
            # Return the change unchanged if it is not in a re-runnable state.
            return change

        if self.kill_switch:
            change.status = ChangeStatus.KILLED
            self.repo.save_change(change)
            return change

        # Clear prior run artefacts so the new run is fully independent.
        change.eval_run = None
        change.evidence_pack = None
        change.comparisons = []
        change.trust_factor = None
        change.posture_after = None
        change.pinned_context = None
        change.affected_risks = []
        change.affected_metric_ids = []
        change.selected_test_ids = []
        change.recommendations = []
        change.status = ChangeStatus.DETECTED
        change.updated_at = self.clock.now()

        self._ledger(
            change.change_id,
            "5.resubmit",
            "resubmitted_to_rmte",
            {"previous_status": ChangeStatus.REJECTED.value},
        )
        self._publish(
            change.change_id,
            "revalidation.resubmitted",
            {
                "change_ref": f"change://{change.change_id}",
                "reason": "Manually resubmitted after rejection for a fresh RMTE run.",
            },
        )
        self.repo.save_change(change)

        # Re-run the full impact + revalidation pipeline.
        return self.start_revalidation(change.change_id)

    def dashboard(self, product_id: str = "rag-demo") -> DashboardSummary:
        state = self.initial_state(product_id)
        changes = self.repo.list_changes()
        return DashboardSummary(
            product_id=product_id,
            posture=state.posture,
            completed_revalidations=len(
                [
                    item
                    for item in changes
                    if item.eval_run is not None
                    and item.status
                    not in {ChangeStatus.RUNNING, ChangeStatus.FAILED}
                ]
            ),
            pending_changes=len(
                [
                    item
                    for item in changes
                    if item.status
                    in {ChangeStatus.DETECTED, ChangeStatus.IMPACT_READY}
                ]
            ),
            awaiting_approval=len(
                [item for item in changes if item.status == ChangeStatus.AWAITING_APPROVAL]
            ),
            evidence_packs=len(self.repo.list_packs()),
            notifications=len(self.repo.list_tasks()),
            stale_metrics=[
                metric_id
                for metric_id, freshness in state.metric_freshness.items()
                if freshness == Freshness.STALE
            ],
            last_pack_id=state.last_pack_id,
        )

    def _create_notification(self, change: ChangeRecord, kind: str) -> None:
        task = TaskRecord(
            task_id=self.ids.new("task"),
            job_id=change.change_id,
            kind=kind,
            references={"change_ref": f"change://{change.change_id}"},
            created_at=self.clock.now(),
        )
        reference = self._call_tool(
            change.change_id, "ticket.create", lambda: self.notifier.create(task)
        )
        change.task_reference = reference
        self._publish(
            change.change_id,
            "task.created",
            {"change_ref": f"change://{change.change_id}", "task_ref": reference},
        )
        alert = TaskRecord(
            task_id=self.ids.new("alert"),
            job_id=change.change_id,
            kind=kind,
            channel="alert",
            references={"change_ref": f"change://{change.change_id}"},
            created_at=self.clock.now(),
        )
        alert_ref = self._call_tool(
            change.change_id, "notification.create", lambda: self.notifier.create(alert)
        )
        self._publish(
            change.change_id,
            "alert.raised",
            {"change_ref": f"change://{change.change_id}", "alert_ref": alert_ref},
        )
