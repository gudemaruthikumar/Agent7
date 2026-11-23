from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent7.adapters import DefaultDenyGateway, FallbackExplainer, GroqExplainer, SyntheticRMTE
from agent7.api import create_app
from agent7.domain import ChangeRecord, ImpactNarrative, TriggerCode


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RMTE_SPINE_DISABLE", "1")
    app = create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    return app, TestClient(app)


def test_root_serves_dashboard(app_client):
    _, client = app_client
    response = client.get("/")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert client.get("/static/app.js").status_code == 200


def test_registry_exposes_assets_and_changes(app_client):
    _, client = app_client
    registry = client.get("/v1/ai-registry").json()
    assert registry["product"]["name"] == "Support Knowledge Assistant"
    assert len(registry["assets"]) == 3
    assert registry["changes"]


def test_evidence_pack_contains_documented_sections(app_client):
    _, client = app_client
    packs = client.get("/v1/packs").json()
    pack = client.get(f"/v1/packs/{packs[0]['pack_id']}").json()
    assert pack["storage"] == {"metadata": "index", "immutable_file": "worm"}
    assert pack["integrity"] == "HASH_ONLY"
    assert pack["artefacts"]
    first = pack["artefacts"][0]
    assert "worm_uri" in first
    assert "worm_key" in first
    assert "rmte" in first
    assert "value" not in first
    assert pack["sha256"]
    assert "scorecard_results" not in pack or pack.get("scorecard_results") in (None, [])


def test_bootstrap_maps_impact_but_does_not_run(app_client):
    _, client = app_client
    dash = client.get("/v1/dashboard").json()
    assert dash["pending_changes"] >= 1
    assert dash["completed_revalidations"] == 0
    changes = client.get("/v1/changes").json()
    assert changes[0]["status"] == "impact_ready"
    assert changes[0]["affected_risks"]
    assert changes[0]["affected_metric_ids"]
    assert changes[0]["eval_run"] is None
    assert changes[0]["comparisons"] == []


def test_prompt_change_maps_grounding_suite(app_client):
    _, client = app_client
    change = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-v9"},
    ).json()
    assert change["classification"]["trigger_code"] == "TD-06"
    assert [risk["risk_id"] for risk in change["affected_risks"]] == ["RAG-01"]
    assert change["affected_metric_ids"] == ["M20", "M21", "M24"]
    assert change["selected_test_ids"] == []
    assert change["previous_pack"]["pack_id"] == "pack_baseline_v1"
    state = client.get("/v1/assurance/rag-demo").json()
    assert state["metric_freshness"]["M20"] == "stale"


def test_corpus_change_maps_retrieval_and_staleness(app_client):
    _, client = app_client
    change = client.post(
        "/v1/changes",
        json={"name": "retrieval.corpus_updated", "resource_version": "corpus-v9"},
    ).json()
    assert change["classification"]["trigger_code"] == "TD-11"
    assert {risk["risk_id"] for risk in change["affected_risks"]} == {
        "RAG-02",
        "RAG-03",
        "RAG-04",
    }
    assert change["selected_test_ids"] == []
    ran = client.post(f"/v1/changes/{change['change_id']}/run").json()
    assert ran["status"] == "awaiting_approval"
    assert set(ran["selected_test_ids"]) == {"RAG-S5", "RAG-S6"}
    assert all(row["previous"] is not None for row in ran["comparisons"])


def test_explicit_run_compare_and_human_approval(app_client):
    _, client = app_client
    change = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-run", "simulation": "pass"},
    ).json()
    ran = client.post(f"/v1/changes/{change['change_id']}/run").json()
    assert ran["status"] == "awaiting_approval"
    assert ran["comparisons"]
    assert all(row["previous"] is not None for row in ran["comparisons"])
    assert ran["trust_factor"]["reference"].startswith("q6://")
    approved = client.post(
        f"/v1/changes/{change['change_id']}/approve",
        json={"reviewer": "owner", "notes": "looks good"},
    ).json()
    assert approved["status"] == "approved"
    state = client.get("/v1/assurance/rag-demo").json()
    assert state["posture"] == "GO"
    assert state["metric_freshness"]["M20"] == "fresh"


def test_quality_fail_requires_approval_and_reject_holds(app_client):
    _, client = app_client
    change = client.post(
        "/v1/changes",
        json={
            "name": "retrieval.corpus_updated",
            "resource_version": "corpus-bad",
            "simulation": "quality_fail",
        },
    ).json()
    ran = client.post(f"/v1/changes/{change['change_id']}/run").json()
    assert ran["status"] == "awaiting_approval"
    assert ran["posture_after"] == "RISK"
    assert any(not row["passed"] for row in ran["comparisons"])
    rejected = client.post(
        f"/v1/changes/{change['change_id']}/reject",
        json={"reviewer": "owner"},
    ).json()
    assert rejected["status"] == "rejected"
    state = client.get("/v1/assurance/rag-demo").json()
    assert state["posture"] == "HOLD"
    assert state["metric_freshness"]["M06"] == "stale"


def test_infra_failure_skips_scoring(app_client):
    app, client = app_client
    change = client.post(
        "/v1/changes",
        json={
            "name": "prompt.changed",
            "resource_version": "prompt-infra",
            "simulation": "infra_fail",
        },
    ).json()
    ran = client.post(f"/v1/changes/{change['change_id']}/run").json()
    assert ran["status"] == "failed"
    assert ran["trust_factor"] is None
    assert app.state.scorer.call_count == 0


