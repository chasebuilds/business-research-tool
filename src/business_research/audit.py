"""Hand audit of accepted matches.

The tool cannot grade its own live matches: only a person looking at the source record can say
whether it is the company the client meant. `draw_sample` picks a seeded random sample of
accepted matches into a sheet with an empty verdict column. `score_sample` reads the marks back
and reports the error rate with a Wilson interval, so a clean check of 50 records is stated as
"error rate below about 7%" and not as "100% accurate".
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from openpyxl import Workbook, load_workbook

HEADER = ["input_name", "source", "matched_name", "source_record_id", "url", "verdict", "comment"]
PASS_WORDS = {"pass", "ok", "y", "yes", "right", "correct", "1", "true"}
FAIL_WORDS = {"fail", "wrong", "n", "no", "incorrect", "0", "false"}


def draw_sample(workbook: str | Path, output: str | Path, size: int = 50, seed: int = 1) -> int:
    """Write a random sample of accepted matches for a person to mark pass or fail."""
    rows = list(load_workbook(workbook, read_only=True)["Sources"].iter_rows(values_only=True))
    column = {name: index for index, name in enumerate(rows[0])}
    matches = sorted(
        (
            str(row[column["input_name"]]),
            str(row[column["source"]]),
            str(row[column["value"]]),
            str(row[column["source_record_id"]]),
            str(row[column["url"]]),
        )
        for row in rows[1:]
        # One legal_name row exists per accepted match, so it stands for the match.
        if row[column["field"]] == "legal_name"
    )
    chosen = sorted(random.Random(seed).sample(matches, min(size, len(matches))))
    book = Workbook()
    sheet = book.active
    sheet.title = "Audit"
    sheet.append(HEADER)
    for match in chosen:
        sheet.append([*match, "", ""])
    sheet.freeze_panes = "A2"
    for letter, width in zip("ABCDEFG", (34, 12, 40, 24, 60, 10, 40), strict=True):
        sheet.column_dimensions[letter].width = width
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    book.save(output)
    return len(chosen)


def wilson_interval(errors: int, checked: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for an error rate. Stays sensible at zero errors, where the
    plain formula would claim an interval of exactly 0 to 0."""
    if checked == 0:
        return 0.0, 1.0
    p = errors / checked
    denominator = 1 + z * z / checked
    centre = (p + z * z / (2 * checked)) / denominator
    half = z * math.sqrt(p * (1 - p) / checked + z * z / (4 * checked * checked)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def score_sample(path: str | Path) -> dict[str, float | int]:
    rows = list(load_workbook(path, read_only=True)["Audit"].iter_rows(values_only=True))
    verdict_column = list(rows[0]).index("verdict")
    marks = [str(row[verdict_column] or "").strip().lower() for row in rows[1:]]
    passed = sum(mark in PASS_WORDS for mark in marks)
    failed = sum(mark in FAIL_WORDS for mark in marks)
    checked = passed + failed
    low, high = wilson_interval(failed, checked)
    return {
        "sampled": len(marks),
        "checked": checked,
        "unmarked": len(marks) - checked,
        "wrong": failed,
        "error_rate": failed / checked if checked else 0.0,
        "low": low,
        "high": high,
    }


def describe(result: dict[str, float | int]) -> str:
    if not result["checked"]:
        return f"No verdicts yet: {result['sampled']} rows sampled, none marked pass or fail."
    text = (
        f"Checked {result['checked']} accepted matches by hand, {result['wrong']} wrong. "
        f"Error rate {100 * result['error_rate']:.1f}%, "
        f"95% interval {100 * result['low']:.1f}% to {100 * result['high']:.1f}% (Wilson)."
    )
    if result["unmarked"]:
        text += f" {result['unmarked']} sampled rows are still unmarked."
    return text
