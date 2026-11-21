from __future__ import annotations

import os

from business_research.http import PRODUCT, VERSION, PoliteClient
from business_research.matching import name_score
from business_research.models import Candidate, Fact

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
CONTACT_ENV = "SEC_EDGAR_CONTACT"

US_STATES = frozenset(
    """AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ
    NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY""".split()
)


def edgar_contact() -> str | None:
    return os.environ.get(CONTACT_ENV, "").strip() or None


class EdgarAdapter:
    """SEC EDGAR: legal name, SIC industry and website of companies that file with the SEC.

    The SEC asks every automated client to declare itself with an organization name and a
    contact email. This repo holds no such contact. The adapter reads it from the
    SEC_EDGAR_CONTACT environment variable and cannot be built without it.
    """

    name = "edgar"
    label = "SEC EDGAR"
    terms_url = "https://www.sec.gov/os/accessing-edgar-data"
    licence = "Public SEC filings data. No licence text was read for this build"
    attribution = "Not verified"
    # Not set to False: the adapter is opt-in and its licence line says it was not verified.
    redistribution_ok = True
    group = "sec"
    hosts = ("www.sec.gov", "data.sec.gov")

    def __init__(self, client: PoliteClient, contact: str | None = None, shortlist: int = 3):
        contact = contact or edgar_contact()
        if not contact:
            raise ValueError(f"EDGAR adapter is disabled: set {CONTACT_ENV} to 'Organization contact@domain'")
        self.client = client
        self.shortlist = shortlist
        for host in self.hosts:
            # Still the one tool name, with the contact the SEC asks for in place of the project URL.
            client.host_user_agents[host] = f"{PRODUCT}/{VERSION} ({contact})"

    def search(self, name: str, country: str | None) -> list[Candidate]:
        listing = self.client.get_json(TICKERS_URL).data or {}
        best: dict[int, float] = {}
        for row in listing.values():
            score = name_score(name, row.get("title", ""))
            if score >= 70:
                best[row["cik_str"]] = max(score, best.get(row["cik_str"], 0))
        top = sorted(best, key=lambda cik: (-best[cik], cik))[: self.shortlist]
        return [c for c in (self.fetch(str(cik)) for cik in top) if c]

    def fetch(self, record_id: str) -> Candidate | None:
        reply = self.client.get_json(SUBMISSIONS_URL.format(cik=int(record_id)))
        data = reply.data
        if not data or not data.get("name"):
            return None
        former = [f["name"] for f in data.get("formerNames") or [] if f.get("name")]
        facts = {"legal_name": Fact(data["name"], tuple(former))}
        # EDGAR uses its own codes for foreign countries. Only US states map cleanly.
        state = ((data.get("addresses") or {}).get("business") or {}).get("stateOrCountry")
        countries = {"US"} if state in US_STATES else set()
        if countries:
            facts["country"] = Fact("US")
        if data.get("website"):
            facts["website"] = Fact(data["website"])
        if data.get("sicDescription"):
            # SIC is its own taxonomy, so it is never compared with another source's industry label.
            facts["sic_industry"] = Fact(data["sicDescription"])
        return Candidate(
            source=self.name,
            record_id=str(int(record_id)),
            name=data["name"],
            aliases=former,
            countries=countries,
            url=reply.url,
            retrieved=reply.retrieved,
            facts=facts,
        )
