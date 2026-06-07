from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


EvalJSON = JSON().with_variant(JSONB(), "postgresql")


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint("report_name", name="uq_eval_runs_report_name"),
        Index("ix_eval_runs_timestamp", "timestamp"),
        Index("ix_eval_runs_suite_runner", "suite", "runner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_name: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suite: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(128), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    overall_success_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    total_trials: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    raw_report_json: Mapped[dict] = mapped_column(EvalJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalCase(Base):
    __tablename__ = "eval_cases"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_eval_cases_run_case"),
        Index("ix_eval_cases_case_id", "case_id"),
        Index("ix_eval_cases_scene_category", "scene", "category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    task: Mapped[str | None] = mapped_column(Text, nullable=True)
    scene: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    success_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_scores_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalTrial(Base):
    __tablename__ = "eval_trials"
    __table_args__ = (
        UniqueConstraint("case_row_id", "trial_number", name="uq_eval_trials_case_trial"),
        Index("ix_eval_trials_failure_class", "failure_class"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    case_row_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_cases.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trial_number: Mapped[int] = mapped_column(Integer, default=0)
    weighted_score: Mapped[float] = mapped_column(Float, default=0.0)
    expected_scene: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_scene: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_worker: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_calls_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    threshold_failures_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    missing_metrics_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalScore(Base):
    __tablename__ = "eval_scores"
    __table_args__ = (
        UniqueConstraint("trial_id", "metric", name="uq_eval_scores_trial_metric"),
        Index("ix_eval_scores_metric", "metric"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    trial_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_trials.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metric: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalTraceEvent(Base):
    __tablename__ = "eval_trace_events"
    __table_args__ = (
        UniqueConstraint("trial_id", "event_index", name="uq_eval_trace_events_trial_index"),
        Index("ix_eval_trace_events_event_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    trial_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_trials.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, default=0)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
