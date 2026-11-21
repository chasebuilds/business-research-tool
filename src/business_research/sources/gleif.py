from __future__ import annotations

from business_research.http import PoliteClient
from business_research.models import Candidate, Fact

API = "https://api.gleif.org/api/v1/lei-records"


class GleifAdapter:
    """GLEIF LEI records: the legal name, country and LEI of registered legal entities."""

    name = "gleif"
    label = "GLEIF LEI records"
    terms_url = "https://www.gleif.org/en/meta/lei-data-terms-of-use"
    licence = "CC0 1.0"
    attribution = "None required. Do not imply GLEIF endorsement; its name and logo are trademarks"
    redistribution_ok = True
    group = "gleif"
    host = "api.gleif.org"
    # GLEIF's API documentation has stated a limit of 60 requests a minute. Stay under it.
    min_interval = 1.1

    def __init__(self, client: PoliteClient, page_size: int = 10):
        self.client = client
        self.page_size = page_size
        client.host_intervals.setdefault(self.host, max(self.min_interval, client.min_interval))

    def search(self, name: str, country: str | None) -> list[Candidate]:
        size = {"page[size]": str(self.page_size)}
        reply = self.client.get_json(API, {"filter[entity.legalName]": name, **size})
        records = (reply.data or {}).get("data", [])
        if not records:
            # The legal name may be registered in another script or spelled out in full
            # ("Aktiengesellschaft"). Full text also searches transliterated and other names.
            reply = self.client.get_json(API, {"filter[fulltext]": name, **size})
            records = (reply.data or {}).get("data", [])
        return [c for c in (self._candidate(r, reply.retrieved) for r in records) if c]

    def fetch(self, record_id: str) -> Candidate | None:
        reply = self.client.get_json(f"{API}/{record_id}")
        record = (reply.data or {}).get("data")
        return self._candidate(record, reply.retrieved) if record else None

    def _candidate(self, record: dict, retrieved: str) -> Candidate | None:
        attributes = record.get("attributes", {})
        entity = attributes.get("entity", {})
        lei = attributes.get("lei")
        legal_name = (entity.get("legalName") or {}).get("name")
        if not lei or not legal_name:
            return None
        other = [
            n["name"]
            for key in ("otherNames", "transliteratedOtherNames")
            for n in entity.get(key) or []
            if n.get("name")
        ]
        legal = (entity.get("legalAddress") or {}).get("country")
        headquarters = (entity.get("headquartersAddress") or {}).get("country")
        facts = {"legal_name": Fact(legal_name, tuple(other)), "lei": Fact(lei)}
        if headquarters or legal:
            # Headquarters country is what a client usually means. The legal address is kept as an
            # equivalent so a company registered in one place and run from another still agrees.
            facts["country"] = Fact(
                headquarters or legal, tuple(c for c in (legal,) if c and c != headquarters)
            )
        return Candidate(
            source=self.name,
            record_id=lei,
            name=legal_name,
            aliases=other,
            countries={c for c in (legal, headquarters) if c},
            url=f"{API}/{lei}",
            retrieved=retrieved,
            as_of=(attributes.get("registration") or {}).get("lastUpdateDate") or "",
            facts=facts,
            active=entity.get("status", "ACTIVE") == "ACTIVE",
        )
