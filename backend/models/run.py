import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base

if TYPE_CHECKING:
    from backend.models.attempt import Attempt


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStage(str, enum.Enum):
    INTAKE = "intake"
    BASELINE = "baseline"
    LOCALIZATION = "localization"
    GENERATION = "generation"
    VERIFICATION = "verification"
    RETRY = "retry"
    PR_CREATION = "pr_creation"
    COMPLETED = "completed"


class RunMode(str, enum.Enum):
    PUBLIC_DEMO = "public_demo"


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    issue_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    issue_url: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    base_commit: Mapped[str] = mapped_column(String, nullable=False)

    mode: Mapped[RunMode] = mapped_column(Enum(RunMode, name="run_mode"), nullable=False, default=RunMode.PUBLIC_DEMO)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"), nullable=False, default=RunStatus.QUEUED, index=True
    )
    current_stage: Mapped[RunStage] = mapped_column(
        Enum(RunStage, name="run_stage"), nullable=False, default=RunStage.INTAKE
    )

    model_policy: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts_taken: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pr_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Why the run stopped, in the words an operator (or the dashboard) needs.
    # A failed run that says only "failed" is not a useful record
    # (docs/phase5.md "Reliability").
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list["Attempt"]] = relationship(back_populates="run", cascade="all, delete-orphan")
