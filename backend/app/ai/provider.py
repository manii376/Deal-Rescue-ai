"""AIProvider protocol. Provider SDK calls belong only in app/ai/providers/<name>.py."""

from typing import Protocol, runtime_checkable

from app.ai.schemas import (
    AIStatus,
    BriefingRequest,
    DealBriefing,
    FollowUpDraft,
    FollowUpRequest,
    ObjectionAnalysis,
    ObjectionAnalysisRequest,
    StrategyComparison,
    StrategyComparisonRequest,
)


class AIUnavailableError(Exception):
    """No usable provider (not configured, not implemented, unreachable). Safe to show."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AITransientError(Exception):
    """Temporary provider failure (rate limit, 5xx, timeout); AIService may retry."""


class AIOperationError(Exception):
    """Provider answered but the result is unusable (e.g. schema mismatch). Not retried."""


@runtime_checkable
class AIProvider(Protocol):
    name: str
    model: str | None

    async def status(self) -> AIStatus: ...

    async def generate_briefing(self, request: BriefingRequest) -> DealBriefing: ...

    async def analyze_objections(self, request: ObjectionAnalysisRequest) -> ObjectionAnalysis: ...

    async def draft_follow_up(self, request: FollowUpRequest) -> FollowUpDraft: ...

    async def compare_strategies(self, request: StrategyComparisonRequest) -> StrategyComparison: ...