def test_kill_switch_and_debounce(app_client):
    _, client = app_client
    client.put("/v1/kill-switch", json={"enabled": True})
    killed = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-kill"},
    ).json()
    assert killed["status"] == "killed"
    client.put("/v1/kill-switch", json={"enabled": False})
    first = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-dup"},
    ).json()
    second = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-dup"},
    ).json()
    assert first["status"] == "impact_ready"
    assert second["status"] == "debounced"


def test_killed_change_runs_after_switch_is_released(app_client):
    _, client = app_client
    client.put("/v1/kill-switch", json={"enabled": True})
    change = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-retry"},
    ).json()
    still_killed = client.post(f"/v1/changes/{change['change_id']}/run").json()
    assert still_killed["status"] == "killed"
    client.put("/v1/kill-switch", json={"enabled": False})
    retried = client.post(f"/v1/changes/{change['change_id']}/run").json()
    assert retried["status"] == "awaiting_approval"
    assert retried["selected_test_ids"] == ["RAG-S1"]


def test_notifications_are_reference_only(app_client):
    _, client = app_client
    change = client.post(
        "/v1/changes",
        json={"name": "prompt.changed", "resource_version": "prompt-note"},
    ).json()
    client.post(f"/v1/changes/{change['change_id']}/run")
    notes = client.get("/v1/notifications").json()
    assert notes
    blob = str(notes).lower()
    assert "prompt text" not in blob
    assert all("change_ref" in item["references"] for item in notes)


def test_llm_mapper_drops_invented_ids():
    rmte = SyntheticRMTE()
    explainer = GroqExplainer()
    explainer.api_key = "test-key"
    explainer._complete = lambda system, payload: {  # type: ignore[method-assign]
        "risk_ids": ["RAG-01", "FAKE-99"],
        "metrics_by_risk": {"RAG-01": ["M20", "M99"]},
        "summary": "Prompt change hits grounding risk.",
        "risk_rationale": {"RAG-01": "Hallucinated claims", "FAKE-99": "ignored"},
    }
    change = ChangeRecord.model_validate(
        {
            "change_id": "chg_llm",
            "event": {"name": "prompt.changed", "resource_version": "prompt-v2"},
            "classification": {"trigger_code": "TD-06", "scope": "DELTA", "severity": "HIGH"},
            "status": "detected",
            "detail": "prompt widened",
            "posture_before": "GO",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    risk_ids, by_risk, narrative = explainer.map_risks_and_metrics(
        change, rmte.risks(), rmte.metrics(), ["RAG-01"]
    )
    assert risk_ids == ["RAG-01"]
    assert "M20" in by_risk["RAG-01"]
    assert "M99" not in by_risk["RAG-01"]
    assert narrative.source == "groq"


def test_fallback_explainer_stays_on_known_risks():
    change = ChangeRecord.model_validate(
        {
            "change_id": "chg_1",
            "event": {"name": "prompt.changed", "resource_version": "prompt-v2"},
            "classification": {"trigger_code": "TD-06", "scope": "DELTA", "severity": "HIGH"},
            "status": "impact_ready",
            "detail": "prompt widened",
            "affected_risks": [
                {
                    "risk_id": "RAG-01",
                    "name": "Hallucinated claims",
                    "category": "Generation",
                    "metric_ids": ["M20"],
                }
            ],
            "posture_before": "GO",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    narrative: ImpactNarrative = FallbackExplainer().explain(change)
    assert "RAG-01" in narrative.risk_rationale
    assert narrative.source == "fallback"


def test_default_deny_gateway():
    gateway = DefaultDenyGateway()
    with pytest.raises(PermissionError):
        gateway.execute("customer.write", {"rmte.read"}, lambda: "no")


def test_spine_threshold_band_and_lt():
    from agent7.spine_rmte import threshold_from_spine

    band = threshold_from_spine({"operator": "band", "min": 0.2, "max": 0.5})
    assert band.passes(0.3)
    assert not band.passes(0.9)
    lower = threshold_from_spine({"operator": "lt", "value": 0.05})
    assert lower.passes(0.01)
    assert not lower.passes(0.2)


def test_create_rmte_respects_spine_disable(monkeypatch):
    from agent7.adapters import SyntheticRMTE
    from agent7.spine_rmte import create_rmte

    monkeypatch.setenv("RMTE_SPINE_DISABLE", "1")
    rmte = create_rmte("rag-demo")
    assert isinstance(rmte, SyntheticRMTE)
    assert "M20" in rmte.metrics()


def test_spine_library_files_supply_rag_risks_and_metrics():
    from rmte_spine.db import reset_engine

    from agent7.spine_rmte import SpineRMTE

    try:
        rmte = SpineRMTE("rag-demo", database_url="sqlite:///:memory:")
        assert rmte.source == "library_files"
        assert "RAG-01" in rmte.risks()
        assert "CQ-01" in rmte.metrics()
        assert rmte.metrics()["CQ-01"].risk_ids
        prompt = rmte.impact_for(TriggerCode.TD_06)
        corpus = rmte.impact_for(TriggerCode.TD_11)
        assert "CQ-01" not in prompt["metric_ids"]
        assert "ALL" not in prompt["risk_ids"]
        assert "ALL" not in corpus["risk_ids"]
        assert len(prompt["metric_ids"]) < len(rmte.metrics())
        assert len(corpus["metric_ids"]) < len(rmte.metrics())
        assert set(prompt["metric_ids"]) & {"AQ-01", "GH-01", "AE-01", "RS-01"}
        assert "CQ-01" in corpus["metric_ids"]
    finally:
        reset_engine()
