from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

# Legal-form tokens stripped from the END of a name only. "Group" and "Holdings" are kept on
# purpose: "X Holdings" and "X Ltd" are often different legal entities.
LEGAL_SUFFIXES = frozenset(
    """inc incorporated corp corporation co company ltd limited llc llp lp plc gmbh ag kg kgaa se sa
    sas sarl srl spa nv bv ab as asa oy oyj aps pty pte sdn bhd kk sl ltda sae aktiengesellschaft
    mbh ug ohg ev eg sca scs snc sro zrt kft doo dd ad tbk pjsc jsc ooo""".split()
)

_PHRASES = (
    ("public limited company", "plc"),
    ("limited liability company", "llc"),
    ("joint stock company", "jsc"),
    ("societe anonyme", "sa"),
    ("societas europaea", "se"),
    ("kabushiki kaisha", "kk"),
    ("naamloze vennootschap", "nv"),
    ("besloten vennootschap", "bv"),
    ("gesellschaft mit beschrankter haftung", "gmbh"),
)

_COUNTRY_NAMES = {
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "america": "US", "germany": "DE", "deutschland": "DE", "united kingdom": "GB", "uk": "GB",
    "great britain": "GB", "england": "GB", "japan": "JP", "france": "FR", "china": "CN",
    "switzerland": "CH", "netherlands": "NL", "the netherlands": "NL", "south korea": "KR", "korea": "KR",
    "canada": "CA", "australia": "AU", "italy": "IT", "spain": "ES", "sweden": "SE", "india": "IN",
    "brazil": "BR", "mexico": "MX", "ireland": "IE", "belgium": "BE", "denmark": "DK", "norway": "NO",
    "finland": "FI", "austria": "AT", "singapore": "SG", "taiwan": "TW", "israel": "IL", "poland": "PL",
    "south africa": "ZA", "luxembourg": "LU", "hong kong": "HK", "portugal": "PT", "new zealand": "NZ",
    "usa.": "US", "deu": "DE", "gbr": "GB", "jpn": "JP", "fra": "FR", "chn": "CN", "che": "CH",
    "nld": "NL", "kor": "KR", "can": "CA", "aus": "AU", "ita": "IT", "esp": "ES", "swe": "SE",
    "ind": "IN", "bra": "BR", "mex": "MX", "irl": "IE", "bel": "BE", "dnk": "DK", "nor": "NO",
    "fin": "FI", "aut": "AT", "sgp": "SG", "twn": "TW", "isr": "IL",
}  # fmt: skip

_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne"}


def strip_accents(text: str) -> str:
    text = text.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("Æ", "AE")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def name_tokens(name: str) -> list[str]:
    text = strip_accents(name).lower().replace("&", " and ")
    # "S.A." and "Co., Ltd." must become "sa" and "co ltd", so dots vanish and other punctuation splits.
    text = text.replace(".", "").replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    for phrase, token in _PHRASES:
        text = re.sub(rf"\b{phrase}\b", token, text)
    tokens = text.split()
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    return tokens


def normalize_name(name: str) -> str:
    """Lowercase, accent-free name with trailing legal-form tokens removed."""
    tokens = name_tokens(name)
    core = list(tokens)
    while len(core) > 1:
        if len(core) > 2 and core[-2] == "and" and core[-1] in LEGAL_SUFFIXES:
            core = core[:-2]
        elif core[-1] in LEGAL_SUFFIXES:
            core.pop()
        else:
            break
    return " ".join(core)


def core_name(name: str) -> str:
    """The name as typed, minus trailing legal-form words. Used to build search queries."""
    words = name.replace(",", " ").split()
    while len(words) > 1 and (name_tokens(words[-1]) or [""])[0] in LEGAL_SUFFIXES | {"and", ""}:
        words.pop()
    return " ".join(words)


def normalize_country(value: str | None) -> str | None:
    """ISO 3166-1 alpha-2 code, or None when the value is empty or not recognized."""
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 2 and text.isalpha():
        return "GB" if text.upper() == "UK" else text.upper()
    return _COUNTRY_NAMES.get(text.lower())


def website_key(url: str) -> str | None:
    """Registrable domain, so http://www.example.com/en and https://example.com compare equal."""
    text = str(url).strip().lower()
    if not text:
        return None
    host = urlsplit(text if "//" in text else "//" + text).hostname or ""
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2:
        return None
    take = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-take:])


def parse_year(value: object) -> int | None:
    match = re.search(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)", str(value))
    return int(match.group(1)) if match else None
