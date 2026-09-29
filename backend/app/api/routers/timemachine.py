"""Customer-scoped Deal Time Machine routes (TM1: recorded timeline and branch view).

Both routes are deterministic: SQLite records and M4 rules only. No model is called and nothing is written.
The branch view recalls memory only when asked (``include_memory=true``; one read from the customer's own bank).
Otherwise its memory status is ``not_requested``. The strategies route evaluates the fixed catalogue (TM2) at the
branch point: rule-derived, hypothetical alternatives with their evidence maps; nothing is predicted. A failed recall is reported as ``unavailable`` and the SQLite
view is still returned, as for briefings.
Ownership is checked first: an unknown customer, or a deal of another customer, is the usual 404.
"""

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from app.api.deps import MemoryDep, SessionDep, get_owned, require_customer
from app.api.errors import ValidationFailedError
from app.api.routers.intelligence import AsOf, IntelDep, _as_of
from app.domain.models import Deal
from app.timemachine.schemas import REF_PATTERN, BranchView, DealTimeline, StrategyCatalogue
from app.timemachine.strategies import InvalidAnchorError, build_strategy_catalogue
from app.timemachine.timeline import BranchAfterAsOfError, build_branch_view, build_timeline

router = APIRouter(tags=["time machine"])

BranchAt = Annotated[AwareDatetime, Query(description="Branch point (ISO 8601 with timezone); must be ≤ as_of.")]
AnchorRef = Annotated[str | None, Query(pattern=REF_PATTERN,
                                         description="address_requirement_early: the chosen anchor ref")]
IncludeMemory = Annotated[bool, Query(description="Recall linked memories known by branch_at (one Hindsight read).")]


@router.get("/api/customers/{customer_id}/deals/{deal_id}/timeline", response_model=DealTimeline,
            summary="Recorded deal timeline (deterministic; no model)")
def get_deal_timeline(customer_id: str, deal_id: str, session: SessionDep, intel: IntelDep,
                      as_of: AsOf = None) -> DealTimeline:
    require_customer(session, customer_id)
    get_owned(session, Deal, deal_id, customer_id, "Deal")
    return build_timeline(session, customer_id, deal_id, _as_of(as_of), intel.config)


@router.get("/api/customers/{customer_id}/deals/{deal_id}/timeline/branch", response_model=BranchView,
            summary="What was recorded at a point in time, and what the records show afterwards")
async def get_branch_view(customer_id: str, deal_id: str, session: SessionDep, intel: IntelDep, memory: MemoryDep,
                          branch_at: BranchAt, as_of: AsOf = None, include_memory: IncludeMemory = False) -> BranchView:
    require_customer(session, customer_id)
    get_owned(session, Deal, deal_id, customer_id, "Deal")
    try:
        return await build_branch_view(session, customer_id, deal_id, branch_at.astimezone(UTC),
                                       _as_of(as_of), intelligence=intel,
                                       memory=memory if include_memory else None)
    except BranchAfterAsOfError:
        raise ValidationFailedError("branch_at must not be after as_of") from None


@router.get("/api/customers/{customer_id}/deals/{deal_id}/time-machine/strategies", response_model=StrategyCatalogue,
            summary="Fixed strategy catalogue at a branch point, with rule-derived evidence maps (no model)")
async def get_strategy_catalogue(customer_id: str, deal_id: str, session: SessionDep, intel: IntelDep,
                                 memory: MemoryDep, branch_at: BranchAt, as_of: AsOf = None,
                                 anchor_ref: AnchorRef = None,
                                 include_memory: IncludeMemory = False) -> StrategyCatalogue:
    require_customer(session, customer_id)
    get_owned(session, Deal, deal_id, customer_id, "Deal")
    try:
        return await build_strategy_catalogue(session, customer_id, deal_id, branch_at.astimezone(UTC),
                                              _as_of(as_of), intelligence=intel,
                                              memory=memory if include_memory else None, anchor_ref=anchor_ref)
    except BranchAfterAsOfError:
        raise ValidationFailedError("branch_at must not be after as_of") from None
    except InvalidAnchorError:
        raise ValidationFailedError("anchor_ref is not a rep note, linked memory or commitment known at branch_at",
                                    code="invalid_anchor") from None
