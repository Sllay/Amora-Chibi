from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from amora.events import PrivacyScope


class RiskLevel(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30


class ExecutionScope(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class PluginHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class PlannerMode(StrEnum):
    REFLEX = "reflex"
    RULE = "rule"
    HTN = "htn"
    LLM = "llm"


class PlanStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class ToolExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class MonitorOutcome(StrEnum):
    VERIFIED = "verified"
    RETRY = "retry"
    REPLAN = "replan"
    ASK = "ask"
    ABORT = "abort"


class ToolSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    permissions: set[str] = Field(default_factory=set)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    cost: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=120.0)
    idempotent: bool = True


class PluginManifest(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=64)
    permissions: set[str] = Field(default_factory=set)
    events: list[str] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cost: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=120.0)
    health: PluginHealth = PluginHealth.HEALTHY
    execution_scope: ExecutionScope = ExecutionScope.LOCAL


class ExecutionContext(BaseModel):
    granted_permissions: set[str] = Field(default_factory=set)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    max_risk: RiskLevel = RiskLevel.LOW
    max_total_cost: float = Field(default=0.25, ge=0.0, le=1.0)
    session_id: str | None = None


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=5)
    retry_on_timeout: bool = True
    retry_on_failure: bool = True
    on_exhausted: MonitorOutcome = MonitorOutcome.REPLAN


class ToolTemplate(BaseModel):
    plugin_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


class SkillDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(default="1", min_length=1, max_length=64)
    intention_kinds: set[str] = Field(default_factory=set)
    planner_mode: PlannerMode = PlannerMode.HTN
    steps: list[ToolTemplate] = Field(default_factory=list)
    description: str = Field(default="", max_length=500)


class PlanStep(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    order: int = Field(ge=0)
    plugin_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    status: StepStatus = StepStatus.PENDING
    attempt_count: int = Field(default=0, ge=0, le=50)
    last_error_code: str | None = None


class ActionPlan(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    intention_id: UUID
    intention_kind: str
    target_id: str | None = None
    skill_name: str | None = None
    planner_mode: PlannerMode
    steps: list[PlanStep] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.PENDING
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    dedupe_key: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    step_id: UUID
    intention_id: UUID
    plugin_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    dedupe_key: str | None = Field(default=None, max_length=256)
    created_at: datetime


class ToolExecutionResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    call_id: UUID
    plan_id: UUID
    step_id: UUID
    plugin_id: str
    tool_name: str
    status: ToolExecutionStatus
    attempt: int = Field(default=1, ge=1)
    output: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=1000)
    retryable: bool = False
    verified: bool = False
    monitor_outcome: MonitorOutcome | None = None
    started_at: datetime
    finished_at: datetime
    dedupe_key: str | None = Field(default=None, max_length=256)
    deduplicated: bool = False


class PlanExecutionResult(BaseModel):
    plan: ActionPlan
    executions: list[ToolExecutionResult] = Field(default_factory=list)
    monitor_outcome: MonitorOutcome
    deduplicated: bool = False
