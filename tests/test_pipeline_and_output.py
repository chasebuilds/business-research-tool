import csv

from openpyxl import Workbook, load_workbook

from business_research.cli import main
from business_research.inputs import read_companies
from business_research.models import (
    ACCEPTED,
    CONFLICT,
    CORROBORATED,
    NONE,
    REVIEW,
    SINGLE,
    VERIFIED,
    Candidate,
    CompanyInput,
    Fact,
)
from business_research.pipeline import research
from business_research.sources import build_adapters
from business_research.workbook import write_workbook

from conftest import FakeReply


class StubSource:
    terms_url, licence, attribution, redistribution_ok = "stub", "stub licence", "none", True

    def __init__(self, name, records, fail=False):
        self.name, self.label, self.records, self.fail = name, name, records, fail
        self.group = name

    def search(self, name, country):
        if self.fail:
            raise ValueError("boom")
        return [r for r in self.records if r.via == "search"]

    def fetch(self, record_id):
        return next((r for r in self.records if r.record_id == record_id), None)


def record(source, record_id, name, country="DE", links=None, via="search", **facts):
    return Candidate(
        source, record_id, name, f"stub://{source}/{record_id}", "2026-01-02T03:04:05Z",
        countries={country}, facts={"legal_name": Fact(name), **{k: Fact(v) for k, v in facts.items()}},
        links=links or {}, via=via,
    )  # fmt: skip


SHEETS = ["Companies", "Sources", "Review", "NotFound", "Summary", "Licences"]


def sheet_rows(path, name):
    rows = list(load_workbook(path)[name].iter_rows(values_only=True))
    return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


