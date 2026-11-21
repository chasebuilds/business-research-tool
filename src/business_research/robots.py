"""A small robots.txt reader that follows RFC 9309.

The standard library parser is not used because it takes the first matching rule and ignores
`*` wildcards. Real files (Wikidata's, for one) rely on longest-match and wildcards, so the
stdlib parser gives wrong answers on them in both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RobotsRules:
    groups: dict[str, list[tuple[bool, str]]] = field(default_factory=dict)
    allow_all: bool = False
    deny_all: bool = False

    def allowed(self, product_token: str, path: str) -> bool:
        if self.allow_all:
            return True
        if self.deny_all:
            return False
        token = product_token.lower()
        named = [rules for agent, rules in self.groups.items() if agent == token]
        rules = [r for group in named for r in group] if named else self.groups.get("*", [])
        best_len, best_allow = -1, True
        for allow, pattern in rules:
            # Longest pattern wins, and Allow wins a tie.
            better = len(pattern) > best_len or (len(pattern) == best_len and allow)
            if pattern and better and _matches(pattern, path):
                best_len, best_allow = len(pattern), allow
        return best_allow


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(regex + ("$" if anchored else ""), path) is not None


def parse(text: str) -> RobotsRules:
    rules = RobotsRules()
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value.lower())
            rules.groups.setdefault(value.lower(), [])
        elif key in ("allow", "disallow") and agents:
            in_rules = True
            for agent in agents:
                rules.groups[agent].append((key == "allow", value))
    return rules


def from_status(status: int, text: str) -> RobotsRules:
    """RFC 9309 section 2.3.1: no robots.txt (4xx) means no restrictions, an unreachable one
    (5xx) means assume everything is disallowed."""
    if 200 <= status < 300:
        return parse(text)
    if 400 <= status < 500:
        return RobotsRules(allow_all=True)
    return RobotsRules(deny_all=True)
