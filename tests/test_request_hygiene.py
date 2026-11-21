"""Cache TTL, offline mode, per-source pacing. Fakes only: no test here opens a socket."""

import pytest
from openpyxl import load_workbook

from business_research.cli import main
from business_research.http import MIN_INTERVAL_FLOOR, OfflineMiss, PoliteClient
from business_research.models import CompanyInput
from business_research.pipeline import research
from business_research.sources import GleifAdapter, RorAdapter, adapter_hosts, build_adapters

from conftest import FakeReply, FakeSession

DAY = 86400.0


def sheet_rows(path, name):
    rows = list(load_workbook(path)[name].iter_rows(values_only=True))
    return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


class WallClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class NoNetwork:
    """A session that fails the test if anything is sent."""

    def get(self, url, headers=None, timeout=None):
        raise AssertionError(f"offline client sent a request: {url}")


def test_cache_entry_is_reused_inside_the_ttl_and_refetched_after(make_client):
    wall = WallClock()
    client, session = make_client({"https://site.test/a": '{"n": 1}'}, cache_ttl=2 * DAY, wall_clock=wall)
    client.get_json("https://site.test/a")
    wall.now += DAY
    assert client.get_json("https://site.test/a").from_cache
    wall.now += 1.5 * DAY
    assert not client.get_json("https://site.test/a").from_cache
    assert len(session.data_calls()) == 2


def test_a_404_is_cached_so_a_rerun_does_not_ask_again(make_client):
    client, session = make_client()
    first, second = client.get_json("https://site.test/gone"), client.get_json("https://site.test/gone")
    assert first.data is None and second.data is None
    assert (first.from_cache, second.from_cache) == (False, True)
    assert len(session.data_calls()) == 1


def test_errors_other_than_404_are_never_cached(make_client):
    client, session = make_client({"https://site.test/a": [FakeReply(403, "no"), FakeReply(200, "{}")]})
    with pytest.raises(Exception, match="HTTP 403"):
        client.get_json("https://site.test/a")
    assert client.get_json("https://site.test/a").data == {}
    assert len(session.data_calls()) == 2


@pytest.mark.parametrize("junk", ["{not json", '{"saved": 1}', "[]", '{"saved": "x", "status": 200}'])
def test_a_broken_cache_file_is_a_miss_not_a_crash(make_client, junk):
    client, session = make_client({"https://site.test/a": '{"n": 2}'})
    client._cache_path("https://site.test/a").write_text(junk, encoding="utf-8")
    assert client.get_json("https://site.test/a").data == {"n": 2}
    assert client.get_json("https://site.test/a").from_cache


def test_offline_serves_the_cache_at_any_age_and_sends_nothing(tmp_path, make_client):
    wall = WallClock()
    online, _ = make_client({"https://site.test/a": '{"n": 1}'}, wall_clock=wall)
    fetched = online.get_json("https://site.test/a")
    wall.now += 365 * DAY
    offline = PoliteClient(cache_dir=online.cache_dir, offline=True, session=NoNetwork(), wall_clock=wall)
    answer = offline.get_json("https://site.test/a")
    assert answer.data == {"n": 1} and answer.from_cache
    assert answer.retrieved == fetched.retrieved


def test_offline_miss_raises_without_a_request(tmp_path):
    client = PoliteClient(cache_dir=tmp_path, offline=True, session=NoNetwork())
    with pytest.raises(OfflineMiss, match="not in cache"):
        client.get_json("https://site.test/never")
    assert client.stats["offline_misses"] == 1 and client.stats["requests"] == 0


def test_offline_and_refresh_are_refused_together(tmp_path):
    with pytest.raises(ValueError, match="cannot be combined"):
        PoliteClient(cache_dir=tmp_path, offline=True, refresh=True)


def test_offline_miss_becomes_a_row_error_and_other_sources_carry_on(tmp_path, make_client, siemens_routes):
    online, _ = make_client(siemens_routes)
    RorAdapter(online).search("Siemens AG", "DE")  # only ROR is cached
    offline = PoliteClient(cache_dir=online.cache_dir, offline=True, session=NoNetwork())
    company = research(
        [CompanyInput("Siemens AG", "DE")], [GleifAdapter(offline), RorAdapter(offline)]
    ).companies[0]
    assert "ror" in company.accepted
    assert any(e.startswith("gleif: search failed (offline") for e in company.errors)


def test_set_interval_paces_one_host_and_keeps_the_floor(make_client, clock):
    client, _ = make_client({"https://slow.test/": "{}", "https://fast.test/": "{}"})
    client.set_interval("slow.test", 4)
    client.set_interval("fast.test", 0)
    assert client.interval_for("fast.test") == MIN_INTERVAL_FLOOR
    for _ in range(2):
        client.get_json("https://slow.test/x", {"i": str(clock.now)})
    assert max(clock.sleeps) == pytest.approx(4.0)


def test_adapter_hosts_cover_every_live_source(make_client, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_CONTACT", "Example Org ops@example.test")
    client, _ = make_client()
    hosts = {a.name: adapter_hosts(a) for a in build_adapters(client)}
    assert hosts == {
        "gleif": ("api.gleif.org",),
        "ror": ("api.ror.org",),
        "wikidata": ("www.wikidata.org",),
        "edgar": ("www.sec.gov", "data.sec.gov"),
    }


def _cli_env(tmp_path, monkeypatch, session, clock):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("requests.Session", lambda: session)
    # Fake time for the client the CLI builds, so pacing is measured and nothing really sleeps.
    monkeypatch.setattr(
        "business_research.cli.PoliteClient",
        lambda **kw: PoliteClient(**kw, sleep=clock.sleep, clock=clock),
    )
    (tmp_path / "in.csv").write_text("company,country\nSiemens AG,DE\n", encoding="utf-8")


def test_cli_offline_rerun_matches_the_online_run_without_a_request(
    tmp_path, monkeypatch, siemens_routes, clock
):
    _cli_env(tmp_path, monkeypatch, FakeSession(siemens_routes), clock)
    assert main(["run", "in.csv", "-o", "online.xlsx"]) == 0
    monkeypatch.setattr("requests.Session", lambda: NoNetwork())
    assert main(["run", "in.csv", "-o", "offline.xlsx", "--offline"]) == 0
    online, offline = (sheet_rows(tmp_path / f, "Companies") for f in ("online.xlsx", "offline.xlsx"))
    assert offline[0]["matched_sources"] == online[0]["matched_sources"] == "gleif, ror, wikidata"
    assert "offline: no requests will be sent" in (tmp_path / "offline.log").read_text(encoding="utf-8")


def test_cli_source_interval_slows_that_source(tmp_path, monkeypatch, siemens_routes, clock):
    _cli_env(tmp_path, monkeypatch, FakeSession(siemens_routes), clock)
    seen = {}
    original = PoliteClient.set_interval

    def spy(self, host, seconds):
        seen[host] = seconds
        original(self, host, seconds)

    monkeypatch.setattr(PoliteClient, "set_interval", spy)
    assert main(["run", "in.csv", "-o", "out.xlsx", "--source-interval", "ror=3"]) == 0
    assert seen == {"api.ror.org": 3.0}
    assert max(clock.sleeps) == pytest.approx(3.0)


@pytest.mark.parametrize(
    "extra", [["--offline", "--refresh"], ["--source-interval", "ror"], ["--source-interval", "ror=fast"]]
)
def test_cli_rejects_bad_hygiene_flags(tmp_path, monkeypatch, extra):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as stop:
        main(["run", "in.csv", *extra])
    assert stop.value.code == 2