def test_recorded_siemens_run_end_to_end(make_client, siemens_routes, tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    client, session = make_client(siemens_routes)
    run = research([CompanyInput("Siemens AG", "DE", "Germany")], build_adapters(client))
    company = run.companies[0]
    assert company.status == "matched"
    assert list(company.accepted) == ["gleif", "ror", "wikidata"]
    assert company.accepted["wikidata"].via == "link from ror"
    assert company.fields["legal_name"].status == VERIFIED
    assert company.fields["country"].status == VERIFIED
    # The LEI on Wikidata came from GLEIF, and ROR and Wikidata feed each other, so agreement
    # there is corroboration and not independent verification.
    assert company.fields["lei"].status == CORROBORATED
    assert company.fields["founded_year"].status == CORROBORATED
    assert company.fields["industry"].status == SINGLE
    # ROR records a regional landing page, Wikidata the main site: same registrable domain.
    assert company.fields["website"].status == CORROBORATED

    out = write_workbook(run, tmp_path / "out.xlsx")
    assert load_workbook(out).sheetnames == SHEETS
    row = sheet_rows(out, "Companies")[0]
    assert row["row_status"] == "matched" and row["lei_status"] == CORROBORATED
    sources = sheet_rows(out, "Sources")
    assert {s["source"] for s in sources} == {"gleif", "ror", "wikidata"}
    assert all(s["url"].startswith("https://") and s["retrieved_utc"].endswith("Z") for s in sources)
    lei_rows = [s for s in sources if s["field"] == "lei"]
    assert {s["source"] for s in lei_rows} == {"gleif", "wikidata"}


def test_without_a_country_the_ror_branches_go_to_review(make_client, siemens_routes, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    routes = dict(siemens_routes)
    # A Siemens Energy record that also displays as plain "Siemens" makes two exact name matches.
    routes["https://api.ror.org/v2/organizations?"] = routes["https://api.ror.org/v2/organizations?"].replace(
        '"Siemens Energy"', '"Siemens (Energy)"'
    )
    client, _ = make_client(routes)
    company = research([CompanyInput("Siemens")], build_adapters(client)).companies[0]
    assert company.decisions["ror"].status == REVIEW
    assert "wikidata" not in company.accepted  # no accepted ROR record, so no link to follow


def test_low_score_candidates_land_in_review_and_not_in_companies_facts(tmp_path):
    source = StubSource("a", [record("a", "1", "Exemple Corporation", website="https://x.test")])
    run = research([CompanyInput("Example Corp")], [source])
    company = run.companies[0]
    assert company.status == "review" and company.fields == {}
    out = write_workbook(run, tmp_path / "out.xlsx")
    assert sheet_rows(out, "Companies")[0]["website"] in ("", None)
    review = sheet_rows(out, "Review")
    assert review[0]["candidate_name"] == "Exemple Corporation" and review[0]["score"] < 90
    assert sheet_rows(out, "Sources") == [] and sheet_rows(out, "NotFound") == []


def test_not_found_sheet_lists_names_with_no_candidate(tmp_path):
    run = research([CompanyInput("Nowhere Ltd", "GB", "UK")], [StubSource("a", []), StubSource("b", [])])
    assert run.companies[0].decisions["a"].status == NONE
    rows = sheet_rows(write_workbook(run, tmp_path / "out.xlsx"), "NotFound")
    assert rows == [
        {"input_name": "Nowhere Ltd", "input_country": "GB", "sources_tried": "a, b", "notes": None}
    ]


def test_conflict_keeps_both_values_in_both_sheets(tmp_path):
    a = StubSource("a", [record("a", "1", "Example GmbH", founded_year=1990)])
    b = StubSource("b", [record("b", "9", "Example", founded_year=1987)])
    run = research([CompanyInput("Example GmbH")], [a, b])
    assert run.companies[0].fields["founded_year"].status == CONFLICT
    out = write_workbook(run, tmp_path / "out.xlsx")
    assert sheet_rows(out, "Companies")[0]["founded_year"] == "1990 [a] | 1987 [b]"
    years = [s["value"] for s in sheet_rows(out, "Sources") if s["field"] == "founded_year"]
    assert years == [1990, 1987]


def test_link_is_followed_but_the_linked_record_must_still_match_by_name():
    good = StubSource("b", [record("b", "L1", "Example GmbH", via="lookup")])
    bad = StubSource("b", [record("b", "L1", "Unrelated Trading GmbH", via="lookup")])
    a = [record("a", "1", "Example GmbH", links={"b": ["L1"]})]
    followed = research([CompanyInput("Example GmbH")], [StubSource("a", a), good]).companies[0]
    assert followed.accepted["b"].via == "link from a"
    refused = research([CompanyInput("Example GmbH")], [StubSource("a", a), bad]).companies[0]
    assert "b" not in refused.accepted


def test_fuzzy_match_with_a_different_name_than_a_better_match_is_demoted():
    a = StubSource("a", [record("a", "1", "Dorloel Robotics GmbH")])
    b = StubSource("b", [record("b", "2", "Dorlool Robotics GmbH")])
    company = research([CompanyInput("Dorloel Robotics GmbH")], [a, b]).companies[0]
    assert company.decisions["a"].status == ACCEPTED
    assert company.decisions["b"].status == REVIEW
    assert "name differs" in company.decisions["b"].reason


def test_a_failing_source_is_reported_and_does_not_sink_the_row():
    ok = StubSource("a", [record("a", "1", "Example GmbH")])
    company = research([CompanyInput("Example GmbH")], [ok, StubSource("b", [], fail=True)]).companies[0]
    assert company.status == "matched"
    assert company.errors == ["b: search failed (boom)"]


def test_blocked_host_is_reported_per_row(make_client, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    client, session = make_client({"https://api.gleif.org/robots.txt": "User-agent: *\nDisallow: /\n"})
    company = research([CompanyInput("Example")], build_adapters(client, ["gleif"])).companies[0]
    assert company.status == "not_found"
    assert "robots.txt" in company.errors[0]
    assert session.data_calls() == []


def test_read_companies_from_csv_and_xlsx(tmp_path):
    csv_path = tmp_path / "in.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(
            [["Company Name", "Country", "wikidata_id"], ["Example GmbH", "Germany", "Q1"], ["", "US", ""],
             ["example gmbh", "DE", ""], ["Sample Inc", "Atlantis", ""]]
        )  # fmt: skip
    companies = read_companies(csv_path)
    assert [(c.name, c.country, c.ids) for c in companies] == [
        ("Example GmbH", "DE", {"wikidata": "Q1"}),
        ("Sample Inc", None, {}),
    ]
    assert companies[1].country_raw == "Atlantis"

    book = Workbook()
    book.active.append(["name", "website"])
    book.active.append(["Example GmbH", "https://example.test"])
    book.save(tmp_path / "in.xlsx")
    assert [c.name for c in read_companies(tmp_path / "in.xlsx")] == ["Example GmbH"]


def test_id_column_feeds_a_source_that_cannot_search(make_client, siemens_routes):
    client, _ = make_client(siemens_routes)
    company = research(
        [CompanyInput("Siemens", "DE", ids={"wikidata": "Q81230"})], build_adapters(client, ["wikidata"])
    ).companies[0]
    assert company.accepted["wikidata"].via == "id in input"


def test_cli_run_writes_workbook_and_log(tmp_path, monkeypatch, siemens_routes, clock):
    from conftest import FakeSession

    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    monkeypatch.chdir(tmp_path)
    session = FakeSession(siemens_routes | {"https://api.ror.org/robots.txt": FakeReply(403, "no")})
    monkeypatch.setattr("requests.Session", lambda: session)
    monkeypatch.setattr("time.sleep", clock.sleep)
    (tmp_path / "in.csv").write_text("company,country\nSiemens AG,DE\n", encoding="utf-8")
    assert main(["run", "in.csv", "-o", "out/result.xlsx"]) == 0
    assert (
        sheet_rows(tmp_path / "out/result.xlsx", "Companies")[0]["matched_sources"] == "gleif, ror, wikidata"
    )
    log_text = (tmp_path / "out/result.log").read_text(encoding="utf-8")
    assert (
        "company 1/1: Siemens AG" in log_text
        and "robots https://api.ror.org/robots.txt -> HTTP 403" in log_text
    )
    summary = dict(row.values() for row in sheet_rows(tmp_path / "out/result.xlsx", "Summary"))
    assert summary["matched in at least one source"] == 1 and summary["http: requests"] >= 5


def test_cli_demo_is_offline_and_repeatable(tmp_path, capsys):
    for name in ("one", "two"):
        args = ["demo", "-o", str(tmp_path / f"{name}.xlsx"), "--input-csv", str(tmp_path / f"{name}.csv")]
        assert main(args) == 0
    assert sheet_rows(tmp_path / "one.xlsx", "Companies") == sheet_rows(tmp_path / "two.xlsx", "Companies")
    assert (tmp_path / "one.csv").read_text(encoding="utf-8") == (tmp_path / "two.csv").read_text(
        encoding="utf-8"
    )
    assert "Workbook:" in capsys.readouterr().out


def test_sources_sheet_carries_per_field_provenance(make_client, siemens_routes, tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    client, _ = make_client(siemens_routes)
    run = research([CompanyInput("Siemens AG", "DE")], build_adapters(client))
    rows = sheet_rows(write_workbook(run, tmp_path / "out.xlsx"), "Sources")
    updated = {r["source"]: r["source_last_updated"] for r in rows}
    assert updated == {
        "gleif": "2026-09-01T15:35:21Z",
        "ror": "2026-09-22",
        "wikidata": "2026-11-06T20:33:52Z",
    }
    assert {r["licence"] for r in rows} == {"CC0 1.0"}
    licences = sheet_rows(tmp_path / "out.xlsx", "Licences")
    assert [r["independence_group"] for r in licences] == ["gleif", "ror-wikidata", "ror-wikidata"]
    assert all(r["redistribution"] == "allowed" and r["terms_url"].startswith("https://") for r in licences)


def test_agreement_inside_one_group_is_corroborated_and_across_groups_is_verified():
    a = StubSource("a", [record("a", "1", "Example GmbH", founded_year=1990)])
    b = StubSource("b", [record("b", "2", "Example GmbH", founded_year=1990)])
    assert (
        research([CompanyInput("Example GmbH")], [a, b]).companies[0].fields["founded_year"].status
        == VERIFIED
    )
    b.group = "a"
    related = research([CompanyInput("Example GmbH")], [a, b]).companies[0]
    assert related.fields["founded_year"].status == CORROBORATED
    assert related.fields["legal_name"].status == CORROBORATED


def test_a_source_that_forbids_redistribution_is_refused():
    locked = StubSource("locked", [record("locked", "1", "Example GmbH")])
    locked.redistribution_ok = False
    try:
        research([CompanyInput("Example GmbH")], [locked])
    except ValueError as exc:
        assert "forbid redistribution: locked" in str(exc)
    else:
        raise AssertionError("a source marked not redistributable must not run")


def test_two_input_rows_resolving_to_one_record_are_flagged(tmp_path):
    source = StubSource("a", [record("a", "1", "Example GmbH")])
    run = research(
        [CompanyInput("Example GmbH"), CompanyInput("EXAMPLE"), CompanyInput("Other AG")], [source]
    )
    first, second, third = run.companies
    assert "same a record (1) as 'EXAMPLE': possible duplicate" in first.notes[0]
    assert "'Example GmbH'" in second.notes[0] and third.notes == []
    rows = sheet_rows(write_workbook(run, tmp_path / "out.xlsx"), "Companies")
    assert "possible duplicate" in rows[0]["notes"]
    summary = dict(row.values() for row in sheet_rows(tmp_path / "out.xlsx", "Summary"))
    assert summary["input rows flagged as possible duplicates"] == 2
    assert summary["coverage: a"] == "asked 3, matched 2, review 0, not found 1, rows with a failed request 0"
