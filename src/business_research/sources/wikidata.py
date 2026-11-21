from __future__ import annotations

import re

from business_research.http import ApiExemption, FetchError, PoliteClient
from business_research.matching import name_score
from business_research.models import Candidate, Fact
from business_research.normalize import core_name, parse_year

ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
SEARCH_URL = "https://www.wikidata.org/w/api.php"

# The single robots.txt exemption in this tool. Wikidata's robots.txt disallows /w/ for
# crawlers, while Wikidata's data access page tells API clients to use this search action for
# text lookups and welcomes well-behaved bots. Entity data is still read from the path
# robots.txt allows.
SEARCH_EXEMPTION = ApiExemption(
    host="www.wikidata.org",
    path_prefix="/w/api.php?action=wbsearchentities&",
    terms_url="https://www.wikidata.org/wiki/Wikidata:Data_access",
    reason="Documented search endpoint of the MediaWiki Action API",
)
# A search hit is only fetched when its label is at least this close to the input name.
SEARCH_FETCH_SCORE = 70
# Properties that mark an item as an organization: LEI, industry, stock exchange, headquarters,
# employees, chief executive. A fruit or a surname has none of them.
ORGANIZATION_PROPERTIES = ("P1278", "P452", "P414", "P159", "P1128", "P169")

# Wikidata item id to ISO 3166-1 alpha-2, for the countries seen most. Anything else is looked
# up from the country item itself (property P297) and cached like every other fetch.
COUNTRY_QIDS = {
    "Q30": "US", "Q183": "DE", "Q142": "FR", "Q145": "GB", "Q17": "JP", "Q148": "CN", "Q39": "CH",
    "Q55": "NL", "Q884": "KR", "Q16": "CA", "Q408": "AU", "Q38": "IT", "Q29": "ES", "Q34": "SE",
    "Q668": "IN", "Q155": "BR", "Q96": "MX", "Q27": "IE", "Q31": "BE", "Q35": "DK", "Q20": "NO",
    "Q33": "FI", "Q40": "AT", "Q334": "SG", "Q865": "TW", "Q801": "IL", "Q36": "PL", "Q258": "ZA",
    "Q32": "LU", "Q8646": "HK", "Q45": "PT", "Q41": "GR", "Q664": "NZ", "Q213": "CZ", "Q28": "HU",
}  # fmt: skip


