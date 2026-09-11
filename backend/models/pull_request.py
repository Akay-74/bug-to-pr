import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base

if TYPE_CHECKING:
    from backend.models.attempt import Attempt
    from backend.models.run import Run


class DeliveryStatus(str, enum.Enum):
    """How far a validated fix got along branch -> commit -> draft PR.

    COMMITTED is a complete, useful outcome on its own: with GitHub access
    unconfigured the pipeline stops there rather than pushing
    (docs/phase4.md "GitHub").
    """

    PENDING = "pending"
    COMMITTED = "committed"
    PR_CREATED = "pr_created"
    FAILED = "failed"


class DeliveryFailureType(str, enum.Enum):
    NONE = "none"
    NOT_VALIDATED = "not_validated"
    PATCH_APPLICATION_ERROR = "patch_application_error"
    VERIFICATION_FAILED = "verification_failed"
    GIT_ERROR = "git_error"
    GITHUB_AUTH_ERROR = "github_auth_error"
    GITHUB_PERMISSION_ERROR = "github_permission_error"
    BRANCH_CONFLICT = "branch_conflict"
    PR_CREATION_ERROR = "pr_creation_error"


class PullRequest(Base):
    """One branch/commit/draft-PR delivery for a validated fix (docs/phase4.md).

    Separate from Run because a Run is the fix-generation record: it can be
    replayed into a delivery more than once (a first attempt that stopped at
    COMMITTED because GitHub was not configured, then a later one that
    pushed), and each of those needs its own branch, commit and outcome.
    """

    __tablename__ = "pull_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Reproducibility: the exact commit the branch was cut from, and the
    # patch that was committed on top of it.
    repo: Mapped[str] = mapped_column(String, nullable=False)
    base_commit: Mapped[str] = mapped_column(String, nullable=False)
    branch_name: Mapped[str] = mapped_column(String, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status"), nullable=False, default=DeliveryStatus.PENDING, index=True
    )
    failure_type: Mapped[DeliveryFailureType | None] = mapped_column(
        Enum(DeliveryFailureType, name="delivery_failure_type"), nullable=True
    )
    # Always passed through backend/delivery/github.py's redaction before it
    # is written here (docs/phase4.md "Security": never log secrets).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_repo: Mapped[str | None] = mapped_column(String, nullable=True)
    base_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_state: Mapped[str | None] = mapped_column(String, nullable=True)
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    verification_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pr_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["Run"] = relationship()
    attempt: Mapped["Attempt | None"] = relationship()
