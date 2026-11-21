import pytest

from business_research.normalize import core_name, normalize_country, normalize_name, parse_year, website_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Apple Inc.", "apple"),
        ("APPLE INCORPORATED", "apple"),
        ("Siemens Aktiengesellschaft", "siemens"),
        ("Siemens AG", "siemens"),
        ("Acme GmbH & Co. KG", "acme"),
        ("Nestlé S.A.", "nestle"),
        ("Toyota Motor Co., Ltd.", "toyota motor"),
        ("The Example Company", "example"),
        ("Procter & Gamble Co", "procter and gamble"),
        ("Smith and Co", "smith"),
        ("Example Public Limited Company", "example"),
        ("Example Holdings Ltd", "example holdings"),
        ("Limited", "limited"),
    ],
)
def test_normalize_name_strips_legal_suffixes(raw, expected):
    assert normalize_name(raw) == expected


def test_core_name_keeps_the_typed_spelling():
    assert core_name("Nestlé S.A.") == "Nestlé"
    assert core_name("Acme GmbH & Co. KG") == "Acme"
    assert core_name("Inc") == "Inc"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("us", "US"),
        ("USA", "US"),
        ("United States", "US"),
        ("Germany", "DE"),
        ("UK", "GB"),
        ("", None),
        ("Atlantis", None),
    ],
)
def test_normalize_country(raw, expected):
    assert normalize_country(raw) == expected


def test_website_key_ignores_scheme_www_and_path():
    assert website_key("http://www.example.com/en/") == website_key("https://example.com") == "example.com"
    assert website_key("https://new.example.co.uk/x") == "example.co.uk"
    assert website_key("not a url") is None


def test_parse_year():
    assert parse_year("+1847-10-01T00:00:00Z") == 1847
    assert parse_year(1998) == 1998
    assert parse_year("unknown") is None
