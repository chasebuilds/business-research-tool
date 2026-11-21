"""Score the tool against seeded fake sources and write BENCHMARK.md.

Run: python benchmark/run_benchmark.py
The numbers describe matching and verification logic on the mock harness in
business_research.mock. They are not measurements of the live sources.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from business_research import mock
from business_research.matching import DEFAULT_ACCEPT, DEFAULT_REVIEW
from business_research.models import ACCEPTED, CORROBORATED, FIELDS, REVIEW, SINGLE, VERIFIED
from business_research.pipeline import research

ROOT = Path(__file__).resolve().parent.parent
STATUSES = (VERIFIED, CORROBORATED, SINGLE, "conflict", "missing")


def expected_status(planted: list[tuple[str, str]]) -> str:
    """The right status for one field, from what was planted: (tag, independence group) per
    source that holds the field."""
    if not planted:
        return "missing"
    if len(planted) == 1:
        return SINGLE
    if any(tag != "same" for tag, _ in planted):
        return "conflict"
    return VERIFIED if len({group for _, group in planted}) >= 2 else CORROBORATED


def score(world: mock.World, accept: float = DEFAULT_ACCEPT, review: float = DEFAULT_REVIEW) -> dict:
    run = research(world.inputs, world.sources, accept, review)
    truth_of = {s.name: {rid: r.truth_id for rid, r in s.records.items()} for s in world.sources}
    record_for = {s.name: {r.truth_id: r for r in s.records.values()} for s in world.sources}

    pairs = Counter()
    rows = Counter()
    cells = Counter()
    clean_cells = Counter()
    confusion = Counter()
    for company in run.companies:
        truth_id = world.expected[company.input.name]
        wrong_sources = []
        all_right = True
        for source, decision in company.decisions.items():
            present = truth_id is not None and truth_id in record_for[source]
            pairs["present" if present else "absent"] += 1
            if decision.status == ACCEPTED:
                if present and truth_of[source][decision.chosen.candidate.record_id] == truth_id:
                    pairs["correct_accept"] += 1
                else:
                    pairs["wrong_accept"] += 1
                    wrong_sources.append(source)
                    all_right = False
            elif decision.status == REVIEW:
                listed = present and any(
                    truth_of[source][s.candidate.record_id] == truth_id for s in decision.scored
                )
                pairs[
                    "review_true_listed"
                    if listed
                    else ("review_true_not_listed" if present else "review_absent")
                ] += 1
                all_right = all_right and not present
            else:
                pairs["missed" if present else "correct_none"] += 1
                all_right = all_right and not present

        rows["inputs"] += 1
        rows["in_dataset"] += company.status == "matched"
        rows["wrong_entity_rows"] += bool(wrong_sources)
        rows["review_only"] += company.status == "review"
        rows["not_found"] += company.status == "not_found"
        if truth_id is None:
            rows["absent_inputs"] += 1
            rows["absent_kept_out"] += company.status != "matched"
            continue

        for field in FIELDS:
            planted = [
                (record_for[s.name][truth_id].tags[field], s.group)
                for s in world.sources
                if truth_id in record_for[s.name] and field in record_for[s.name][truth_id].tags
            ]
            want = expected_status(planted)
            got = company.fields[field].status if field in company.fields else "missing"
            if want == "missing" and got == "missing":
                continue
            cells["total"] += 1
            cells["right"] += want == got
            confusion[(want, got)] += 1
            if all_right:
                clean_cells["total"] += 1
                clean_cells["right"] += want == got

    accepts = pairs["correct_accept"] + pairs["wrong_accept"]
    return {
        "pairs": pairs,
        "rows": rows,
        "cells": cells,
        "clean_cells": clean_cells,
        "confusion": confusion,
        "precision": pairs["correct_accept"] / accepts if accepts else 0.0,
        "recall": pairs["correct_accept"] / pairs["present"] if pairs["present"] else 0.0,
        "status_accuracy": cells["right"] / cells["total"] if cells["total"] else 0.0,
        "clean_status_accuracy": clean_cells["right"] / clean_cells["total"] if clean_cells["total"] else 0.0,
    }


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def render(world: mock.World, main: dict, sweep: list[tuple[float, dict]], args) -> str:
    p, r, c = main["pairs"], main["rows"], main["cells"]
    accepts = p["correct_accept"] + p["wrong_accept"]
    lines = [
        "# Benchmark",
        "",
        "Written by `benchmark/run_benchmark.py`. Do not edit by hand.",
        "",
        "**These numbers come from a mock harness, not from live sources.** The harness builds invented",
        "companies, renders them into three fake sources with planted name variants, gaps, disagreements and",
        "look-alike companies, then checks the tool's output against what was planted. It measures the",
        "matching and verification logic. It says nothing about how complete or correct GLEIF, ROR,",
        "Wikidata or SEC EDGAR are.",
        "",
        "## Setup",
        "",
        f"- Seed {world.seed}, {len(world.truth)} invented companies in the truth set",
        f"- {r['inputs']} input names: {r['inputs'] - r['absent_inputs']} drawn from the truth set "
        f"(typed with suffix changes, case changes and typos), {r['absent_inputs']} that are in no source",
        "- Fake sources and coverage: "
        + ", ".join(f"{name} {int(cov * 100)}%" for name, cov, _ in mock.SOURCE_SPECS),
        f"- Per field: {int(mock.P_FIELD_MISSING * 100)}% left blank, "
        f"{int(mock.P_FIELD_DIFFERENT * 100)}% given a disagreeing value, "
        f"{int(mock.P_SOURCE_TYPO * 100)}% of source names misspelled",
        f"- Thresholds: accept at name score {args.accept:g}, review from {args.review:g}",
        "",
        "## Result",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Match precision (accepted matches that are the right entity) | {pct(main['precision'])} "
        f"({p['correct_accept']} of {accepts}) |",
        f"| Match recall (right record accepted, when the source has one) | {pct(main['recall'])} "
        f"({p['correct_accept']} of {p['present']}) |",
        f"| **Wrong-entity matches accepted as fact** | **{p['wrong_accept']} of {accepts} accepted matches, "
        f"touching {r['wrong_entity_rows']} of {r['inputs']} input rows** |",
        f"| Verification status accuracy, end to end | {pct(main['status_accuracy'])} ({c['right']} of {c['total']} field cells) |",
        f"| Verification status accuracy, rows where every match was right | {pct(main['clean_status_accuracy'])} "
        f"({main['clean_cells']['right']} of {main['clean_cells']['total']}) |",
        f"| Names that are in no source and stayed out of the dataset | {r['absent_kept_out']} of {r['absent_inputs']} |",
        "",
        "A wrong-entity match is the costly error: facts about a different company land in the dataset",
        "with a source link that looks fine. The count above is every such case in this run, not a sample.",
        "",
        "## Where each lookup ended up",
        "",
        "One lookup is one input name in one source.",
        "",
        "| Outcome | Lookups |",
        "|---|---|",
        f"| Source has the company: right record accepted | {p['correct_accept']} |",
        f"| Source has the company: sent to Review with the right record listed | {p['review_true_listed']} |",
        f"| Source has the company: sent to Review without the right record | {p['review_true_not_listed']} |",
        f"| Source has the company: missed | {p['missed']} |",
        f"| Source does not have the company: nothing accepted | {p['correct_none'] + p['review_absent']} "
        f"({p['review_absent']} of them listed look-alikes for review) |",
        f"| Wrong record accepted | {p['wrong_accept']} |",
        "",
        "## Verification status, expected against reported",
        "",
        "Rows are what was planted, columns are what the tool reported. Cells where both are missing are left out.",
        "",
        "| Planted \\ Reported | " + " | ".join(STATUSES) + " |",
        "|---|" + "---|" * len(STATUSES),
    ]
    for want in STATUSES:
        lines.append(
            f"| {want} | " + " | ".join(str(main["confusion"][(want, got)]) for got in STATUSES) + " |"
        )
    lines += [
        "",
        "Most end-to-end status errors follow from a lookup that went to Review or was missed: with one",
        "source fewer, a planted `verified` is reported as `single-source`. The second accuracy row shows",
        "the verification step alone.",
        "",
        "`verified` needs agreement between independent sources. The directory is modelled as copying",
        "from the encyclopedia, so those two agreeing with no registry value is `corroborated`.",
        "",
        "## Accept threshold sweep",
        "",
        "Same data, different accept thresholds. The default is the row in bold.",
        "",
        "| Accept score | Precision | Recall | Wrong-entity accepts | Rows touched |",
        "|---|---|---|---|---|",
    ]
    for threshold, result in sweep:
        mark = "**" if threshold == args.accept else ""
        lines.append(
            f"| {mark}{threshold:g}{mark} | {pct(result['precision'])} | {pct(result['recall'])} | "
            f"{result['pairs']['wrong_accept']} | {result['rows']['wrong_entity_rows']} |"
        )
    lines += [
        "",
        "## Reproduce",
        "",
        "```",
        f"python benchmark/run_benchmark.py --seed {world.seed}",
        "```",
        "",
        "The run is deterministic: the same seed gives the same file.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--companies", type=int, default=300)
    parser.add_argument("--inputs", type=int, default=250)
    parser.add_argument("--absent", type=int, default=30)
    parser.add_argument("--accept", type=float, default=DEFAULT_ACCEPT)
    parser.add_argument("--review", type=float, default=DEFAULT_REVIEW)
    parser.add_argument("--output", default=str(ROOT / "BENCHMARK.md"))
    args = parser.parse_args(argv)

    world = mock.generate(args.seed, args.companies, args.inputs, args.absent)
    result = score(world, args.accept, args.review)
    thresholds = sorted({85.0, 90.0, 95.0, 100.0, args.accept})
    sweep = [(t, score(world, t, min(args.review, t))) for t in thresholds]
    Path(args.output).write_text(render(world, result, sweep, args), encoding="utf-8", newline="\n")
    print(f"precision {pct(result['precision'])}  recall {pct(result['recall'])}  "
          f"wrong-entity accepts {result['pairs']['wrong_accept']}  "
          f"status accuracy {pct(result['status_accuracy'])}")  # fmt: skip
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
