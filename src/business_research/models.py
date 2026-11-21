from __future__ import annotations

from dataclasses import dataclass, field

FIELDS = ("legal_name", "country", "lei", "website", "industry", "sic_industry", "founded_year", "employees")

ACCEPTED = "accepted"
REVIEW = "review"
NONE = "none"

VERIFIED = "verified"
CORROBORATED = "corroborated"
SINGLE = "single-source"
CONFLICT = "conflict"


@dataclass(frozen=True)
class Fact:
    """One field value from one source. `alt` holds other values the source treats as equivalent
    (aliases, a second registered LEI), used only when comparing sources."""

    value: str | int
    alt: tuple[str | int, ...] = ()


@dataclass
class Candidate:
    """A source record that might be the company the client asked about."""

    source: str
    record_id: str
    name: str
    url: str
    retrieved: str
    aliases: list[str] = field(default_factory=list)
    countries: set[str] = field(default_factory=set)
    facts: dict[str, Fact] = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    active: bool = True
    via: str = "search"
    # When the source itself last changed the record, where it says so.
    as_of: str = ""
    # Another source's matched record, or the input file, named this exact record.
    linked: bool = False


@dataclass
class CompanyInput:
    name: str
    country: str | None = None
    country_raw: str = ""
    ids: dict[str, str] = field(default_factory=dict)
    row: int = 0


@dataclass
class Scored:
    candidate: Candidate
    score: float
    matched_on: str
    country_match: bool | None


@dataclass
class MatchDecision:
    status: str
    chosen: Scored | None = None
    scored: list[Scored] = field(default_factory=list)
    reason: str = ""


@dataclass
class FieldResult:
    status: str
    value: str | int
    entries: list[tuple[str, Fact]]


@dataclass
class CompanyResult:
    input: CompanyInput
    decisions: dict[str, MatchDecision] = field(default_factory=dict)
    fields: dict[str, FieldResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> dict[str, Candidate]:
        return {s: d.chosen.candidate for s, d in self.decisions.items() if d.status == ACCEPTED and d.chosen}

    @property
    def status(self) -> str:
        if self.accepted:
            return "matched"
        if any(d.status == REVIEW for d in self.decisions.values()):
            return "review"
        return "not_found"


@dataclass
class RunResult:
    companies: list[CompanyResult]
    sources: list[dict[str, str]]
    started: str
    finished: str
    stats: dict[str, int] = field(default_factory=dict)
    settings: dict[str, object] = field(default_factory=dict)
