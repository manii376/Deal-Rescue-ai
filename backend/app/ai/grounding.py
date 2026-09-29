"""Deterministic lexical grounding check for AI claims.

For each claim, every number and proper name it mentions must occur in the text of the
evidence it cites (or in the deal context: customer name, deal title). A claim that
introduces a number or name absent from its evidence is reported as unsupported.

Names are capitalised words that are not sentence-initial, acronyms, and sentence-initial
capitalised words that do not look like ordinary English (not in a list of common starters,
no common inflection suffix). Remaining gap: an invented name that happens to be a common
word or to end like one (e.g. "Morning", "Ventures") at the start of a sentence is not caught.

Recorded dates: a cited source's ``occurred_at`` is trusted structured evidence that the model is shown next to the
excerpt (as a UTC date). ``grounding_issues(..., recorded_dates=...)`` accepts a *whole* date in the claim
(2026-09-20, 20 Sep 2026, September 20, 2026) that equals one of those dates. Its year, month and day are not
accepted as standalone numbers, and any other date must still appear in the evidence text itself.

IMPORTANT: this is a *necessary*, not a sufficient, condition. Passing it only means the
claim does not introduce new numbers/names; it does NOT prove the claim follows from the
evidence (no semantic entailment). The UI must show the cited source records so a person
can verify the claim.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime

_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s?[kKmM]\b)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’&\-]*")
_POSSESSIVE = re.compile(r"['’]s?$")  # "CFO's", "CFO’s", "Partners'" -> compare as the base word


def _token(word: str) -> str:
    """Comparison form of a word (displayed claim text is never modified)."""
    return _POSSESSIVE.sub("", word)
_SENTENCE_START = re.compile(r"(^|[.!?:;\n]\s*|\(\s*|\"\s*|-\s+)$")

# Capitalised words that are not names (months, days, generic business words, units).
_NOT_NAMES = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "i", "q1", "q2", "q3", "q4", "ok", "n/a", "id",
}


def _normalise_number(raw: str, suffix: str | None) -> str:
    value = raw.replace(",", "")
    if suffix:
        factor = 1_000 if suffix.strip().lower() == "k" else 1_000_000
        try:
            value = str(int(float(value) * factor))
        except ValueError:
            pass
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def extract_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    for y, m, d in _DATE.findall(text):
        numbers.update({_normalise_number(y, None), _normalise_number(m, None), _normalise_number(d, None)})
    for raw, suffix in _NUMBER.findall(_DATE.sub(" ", text)):
        numbers.add(_normalise_number(raw, suffix or None))
    return numbers


def extract_names(text: str) -> set[str]:
    """Capitalised words that are not sentence-initial, plus acronyms (e.g. SOC, USD)."""
    names: set[str] = set()
    for match in _WORD.finditer(text):
        word = _token(match.group(0))
        lowered = word.lower()
        if lowered in _NOT_NAMES or len(word) < 2:
            continue
        is_acronym = sum(ch.isupper() for ch in word) >= 2 and word.upper() == word
        if is_acronym:
            names.add(lowered)
            continue
        if word[0].isupper() and not _SENTENCE_START.search(text[: match.start()]):
            names.add(lowered)
    return names


# Ordinary words that commonly start a sentence in a deal briefing. A sentence-initial
# capitalised word in this list (or matching _ORDINARY_SUFFIXES) is never treated as a name.
_COMMON_SENTENCE_STARTERS = frozenset("""
a about above according across additionally after again against all also although among an and another
any anyone anything are as at based be because been before being below besides between both but by can
cannot consequently could currently despite did do does due during each either even every everyone
everything few finally first following for from further furthermore given had has have having he her here
hers his how however if in including instead is it its just last late later least less let like many may
meanwhile might more moreover most much must neither next no none nor not nothing now of on once one only
or other others otherwise our ours overall per perhaps please prior rather recent recently regarding
second several she should since so some someone something soon still such than that the their theirs them
then there therefore these they third this those though through thus to today too two under unless until
up upon us was we were what whatever when where whereas whether which while who whom whose why will with
within without would yet you your
account action actions agreement approval approvals area areas budget budgets buyer call calls case
change changes client clients close closing commitment commitments competitor competitors compliance
concern concerns condition conditions contact contract contracts cost costs customer customers data date
dates deadline deadlines deal deals decision decisions delay delays delivery demo details
discount discounts discussion document documents email emails engagement evidence expected feedback
finance follow-up forecast funding gap gaps goal goals information interest issue issues key legal
meeting meetings milestone milestones missing negotiation negotiations new note notes objection
objections open option options order outcome owner owners payment payments phase pilot plan plans
price prices pricing priorities priority procurement progress project projects proposal proposals
purchase quote quotes reason reasons report reports request requests requirement requirements response
review reviews risk risks rollout sales schedule scope security signal signals signing situation stage
stakeholder stakeholders status step steps support team teams terms timeline timelines timing trial
unknown update updates urgent value vendor vendors
""".split())
_ORDINARY_SUFFIXES = ("ly", "ing", "ed", "tion", "tions", "ment", "ments", "ness", "ity", "ies", "ous", "ive")
_MIN_SUFFIX_WORD = 6


def _is_ordinary_word(lowered: str) -> bool:
    if lowered in _COMMON_SENTENCE_STARTERS or lowered in _NOT_NAMES:
        return True
    return len(lowered) >= _MIN_SUFFIX_WORD and lowered.endswith(_ORDINARY_SUFFIXES)


def extract_sentence_initial_candidates(text: str) -> set[str]:
    """Sentence-initial capitalised words that do not look like ordinary English words.

    extract_names() deliberately skips sentence-initial words (every sentence starts with a
    capital). This complements it: "Northwind is evaluating..." yields {"northwind"}, while
    "The ...", "Currently ...", "Budget ..." yield nothing. Candidates are only reported as
    issues when they are also absent from the evidence (see grounding_issues).
    """
    candidates: set[str] = set()
    for match in _WORD.finditer(text):
        word = _token(match.group(0))
        if len(word) < 2 or not word[0].isupper() or not _SENTENCE_START.search(text[: match.start()]):
            continue
        lowered = word.lower()
        if word.upper() == word and sum(ch.isupper() for ch in word) >= 2:
            continue  # acronyms are already covered by extract_names
        if not _is_ordinary_word(lowered):
            candidates.add(lowered)
    return candidates


def _vocabulary(texts: Iterable[str]) -> tuple[set[str], set[str]]:
    words: set[str] = set()
    numbers: set[str] = set()
    for text in texts:
        words.update(_token(w).lower() for w in _WORD.findall(text))
        numbers.update(extract_numbers(text))
    return words, numbers


_MONTH_RE = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
             r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\.?,?\s+(\d{{4}})\b", re.I)
_MDY = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)


def _date(year: str, month: int, day: str) -> date | None:
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def recorded_dates(occurred: Iterable[datetime | None]) -> set[date]:
    """The UTC calendar dates of trusted evidence timestamps: exactly the dates the prompts show the model."""
    return {o.astimezone(UTC).date() for o in occurred if o is not None}


def _date_matches(text: str) -> list[tuple[int, int, date | None, str]]:
    """Every date expression in the text: (start, end, parsed date or None if impossible, raw text). ISO first."""
    found: list[tuple[int, int, date | None, str]] = []
    patterns = (
        (_DATE, lambda m: _date(m.group(1), int(m.group(2)), m.group(3)) if 1 <= int(m.group(2)) <= 12 else None),
        (_DMY, lambda m: _date(m.group(3), _MONTHS[m.group(2)[:3].lower()], m.group(1))),
        (_MDY, lambda m: _date(m.group(3), _MONTHS[m.group(1)[:3].lower()], m.group(2))),
    )
    for pattern, parse in patterns:
        for m in pattern.finditer(text):
            if not any(start < m.end() and m.start() < end for start, end, _, _ in found):
                found.append((m.start(), m.end(), parse(m), m.group(0)))
    return found


def extract_dates(text: str) -> set[date]:
    return {d for _, _, d, _ in _date_matches(text) if d is not None}


def grounding_issues(claim_text: str, evidence_texts: Iterable[str], context_texts: Iterable[str] = (),
                     recorded_dates: Iterable[date] = ()) -> list[str]:
    """Numbers, dates and names in the claim that do not occur in the evidence (or deal context).

    Dates are matched whole (never as separate year/month/day numbers): a date in the claim must equal a date written
    in the evidence/context text or one of ``recorded_dates`` (trusted ``occurred_at`` dates of the same evidence; see
    the module docstring). Other numbers are checked as before.
    """
    texts = [*evidence_texts, *context_texts]
    words, numbers = _vocabulary(texts)
    known_dates = set(recorded_dates).union(*(extract_dates(t) for t in texts))
    spans = _date_matches(claim_text)
    checked = claim_text
    for start, end, _, _ in sorted(spans, reverse=True):
        checked = checked[:start] + " " + checked[end:]
    issues = [f"number '{n}' does not appear in the cited evidence"
              for n in sorted(extract_numbers(checked) - numbers)]
    issues += [f"date '{raw}' does not appear in the cited evidence"
               for raw in sorted({d.isoformat() if d else raw for _, _, d, raw in spans if d not in known_dates})]
    names = extract_names(claim_text) | extract_sentence_initial_candidates(claim_text)
    issues += [f"name '{n}' does not appear in the cited evidence" for n in sorted(names - words)]
    return issues
