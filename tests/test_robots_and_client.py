import pytest

from business_research import robots
from business_research.http import (
    MIN_INTERVAL_FLOOR,
    FetchError,
    PoliteClient,
    RobotsDisallowed,
    default_user_agent,
)

from conftest import FakeReply

# Shaped like the real Wikidata file: a broad Disallow, then a narrower wildcard Allow.
WIKI_STYLE = """
User-agent: SomeBadBot
Disallow: /

User-agent: *
Allow: /w/api.php?action=mobileview&
Disallow: /w/
Disallow: /wiki/Special:
Disallow: /wiki/Special:EntityData/
Allow: /wiki/Special:EntityData/*.
Disallow: /private$
"""


def test_longest_match_and_wildcards():
    rules = robots.parse(WIKI_STYLE)
    agent = "business-research-tool"
    assert rules.allowed(agent, "/wiki/Special:EntityData/Q42.json")
    assert not rules.allowed(agent, "/wiki/Special:EntityData/Q42")
    assert not rules.allowed(agent, "/w/api.php?action=wbsearchentities&search=x")
    assert rules.allowed(agent, "/w/api.php?action=mobileview&page=x")
    assert not rules.allowed(agent, "/wiki/Special:Search")
    assert rules.allowed(agent, "/wiki/Q42")
    assert not rules.allowed(agent, "/private")
    assert rules.allowed(agent, "/private/page")


def test_a_group_naming_this_tool_overrides_the_wildcard_group():
    rules = robots.parse("User-agent: *\nDisallow:\n\nUser-agent: business-research-tool\nDisallow: /\n")
    assert not rules.allowed("business-research-tool", "/anything")
    assert rules.allowed("other-bot", "/anything")


def test_missing_robots_allows_and_broken_robots_denies():
    assert robots.from_status(404, "").allowed("x", "/a")
    assert not robots.from_status(503, "").allowed("x", "/a")


def test_client_refuses_a_disallowed_path_without_fetching_it(make_client):
    client, session = make_client({"https://site.test/robots.txt": WIKI_STYLE})
    with pytest.raises(RobotsDisallowed):
        client.get_json("https://site.test/w/api.php", {"action": "wbsearchentities", "search": "x"})
    assert session.data_calls() == []
    assert client.stats["robots_refused"] == 1


def test_client_fetches_an_allowed_path_on_the_same_host(make_client):
    client, session = make_client(
        {
            "https://site.test/robots.txt": WIKI_STYLE,
            "https://site.test/wiki/Special:EntityData/Q1.json": '{"ok": true}',
        }
    )
    assert client.get_json("https://site.test/wiki/Special:EntityData/Q1.json").data == {"ok": True}
    assert len(session.data_calls()) == 1


def test_unreachable_robots_blocks_the_host(make_client):
    client, session = make_client({"https://site.test/robots.txt": FakeReply(500)}, max_retries=1)
    with pytest.raises(RobotsDisallowed):
        client.get_json("https://site.test/data.json")
    assert session.data_calls() == []


def test_robots_is_fetched_once_per_host(make_client):
    client, session = make_client({"https://site.test/a": "{}", "https://site.test/b": "{}"})
    client.get_json("https://site.test/a")
    client.get_json("https://site.test/b")
    assert [url for url, _ in session.calls].count("https://site.test/robots.txt") == 1


def test_requests_to_one_host_are_spaced(make_client, clock):
    client, _ = make_client({"https://site.test/": "{}"}, min_interval=2.0)
    client.get_json("https://site.test/a")
    client.get_json("https://site.test/b")
    # robots.txt, then a, then b: two waits of the full interval on a clock that only moves on sleep
    assert clock.sleeps == [2.0, 2.0]


def test_hosts_are_limited_independently(make_client, clock):
    client, _ = make_client({"https://one.test/": "{}", "https://two.test/": "{}"}, min_interval=2.0)
    client.get_json("https://one.test/a")
    client.get_json("https://two.test/a")
    assert clock.sleeps == [2.0, 2.0]  # one wait per host after its robots.txt, none across hosts


@pytest.mark.parametrize("asked", [0, -5, 0.01])
def test_rate_limit_cannot_be_set_to_zero(tmp_path, asked):
    client = PoliteClient(cache_dir=tmp_path, min_interval=asked, host_intervals={"x.test": asked})
    assert client.min_interval == MIN_INTERVAL_FLOOR
    assert client.interval_for("x.test") == MIN_INTERVAL_FLOOR
    assert client.interval_for("other.test") == MIN_INTERVAL_FLOOR


def test_backoff_honors_retry_after_then_succeeds(make_client, clock):
    replies = [FakeReply(429, "", {"Retry-After": "7"}), FakeReply(200, '{"ok": 1}')]
    client, session = make_client({"https://site.test/a": replies})
    assert client.get_json("https://site.test/a").data == {"ok": 1}
    assert 7.0 in clock.sleeps
    assert client.stats["retries"] == 1
    assert len(session.data_calls()) == 2


def test_backoff_grows_and_gives_up(make_client, clock):
    client, session = make_client({"https://site.test/a": FakeReply(503)}, max_retries=3)
    with pytest.raises(FetchError, match="gave up"):
        client.get_json("https://site.test/a")
    assert [s for s in clock.sleeps if s >= 5] == [5.0, 10.0, 20.0]
    assert len(session.data_calls()) == 4


def test_connection_errors_are_retried(make_client):
    client, session = make_client({"https://site.test/a": [OSError("reset"), FakeReply(200, "{}")]})
    assert client.get_json("https://site.test/a").data == {}
    assert len(session.data_calls()) == 2


def test_second_call_is_served_from_disk_cache_with_the_original_time(make_client):
    client, session = make_client({"https://site.test/a": '{"n": 1}'})
    first = client.get_json("https://site.test/a", {"q": "x y"})
    second = client.get_json("https://site.test/a", {"q": "x y"})
    assert (first.from_cache, second.from_cache) == (False, True)
    assert second.retrieved == first.retrieved
    assert second.url == "https://site.test/a?q=x+y"
    assert len(session.data_calls()) == 1
    assert client.stats["cache_hits"] == 1


def test_refresh_ignores_the_cache(make_client):
    client, session = make_client({"https://site.test/a": "{}"})
    client.get_json("https://site.test/a")
    client.refresh = True
    client.get_json("https://site.test/a")
    assert len(session.data_calls()) == 2


def test_404_is_a_normal_empty_answer_and_other_errors_raise(make_client):
    client, _ = make_client({"https://site.test/bad": FakeReply(403, "no")})
    assert client.get_json("https://site.test/missing").data is None
    with pytest.raises(FetchError, match="HTTP 403"):
        client.get_json("https://site.test/bad")


def test_user_agent_names_the_tool_and_carries_no_email(make_client, monkeypatch):
    monkeypatch.delenv("BRT_CONTACT", raising=False)
    agent = default_user_agent()
    assert agent.startswith("business-research-tool/")
    assert "@" not in agent and "python-requests" not in agent
    client, session = make_client({"https://site.test/a": "{}"})
    client.get_json("https://site.test/a")
    assert all(headers["User-Agent"] == agent for _, headers in session.calls)


def test_contact_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("BRT_CONTACT", "https://example.org/tool")
    assert default_user_agent().endswith("(https://example.org/tool)")
