"""Build Agent-5-shaped libraries from a parsed TLAI workbook.

Each archetype gets the same twelve governed files as libraries/agent/v1:
metrics, risks, risk_metric_map, formula_inputs, logs_layers, metric_pattern_matrix,
category_rules, scenario_templates, case_variant_slots, test_case_schema,
test_case_templates, llm_options.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from rmte_spine.config import PACKAGE_ROOT
from rmte_spine.parse import ParsedLibrary, parse_workbook, discover_workbooks

LIBRARIES_ROOT = PACKAGE_ROOT / "libraries"
AGENT5_LIBS = PACKAGE_ROOT.parent / "agent5_rag" / "agent5_rag" / "libraries"
GOLD_FOLDERS = {
    "agent": AGENT5_LIBS / "agent" / "v1",
    "rag": AGENT5_LIBS / "v1",
}

CATEGORY_VOCABULARY = [
    "positive",
    "negative",
    "edge",
    "adversarial",
    "security",
    "monitoring",
    "regression",
    "robustness",
    "stress",
    "recovery",
    "drift",
]

_STAGE_HINTS = (
    ("retriev", "retrieval"),
    ("chunk", "corpus"),
    ("corpus", "corpus"),
    ("ingest", "corpus"),
    ("generat", "answer"),
    ("citation", "answer"),
    ("answer", "answer"),
    ("tool", "tool"),
    ("plan", "plan"),
    ("authoriz", "authorization"),
    ("approv", "authorization"),
    ("permission", "authorization"),
    ("goal", "goal"),
    ("monitor", "operations"),
    ("lifecycle", "assurance"),
    ("drift", "assurance"),
    ("guardrail", "answer"),
    ("safety", "answer"),
    ("forecast", "answer"),
    ("inference", "answer"),
    ("ranking", "retrieval"),
    ("context", "context"),
)


def _slug_stage(value: str) -> str:
    raw = (value or "").lower()
    for needle, stage in _STAGE_HINTS:
        if needle in raw:
            return stage
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug[:48] or "runtime"


def _family_kind(metrics: list[dict], family: str) -> str:
    blob = " ".join(
        f"{m.get('category', '')} {m.get('test_type', '')} {m.get('pillar', '')} {m.get('name', '')}"
        for m in metrics
        if m["family"] == family
    ).lower()
    if any(w in blob for w in ("adversarial", "jailbreak", "poison", "attack", "prompt injection")):
        return "rs"
    if any(w in blob for w in ("authoriz", "permission", "policy enforcement", "privilege")):
        return "ap"
    if any(
        w in blob
        for w in ("lifecycle", "drift", "monitoring", "ops ", "operation", "efficiency", "cost &", "uptime")
    ):
        return "ops"
    return "default"


def _required_categories(kind: str) -> list[str]:
    if kind == "rs":
        return ["adversarial", "security", "negative"]
    if kind == "ap":
        return ["positive", "negative", "security"]
    if kind == "ops":
        return ["monitoring", "regression"]
    return ["positive", "negative", "edge"]


def _observation_stages(parsed: ParsedLibrary) -> dict[str, list[str]]:
    category_to_family = {}
    for metric in parsed.metrics:
        category_to_family.setdefault(metric["category"].lower(), metric["family"])
        category_to_family.setdefault(metric["family"].lower(), metric["family"])
    by_family: dict[str, list[str]] = {}
    for layer in parsed.layers:
        stage = _slug_stage(layer.get("layer") or "")
        for label in layer.get("related_metric_families") or []:
            fam = category_to_family.get(label.lower())
            if not fam:
                continue
            stages = by_family.setdefault(fam, [])
            if stage not in stages:
                stages.append(stage)
    for metric in parsed.metrics:
        fam = metric["family"]
        stage = _slug_stage(metric.get("ai_product_layer") or "")
        stages = by_family.setdefault(fam, [])
        if stage and stage not in stages:
            stages.append(stage)
    return by_family


def _enrich_metrics(parsed: ParsedLibrary) -> list[dict]:
    stages = _observation_stages(parsed)
    out = []
    for metric in parsed.metrics:
        item = dict(metric)
        family = item["family"]
        kind = _family_kind(parsed.metrics, family)
        eval_method = (item.get("evaluation_method") or "").lower()
        item["design_mode"] = "template" if "deterministic" in eval_method else "llm"
        item["evidence_artefact"] = f"{(item.get('category') or family).lower().replace(' ', '-')}-evaluation"
        item["requires_separate_adversarial_approval"] = kind == "rs"
        item["observation_stages"] = stages.get(family, ["runtime"])
        out.append(item)
    return out


def _load_gold_schema() -> dict:
    path = GOLD_FOLDERS["agent"] / "test_case_schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_gold_templates() -> dict:
    path = GOLD_FOLDERS["agent"] / "test_case_templates.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_gold_llm() -> dict:
    path = GOLD_FOLDERS["agent"] / "llm_options.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_library_docs(parsed: ParsedLibrary) -> dict[str, dict[str, Any]]:
    metrics = _enrich_metrics(parsed)
    families = sorted({m["family"] for m in metrics})
    stages = _observation_stages(parsed)
    display = parsed.display_name
    aid = parsed.archetype_id

    metrics_doc = {
        "library_id": parsed.library_id,
        "version": parsed.version,
        "source": parsed.source,
        "archetype": display,
        "execution_patterns": parsed.execution_patterns,
        "notes": {
            **parsed.notes,
            "threshold_binding": "Always bind metric_id + execution_pattern + threshold; never a generic-only threshold.",
            "observation_stage": (
                "Derived from TLAI Logs ↔ Architecture Layers related families and AI Product Layer. "
                "A convenience for Agent 5, not an HLA fact. The designer still chooses capture stage."
            ),
            "compliance": "TrustLensAI produces evidence; it does not certify compliance.",
        },
        "family_observation_stage": stages,
        "metric_observation_stage_overrides": {},
        "family_product_layer": parsed.family_product_layer,
        "metrics": metrics,
    }

    risks_doc = {
        "library_id": f"{parsed.library_id}-risks",
        "version": parsed.version,
        "source": parsed.source,
        "reconstruction": True,
        "notes": {"compliance": "TrustLensAI produces evidence; it does not certify compliance."},
        "risks": parsed.risks,
    }

    risk_map = {
        "library_id": f"{parsed.library_id}-risk-metric-map",
        "version": parsed.version,
        "source": parsed.source,
        "edges": [
            {
                "risk_id": rid,
                "metric_ids": sorted({mid for r, mid in parsed.edges if r == rid}),
            }
            for rid in sorted({r for r, _mid in parsed.edges})
        ],
        "derived_perspectives": parsed.derived_perspectives,
        "rmte_links": [
            {
                "metric_id": row["metric_id"],
                "risk_id": row.get("risk_id"),
                "trust_attribute": row.get("trust_attribute"),
                "pillar": row.get("pillar"),
                "risk_category": row.get("risk_category"),
                "risk": row.get("risk"),
                "test_suite_id": row.get("test_suite_id"),
                "severity": row.get("severity"),
                "priority": row.get("priority"),
            }
            for row in parsed.rmte_rows
        ],
    }

    formula_doc = {
        "library_id": f"{parsed.library_id}-formula-inputs",
        "version": parsed.version,
        "source": parsed.source,
        "notes": (
            f"For every {display} metric, each variable its formula consumes is traced to the "
            "log / reference dataset / config that supplies it, and the layer that emits it."
        ),
        "inputs": parsed.formula_inputs,
    }

    layers_doc = {
        "library_id": f"{parsed.library_id}-logs-layers",
        "version": parsed.version,
        "source": parsed.source,
        "notes": (
            f"Each {display} pipeline stage emits telemetry; TrustLensAI platform layers ingest and evaluate it. "
            "TrustLensAI produces evidence; it does not certify compliance."
        ),
        "layers": parsed.layers,
    }

    pattern_doc = {
        "library_id": f"{parsed.library_id}-pattern-matrix",
        "version": parsed.version,
        "source": parsed.source,
        "patterns": parsed.execution_patterns,
        "family_defaults": parsed.family_defaults,
        "metric_overrides": parsed.metric_overrides,
    }

    required = {fam: _required_categories(_family_kind(metrics, fam)) for fam in families}
    mvp_extra = {
        m["metric_id"]: ["adversarial"]
        for m in metrics
        if m.get("requires_separate_adversarial_approval")
    }
    category_rules = {
        "library_id": f"agent5-{aid}-category-rules",
        "version": "v1",
        "source": f"SAD A7 scenario dimensions + TrustLensAI {display} RMTE test-suite routing",
        "note": (
            "required_categories_by_family is a floor, not the answer. Agent 5 asks the designer which "
            "categories a metric needs; the floor is what the designer may not drop, and category_vocabulary "
            "bounds what it may add."
        ),
        "category_vocabulary": CATEGORY_VOCABULARY,
        "required_categories_by_family": required,
        "mvp_extra": mvp_extra,
    }

    templates = {}
    for fam in families:
        label = next((m["category"] for m in metrics if m["family"] == fam), fam)
        templates[fam] = {
            "positive": {
                "title": f"Healthy {label} measurement",
                "intent": (
                    f"Measure {{metric_name}} on a well-behaved {display} run and expect HLA threshold "
                    "{threshold_display}."
                ),
                "steps": [
                    f"Pin the {display} product registry and golden/reference set",
                    "Obtain the run (Observe traces, or the execution engine submits the prompt — Agent 5 does not invoke)",
                    "Compute {metric_id} with formula {formula}",
                    "Compare to threshold {threshold_display} for pattern {execution_pattern}",
                ],
                "dataset_kind": "golden",
            },
            "negative": {
                "title": f"Degraded {label}",
                "intent": "Use a fixture whose run should fail {metric_id} against {threshold_display}.",
                "steps": [
                    "Load the negative fixture for {metric_id}",
                    "Collect the designed observations",
                    "Score {metric_id} and expect it to violate {threshold_display}",
                ],
                "dataset_kind": "golden",
            },
            "edge": {
                "title": f"Boundary {label}",
                "intent": "Exercise {metric_id} at the HLA threshold boundary.",
                "steps": [
                    "Use a fixture that sits on the threshold for {metric_id}",
                    "Score {metric_id}",
                    "Record whether the value sits on {threshold_display}",
                ],
                "dataset_kind": "golden",
            },
            "adversarial": {
                "title": f"Adversarial probe of {label}",
                "intent": f"An adversarial input tries to break {{metric_id}} on a {display} product.",
                "steps": [
                    "Submit the adversarial fixture only after separate approval",
                    "Collect the designed observations",
                    "Score {metric_id}",
                ],
                "dataset_kind": "benchmark",
                "requires_separate_adversarial_approval": True,
            },
            "security": {
                "title": f"Out-of-scope {label}",
                "intent": "The product attempts an action outside the pinned policy or permission scope.",
                "steps": [
                    "Pin the policy / permission set",
                    "Collect authorization and runtime evidence",
                    "Score {metric_id} against {threshold_display}",
                ],
                "dataset_kind": "golden",
            },
            "monitoring": {
                "title": f"Replay {label} from Observe traces",
                "intent": "Score {metric_id} on existing traces without a new invocation.",
                "steps": [
                    "Join spans for the pinned product window",
                    "Compute {metric_id}",
                ],
                "dataset_kind": "production-replay",
            },
            "regression": {
                "title": f"Regression window for {label}",
                "intent": "Re-score {metric_id} on a frozen prior window and compare to the pinned threshold.",
                "steps": [
                    "Load the pinned regression trace window",
                    "Compute {metric_id}",
                    "Apply {threshold_display}",
                ],
                "dataset_kind": "production-replay",
            },
        }

    scenario_templates = {
        "library_id": f"agent5-{aid}-scenario-templates",
        "version": "v1",
        "source": f"SAD A7 scenario dimensions aligned to TrustLensAI {display} RMTE test suites",
        "templates": templates,
    }

    case_variant_slots = {
        "library_id": f"agent5-{aid}-case-variant-slots",
        "version": "v1",
        "note": "Each extra test case under a scenario must occupy a different slot. Code never synthesises clones.",
        "generic_extras": [
            {"slot_id": "canonical_happy_path", "focus": f"A canonical in-scope {display} task with a gold outcome."},
            {"slot_id": "sparse_or_missing_signal", "focus": "Required evidence is missing or the signal is empty."},
            {"slot_id": "threshold_boundary", "focus": "The score sits on the pinned HLA boundary."},
            {"slot_id": "policy_or_scope_violation", "focus": "The run crosses a pinned policy, permission, or safety boundary."},
        ],
        "by_family": {
            fam: {
                "positive": [
                    {"slot_id": "canonical_happy_path", "focus": f"A canonical in-scope task for family {fam}."},
                    {"slot_id": "typical_production_shape", "focus": f"A typical {display} production-shaped input for {fam}."},
                ],
                "negative": [
                    {"slot_id": "missing_required_signal", "focus": f"A required {fam} signal is omitted or corrupted."},
                    {"slot_id": "wrong_but_plausible", "focus": f"A plausible wrong {fam} outcome that should fail the metric."},
                ],
                "edge": [
                    {"slot_id": "threshold_boundary", "focus": "The score sits on the pinned HLA boundary."},
                ],
                "adversarial": [
                    {"slot_id": "adversarial_probe", "focus": f"An adversarial fixture tries to break family {fam}."},
                ],
                "security": [
                    {"slot_id": "scope_violation", "focus": f"The run exceeds pinned scope for family {fam}."},
                ],
                "monitoring": [
                    {"slot_id": "replay_window", "focus": "Score the pinned Observe window without a new invoke."},
                ],
                "regression": [
                    {"slot_id": "frozen_prior_run", "focus": "Re-score a frozen prior evaluation window."},
                ],
            }
            for fam in families
        },
    }

    schema = _load_gold_schema()
    schema["source"] = (
        f"Agent 5 14-field contract; formula terms and evidence observations bind to "
        f"TrustLensAI {display} RMTE (Master) and Logs ↔ Formula Inputs"
    )

    templates_doc = _load_gold_templates()
    templates_doc["source"] = (
        f"Agent 5 14-field skeleton; metric thresholds, pass/fail rules, and evidence from "
        f"TrustLensAI {display} RMTE (Master) / HLA A1"
    )
    templates_doc["skeleton"]["preconditions"] = [
        "Product registry pinned",
        f"{display} metric library v1 pinned",
        "Pattern {execution_pattern}",
    ]

    llm = _load_gold_llm()
    llm["families"] = families

    return {
        "metrics": metrics_doc,
        "risks": risks_doc,
        "risk_metric_map": risk_map,
        "formula_inputs": formula_doc,
        "logs_layers": layers_doc,
        "metric_pattern_matrix": pattern_doc,
        "category_rules": category_rules,
        "scenario_templates": scenario_templates,
        "case_variant_slots": case_variant_slots,
        "test_case_schema": schema,
        "test_case_templates": templates_doc,
        "llm_options": llm,
    }


def filenames_for(archetype_id: str) -> dict[str, str]:
    stem = "agent" if archetype_id == "agent" else "rag" if archetype_id == "rag" else archetype_id
    return {
        "metrics": f"{stem}_metrics.json",
        "risks": f"{stem}_risks.json",
        "risk_metric_map": "risk_metric_map.json",
        "formula_inputs": "formula_inputs.json",
        "logs_layers": "logs_layers.json",
        "metric_pattern_matrix": "metric_pattern_matrix.json",
        "category_rules": "category_rules.json",
        "scenario_templates": "scenario_templates.json",
        "case_variant_slots": "case_variant_slots.json",
        "test_case_schema": "test_case_schema.json",
        "test_case_templates": "test_case_templates.json",
        "llm_options": "llm_options.json",
    }


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_library(dest: Path, archetype_id: str, docs: dict[str, dict]) -> list[str]:
    names = filenames_for(archetype_id)
    written = []
    for kind, filename in names.items():
        dump_json(dest / filename, docs[kind])
        written.append(filename)
    return written


def export_libraries(root: Path | None = None) -> dict[str, Any]:
    """Write the twelve-file library for every TLAI archetype.

    Agent and RAG keep the existing hand-built Agent 5 libraries (the quality bar).
    Every other archetype is built from its TLAI workbook into the same file set.
    """
    from rmte_spine.config import tlai_root

    folder = root or tlai_root()
    index = []
    LIBRARIES_ROOT.mkdir(parents=True, exist_ok=True)

    for archetype_id, gold in GOLD_FOLDERS.items():
        dest = LIBRARIES_ROOT / archetype_id / "v1"
        dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in sorted(gold.glob("*.json")):
            shutil.copy2(src, dest / src.name)
            copied.append(src.name)
        metrics = json.loads((dest / filenames_for(archetype_id)["metrics"]).read_text(encoding="utf-8"))
        index.append(
            {
                "archetype": archetype_id,
                "source": "gold_agent5_library",
                "path": str(dest),
                "files": copied,
                "metrics": len(metrics.get("metrics") or []),
                "library_id": metrics.get("library_id"),
            }
        )

    for path in discover_workbooks(folder):
        parsed = parse_workbook(path)
        if parsed.archetype_id in GOLD_FOLDERS:
            continue
        dest = LIBRARIES_ROOT / parsed.archetype_id / "v1"
        docs = build_library_docs(parsed)
        written = write_library(dest, parsed.archetype_id, docs)
        index.append(
            {
                "archetype": parsed.archetype_id,
                "display_name": parsed.display_name,
                "source": parsed.workbook,
                "path": str(dest),
                "files": written,
                "metrics": len(parsed.metrics),
                "risks": len(parsed.risks),
                "rmte_rows": len(parsed.rmte_rows),
                "formula_inputs": len(parsed.formula_inputs),
                "layers": len(parsed.layers),
                "families": sorted({m["family"] for m in parsed.metrics}),
                "library_id": parsed.library_id,
            }
        )

    catalog = {
        "library_root": str(LIBRARIES_ROOT),
        "note": (
            "Each folder is an Agent-5-shaped RMTE library: metrics, risks, formula inputs, "
            "layers, pattern matrix, category rules, scenario templates, test-case contract."
        ),
        "archetypes": sorted(index, key=lambda row: row["archetype"]),
    }
    dump_json(LIBRARIES_ROOT / "INDEX.json", catalog)
    return catalog
