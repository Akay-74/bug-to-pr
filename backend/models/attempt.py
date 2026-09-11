import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base

if TYPE_CHECKING:
    from backend.models.run import Run
    from backend.models.verification_result import VerificationResult


class AttemptStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class FailureType(str, enum.Enum):
    NONE = "none"
    GENERATION_ERROR = "generation_error"
    PATCH_APPLICATION_ERROR = "patch_application_error"
    VERIFICATION_FAILED = "verification_failed"
    ENVIRONMENT_ERROR = "environment_error"
    PROTECTED_TEST_MODIFIED = "protected_test_modified"
    TIMEOUT = "timeout"


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    model: Mapped[str | None] = mapped_column(String, nullable=True)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[AttemptStatus] = mapped_column(
        Enum(AttemptStatus, name="attempt_status"), nullable=False, default=AttemptStatus.PENDING
    )
    failure_type: Mapped[FailureType | None] = mapped_column(
        Enum(FailureType, name="attempt_failure_type"), nullable=True
    )

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["Run"] = relationship(back_populates="attempts")
    verification_results: Mapped[list["VerificationResult"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
