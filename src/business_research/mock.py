"""Seeded fake sources over a truth set of invented companies.

Used by the benchmark and by `business-research demo`. Nothing here touches the network and
no company in it is real: names are built from made-up syllables.

Each fake source covers only part of the truth set and renders names its own way. Fields have
planted gaps and planted disagreements, and the truth set holds look-alike companies (same name
in another country, a "Group" sibling, a one-letter-off name) so a careless matcher has
something to get wrong. Every planted value is tagged, which lets the benchmark know what the
right answer was without asking the tool.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from business_research.models import Candidate, CompanyInput, Fact

ONSETS = (
    "Vel Or Kas Tre Mur Zan Bri Lun Fen Qua Hal Dor Nim Sav Tal Wex Yar Pol Gri Ced Ulm Jor Eph Ryn".split()
)
MIDS = "tra min da lo ve ri nu sta ko be zu pha gan mo".split()
ENDS = "x ra on is um ar el ia or en yx ek".split()
SECTORS = {
    "Logistics": "freight transport", "Foods": "food processing", "Energy": "electric power",
    "Systems": "information technology", "Pharma": "pharmaceuticals", "Textiles": "textile manufacturing",
    "Robotics": "industrial automation", "Insurance": "insurance", "Mining": "mining", "Software": "software",
    "Retail": "retail", "Shipping": "maritime transport", "Motors": "automotive", "Media": "mass media",
    "Chemicals": "chemicals", "Telecom": "telecommunications", "Aerospace": "aerospace",
    "Tools": "machine tools",
}  # fmt: skip
# country -> list of (suffix as registered, other spellings of the same legal form)
SUFFIXES = {
    "US": [("Inc.", ["Incorporated", "Inc"]), ("Corporation", ["Corp.", "Corp"]), ("LLC", ["L.L.C."])],
    "GB": [("Ltd", ["Limited", "Ltd."]), ("PLC", ["plc", "Public Limited Company"])],
    "DE": [("GmbH", ["Gesellschaft mit beschränkter Haftung"]), ("AG", ["Aktiengesellschaft"])],
    "FR": [("SA", ["S.A.", "Société Anonyme"]), ("SAS", ["S.A.S."])],
    "JP": [("K.K.", ["Kabushiki Kaisha", "KK"]), ("Co., Ltd.", ["Co. Ltd", "Company Limited"])],
    "NL": [("B.V.", ["BV"]), ("N.V.", ["NV"])],
    "SE": [("AB", ["A.B."])],
    "IT": [("S.p.A.", ["SpA"]), ("S.r.l.", ["SRL"])],
}
COUNTRIES = list(SUFFIXES)

SOURCE_SPECS = (
    # name, coverage, fields it can hold
    ("registry", 0.80, ("legal_name", "country", "lei")),
    (
        "encyclopedia",
        0.70,
        ("legal_name", "country", "website", "industry", "founded_year", "employees", "lei"),
    ),
    ("directory", 0.60, ("legal_name", "country", "website", "founded_year")),
)
# The directory is modelled as copying from the encyclopedia, the way real open datasets feed
# each other. The two agreeing is corroboration, not independent verification.
SOURCE_GROUPS = {"registry": "registry", "encyclopedia": "reference", "directory": "reference"}
P_FIELD_MISSING = 0.15
P_FIELD_DIFFERENT = 0.08
P_SOURCE_TYPO = 0.03
P_LINK = 0.5
P_LINK_WRONG = 0.03


@dataclass
class TruthCompany:
    truth_id: int
    core: str
    suffix: str
    suffix_alts: list[str]
    country: str
    website: str
    industry: str
    founded_year: int
    employees: int
    lei: str

    @property
    def name(self) -> str:
        return f"{self.core} {self.suffix}"


@dataclass
class MockRecord:
    record_id: str
    truth_id: int
    name: str
    aliases: list[str]
    facts: dict[str, Fact]
    # field -> "same" (agrees with truth after normalization) or "diff" (planted disagreement)
    tags: dict[str, str]
    links: dict[str, list[str]] = field(default_factory=dict)


class MockSource:
    """A fake source behind the same adapter interface the live sources use."""

    terms_url = "mock source, no network"
    licence = "invented data"
    attribution = "none"
    redistribution_ok = True

    def __init__(self, name: str, records: list[MockRecord], retrieved: str = "2026-01-01T00:00:00Z"):
        self.name = name
        self.label = f"mock {name}"
        self.group = SOURCE_GROUPS.get(name, name)
        self.records = {r.record_id: r for r in records}
        self.retrieved = retrieved
        self._names = {r.record_id: r.name.lower() for r in records}

    def search(self, name: str, country: str | None) -> list[Candidate]:
        # A loose scorer on purpose, like a real search box: it returns near misses and
        # look-alikes, and leaves judging them to the tool.
        hits = process.extract(name.lower(), self._names, scorer=fuzz.WRatio, limit=8, score_cutoff=75)
        return [self._candidate(self.records[record_id]) for _, _, record_id in hits]

    def fetch(self, record_id: str) -> Candidate | None:
        record = self.records.get(record_id)
        return self._candidate(record) if record else None

    def _candidate(self, record: MockRecord) -> Candidate:
        country = record.facts.get("country")
        return Candidate(
            source=self.name,
            record_id=record.record_id,
            name=record.name,
            aliases=list(record.aliases),
            countries={str(country.value)} if country else set(),
            url=f"mock://{self.name}/{record.record_id}",
            retrieved=self.retrieved,
            facts=dict(record.facts),
            links={k: list(v) for k, v in record.links.items()},
        )


@dataclass
class World:
    seed: int
    truth: list[TruthCompany]
    sources: list[MockSource]
    inputs: list[CompanyInput]
    # input name -> truth id, or None for a name that is in no source
    expected: dict[str, int | None]


def generate(seed: int = 7, n_companies: int = 300, n_inputs: int = 250, n_absent: int = 30) -> World:
    rng = random.Random(seed)
    stems = [o + m + e for o in ONSETS for m in MIDS for e in ENDS]
    rng.shuffle(stems)
    stem_iter = iter(stems)

    truth: list[TruthCompany] = []

    def add(core: str, country: str, sector: str) -> TruthCompany:
        suffix, alts = rng.choice(SUFFIXES[country])
        domain = "".join(ch for ch in core.lower() if ch.isalnum())[:24]
        company = TruthCompany(
            truth_id=len(truth),
            core=core,
            suffix=suffix,
            suffix_alts=alts,
            country=country,
            website=f"{domain}{len(truth)}.example",
            industry=SECTORS[sector],
            founded_year=rng.randint(1890, 2019),
            employees=rng.randint(40, 90000),
            lei="".join(rng.choices(string.ascii_uppercase + string.digits, k=18))
            + f"{rng.randint(0, 99):02d}",
        )
        truth.append(company)
        return company

    while len(truth) < n_companies:
        stem, sector = next(stem_iter), rng.choice(list(SECTORS))
        country = rng.choice(COUNTRIES)
        core = stem if rng.random() < 0.15 else f"{stem} {sector}"
        add(core, country, sector)
        roll = rng.random()
        if roll < 0.08:  # same name, another country
            add(core, rng.choice([c for c in COUNTRIES if c != country]), sector)
        elif roll < 0.14:  # a sibling entity in the same country
            add(f"{core} {rng.choice(['Group', 'Holdings', 'International'])}", country, sector)
        elif roll < 0.20:  # a different company whose name is one letter off
            add(core.replace(stem, _one_letter_off(stem, rng)), rng.choice(COUNTRIES), sector)

    sources, by_source = [], {}
    for name, coverage, fields in SOURCE_SPECS:
        records = [_render(rng, name, company, fields, truth) for company in truth if rng.random() < coverage]
        by_source[name] = {r.truth_id: r for r in records}
        sources.append(MockSource(name, records))

    # Some encyclopedia records point at the registry record, as real sources cross-reference
    # each other. A few of those pointers are wrong, as real ones are.
    for record in by_source["encyclopedia"].values():
        if rng.random() >= P_LINK:
            continue
        target = by_source["registry"].get(record.truth_id)
        if rng.random() < P_LINK_WRONG:
            target = rng.choice(list(by_source["registry"].values()))
        if target:
            record.links["registry"] = [target.record_id]

    inputs: list[CompanyInput] = []
    expected: dict[str, int | None] = {}
    for company in rng.sample(truth, min(n_inputs, len(truth))):
        name = _input_name(rng, company)
        if name in expected:
            continue
        expected[name] = company.truth_id
        given = company.country if rng.random() < 0.6 else None
        inputs.append(CompanyInput(name=name, country=given, country_raw=given or "", row=len(inputs) + 2))
    for _ in range(n_absent):
        sector = rng.choice(list(SECTORS))
        country = rng.choice(COUNTRIES)
        name = f"{next(stem_iter)} {sector} {rng.choice(SUFFIXES[country])[0]}"
        expected[name] = None
        inputs.append(CompanyInput(name=name, country=country, country_raw=country, row=len(inputs) + 2))
    return World(seed, truth, sources, inputs, expected)


def _one_letter_off(stem: str, rng: random.Random) -> str:
    position = rng.randrange(1, len(stem))
    letter = rng.choice([c for c in "aeiou" if c != stem[position].lower()])
    return stem[:position] + letter + stem[position + 1 :]


def _typo(text: str, rng: random.Random) -> str:
    position = rng.randrange(1, max(2, len(text.split()[0]) - 1))
    if rng.random() < 0.5:
        return text[:position] + text[position + 1 :]
    return text[:position] + text[position + 1] + text[position] + text[position + 2 :]


def _input_name(rng: random.Random, company: TruthCompany) -> str:
    """How a client might have typed the company into their list."""
    roll = rng.random()
    if roll < 0.40:
        return company.name
    if roll < 0.65:
        return company.core
    if roll < 0.80:
        return f"{company.core} {rng.choice(company.suffix_alts)}"
    if roll < 0.90:
        return company.name.upper()
    return f"{_typo(company.core, rng)} {company.suffix}"


def _render(
    rng: random.Random, source: str, company: TruthCompany, fields: tuple[str, ...], truth
) -> MockRecord:
    tags: dict[str, str] = {}
    facts: dict[str, Fact] = {}

    roll = rng.random()
    aliases: list[str] = []
    if roll < 0.45:
        name = company.name
    elif roll < 0.65:
        name = f"{company.core} {rng.choice(company.suffix_alts)}"
    elif roll < 0.80:
        name = company.core
    elif roll < 0.90:
        name = company.name.upper()
    else:
        # Listed under a trading name, with the legal name kept as an alias.
        name, aliases = f"{company.core} Worldwide", [company.name]
    name_tag = "same"
    if rng.random() < P_SOURCE_TYPO:
        name, aliases, name_tag = f"{_typo(company.core, rng)} {company.suffix}", [], "diff"
    facts["legal_name"], tags["legal_name"] = Fact(name, tuple(aliases)), name_tag

    for field_name in fields:
        if field_name == "legal_name" or rng.random() < P_FIELD_MISSING:
            continue
        different = rng.random() < P_FIELD_DIFFERENT
        value: str | int
        tags[field_name] = "diff" if different else "same"
        if field_name == "country":
            value = (
                rng.choice([c for c in COUNTRIES if c != company.country]) if different else company.country
            )
        elif field_name == "lei":
            value = rng.choice([c for c in truth if c is not company]).lei if different else company.lei
        elif field_name == "website":
            if different:
                value = f"https://www.{company.website.replace('.example', '-global.example')}"
            else:
                value = rng.choice(["https://www.{}", "http://{}/", "https://{}/en/about", "www.{}"]).format(
                    company.website
                )
        elif field_name == "industry":
            other = rng.choice([v for v in SECTORS.values() if v != company.industry])
            value = other if different else rng.choice([company.industry, company.industry.title()])
        elif field_name == "founded_year":
            value = (
                company.founded_year + rng.choice([-1, 1]) * rng.randint(1, 30)
                if different
                else company.founded_year
            )
        else:  # employees: sources snapshot headcount at different times, so "same" still wobbles
            factor = (
                rng.choice([rng.uniform(0.3, 0.7), rng.uniform(1.4, 3.0)])
                if different
                else rng.uniform(0.98, 1.02)
            )
            value = max(1, round(company.employees * factor))
        facts[field_name] = Fact(value)
    return MockRecord(f"{source[:3]}-{company.truth_id:04d}", company.truth_id, name, aliases, facts, tags)
