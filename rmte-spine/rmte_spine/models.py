from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator


class Base(DeclarativeBase):
    pass


class JsonDoc(TypeDecorator):
    """JSON on SQLite, JSONB on Postgres."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Archetype(Base):
    __tablename__ = "archetypes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    pattern_field: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    releases: Mapped[list[LibraryRelease]] = relationship(back_populates="archetype")


class LibraryRelease(Base):
    __tablename__ = "library_releases"
    __table_args__ = (UniqueConstraint("archetype_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archetype_id: Mapped[str] = mapped_column(ForeignKey("archetypes.id"), nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    library_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="published")
    source: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    execution_patterns: Mapped[list] = mapped_column(JsonDoc, nullable=False, default=list)
    family_observation_stage: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    family_product_layer: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    metric_observation_stage_overrides: Mapped[dict] = mapped_column(
        JsonDoc, nullable=False, default=dict
    )
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    archetype: Mapped[Archetype] = relationship(back_populates="releases")
    documents: Mapped[list[AuthoringDocument]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )
    metrics: Mapped[list[Metric]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )
    risks: Mapped[list[Risk]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )


class AuthoringDocument(Base):
    __tablename__ = "authoring_documents"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    library_id: Mapped[str | None] = mapped_column(String)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String)
    source_path: Mapped[str | None] = mapped_column(Text)

    release: Mapped[LibraryRelease] = relationship(back_populates="documents")


class Metric(Base):
    __tablename__ = "metrics"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    metric_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    family: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String)
    pillar: Mapped[str | None] = mapped_column(String)
    primary_trust_attribute: Mapped[str | None] = mapped_column(String)
    formula: Mapped[str | None] = mapped_column(Text)
    test_suite_id: Mapped[str | None] = mapped_column(String)
    gate: Mapped[str | None] = mapped_column(String)
    priority: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)
    design_mode: Mapped[str | None] = mapped_column(String)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)

    release: Mapped[LibraryRelease] = relationship(back_populates="metrics")


class Risk(Base):
    __tablename__ = "risks"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    risk_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String)
    pillar: Mapped[str | None] = mapped_column(String)
    trust_attribute: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)

    release: Mapped[LibraryRelease] = relationship(back_populates="risks")


class RiskMetricLink(Base):
    __tablename__ = "risk_metric_links"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    risk_id: Mapped[str] = mapped_column(String, primary_key=True)
    metric_id: Mapped[str] = mapped_column(String, primary_key=True)


class FormulaTerm(Base):
    __tablename__ = "formula_terms"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    metric_id: Mapped[str] = mapped_column(String, primary_key=True)
    term: Mapped[str] = mapped_column(String, primary_key=True)
    meaning: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String)
    log_or_dataset: Mapped[str | None] = mapped_column(String)
    ai_product_layer: Mapped[str | None] = mapped_column(String)
    platform_layer: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)


class ProductLayer(Base):
    __tablename__ = "product_layers"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    layer_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    layer: Mapped[str] = mapped_column(String, nullable=False)
    architecture: Mapped[str | None] = mapped_column(String)
    logs_telemetry: Mapped[str | None] = mapped_column(Text)
    key_fields: Mapped[str | None] = mapped_column(Text)
    ingested_by: Mapped[str | None] = mapped_column(Text)
    related_metric_families: Mapped[list | None] = mapped_column(JsonDoc)
    notes: Mapped[str | None] = mapped_column(Text)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)


class FamilyPatternNote(Base):
    __tablename__ = "family_pattern_notes"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    family: Mapped[str] = mapped_column(String, primary_key=True)
    pattern: Mapped[str] = mapped_column(String, primary_key=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)


class MetricPatternNote(Base):
    __tablename__ = "metric_pattern_notes"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    metric_id: Mapped[str] = mapped_column(String, primary_key=True)
    pattern: Mapped[str] = mapped_column(String, primary_key=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)


class DerivedPerspective(Base):
    __tablename__ = "derived_perspectives"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)


class RmteRow(Base):
    """One Excel RMTE Master row: Risk → Metric → Test → Evidence."""

    __tablename__ = "rmte_rows"

    release_id: Mapped[int] = mapped_column(
        ForeignKey("library_releases.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_id: Mapped[str | None] = mapped_column(String)
    risk_id: Mapped[str | None] = mapped_column(String)
    test_suite_id: Mapped[str | None] = mapped_column(String)
    trust_attribute: Mapped[str | None] = mapped_column(String)
    pillar: Mapped[str | None] = mapped_column(String)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)


class CatalogDocument(Base):
    """Workbook-level docs that are not per-archetype (coverage, QC, pillar map)."""

    __tablename__ = "catalog_documents"

    kind: Mapped[str] = mapped_column(String, primary_key=True)
    library_id: Mapped[str | None] = mapped_column(String)
    body: Mapped[dict] = mapped_column(JsonDoc, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
