"""Parse TLAI Logs ↔ Layers ↔ Formulas workbooks into the RMTE spine shape."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from rmte_spine.config import WORKBOOK_ARCHETYPES, normalize_archetype
from rmte_spine.hashutil import file_sha256

METRIC_ID_RE = re.compile(r"^[A-Z]{1,4}-\d{2,3}$")

TA_NORM = {
    "Accuracy": "Reliability",
    "Transparency": "Explainability",
    "Accountability": "Explainability",
    "Fairness": "Fairness & Policy Consistency",
    "Robustness": "Security/Safety/Reliability",
    "Explainability": "Explainability",
    "Security": "Security",
    "Human Control": "Human Oversight",
    "Human Oversight": "Human Oversight",
    "Privacy": "Privacy",
    "Reliability": "Reliability",
    "Safety": "Safety",
}


@dataclass
class ParsedLibrary:
    archetype_id: str
    display_name: str
    version: str
    library_id: str
    source: str
    workbook: str
    workbook_path: Path
    workbook_sha256: str
    execution_patterns: list[str]
    notes: dict[str, Any]
    metrics: list[dict]
    risks: list[dict]
    rmte_rows: list[dict]
    formula_inputs: list[dict]
    layers: list[dict]
    family_defaults: dict
    metric_overrides: dict
    derived_perspectives: list[dict]
    edges: list[tuple[str, str]]
    family_product_layer: dict[str, str] = field(default_factory=dict)


class ParseError(Exception):
    pass


def text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def cell(row: dict, *names: str):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def load_table(ws, required_header: str) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    header_idx = None
    for i, raw in enumerate(rows):
        vals = [text(v) for v in raw]
        if required_header in vals:
            header_idx = i
            break
    if header_idx is None:
        raise ParseError(f"header {required_header!r} not found in {ws.title}")
    headers = [text(h) or f"col_{i}" for i, h in enumerate(rows[header_idx])]
    out = []
    for raw in rows[header_idx + 1 :]:
        if all(v is None or str(v).strip() == "" for v in raw):
            continue
        item = {}
        for i, h in enumerate(headers):
            item[h] = raw[i] if i < len(raw) else None
        out.append(item)
    return out


def parse_threshold(display: str) -> dict:
    raw = text(display)
    lower = raw.lower()
    band = re.search(r"(\d+(?:\.\d+)?)\s*[–\-]\s*(\d+(?:\.\d+)?)", raw)
    if band:
        lo, hi = float(band.group(1)), float(band.group(2))
        if lo > 1:
            lo /= 100
        if hi > 1:
            hi /= 100
        return {"operator": "band", "min": lo, "max": hi, "display": raw, "k": None}
    if re.search(r"100\s*%", raw):
        return {"operator": "eq", "value": 1.0, "display": raw, "k": None}
    pct = re.search(r"[≥>=]{1,2}\s*(\d+(?:\.\d+)?)\s*%", raw)
    if pct:
        return {"operator": "gte", "value": float(pct.group(1)) / 100, "display": raw, "k": None}
    lte_pct = re.search(r"[≤<=]{1,2}\s*(\d+(?:\.\d+)?)\s*%", raw)
    if lte_pct:
        return {"operator": "lte", "value": float(lte_pct.group(1)) / 100, "display": raw, "k": None}
    num = re.search(r"[≥>=]{1,2}\s*(\d+(?:\.\d+)?)", raw)
    if num:
        value = float(num.group(1))
        if value > 1:
            value /= 100
        return {"operator": "gte", "value": value, "display": raw, "k": None}
    lte = re.search(r"[≤<=]{1,2}\s*(\d+(?:\.\d+)?)", raw)
    if lte:
        value = float(lte.group(1))
        if value > 1:
            value /= 100
        return {"operator": "lte", "value": value, "display": raw, "k": None}
    if re.search(r"\b0\s*%\b|=\s*0\b|zero incidents|no unauthorized", lower):
        return {"operator": "eq", "value": 0.0, "display": raw, "k": None}
    return {"operator": "gte", "value": None, "display": raw or "unspecified", "k": None}


def split_patterns(value: str) -> list[str]:
    raw = text(value)
    if not raw:
        return []
    return [p.strip() for p in re.split(r"\s*,\s*", raw) if p.strip()]


def slug_pattern(name: str) -> str | None:
    raw = text(name).lower()
    if not raw or "perspective" in raw or "all patterns" in raw:
        return None
    raw = raw.replace("plan-execute", "plan_and_execute").replace("plan–execute", "plan_and_execute")
    raw = raw.replace("simple tool-use", "simple_tool_use").replace("simple tool use", "simple_tool_use")
    raw = raw.replace("multi-agent", "multi_agent")
    raw = raw.replace(" ", "_").replace("-", "_")
    raw = re.sub(r"[^a-z0-9_]+", "", raw)
    return raw or None


def parse_pattern_notes(value: str) -> dict[str, str]:
    notes: dict[str, str] = {}
    raw = text(value)
    if not raw:
        return notes
    for chunk in re.split(r"\s*\|\s*", raw):
        if ":" not in chunk:
            continue
        key, rest = chunk.split(":", 1)
        slug = slug_pattern(key)
        if slug:
            notes[slug] = rest.strip()
    return notes


def family_of(metric_id: str) -> str:
    return metric_id.split("-")[0]


def gate_of(value: str) -> str:
    raw = text(value)
    if "2/3" in raw:
        return "G2/G3"
    if "Gate 3" in raw:
        return "G3"
    if "Gate 2" in raw:
        return "G2"
    return raw


def is_derived_risk(rid: str) -> bool:
    return text(rid).lower() in {"", "(derived)", "derived"}


def yesno(value) -> bool:
    return text(value).lower() in {"yes", "y", "true", "1"}


def secondary_from_notes(notes: str, primary: str) -> list[str]:
    match = re.search(r"HLA secondary TA\(s\):\s*([^.;]+)", text(notes))
    if not match:
        return []
    items = [part.strip() for part in match.group(1).split(",") if part.strip()]
    cleaned = []
    for item in items:
        item = re.split(r"\s*;\s*", item)[0].strip()
        if item and item != primary and "Original HLA" not in item:
            cleaned.append(item)
    return cleaned


def hla_primary_from_notes(notes: str, fallback: str) -> str:
    match = re.search(r"Original HLA primary TA:\s*([^(]+)", text(notes))
    if match:
        return match.group(1).strip()
    return fallback


def _related_families(row: dict) -> list[str]:
    for key, value in row.items():
        if text(key).startswith("Related") and "famil" in text(key).lower():
            return [part.strip() for part in text(value).split(",") if part.strip()]
    return []


def _pick_sheet(wb, *needles: str):
    names = list(wb.sheetnames)
    for needle in needles:
        for name in names:
            if needle.lower() in name.lower():
                return wb[name]
    raise ParseError(f"no sheet matching {needles} in {names}")


def parse_workbook(path: Path, archetype: str | None = None) -> ParsedLibrary:
    path = Path(path)
    if not path.exists():
        raise ParseError(f"workbook not found: {path}")
    meta = WORKBOOK_ARCHETYPES.get(path.name)
    archetype_id = normalize_archetype(
        archetype or (meta["id"] if meta else path.stem)
    )
    display_name = meta["display_name"] if meta else archetype_id.replace("_", " ").title()

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        layers_ws = _pick_sheet(wb, "Architecture Layers")
        formula_ws = _pick_sheet(wb, "Formula Inputs")
        master_ws = _pick_sheet(wb, "RMTE (Master)", "RMTE Master")
        layers_rows = load_table(layers_ws, "Layer")
        formula_rows = load_table(formula_ws, "Metric ID")
        master_rows = load_table(master_ws, "Metric ID")
        master_sheet = master_ws.title
    finally:
        wb.close()

    formula_by_metric: dict[str, list[dict]] = defaultdict(list)
    formula_inputs: list[dict] = []
    for row in formula_rows:
        mid = text(cell(row, "Metric ID"))
        if not mid:
            continue
        item = {
            "metric_id": mid,
            "term": text(cell(row, "Formula Input")),
            "meaning": text(cell(row, "Input Meaning")),
            "source_type": text(cell(row, "Input Source Type")),
            "log_or_dataset": text(cell(row, "Log / Dataset providing it")),
            "ai_product_layer": text(cell(row, "AI Product Layer (emits)")),
            "platform_layer": text(cell(row, "TrustLensAI Platform Layer")),
            "notes": text(cell(row, "Notes")),
            "formula": text(cell(row, "Formula")),
            "threshold": text(cell(row, "Threshold")),
        }
        formula_by_metric[mid].append({k: v for k, v in item.items() if k != "metric_id"})
        formula_inputs.append(item)

    metrics: dict[str, dict] = {}
    risks: dict[str, dict] = {}
    edges: dict[str, set[str]] = defaultdict(set)
    pattern_notes: dict[str, dict[str, str]] = {}
    family_defaults: dict[str, dict[str, str]] = defaultdict(dict)
    derived_perspectives: dict[str, dict] = {}
    rmte_rows: list[dict] = []
    pattern_set: list[str] = []

    for ordinal, row in enumerate(master_rows):
        mid = text(cell(row, "Metric ID"))
        if not METRIC_ID_RE.match(mid):
            continue
        rid = text(cell(row, "Risk ID"))
        name = text(cell(row, "Metric"))
        category = text(cell(row, "Metric Category"))
        family = family_of(mid)
        hla_ta = text(cell(row, "Trust Attribute"))
        notes = text(cell(row, "Notes / Assumptions"))
        primary = TA_NORM.get(hla_ta, hla_ta or "Reliability")
        patterns = split_patterns(cell(row, "Execution Pattern") or "")
        for p in patterns:
            slug = slug_pattern(p) or p
            if slug not in pattern_set:
                pattern_set.append(slug)
        notes_map = parse_pattern_notes(cell(row, "Pattern Applicability") or "")
        if notes_map:
            pattern_notes[mid] = notes_map
            for key, val in notes_map.items():
                family_defaults[family].setdefault(key, val)

        spine_row = {
            "ordinal": ordinal,
            "index": cell(row, "Index"),
            "metric_id": mid,
            "metric": name,
            "risk_id": rid or None,
            "risk": text(cell(row, "Risk")),
            "risk_description": text(cell(row, "Risk Description")),
            "risk_cause": text(cell(row, "Risk Cause")),
            "risk_impact": text(cell(row, "Risk Impact")),
            "risk_category": text(cell(row, "Risk Category")),
            "trust_attribute": primary,
            "hla_trust_attribute": hla_ta,
            "pillar": text(cell(row, "Pillar")),
            "test_suite": text(cell(row, "Test Suite")),
            "test_suite_id": text(cell(row, "Test Suite ID")),
            "test_scenario": text(cell(row, "Test Scenario")),
            "test_case": text(cell(row, "Test Case")),
            "test_type": text(cell(row, "Test Type")),
            "test_method": text(cell(row, "Test Method")),
            "evidence": text(cell(row, "Evidence")),
            "evidence_type": text(cell(row, "Evidence Type")),
            "evidence_description": text(cell(row, "Evidence Description")),
            "log_source": text(cell(row, "Log / Telemetry Source")),
            "required_telemetry": text(cell(row, "Required Telemetry / Data")),
            "formula": text(cell(row, "Formula / Calculation")),
            "threshold": text(cell(row, "Acceptance Threshold")),
            "pass_fail_rule": text(cell(row, "Pass / Fail Rule")),
            "evaluation_method": text(cell(row, "Evaluation Method")),
            "evaluator": text(cell(row, "Evaluator / Judge")),
            "ai_product_layer": text(cell(row, "AI Product Layer")),
            "platform_layer": text(cell(row, "TrustLensAI Platform Layer")),
            "platform_layer_chain": text(cell(row, "Platform Layer Chain")),
            "execution_pattern": text(cell(row, "Execution Pattern")),
            "pattern_applicability": text(cell(row, "Pattern Applicability")),
            "severity": text(cell(row, "Severity")),
            "priority": text(cell(row, "Priority")),
            "governance_gate": text(cell(row, "Governance Gate")),
            "automation_level": text(cell(row, "Automation Level")),
            "human_review_required": yesno(cell(row, "Human Review Required")),
            "source_section": text(cell(row, "Source Section")),
            "source_page": text(cell(row, "Source Page")),
            "source_mapping": text(cell(row, "Source Metric / Risk Mapping")),
            "notes": notes,
        }
        rmte_rows.append(spine_row)

        if is_derived_risk(rid):
            bucket = derived_perspectives.setdefault(
                text(cell(row, "Risk")) or "(derived)",
                {
                    "risk_id": "(derived)",
                    "name": text(cell(row, "Risk")) or text(cell(row, "Risk Description")),
                    "description": text(cell(row, "Risk Description")) or text(cell(row, "Risk")),
                    "category": text(cell(row, "Risk Category")) or "Business",
                    "pillar": text(cell(row, "Pillar")) or "Prove",
                    "trust_attribute": primary,
                    "metric_ids": [],
                    "notes": notes,
                },
            )
            if mid not in bucket["metric_ids"]:
                bucket["metric_ids"].append(mid)
        else:
            edges[rid].add(mid)
            risks.setdefault(
                rid,
                {
                    "risk_id": rid,
                    "name": text(cell(row, "Risk")) or text(cell(row, "Risk Description")),
                    "description": text(cell(row, "Risk Description")) or text(cell(row, "Risk")),
                    "category": text(cell(row, "Risk Category")) or "Technical",
                    "pillar": text(cell(row, "Pillar")) or "Observe",
                    "trust_attribute": primary,
                    "trust_attributes": [primary],
                    "hla_primary_trust_attribute": hla_primary_from_notes(notes, hla_ta),
                    "secondary_trust_attributes": secondary_from_notes(notes, hla_ta),
                    "cause": text(cell(row, "Risk Cause")),
                    "impact": text(cell(row, "Risk Impact")),
                    "severity": text(cell(row, "Severity")),
                    "priority": text(cell(row, "Priority")),
                    "source_section": text(cell(row, "Source Section")),
                    "notes": notes,
                },
            )

        metric = metrics.setdefault(
            mid,
            {
                "metric_id": mid,
                "name": name,
                "definition": text(cell(row, "Metric Definition")),
                "formula": text(cell(row, "Formula / Calculation")),
                "formula_inputs": formula_by_metric.get(mid, []),
                "category": category,
                "family": family,
                "pillar": text(cell(row, "Pillar")) or "Prove",
                "primary_trust_attribute": primary,
                "hla_primary_trust_attribute": hla_primary_from_notes(notes, hla_ta),
                "secondary_trust_attributes": secondary_from_notes(notes, hla_ta),
                "risk_ids": [],
                "threshold": parse_threshold(cell(row, "Acceptance Threshold") or ""),
                "measurement_direction": text(cell(row, "Measurement Direction")),
                "pass_fail_rule": text(cell(row, "Pass / Fail Rule")),
                "test_suite": text(cell(row, "Test Suite")),
                "test_suite_id": text(cell(row, "Test Suite ID")),
                "test_type": text(cell(row, "Test Type")),
                "test_method": text(cell(row, "Test Method")),
                "evaluation_method": text(cell(row, "Evaluation Method")),
                "evaluator": text(cell(row, "Evaluator / Judge")),
                "evidence_type": text(cell(row, "Evidence Type")),
                "evidence_description": text(cell(row, "Evidence Description")),
                "evidence_pack": text(cell(row, "Evidence")),
                "required_telemetry": text(cell(row, "Required Telemetry / Data")),
                "log_source": text(cell(row, "Log / Telemetry Source")),
                "ai_product_layer": text(cell(row, "AI Product Layer")),
                "platform_layer": text(cell(row, "TrustLensAI Platform Layer")),
                "platform_layer_chain": text(cell(row, "Platform Layer Chain")),
                "execution_patterns": patterns,
                "pattern_applicability": text(cell(row, "Pattern Applicability")),
                "severity": text(cell(row, "Severity")),
                "priority": text(cell(row, "Priority")),
                "gate": gate_of(cell(row, "Governance Gate") or ""),
                "governance_gate": text(cell(row, "Governance Gate")),
                "automation_level": text(cell(row, "Automation Level")),
                "human_review_required": yesno(cell(row, "Human Review Required")),
                "source_section": text(cell(row, "Source Section")),
                "source_page": text(cell(row, "Source Page")),
                "source_mapping": text(cell(row, "Source Metric / Risk Mapping")),
                "notes": notes,
            },
        )
        if rid and not is_derived_risk(rid) and rid not in metric["risk_ids"]:
            metric["risk_ids"].append(rid)

    metric_list = [metrics[k] for k in sorted(metrics, key=lambda m: (family_of(m), m))]
    family_product_layer = {}
    for metric in metric_list:
        family_product_layer.setdefault(metric["family"], metric.get("ai_product_layer") or "")

    layers = []
    for row in layers_rows:
        if cell(row, "Index") in (None, "", "Index"):
            continue
        layers.append(
            {
                "index": cell(row, "Index"),
                "architecture": text(cell(row, "Architecture")),
                "layer": text(cell(row, "Layer")),
                "logs_telemetry": text(cell(row, "Logs / Telemetry emitted")),
                "key_fields": text(cell(row, "Key fields (evidence carried)")),
                "ingested_by": text(cell(row, "Ingested / consumed by (platform)")),
                "related_metric_families": _related_families(row),
                "notes": text(cell(row, "Notes")),
            }
        )

    source = (
        f"{path.name} — sheet '{master_sheet}' plus 'Logs ↔ Formula Inputs' "
        "and 'Logs ↔ Architecture Layers'"
    )
    return ParsedLibrary(
        archetype_id=archetype_id,
        display_name=display_name,
        version="v1",
        library_id=f"tlai-rmte-{archetype_id}",
        source=source,
        workbook=path.name,
        workbook_path=path,
        workbook_sha256=file_sha256(path),
        execution_patterns=pattern_set,
        notes={
            "workbook": path.name,
            "master_sheet": master_sheet,
            "formula_sheet": "Logs ↔ Formula Inputs",
            "layers_sheet": "Logs ↔ Architecture Layers",
            "threshold_binding": "Always bind metric_id + execution_pattern + threshold; never a generic-only threshold.",
            "compliance": "TrustLensAI produces evidence; it does not certify compliance.",
        },
        metrics=metric_list,
        risks=[risks[k] for k in sorted(risks)],
        rmte_rows=rmte_rows,
        formula_inputs=[item for item in formula_inputs if METRIC_ID_RE.match(item["metric_id"])],
        layers=layers,
        family_defaults={fam: dict(vals) for fam, vals in family_defaults.items()},
        metric_overrides=pattern_notes,
        derived_perspectives=list(derived_perspectives.values()),
        edges=[(rid, mid) for rid, mids in sorted(edges.items()) for mid in sorted(mids)],
        family_product_layer=family_product_layer,
    )


def discover_workbooks(root: Path) -> list[Path]:
    found = []
    for name in WORKBOOK_ARCHETYPES:
        path = root / name
        if path.exists():
            found.append(path)
    return found
