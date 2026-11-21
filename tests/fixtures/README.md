# Test fixtures

No test touches the network. These files stand in for the live sources.

| File | Origin |
|---|---|
| `gleif_search_siemens.json`, `gleif_record_siemens.json` | Recorded from the GLEIF API on 2026-11-21 (UTC), trimmed to the fields the adapter reads |
| `gleif_fulltext_sony.json` | Recorded from the GLEIF API on 2026-11-21 (UTC), trimmed. Legal name is in Japanese script |
| `ror_search_siemens_de.json` | Recorded from the ROR API on 2026-11-21 (UTC), trimmed |
| `wikidata_Q81230.json`, `wikidata_Q43035.json` | Recorded from Wikidata `Special:EntityData` on 2026-11-21 (UTC), trimmed to the claims the adapter reads |
| `wikidata_search_siemens.json` | Recorded from the Wikidata `wbsearchentities` action on 2026-11-21 (UTC), trimmed to three hits |
| `edgar_company_tickers.json`, `edgar_submissions_9990001.json` | Hand-built from the documented shape of the SEC files, with invented companies. Never recorded live |

GLEIF, ROR and Wikidata data is published under CC0.
