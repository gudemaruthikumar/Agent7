"""RMTE risks and metrics from the Postgres (or SQLite) spine catalog."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .adapters import SyntheticRMTE, load_fixture
from .domain import Comparison, MetricDefinition, TestDefinition, Threshold, TriggerCode

_SPINE_ROOT = Path(__file__).resolve().parents[1] / "rmte-spine"
if _SPINE_ROOT.is_dir() and str(_SPINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SPINE_ROOT))

_PRODUCT_ARCHETYPE = {
    "rag-demo": "rag",
    "agentic-summariser": "agent",
    "multimodal-classifier": "multimodal",
    "askmd-rewards": "agent",
}

_ARCHETYPE_FROM_LABEL = {
    "naive rag": "rag",
    "tool-calling agent": "agent",
    "multi-modal classifier": "multimodal",
}

_PROMPT_FAMILIES = frozenset({"GH", "AQ", "AE", "RS", "AP", "AG"})
_CORPUS_FAMILIES = frozenset({"CQ", "RE", "CS", "LC", "EO"})

_DEFAULT_POSTGRES = (
    "postgresql+psycopg://trustlens:trustlens@127.0.0.1:5432/rmte_spine?connect_timeout=3"
)


def spine_disabled() -> bool:
    return os.environ.get("RMTE_SPINE_DISABLE", "").strip().lower() in {"1", "true", "yes"}


def spine_database_url() -> str:
    url = (os.environ.get("RMTE_DATABASE_URL") or _DEFAULT_POSTGRES).strip()
    if url.startswith("postgresql") and "connect_timeout" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}connect_timeout=3"
    return url


def threshold_from_spine(raw: dict[str, Any] | None) -> Threshold:
    data = raw or {}
    op = str(data.get("operator") or "gte").lower()
    if op == "band" and data.get("min") is not None and data.get("max") is not None:
        return Threshold(
            value=float(data["min"]),
            comparison=Comparison.GTE,
            band_min=float(data["min"]),
            band_max=float(data["max"]),
        )
    value = data.get("value", data.get("min", data.get("max", 0.85)))
    comparison = {
        "gte": Comparison.GTE,
        "gte_tiered": Comparison.GTE,
        "gt": Comparison.GT,
        "lte": Comparison.LTE,
        "lt": Comparison.LT,
        "lt_tiered": Comparison.LT,
        "low": Comparison.LTE,
    }.get(op, Comparison.GTE)
    return Threshold(value=float(value), comparison=comparison)


def _simulated_value(metric: MetricDefinition, passing: bool) -> float:
    if metric.threshold.band_min is not None and metric.threshold.band_max is not None:
        mid = (metric.threshold.band_min + metric.threshold.band_max) / 2
        return round(mid if passing else metric.threshold.band_max + 0.15, 4)
    value = metric.threshold.value
    if metric.threshold.comparison in {Comparison.GTE, Comparison.GT}:
        return round(value + 0.05 if passing else max(0.0, value - 0.2), 4)
    return round(max(0.0, value - 0.02) if passing else value + 0.15, 4)


class SpineRMTE:
    """Product fixture for registry/eval context; spine DB for risks and metrics."""

    def __init__(
        self,
        product_id: str = "rag-demo",
        database_url: str | None = None,
        fixture: dict[str, Any] | None = None,
    ) -> None:
        self.product_id = product_id
        self.database_url = database_url or spine_database_url()
        self.fixture = fixture or load_fixture(product_id=product_id)
        self.archetype = self._resolve_archetype()
        self._metrics: dict[str, MetricDefinition] = {}
        self._tests: dict[str, TestDefinition] = {}
        self._risks: dict[str, dict[str, Any]] = {}
        self._families: dict[str, str] = {}
        self.source = "unset"
        self._load()

    def _resolve_archetype(self) -> str:
        override = (os.environ.get("RMTE_ARCHETYPE") or "").strip()
        if override:
            return override
        product = self.fixture.get("product") or {}
        if product_id := product.get("id"):
            mapped = _PRODUCT_ARCHETYPE.get(str(product_id))
            if mapped:
                return mapped
        label = str(product.get("archetype") or "").strip().lower()
        return _ARCHETYPE_FROM_LABEL.get(label, "rag")

    def _load(self) -> None:
        try:
            self._load_from_db()
            self.source = "postgres"
        except Exception:
            self._load_from_library_files()
            self.source = "library_files"

    def _load_from_db(self) -> None:
        from rmte_spine.db import session_scope
        from rmte_spine.models import Metric, Risk, RiskMetricLink
        from rmte_spine.query import get_release

        with session_scope(self.database_url) as session:
            release = get_release(session, self.archetype)
            metrics = session.scalars(select(Metric).where(Metric.release_id == release.id)).all()
            risks = session.scalars(select(Risk).where(Risk.release_id == release.id)).all()
            links = session.scalars(
                select(RiskMetricLink).where(RiskMetricLink.release_id == release.id)
            ).all()
            version = release.version
            content_hash = release.content_hash

        metric_risks: dict[str, list[str]] = {row.metric_id: [] for row in metrics}
        for link in links:
            metric_risks.setdefault(link.metric_id, []).append(link.risk_id)
        payloads = []
        for row in metrics:
            body = dict(row.body or {})
            body.setdefault("metric_id", row.metric_id)
            body.setdefault("name", row.name)
            body.setdefault("category", row.category)
            body.setdefault("family", row.family)
            body.setdefault("test_suite_id", row.test_suite_id)
            body["risk_ids"] = metric_risks.get(row.metric_id) or list(body.get("risk_ids") or [])
            payloads.append(body)
        risk_payloads = [
            {
                "risk_id": row.risk_id,
                "name": row.name,
                "category": row.category or "Technical",
                "trust_attributes": [row.trust_attribute] if row.trust_attribute else [],
            }
            for row in risks
        ]
        self._apply_catalog(payloads, risk_payloads, version, content_hash)

    def _load_from_library_files(self) -> None:
        folder = _SPINE_ROOT / "libraries" / self.archetype / "v1"
        metrics_path = next(folder.glob("*_metrics.json"))
        risks_path = next(folder.glob("*_risks.json"))
        map_path = folder / "risk_metric_map.json"
        metrics_doc = json.loads(metrics_path.read_text(encoding="utf-8"))
        risks_doc = json.loads(risks_path.read_text(encoding="utf-8"))
        edges = json.loads(map_path.read_text(encoding="utf-8")).get("edges") or []
        by_metric: dict[str, list[str]] = {}
        for edge in edges:
            for metric_id in edge.get("metric_ids") or []:
                by_metric.setdefault(metric_id, []).append(edge["risk_id"])
        payloads = []
        for body in metrics_doc.get("metrics") or []:
            item = dict(body)
            item["risk_ids"] = by_metric.get(item["metric_id"]) or list(item.get("risk_ids") or [])
            payloads.append(item)
        risk_payloads = [
            {
                "risk_id": row["risk_id"],
                "name": row.get("name") or row["risk_id"],
                "category": row.get("category") or "Technical",
                "trust_attributes": row.get("trust_attributes") or [],
            }
            for row in risks_doc.get("risks") or []
        ]
        digest = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
        self._apply_catalog(payloads, risk_payloads, str(metrics_doc.get("version") or "v1"), digest)

    def _apply_catalog(
        self,
        metrics: list[dict[str, Any]],
        risks: list[dict[str, Any]],
        version: str,
        content_hash: str,
    ) -> None:
        suite_metrics: dict[str, list[str]] = {}
        for body in metrics:
            metric_id = str(body["metric_id"])
            suite_id = str(body.get("test_suite_id") or f"TS-{metric_id}")
            suite_metrics.setdefault(suite_id, []).append(metric_id)
            self._families[metric_id] = str(body.get("family") or "")
            self._metrics[metric_id] = MetricDefinition(
                id=metric_id,
                name=str(body.get("name") or metric_id),
                category=str(body.get("category") or body.get("family") or "RMTE"),
                threshold=threshold_from_spine(body.get("threshold")),
                approved_test_ids=[suite_id],
                risk_ids=list(body.get("risk_ids") or []),
            )
        for suite_id, metric_ids in suite_metrics.items():
            digest = hashlib.sha256(f"{content_hash}|{suite_id}".encode()).hexdigest()
            self._tests[suite_id] = TestDefinition(
                id=suite_id,
                name=suite_id,
                metric_ids=metric_ids,
                version=version,
                hash=f"sha256:{digest[:16]}",
                g2_approved=True,
                estimated_cost=min(2.0, 0.2 * len(metric_ids)),
                estimated_duration_seconds=min(90.0, 8.0 * len(metric_ids)),
            )
        self._risks = {
            row["risk_id"]: {
                "name": row["name"],
                "category": row.get("category") or "Technical",
                "trust_attributes": row.get("trust_attributes") or [],
            }
            for row in risks
        }
        self.fixture = {
            **self.fixture,
            "risks": self._risks,
            "metrics": {
                metric_id: {
                    "name": metric.name,
                    "category": metric.category,
                    "threshold": {
                        "value": metric.threshold.value,
                        "comparison": metric.threshold.comparison.value,
                    },
                    "approved_test_ids": metric.approved_test_ids,
                    "risk_ids": metric.risk_ids,
                }
                for metric_id, metric in self._metrics.items()
            },
            "tests": {
                test_id: {
                    key: value
                    for key, value in test.model_dump(mode="json").items()
                    if key != "id"
                }
                for test_id, test in self._tests.items()
            },
            "baseline_scores": {
                metric_id: _simulated_value(metric, True)
                for metric_id, metric in self._metrics.items()
            },
            "evaluation": {
                "pass_values": {
                    metric_id: _simulated_value(metric, True)
                    for metric_id, metric in self._metrics.items()
                },
                "fail_values": {
                    metric_id: _simulated_value(metric, False)
                    for metric_id, metric in self._metrics.items()
                },
            },
            "impacts": self._impacts(),
        }

    def _impacts(self) -> dict[str, dict[str, Any]]:
        prompt_metrics = [
            metric_id
            for metric_id, family in self._families.items()
            if family in _PROMPT_FAMILIES
        ]
        corpus_metrics = [
            metric_id
            for metric_id, family in self._families.items()
            if family in _CORPUS_FAMILIES
        ]

        def risks_for(metric_ids: list[str]) -> list[str]:
            seen: set[str] = set()
            ordered: list[str] = []
            for metric_id in metric_ids:
                for risk_id in self._metrics[metric_id].risk_ids:
                    if risk_id == "ALL" or risk_id in seen or risk_id not in self._risks:
                        continue
                    ordered.append(risk_id)
                    seen.add(risk_id)
            return ordered

        prompt_risks = risks_for(prompt_metrics) or [rid for rid in self._risks if rid != "ALL"]
        corpus_risks = risks_for(corpus_metrics) or [rid for rid in self._risks if rid != "ALL"]
        prompt_tests = list(
            dict.fromkeys(
                test_id
                for metric_id in prompt_metrics
                for test_id in self._metrics[metric_id].approved_test_ids
            )
        )
        corpus_tests = list(
            dict.fromkeys(
                test_id
                for metric_id in corpus_metrics
                for test_id in self._metrics[metric_id].approved_test_ids
            )
        )
        return {
            TriggerCode.TD_06.value: {
                "risk_ids": prompt_risks,
                "metric_ids": prompt_metrics,
                "test_ids": prompt_tests,
            },
            TriggerCode.TD_11.value: {
                "risk_ids": corpus_risks,
                "metric_ids": corpus_metrics,
                "test_ids": corpus_tests,
            },
        }

    def impact_for(self, trigger: TriggerCode) -> dict[str, Any]:
        return dict(self.fixture["impacts"][trigger.value])

    def metrics(self) -> dict[str, MetricDefinition]:
        return self._metrics

    def tests(self) -> dict[str, TestDefinition]:
        return self._tests

    def risks(self) -> dict[str, dict[str, Any]]:
        return dict(self._risks)

    def product_context(self) -> dict[str, Any]:
        context = dict(self.fixture["product"])
        context["archetype"] = self.archetype
        return context

    def baseline_scores(self) -> dict[str, float]:
        return dict(self.fixture["baseline_scores"])

    def previous_pack(self):
        return SyntheticRMTE(fixture=self.fixture, product_id=self.product_id).previous_pack()

    def seed_changes(self) -> list[dict[str, Any]]:
        return list(self.fixture.get("seed_changes", []))

    def change_detail(self, event: Any) -> str:
        key = "prompt" if event.name.value == "prompt.changed" else "corpus"
        return str(self.fixture["registry"].get(key, {}).get("summary") or event.name.value)


def create_rmte(product_id: str = "rag-demo") -> SpineRMTE | SyntheticRMTE:
    if spine_disabled():
        return SyntheticRMTE(product_id=product_id)
    try:
        return SpineRMTE(product_id=product_id)
    except Exception:
        return SyntheticRMTE(product_id=product_id)
