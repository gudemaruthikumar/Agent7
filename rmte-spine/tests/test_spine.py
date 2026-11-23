from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from rmte_spine.config import tlai_root
from rmte_spine.db import create_schema, reset_engine
from rmte_spine.ingest import ingest_tlai, ingest_workbook
from rmte_spine.models import Base
from rmte_spine.parse import parse_workbook
from rmte_spine.query import get_metric, get_risk, list_releases, rmte_links

TLAI = tlai_root()
RAG_XLSX = TLAI / "TrustLensAI_RAG_Logs_Layers_Formulas.xlsx"
AGENT_XLSX = TLAI / "TrustLensAI_Agentic_Logs_Layers_Formulas.xlsx"


@pytest.fixture()
def session(tmp_path: Path):
    reset_engine()
    engine = create_engine(f"sqlite:///{tmp_path / 'spine.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess = Session()
    try:
        yield sess
        sess.commit()
    finally:
        sess.close()
        engine.dispose()
        reset_engine()


@pytest.mark.skipif(not RAG_XLSX.exists(), reason="TLAI RAG workbook missing")
def test_parse_rag_has_cq01():
    parsed = parse_workbook(RAG_XLSX)
    ids = [m["metric_id"] for m in parsed.metrics]
    assert parsed.archetype_id == "rag"
    assert "CQ-01" in ids
    assert parsed.rmte_rows
    assert any(t["metric_id"] == "CQ-01" for t in parsed.formula_inputs)


@pytest.mark.skipif(not AGENT_XLSX.exists(), reason="TLAI Agentic workbook missing")
def test_parse_agent_has_ap01():
    parsed = parse_workbook(AGENT_XLSX)
    ids = [m["metric_id"] for m in parsed.metrics]
    assert parsed.archetype_id == "agent"
    assert "AP-01" in ids
    assert "CQ-01" not in ids


@pytest.mark.skipif(not RAG_XLSX.exists() or not AGENT_XLSX.exists(), reason="TLAI workbooks missing")
def test_ingest_two_archetypes_share_one_catalog(session):
    ingest_workbook(session, RAG_XLSX)
    ingest_workbook(session, AGENT_XLSX)
    releases = {row["archetype"]: row for row in list_releases(session)}
    assert "rag" in releases
    assert "agent" in releases
    cq = get_metric(session, "rag", "CQ-01")
    assert cq["metric"]["name"]
    ap = get_metric(session, "agent", "AP-01")
    assert ap["metric"]["metric_id"] == "AP-01"
    links = rmte_links(session, "agent", metric_id="AP-01")
    assert links
    assert links[0]["metric_id"] == "AP-01"
    ag = get_risk(session, "agent", "AG-01")
    assert "AP-01" in ag["metric_ids"]


def test_export_libraries_match_agent5_shape(tmp_path, monkeypatch):
    from rmte_spine import library as lib

    dest = tmp_path / "libraries"
    monkeypatch.setattr(lib, "LIBRARIES_ROOT", dest)
    catalog = lib.export_libraries()
    names = {row["archetype"] for row in catalog["archetypes"]}
    assert "agent" in names and "rag" in names and "chat_assistant" in names
    chat = dest / "chat_assistant" / "v1"
    required = {
        "chat_assistant_metrics.json",
        "chat_assistant_risks.json",
        "risk_metric_map.json",
        "formula_inputs.json",
        "logs_layers.json",
        "metric_pattern_matrix.json",
        "category_rules.json",
        "scenario_templates.json",
        "case_variant_slots.json",
        "test_case_schema.json",
        "test_case_templates.json",
        "llm_options.json",
    }
    assert required <= {p.name for p in chat.glob("*.json")}
    metrics = json.loads((chat / "chat_assistant_metrics.json").read_text(encoding="utf-8"))
    assert metrics["metrics"]
    assert "formula_inputs" in metrics["metrics"][0]
    assert metrics["family_observation_stage"]
    rules = json.loads((chat / "category_rules.json").read_text(encoding="utf-8"))
    assert rules["required_categories_by_family"]


@pytest.mark.skipif(not TLAI.is_dir(), reason="TLAI folder missing")
def test_ingest_all_tlai_archetypes(session):
    results = ingest_tlai(session)
    published = [r for r in results if r["status"] in {"published", "unchanged"}]
    assert len(published) >= 16
    archetypes = {r["archetype"] for r in published}
    assert {"rag", "agent", "chat_assistant", "copilot", "traditional_ml"} <= archetypes
    assert get_metric(session, "chat_assistant", "RQ-01")["metric"]["metric_id"] == "RQ-01"
