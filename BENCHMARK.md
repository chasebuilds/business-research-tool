# Benchmark

Written by `benchmark/run_benchmark.py`. Do not edit by hand.

**These numbers come from a mock harness, not from live sources.** The harness builds invented
companies, renders them into three fake sources with planted name variants, gaps, disagreements and
look-alike companies, then checks the tool's output against what was planted. It measures the
matching and verification logic. It says nothing about how complete or correct GLEIF, ROR,
Wikidata or SEC EDGAR are.

## Setup

- Seed 7, 300 invented companies in the truth set
- 279 input names: 249 drawn from the truth set (typed with suffix changes, case changes and typos), 30 that are in no source
- Fake sources and coverage: registry 80%, encyclopedia 70%, directory 60%
- Per field: 15% left blank, 8% given a disagreeing value, 3% of source names misspelled
- Thresholds: accept at name score 90, review from 70

## Result

| Measure | Result |
|---|---|
| Match precision (accepted matches that are the right entity) | 99.4% (466 of 469) |
| Match recall (right record accepted, when the source has one) | 89.8% (466 of 519) |
| **Wrong-entity matches accepted as fact** | **3 of 469 accepted matches, touching 2 of 279 input rows** |
| Verification status accuracy, end to end | 85.3% (1189 of 1394 field cells) |
| Verification status accuracy, rows where every match was right | 100.0% (1092 of 1092) |
| Names that are in no source and stayed out of the dataset | 30 of 30 |

A wrong-entity match is the costly error: facts about a different company land in the dataset
with a source link that looks fine. The count above is every such case in this run, not a sample.

## Where each lookup ended up

One lookup is one input name in one source.

| Outcome | Lookups |
|---|---|
| Source has the company: right record accepted | 466 |
| Source has the company: sent to Review with the right record listed | 44 |
| Source has the company: sent to Review without the right record | 2 |
| Source has the company: missed | 7 |
| Source does not have the company: nothing accepted | 315 (115 of them listed look-alikes for review) |
| Wrong record accepted | 3 |

## Verification status, expected against reported

Rows are what was planted, columns are what the tool reported. Cells where both are missing are left out.

| Planted \ Reported | verified | corroborated | single-source | conflict | missing |
|---|---|---|---|---|---|
| verified | 318 | 3 | 25 | 0 | 6 |
| corroborated | 0 | 118 | 33 | 0 | 5 |
| single-source | 1 | 0 | 707 | 2 | 73 |
| conflict | 15 | 4 | 28 | 46 | 1 |
| missing | 0 | 2 | 7 | 0 | 0 |

Most end-to-end status errors follow from a lookup that went to Review or was missed: with one
source fewer, a planted `verified` is reported as `single-source`. The second accuracy row shows
the verification step alone.

`verified` needs agreement between independent sources. The directory is modelled as copying
from the encyclopedia, so those two agreeing with no registry value is `corroborated`.

## Accept threshold sweep

Same data, different accept thresholds. The default is the row in bold.

| Accept score | Precision | Recall | Wrong-entity accepts | Rows touched |
|---|---|---|---|---|
| 85 | 98.9% | 90.0% | 5 | 4 |
| **90** | 99.4% | 89.8% | 3 | 2 |
| 95 | 99.5% | 83.6% | 2 | 1 |
| 100 | 99.5% | 78.2% | 2 | 1 |

## Reproduce

```
python benchmark/run_benchmark.py --seed 7
```

The run is deterministic: the same seed gives the same file.
