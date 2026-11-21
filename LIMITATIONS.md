# Limitations

What this tool does not do, and where its answers need a second look.

## Coverage

- **Small and private companies are thin.** GLEIF only lists entities that bought an LEI (mostly firms that trade securities or report to regulators). ROR only lists organizations that show up in research output. A local business with neither will land in `NotFound`. That is a true statement about the sources, not a failed lookup.
- **Wikidata name search runs under an exemption to `robots.txt`.** Wikidata's `robots.txt` disallows `/w/`, where its search API lives, and its data access page invites bots to use that API. The tool allows the single `wbsearchentities` endpoint and logs every use. This is a judgement that the operator's API terms govern its API. If you do not want it, run with `--strict-robots`: Wikidata is then reached only by a ROR link or a `wikidata_id` column, and companies ROR does not know get no Wikidata fields.
- **Wikidata search returns look-alikes.** Brands, products, annual reports and stray duplicate items share company names. Hits are fetched only when the name is close, and kept only when the item has organization properties, but same-named organizations still tie. A tie is broken by country or by a link from another source, and otherwise goes to `Review`.
- **SEC EDGAR is off by default.** The SEC asks automated clients to send an organization name and contact email. This repo ships none. Set `SEC_EDGAR_CONTACT` to turn it on. The EDGAR adapter is tested against hand-built fixtures only and has never been run against the live SEC site.
- **No websites are crawled.** Nothing is scraped from company sites, LinkedIn, news or search engines. Fields come only from the listed APIs.
- **No contact data.** No people, emails or phone numbers. Company-level facts only.

## Matching

- **A name alone can be ambiguous.** Without a country column, two records with the same name in different countries go to `Review`. Adding a country column resolves most of these.
- **Look-alike names can still slip through.** A company whose name is one letter away from the input, in a source that does not hold the real company, can be accepted. The benchmark counts these cases. Raising `--accept` to 95 or 100 trades recall for fewer of them.
- **Duplicate check is by record, not by family.** Two input rows that land on the same source record are flagged. A parent and its subsidiary in the same list are not flagged, although GLEIF publishes those relationships.
- **Subsidiaries and parents are separate records.** "Siemens AG" and "Siemens Energy AG" are different entities and are treated that way. The tool does not roll subsidiaries up to a parent or pick the "main" entity for you.
- **Brand names differ from legal names.** "Google" will not match "Alphabet Inc." unless a source lists it as an alias.
- **Legal-suffix list is finite.** About 60 common forms (Inc, LLC, GmbH, S.A., K.K. and so on) are normalized. Rare local forms are compared as written.
- **Non-Latin names** are compared as given. There is no transliteration.

## Verification

- **"Verified" means independent sources agree, not that the fact is true.** Nothing is checked against the company itself.
- **Independence is a fixed table, not a measurement.** The tool treats GLEIF as independent of ROR and Wikidata, treats ROR and Wikidata as related, and treats every LEI as coming from GLEIF. Agreement among related sources is labelled `corroborated`. The table is a judgement about how these projects exchange data. It does not trace where one particular value came from, so a `verified` name or country could still share an upstream origin.
- **On the live sources only name and country can reach `verified`.** Website and founding year come from ROR and Wikidata, which are related, so the best they get is `corroborated`.
- **Country can mean different things.** GLEIF gives the headquarters and legal address, Wikidata and ROR give a single country. A company registered in one place and run from another can show as a conflict. Both values are kept.
- **Headcount** agrees when two figures are within 5%. Sources date their figures differently, so treat it as approximate.
- **Industry** labels come from different vocabularies. Wikidata's label and the SEC's SIC description are kept in separate columns and never compared.
- **Website** comparison uses the registrable domain, so a regional landing page and the main site count as the same site.
- **Founding year** from ROR for a regional branch is often the parent's year.

## Operation

- **Speed is capped on purpose.** At least 0.5 seconds between requests to one host, 1 second by default, slower for GLEIF and Wikidata. The floor cannot be lowered. A list of 1,000 companies takes roughly an hour on a cold cache.
- **A blocked source is dropped, not worked around.** Three 403 or 429 answers in a row, or a `Retry-After` longer than 120 seconds, end all requests to that host for the run. Rows after that point lack that source and say so in `notes`. Rerun later to fill them in.
- **Terms are checked by a person, not by code.** The licence and terms link per source are recorded in the adapters and the `Summary` sheet as read on 2026-11-20. If a source changes its terms, that record has to be updated by hand. The SEC EDGAR licence line is marked not verified.
- **Cached answers are up to 7 days old** unless `--refresh` is passed. The `retrieved_utc` column always shows when the data was really fetched.
- **APIs change.** The adapters were written against the responses seen on 2026-11-21 (UTC). Wikidata states its data access paths are not guaranteed stable.
- **Live matches are not graded by the tool.** The `audit-sample` and `audit-score` commands support a hand check and report it with a Wilson interval. No hand check has been done on the sample run in this repo.
- **Source dates are record dates.** `source_last_updated` is when the source last changed the record, not when each field was last confirmed. SEC EDGAR records carry none.
- **Benchmark numbers come from a mock harness.** They measure the matching and verification logic on invented companies. They are not accuracy figures for any live source.
