from __future__ import annotations

from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from business_research.models import (
    ACCEPTED,
    CONFLICT,
    CORROBORATED,
    FIELDS,
    NONE,
    REVIEW,
    SINGLE,
    VERIFIED,
    RunResult,
)

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STATUS_FILLS = {
    VERIFIED: PatternFill("solid", fgColor="D1FAE5"),
    CORROBORATED: PatternFill("solid", fgColor="DBEAFE"),
    SINGLE: PatternFill("solid", fgColor="FEF3C7"),
    CONFLICT: PatternFill("solid", fgColor="FECACA"),
    "matched": PatternFill("solid", fgColor="D1FAE5"),
    "review": PatternFill("solid", fgColor="FEF3C7"),
    "not_found": PatternFill("solid", fgColor="E5E7EB"),
}


def write_workbook(run: RunResult, path: str | Path) -> Path:
    """Write the deliverable: Companies, Sources, Review, NotFound, Summary, Licences."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    _sheet(book.active, "Companies", *_companies(run))
    _sheet(book.create_sheet(), "Sources", *_sources(run))
    _sheet(book.create_sheet(), "Review", *_review(run))
    _sheet(book.create_sheet(), "NotFound", *_not_found(run))
    _sheet(book.create_sheet(), "Summary", ["item", "value"], summary_rows(run))
    _sheet(book.create_sheet(), "Licences", *_licences(run))
    book.save(path)
    return path


def _companies(run: RunResult):
    header = ["input_name", "input_country", "row_status", "matched_sources", "match_detail"]
    for field in FIELDS:
        header += [field, f"{field}_status"]
    header += ["review_candidates", "notes"]
    rows = []
    for company in run.companies:
        detail = "; ".join(
            f"{source} {d.chosen.score:g} ({d.chosen.candidate.via})"
            for source, d in company.decisions.items()
            if d.status == ACCEPTED and d.chosen
        )
        row = [
            company.input.name,
            company.input.country or company.input.country_raw,
            company.status,
            ", ".join(company.accepted),
            detail,
        ]
        for field in FIELDS:
            result = company.fields.get(field)
            row += [result.value, result.status] if result else ["", ""]
        pending = sum(len(d.scored) for d in company.decisions.values() if d.status == REVIEW)
        row += [pending, "; ".join(company.notes + company.errors)]
        rows.append(row)
    return header, rows


def _sources(run: RunResult):
    header = [
        "input_name",
        "field",
        "value",
        "field_status",
        "source",
        "source_record_id",
        "url",
        "retrieved_utc",
        "source_last_updated",
        "licence",
    ]
    licence = {s["name"]: s["licence"] for s in run.sources}
    rows = []
    for company in run.companies:
        accepted = company.accepted
        for field in FIELDS:
            result = company.fields.get(field)
            for source, fact in result.entries if result else []:
                record = accepted[source]
                rows.append(
                    [
                        company.input.name,
                        field,
                        fact.value,
                        result.status,
                        source,
                        record.record_id,
                        record.url,
                        record.retrieved,
                        record.as_of,
                        licence.get(source, ""),
                    ]
                )
    return header, rows


def _review(run: RunResult):
    header = [
        "input_name",
        "input_country",
        "source",
        "reason",
        "candidate_name",
        "matched_on",
        "candidate_id",
        "candidate_country",
        "score",
        "found_via",
        "url",
        "retrieved_utc",
    ]
    rows = []
    for company in run.companies:
        for source, decision in company.decisions.items():
            if decision.status != REVIEW:
                continue
            for scored in decision.scored:
                c = scored.candidate
                rows.append(
                    [
                        company.input.name,
                        company.input.country or "",
                        source,
                        decision.reason,
                        c.name,
                        scored.matched_on,
                        c.record_id,
                        ", ".join(sorted(c.countries)),
                        scored.score,
                        c.via,
                        c.url,
                        c.retrieved,
                    ]
                )
    return header, rows


def _not_found(run: RunResult):
    header = ["input_name", "input_country", "sources_tried", "notes"]
    rows = [
        [c.input.name, c.input.country or c.input.country_raw, ", ".join(c.decisions), "; ".join(c.errors)]
        for c in run.companies
        if c.status == "not_found"
    ]
    return header, rows


def _licences(run: RunResult):
    header = ["source", "licence", "attribution", "redistribution", "independence_group", "terms_url"]
    rows = [
        [s["label"], s["licence"], s["attribution"], s["redistribution"], s["group"], s["terms_url"]]
        for s in run.sources
    ]
    return header, rows


def summary_rows(run: RunResult) -> list[list[object]]:
    by_status = Counter(c.status for c in run.companies)
    field_status = Counter(r.status for c in run.companies for r in c.fields.values())
    asked = len(run.companies)
    rows: list[list[object]] = [
        ["run started (UTC)", run.started],
        ["run finished (UTC)", run.finished],
        ["companies in input", asked],
        ["matched in at least one source", by_status["matched"]],
        ["sent to review only", by_status["review"]],
        ["not found", by_status["not_found"]],
        ["companies matched in two or more sources", sum(len(c.accepted) >= 2 for c in run.companies)],
        ["input rows flagged as possible duplicates", sum(bool(c.notes) for c in run.companies)],
        ["field values: verified (independent sources agree)", field_status[VERIFIED]],
        ["field values: corroborated (related sources agree)", field_status[CORROBORATED]],
        ["field values: single-source", field_status[SINGLE]],
        ["field values: conflict", field_status[CONFLICT]],
        ["accept score threshold", run.settings.get("accept_score", "")],
        ["review score threshold", run.settings.get("review_score", "")],
    ]
    for source in run.sources:
        outcome = Counter(
            c.decisions[source["name"]].status for c in run.companies if source["name"] in c.decisions
        )
        failed = sum(any(e.startswith(source["name"] + ":") for e in c.errors) for c in run.companies)
        rows.append(
            [
                f"coverage: {source['label']}",
                f"asked {asked}, matched {outcome[ACCEPTED]}, review {outcome[REVIEW]}, "
                f"not found {outcome[NONE]}, rows with a failed request {failed}",
            ]
        )
    for key in sorted(run.stats):
        rows.append([f"http: {key}", run.stats[key]])
    return rows


def _sheet(sheet, title: str, header: list[str], rows: list[list[object]]) -> None:
    sheet.title = title
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    for cell in sheet[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
    for index, name in enumerate(header, 1):
        longest = max([len(name)] + [len(str(row[index - 1])) for row in rows if row[index - 1] is not None])
        sheet.column_dimensions[get_column_letter(index)].width = min(60, longest + 2)
        if name.endswith("status"):
            for cell in sheet[get_column_letter(index)][1:]:
                if fill := STATUS_FILLS.get(cell.value):
                    cell.fill = fill
