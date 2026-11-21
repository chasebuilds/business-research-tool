from __future__ import annotations

from typing import Protocol

from business_research.models import Candidate


class SourceAdapter(Protocol):
    """One data source. The pipeline only ever talks to this interface.

    `search` finds records by company name. `fetch` loads one record by the source's own id,
    which is how a link found in another source (an LEI, a Wikidata id) gets followed.
    A source that cannot do one of the two returns an empty list or None.
    """

    name: str
    label: str
    terms_url: str
    licence: str
    attribution: str
    # False means the source's terms forbid passing its data on. Such an adapter is refused.
    redistribution_ok: bool
    # Sources in one group feed each other. Agreement inside a group is not independent proof.
    group: str

    def search(self, name: str, country: str | None) -> list[Candidate]: ...

    def fetch(self, record_id: str) -> Candidate | None: ...


def source_group(adapter: SourceAdapter, field: str) -> str:
    """Independence group of a source for one field. A source can sit in another source's group
    for a field it copies from there (every LEI originates at GLEIF)."""
    return getattr(adapter, "field_groups", {}).get(field, adapter.group)
