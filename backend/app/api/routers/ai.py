"""Customer-scoped AI endpoints (M5 slice: deal briefing only).

Order of operations for POST /api/customers/{cid}/deals/{did}/ai/briefing:
  1. ownership: the customer exists and owns the deal (404 otherwise, as elsewhere);
  2. AI availability: 503 ``ai_unavailable`` before any evidence work if the provider is down;
  3. deterministic evidence assembly (app/ai/evidence.py): this customer's records, M4 signals,
     rep notes and only linked, current Hindsight memories of this deal;
  4. insufficient evidence -> 200 with status "insufficient_evidence" and NO model call;
  5. generation through AIService (evidence rules + grounding check);
  6. response with every evidence source resolved to safe metadata of this customer's records.

Nothing is persisted; there is no free-form question input.

POST /api/customers/{cid}/deals/{did}/ai/time-machine/assess (TM4) assesses ONE fixed Deal Time Machine strategy at a
branch point; see app/timemachine/assessment.py for its order of operations. Same error conventions as the briefing:
404 ownership, 422 validation_error / strategy_not_applicable / invalid_anchor, 503 ai_unavailable, 502
ai_provider_error. Nothing is persisted.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ai.evidence import EvidenceSource, MemoryEvidenceStatus, assemble_briefing_evidence
from app.ai.provider import AIOperationError, AIUnavailableError
from app.ai.schemas import DealBriefing
from app.api.deps import AIDep, MemoryDep, SessionDep, get_owned, require_customer
from app.api.errors import AppError, ServiceUnavailableError, ValidationFailedError
from app.api.routers.intelligence import AsOf, IntelDep, _as_of
from app.domain.models import Deal
from app.timemachine.assessment import (
    AnchorRequiredError,
    BranchAfterAsOfError,
    InvalidAnchorError,
    StrategyNotApplicableError,
    assess_strategy,
)
from app.timemachine.schemas import TimeMachineAssessment, TimeMachineAssessRequest

router = APIRouter(tags=["ai"])

GROUNDING_NOTE = (
    "Claims are checked against their cited evidence: unknown citations are removed, and claims whose "
    "numbers or names do not appear in their evidence are marked unsupported. This check is lexical, not "
    "proof: verify claims against the cited source records. Inferences are unconfirmed. Rep notes are the "
    "sales rep's account, not the customer's words."
)


class AIProviderError(AppError):
    status_code = 502
    code = "ai_provider_error"


class BriefingResponse(BaseModel):
    status: Literal["generated", "insufficient_evidence"]
    customer_id: str
    deal_id: str
    as_of: datetime
    briefing: DealBriefing | None = Field(description="None when status is insufficient_evidence")
    sources: list[EvidenceSource] = Field(description="Every evidence item the model could cite, resolved")
    memory: MemoryEvidenceStatus
    insufficient_reason: str | None = None
    note: str = GROUNDING_NOTE


def _unavailable(reason: str) -> ServiceUnavailableError:
    return ServiceUnavailableError(reason or "AI provider unavailable", code="ai_unavailable")


@router.post("/api/customers/{customer_id}/deals/{deal_id}/ai/briefing", response_model=BriefingResponse,
             summary="Evidence-grounded deal briefing (generated on request, never stored)")
async def generate_deal_briefing(customer_id: str, deal_id: str, session: SessionDep, ai: AIDep,
                                 memory: MemoryDep, intel: IntelDep, as_of: AsOf = None) -> BriefingResponse:
    require_customer(session, customer_id)
    get_owned(session, Deal, deal_id, customer_id, "Deal")

    status = await ai.status()
    if not status.available:
        raise _unavailable(status.reason)

    reference = _as_of(as_of)
    pack = await assemble_briefing_evidence(session, customer_id, deal_id, reference, memory=memory,
                                            intelligence=intel)
    if not pack.sufficient:
        return BriefingResponse(status="insufficient_evidence", customer_id=customer_id, deal_id=deal_id,
                                as_of=reference, briefing=None, sources=pack.sources, memory=pack.memory,
                                insufficient_reason=pack.insufficient_reason)
    try:
        briefing = await ai.generate_briefing(pack.request)
    except AIUnavailableError as exc:
        raise _unavailable(exc.reason) from None
    except AIOperationError as exc:
        raise AIProviderError(str(exc)) from None
    return BriefingResponse(status="generated", customer_id=customer_id, deal_id=deal_id, as_of=reference,
                            briefing=briefing, sources=pack.sources, memory=pack.memory)


@router.post("/api/customers/{customer_id}/deals/{deal_id}/ai/time-machine/assess",
             response_model=TimeMachineAssessment,
             summary="AI assessment of one fixed Deal Time Machine strategy at a branch point (hypothetical; not saved)")
async def assess_time_machine_strategy(customer_id: str, deal_id: str, body: TimeMachineAssessRequest,
                                       session: SessionDep, ai: AIDep, memory: MemoryDep,
                                       intel: IntelDep) -> TimeMachineAssessment:
    require_customer(session, customer_id)
    get_owned(session, Deal, deal_id, customer_id, "Deal")
    try:
        return await assess_strategy(session, customer_id, deal_id, body, _as_of(body.as_of), intelligence=intel,
                                     ai=ai, memory=memory if body.include_memory else None)
    except BranchAfterAsOfError:
        raise ValidationFailedError("branch_at must not be after as_of") from None
    except StrategyNotApplicableError:
        raise ValidationFailedError("This strategy is not offered at this branch point",
                                    code="strategy_not_applicable") from None
    except AnchorRequiredError:
        raise ValidationFailedError("This strategy needs an anchor_ref chosen from its anchor candidates",
                                    code="invalid_anchor") from None
    except InvalidAnchorError:
        raise ValidationFailedError("anchor_ref is not a rep note, linked memory or commitment known at branch_at",
                                    code="invalid_anchor") from None
    except AIUnavailableError as exc:
        raise _unavailable(exc.reason) from None
    except AIOperationError as exc:
        raise AIProviderError(str(exc)) from None
