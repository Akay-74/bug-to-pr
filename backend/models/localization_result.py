import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class LocalizationResult(Base):
    """One ranked localization candidate for one issue's run of the
    localization pipeline (docs/phase2.md §6). Rows are replaced wholesale
    on each new localization run for an issue_id (see
    backend/localization/service.py) rather than accumulating history —
    Phase 2 only needs "the current best answer", not an audit trail.
    """

    __tablename__ = "localization_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    file: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    symbol_type: Mapped[str] = mapped_column(String, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    semantic_score: Mapped[float] = mapped_column(Float, nullable=False)
    lexical_score: Mapped[float] = mapped_column(Float, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    matched_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    model_identifier: Mapped[str] = mapped_column(String, nullable=False)
    index_identifier: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
