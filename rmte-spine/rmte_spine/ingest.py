from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rmte_spine.config import (
    ARCHETYPE_META,
    KIND_FILES,
    REQUIRED_KINDS,
    default_library_roots,
    normalize_archetype,
)
from rmte_spine.hashutil import canonical_sha256, file_sha256
from rmte_spine.models import (
    Archetype,
    AuthoringDocument,
    CatalogDocument,
    DerivedPerspective,
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


class IngestError(Exception):
    pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_existing(folder: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = folder / name
        if path.exists():
            return path
    return None


def load_authoring_folder(folder: Path) -> dict[str, tuple[Path, dict]]:
    if not folder.is_dir():
        raise IngestError(f"library folder not found: {folder}")
    docs: dict[str, tuple[Path, dict]] = {}
    missing = []
    for kind, names in KIND_FILES.items():
        path = _first_existing(folder, names)
        if path is None:
            if kind in REQUIRED_KINDS:
                missing.append(kind)
            continue
        docs[kind] = (path, _load_json(path))
    if missing:
        raise IngestError(f"{folder} missing required RMTE documents: {missing}")
    return docs


def _ensure_archetype(session: Session, archetype_id: str) -> None:
    if session.get(Archetype, archetype_id) is not None:
        return
    meta = ARCHETYPE_META.get(
        archetype_id,
        {
            "display_name": archetype_id.replace("_", " ").title(),
            "pattern_field": "execution_pattern",
            "notes": None,
        },
    )
    session.add(
        Archetype(
            id=archetype_id,
            display_name=meta["display_name"],
            pattern_field=meta["pattern_field"],
            notes=meta.get("notes"),
        )
    )
    session.flush()


def _existing_release(
    session: Session, archetype_id: str, version: str
) -> LibraryRelease | None:
    return session.scalar(
        select(LibraryRelease).where(
            LibraryRelease.archetype_id == archetype_id,
            LibraryRelease.version == version,
        )
    )


def _delete_release(session: Session, release: LibraryRelease) -> None:
    session.delete(release)
    session.flush()


def _metric_rows(metrics_doc: dict) -> list[Metric]:
    rows = []
    for item in metrics_doc.get("metrics") or []:
        mid = item["metric_id"]
        rows.append(
            Metric(
                metric_id=mid,
                name=item.get("name") or mid,
                family=item.get("family") or mid.split("-", 1)[0],
                category=item.get("category"),
                pillar=item.get("pillar"),
                primary_trust_attribute=item.get("primary_trust_attribute"),
                formula=item.get("formula"),
                test_suite_id=item.get("test_suite_id"),
                gate=item.get("gate"),
                priority=item.get("priority"),
                severity=item.get("severity"),
                design_mode=item.get("design_mode"),
                body=item,
            )
        )
    return rows


def _risk_rows(risks_doc: dict) -> list[Risk]:
    rows = []
    for item in risks_doc.get("risks") or []:
        rid = item["risk_id"]
        rows.append(
            Risk(
                risk_id=rid,
                name=item.get("name") or rid,
                category=item.get("category"),
                pillar=item.get("pillar"),
                trust_attribute=item.get("trust_attribute"),
                severity=item.get("severity"),
                body=item,
            )
        )
    return rows


def _link_rows(metrics_doc: dict, map_doc: dict) -> list[RiskMetricLink]:
    pairs: set[tuple[str, str]] = set()
    for metric in metrics_doc.get("metrics") or []:
        mid = metric["metric_id"]
        for rid in metric.get("risk_ids") or []:
            if rid:
                pairs.add((str(rid), mid))
    for edge in map_doc.get("edges") or []:
        rid = edge.get("risk_id")
        if not rid:
            continue
        for mid in edge.get("metric_ids") or []:
            pairs.add((str(rid), str(mid)))
    return [RiskMetricLink(risk_id=rid, metric_id=mid) for rid, mid in sorted(pairs)]


def _formula_rows(formula_doc: dict) -> list[FormulaTerm]:
    rows = []
    seen: set[tuple[str, str]] = set()
    for item in formula_doc.get("inputs") or []:
        mid = item.get("metric_id")
        term = item.get("term")
        if not mid or not term:
            continue
        key = (str(mid), str(term))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            FormulaTerm(
                metric_id=str(mid),
                term=str(term),
                meaning=item.get("meaning"),
                source_type=item.get("source_type"),
                log_or_dataset=item.get("log_or_dataset"),
                ai_product_layer=item.get("ai_product_layer"),
                platform_layer=item.get("platform_layer"),
                notes=item.get("notes"),
                body=item,
            )
        )
    return rows


def _layer_rows(layers_doc: dict) -> list[ProductLayer]:
    rows = []
    for item in layers_doc.get("layers") or []:
        idx = item.get("index")
        if idx is None:
            continue
        rows.append(
            ProductLayer(
                layer_index=int(idx),
                layer=item.get("layer") or f"layer-{idx}",
                architecture=item.get("architecture"),
                logs_telemetry=item.get("logs_telemetry"),
                key_fields=item.get("key_fields"),
                ingested_by=item.get("ingested_by"),
                related_metric_families=item.get("related_metric_families"),
                notes=item.get("notes"),
                body=item,
            )
        )
    return rows


def ingest_folder(
    session: Session,
    folder: Path,
    archetype: str | None = None,
    version: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Publish one archetype library folder as an immutable RMTE release."""
    docs = load_authoring_folder(folder)
    metrics_doc = docs["metrics"][1]
    archetype_id = normalize_archetype(archetype or metrics_doc.get("archetype"))
    version = version or str(metrics_doc.get("version") or "v1")
    _ensure_archetype(session, archetype_id)

    existing = _existing_release(session, archetype_id, version)
    document_hashes = {
        kind: canonical_sha256(body) for kind, (_path, body) in docs.items()
    }
    release_hash = canonical_sha256(document_hashes)

    if existing is not None:
        if existing.content_hash == release_hash and not replace:
            return {
                "status": "unchanged",
                "archetype": archetype_id,
                "version": version,
                "release_id": existing.id,
                "library_id": existing.library_id,
                "content_hash": existing.content_hash,
                "metrics": len(existing.metrics),
                "risks": len(existing.risks),
            }
        if not replace:
            raise IngestError(
                f"{archetype_id}/{version} already published with a different hash; "
                "pass replace=True to supersede, or publish a new version"
            )
        _delete_release(session, existing)

    release = LibraryRelease(
        archetype_id=archetype_id,
        version=version,
        library_id=metrics_doc.get("library_id") or f"hla-a1-{archetype_id}",
        status="published",
        source=metrics_doc.get("source"),
        notes=metrics_doc.get("notes") or {},
        execution_patterns=metrics_doc.get("execution_patterns") or [],
        family_observation_stage=metrics_doc.get("family_observation_stage") or {},
        family_product_layer=metrics_doc.get("family_product_layer") or {},
        metric_observation_stage_overrides=metrics_doc.get(
            "metric_observation_stage_overrides"
        )
        or {},
        content_hash=release_hash,
        published_at=datetime.now(timezone.utc),
    )
    session.add(release)
    session.flush()

    for kind, (path, body) in docs.items():
        session.add(
            AuthoringDocument(
                release_id=release.id,
                kind=kind,
                library_id=body.get("library_id") if isinstance(body, dict) else None,
                body=body,
                content_hash=document_hashes[kind],
                source_sha256=file_sha256(path),
                source_path=str(path),
            )
        )

    for row in _metric_rows(metrics_doc):
        row.release_id = release.id
        session.add(row)
    for row in _risk_rows(docs["risks"][1]):
        row.release_id = release.id
        session.add(row)
    for row in _link_rows(metrics_doc, docs["risk_metric_map"][1]):
        row.release_id = release.id
        session.add(row)
    for row in _formula_rows(docs["formula_inputs"][1]):
        row.release_id = release.id
        session.add(row)
    for row in _layer_rows(docs["logs_layers"][1]):
        row.release_id = release.id
        session.add(row)

    matrix = docs["metric_pattern_matrix"][1]
    for family, patterns in (matrix.get("family_defaults") or {}).items():
        if not isinstance(patterns, dict):
            continue
        for pattern, note in patterns.items():
            session.add(
                FamilyPatternNote(
                    release_id=release.id,
                    family=str(family),
                    pattern=str(pattern),
                    note=str(note),
                )
            )
    for metric_id, patterns in (matrix.get("metric_overrides") or {}).items():
        if not isinstance(patterns, dict):
            continue
        for pattern, note in patterns.items():
            session.add(
                MetricPatternNote(
                    release_id=release.id,
                    metric_id=str(metric_id),
                    pattern=str(pattern),
                    note=str(note),
                )
            )

    for i, item in enumerate(docs["risk_metric_map"][1].get("derived_perspectives") or []):
        session.add(
            DerivedPerspective(release_id=release.id, ordinal=i, body=item)
        )

    session.flush()
    return {
        "status": "published",
        "archetype": archetype_id,
        "version": version,
        "release_id": release.id,
        "library_id": release.library_id,
        "content_hash": release.content_hash,
        "metrics": len(metrics_doc.get("metrics") or []),
        "risks": len(docs["risks"][1].get("risks") or []),
        "documents": sorted(docs),
    }


def ingest_defaults(
    session: Session,
    roots: dict[str, Path] | None = None,
    replace: bool = False,
) -> list[dict[str, Any]]:
    roots = roots or default_library_roots()
    results = []
    for archetype_id, folder in roots.items():
        results.append(
            ingest_folder(
                session,
                folder,
                archetype=archetype_id,
                replace=replace,
            )
        )
    return results


def _docs_from_parsed(parsed) -> dict[str, dict]:
    from rmte_spine.library import build_library_docs

    return build_library_docs(parsed)


def ingest_workbook(
    session: Session,
    path: Path,
    archetype: str | None = None,
    version: str = "v1",
    replace: bool = False,
) -> dict[str, Any]:
    from rmte_spine.parse import parse_workbook

    parsed = parse_workbook(path, archetype=archetype)
    docs = _docs_from_parsed(parsed)
    packed = {kind: (parsed.workbook_path, body) for kind, body in docs.items()}
    result = ingest_parsed_docs(
        session,
        packed,
        archetype=parsed.archetype_id,
        version=version,
        replace=replace,
        source_sha256=parsed.workbook_sha256,
        source_path=str(parsed.workbook_path),
        rmte_rows=parsed.rmte_rows,
        display_name=parsed.display_name,
        library_id=parsed.library_id,
        source=parsed.source,
        notes=parsed.notes,
        execution_patterns=parsed.execution_patterns,
        family_product_layer=parsed.family_product_layer,
    )
    result["rmte_rows"] = len(parsed.rmte_rows)
    result["workbook"] = parsed.workbook
    return result


def ingest_parsed_docs(
    session: Session,
    docs: dict[str, tuple[Path, dict]],
    *,
    archetype: str,
    version: str,
    replace: bool,
    source_sha256: str | None,
    source_path: str | None,
    rmte_rows: list[dict] | None = None,
    display_name: str | None = None,
    library_id: str | None = None,
    source: str | None = None,
    notes: dict | None = None,
    execution_patterns: list | None = None,
    family_product_layer: dict | None = None,
) -> dict[str, Any]:
    metrics_doc = docs["metrics"][1]
    archetype_id = normalize_archetype(archetype or metrics_doc.get("archetype"))
    _ensure_archetype(session, archetype_id)
    existing = _existing_release(session, archetype_id, version)
    document_hashes = {kind: canonical_sha256(body) for kind, (_path, body) in docs.items()}
    release_hash = canonical_sha256(document_hashes)
    if existing is not None:
        if existing.content_hash == release_hash and not replace:
            return {
                "status": "unchanged",
                "archetype": archetype_id,
                "version": version,
                "release_id": existing.id,
                "library_id": existing.library_id,
                "content_hash": existing.content_hash,
                "metrics": len(existing.metrics),
                "risks": len(existing.risks),
            }
        if not replace:
            raise IngestError(
                f"{archetype_id}/{version} already published with a different hash; "
                "pass replace=True to supersede, or publish a new version"
            )
        _delete_release(session, existing)

    release = LibraryRelease(
        archetype_id=archetype_id,
        version=version,
        library_id=library_id or metrics_doc.get("library_id") or f"tlai-rmte-{archetype_id}",
        status="published",
        source=source or metrics_doc.get("source"),
        notes=notes or metrics_doc.get("notes") or {},
        execution_patterns=execution_patterns or metrics_doc.get("execution_patterns") or [],
        family_observation_stage=metrics_doc.get("family_observation_stage") or {},
        family_product_layer=family_product_layer or metrics_doc.get("family_product_layer") or {},
        metric_observation_stage_overrides=metrics_doc.get("metric_observation_stage_overrides") or {},
        content_hash=release_hash,
        published_at=datetime.now(timezone.utc),
    )
    session.add(release)
    session.flush()

    for kind, (path, body) in docs.items():
        session.add(
            AuthoringDocument(
                release_id=release.id,
                kind=kind,
                library_id=body.get("library_id") if isinstance(body, dict) else None,
                body=body,
                content_hash=document_hashes[kind],
                source_sha256=source_sha256 or file_sha256(path),
                source_path=source_path or str(path),
            )
        )
    for row in _metric_rows(metrics_doc):
        row.release_id = release.id
        session.add(row)
    for row in _risk_rows(docs["risks"][1]):
        row.release_id = release.id
        session.add(row)
    for row in _link_rows(metrics_doc, docs["risk_metric_map"][1]):
        row.release_id = release.id
        session.add(row)
    for row in _formula_rows(docs["formula_inputs"][1]):
        row.release_id = release.id
        session.add(row)
    for row in _layer_rows(docs["logs_layers"][1]):
        row.release_id = release.id
        session.add(row)

    matrix = docs["metric_pattern_matrix"][1]
    for family, patterns in (matrix.get("family_defaults") or {}).items():
        if not isinstance(patterns, dict):
            continue
        for pattern, note in patterns.items():
            session.add(
                FamilyPatternNote(
                    release_id=release.id,
                    family=str(family),
                    pattern=str(pattern),
                    note=str(note),
                )
            )
    for metric_id, patterns in (matrix.get("metric_overrides") or {}).items():
        if not isinstance(patterns, dict):
            continue
        for pattern, note in patterns.items():
            session.add(
                MetricPatternNote(
                    release_id=release.id,
                    metric_id=str(metric_id),
                    pattern=str(pattern),
                    note=str(note),
                )
            )
    for i, item in enumerate(docs["risk_metric_map"][1].get("derived_perspectives") or []):
        session.add(DerivedPerspective(release_id=release.id, ordinal=i, body=item))
    for item in rmte_rows or []:
        session.add(
            RmteRow(
                release_id=release.id,
                ordinal=int(item.get("ordinal") or 0),
                metric_id=item.get("metric_id"),
                risk_id=item.get("risk_id"),
                test_suite_id=item.get("test_suite_id"),
                trust_attribute=item.get("trust_attribute"),
                pillar=item.get("pillar"),
                body=item,
            )
        )
    session.flush()
    return {
        "status": "published",
        "archetype": archetype_id,
        "display_name": display_name,
        "version": version,
        "release_id": release.id,
        "library_id": release.library_id,
        "content_hash": release.content_hash,
        "metrics": len(metrics_doc.get("metrics") or []),
        "risks": len(docs["risks"][1].get("risks") or []),
        "documents": sorted(docs),
    }


def ingest_tlai(
    session: Session,
    root: Path | None = None,
    replace: bool = False,
) -> list[dict[str, Any]]:
    from rmte_spine.config import tlai_root
    from rmte_spine.parse import discover_workbooks

    folder = root or tlai_root()
    workbooks = discover_workbooks(folder)
    if not workbooks:
        raise IngestError(f"no TLAI archetype workbooks under {folder}")
    results = []
    for path in workbooks:
        results.append(ingest_workbook(session, path, replace=replace))
    ingest_catalog(session, folder)
    return results


def ingest_catalog(session: Session, root: Path) -> None:
    from rmte_spine.parse import load_table
    from openpyxl import load_workbook

    coverage = root / "TrustLensAI_Archetype_Coverage_2035_Roadmap.xlsx"
    master = next(root.glob("TrustLensAI_RMTE_Master*.xlsx"), None)
    if coverage.exists():
        wb = load_workbook(coverage, data_only=True, read_only=True)
        try:
            rows = load_table(wb["② Coverage Matrix"], "Archetype / Module")
        finally:
            wb.close()
        body = {"source": coverage.name, "rows": rows}
        _upsert_catalog(session, "coverage_matrix", "tlai-coverage", body, str(coverage))
    if master is not None:
        wb = load_workbook(master, data_only=True, read_only=True)
        try:
            qc = load_table(wb["_QC Reconciliation"], "Archetype")
            pillars = load_table(wb["_Pillar Map"], "Pillar")
        finally:
            wb.close()
        _upsert_catalog(
            session, "qc_reconciliation", "tlai-rmte-qc", {"source": master.name, "rows": qc}, str(master)
        )
        _upsert_catalog(
            session, "pillar_map", "tlai-pillar-map", {"source": master.name, "rows": pillars}, str(master)
        )


def _upsert_catalog(session: Session, kind: str, library_id: str, body: dict, source_path: str) -> None:
    existing = session.get(CatalogDocument, kind)
    digest = canonical_sha256(body)
    if existing is None:
        session.add(
            CatalogDocument(
                kind=kind,
                library_id=library_id,
                body=body,
                content_hash=digest,
                source_path=source_path,
            )
        )
        return
    existing.library_id = library_id
    existing.body = body
    existing.content_hash = digest
    existing.source_path = source_path
