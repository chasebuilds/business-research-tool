from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from business_research import __version__, audit, mock
from business_research.http import DEFAULT_INTERVAL, DEFAULT_TTL, MIN_INTERVAL_FLOOR, PoliteClient
from business_research.inputs import read_companies
from business_research.matching import DEFAULT_ACCEPT, DEFAULT_REVIEW
from business_research.pipeline import research
from business_research.sources import DEFAULT_SOURCES, CsvSource, adapter_hosts, build_adapters
from business_research.workbook import summary_rows, write_workbook

log = logging.getLogger("business_research")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="business-research",
        description="Research a list of companies. Every fact gets a source, a date and a status.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="look companies up in the live public sources")
    run.add_argument("input", help="CSV or XLSX with a company name column and optional country column")
    run.add_argument(
        "-o", "--output", default="research.xlsx", help="output workbook (default research.xlsx)"
    )
    run.add_argument("--sources", default=",".join(DEFAULT_SOURCES), help="comma list, in priority order")
    run.add_argument(
        "--reference",
        metavar="CSV",
        help="your own reference list (id, name, optional country and fact columns) used as one more source",
    )
    run.add_argument("--cache-dir", default=".cache", help="on-disk response cache (default .cache)")
    run.add_argument("--refresh", action="store_true", help="ignore cached responses and fetch again")
    run.add_argument(
        "--offline",
        action="store_true",
        help="send no request at all: answer only from the cache, at any age",
    )
    run.add_argument(
        "--cache-days",
        type=float,
        default=DEFAULT_TTL / 86400,
        help=f"reuse cached answers up to this many days old (default {DEFAULT_TTL / 86400:g})",
    )
    run.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between requests to one host (default {DEFAULT_INTERVAL}, floor {MIN_INTERVAL_FLOOR})",
    )
    run.add_argument(
        "--source-interval",
        action="append",
        default=[],
        metavar="SOURCE=SECONDS",
        help=f"slow one source down, e.g. gleif=2 (repeatable, floor {MIN_INTERVAL_FLOOR})",
    )
    _thresholds(run)
    run.add_argument("--log", help="run log path (default: output name with .log)")
    run.add_argument(
        "--strict-robots",
        action="store_true",
        help="make no exempted request: Wikidata is then reached by id or link only, never by name search",
    )

    demo = commands.add_parser("demo", help="offline demo on invented companies and fake sources")
    demo.add_argument("-o", "--output", default="samples/demo_output.xlsx")
    demo.add_argument(
        "--input-csv", default="samples/demo_input.csv", help="where to write the demo input list"
    )
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--companies", type=int, default=40)
    _thresholds(demo)

    sample = commands.add_parser(
        "audit-sample", help="draw a random sample of accepted matches to check by hand"
    )
    sample.add_argument("workbook", help="an output workbook from run or demo")
    sample.add_argument("-o", "--output", default="audit_sample.xlsx")
    sample.add_argument("-n", "--size", type=int, default=50)
    sample.add_argument("--seed", type=int, default=1)

    score = commands.add_parser("audit-score", help="read pass or fail marks and report the error rate")
    score.add_argument("sample", help="an audit sample with the verdict column filled in")

    args = parser.parse_args(argv)
    if args.command == "audit-sample":
        count = audit.draw_sample(args.workbook, args.output, args.size, args.seed)
        print(f"Wrote {count} accepted matches to {args.output}. Mark each verdict pass or fail.")
        return 0
    if args.command == "audit-score":
        result = audit.score_sample(args.sample)
        print(audit.describe(result))
        return 0 if result["checked"] else 1
    if args.review > args.accept:
        parser.error("--review must not be higher than --accept")
    if args.command == "run":
        if args.offline and args.refresh:
            parser.error("--offline and --refresh cannot be combined")
        try:
            args.source_interval = _source_intervals(args.source_interval)
        except ValueError as exc:
            parser.error(str(exc))
    return _run(args) if args.command == "run" else _demo(args)


def _source_intervals(items: list[str]) -> dict[str, float]:
    intervals = {}
    for item in items:
        source, _, seconds = item.partition("=")
        try:
            intervals[source.strip()] = float(seconds)
        except ValueError:
            raise ValueError(f"--source-interval wants SOURCE=SECONDS, got {item!r}") from None
    return intervals


def _thresholds(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--accept", type=float, default=DEFAULT_ACCEPT, help="name score needed to accept a match"
    )
    parser.add_argument(
        "--review", type=float, default=DEFAULT_REVIEW, help="name score needed to list for review"
    )


def _run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    log_path = Path(args.log) if args.log else output.with_suffix(".log")
    _logging(log_path)
    companies = read_companies(args.input)
    if not companies:
        print(f"No companies found in {args.input}", file=sys.stderr)
        return 1
    client = PoliteClient(
        cache_dir=args.cache_dir,
        min_interval=args.min_interval,
        refresh=args.refresh,
        offline=args.offline,
        cache_ttl=args.cache_days * 86400,
    )
    adapters = build_adapters(
        client, [s.strip() for s in args.sources.split(",") if s.strip()], not args.strict_robots
    )
    if args.reference:
        adapters.append(CsvSource(args.reference))
    by_name = {a.name: a for a in adapters}
    for source, seconds in args.source_interval.items():
        if source not in by_name:
            log.warning("--source-interval %s ignored: that source is not in this run", source)
        for host in adapter_hosts(by_name[source]) if source in by_name else ():
            client.set_interval(host, seconds)
    if args.offline:
        log.info("offline: no requests will be sent, answers come from %s only", args.cache_dir)
    for exemption in client.exemptions:
        log.info(
            "robots exemption in force: %s%s (%s)", exemption.host, exemption.path_prefix, exemption.terms_url
        )
    log.info("run start: %d companies, sources %s", len(companies), ", ".join(a.name for a in adapters))
    log.info("user agent: %s", client.user_agent)
    result = research(companies, adapters, args.accept, args.review)
    result.stats = dict(client.stats)
    write_workbook(result, output)
    log.info("run end: wrote %s", output)
    _report(result, output, log_path)
    return 0


def _demo(args: argparse.Namespace) -> int:
    world = mock.generate(seed=args.seed, n_companies=args.companies * 2, n_inputs=args.companies, n_absent=4)
    input_csv = Path(args.input_csv)
    input_csv.parent.mkdir(parents=True, exist_ok=True)
    with input_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["company", "country"])
        writer.writerows([c.name, c.country or ""] for c in world.inputs)
    result = research(read_companies(input_csv), world.sources, args.accept, args.review)
    write_workbook(result, args.output)
    print(f"Demo input (invented companies): {input_csv}")
    _report(result, Path(args.output), None)
    return 0


def _report(result, output: Path, log_path: Path | None) -> None:
    for item, value in summary_rows(result):
        if not str(item).startswith(("source:", "run ")):
            print(f"{item:<44} {value}")
    print(f"Workbook: {output}")
    if log_path:
        print(f"Run log:  {log_path}")


def _logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)sZ %(levelname)-7s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    file_handler.formatter.converter = __import__("time").gmtime
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    log.addHandler(file_handler)
    log.addHandler(console)


if __name__ == "__main__":
    raise SystemExit(main())
