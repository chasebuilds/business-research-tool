from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import load_workbook

from business_research.models import CompanyInput
from business_research.normalize import normalize_country

NAME_HEADERS = (
    "company",
    "company name",
    "company_name",
    "name",
    "organization",
    "organisation",
    "legal name",
)
COUNTRY_HEADERS = ("country", "country code", "country_code", "hq country")
ID_HEADERS = {
    "wikidata": ("wikidata_id", "wikidata", "qid"),
    "gleif": ("lei",),
    "ror": ("ror_id", "ror"),
    "reference": ("reference_id",),
}


def read_companies(path: str | Path) -> list[CompanyInput]:
    """Read a CSV or XLSX list of companies. Needs a name column, takes an optional country
    column and optional id columns (wikidata_id, lei, ror_id, reference_id)."""
    path = Path(path)
    rows = _read_xlsx(path) if path.suffix.lower() in (".xlsx", ".xlsm") else _read_csv(path)
    if not rows:
        return []
    header = [str(h or "").strip().lower() for h in rows[0]]
    name_col = _find(header, NAME_HEADERS)
    if name_col is None:
        raise ValueError(f"no company name column in {path.name}: expected one of {', '.join(NAME_HEADERS)}")
    country_col = _find(header, COUNTRY_HEADERS)
    id_cols = {source: _find(header, names) for source, names in ID_HEADERS.items()}

    companies, seen = [], set()
    for number, row in enumerate(rows[1:], 2):

        def cell(col: int | None, row=row) -> str:
            return str(row[col]).strip() if col is not None and col < len(row) and row[col] else ""

        name = cell(name_col)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        country_raw = cell(country_col)
        companies.append(
            CompanyInput(
                name=name,
                country=normalize_country(country_raw),
                country_raw=country_raw,
                ids={source: cell(col) for source, col in id_cols.items() if cell(col)},
                row=number,
            )
        )
    return companies


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    return next((header.index(n) for n in names if n in header), None)


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [row for row in csv.reader(handle) if any(cell.strip() for cell in row)]


def _read_xlsx(path: Path) -> list[list[object]]:
    sheet = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    return [
        list(row) for row in sheet.iter_rows(values_only=True) if any(cell not in (None, "") for cell in row)
    ]
