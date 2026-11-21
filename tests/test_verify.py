from business_research.models import CONFLICT, SINGLE, VERIFIED, Fact
from business_research.verify import verify_field


def test_no_values_is_no_result():
    assert verify_field("website", []) is None


def test_one_source_is_single_source():
    result = verify_field("website", [("a", Fact("https://example.com"))])
    assert result.status == SINGLE


def test_two_sources_agreeing_after_normalization_are_verified():
    result = verify_field(
        "website", [("a", Fact("https://www.example.com/en")), ("b", Fact("http://example.com"))]
    )
    assert result.status == VERIFIED
    assert result.value == "https://www.example.com/en"


def test_disagreement_is_a_conflict_and_keeps_both_values():
    result = verify_field("founded_year", [("a", Fact(1990)), ("b", Fact(1987))])
    assert result.status == CONFLICT
    assert "1990 [a]" in result.value and "1987 [b]" in result.value
    assert len(result.entries) == 2


def test_two_agree_one_disagrees_is_still_a_conflict():
    result = verify_field("country", [("a", Fact("DE")), ("b", Fact("Germany")), ("c", Fact("AT"))])
    assert result.status == CONFLICT
    assert result.value == "DE [a, b] | AT [c]"


def test_legal_name_agrees_across_suffix_spellings_and_aliases():
    names = [
        ("a", Fact("Siemens Aktiengesellschaft")),
        ("b", Fact("Siemens")),
        ("c", Fact("SAG", ("Siemens AG",))),
    ]
    assert verify_field("legal_name", names).status == VERIFIED


def test_any_shared_lei_counts_as_agreement():
    result = verify_field("lei", [("a", Fact("AAA")), ("b", Fact("BBB", ("aaa",)))])
    assert result.status == VERIFIED


def test_headcount_within_tolerance_agrees():
    assert verify_field("employees", [("a", Fact(1000)), ("b", Fact(1030))]).status == VERIFIED
    assert verify_field("employees", [("a", Fact(1000)), ("b", Fact(1500))]).status == CONFLICT
