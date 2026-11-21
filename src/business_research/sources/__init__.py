from __future__ import annotations

import logging

from business_research.http import PoliteClient
from business_research.sources.base import SourceAdapter
from business_research.sources.edgar import EdgarAdapter, edgar_contact
from business_research.sources.gleif import GleifAdapter
from business_research.sources.ror import RorAdapter
from business_research.sources.wikidata import WikidataAdapter

log = logging.getLogger("business_research")

DEFAULT_SOURCES = ("gleif", "ror", "wikidata", "edgar")

__all__ = ["SourceAdapter", "GleifAdapter", "RorAdapter", "WikidataAdapter", "EdgarAdapter", "build_adapters"]


def build_adapters(
    client: PoliteClient,
    names: tuple[str, ...] | list[str] = DEFAULT_SOURCES,
    wikidata_search: bool = True,
) -> list[SourceAdapter]:
    """Adapters in priority order. EDGAR is skipped, with a log line, when no contact is set."""
    registry = {"gleif": GleifAdapter, "ror": RorAdapter, "wikidata": WikidataAdapter, "edgar": EdgarAdapter}
    adapters: list[SourceAdapter] = []
    for name in names:
        if name not in registry:
            raise ValueError(f"unknown source '{name}', choose from {', '.join(registry)}")
        if name == "edgar" and not edgar_contact():
            log.info("source edgar skipped: SEC_EDGAR_CONTACT is not set")
            continue
        if name == "wikidata":
            adapters.append(WikidataAdapter(client, name_search=wikidata_search))
            continue
        adapters.append(registry[name](client))
    return adapters
