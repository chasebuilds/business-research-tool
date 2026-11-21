import pytest
from openpyxl import load_workbook

from business_research import mock
from business_research.audit import describe, draw_sample, score_sample, wilson_interval
from business_research.cli import main
from business_research.pipeline import research
from business_research.workbook import write_workbook


@pytest.fixture
def demo_workbook(tmp_path):
    world = mock.generate(seed=3, n_companies=60, n_inputs=40, n_absent=4)
    return write_workbook(research(world.inputs, world.sources), tmp_path / "demo.xlsx")


def mark(path, verdicts):
    book = load_workbook(path)
    for row, verdict in zip(book["Audit"].iter_rows(min_row=2), verdicts, strict=False):
        row[5].value = verdict
    book.save(path)


def test_wilson_interval_matches_known_values():
    low, high = wilson_interval(0, 50)
    assert low == 0 and high == pytest.approx(0.0713, abs=0.0005)
    low, high = wilson_interval(5, 100)
    assert low == pytest.approx(0.0215, abs=0.0005) and high == pytest.approx(0.1118, abs=0.0005)
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_sample_is_seeded_and_drawn_from_accepted_matches(demo_workbook, tmp_path):
    assert draw_sample(demo_workbook, tmp_path / "a.xlsx", size=20, seed=5) == 20
    draw_sample(demo_workbook, tmp_path / "b.xlsx", size=20, seed=5)
    draw_sample(demo_workbook, tmp_path / "c.xlsx", size=20, seed=6)
    rows = {
        n: list(load_workbook(tmp_path / f"{n}.xlsx")["Audit"].iter_rows(values_only=True)) for n in "abc"
    }
    assert rows["a"] == rows["b"] != rows["c"]
    assert rows["a"][0][5] == "verdict" and all(r[5] in (None, "") for r in rows["a"][1:])
    assert all(str(r[4]).startswith("mock://") for r in rows["a"][1:])


def test_sample_never_exceeds_the_matches_available(demo_workbook, tmp_path):
    available = draw_sample(demo_workbook, tmp_path / "all.xlsx", size=10_000)
    assert 0 < available < 10_000


def test_score_reports_error_rate_with_interval(demo_workbook, tmp_path):
    path = tmp_path / "audit.xlsx"
    draw_sample(demo_workbook, path, size=20)
    assert score_sample(path)["checked"] == 0
    assert describe(score_sample(path)).startswith("No verdicts yet")
    mark(path, ["pass"] * 17 + ["FAIL", "wrong"])
    result = score_sample(path)
    assert (result["checked"], result["wrong"], result["unmarked"]) == (19, 2, 1)
    assert result["low"] < result["error_rate"] < result["high"]
    text = describe(result)
    assert (
        "Checked 19 accepted matches by hand, 2 wrong" in text and "1 sampled rows are still unmarked" in text
    )


def test_a_clean_audit_is_not_reported_as_zero_risk(demo_workbook, tmp_path):
    path = tmp_path / "audit.xlsx"
    draw_sample(demo_workbook, path, size=20)
    mark(path, ["pass"] * 20)
    result = score_sample(path)
    assert result["error_rate"] == 0 and result["high"] > 0.1


def test_audit_commands(demo_workbook, tmp_path, capsys):
    sample = tmp_path / "s.xlsx"
    assert main(["audit-sample", str(demo_workbook), "-o", str(sample), "-n", "10"]) == 0
    assert main(["audit-score", str(sample)]) == 1  # nothing marked yet
    mark(sample, ["pass"] * 10)
    assert main(["audit-score", str(sample)]) == 0
    assert "Wilson" in capsys.readouterr().out
