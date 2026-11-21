from __future__ import annotations

from business_research.models import CONFLICT, CORROBORATED, SINGLE, VERIFIED, Fact, FieldResult
from business_research.normalize import normalize_country, normalize_name, parse_year, website_key

# Headcount moves between a source's snapshots, so close figures count as agreement.
EMPLOYEE_TOLERANCE = 0.05


def _key(field: str, value: str | int) -> str | None:
    if field == "legal_name":
        return normalize_name(str(value)) or None
    if field == "country":
        return normalize_country(str(value))
    if field == "website":
        return website_key(str(value))
    if field == "founded_year":
        year = parse_year(value)
        return str(year) if year else None
    return " ".join(str(value).lower().split()) or None


def _keys(field: str, fact: Fact) -> set[str]:
    return {k for k in (_key(field, v) for v in (fact.value, *fact.alt)) if k}


def agree(field: str, a: Fact, b: Fact) -> bool:
    if field == "employees":
        try:
            x, y = float(a.value), float(b.value)
        except (TypeError, ValueError):
            return False
        return abs(x - y) <= EMPLOYEE_TOLERANCE * max(x, y)
    return bool(_keys(field, a) & _keys(field, b))


def verify_field(
    field: str, entries: list[tuple[str, Fact]], groups: dict[str, str] | None = None
) -> FieldResult | None:
    """Compare one field across sources.

    verified: sources from two or more independent groups, all agreeing after normalization.
    corroborated: two or more sources agree, but they are related (one feeds the other, or
    both take the value from the same place), so the agreement is weaker evidence.
    single-source: only one source has the field.
    conflict: sources disagree. Every value is kept and shown.
    """
    if not entries:
        return None
    if len(entries) == 1:
        return FieldResult(SINGLE, entries[0][1].value, entries)

    # Clusters hold positions in `entries`, so each group keeps source-priority order.
    clusters: list[list[int]] = []
    for i, (_, fact) in enumerate(entries):
        joined = [g for g in clusters if any(agree(field, fact, entries[j][1]) for j in g)]
        clusters = [g for g in clusters if g not in joined] + [sorted([i] + [j for g in joined for j in g])]
    if len(clusters) == 1:
        # `groups` maps a source to its independence group. Without it every source stands alone.
        independent = {(groups or {}).get(source, source) for source, _ in entries}
        status = VERIFIED if len(independent) >= 2 else CORROBORATED
        return FieldResult(status, entries[0][1].value, entries)
    shown = " | ".join(
        f"{entries[g[0]][1].value} [{', '.join(entries[j][0] for j in g)}]" for g in sorted(clusters)
    )
    return FieldResult(CONFLICT, shown, entries)
