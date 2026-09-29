"""AIService: provider-independent wrapper that enforces timeouts, bounded retries and
the product's evidence rules on every provider's output:

* citations must refer to evidence supplied in the request; others are removed and
  listed in ``rejected_citations``;
* a claim labelled "recorded"/"rep_note"/"statement" must cite evidence of a matching kind,
  otherwise it is relabelled "inference" (never present a guess as a fact). Rep notes are
  never customer statements: "statement" needs verbatim-statement evidence;
* a "recorded"/"rep_note" claim that appends a conclusion to its fact through an explicit
  consequence connector (", making ...", "; therefore ...") is split into the fact (original kind)
  and the conclusion (kind "inference"); see app/ai/claim_checks.py. Both parts then go through the
  remaining checks, and each split counts as one relabelled claim;
* briefing claims must pass the lexical grounding check (app/ai/grounding.py): numbers and
  proper names must occur in the cited evidence (or, for uncited claims, in any evidence).
  Failing claims become kind "unsupported" with their citations removed and the reasons in
  ``grounding_issues``. Passing is NOT proof of support; the UI shows the source records;
* a strategy verdict of "supported" or "mixed" needs at least one valid supporting
  citation, otherwise it becomes "unsupported" (verdicts are only ever weakened);
* a sentence that concludes something about the customer side (contact, decision-maker) from a missing seller-side
  deal owner is split (claim_checks.owner_absence_conclusion): the owner fact goes through the normal checks; the
  conclusion is not supported by the owner gap (briefing: kind "unsupported"; strategy item: marked, uncited);
* strategy items (Deal Time Machine, plan §7): supporting/contradicting items must cite known evidence and pass the
  grounding check; a quotation needs a cited customer statement; "no X" must be "no X recorded". Failing items are
  kept with citations removed and the reasons in ``issues``. Items with outcome, probability or certainty language
  are removed (reason in ``check_notes``), as are such gaps and next steps; next steps must also pass grounding.
  Without recorded outcome evidence, "supported" is capped at "mixed" unless >= 2 distinct recorded/rep-note refs
  support it. All of this is lexical: passing is not proof.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.ai.claim_checks import (
    absence_as_fact,
    outcome_language,
    owner_absence_conclusion,
    quotes_without_statement,
    split_mixed_claim,
)
from app.ai.grounding import grounding_issues, recorded_dates
from app.ai.provider import AIProvider, AITransientError, AIUnavailableError
from app.ai.schemas import (
    AIStatus,
    BriefingRequest,
    CitedText,
    Claim,
    DealBriefing,
    EvidenceItem,
    FollowUpDraft,
    FollowUpRequest,
    ObjectionAnalysis,
    ObjectionAnalysisRequest,
    StrategyComparison,
    StrategyComparisonRequest,
)

logger = logging.getLogger("deal_rescue.ai")
T = TypeVar("T")

# Which evidence kinds can back which claim kinds.
_BACKING = {
    "recorded": {"recorded", "outcome", "signal"},
    "rep_note": {"rep_note", "memory"},  # memories are extracted from rep-written notes
    "statement": {"statement"},
}


class AIService:
    def __init__(self, provider: AIProvider, timeout_seconds: float, max_retries: int, retry_base_delay: float = 1.0):
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    async def status(self) -> AIStatus:
        return await self.provider.status()

    async def _call(self, operation: str, func: Callable[[], Awaitable[T]]) -> T:
        delay = self.retry_base_delay
        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.wait_for(func(), timeout=self.timeout_seconds)
            except (TimeoutError, AITransientError) as exc:
                if attempt == self.max_retries:
                    raise AIUnavailableError(
                        f"AI {operation} failed after {attempt + 1} attempt(s) ({type(exc).__name__})") from None
                logger.info("AI %s attempt %d failed (%s); retrying", operation, attempt + 1, type(exc).__name__)
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    # -- evidence enforcement -----------------------------------------------

    @staticmethod
    def _filter(citations: list[str], known: dict[str, EvidenceItem], rejected: set[str]) -> list[str]:
        kept = []
        for c in citations:
            if c in known:
                kept.append(c)
            else:
                rejected.add(c)
        return kept

    @staticmethod
    def _check_strategy_items(items: list[CitedText], label: str, known: dict[str, EvidenceItem], rejected: set[str],
                              context: list[str], notes: list[str]) -> tuple[list[CitedText], int]:
        """Filter, ground and screen one list of strategy items. Returns (kept items, rejected/marked count)."""
        kept: list[CitedText] = []
        bad = 0

        def check(text: str, citations: list[str], prior: list[str]) -> CitedText:
            cites = AIService._filter(citations, known, rejected)
            issues = list(prior)
            if not cites:
                issues.append("no valid citation to the evidence known at the branch date")
            else:
                issues += grounding_issues(text, [known[c].text for c in cites], context,
                                           recorded_dates([known[c].occurred_at for c in cites]))
                if quotes_without_statement(text, {known[c].kind for c in cites}):
                    issues.append("quotes text as if verbatim; no customer statement is cited (rep notes are the "
                                  "rep's account)")
            if absence_as_fact(text):
                issues.append("states that something does not exist; the records only show it was not recorded")
            if issues:
                return CitedText(text=text, citations=[], issues=issues)
            return CitedText(text=text, citations=cites)

        for item in items:
            rule = outcome_language(item.text)
            if rule is not None:
                bad += 1
                notes.append(f"A {label} item was removed: it {rule.message}.")
                continue
            owner = owner_absence_conclusion(item.text)
            if owner is not None:
                # Keep the owner fact (checked as usual); the conclusion is never grounded by the owner gap.
                if owner.fact:
                    fact = check(owner.fact, item.citations, item.issues)
                    bad += bool(fact.issues)
                    kept.append(fact)
                else:
                    AIService._filter(item.citations, known, rejected)  # still report unknown refs
                kept.append(CitedText(text=owner.conclusion, citations=[], issues=[owner.reason]))
                bad += 1
                continue
            checked = check(item.text, item.citations, item.issues)
            bad += bool(checked.issues)
            kept.append(checked)
        return kept, bad

    def _filter_cited(self, items: list[CitedText], known, rejected) -> list[CitedText]:
        out = []
        for item in items:
            cites = self._filter(item.citations, known, rejected)
            out.append(CitedText(text=item.text, citations=cites))
        return out

    # -- operations ----------------------------------------------------------

    async def generate_briefing(self, request: BriefingRequest) -> DealBriefing:
        result = await self._call("briefing", lambda: self.provider.generate_briefing(request))
        known = {e.ref: e for e in request.evidence}
        context = [request.deal.customer_name, request.deal.deal_title]
        rejected: set[str] = set(result.rejected_citations)
        relabelled = unsupported = 0
        claims = []
        parts: list[Claim] = []
        final: set[int] = set()  # positions in `parts` that are already decided (owner-gap conclusions)
        for claim in result.claims:
            owner = owner_absence_conclusion(claim.text) if claim.kind != "unsupported" else None
            if owner is not None:
                self._filter(claim.citations, known, rejected)  # still report unknown refs
                if owner.fact:
                    fact = claim.model_copy(update={"text": owner.fact})
                    split = split_mixed_claim(fact)
                    relabelled += sum(1 for p in split if p.kind != fact.kind)
                    parts.extend(split)
                final.add(len(parts))
                parts.append(claim.model_copy(update={"text": owner.conclusion, "kind": "unsupported",
                                                      "citations": [], "grounding_issues": [owner.reason]}))
                continue
            split = split_mixed_claim(claim)
            relabelled += sum(1 for p in split if p.kind != claim.kind)
            parts.extend(split)
        for position, claim in enumerate(parts):
            if position in final:
                claims.append(claim)
                unsupported += 1
                continue
            cites = self._filter(claim.citations, known, rejected)
            # Grounding: cited claims against their own evidence (text + recorded date); uncited against all.
            sources = [known[c] for c in cites] if cites else list(request.evidence)
            issues = grounding_issues(claim.text, [s.text for s in sources], context,
                                      recorded_dates([s.occurred_at for s in sources]))
            if issues:
                claims.append(claim.model_copy(update={"citations": [], "kind": "unsupported",
                                                       "grounding_issues": issues}))
                unsupported += 1
                continue
            kind = claim.kind
            if kind == "unsupported":
                cites = []
            elif kind in _BACKING and not any(known[c].kind in _BACKING[kind] for c in cites):
                kind = "inference"
                relabelled += 1
            claims.append(claim.model_copy(update={"citations": cites, "kind": kind}))
        return result.model_copy(update={"claims": claims, "rejected_citations": sorted(rejected),
                                         "relabelled_claims": result.relabelled_claims + relabelled,
                                         "unsupported_claims": result.unsupported_claims + unsupported})

    async def analyze_objections(self, request: ObjectionAnalysisRequest) -> ObjectionAnalysis:
        result = await self._call("objections", lambda: self.provider.analyze_objections(request))
        known = {e.ref: e for e in request.evidence}
        rejected: set[str] = set(result.rejected_citations)
        hypotheses = [
            h.model_copy(update={"kind": "inference",
                                 "supporting": self._filter_cited(h.supporting, known, rejected),
                                 "contradicting": self._filter_cited(h.contradicting, known, rejected)})
            for h in result.hypotheses
        ]
        return result.model_copy(update={"hypotheses": hypotheses, "rejected_citations": sorted(rejected)})

    async def draft_follow_up(self, request: FollowUpRequest) -> FollowUpDraft:
        result = await self._call("follow_up", lambda: self.provider.draft_follow_up(request))
        known = {e.ref: e for e in request.evidence}
        rejected: set[str] = set(result.rejected_citations)
        cites = self._filter(result.citations, known, rejected)
        return result.model_copy(update={"kind": "hypothetical", "citations": cites,
                                         "rejected_citations": sorted(rejected)})

    async def compare_strategies(self, request: StrategyComparisonRequest) -> StrategyComparison:
        result = await self._call("strategy_comparison", lambda: self.provider.compare_strategies(request))
        known = {e.ref: e for e in request.evidence}
        context = [request.deal.customer_name, request.deal.deal_title]
        all_texts = [e.text for e in request.evidence]
        all_dates = recorded_dates([e.occurred_at for e in request.evidence])
        # Next steps only: the server's own rule-derived next steps are trusted basis too (e.g. "within 5 day(s) of
        # 2026-09-20"). They are hypothetical rule text, never evidence or recorded commitments.
        step_basis = all_texts + list(request.rule_next_steps)
        has_outcomes = any(e.kind == "outcome" for e in request.evidence)
        valid_ids = {s.id for s in request.strategies}
        rejected: set[str] = set(result.rejected_citations)
        adjusted = 0
        assessments = []
        for a in result.assessments:
            if a.strategy_id not in valid_ids:
                logger.warning("provider returned an assessment for unknown strategy; dropped")
                continue
            notes = list(a.check_notes)
            supporting, bad_for = self._check_strategy_items(a.supporting, "supporting", known, rejected, context, notes)
            contradicting, bad_against = self._check_strategy_items(a.contradicting, "contradicting", known, rejected,
                                                                    context, notes)
            gaps = []
            for gap in a.evidence_gaps:
                rule = outcome_language(gap)
                if rule is not None:
                    notes.append(f"An evidence gap was removed: it {rule.message}.")
                else:
                    gaps.append(gap)
            steps = []
            for step in a.commitments_created:
                rule = outcome_language(step)
                issues = grounding_issues(step, step_basis, context, all_dates)
                if rule is not None:
                    notes.append(f"A next step was removed: it {rule.message}.")
                elif issues:
                    notes.append(f"A next step was removed: {'; '.join(issues)}.")
                else:
                    steps.append(step)
            removed_other = (len(a.evidence_gaps) - len(gaps)) + (len(a.commitments_created) - len(steps))

            valid_for = [x for x in supporting if x.citations]
            verdict = a.verdict
            # Only ever weaken a verdict, never strengthen it.
            if verdict in ("supported", "mixed") and not valid_for:
                verdict = "unsupported"
            factual = {c for x in valid_for for c in x.citations
                       if known[c].kind in ("recorded", "rep_note", "memory", "statement")}
            if verdict == "supported" and not has_outcomes and len(factual) < 2:
                verdict = "mixed"
                notes.append("Verdict capped at 'mixed': no recorded outcomes, and fewer than two recorded or "
                             "rep-note sources support it.")
            if verdict != a.verdict:
                adjusted += 1
            assessments.append(a.model_copy(update={
                "supporting": supporting, "contradicting": contradicting, "evidence_gaps": gaps,
                "commitments_created": steps, "verdict": verdict, "check_notes": notes,
                "rejected_items": a.rejected_items + bad_for + bad_against + removed_other}))
        return result.model_copy(update={"kind": "hypothetical", "assessments": assessments,
                                         "rejected_citations": sorted(rejected),
                                         "adjusted_verdicts": result.adjusted_verdicts + adjusted})


def build_ai_service(settings, provider: AIProvider | None = None) -> AIService:
    from app.ai.providers import build_ai_provider

    return AIService(provider or build_ai_provider(settings), settings.ai_timeout_seconds, settings.ai_max_retries)
