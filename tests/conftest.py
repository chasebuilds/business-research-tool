from __future__ import annotations

import socket
from pathlib import Path

import pytest

from business_research.http import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures"
ALLOW_ALL = "User-agent: *\nDisallow:\n"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any test that opens a socket fails. The suite must run offline."""

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeReply:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code, self.text, self.headers = status_code, text, headers or {}


class FakeSession:
    """Serves canned replies by URL prefix and records every request."""

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                reply = self.routes[prefix]
                if isinstance(reply, list):
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                if isinstance(reply, Exception):
                    raise reply
                return reply if isinstance(reply, FakeReply) else FakeReply(200, reply)
        if url.endswith("/robots.txt"):
            return FakeReply(200, ALLOW_ALL)
        return FakeReply(404, "")

    def data_calls(self) -> list[str]:
        return [url for url, _ in self.calls if not url.endswith("/robots.txt")]


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_client(tmp_path, clock):
    def build(routes=None, **kwargs) -> tuple[PoliteClient, FakeSession]:
        session = FakeSession(routes)
        client = PoliteClient(
            cache_dir=tmp_path / "cache", session=session, sleep=clock.sleep, clock=clock, **kwargs
        )
        return client, session

    return build


@pytest.fixture
def siemens_routes():
    """The recorded Siemens responses, wired to the URLs the adapters request."""
    return {
        "https://api.gleif.org/api/v1/lei-records?": load("gleif_search_siemens.json"),
        "https://api.gleif.org/api/v1/lei-records/W38RGI023J3WT1HWRP32": load("gleif_record_siemens.json"),
        "https://api.ror.org/v2/organizations?": load("ror_search_siemens_de.json"),
        "https://www.wikidata.org/wiki/Special:EntityData/Q81230.json": load("wikidata_Q81230.json"),
        "https://www.wikidata.org/wiki/Special:EntityData/Q43035.json": load("wikidata_Q43035.json"),
    }
