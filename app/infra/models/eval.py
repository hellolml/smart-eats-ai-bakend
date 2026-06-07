from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
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
    # ── Experiment management fields (Phase 5) ──
    baseline_pin: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="Pinned baseline run ID for comparison")
    tags_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True, comment="Arbitrary tags for filtering/grouping")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Run notes or description")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="Owner of this eval run")
    release_marker: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="Release marker: candidate | released | rolled_back")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRunJob(Base):
    __tablename__ = "eval_run_jobs"
    __table_args__ = (
        Index("ix_eval_run_jobs_status", "status"),
        Index("ix_eval_run_jobs_created_at", "created_at"),
        Index("ix_eval_run_jobs_requested_by", "requested_by"),
        Index("ix_eval_run_jobs_runner_suite", "runner", "suite"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    runner: Mapped[str] = mapped_column(String(32), nullable=False)
    suite: Mapped[str] = mapped_column(String(32), nullable=False)
    num_trials: Mapped[int] = mapped_column(Integer, default=1)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    include_llm_judge: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_verify: Mapped[bool] = mapped_column(Boolean, default=False)
    persist_db: Mapped[bool] = mapped_column(Boolean, default=True)
    require_db_persist: Mapped[bool] = mapped_column(Boolean, default=False)
    output_dir: Mapped[str] = mapped_column(String(1024), nullable=False)
    report_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class ConversationRun(Base):
    __tablename__ = "conversation_runs"
    __table_args__ = (
        Index("ix_conversation_runs_started_at", "started_at"),
        Index("ix_conversation_runs_session_id", "session_id"),
        Index("ix_conversation_runs_user_id", "user_id"),
        Index("ix_conversation_runs_scene_worker", "scene", "worker"),
        Index("ix_conversation_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scene: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    final_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationTraceEvent(Base):
    __tablename__ = "conversation_trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "event_index", name="uq_conversation_trace_events_run_index"),
        Index("ix_conversation_trace_events_event_type", "event_type"),
        Index("ix_conversation_trace_events_tool_name", "tool_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, default=0)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationToolCall(Base):
    __tablename__ = "conversation_tool_calls"
    __table_args__ = (
        Index("ix_conversation_tool_calls_tool_name", "tool_name"),
        Index("ix_conversation_tool_calls_success", "success"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    args_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationMetric(Base):
    __tablename__ = "conversation_metrics"
    __table_args__ = (
        Index("ix_conversation_metrics_metric_name", "metric_name"),
        Index("ix_conversation_metrics_window", "window_start", "window_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=True)
    metric_name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(64), default="realtime")
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationEvalJob(Base):
    __tablename__ = "conversation_eval_jobs"
    __table_args__ = (
        Index("ix_conversation_eval_jobs_status", "status"),
        Index("ix_conversation_eval_jobs_job_type", "job_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationHumanReview(Base):
    __tablename__ = "conversation_human_reviews"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_conversation_human_reviews_run_id"),
        Index("ix_conversation_human_reviews_decision", "decision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), default="pending")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_tags_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    corrected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_behavior: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    dataset_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationCost(Base):
    __tablename__ = "conversation_costs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_conversation_costs_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    token_input: Mapped[int] = mapped_column(Integer, default=0)
    token_output: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_cost: Mapped[float] = mapped_column(Float, default=0.0)
    tool_cost: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    cost_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    pricing_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalProject(Base):
    __tablename__ = "eval_projects"
    __table_args__ = (
        UniqueConstraint("name", "environment", name="uq_eval_projects_name_environment"),
        Index("ix_eval_projects_environment", "environment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), default="local", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("project", "agent_name", "version", name="uq_agent_versions_project_agent_version"),
        Index("ix_agent_versions_project", "project"),
        Index("ix_agent_versions_agent_name", "agent_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), default="smart-eats", nullable=False)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("project", "prompt_name", "version", name="uq_prompt_versions_project_prompt_version"),
        Index("ix_prompt_versions_project", "project"),
        Index("ix_prompt_versions_prompt_name", "prompt_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), default="smart-eats", nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolVersion(Base):
    __tablename__ = "tool_versions"
    __table_args__ = (
        UniqueConstraint("project", "tool_name", "version", name="uq_tool_versions_project_tool_version"),
        Index("ix_tool_versions_project", "project"),
        Index("ix_tool_versions_tool_name", "tool_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), default="smart-eats", nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schema_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluatorDefinition(Base):
    __tablename__ = "evaluator_definitions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_evaluator_definitions_name_version"),
        Index("ix_evaluator_definitions_type_status", "evaluator_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluator_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(128), default="v1", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    rubric: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TraceSpan(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (
        UniqueConstraint("run_id", "span_index", name="uq_trace_spans_run_index"),
        Index("ix_trace_spans_trace_id", "trace_id"),
        Index("ix_trace_spans_session_id", "session_id"),
        Index("ix_trace_spans_span_type", "span_type"),
        Index("ix_trace_spans_started_at", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    span_index: Mapped[int] = mapped_column(Integer, default=0)
    span_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    score_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_status", "status"),
        Index("ix_experiments_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[str] = mapped_column(String(255), default="smart-eats")
    dataset_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluator_suite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "eval_run_id", name="uq_experiment_runs_experiment_eval_run"),
        Index("ix_experiment_runs_role", "role"),
        Index("ix_experiment_runs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    eval_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True)
    report_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="candidate")
    agent_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlaygroundRun(Base):
    __tablename__ = "playground_runs"
    __table_args__ = (
        Index("ix_playground_runs_created_at", "created_at"),
        Index("ix_playground_runs_owner", "owner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), default="smart-eats")
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    outputs_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    scores_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    trace_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimulationScenario(Base):
    __tablename__ = "simulation_scenarios"
    __table_args__ = (
        Index("ix_simulation_scenarios_status", "status"),
        Index("ix_simulation_scenarios_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[str] = mapped_column(String(255), default="smart-eats")
    scenario_json: Mapped[dict] = mapped_column(EvalJSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (
        Index("ix_simulation_runs_scenario", "scenario_id"),
        Index("ix_simulation_runs_status", "status"),
        Index("ix_simulation_runs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("simulation_scenarios.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    result_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    scores_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalDataset(Base):
    __tablename__ = "eval_datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_eval_datasets_name_version"),
        Index("ix_eval_datasets_suite_status", "suite", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="draft")
    suite: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalDatasetCase(Base):
    __tablename__ = "eval_dataset_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "case_id", name="uq_eval_dataset_cases_dataset_case"),
        Index("ix_eval_dataset_cases_case_id", "case_id"),
        Index("ix_eval_dataset_cases_source", "source"),
        Index("ix_eval_dataset_cases_review_status", "review_status"),
        Index("ix_eval_dataset_cases_scene_category", "scene", "category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    case_json: Mapped[dict] = mapped_column(EvalJSON, nullable=False)
    scene: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), default="draft")
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalCaseLineage(Base):
    __tablename__ = "eval_case_lineage"
    __table_args__ = (
        Index("ix_eval_case_lineage_source_run", "source_run_id"),
        Index("ix_eval_case_lineage_source_trace", "source_trace_id"),
        Index("ix_eval_case_lineage_target_case", "target_case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_case_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("eval_dataset_cases.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalOutcomeResult(Base):
    __tablename__ = "eval_outcome_results"
    __table_args__ = (
        Index("ix_eval_outcome_results_run_case", "run_id", "case_id"),
        Index("ix_eval_outcome_results_verifier", "verifier"),
        Index("ix_eval_outcome_results_passed", "passed"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=True)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trial_number: Mapped[int] = mapped_column(Integer, default=0)
    verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    failures_json: Mapped[list | None] = mapped_column(EvalJSON, nullable=True)
    details_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalJudgeResult(Base):
    __tablename__ = "eval_judge_results"
    __table_args__ = (
        Index("ix_eval_judge_results_run_case", "run_id", "case_id"),
        Index("ix_eval_judge_results_metric", "metric"),
        Index("ix_eval_judge_results_rubric", "rubric_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=True)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trial_number: Mapped[int] = mapped_column(Integer, default=0)
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rubric_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationAlert(Base):
    __tablename__ = "evaluation_alerts"
    __table_args__ = (
        Index("ix_evaluation_alerts_type_status", "alert_type", "status"),
        Index("ix_evaluation_alerts_severity_status", "severity", "status"),
        Index("ix_evaluation_alerts_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), default="warning")
    status: Mapped[str] = mapped_column(String(32), default="open")
    payload_json: Mapped[dict | None] = mapped_column(EvalJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # ── Notification tracking ──
    notification_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
