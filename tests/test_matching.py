from business_research.matching import decide, name_score
from business_research.models import ACCEPTED, NONE, REVIEW, Candidate


def cand(record_id, name, country=None, aliases=(), active=True):
    return Candidate(
        "src",
        record_id,
        name,
        f"mock://{record_id}",
        "2026-01-01T00:00:00Z",
        aliases=list(aliases),
        countries={country} if country else set(),
        active=active,
    )


def test_suffix_variants_score_as_the_same_name():
    assert name_score("Siemens AG", "Siemens Aktiengesellschaft") == 100
    assert name_score("Example Corp", "EXAMPLE CORPORATION") == 100


def test_a_longer_name_is_not_the_same_company():
    assert name_score("Siemens", "Siemens Energy") < 90


def test_clear_match_is_accepted():
    decision = decide(
        "Example Corp", None, [cand("1", "Example Corporation"), cand("2", "Sample Industries")]
    )
    assert decision.status == ACCEPTED
    assert decision.chosen.candidate.record_id == "1"


def test_alias_can_carry_the_match():
    decision = decide("Example Corp", None, [cand("1", "Exa Worldwide", aliases=["Example Corporation"])])
    assert decision.status == ACCEPTED
    assert decision.chosen.matched_on == "Example Corporation"


def test_low_score_goes_to_review_not_into_the_dataset():
    decision = decide("Example Corp", None, [cand("1", "Exemple Corporation")])
    assert decision.status == REVIEW
    assert decision.chosen is None
    assert "below" in decision.reason


def test_nothing_similar_is_not_found():
    assert decide("Example Corp", None, [cand("1", "Completely Different Name")]).status == NONE
    assert decide("Example Corp", None, []).status == NONE


def test_same_name_twice_is_ambiguous_without_a_country():
    twins = [cand("1", "Example Ltd", "GB"), cand("2", "Example GmbH", "DE")]
    decision = decide("Example", None, twins)
    assert decision.status == REVIEW
    assert "too close" in decision.reason


def test_country_breaks_the_tie():
    twins = [cand("1", "Example Ltd", "GB"), cand("2", "Example GmbH", "DE")]
    decision = decide("Example", "DE", twins)
    assert decision.status == ACCEPTED
    assert decision.chosen.candidate.record_id == "2"


def test_right_name_in_the_wrong_country_goes_to_review():
    decision = decide("Example", "FR", [cand("1", "Example Ltd", "GB")])
    assert decision.status == REVIEW
    assert "country" in decision.reason


def test_unknown_candidate_country_does_not_block_a_match():
    assert decide("Example", "FR", [cand("1", "Example SA")]).status == ACCEPTED


def test_inactive_record_is_not_accepted_on_its_own():
    assert decide("Example", None, [cand("1", "Example Ltd", active=False)]).status == REVIEW
    both = [cand("1", "Example Ltd", active=False), cand("2", "Example Ltd")]
    assert decide("Example", None, both).chosen.candidate.record_id == "2"


def test_thresholds_are_adjustable():
    near = [cand("1", "Exampel Corp")]
    assert decide("Example Corp", None, near, accept=80).status == ACCEPTED
    assert decide("Example Corp", None, near, accept=99).status == REVIEW
