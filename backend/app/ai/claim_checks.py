"""Deterministic check for claims that mix a fact with a conclusion drawn from it.

A claim labelled "recorded" or "rep_note" must state only what its evidence records. Small models
often append a conclusion to a recorded fact in the same sentence, e.g.

    "The deal has no stakeholders recorded, making decision-makers unassessable."  (kind=recorded)

The first clause is a recorded absence; the second is a conclusion. This module splits such a claim
at an explicit consequence connector into a fact part (original kind) and a conclusion part
(kind "inference"). Both parts keep the original citations and then go through the normal
citation, grounding and evidence-kind checks in AIService, so a split never bypasses them.

Scope, deliberately narrow:
* only explicit connectors that follow a clause boundary (", making ...", ", which means ...",
  "; therefore ...", ", so ..."). A claim's wording is never judged on vocabulary alone
  (e.g. "cannot be assessed" by itself is not treated as a conclusion);
* connectors inside quotation marks are ignored (quoted words are never rewritten);
* only "recorded" and "rep_note" claims. "statement" claims are verbatim customer words and are
  not rewritten; "inference"/"unsupported" claims need no split.
Extend by adding a MixedClaimRule to MIXED_CLAIM_RULES.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.ai.schemas import Claim

SPLIT_KINDS = frozenset({"recorded", "rep_note"})

# A clause boundary: comma, semicolon, colon or a spaced dash.
_BOUNDARY = r"(?:[,;:]|\s[-–—])\s*"
_VERBS = {"making": "makes", "meaning": "means", "suggesting": "suggests", "indicating": "indicates",
          "implying": "implies", "leaving": "leaves"}
_MIN_FACT_WORDS = 3


@dataclass(frozen=True)
class MixedClaimRule:
    """One way a conclusion is attached to a fact. ``conclusion`` rewrites the attached clause as a
    standalone sentence using only words from the claim (so grounding still applies to it)."""

    name: str
    pattern: re.Pattern[str]
    conclusion: Callable[[re.Match[str], str], str]


MIXED_CLAIM_RULES: tuple[MixedClaimRule, ...] = (
    # "..., making decision-makers unassessable" -> "This makes decision-makers unassessable."
    MixedClaimRule("participle_consequence",
                   re.compile(_BOUNDARY + r"(making|meaning|suggesting|indicating|implying|leaving)\s+", re.I),
                   lambda m, rest: f"This {_VERBS[m.group(1).lower()]} {rest}"),
    # "..., which means the timeline is unclear" -> "This means the timeline is unclear."
    MixedClaimRule("which_consequence",
                   re.compile(_BOUNDARY + r"which\s+(means|suggests|indicates|implies|makes|leaves)\s+", re.I),
                   lambda m, rest: f"This {m.group(1).lower()} {rest}"),
    # "..., so timing cannot be assessed" / "; therefore ..." -> "As a result, timing cannot be assessed."
    MixedClaimRule("adverbial_consequence",
                   re.compile(_BOUNDARY + r"(?:so(?!\s+(?:far|that|much|many)\b)|therefore|thus|hence|"
                              r"consequently|as\s+a\s+result)\s*,?\s+", re.I),
                   lambda m, rest: f"As a result, {rest}"),
)

_QUOTES = '"“”'


def _inside_quotes(text: str, index: int) -> bool:
    return sum(text[:index].count(q) for q in _QUOTES) % 2 == 1


def _sentence(text: str) -> str:
    text = text.strip().rstrip(" ,;:-–—")
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _first_connector(text: str) -> tuple[MixedClaimRule, re.Match[str]] | None:
    found: tuple[MixedClaimRule, re.Match[str]] | None = None
    for rule in MIXED_CLAIM_RULES:
        for match in rule.pattern.finditer(text):
            if _inside_quotes(text, match.start()):
                continue
            if found is None or match.start() < found[1].start():
                found = (rule, match)
            break
    return found


def split_mixed_claim(claim: Claim) -> list[Claim]:
    """Return [claim] unchanged, or [fact part, conclusion part] for a mixed recorded/rep_note claim.

    If the text before the connector is too short to be a fact on its own, the whole claim is
    returned as a single inference instead (never presented as fact).
    """
    if claim.kind not in SPLIT_KINDS:
        return [claim]
    hit = _first_connector(claim.text)
    if hit is None:
        return [claim]
    rule, match = hit
    fact = claim.text[: match.start()]
    rest = claim.text[match.end():].strip()
    if not rest:
        return [claim]
    if len(fact.split()) < _MIN_FACT_WORDS:
        return [claim.model_copy(update={"kind": "inference"})]
    conclusion = _sentence(rule.conclusion(match, rest.rstrip(".!? ")))
    return [
        claim.model_copy(update={"text": _sentence(fact)}),
        claim.model_copy(update={"text": conclusion, "kind": "inference"}),
    ]


# -- Deal Time Machine strategy text (TM4) -----------------------------------------------------------------
#
# Strategy assessments are hypothetical. These lexical checks enforce, in code rather than only in the prompt:
# * no outcome predictions, probabilities or certainty ("would have closed", "70%", "definitely");
# * no statement that something does not exist when the records only show it was not recorded;
# * no quotation presented as the customer's words unless a verbatim customer statement is cited.
# They are lexical, not semantic proof. Extend by adding a rule to the tuples below.


@dataclass(frozen=True)
class TextRule:
    name: str
    pattern: re.Pattern[str]
    message: str


OUTCOME_RULES: tuple[TextRule, ...] = (
    TextRule("predicted_outcome", re.compile(
        r"\b(?:would|will)\s+(?:have\s+)?(?:won|win|closed|close|signed|sign|saved|save|secured|secure|rescued|"
        r"rescue|converted|convert)\b", re.I), "predicts a deal outcome"),
    TextRule("causal_outcome", re.compile(
        r"\bwould\s+have\s+(?:led|resulted|caused|changed|prevented|improved|increased)\b", re.I),
        "claims an effect the records cannot establish"),
    TextRule("likely_outcome", re.compile(r"\b(?:likely|unlikely|sure|certain)\s+to\s+(?:win|close|sign|succeed|fail)\b",
                                          re.I), "predicts a deal outcome"),
    TextRule("certainty", re.compile(r"\b(?:guarantee[sd]?|definitely|certainly|undoubtedly|surely)\b", re.I),
             "states certainty the evidence cannot support"),
    TextRule("probability", re.compile(
        r"\d+(?:\.\d+)?\s?%|\bper\s?cent\b|\b(?:probability|likelihood|odds)\b|"
        r"\bchances?\s+of\s+(?:winning|closing|success|signing)\b|\b(?:win|close|success)\s+rate\b|"
        r"\bconfidence\s+(?:score|level)\b", re.I), "states a probability or score"),
)

_ABSENCE = re.compile(
    r"\b(?:(?:has|had|have)\s+no|there\s+(?:is|was|were|are)\s+no|lack(?:s|ed)?(?:\s+an?|\s+any)?|without\s+an?)\s+"
    r"(?:[\w-]+\s+){0,2}?(?:decision[- ]makers?|stakeholders?|champions?|budget|buyers?|sponsors?|owners?|"
    r"requirements?)\b", re.I)
_QUOTE = re.compile(r"[\"“]([^\"”]{12,})[\"”]")


# An outcome phrase inside an explicit hedge is the wording we ask for, e.g. "the evidence does not establish whether
# this would have changed the outcome". Plain "if" is not a hedge ("if we had ..., it would have closed" is caught).
_HEDGE = re.compile(r"\b(?:whether|does\s+not\s+(?:establish|show)|do\s+not\s+(?:establish|show)|cannot\s+"
                    r"(?:establish|say|tell|know)|unclear|no\s+evidence\s+that|not\s+known)\b", re.I)
_HEDGEABLE = {"predicted_outcome", "causal_outcome"}


def outcome_language(text: str) -> TextRule | None:
    """The first outcome/probability/certainty rule the text breaks, if any."""
    for rule in OUTCOME_RULES:
        for match in rule.pattern.finditer(text):
            if rule.name in _HEDGEABLE and _HEDGE.search(text[max(0, match.start() - 60):match.start()]):
                continue
            return rule
    return None


def absence_as_fact(text: str) -> bool:
    """True if the text says something did not exist rather than that it was not recorded."""
    for match in _ABSENCE.finditer(text):
        after = text[match.end():match.end() + 30].lower()
        if "record" not in after and "record" not in match.group(0).lower():
            return True
    return False


def quotes_without_statement(text: str, cited_kinds: set[str]) -> bool:
    """A quotation of 3+ words presented without citing a verbatim customer statement (rep notes are not)."""
    return any(len(m.group(1).split()) >= 3 for m in _QUOTE.finditer(text)) and "statement" not in cited_kinds


# Our deal owner is the seller-side rep; stakeholders and decision-makers are on the customer side. A missing deal
# owner is an internal gap: by itself it says nothing about the customer's people, whether the customer can be
# contacted, whether the rep knows whom to contact, or whether the next contact can be scheduled.
#
# owner_absence_conclusion() finds a sentence that draws such a conclusion from the missing owner through an explicit
# causal link ("…, so …", "… because …", "Because …, …", "Without an owner, …", "… until an owner is assigned") and
# splits it into the owner fact (kept, still checked normally) and the conclusion (not supported by the owner gap).
# Deterministic and lexical: it covers these explicit forms, not every paraphrase of the same mistake.

OwnerConclusionKind = Literal["customer_contact", "customer_people"]

_OWNER = r"(?:internal\s+|seller[- ]side\s+|sales\s+)*(?:deal\s+)?owner"
_OWNER_ABSENT = re.compile(
    rf"\b(?:no|missing|lacks?|lacking|without)\s+(?:an?\s+|any\s+|recorded\s+|assigned\s+)?{_OWNER}\b"
    rf"|\b{_OWNER}(?:\s+field)?\s+(?:is|was|remains)\s+(?:not\s+(?:recorded|set|assigned|filled(?:\s+in)?|known)|"
    r"missing|unknown|empty|blank|unassigned|unset)\b"
    rf"|\b(?:until|unless)\s+(?:an?\s+|the\s+)?{_OWNER}\s+is\s+(?:assigned|recorded|set|named)\b", re.I)
_IMPLICIT_LINK = re.compile(r"^(?:without|lacking|until|unless)\b", re.I)  # the owner phrase itself is the link
_CUSTOMER_SIDE = re.compile(r"\b(?:decision[- ]makers?|stakeholders?|buyers?|approvers?)\b", re.I)
_UNABLE = (r"(?:can(?:not|'t|\u2019t)|can\s+not|could(?:\s+not|n't|n\u2019t)|unable\s+to|"
           r"(?:does|do|did)(?:\s+not|n't|n\u2019t)\s+know|not\s+(?:sure|clear)|unclear|unknown|no\s+way\s+to)")
_CONTACT_VERB = r"(?:contact|reach(?:\s+out)?|call|e-?mail|approach|engage|follow[\s-]*up|schedul|plan|arrang)"
_CONTACT_UNCERTAIN = re.compile(
    rf"\b{_UNABLE}\b[^.;]{{0,40}}?\b(?:who(?:m)?\s+to\s+{_CONTACT_VERB}|{_CONTACT_VERB}\w*)"
    r"|\b(?:customer\s+)?(?:contact|follow[\s-]*up|outreach|next\s+(?:contact|step|call|meeting)|meeting|call)s?\s+"
    r"(?:cannot|can't|can\s+not|could\s+not|couldn't)\s+be\s+(?:scheduled|planned|arranged|made|initiated|set\s+up)"
    rf"|\bwho(?:m)?\s+to\s+{_CONTACT_VERB}\w*\b[^.;]{{0,30}}\b(?:unknown|unclear|undetermined|not\s+known)\b"
    rf"|\b(?:no\s*one|nobody)\s+(?:knows\s+who(?:m)?\s+)?to\s+{_CONTACT_VERB}", re.I)
_LINK = re.compile(r",?\s*\b(?:so\s+that|so|therefore|thus|hence|because|since|as\s+a\s+result|which\s+means|meaning|"
                   r"means|leaving|making|due\s+to|given\s+that|without\s+identifying|until|unless)\b\s*,?", re.I)
_SENTENCE = re.compile(r"[^.;!?]+[.;!?]*")


@dataclass(frozen=True)
class OwnerAbsenceConclusion:
    """A sentence that concludes something about the customer side from a missing seller-side deal owner."""

    kind: OwnerConclusionKind
    fact: str | None      # the text with the owner fact kept (other sentences kept too); None if no clean fact clause
    conclusion: str       # the unsupported conclusion clause, as a sentence
    reason: str


_REASONS: dict[str, str] = {
    "customer_contact": "concludes something about contacting the customer from a missing deal owner; our deal owner "
                        "is seller-side and its absence says nothing about whom to contact or when",
    "customer_people": "links our deal owner (seller side) to the customer's decision-maker or stakeholders; the "
                       "absence of one says nothing about the other",
}


def _clause(text: str) -> str | None:
    text = text.strip().strip(",;:").strip()
    if len(text.split()) < 3:
        return None
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


def owner_absence_conclusion(text: str) -> OwnerAbsenceConclusion | None:
    """The first sentence linking a missing deal owner to a customer-side conclusion, split into fact and conclusion."""
    for sentence_match in _SENTENCE.finditer(text):
        sentence = sentence_match.group(0)
        owner = _OWNER_ABSENT.search(sentence)
        if owner is None:
            continue
        target = _CONTACT_UNCERTAIN.search(sentence)
        kind: OwnerConclusionKind = "customer_contact"
        if target is None:
            target, kind = _CUSTOMER_SIDE.search(sentence), "customer_people"
        if target is None:
            continue
        body = sentence.rstrip(" .;!?")
        fact_clause: str | None
        if _IMPLICIT_LINK.match(owner.group(0)):          # "Without an owner, …" / "… until an owner is assigned"
            fact_clause = None
            conclusion_clause = body[:owner.start()] + body[owner.end():]
        else:
            lo, hi = sorted((owner.end(), target.end()))[0], max(owner.start(), target.start())
            between = next((m for m in _LINK.finditer(body) if lo <= m.start() and m.end() <= hi), None)
            leading = next((m for m in _LINK.finditer(body) if m.end() <= min(owner.start(), target.start())), None)
            if between is not None:                      # "fact, so conclusion" / "conclusion because fact"
                left, right = body[:between.start()], body[between.end():]
                fact_clause, conclusion_clause = (left, right) if owner.start() < between.start() else (right, left)
            elif leading is not None and owner.start() < target.start():   # "Because fact, conclusion"
                comma = body.find(",", owner.end())
                if comma == -1 or comma > target.start():
                    continue
                fact_clause, conclusion_clause = body[leading.end():comma], body[comma + 1:]
            else:
                continue                                  # no explicit causal link: not this safeguard's business
        conclusion = _clause(conclusion_clause) or _clause(body) or body
        fact_sentence = _clause(fact_clause) if fact_clause else None
        rest = (text[:sentence_match.start()] + (f"{fact_sentence} " if fact_sentence else "")
                + text[sentence_match.end():]).strip()
        return OwnerAbsenceConclusion(kind=kind, fact=rest or None, conclusion=conclusion, reason=_REASONS[kind])
    return None


def conflates_owner_and_decision_maker(text: str) -> bool:
    """Backward-compatible wrapper: True if a sentence draws a customer-side conclusion from a missing deal owner."""
    return owner_absence_conclusion(text) is not None