class WikidataAdapter:
    """Wikidata items: website, industry, country, founding year, headcount and LEI.

    Item data is read from /wiki/Special:EntityData/<id>.json, the path Wikidata's robots.txt
    allows. Name search uses the documented wbsearchentities action under a single, logged
    robots.txt exemption (see SEARCH_EXEMPTION). With name_search=False the adapter makes no
    exempted request and finds items only by a `wikidata_id` input column or a link from
    another source's matched record.
    """

    name = "wikidata"
    label = "Wikidata"
    terms_url = "https://www.wikidata.org/wiki/Wikidata:Data_access"
    licence = "CC0 1.0"
    attribution = "None required. Wikidata asks to be mentioned as the source"
    redistribution_ok = True
    group = "ror-wikidata"
    # An LEI on a Wikidata item was copied from GLEIF, so on that field it is not a second opinion.
    field_groups = {"lei": "gleif"}
    host = "www.wikidata.org"
    min_interval = 1.5

    def __init__(self, client: PoliteClient, name_search: bool = True):
        self.client = client
        self.name_search = name_search
        client.host_intervals.setdefault(self.host, max(self.min_interval, client.min_interval))
        if name_search and SEARCH_EXEMPTION not in client.exemptions:
            client.exemptions.append(SEARCH_EXEMPTION)

    def search(self, name: str, country: str | None) -> list[Candidate]:
        if not self.name_search:
            return []
        hits = self._search_hits(name)
        if not hits and core_name(name) != name:
            hits = self._search_hits(core_name(name))
        found = []
        for hit in hits:
            texts = [hit.get("label", ""), (hit.get("match") or {}).get("text", ""), *hit.get("aliases", [])]
            if max(name_score(name, text) for text in texts) < SEARCH_FETCH_SCORE:
                continue
            candidate = self.fetch(hit["id"], organizations_only=True)
            if candidate:
                found.append(candidate)
        return found

    def _search_hits(self, text: str) -> list[dict]:
        params = {
            "action": "wbsearchentities",
            "search": text,
            "language": "en",
            "type": "item",
            "limit": "5",
            "format": "json",
        }
        data = self.client.get_json(SEARCH_URL, params).data or {}
        if "error" in data:
            raise FetchError(f"Wikidata search error: {data['error'].get('code', 'unknown')}")
        return data.get("search", [])

    def fetch(self, record_id: str, organizations_only: bool = False) -> Candidate | None:
        qid = record_id.strip().upper()
        if not re.fullmatch(r"Q\d+", qid):
            return None
        reply = self.client.get_json(ENTITY_URL.format(qid=qid))
        entities = (reply.data or {}).get("entities", {})
        # A redirected item comes back under its new id.
        entity = entities.get(qid) or next(iter(entities.values()), None)
        if not entity:
            return None
        claims = entity.get("claims", {})
        label = _label(entity)
        if not label:
            return None
        if organizations_only and not any(p in claims for p in ORGANIZATION_PROPERTIES):
            return None
        aliases = [a["value"] for a in entity.get("aliases", {}).get("en", [])]
        aliases += [v["text"] for v in _values(claims, "P1448") if isinstance(v, dict) and v.get("text")]

        facts: dict[str, Fact] = {"legal_name": Fact(label, tuple(aliases))}
        countries = [c for c in (self._country(v.get("id")) for v in _values(claims, "P17")) if c]
        if countries:
            facts["country"] = Fact(countries[0], tuple(countries[1:]))
        websites = [v for v in _values(claims, "P856") if isinstance(v, str)]
        if websites:
            facts["website"] = Fact(websites[0], tuple(websites[1:]))
        industry = next((v.get("id") for v in _values(claims, "P452") if isinstance(v, dict)), None)
        if industry and (industry_label := self._label_of(industry)):
            facts["industry"] = Fact(industry_label)
        year = next((y for y in (parse_year(v.get("time", "")) for v in _values(claims, "P571")) if y), None)
        if year:
            facts["founded_year"] = Fact(year)
        employees = _latest_quantity(claims.get("P1128", []))
        if employees is not None:
            facts["employees"] = Fact(employees)
        leis = [v for v in _values(claims, "P1278") if isinstance(v, str)]
        if leis:
            facts["lei"] = Fact(leis[0], tuple(leis[1:]))
        return Candidate(
            source=self.name,
            record_id=entity.get("id", qid),
            name=label,
            aliases=aliases,
            countries=set(countries),
            url=reply.url,
            retrieved=reply.retrieved,
            as_of=entity.get("modified") or "",
            facts=facts,
            links={"gleif": leis} if leis else {},
        )

    def _country(self, qid: str | None) -> str | None:
        if not qid:
            return None
        if qid in COUNTRY_QIDS:
            return COUNTRY_QIDS[qid]
        reply = self.client.get_json(ENTITY_URL.format(qid=qid))
        entity = (reply.data or {}).get("entities", {}).get(qid, {})
        return next((v for v in _values(entity.get("claims", {}), "P297") if isinstance(v, str)), None)

    def _label_of(self, qid: str) -> str | None:
        reply = self.client.get_json(ENTITY_URL.format(qid=qid))
        return _label((reply.data or {}).get("entities", {}).get(qid, {}))


def _label(entity: dict) -> str | None:
    labels = entity.get("labels", {})
    chosen = labels.get("en") or labels.get("mul") or next(iter(labels.values()), None)
    return chosen["value"] if chosen else None


def _best(statements: list[dict]) -> list[dict]:
    """Preferred-rank statements when there are any, else normal rank. Never deprecated."""
    usable = [s for s in statements if s.get("mainsnak", {}).get("datavalue")]
    preferred = [s for s in usable if s.get("rank") == "preferred"]
    return preferred or [s for s in usable if s.get("rank") != "deprecated"]


def _values(claims: dict, prop: str) -> list:
    return [s["mainsnak"]["datavalue"]["value"] for s in _best(claims.get(prop, []))]


def _latest_quantity(statements: list[dict]) -> int | None:
    def point_in_time(statement: dict) -> str:
        for snak in statement.get("qualifiers", {}).get("P585", []):
            time = ((snak.get("datavalue") or {}).get("value") or {}).get("time")
            if time:
                return time
        return ""

    best = _best(statements)
    if not best:
        return None
    newest = max(best, key=point_in_time)
    try:
        return int(float(newest["mainsnak"]["datavalue"]["value"]["amount"]))
    except (KeyError, TypeError, ValueError):
        return None
