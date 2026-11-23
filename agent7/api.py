from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .adapters import (
    DefaultDenyGateway,
    GroqExplainer,
    RepositoryPublisher,
    SyntheticEvaluation,
    SyntheticEvidenceWriter,
    SyntheticNotificationTicket,
    SyntheticPinner,
    SyntheticQ6,
    SystemClock,
    UUIDGenerator,
)
from .db import SqlAlchemyRepository
from .domain import (
    AssuranceState,
    ChangeRecord,
    DashboardSummary,
    Decision,
    DecisionRequest,
    KillSwitch,
    RegistryEntry,
    RegistryEvent,
)
from .service import AssuranceRevalidationService
from .spine_rmte import create_rmte

load_dotenv()

SYSTEM_PROMPT = (
    "From a registered change, use the LLM to select affected RMTE risks and map "
    "metrics on those risks. Use only catalog IDs. Do not stale metrics, run "
    "evaluation, or compare scores."
)


def create_app(database_url: str | None = None) -> FastAPI:
    resolved_database_url = (
        database_url or os.environ.get("AGENT7_DATABASE_URL") or "sqlite:///./agent7.db"
    )
    repository = SqlAlchemyRepository(resolved_database_url)
    rmte = create_rmte()
    scorer = SyntheticQ6()
    gateway = DefaultDenyGateway()
    service = AssuranceRevalidationService(
        repository=repository,
        rmte=rmte,
        pinner=SyntheticPinner(rmte),
        evaluation=SyntheticEvaluation(rmte),
        evidence=SyntheticEvidenceWriter(rmte),
        scorer=scorer,
        notifier=SyntheticNotificationTicket(repository),
        publisher=RepositoryPublisher(repository),
        gateway=gateway,
        clock=SystemClock(),
        ids=UUIDGenerator(),
        explainer=GroqExplainer(),
    )
    service.bootstrap()
    app = FastAPI(title="TrustLensAI Agent 7", version="0.2.0")
    app.state.repository = repository
    app.state.service = service
    app.state.rmte = rmte
    app.state.scorer = scorer
    app.state.gateway = gateway
    static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "agent": "tlai.agent.assurance_revalidation"}

    @app.get("/registry", response_model=RegistryEntry)
    def registry() -> RegistryEntry:
        return RegistryEntry(
            id="tlai.agent.assurance_revalidation",
            version="0.2.0",
            prompt_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            tools=sorted(service.ALLOWLIST),
            output_schema=ChangeRecord.model_json_schema(),
        )

    @app.get("/v1/dashboard", response_model=DashboardSummary)
    def dashboard(product_id: str | None = None) -> DashboardSummary:
        pid = product_id or rmte.product_context()["id"]
        return service.dashboard(pid)

    @app.put("/v1/active-product")
    def set_active_product(product_id: str):
        nonlocal rmte
        try:
            new_rmte = create_rmte(product_id)
        except FileNotFoundError:
            raise HTTPException(404, "Product not found")
        rmte = new_rmte
        app.state.rmte = new_rmte
        service.rmte = new_rmte
        service.pinner.rmte = new_rmte
        service.evaluation.rmte = new_rmte
        service.evidence.rmte = new_rmte
        return {"status": "ok", "active": product_id}

    @app.get("/v1/ai-registry")
    def ai_registry(product_id: str | None = None):
        # We use the provided product_id, or the currently active RMTE product
        pid = product_id or rmte.product_context()["id"]
        if pid != rmte.product_context()["id"]:
            # Load specific if requested via query param
            from .adapters import load_fixture
            try:
                product = load_fixture(product_id=pid)["product"]
            except FileNotFoundError:
                raise HTTPException(404, "Product not found")
        else:
            product = rmte.product_context()
        
        changes = [c for c in repository.list_changes() if c.event.product_id == pid]
        return {
            "product": {
                "id": product["id"],
                "name": product["name"],
                "archetype": product["archetype"],
                "criticality": product["criticality"],
                "activeVersion": product["active_version"],
            },
            "assets": [
                {
                    "type": "System prompt",
                    "version": product["prompt_version"],
                    "status": "Registered",
                },
                {
                    "type": "Knowledge corpus",
                    "version": product["corpus_version"],
                    "status": "Registered",
                },
                {
                    "type": "Evaluation dataset",
                    "version": product["dataset"]["version"],
                    "status": "Pinned",
                },
            ],
            "changes": [
                {
                    "changeId": change.change_id,
                    "type": change.event.name.value,
                    "version": change.event.resource_version,
                    "detail": change.detail,
                    "status": change.status.value,
                    "detectedAt": change.created_at,
                }
                for change in changes
            ],
        }

    @app.get("/v1/changes", response_model=list[ChangeRecord])
    def list_changes(product_id: str | None = None) -> list[ChangeRecord]:
        pid = product_id or rmte.product_context()["id"]
        changes = repository.list_changes()
        return [c for c in changes if c.event.product_id == pid]

    @app.get("/v1/changes/{change_id}", response_model=ChangeRecord)
    def get_change(change_id: str) -> ChangeRecord:
        change = repository.get_change(change_id)
        if not change:
            raise HTTPException(404, "Change not found")
        return change

    @app.post("/v1/changes", response_model=ChangeRecord)
    def register_change(event: RegistryEvent) -> ChangeRecord:
        return service.register_change(event)

    @app.post("/v1/changes/{change_id}/run", response_model=ChangeRecord)
    def run_change(change_id: str) -> ChangeRecord:
        try:
            return service.start_revalidation(change_id)
        except KeyError:
            raise HTTPException(404, "Change not found") from None

    @app.post("/v1/changes/{change_id}/approve", response_model=ChangeRecord)
    def approve_change(change_id: str, body: DecisionRequest | None = None) -> ChangeRecord:
        request = body or DecisionRequest()
        try:
            return service.decide(change_id, Decision.APPROVE, request.reviewer, request.notes)
        except KeyError:
            raise HTTPException(404, "Change not found") from None

    @app.post("/v1/changes/{change_id}/reject", response_model=ChangeRecord)
    def reject_change(change_id: str, body: DecisionRequest | None = None) -> ChangeRecord:
        request = body or DecisionRequest()
        try:
            return service.decide(change_id, Decision.REJECT, request.reviewer, request.notes)
        except KeyError:
            raise HTTPException(404, "Change not found") from None

    @app.post("/v1/changes/{change_id}/resubmit", response_model=ChangeRecord)
    def resubmit_change(change_id: str) -> ChangeRecord:
        """Re-escalate a rejected or failed change back through RMTE for a fresh evaluation run.

        Only valid when the change status is REJECTED or FAILED.  Clears all prior
        evaluation artefacts and re-runs the full impact-analysis → revalidation pipeline.
        """
        try:
            return service.resubmit(change_id)
        except KeyError:
            raise HTTPException(404, "Change not found") from None

    @app.get("/v1/assurance/{product_id}", response_model=AssuranceState)
    def assurance(product_id: str) -> AssuranceState:
        return service.initial_state(product_id)

    @app.get("/v1/packs")
    def packs():
        pid = rmte.product_context()["id"]
        return [p for p in repository.list_packs() if p.product_id == pid]

    @app.get("/v1/packs/{pack_id}")
    def pack_detail(pack_id: str):
        pack = repository.get_pack(pack_id)
        if not pack:
            raise HTTPException(404, "Evidence pack not found")
        return pack

    @app.get("/v1/notifications")
    def notifications():
        return repository.list_tasks()

    @app.get("/v1/ledger")
    def ledger(job_id: str | None = Query(default=None)):
        return repository.list_ledger(job_id)

    @app.get("/v1/emitted-events")
    def emitted_events():
        return repository.list_events()

    @app.get("/v1/kill-switch", response_model=KillSwitch)
    def get_kill_switch() -> KillSwitch:
        return KillSwitch(enabled=service.kill_switch)

    @app.put("/v1/kill-switch", response_model=KillSwitch)
    def put_kill_switch(value: KillSwitch) -> KillSwitch:
        service.kill_switch = value.enabled
        return value

    @app.post("/demo/reset")
    def reset_demo() -> dict[str, str]:
        repository.reset()
        service.kill_switch = False
        service.bootstrap()
        return {"status": "reset"}

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


app = create_app()
