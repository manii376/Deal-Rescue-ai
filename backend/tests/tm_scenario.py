"""Fixed-timestamp records for Deal Time Machine tests (written directly, so created_at is controlled).

Mirrors the synthetic Aurora case: the deal record is entered (09-28) after its first call (09-20).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlmodel import Session

from app.domain.models import Commitment, Customer, Deal, Interaction, Stakeholder

AS_OF = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
CALL_AT = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
NOTE_AT = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)          # channel "note": not meaningful activity
FUTURE_CALL_AT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)   # after AS_OF
C1_CREATED = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
C1_DUE = date(2026, 9, 26)
C1_DONE = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
C3_CREATED = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
LATE_COMMITMENT_CREATED = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)  # after AS_OF
STAKEHOLDER_CREATED = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)
DEAL_ENTERED = datetime(2026, 9, 28, 16, 17, tzinfo=UTC)
BRANCH_AT = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@dataclass
class Scenario:
    customer: str
    deal: str
    call: str
    note: str
    future_call: str
    c1: str
    c3: str
    late_commitment: str
    stakeholder: str
    other_customer: str
    other_deal: str
    other_call: str
    empty_deal: str


def add(session: Session, *records):
    for r in records:
        session.add(r)
    session.commit()
    for r in records:
        session.refresh(r)
    return records


def build(engine) -> Scenario:
    with Session(engine) as s:
        (a, b) = add(s, Customer(name="Aurora Logistics", is_synthetic=True, created_at=datetime(2026, 9, 1, tzinfo=UTC)),
                     Customer(name="Borealis Foods", is_synthetic=True, created_at=datetime(2026, 9, 1, tzinfo=UTC)))
        (deal, empty, other_deal) = add(
            s, Deal(customer_id=a.id, title="Pune warehouse pilot", stage="proposal", created_at=DEAL_ENTERED),
            Deal(customer_id=a.id, title="Empty deal", stage="discovery", created_at=datetime(2026, 9, 10, tzinfo=UTC)),
            Deal(customer_id=b.id, title="Borealis deal", stage="proposal", created_at=datetime(2026, 9, 2, tzinfo=UTC)))
        (call, note, future, other_call) = add(
            s, Interaction(customer_id=a.id, deal_id=deal.id, occurred_at=CALL_AT, channel="call", title="Budget call",
                           notes="CFO said the budget is capped at USD 42,000.", created_at=DEAL_ENTERED),
            Interaction(customer_id=a.id, deal_id=deal.id, occurred_at=NOTE_AT, channel="note",
                        notes="LATER-NOTE: internal reminder.", created_at=NOTE_AT),
            Interaction(customer_id=a.id, deal_id=deal.id, occurred_at=FUTURE_CALL_AT, channel="call",
                        notes="FUTURE-CALL scheduled.", created_at=NOTE_AT),
            Interaction(customer_id=b.id, deal_id=other_deal.id, occurred_at=CALL_AT, channel="call",
                        notes="BOREALIS-SECRET note.", created_at=CALL_AT))
        (c1, c3, late) = add(
            s, Commitment(customer_id=a.id, deal_id=deal.id, description="Send SOC 2 report", owner_party="us",
                          due_date=C1_DUE, status="done", completed_at=C1_DONE, created_at=C1_CREATED),
            Commitment(customer_id=a.id, deal_id=deal.id, description="Share pricing sheet", owner_party="us",
                       status="cancelled", created_at=C3_CREATED),
            Commitment(customer_id=a.id, deal_id=deal.id, description="LATE-COMMITMENT", owner_party="customer",
                       due_date=date(2026, 10, 10), created_at=LATE_COMMITMENT_CREATED))
        (stk,) = add(s, Stakeholder(customer_id=a.id, name="Priya Raman", role="CFO", created_at=STAKEHOLDER_CREATED))
        return Scenario(customer=a.id, deal=deal.id, call=call.id, note=note.id, future_call=future.id, c1=c1.id,
                        c3=c3.id, late_commitment=late.id, stakeholder=stk.id, other_customer=b.id,
                        other_deal=other_deal.id, other_call=other_call.id, empty_deal=empty.id)
