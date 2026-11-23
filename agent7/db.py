from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .domain import ChangeRecord, EmittedEvent, EvidencePackResult, LedgerEntry, TaskRecord


class Base(DeclarativeBase):
    pass


class JsonRow(Base):
    __abstract__ = True
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class JobRow(JsonRow):
    __tablename__ = "revalidation_jobs"
    external_id: Mapped[str] = mapped_column(String, unique=True, index=True)


class ChangeRow(JsonRow):
    __tablename__ = "assurance_changes"
    external_id: Mapped[str] = mapped_column(String, unique=True, index=True)


class DecayTriggerRow(JsonRow):
    __tablename__ = "decay_triggers"


class ImpactSetRow(JsonRow):
    __tablename__ = "impact_sets"
    job_id: Mapped[str] = mapped_column(String, index=True)


class AssuranceStateRow(JsonRow):
    __tablename__ = "assurance_state"
    product_id: Mapped[str] = mapped_column(String, unique=True)


class ProductContextRow(JsonRow):
    __tablename__ = "product_context"
    product_id: Mapped[str] = mapped_column(String, unique=True)


class LedgerRow(JsonRow):
    __tablename__ = "ledger"
    job_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True))


class EventRow(JsonRow):
    __tablename__ = "emitted_events"
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True))


class TaskRow(JsonRow):
    __tablename__ = "tasks"
    external_id: Mapped[str] = mapped_column(String, unique=True)


class PackRow(JsonRow):
    __tablename__ = "evidence_packs"
    external_id: Mapped[str] = mapped_column(String, unique=True)


class SqlAlchemyRepository:
    def __init__(self, url: str = "sqlite:///./agent7.db"):
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _dump(model: Any) -> dict[str, Any]:
        return model.model_dump(mode="json") if hasattr(model, "model_dump") else model

    def save_change(self, change: ChangeRecord) -> None:
        with Session(self.engine) as session:
            row = session.scalar(select(ChangeRow).where(ChangeRow.external_id == change.change_id))
            payload = self._dump(change)
            if row:
                row.data = payload
            else:
                session.add(ChangeRow(external_id=change.change_id, data=payload))
            session.commit()

    def get_change(self, change_id: str) -> ChangeRecord | None:
        with Session(self.engine) as session:
            row = session.scalar(select(ChangeRow).where(ChangeRow.external_id == change_id))
            return ChangeRecord.model_validate(row.data) if row else None

    def list_changes(self) -> list[ChangeRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(select(ChangeRow).order_by(ChangeRow.id.desc())).all()
            return [ChangeRecord.model_validate(row.data) for row in rows]

    def append_ledger(self, entry: LedgerEntry) -> None:
        with Session(self.engine) as session:
            session.add(
                LedgerRow(
                    job_id=entry.job_id, created_at=entry.created_at, data=self._dump(entry)
                )
            )
            session.commit()

    def list_ledger(self, job_id: str | None = None) -> list[LedgerEntry]:
        with Session(self.engine) as session:
            query = select(LedgerRow).order_by(LedgerRow.id)
            if job_id:
                query = query.where(LedgerRow.job_id == job_id)
            return [LedgerEntry.model_validate(row.data) for row in session.scalars(query).all()]

    def append_event(self, event: EmittedEvent) -> None:
        with Session(self.engine) as session:
            session.add(EventRow(created_at=event.created_at, data=self._dump(event)))
            session.commit()

    def list_events(self) -> list[EmittedEvent]:
        with Session(self.engine) as session:
            return [
                EmittedEvent.model_validate(row.data)
                for row in session.scalars(select(EventRow).order_by(EventRow.id)).all()
            ]

    def save_task(self, task: TaskRecord) -> None:
        with Session(self.engine) as session:
            session.add(TaskRow(external_id=task.task_id, data=self._dump(task)))
            session.commit()

    def list_tasks(self) -> list[TaskRecord]:
        with Session(self.engine) as session:
            return [
                TaskRecord.model_validate(row.data)
                for row in session.scalars(select(TaskRow).order_by(TaskRow.id)).all()
            ]

    def save_state(self, state: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(AssuranceStateRow).where(
                    AssuranceStateRow.product_id == state["product_id"]
                )
            )
            if row:
                row.data = state
            else:
                session.add(AssuranceStateRow(product_id=state["product_id"], data=state))
            session.commit()

    def get_state(self, product_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(AssuranceStateRow).where(AssuranceStateRow.product_id == product_id)
            )
            return row.data if row else None

    def save_pack(self, pack: EvidencePackResult) -> None:
        with Session(self.engine) as session:
            row = session.scalar(select(PackRow).where(PackRow.external_id == pack.pack_id))
            payload = self._dump(pack)
            if row:
                row.data = payload
            else:
                session.add(PackRow(external_id=pack.pack_id, data=payload))
            session.commit()

    def list_packs(self) -> list[EvidencePackResult]:
        with Session(self.engine) as session:
            return [
                EvidencePackResult.model_validate(row.data)
                for row in session.scalars(select(PackRow).order_by(PackRow.id.desc())).all()
            ]

    def get_pack(self, pack_id: str) -> EvidencePackResult | None:
        with Session(self.engine) as session:
            row = session.scalar(select(PackRow).where(PackRow.external_id == pack_id))
            return EvidencePackResult.model_validate(row.data) if row else None

    def save_decay_trigger(self, trigger: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            session.add(DecayTriggerRow(data=trigger))
            session.commit()

    def save_impact_set(self, job_id: str, impacts: list[dict[str, Any]]) -> None:
        with Session(self.engine) as session:
            session.add(ImpactSetRow(job_id=job_id, data={"impacts": impacts}))
            session.commit()

    def save_product_context(self, product_id: str, context: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(ProductContextRow).where(ProductContextRow.product_id == product_id)
            )
            if row:
                row.data = context
            else:
                session.add(ProductContextRow(product_id=product_id, data=context))
            session.commit()

    def reset(self) -> None:
        with Session(self.engine) as session:
            for table in (
                JobRow,
                ChangeRow,
                DecayTriggerRow,
                ImpactSetRow,
                AssuranceStateRow,
                ProductContextRow,
                LedgerRow,
                EventRow,
                TaskRow,
                PackRow,
            ):
                session.execute(delete(table))
            session.commit()
