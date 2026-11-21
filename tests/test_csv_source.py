import pytest

from business_research.cli import main
from business_research.mock import MockRecord, MockSource
from business_research.models import CompanyInput, Fact
from business_research.pipeline import research
from business_research.sources import CsvSource

REFERENCE = """id,name,aliases,country,website,founded_year,employees,wikidata_id
c1,Halvorsen Pumps AS,Halvorsen;HPumps,NO,https://halvorsen.example,1952,"1,200",Q1
c2,Brightwater Analytics Ltd,,GB,https://brightwater.example,2011,85,
c3,Orchid Valley Foods Inc,,US,,1990,,
"""


@pytest.fixture
def reference(tmp_path):
    path = tmp_path / "crm.csv"
    path.write_text(REFERENCE, encoding="utf-8")
    return path


def registry(*records: MockRecord) -> MockSource:
    return MockSource("registry", list(records))


def record(record_id: str, name: str, country: str, **facts) -> MockRecord:
    return MockRecord(
        record_id,
        0,
        name,
        [],
        {"legal_name": Fact(name), "country": Fact(country), **{k: Fact(v) for k, v in facts.items()}},
        {},
    )


def test_loads_rows_as_candidates_with_typed_facts(reference):
    source = CsvSource(reference)
    found = source.fetch("c1")
    assert found.name == "Halvorsen Pumps AS" and found.aliases == ["Halvorsen", "HPumps"]
    assert found.countries == {"NO"}
    assert found.facts["employees"].value == 1200 and found.facts["founded_year"].value == 1952
    assert found.links == {"wikidata": ["Q1"]}
    assert found.url == "crm.csv#line=2" and found.retrieved.endswith("Z")
    assert source.fetch("nope") is None
    assert source.group == "user-supplied" and source.redistribution_ok


def test_search_ranks_close_names_and_drops_unrelated_ones(reference):
    source = CsvSource(reference)
    assert [c.record_id for c in source.search("Brightwater Analytics Limited", None)] == ["c2"]
    assert [c.record_id for c in source.search("HPumps", "NO")] == ["c1"]
    assert source.search("Completely Different Co", None) == []


def test_answers_are_fresh_objects(reference):
    source = CsvSource(reference)
    first = source.fetch("c1")
    first.linked = True
    first.facts["website"] = Fact("changed")
    assert not source.fetch("c1").linked
    assert source.fetch("c1").facts["website"].value == "https://halvorsen.example"


@pytest.mark.parametrize(
    "text, message",
    [
        ("name,country\nAcme,US\n", "needs columns id"),
        ("id,name\nx,Acme\nx,Acme Two\n", "duplicate id x"),
        ("id,name,employees\nx,Acme,lots\n", "employees must be a whole number"),
        ("id,name,country\nx,Acme,Atlantis\n", "unknown country"),
    ],
)
def test_bad_files_fail_loudly(tmp_path, text, message):
    path = tmp_path / "bad.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        CsvSource(path)


def test_reference_list_can_verify_a_public_source(reference):
    result = research(
        [CompanyInput("Brightwater Analytics", "GB")],
        [
            registry(record("r9", "Brightwater Analytics Limited", "GB", founded_year=2011)),
            CsvSource(reference),
        ],
    )
    company = result.companies[0]
    assert set(company.accepted) == {"registry", "reference"}
    assert company.fields["country"].status == "verified"
    assert company.fields["founded_year"].status == "verified"
    assert company.fields["website"].status == "single-source"
    assert {s["name"]: s["group"] for s in result.sources}["reference"] == "user-supplied"


def test_reference_list_can_contradict_a_public_source(reference):
    company = research(
        [CompanyInput("Orchid Valley Foods", "US")],
        [registry(record("r1", "Orchid Valley Foods Inc", "US", founded_year=1987)), CsvSource(reference)],
    ).companies[0]
    assert company.fields["founded_year"].status == "conflict"


def test_link_column_leads_to_the_other_source(reference):
    encyclopedia = MockSource("wikidata", [record("Q1", "Halvorsen Pumps", "NO")])
    encyclopedia.search = lambda name, country: []  # only reachable through the link
    company = research(
        [CompanyInput("Halvorsen Pumps", "NO")], [CsvSource(reference), encyclopedia]
    ).companies[0]
    assert company.accepted["wikidata"].via == "link from reference"


def test_reference_id_column_in_the_input_fetches_that_row(tmp_path, reference):
    from business_research.inputs import read_companies

    path = tmp_path / "in.csv"
    path.write_text("company,country,reference_id\nOrchid Valley Foods,US,c3\n", encoding="utf-8")
    company = research(read_companies(path), [CsvSource(reference)]).companies[0]
    assert company.accepted["reference"].record_id == "c3"
    assert any(s.candidate.via == "id in input" for s in company.decisions["reference"].scored)


def test_cli_runs_on_a_reference_list_alone(tmp_path, monkeypatch, reference):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "in.csv").write_text("company,country\nHalvorsen Pumps,NO\nNobody Ltd,GB\n", encoding="utf-8")
    args = ["run", "in.csv", "-o", "out.xlsx", "--sources", "", "--reference", str(reference)]
    assert main(args) == 0
    assert "sources reference" in (tmp_path / "out.log").read_text(encoding="utf-8")
