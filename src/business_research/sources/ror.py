from __future__ import annotations

import re

from business_research.http import PoliteClient
from business_research.models import Candidate, Fact
from business_research.normalize import core_name

API = "https://api.ror.org/v2/organizations"


class RorAdapter:
    """Research Organization Registry: name, country, website, founding year and a Wikidata id.

    ROR lists organizations that appear in research output, so it knows large companies and
    misses most small ones.
    """

    name = "ror"
    label = "Research Organization Registry (ROR)"
    terms_url = "https://ror.readme.io/docs/rest-api"
    licence = "CC0 1.0"
    attribution = "None required"
    redistribution_ok = True
    # ROR and Wikidata import from and link to each other.
    group = "ror-wikidata"
    host = "api.ror.org"

    def __init__(self, client: PoliteClient):
        self.client = client

    def search(self, name: str, country: str | None) -> list[Candidate]:
        # Quoted so ROR treats the name as one phrase instead of matching any single word.
        params = {"query": f'"{core_name(name)}"'}
        if country:
            params["filter"] = f"country.country_code:{country}"
        reply = self.client.get_json(API, params)
        items = (reply.data or {}).get("items", [])
        if not items and country:
            reply = self.client.get_json(API, {"query": params["query"]})
            items = (reply.data or {}).get("items", [])
        return [c for c in (self._candidate(i, reply.retrieved) for i in items) if c]

    def fetch(self, record_id: str) -> Candidate | None:
        reply = self.client.get_json(f"{API}/{record_id.rsplit('/', 1)[-1]}")
        return self._candidate(reply.data, reply.retrieved) if reply.data else None

    def _candidate(self, item: dict, retrieved: str) -> Candidate | None:
        ror_id = (item.get("id") or "").rsplit("/", 1)[-1]
        names = item.get("names") or []
        display = next((n["value"] for n in names if "ror_display" in n.get("types", [])), None)
        if not ror_id or not display:
            return None
        country = next(
            (
                loc["geonames_details"]["country_code"]
                for loc in item.get("locations") or []
                if (loc.get("geonames_details") or {}).get("country_code")
            ),
            None,
        )
        # ROR titles branches and same-named bodies as "Siemens (Germany)". The country is
        # already a field, so the suffix is dropped before names are compared.
        clean = re.sub(r"\s*\([^()]*\)\s*$", "", display).strip() or display
        aliases = [
            n["value"]
            for n in names
            if n.get("value") != display and {"label", "alias"} & set(n.get("types", []))
        ]
        facts = {"legal_name": Fact(clean, tuple(aliases))}
        if country:
            facts["country"] = Fact(country)
        website = next(
            (link["value"] for link in item.get("links") or [] if link.get("type") == "website"), None
        )
        if website:
            facts["website"] = Fact(website)
        if item.get("established"):
            facts["founded_year"] = Fact(int(item["established"]))
        wikidata = [
            q for x in item.get("external_ids") or [] if x.get("type") == "wikidata" for q in x.get("all", [])
        ]
        return Candidate(
            source=self.name,
            record_id=ror_id,
            name=clean,
            aliases=aliases,
            countries={country} if country else set(),
            url=f"{API}/{ror_id}",
            retrieved=retrieved,
            as_of=((item.get("admin") or {}).get("last_modified") or {}).get("date") or "",
            facts=facts,
            links={"wikidata": wikidata} if wikidata else {},
            active=item.get("status", "active") == "active",
        )
