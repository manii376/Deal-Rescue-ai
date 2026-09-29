"""AI provider adapters.

Implemented in M2
-----------------
* ``none`` (default): reports unavailable; every operation raises AIUnavailableError.
  No text is ever fabricated.

Planned (recognised by AI_PROVIDER but not implemented; status says so)
------------------------------------------------------------------------
* ``anthropic``: AnthropicProvider using the official ``anthropic`` SDK
  (``AsyncAnthropic``), model from ANTHROPIC_MODEL, structured outputs via
  ``client.messages.parse`` with the Pydantic response models in app/ai/schemas.py,
  timeout/retries from AI_TIMEOUT_SECONDS / AI_MAX_RETRIES. SDK calls live only in
  app/ai/providers/anthropic.py.
* ``ollama`` (implemented in M5 for briefings; see app/ai/providers/ollama.py). Original design:
    - HTTP to OLLAMA_BASE_URL (default http://127.0.0.1:11434) using ``httpx.AsyncClient``
      (already a dependency), ``POST /api/chat`` with ``stream: false`` and
      ``format: <JSON schema of the response model>`` for structured output;
    - model from OLLAMA_MODEL (required; no default so nothing is pulled implicitly);
    - ``status()`` calls ``GET /api/tags`` to confirm the server is up and the model exists;
    - responses validated with the same Pydantic models; invalid JSON -> AIOperationError.
  No local model is required until this adapter is implemented and selected.

Adding a provider = one module implementing AIProvider + one branch in build_ai_provider.
"""

from app.ai.provider import AIProvider, AIUnavailableError
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


class UnavailableProvider:
    """Used when no provider is configured, or the configured one is not implemented yet."""

    def __init__(self, name: str, reason: str, implemented: bool, model: str | None = None):
        self.name = name
        self.model = model
        self._reason = reason
        self._implemented = implemented

    async def status(self) -> AIStatus:
        return AIStatus(provider=self.name, implemented=self._implemented, available=False,
                        model=self.model, reason=self._reason)

    async def generate_briefing(self, request: BriefingRequest) -> DealBriefing:
        raise AIUnavailableError(self._reason)

    async def analyze_objections(self, request: ObjectionAnalysisRequest) -> ObjectionAnalysis:
        raise AIUnavailableError(self._reason)

    async def draft_follow_up(self, request: FollowUpRequest) -> FollowUpDraft:
        raise AIUnavailableError(self._reason)

    async def compare_strategies(self, request: StrategyComparisonRequest) -> StrategyComparison:
        raise AIUnavailableError(self._reason)


def build_ai_provider(settings) -> AIProvider:
    if settings.ai_provider == "anthropic":
        reason = "AI_PROVIDER=anthropic is planned but not implemented in M2."
        if settings.anthropic_api_key is None:
            reason += " ANTHROPIC_API_KEY is also not set."
        return UnavailableProvider("anthropic", reason, implemented=False, model=settings.anthropic_model)
    if settings.ai_provider == "ollama":
        from app.ai.providers.ollama import OllamaProvider

        return OllamaProvider(settings.ollama_base_url, settings.ollama_model, num_ctx=settings.ollama_num_ctx,
                              think=settings.ollama_think, timeout_seconds=settings.ai_timeout_seconds)
    return UnavailableProvider("none", "No AI provider configured (AI_PROVIDER=none). "
                                       "AI features are unavailable; nothing is generated.", implemented=True)
