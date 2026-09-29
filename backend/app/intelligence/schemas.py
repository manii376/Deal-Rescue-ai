"""Typed output of the deterministic deal-intelligence engine.

Every signal is derived from recorded SQLite facts by a named rule; nothing here is
inferred by a model, and nothing is a prediction.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

RULES_VERSION = "m4.1"

Severity = Literal["high", "medium", "low"]
# finding: a negative fact about the deal. missing_information: we cannot assess something
# because a record/field is absent (not evidence of a problem). data_quality: records that
# look wrong and were excluded or should be fixed.
Nature = Literal["finding", "missing_information", "data_quality"]
Category = Literal["activity", "commitment", "deal", "unresolved_work", "data_quality"]
SignalType = Literal[
    "stalled_deal",
    "no_recorded_activity",
    "commitment_overdue",
    "commitment_due_soon",
    "commitment_undated",
    "close_date_passed",
    "open_commitment_on_closed_deal",
    "missing_expected_close_date",
    "missing_deal_value",
    "missing_deal_owner",
    "no_stakeholders_recorded",
    "future_dated_activity",
]
SourceType = Literal["customer", "deal", "interaction", "commitment"]

FactValue = str | int | bool | None


class SourceRef(BaseModel):
    type: SourceType
    id: str


class RuleRef(BaseModel):
    id: SignalType
    version: str = RULES_VERSION
    description: str
    thresholds: dict[str, FactValue] = Field(default_factory=dict)


class Signal(BaseModel):
    id: str = Field(description="Deterministic: '<type>:<primary record id>'")
    type: SignalType
    category: Category
    nature: Nature
    severity: Severity
    customer_id: str
    deal_id: str | None
    title: str
    explanation: str
    rule: RuleRef
    facts: dict[str, FactValue] = Field(default_factory=dict,
                                        description="Dates/times as ISO strings; the values the rule compared")
    sources: list[SourceRef]
    urgency: int = Field(0, description="Rule-specific tie-breaker within a severity (see /api/intelligence/rules)")


class SignalList(BaseModel):
    as_of: datetime
    business_date: date
    timezone: str
    rules_version: str = RULES_VERSION
    items: list[Signal]
    total: int
    limit: int
    offset: int


class ActivitySummary(BaseModel):
    last_meaningful_activity_at: datetime | None
    last_meaningful_interaction_id: str | None
    days_since_meaningful_activity: int | None
    stall_threshold_days: int | None  # None for closed deals
    meaningful_interaction_count: int
    non_meaningful_interaction_count: int
    future_dated_interaction_count: int


class CommitmentSummary(BaseModel):
    open: int
    overdue: int
    due_soon: int
    undated: int
    done: int
    cancelled: int


class DealIntelligence(BaseModel):
    as_of: datetime
    business_date: date
    timezone: str
    rules_version: str = RULES_VERSION
    customer_id: str
    deal_id: str
    title: str
    stage: str
    status: str
    is_closed: bool
    highest_severity: Severity | None
    signal_counts: dict[Severity, int]
    activity: ActivitySummary
    commitments: CommitmentSummary
    signals: list[Signal]
    customer_signals: list[Signal] = Field(default_factory=list,
                                           description="Customer-level signals that affect this deal")


class DealAttentionSummary(BaseModel):
    deal_id: str
    title: str
    stage: str
    status: str
    highest_severity: Severity | None
    signal_counts: dict[Severity, int]
    signal_types: list[SignalType]
    last_meaningful_activity_at: datetime | None


class DealAttentionList(BaseModel):
    as_of: datetime
    business_date: date
    timezone: str
    rules_version: str = RULES_VERSION
    items: list[DealAttentionSummary]
    total: int
    limit: int
    offset: int


class RuleDescription(BaseModel):
    id: SignalType
    category: Category
    nature: Nature
    applies_to: str
    condition: str
    severity_policy: str
    urgency: str


class RuleCatalog(BaseModel):
    rules_version: str = RULES_VERSION
    thresholds: dict[str, object]
    priority_order: list[str]
    rules: list[RuleDescription]
    not_included: list[str]
