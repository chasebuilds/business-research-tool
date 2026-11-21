from business_research import mock
from business_research.models import CORROBORATED, SINGLE, VERIFIED

from run_benchmark import expected_status, main, score


def small_world(seed=11):
    return mock.generate(seed=seed, n_companies=80, n_inputs=60, n_absent=8)


def test_generator_is_seeded():
    a, b = small_world(), small_world()
    assert [c.name for c in a.inputs] == [c.name for c in b.inputs]
    assert [list(s.records) for s in a.sources] == [list(s.records) for s in b.sources]
    assert [c.name for c in small_world(12).inputs] != [c.name for c in a.inputs]


def test_world_has_the_planted_problems():
    world = small_world()
    tags = [t for s in world.sources for r in s.records.values() for t in r.tags.values()]
    assert "diff" in tags and "same" in tags
    assert sum(v is None for v in world.expected.values()) == 8
    assert any(len(s.records) < len(world.truth) for s in world.sources)


def test_expected_status_rules():
    assert expected_status([]) == "missing"
    assert expected_status([("diff", "x")]) == SINGLE
    assert expected_status([("same", "x"), ("same", "y")]) == VERIFIED
    assert expected_status([("same", "x"), ("same", "x")]) == CORROBORATED
    assert expected_status([("same", "x"), ("diff", "y"), ("same", "x")]) == "conflict"


def test_score_is_repeatable_and_counts_add_up():
    world = small_world()
    first, second = score(world), score(world)
    assert first["pairs"] == second["pairs"] and first["cells"] == second["cells"]
    pairs = first["pairs"]
    assert pairs["present"] + pairs["absent"] == len(world.inputs) * len(world.sources)
    assert first["rows"]["inputs"] == len(world.inputs)
    assert 0 <= first["precision"] <= 1 and 0 <= first["recall"] <= 1
    # With every match right, a status error can only come from the verification step itself.
    assert first["clean_status_accuracy"] == 1.0


def test_a_stricter_threshold_never_accepts_more():
    world = small_world()
    loose, strict = score(world, accept=85, review=70), score(world, accept=100, review=70)
    accepted = lambda result: result["pairs"]["correct_accept"] + result["pairs"]["wrong_accept"]  # noqa: E731
    assert accepted(strict) <= accepted(loose)


def test_benchmark_writes_the_report(tmp_path):
    out = tmp_path / "BENCHMARK.md"
    args = ["--companies", "80", "--inputs", "60", "--absent", "8", "--output", str(out)]
    assert main(args) == 0
    text = out.read_text(encoding="utf-8")
    assert "mock harness, not from live sources" in text
    assert "Wrong-entity matches accepted as fact" in text
    assert chr(0x2014) not in text  # house rule: no em dashes in any file
