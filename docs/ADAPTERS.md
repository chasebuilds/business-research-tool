# Source adapters

Every source sits behind one small interface. The pipeline never knows which source it is talking to. This page is the contract for writing a new one.

## The interface

Defined in [src/business_research/sources/base.py](../src/business_research/sources/base.py) as a `Protocol`:

| Member | Type | Meaning |
|---|---|---|
| `name` | `str` | Short id, used as the key everywhere (`gleif`, `ror`). Also the name of the id column the input file can carry |
| `label` | `str` | Human name for the `Licences` and `Summary` sheets |
| `terms_url` | `str` | Where the access terms were read |
| `licence` | `str` | Licence of the data, as published |
| `attribution` | `str` | What the licence asks users to credit |
| `redistribution_ok` | `bool` | `False` means the terms forbid passing the data on. The pipeline refuses such an adapter before any request |
| `group` | `str` | Independence group. Sources in one group feed each other, so their agreement is `corroborated`, not `verified` |
| `search(name, country)` | `list[Candidate]` | Records that might be the company. Return near misses too: matching is the pipeline's job |
| `fetch(record_id)` | `Candidate \| None` | One record by the source's own id. `None` when it does not exist |

Optional: `field_groups: dict[str, str]` puts one field in another group. Wikidata uses it for `lei`, because every LEI originates at GLEIF.

## Rules for a `Candidate`

- `record_id` is the source's own stable id. `url` points at that record, so a reader can check it.
- `retrieved` is the UTC time the data was fetched (`Response.retrieved` from the client), never the time of the run. A cached answer keeps its original time.
- `facts` maps a field from `models.FIELDS` to a `Fact`. Put equivalent forms (aliases, a second LEI) in `Fact.alt`, not in the value.
- `links` maps another source's `name` to ids that source would accept in `fetch`. The pipeline follows them, and the linked record still has to pass the name check.
- `as_of` is the date the source last changed the record, when it says so.
- `search` must not filter on country unless the API does it better than the matcher. A wrong-country record is useful evidence for `Review`.

## Errors

Raise `FetchError` (or let the client raise it) when the source fails. The pipeline catches it, logs it, writes it on the row and carries on with the other sources. A 404 is not an error: return `[]` or `None`.

## Network access

A live adapter takes a `PoliteClient` and calls only `client.get_json(url, params)`. That one call gives it, for free:

- robots.txt checked before every request
- per-host spacing, with a 0.5 second floor no setting can lower
- retry with backoff on 429 and 5xx, `Retry-After` honored
- stop on block (three 403 or 429 in a row)
- on-disk cache with a TTL

An adapter that needs a slower pace for its host sets it in `__init__`, the way `GleifAdapter` does: `client.host_intervals.setdefault(self.host, max(self.min_interval, client.min_interval))`.

## Registering and testing

1. Add the class to `registry` in `sources/__init__.py` and, if it should run by default, to `DEFAULT_SOURCES`.
2. Record one or two real responses into `tests/fixtures/` and serve them with the `make_client` fixture from `tests/conftest.py`. That fixture's `FakeSession` maps URL prefixes to canned replies and records every call. Any test that opens a socket fails.
3. Test the parse (`search`, `fetch`, missing record) and one pipeline run with the adapter next to a `MockSource`.

## Bring your own source: `CsvSource`

A client often holds a reference list of their own: a CRM export, a supplier master, last year's cleaned file. `CsvSource` turns that list into one more source, so its facts go through the same matching and cross-check as the public ones.

```powershell
.venv\Scripts\business-research run samples/companies.csv -o out.xlsx --reference my_list.csv
```

The file needs `id` and `name` columns. Optional columns:

| Column | Becomes |
|---|---|
| `aliases` | Other names, split on `;` |
| `country` | Country, as an ISO code or a common name. An unknown value stops the load |
| `url` | Link shown for the record. Default: file name and line |
| `lei`, `website`, `industry`, `sic_industry` | Text facts |
| `founded_year`, `employees` | Whole numbers. A value that is not one stops the load |
| `wikidata_id`, `ror_id`, `lei` | Links the pipeline follows into those sources |

Behavior:

- Its group is `user-supplied`, so agreement with GLEIF, ROR or Wikidata counts as `verified`. If the list was itself built from one of those sources, that is not independent evidence; construct `CsvSource(path, group="ror-wikidata")` (or the matching group) in code to say so.
- Its retrieval time is the file's modification time.
- An input row can name a record directly with a `reference_id` column.
- Duplicate ids and missing required columns stop the load with the line number.

## Why no fourth live source

Free sources with no key and no login that were considered:

- **OpenCorporates** and **UK Companies House** need an API key.
- **OpenAlex institutions** are keyed on ROR ids and built from ROR, so they would mostly repeat ROR's answers inside its group.
- **Wikipedia** is the same community data as Wikidata, in prose.

None adds independent, keyless evidence, so a user-supplied list was the more useful addition.
