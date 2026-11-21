from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from business_research.matching import name_score
from business_research.models import Candidate, Fact
from business_research.normalize import normalize_country

# Search returns records at or above this name score. Matching applies the real thresholds later.
SEARCH_FLOOR = 60.0
SEARCH_LIMIT = 10
TEXT_FIELDS = ("lei", "website", "industry", "sic_industry")
INT_FIELDS = ("founded_year", "employees")
# Columns that name a record in another source. The pipeline follows them like any other link.
LINK_COLUMNS = {"wikidata_id": "wikidata", "lei": "gleif", "ror_id": "ror"}


class CsvSource:
    """A reference list the user supplies, used as one more source.

    The file needs `id` and `name` columns. Optional columns: `aliases` (split on `;`),
    `country`, `url`, `lei`, `website`, `industry`, `sic_industry`, `founded_year`, `employees`,
    `wikidata_id`, `ror_id`. Facts from the file get the same cross-check as facts from a public
    source, so a client's own list can confirm or contradict GLEIF, ROR and Wikidata.
    """

    terms_url = "supplied by the user"
    attribution = "None required"

    def __init__(
        self,
        path: str | Path,
        name: str = "reference",
        label: str | None = None,
        licence: str = "supplied by the user",
        redistribution_ok: bool = True,
        group: str | None = None,
    ):
        self.path = Path(path)
        self.name = name
        self.label = label or f"Reference list ({self.path.name})"
        self.licence = licence
        self.redistribution_ok = redistribution_ok
        # Independent of the public sources unless the user says the list was built from one.
        self.group = group or "user-supplied"
        # The file's own modification time stands in for the retrieval time of every record.
        self.retrieved = datetime.fromtimestamp(self.path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.records = self._load()

    def search(self, name: str, country: str | None) -> list[Candidate]:
        scored = []
        for candidate in self.records.values():
            best = max(name_score(name, n) for n in [candidate.name, *candidate.aliases])
            if best >= SEARCH_FLOOR:
                scored.append((best, candidate))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [_copy(c) for _, c in scored[:SEARCH_LIMIT]]

    def fetch(self, record_id: str) -> Candidate | None:
        record = self.records.get(record_id)
        return _copy(record) if record else None

    def _load(self) -> dict[str, Candidate]:
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = {(h or "").strip().lower() for h in reader.fieldnames or []}
            missing = {"id", "name"} - header
            if missing:
                raise ValueError(f"{self.path.name} needs columns {', '.join(sorted(missing))}")
            records: dict[str, Candidate] = {}
            for line, raw in enumerate(reader, 2):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
                if not row["id"] or not row["name"]:
                    continue
                if row["id"] in records:
                    raise ValueError(f"{self.path.name} line {line}: duplicate id {row['id']}")
                records[row["id"]] = self._candidate(row, line)
        return records

    def _candidate(self, row: dict[str, str], line: int) -> Candidate:
        aliases = [a.strip() for a in row.get("aliases", "").split(";") if a.strip()]
        facts = {"legal_name": Fact(row["name"], tuple(aliases))}
        country = normalize_country(row.get("country", ""))
        if row.get("country") and not country:
            raise ValueError(f"{self.path.name} line {line}: unknown country {row['country']!r}")
        if country:
            facts["country"] = Fact(country)
        for field in TEXT_FIELDS:
            if row.get(field):
                facts[field] = Fact(row[field])
        for field in INT_FIELDS:
            if row.get(field):
                try:
                    facts[field] = Fact(int(row[field].replace(",", "")))
                except ValueError:
                    raise ValueError(
                        f"{self.path.name} line {line}: {field} must be a whole number, got {row[field]!r}"
                    ) from None
        links = {source: [row[column]] for column, source in LINK_COLUMNS.items() if row.get(column)}
        return Candidate(
            source=self.name,
            record_id=row["id"],
            name=row["name"],
            aliases=aliases,
            countries={country} if country else set(),
            url=row.get("url") or f"{self.path.name}#line={line}",
            retrieved=self.retrieved,
            facts=facts,
            links=links,
        )


def _copy(candidate: Candidate) -> Candidate:
    """The pipeline marks candidates (via, linked), so every answer is a fresh object."""
    return Candidate(
        source=candidate.source,
        record_id=candidate.record_id,
        name=candidate.name,
        url=candidate.url,
        retrieved=candidate.retrieved,
        aliases=list(candidate.aliases),
        countries=set(candidate.countries),
        facts=dict(candidate.facts),
        links={k: list(v) for k, v in candidate.links.items()},
    )
