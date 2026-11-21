from __future__ import annotations

import logging

from business_research.http import FetchError, utc_now
from business_research.matching import DEFAULT_ACCEPT, DEFAULT_REVIEW, decide
from business_research.models import (
    ACCEPTED,
    FIELDS,
    REVIEW,
    Candidate,
    CompanyInput,
    CompanyResult,
    Fact,
    RunResult,
)
from business_research.sources.base import SourceAdapter, source_group
from business_research.verify import agree, verify_field

log = logging.getLogger("business_research")

MAX_LINKED_FETCHES = 3


def research(
    inputs: list[CompanyInput],
    adapters: list[SourceAdapter],
    accept: float = DEFAULT_ACCEPT,
    review: float = DEFAULT_REVIEW,
    stats: dict[str, int] | None = None,
) -> RunResult:
    """Look every company up in every source, match, then cross-check the fields."""
    refused = [a.name for a in adapters if a.redistribution_ok is False]
    if refused:
        raise ValueError(f"refusing sources whose terms forbid redistribution: {', '.join(refused)}")
    started = utc_now()
    companies = []
    for number, company in enumerate(inputs, 1):
        log.info("company %d/%d: %s", number, len(inputs), company.name)
        result = research_one(company, adapters, accept, review)
        log.info(
            "  -> %s (%s)",
            result.status,
            ", ".join(f"{s}:{d.status}" for s, d in result.decisions.items()),
        )
        companies.append(result)
    _flag_duplicates(companies)
    return RunResult(
        companies=companies,
        sources=[
            {
                "name": a.name,
                "label": a.label,
                "terms_url": a.terms_url,
                "licence": a.licence,
                "attribution": a.attribution,
                "redistribution": "allowed" if a.redistribution_ok else "not allowed",
                "group": a.group,
            }
            for a in adapters
        ],
        started=started,
        finished=utc_now(),
        stats=dict(stats or {}),
        settings={"accept_score": accept, "review_score": review},
    )


def research_one(
    company: CompanyInput, adapters: list[SourceAdapter], accept: float, review: float
) -> CompanyResult:
    result = CompanyResult(company)
    found: dict[str, list[Candidate]] = {}

    def guarded(adapter: SourceAdapter, what: str, call):
        try:
            return call()
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            # One failing source must not sink the row. It is logged and shown on the row.
            message = f"{adapter.name}: {what} failed ({exc})"
            log.warning("  %s", message)
            result.errors.append(message)
            return None

    for adapter in adapters:
        candidates = (
            guarded(adapter, "search", lambda a=adapter: a.search(company.name, company.country)) or []
        )
        given = company.ids.get(adapter.name)
        if given and (record := guarded(adapter, f"fetch {given}", lambda a=adapter, g=given: a.fetch(g))):
            record.via, record.linked = "id in input", True
            candidates.append(record)
        found[adapter.name] = candidates
        result.decisions[adapter.name] = decide(company.name, company.country, candidates, accept, review)

    # Follow ids that a matched record gives for another source (an LEI on a Wikidata item, a
    # Wikidata id on a ROR record). The linked record is still judged on its own name score:
    # a link is a lead, not proof.
    for _ in range(2):
        progressed = False
        for adapter in adapters:
            if result.decisions[adapter.name].status == ACCEPTED:
                continue
            have = {c.record_id: c for c in found[adapter.name]}
            pointed = [
                (record_id, other)
                for other, candidate in result.accepted.items()
                for record_id in candidate.links.get(adapter.name, [])
            ]
            # A record the search already found, and that another source also points at.
            for record_id, other in pointed:
                known = have.get(record_id)
                if known and not known.linked:
                    known.linked, known.via = True, f"{known.via}, link from {other}"
                    progressed = True
            leads = [(r, other) for r, other in pointed if r not in have][:MAX_LINKED_FETCHES]
            for record_id, other in leads:
                record = guarded(adapter, f"fetch {record_id}", lambda a=adapter, r=record_id: a.fetch(r))
                if record:
                    record.via, record.linked = f"link from {other}", True
                    have[record.record_id] = record
                    found[adapter.name].append(record)
                    progressed = True
            if pointed:
                result.decisions[adapter.name] = decide(
                    company.name, company.country, found[adapter.name], accept, review
                )
        if not progressed:
            break

    _demote_inconsistent(result)
    accepted = result.accepted
    for field in FIELDS:
        entries = [(source, c.facts[field]) for source, c in accepted.items() if field in c.facts]
        groups = {a.name: source_group(a, field) for a in adapters}
        if verified := verify_field(field, entries, groups):
            result.fields[field] = verified
    return result


def _demote_inconsistent(result: CompanyResult) -> None:
    """Cross-source sanity check on entity identity.

    A fuzzy match is weaker evidence than a better match in another source. When the two
    matched records do not even share a name, the weaker one is probably a look-alike company,
    so it goes to review instead of into the dataset.
    """
    accepted = [(s, d) for s, d in result.decisions.items() if d.status == ACCEPTED and d.chosen]
    if len(accepted) < 2:
        return
    anchor_source, anchor = max(accepted, key=lambda pair: pair[1].chosen.score)
    anchor_name = Fact(anchor.chosen.candidate.name, tuple(anchor.chosen.candidate.aliases))
    for source, decision in accepted:
        candidate = decision.chosen.candidate
        if decision.chosen.score >= anchor.chosen.score:
            continue
        if not agree("legal_name", anchor_name, Fact(candidate.name, tuple(candidate.aliases))):
            decision.status, decision.chosen = REVIEW, None
            decision.reason = f"name differs from the record matched in {anchor_source}"
            log.info("  %s match sent to review: %s", source, decision.reason)


def _flag_duplicates(companies: list[CompanyResult]) -> None:
    """Note input rows that resolved to the same source record. Two spellings of one company in
    the client's list would otherwise be delivered as two companies."""
    seen: dict[tuple[str, str], list[CompanyResult]] = {}
    for company in companies:
        for source, candidate in company.accepted.items():
            seen.setdefault((source, candidate.record_id), []).append(company)
    for (source, record_id), group in seen.items():
        for company in group:
            others = [f"'{other.input.name}'" for other in group if other is not company]
            if others:
                note = f"same {source} record ({record_id}) as {', '.join(others)}: possible duplicate"
                company.notes.append(note)
                log.info("  %s: %s", company.input.name, note)
