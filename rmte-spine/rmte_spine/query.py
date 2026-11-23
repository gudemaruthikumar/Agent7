from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmte_spine.config import KIND_TO_LIBRARIES_FIELD, normalize_archetype
from rmte_spine.models import (
    AuthoringDocument,
    CatalogDocument,
    FamilyPatternNote,
    FormulaTerm,
    LibraryRelease,
    Metric,
    MetricPatternNote,
    ProductLayer,
    Risk,
    RiskMetricLink,
    RmteRow,
)


class SpineError(Exception):
    pass


def get_release(
    session: Session,
    archetype: str,
    version: str | None = None,
) -> LibraryRelease:
    key = normalize_archetype(archetype)
    stmt = select(LibraryRelease).where(LibraryRelease.archetype_id == key)
    if version:
        stmt = stmt.where(LibraryRelease.version == version)
    else:
        stmt = stmt.order_by(LibraryRelease.published_at.desc(), LibraryRelease.id.desc())
    release = session.scalars(stmt).first()
    if release is None:
        raise SpineError(f"no RMTE release for archetype {archetype!r} version {version or 'latest'}")
    return release


def list_releases(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(LibraryRelease).order_by(LibraryRelease.archetype_id, LibraryRelease.version)
    ).all()
    return [
        {
            "archetype": r.archetype_id,
            "version": r.version,
            "library_id": r.library_id,
            "status": r.status,
            "metrics": len(r.metrics),
            "risks": len(r.risks),
            "content_hash": r.content_hash,
            "published_at": r.published_at.isoformat() if r.published_at else None,
        }
        for r in rows
    ]


def document_map(session: Session, release: LibraryRelease) -> dict[str, AuthoringDocument]:
    docs = session.scalars(
        select(AuthoringDocument).where(AuthoringDocument.release_id == release.id)
    ).all()
    return {d.kind: d for d in docs}


def libraries_payload(
    session: Session,
    archetype: str,
    version: str | None = None,
) -> dict[str, Any]:
    """Exact authoring JSON, shaped for Agent 5 Libraries."""
    release = get_release(session, archetype, version)
    docs = document_map(session, release)
    payload = {
        field: docs[kind].body if kind in docs else {}
        for kind, field in KIND_TO_LIBRARIES_FIELD.items()
    }
    payload["archetype"] = release.archetype_id
    return payload


def get_metric(
    session: Session,
    archetype: str,
    metric_id: str,
    version: str | None = None,
) -> dict[str, Any]:
    release = get_release(session, archetype, version)
    row = session.get(Metric, {"release_id": release.id, "metric_id": metric_id})
    if row is None:
        raise SpineError(f"{metric_id} not in {release.archetype_id}/{release.version}")
    risks = session.scalars(
        select(RiskMetricLink.risk_id).where(
            RiskMetricLink.release_id == release.id,
            RiskMetricLink.metric_id == metric_id,
        )
    ).all()
    terms = session.scalars(
        select(FormulaTerm).where(
            FormulaTerm.release_id == release.id,
            FormulaTerm.metric_id == metric_id,
        )
    ).all()
    return {
        "archetype": release.archetype_id,
        "version": release.version,
        "library_id": release.library_id,
        "metric": row.body,
        "risk_ids": list(risks),
        "formula_terms": [t.body for t in terms],
    }


def get_risk(
    session: Session,
    archetype: str,
    risk_id: str,
    version: str | None = None,
) -> dict[str, Any]:
    release = get_release(session, archetype, version)
    row = session.get(Risk, {"release_id": release.id, "risk_id": risk_id})
    if row is None:
        raise SpineError(f"{risk_id} not in {release.archetype_id}/{release.version}")
    metrics = session.scalars(
        select(RiskMetricLink.metric_id).where(
            RiskMetricLink.release_id == release.id,
            RiskMetricLink.risk_id == risk_id,
        )
    ).all()
    return {
        "archetype": release.archetype_id,
        "version": release.version,
        "risk": row.body,
        "metric_ids": list(metrics),
    }


def formula_terms_for(
    session: Session,
    archetype: str,
    metric_id: str,
    version: str | None = None,
) -> list[dict[str, Any]]:
    release = get_release(session, archetype, version)
    rows = session.scalars(
        select(FormulaTerm).where(
            FormulaTerm.release_id == release.id,
            FormulaTerm.metric_id == metric_id,
        )
    ).all()
    return [r.body for r in rows]


def pattern_note(
    session: Session,
    archetype: str,
    family: str,
    pattern: str,
    metric_id: str | None = None,
    version: str | None = None,
) -> str | None:
    release = get_release(session, archetype, version)
    if metric_id:
        override = session.get(
            MetricPatternNote,
            {"release_id": release.id, "metric_id": metric_id, "pattern": pattern},
        )
        if override is not None:
            return override.note
    row = session.get(
        FamilyPatternNote,
        {"release_id": release.id, "family": family, "pattern": pattern},
    )
    return row.note if row is not None else None


def layers(
    session: Session,
    archetype: str,
    version: str | None = None,
) -> list[dict[str, Any]]:
    release = get_release(session, archetype, version)
    rows = session.scalars(
        select(ProductLayer)
        .where(ProductLayer.release_id == release.id)
        .order_by(ProductLayer.layer_index)
    ).all()
    return [r.body for r in rows]


def pin_metric_set(
    session: Session,
    archetype: str,
    version: str | None = None,
) -> dict[str, Any]:
    """Hashes Squad 3 / Agent 5 can pin without reading the JSON files."""
    release = get_release(session, archetype, version)
    docs = document_map(session, release)
    metrics = docs.get("metrics")
    risks = docs.get("risks")
    if metrics is None or risks is None:
        raise SpineError("release is missing metrics or risks authoring documents")
    return {
        "source": "rmte_spine",
        "archetype": release.archetype_id,
        "library_id": release.library_id,
        "version": release.version,
        "content_hash": release.content_hash,
        "metrics_canonical_hash": metrics.content_hash,
        "metrics_file_sha256": metrics.source_sha256,
        "risks_canonical_hash": risks.content_hash,
        "risks_file_sha256": risks.source_sha256,
        "path": metrics.source_path,
    }


def rmte_links(
    session: Session,
    archetype: str,
    metric_id: str | None = None,
    risk_id: str | None = None,
    version: str | None = None,
) -> list[dict[str, Any]]:
    """The spine itself: Risk → Metric → Test → Evidence rows from the TLAI master sheet."""
    release = get_release(session, archetype, version)
    stmt = select(RmteRow).where(RmteRow.release_id == release.id).order_by(RmteRow.ordinal)
    if metric_id:
        stmt = stmt.where(RmteRow.metric_id == metric_id)
    if risk_id:
        stmt = stmt.where(RmteRow.risk_id == risk_id)
    return [row.body for row in session.scalars(stmt).all()]


def catalog_document(session: Session, kind: str) -> dict[str, Any] | None:
    row = session.get(CatalogDocument, kind)
    if row is None:
        return None
    return {
        "kind": row.kind,
        "library_id": row.library_id,
        "content_hash": row.content_hash,
        "source_path": row.source_path,
        "body": row.body,
    }
