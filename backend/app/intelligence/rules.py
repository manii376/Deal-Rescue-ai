"""Deterministic deal-intelligence rules.

Pure functions over plain snapshots: no database access, no clock, no randomness, no
LLM and no memory recall. The same snapshots, configuration and reference time always
produce the same signals in the same order.

Time semantics
--------------
* ``as_of`` is an aware datetime (the reference time). Elapsed-time rules (inactivity)
  compare durations: a deal is stalled when ``as_of - last_activity >= threshold days``.
* Date-only fields (commitment ``due_date``, deal ``expected_close_date``) are compared with
  the *business date*: ``as_of`` converted to ``INTEL_TIMEZONE``. Something due on the
  business date is "due today", not overdue.
* Interactions dated after ``as_of`` are excluded from activity and reported as
  ``future_dated_activity`` (data quality). ``occurred_at`` is required by the schema, so a
  "missing activity timestamp" means there is no qualifying interaction at all, which is
  reported as ``no_recorded_activity`` (missing information), never as a stall.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.intelligence.schemas import (
    ActivitySummary,
    CommitmentSummary,
    RuleDescription,
    RuleRef,
    Signal,
    SourceRef,
)

TERMINAL_STATUSES = frozenset({"won", "lost", "no_decision"})
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
NATURE_RANK = {"finding": 0, "missing_information": 1, "data_quality": 2}


# -- configuration and snapshots ------------------------------------------------------


@dataclass(frozen=True)
class IntelConfig:
    stall_days_default: int = 14
    stall_days_by_stage: Mapping[str, int] = field(default_factory=lambda: {
        "discovery": 21, "qualification": 14, "proposal": 10, "negotiation": 7, "closing": 5})
    meaningful_channels: frozenset[str] = frozenset({"call", "meeting", "email", "message"})
    due_soon_days: int = 7
    timezone: str = "UTC"

    @classmethod
    def from_settings(cls, settings) -> IntelConfig:
        return cls(
            stall_days_default=settings.intel_stall_days_default,
            stall_days_by_stage=dict(settings.intel_stall_days_by_stage),
            meaningful_channels=frozenset(settings.intel_meaningful_channels),
            due_soon_days=settings.intel_due_soon_days,
            timezone=settings.intel_timezone,
        )

    def stall_days(self, stage: str) -> int:
        return self.stall_days_by_stage.get(stage, self.stall_days_default)

    def business_date(self, as_of: datetime) -> date:
        return as_of.astimezone(ZoneInfo(self.timezone)).date()

    def as_dict(self) -> dict[str, object]:
        return {
            "stall_days_default": self.stall_days_default,
            "stall_days_by_stage": dict(sorted(self.stall_days_by_stage.items())),
            "meaningful_channels": sorted(self.meaningful_channels),
            "due_soon_days": self.due_soon_days,
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class DealSnap:
    id: str
    customer_id: str
    title: str
    stage: str
    status: str
    value_minor: int | None
    currency: str | None
    expected_close_date: date | None
    owner_name: str | None
    created_at: datetime


@dataclass(frozen=True)
class InteractionSnap:
    id: str
    deal_id: str | None
    channel: str
    occurred_at: datetime


@dataclass(frozen=True)
class CommitmentSnap:
    id: str
    deal_id: str
    description: str
    owner_party: str
    owner_name: str | None
    due_date: date | None
    status: str
    source_interaction_id: str | None


@dataclass
class DealEvaluation:
    deal: DealSnap
    signals: list[Signal]
    activity: ActivitySummary
    commitments: CommitmentSummary


# -- helpers --------------------------------------------------------------------------


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _whole_days(delta: timedelta) -> int:
    return delta // timedelta(days=1)


def _signal(*, type: str, category: str, nature: str, severity: str, deal: DealSnap | None, customer_id: str,
            primary_id: str, title: str, explanation: str, description: str, thresholds: dict | None = None,
            facts: dict | None = None, sources: list[SourceRef], urgency: int = 0) -> Signal:
    return Signal(
        id=f"{type}:{primary_id}", type=type, category=category, nature=nature, severity=severity,
        customer_id=customer_id, deal_id=deal.id if deal else None, title=title, explanation=explanation,
        rule=RuleRef(id=type, description=description, thresholds=thresholds or {}),
        facts=facts or {}, sources=sources, urgency=urgency,
    )


def priority_key(signal: Signal) -> tuple:
    """Documented ordering: severity, then nature, then urgency, then stable ids."""
    return (-SEVERITY_RANK[signal.severity], NATURE_RANK[signal.nature], -signal.urgency,
            signal.deal_id or "", signal.type, signal.id)


def classify_commitment(c: CommitmentSnap, today: date, cfg: IntelConfig) -> str:
    """done | cancelled | undated | overdue | due_soon | open (due later)."""
    if c.status in ("done", "cancelled"):
        return c.status
    if c.due_date is None:
        return "undated"
    if c.due_date < today:
        return "overdue"
    if (c.due_date - today).days <= cfg.due_soon_days:
        return "due_soon"
    return "open"


# -- rules ----------------------------------------------------------------------------


def evaluate_deal(deal: DealSnap, interactions: Iterable[InteractionSnap], commitments: Iterable[CommitmentSnap],
                  as_of: datetime, cfg: IntelConfig) -> DealEvaluation:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    today = cfg.business_date(as_of)
    closed = deal.status in TERMINAL_STATUSES
    signals: list[Signal] = []
    deal_src = SourceRef(type="deal", id=deal.id)

    # --- activity ---------------------------------------------------------------
    interactions = list(interactions)
    future = sorted((i for i in interactions if i.occurred_at > as_of), key=lambda i: (i.occurred_at, i.id))
    past = [i for i in interactions if i.occurred_at <= as_of]
    meaningful = [i for i in past if i.channel in cfg.meaningful_channels]
    last = max(meaningful, key=lambda i: (i.occurred_at, i.id)) if meaningful else None
    threshold = None if closed else cfg.stall_days(deal.stage)
    days_since = _whole_days(as_of - last.occurred_at) if last else None

    if future:
        signals.append(_signal(
            type="future_dated_activity", category="data_quality", nature="data_quality", severity="low",
            deal=deal, customer_id=deal.customer_id, primary_id=deal.id,
            title=f"{len(future)} interaction(s) dated after the reference time",
            explanation=(f"{len(future)} interaction(s) on this deal are dated after {as_of.isoformat()} and were "
                         "ignored for activity rules. Check their dates."),
            description="Interactions with occurred_at later than the reference time are excluded and reported.",
            facts={"count": len(future), "earliest_future_at": _iso(future[0].occurred_at),
                   "as_of": _iso(as_of)},
            sources=[deal_src] + [SourceRef(type="interaction", id=i.id) for i in future]))

    if not closed and threshold is not None:
        channels = ", ".join(sorted(cfg.meaningful_channels))
        if last is None:
            age = max(as_of - deal.created_at, timedelta(0))
            age_days = _whole_days(age)
            old_enough = age >= timedelta(days=threshold)
            signals.append(_signal(
                type="no_recorded_activity", category="activity", nature="missing_information",
                severity="medium" if old_enough else "low", deal=deal, customer_id=deal.customer_id,
                primary_id=deal.id, title="No meaningful activity recorded",
                explanation=(f"No {channels} interaction is recorded for this deal "
                             f"(deal created {age_days} day(s) before the reference time). Activity cannot be "
                             "assessed; this is missing information, not evidence of a stall."),
                description="Open deal with no recorded meaningful interaction at or before the reference time.",
                thresholds={"stall_days": threshold, "stage": deal.stage, "meaningful_channels": channels},
                facts={"deal_created_at": _iso(deal.created_at), "deal_age_days": age_days,
                       "non_meaningful_interactions": len(past) - len(meaningful)},
                sources=[deal_src], urgency=age_days))
        elif as_of - last.occurred_at >= timedelta(days=threshold):
            severe = as_of - last.occurred_at >= timedelta(days=2 * threshold)
            signals.append(_signal(
                type="stalled_deal", category="activity", nature="finding",
                severity="high" if severe else "medium", deal=deal, customer_id=deal.customer_id,
                primary_id=deal.id, title=f"No meaningful activity for {days_since} days",
                explanation=(f"The last meaningful interaction ({last.channel}) was {days_since} day(s) before the "
                             f"reference time; the threshold for stage '{deal.stage}' is {threshold} day(s)"
                             + (" and it is exceeded twice over." if severe else ".")),
                description="Open deal whose latest meaningful interaction is at least the stage threshold old.",
                thresholds={"stall_days": threshold, "stage": deal.stage, "high_after_days": 2 * threshold,
                            "meaningful_channels": channels},
                facts={"last_meaningful_activity_at": _iso(last.occurred_at), "days_inactive": days_since,
                       "as_of": _iso(as_of)},
                sources=[deal_src, SourceRef(type="interaction", id=last.id)],
                urgency=days_since - threshold))

    # --- deal fields (open deals only) --------------------------------------------
    if not closed:
        if deal.expected_close_date is None:
            signals.append(_signal(
                type="missing_expected_close_date", category="deal", nature="missing_information", severity="low",
                deal=deal, customer_id=deal.customer_id, primary_id=deal.id, title="No expected close date",
                explanation="The deal has no expected close date, so timing cannot be assessed.",
                description="Open deal without expected_close_date.", sources=[deal_src]))
        elif deal.expected_close_date < today:
            days_past = (today - deal.expected_close_date).days
            signals.append(_signal(
                type="close_date_passed", category="deal", nature="finding", severity="medium",
                deal=deal, customer_id=deal.customer_id, primary_id=deal.id,
                title=f"Expected close date passed {days_past} day(s) ago",
                explanation=(f"The expected close date {deal.expected_close_date.isoformat()} is before the business "
                             f"date {today.isoformat()} ({cfg.timezone}) and the deal is still open."),
                description="Open deal whose expected_close_date is before the business date.",
                thresholds={"timezone": cfg.timezone},
                facts={"expected_close_date": _iso(deal.expected_close_date), "business_date": _iso(today),
                       "days_past": days_past},
                sources=[deal_src], urgency=days_past))
        if deal.value_minor is None:
            signals.append(_signal(
                type="missing_deal_value", category="deal", nature="missing_information", severity="low",
                deal=deal, customer_id=deal.customer_id, primary_id=deal.id, title="No deal value",
                explanation="The deal has no recorded value.", description="Open deal without value_minor.",
                sources=[deal_src]))
        if not deal.owner_name:
            signals.append(_signal(
                type="missing_deal_owner", category="deal", nature="missing_information", severity="low",
                deal=deal, customer_id=deal.customer_id, primary_id=deal.id, title="No deal owner",
                explanation="No owner is recorded for this deal.", description="Open deal without owner_name.",
                sources=[deal_src]))

    # --- commitments ----------------------------------------------------------------
    counts = {"open": 0, "overdue": 0, "due_soon": 0, "undated": 0, "done": 0, "cancelled": 0}
    for c in sorted(commitments, key=lambda c: c.id):
        cls = classify_commitment(c, today, cfg)
        counts[cls] += 1  # "open" here means open and due after the due-soon window
        if cls in ("done", "cancelled"):
            continue
        sources = [SourceRef(type="commitment", id=c.id), deal_src]
        if c.source_interaction_id:
            sources.append(SourceRef(type="interaction", id=c.source_interaction_id))
        owner = c.owner_name or ("we" if c.owner_party == "us" else "the customer")
        facts = {"description": c.description, "owner_party": c.owner_party, "owner_name": c.owner_name,
                 "due_date": _iso(c.due_date), "business_date": _iso(today)}
        if closed:
            signals.append(_signal(
                type="open_commitment_on_closed_deal", category="unresolved_work", nature="finding", severity="low",
                deal=deal, customer_id=deal.customer_id, primary_id=c.id,
                title=f"Open commitment on a {deal.status} deal",
                explanation=(f"'{c.description}' ({owner}) is still open although the deal is {deal.status}. "
                             "Close or cancel it."),
                description="Open commitment on a deal whose status is won, lost or no_decision.",
                facts={**facts, "deal_status": deal.status, "past_due": cls == "overdue"}, sources=sources))
        elif cls == "undated":
            signals.append(_signal(
                type="commitment_undated", category="commitment", nature="missing_information", severity="low",
                deal=deal, customer_id=deal.customer_id, primary_id=c.id, title="Open commitment without a deadline",
                explanation=f"'{c.description}' ({owner}) has no due date, so it cannot be tracked for lateness.",
                description="Open commitment with no due_date.", facts=facts, sources=sources))
        elif cls == "overdue":
            days_over = (today - c.due_date).days
            signals.append(_signal(
                type="commitment_overdue", category="commitment", nature="finding",
                severity="high" if c.owner_party == "us" else "medium", deal=deal, customer_id=deal.customer_id,
                primary_id=c.id, title=f"Commitment overdue by {days_over} day(s)",
                explanation=(f"'{c.description}' ({owner}) was due {c.due_date.isoformat()}; the business date is "
                             f"{today.isoformat()} ({cfg.timezone})."),
                description="Open commitment whose due_date is before the business date.",
                thresholds={"timezone": cfg.timezone},
                facts={**facts, "days_overdue": days_over}, sources=sources, urgency=days_over))
        elif cls == "due_soon":
            days_left = (c.due_date - today).days
            signals.append(_signal(
                type="commitment_due_soon", category="commitment", nature="finding",
                severity="medium" if days_left <= 1 else "low", deal=deal, customer_id=deal.customer_id,
                primary_id=c.id,
                title="Commitment due today" if days_left == 0 else f"Commitment due in {days_left} day(s)",
                explanation=(f"'{c.description}' ({owner}) is due {c.due_date.isoformat()}, within the "
                             f"{cfg.due_soon_days}-day window."),
                description="Open commitment due between the business date and business date + due_soon_days.",
                thresholds={"due_soon_days": cfg.due_soon_days, "timezone": cfg.timezone},
                facts={**facts, "days_until_due": days_left}, sources=sources,
                urgency=cfg.due_soon_days - days_left))

    open_total = sum(counts[k] for k in ("overdue", "due_soon", "undated", "open"))
    summary = CommitmentSummary(open=open_total, overdue=counts["overdue"], due_soon=counts["due_soon"],
                                undated=counts["undated"], done=counts["done"], cancelled=counts["cancelled"])
    activity = ActivitySummary(
        last_meaningful_activity_at=last.occurred_at if last else None,
        last_meaningful_interaction_id=last.id if last else None,
        days_since_meaningful_activity=days_since, stall_threshold_days=threshold,
        meaningful_interaction_count=len(meaningful),
        non_meaningful_interaction_count=len(past) - len(meaningful),
        future_dated_interaction_count=len(future),
    )
    return DealEvaluation(deal=deal, signals=sorted(signals, key=priority_key), activity=activity,
                          commitments=summary)


def evaluate_customer_level(customer_id: str, stakeholder_count: int, has_open_deal: bool) -> list[Signal]:
    if stakeholder_count == 0 and has_open_deal:
        return [_signal(
            type="no_stakeholders_recorded", category="deal", nature="missing_information", severity="low",
            deal=None, customer_id=customer_id, primary_id=customer_id, title="No stakeholders recorded",
            explanation="The customer has open deals but no stakeholders; decision-makers cannot be assessed.",
            description="Customer with at least one open deal and zero stakeholders.",
            sources=[SourceRef(type="customer", id=customer_id)])]
    return []


# -- documentation ----------------------------------------------------------------------

PRIORITY_ORDER = [
    "1. severity: high > medium > low",
    "2. nature: finding > missing_information > data_quality",
    "3. urgency (rule-specific, larger first; see each rule)",
    "4. deal_id, then signal type, then signal id (stable tie-break)",
]

RULE_CATALOG = [
    RuleDescription(id="stalled_deal", category="activity", nature="finding", applies_to="open deals",
                    condition="as_of - latest meaningful interaction (occurred_at <= as_of) >= stall days for the stage",
                    severity_policy="high if >= 2 x stall days, else medium",
                    urgency="days inactive - stall days"),
    RuleDescription(id="no_recorded_activity", category="activity", nature="missing_information",
                    applies_to="open deals",
                    condition="no meaningful interaction at or before as_of",
                    severity_policy="medium if the deal is at least stall days old, else low",
                    urgency="deal age in days"),
    RuleDescription(id="commitment_overdue", category="commitment", nature="finding",
                    applies_to="open commitments on open deals",
                    condition="due_date < business date (as_of in INTEL_TIMEZONE)",
                    severity_policy="high when owner_party is 'us', medium when 'customer'",
                    urgency="days overdue"),
    RuleDescription(id="commitment_due_soon", category="commitment", nature="finding",
                    applies_to="open commitments on open deals",
                    condition="0 <= due_date - business date <= due_soon_days (due today is not overdue)",
                    severity_policy="medium if due today or tomorrow, else low",
                    urgency="due_soon_days - days until due"),
    RuleDescription(id="commitment_undated", category="commitment", nature="missing_information",
                    applies_to="open commitments on open deals", condition="due_date is empty",
                    severity_policy="low", urgency="0"),
    RuleDescription(id="close_date_passed", category="deal", nature="finding", applies_to="open deals",
                    condition="expected_close_date < business date", severity_policy="medium",
                    urgency="days past the expected close date"),
    RuleDescription(id="open_commitment_on_closed_deal", category="unresolved_work", nature="finding",
                    applies_to="open commitments on won/lost/no_decision deals",
                    condition="commitment status is open", severity_policy="low", urgency="0"),
    RuleDescription(id="missing_expected_close_date", category="deal", nature="missing_information",
                    applies_to="open deals", condition="expected_close_date is empty", severity_policy="low",
                    urgency="0"),
    RuleDescription(id="missing_deal_value", category="deal", nature="missing_information", applies_to="open deals",
                    condition="value_minor is empty", severity_policy="low", urgency="0"),
    RuleDescription(id="missing_deal_owner", category="deal", nature="missing_information", applies_to="open deals",
                    condition="owner_name is empty", severity_policy="low", urgency="0"),
    RuleDescription(id="no_stakeholders_recorded", category="deal", nature="missing_information",
                    applies_to="customers with at least one open deal", condition="zero stakeholders",
                    severity_policy="low", urgency="0"),
    RuleDescription(id="future_dated_activity", category="data_quality", nature="data_quality",
                    applies_to="all deals", condition="interaction occurred_at > as_of (excluded from activity)",
                    severity_policy="low", urgency="0"),
]

NOT_INCLUDED = [
    "No win probability, deal score or prediction of whether a deal will close.",
    "No hidden-objection, sentiment or intent inference (deterministic rules only).",
    "No LLM calls and no Hindsight recall; only SQLite records are read.",
    "No history reconstruction: as_of sets the reference time, records are evaluated as currently stored.",
    "Interactions without a deal are not counted as deal activity.",
]
