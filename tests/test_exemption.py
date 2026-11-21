import pytest

from business_research.http import RobotsDisallowed
from business_research.matching import decide
from business_research.models import ACCEPTED, REVIEW, Candidate, CompanyInput
from business_research.pipeline import research
from business_research.sources import WikidataAdapter, build_adapters
from business_research.sources.wikidata import SEARCH_EXEMPTION

from conftest import FakeReply, load

# The rules that matter in the real Wikidata file.
WIKIDATA_ROBOTS = (
    "User-agent: *\nDisallow: /w/\nDisallow: /wiki/Special:\nAllow: /wiki/Special:EntityData/*.\n"
)
SEARCH = "https://www.wikidata.org/w/api.php?action=wbsearchentities"


@pytest.fixture
def wikidata_routes(siemens_routes):
    return dict(siemens_routes) | {
        "https://www.wikidata.org/robots.txt": WIKIDATA_ROBOTS,
        SEARCH: load("wikidata_search_siemens.json"),
    }


def test_the_exemption_is_one_host_and_one_endpoint():
    assert SEARCH_EXEMPTION.host == "www.wikidata.org"
    assert SEARCH_EXEMPTION.path_prefix == "/w/api.php?action=wbsearchentities&"
    assert SEARCH_EXEMPTION.terms_url.startswith("https://www.wikidata.org/")
    assert "*" not in SEARCH_EXEMPTION.path_prefix


def test_name_search_uses_the_exemption_and_logs_it(make_client, wikidata_routes, caplog):
    client, session = make_client(wikidata_routes)
    with caplog.at_level("INFO", logger="business_research"):
        found = WikidataAdapter(client).search("Siemens AG", "DE")
    # Three hits came back. Only the one whose name is close to the input was fetched.
    assert [c.record_id for c in found] == ["Q81230"]
    assert client.stats["robots_exemptions_used"] == 1 and client.stats["robots_refused"] == 0
    assert any(
        "exempt https://www.wikidata.org/w/api.php?action=wbsearchentities" in m for m in caplog.messages
    )
    entity_calls = [u for u in session.data_calls() if "EntityData" in u]
    assert all("Q29533487" not in u and "Q68646904" not in u for u in entity_calls)


def test_everything_else_under_the_disallowed_path_is_still_refused(make_client, wikidata_routes):
    client, session = make_client(wikidata_routes)
    WikidataAdapter(client)
    for params in ({"action": "query", "list": "search"}, {"action": "wbgetentities", "ids": "Q1"}):
        with pytest.raises(RobotsDisallowed):
            client.get_json("https://www.wikidata.org/w/api.php", params)
    with pytest.raises(RobotsDisallowed):
        client.get_json("https://www.wikidata.org/w/index.php", {"title": "Q1"})
    assert session.data_calls() == []


def test_the_exemption_does_not_carry_to_another_host(make_client):
    client, session = make_client({"https://other.test/robots.txt": WIKIDATA_ROBOTS})
    WikidataAdapter(client)
    with pytest.raises(RobotsDisallowed):
        client.get_json("https://other.test/w/api.php", {"action": "wbsearchentities", "search": "x"})
    assert session.data_calls() == []


def test_no_exemption_when_robots_cannot_be_read(make_client):
    client, session = make_client({"https://www.wikidata.org/robots.txt": FakeReply(503)}, max_retries=0)
    with pytest.raises(RobotsDisallowed):
        WikidataAdapter(client).search("Siemens AG", "DE")
    assert session.data_calls() == []


def test_a_client_without_the_adapter_has_no_exemption(make_client, wikidata_routes):
    client, _ = make_client(wikidata_routes)
    with pytest.raises(RobotsDisallowed):
        client.get_json("https://www.wikidata.org/w/api.php", {"action": "wbsearchentities", "search": "x"})


def test_strict_robots_switch_makes_no_exempted_request(make_client, wikidata_routes, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    client, session = make_client(wikidata_routes)
    run = research([CompanyInput("Siemens AG", "DE")], build_adapters(client, wikidata_search=False))
    assert client.exemptions == [] and client.stats["robots_exemptions_used"] == 0
    assert all("/w/api.php" not in u for u in session.data_calls())
    assert run.companies[0].accepted["wikidata"].via == "link from ror"


def test_search_result_must_look_like_an_organization(make_client, wikidata_routes):
    routes = dict(wikidata_routes)
    bare = '{"entities": {"Q81230": {"id": "Q81230", "labels": {"en": {"value": "Siemens"}}, "claims": {}}}}'
    routes["https://www.wikidata.org/wiki/Special:EntityData/Q81230.json"] = bare
    client, _ = make_client(routes)
    adapter = WikidataAdapter(client)
    assert adapter.search("Siemens", "DE") == []
    assert adapter.fetch("Q81230").name == "Siemens"  # an explicit id is still honored


def test_search_error_body_is_reported_not_treated_as_empty(make_client, wikidata_routes):
    routes = dict(wikidata_routes) | {SEARCH: '{"error": {"code": "maxlag"}}'}
    client, _ = make_client(routes)
    company = research([CompanyInput("Siemens AG", "DE")], [WikidataAdapter(client)]).companies[0]
    assert "wikidata: search failed (Wikidata search error: maxlag)" in company.errors


def twin(record_id, linked=False):
    return Candidate("s", record_id, "Example", f"mock://{record_id}", "2026-01-01T00:00:00Z", linked=linked)


def test_a_cross_source_link_breaks_a_name_tie():
    assert decide("Example", None, [twin("1"), twin("2")]).status == REVIEW
    decision = decide("Example", None, [twin("1"), twin("2", linked=True)])
    assert decision.status == ACCEPTED and decision.chosen.candidate.record_id == "2"
    assert decide("Example", None, [twin("1", linked=True), twin("2", linked=True)]).status == REVIEW


def test_pipeline_marks_a_searched_record_that_another_source_points_at():
    from test_pipeline_and_output import StubSource, record

    a = StubSource("a", [record("a", "1", "Example GmbH", links={"b": ["B2"]})])
    b = StubSource("b", [record("b", "B1", "Example GmbH"), record("b", "B2", "Example GmbH")])
    company = research([CompanyInput("Example GmbH")], [a, b]).companies[0]
    assert company.accepted["b"].record_id == "B2"
    assert company.accepted["b"].via == "search, link from a"
