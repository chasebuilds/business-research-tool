from __future__ import annotations

from rapidfuzz import fuzz

from business_research.models import ACCEPTED, NONE, REVIEW, Candidate, MatchDecision, Scored
from business_research.normalize import normalize_name

DEFAULT_ACCEPT = 90.0
DEFAULT_REVIEW = 70.0
# Two different records this close in score cannot be told apart by name alone.
AMBIGUITY_MARGIN = 3.0


def name_score(a: str, b: str) -> float:
    """0 to 100 similarity of two company names after legal-suffix normalization.

    Plain and token-sorted ratio only. Partial-match scorers are left out on purpose: they rate
    "Siemens" against "Siemens Energy" near 100, which is a different company.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb))


def score_candidate(name: str, country: str | None, candidate: Candidate) -> Scored:
    best, matched_on = max((name_score(name, n), n) for n in [candidate.name, *candidate.aliases] if n)
    country_match = (country in candidate.countries) if country and candidate.countries else None
    return Scored(candidate, round(best, 1), matched_on, country_match)


def decide(
    name: str,
    country: str | None,
    candidates: list[Candidate],
    accept: float = DEFAULT_ACCEPT,
    review: float = DEFAULT_REVIEW,
) -> MatchDecision:
    """Accept one candidate as the company, send the question to review, or report nothing found.

    Accepting the wrong entity is the costly error, so every doubtful case goes to review:
    low score, a known country that contradicts the input, several records too close to call,
    or an inactive record.
    """
    scored = sorted(
        (score_candidate(name, country, c) for c in candidates if c.name),
        key=lambda s: (-s.score, s.country_match is not True, s.candidate.record_id),
    )
    shortlist = [s for s in scored if s.score >= review]
    if not shortlist:
        return MatchDecision(NONE, scored=scored[:5], reason="no candidate above the review threshold")

    eligible = [s for s in shortlist if s.score >= accept]
    if not eligible:
        return MatchDecision(
            REVIEW, scored=shortlist, reason=f"best score {shortlist[0].score} is below {accept}"
        )

    if any(s.candidate.active for s in eligible):
        eligible = [s for s in eligible if s.candidate.active]
    else:
        return MatchDecision(REVIEW, scored=shortlist, reason="only inactive records match")

    if country:
        # Country is a tiebreak: same-country records win, unknown-country records stay in play,
        # and a record known to be elsewhere is never accepted on name alone.
        in_country = [s for s in eligible if s.country_match]
        eligible = in_country or [s for s in eligible if s.country_match is None]
        if not eligible:
            return MatchDecision(
                REVIEW, scored=shortlist, reason=f"name matches but country is not {country}"
            )

    top = eligible[0]
    rivals = [s for s in eligible[1:] if top.score - s.score <= AMBIGUITY_MARGIN]
    if rivals:
        # Second tiebreak after country: exactly one of the tied records is the one another
        # source's matched record (or the input file) points at by id.
        linked = [s for s in [top, *rivals] if s.candidate.linked]
        if len(linked) == 1:
            return MatchDecision(ACCEPTED, chosen=linked[0], scored=shortlist, reason="")
        return MatchDecision(
            REVIEW, scored=shortlist, reason=f"{len(rivals) + 1} records too close to call by name"
        )
    return MatchDecision(ACCEPTED, chosen=top, scored=shortlist, reason="")
