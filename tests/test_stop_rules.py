from datetime import UTC, datetime

import pytest

from business_research.http import MAX_RETRY_WAIT, ROBOTS_TTL, FetchError, HostBlocked, retry_after_seconds
from business_research.models import CompanyInput
from business_research.pipeline import research
from business_research.sources import EdgarAdapter, GleifAdapter, RorAdapter, WikidataAdapter, build_adapters
from business_research.workbook import summary_rows

from conftest import FakeReply


def test_retry_after_reads_seconds_and_http_dates():
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    assert retry_after_seconds("90") == 90
    assert retry_after_seconds("Sat, 21 Nov 2026 12:10:00 GMT", now) == 600
    assert retry_after_seconds("Sat, 21 Nov 2026 11:00:00 GMT", now) == 0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("soon") is None


def test_retry_after_is_waited_in_full_not_trimmed(make_client, clock):
    replies = [FakeReply(429, "", {"Retry-After": str(int(MAX_RETRY_WAIT))}), FakeReply(200, "{}")]
    client, _ = make_client({"https://site.test/a": replies})
    client.get_json("https://site.test/a")
    assert MAX_RETRY_WAIT in clock.sleeps


def test_retry_after_longer_than_the_cap_drops_the_host(make_client, clock):
    client, session = make_client(
        {"https://site.test/a": FakeReply(503, "", {"Retry-After": "3600"}), "https://site.test/b": "{}"}
    )
    with pytest.raises(HostBlocked, match="Retry-After asked for 3600s"):
        client.get_json("https://site.test/a")
    assert max(clock.sleeps, default=0) < 3600  # it did not wait an hour, and it did not come back early
    sent = len(session.calls)
    with pytest.raises(HostBlocked):
        client.get_json("https://site.test/b")
    assert len(session.calls) == sent
    assert client.stats["hosts_blocked"] == 1


def test_a_run_of_403s_ends_work_on_that_host(make_client):
    client, session = make_client({"https://site.test/": FakeReply(403, "no"), "https://other.test/a": "{}"})
    for path in ("a", "b"):
        with pytest.raises(FetchError, match="HTTP 403"):
            client.get_json(f"https://site.test/{path}")
    with pytest.raises(HostBlocked, match="in a row"):
        client.get_json("https://site.test/c")
    sent = len(session.data_calls())
    with pytest.raises(HostBlocked):
        client.get_json("https://site.test/d")
    assert len(session.data_calls()) == sent
    assert client.get_json("https://other.test/a").data == {}  # other hosts carry on


def test_repeated_429s_end_work_on_that_host(make_client):
    client, session = make_client({"https://site.test/a": FakeReply(429, "")})
    with pytest.raises(HostBlocked):
        client.get_json("https://site.test/a")
    assert len(session.data_calls()) == 3


def test_a_good_answer_resets_the_block_count(make_client):
    replies = [FakeReply(403), FakeReply(403), FakeReply(200, "{}"), FakeReply(403), FakeReply(403)]
    client, _ = make_client({"https://site.test/robots.txt": "", "https://site.test/": replies})
    outcomes = []
    for path in "abcde":
        try:
            client.get_json(f"https://site.test/{path}")
            outcomes.append("ok")
        except HostBlocked:
            outcomes.append("blocked")
        except FetchError:
            outcomes.append("403")
    assert outcomes == ["403", "403", "ok", "403", "403"]


def test_a_403_on_robots_txt_is_a_missing_file_not_a_block(make_client):
    client, _ = make_client(
        {"https://site.test/robots.txt": FakeReply(403, "no"), "https://site.test/a": "{}"}
    )
    assert client.get_json("https://site.test/a").data == {}
    assert client.blocked == {}


def test_robots_is_read_again_after_a_day(make_client, clock):
    client, session = make_client({"https://site.test/a": "{}", "https://site.test/b": "{}"})
    client.get_json("https://site.test/a")
    clock.now += ROBOTS_TTL + 60
    client.get_json("https://site.test/b")
    assert [url for url, _ in session.calls].count("https://site.test/robots.txt") == 2


def test_robots_is_never_served_from_the_disk_cache(make_client):
    first, _ = make_client({"https://site.test/a": "{}"})
    first.get_json("https://site.test/a")
    second, session = make_client({"https://site.test/a": "{}"})
    assert second.get_json("https://site.test/a").from_cache
    assert [url for url, _ in session.calls] == ["https://site.test/robots.txt"]


def test_a_blocked_host_is_reported_on_the_row_and_other_sources_continue(make_client, siemens_routes):
    routes = {k: FakeReply(429, "") if "gleif" in k else v for k, v in siemens_routes.items()}
    client, _ = make_client(routes)
    companies = research(
        [CompanyInput("Siemens AG", "DE"), CompanyInput("Siemens AG ", "DE")],
        build_adapters(client, ["gleif", "ror"]),
    ).companies
    assert all("ror" in c.accepted and "gleif" not in c.accepted for c in companies)
    assert "skipped for the rest of the run" in companies[1].errors[0]


def test_every_adapter_declares_a_licence_and_attribution_duty():
    for adapter in (GleifAdapter, RorAdapter, WikidataAdapter, EdgarAdapter):
        assert adapter.licence and adapter.attribution and adapter.terms_url.startswith("https://")


def test_run_records_licence_attribution_and_coverage_per_source(make_client, siemens_routes):
    client, _ = make_client(siemens_routes)
    run = research([CompanyInput("Siemens AG", "DE")], build_adapters(client, ["gleif", "ror", "wikidata"]))
    lines = {str(item): str(value) for item, value in summary_rows(run)}
    assert lines["coverage: GLEIF LEI records"].startswith("asked 1, matched 1")
    described = {s["label"]: s for s in run.sources}
    assert described["GLEIF LEI records"]["licence"] == "CC0 1.0"
    assert described["Wikidata"]["attribution"] and described["Wikidata"]["terms_url"].startswith("https://")
