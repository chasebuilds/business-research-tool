import pytest

from business_research.http import RobotsDisallowed
from business_research.matching import decide
from business_research.sources import EdgarAdapter, GleifAdapter, RorAdapter, WikidataAdapter, build_adapters

from conftest import load

SIEMENS_LEI = "W38RGI023J3WT1HWRP32"


def test_gleif_search_parses_recorded_response(make_client, siemens_routes):
    client, session = make_client(siemens_routes)
    found = GleifAdapter(client).search("Siemens Aktiengesellschaft", "DE")
    assert [c.record_id for c in found] == [SIEMENS_LEI, "52990021T5LVTQOGSU18"]
    first = found[0]
    assert first.name == "Siemens Aktiengesellschaft"
    assert first.countries == {"DE"}
    assert first.facts["lei"].value == SIEMENS_LEI
    assert first.facts["country"].value == "DE"
    assert first.url == f"https://api.gleif.org/api/v1/lei-records/{SIEMENS_LEI}"
    assert first.retrieved.endswith("Z") and first.active
    assert "filter%5Bentity.legalName%5D=Siemens+Aktiengesellschaft" in session.data_calls()[0]


def test_gleif_fetch_by_lei_and_missing_record(make_client, siemens_routes):
    client, _ = make_client(siemens_routes)
    adapter = GleifAdapter(client)
    assert adapter.fetch(SIEMENS_LEI).name == "Siemens Aktiengesellschaft"
    assert adapter.fetch("00000000000000000000") is None


def test_gleif_falls_back_to_full_text_when_the_legal_name_is_in_another_script(make_client):
    client, session = make_client(
        {
            "https://api.gleif.org/api/v1/lei-records?filter%5Bentity.legalName%5D": '{"data": []}',
            "https://api.gleif.org/api/v1/lei-records?filter%5Bfulltext%5D": load("gleif_fulltext_sony.json"),
        }
    )
    found = GleifAdapter(client).search("Sony Group Corporation", "JP")
    assert len(session.data_calls()) == 2
    assert found[0].aliases == ["Sony Group Corporation"] and found[0].countries == {"JP"}
    decision = decide("Sony Group Corporation", "JP", found)
    assert decision.status == "accepted" and decision.chosen.matched_on == "Sony Group Corporation"


def test_gleif_keeps_its_own_slower_rate(make_client):
    client, _ = make_client(min_interval=0.5)
    GleifAdapter(client)
    assert client.interval_for("api.gleif.org") == pytest.approx(1.1)


def test_ror_search_parses_recorded_response(make_client, siemens_routes):
    client, session = make_client(siemens_routes)
    found = {c.name: c for c in RorAdapter(client).search("Siemens AG", "DE")}
    # "Siemens (Germany)" is listed under its plain name, with the country kept as a field.
    parent = found["Siemens"]
    assert parent.record_id == "059mq0909"
    assert parent.countries == {"DE"}
    assert parent.facts["founded_year"].value == 1847
    assert parent.facts["website"].value.startswith("http://www.siemens.com")
    assert parent.links == {"wikidata": ["Q81230"]}
    assert "Siemens Healthineers" in found and "Siemens Energy" in found
    call = session.data_calls()[0]
    assert "query=%22Siemens%22" in call and "country.country_code%3ADE" in call


def test_ror_retries_without_the_country_filter_when_it_finds_nothing(make_client):
    client, session = make_client(
        {
            "https://api.ror.org/v2/organizations?query=%22Example%22&filter": '{"items": []}',
            "https://api.ror.org/v2/organizations?query=%22Example%22": load("ror_search_siemens_de.json"),
        }
    )
    assert len(RorAdapter(client).search("Example Ltd", "FR")) == 4
    assert len(session.data_calls()) == 2


def test_wikidata_fetch_parses_recorded_entity(make_client, siemens_routes):
    client, session = make_client(siemens_routes)
    item = WikidataAdapter(client).fetch("Q81230")
    assert item.name == "Siemens"
    assert "Siemens AG" in item.aliases
    assert item.countries == {"DE"}
    assert item.facts["website"].value == "https://www.siemens.com/"
    assert item.facts["founded_year"].value == 1847
    assert item.facts["industry"].value == "electrical engineering"
    assert item.facts["lei"].value == SIEMENS_LEI
    assert item.links == {"gleif": [SIEMENS_LEI, "529900ZZVLZ93I1AW009"]}
    assert isinstance(item.facts["employees"].value, int)
    assert item.url == "https://www.wikidata.org/wiki/Special:EntityData/Q81230.json"
    assert all("/w/api.php" not in url and "sparql" not in url for url in session.data_calls())


def test_wikidata_takes_the_newest_headcount(make_client, siemens_routes):
    client, _ = make_client(siemens_routes)
    # Four recorded statements, each with a point-in-time qualifier. The newest one wins.
    assert WikidataAdapter(client).fetch("Q81230").facts["employees"].value == 351000


def test_wikidata_strict_mode_has_no_name_search_and_rejects_bad_ids(make_client):
    client, session = make_client()
    adapter = WikidataAdapter(client, name_search=False)
    assert adapter.search("Siemens", "DE") == []
    assert adapter.fetch("not-an-id") is None
    assert session.calls == [] and client.exemptions == []


def test_wikidata_search_api_is_refused_by_real_shaped_robots(make_client):
    robots_txt = (
        "User-agent: *\nDisallow: /w/\nDisallow: /wiki/Special:\nAllow: /wiki/Special:EntityData/*.\n"
    )
    client, session = make_client({"https://www.wikidata.org/robots.txt": robots_txt})
    with pytest.raises(RobotsDisallowed):
        client.get_json("https://www.wikidata.org/w/api.php", {"action": "wbsearchentities"})
    assert session.data_calls() == []


def test_edgar_is_disabled_without_a_contact(make_client, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    client, session = make_client()
    with pytest.raises(ValueError, match="disabled"):
        EdgarAdapter(client)
    assert [a.name for a in build_adapters(client)] == ["gleif", "ror", "wikidata"]
    assert session.calls == []


def test_edgar_parses_hand_built_fixture_and_sends_the_env_contact(make_client, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_CONTACT", "Example Org contact@example.org")
    client, session = make_client(
        {
            "https://www.sec.gov/files/company_tickers.json": load("edgar_company_tickers.json"),
            "https://data.sec.gov/submissions/CIK0009990001.json": load("edgar_submissions_9990001.json"),
        }
    )
    adapters = build_adapters(client)
    assert [a.name for a in adapters] == ["gleif", "ror", "wikidata", "edgar"]
    found = adapters[-1].search("Northwind Example Corporation", "US")
    assert len(found) == 1  # the Holdings look-alike has no submissions file in this fixture
    company = found[0]
    assert company.record_id == "9990001"
    assert company.countries == {"US"}
    assert company.facts["sic_industry"].value == "Computer Peripheral Equipment, NEC"
    assert company.facts["website"].value == "https://www.northwind-example.test"
    assert company.aliases == ["Northwind Sample Corp"]
    sec_agents = {headers["User-Agent"] for url, headers in session.calls if "sec.gov" in url}
    assert sec_agents == {"business-research-tool/0.1 (Example Org contact@example.org)"}


def test_unknown_source_name_is_an_error(make_client):
    client, _ = make_client()
    with pytest.raises(ValueError, match="unknown source"):
        build_adapters(client, ["gleif", "linkedin"])
