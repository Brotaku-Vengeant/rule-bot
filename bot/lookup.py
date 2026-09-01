"""Term matching over the rules index.

No Discord imports here - this module is pure logic so it can be unit-tested
and driven from the CLI (build_index.py --query) without a bot token.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "data" / "rules.json"

# Below this score a candidate is noise; above ACCEPT it's a confident match.
FUZZY_ACCEPT = 88.0
FUZZY_SUGGEST = 65.0
AMBIGUITY_BAND = 4.0  # runners-up within this much of the top score tie it


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/possessives, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def singular_forms(text: str) -> list[str]:
    """Candidate singular spellings, most-likely first.

    English plurals are irregular enough that a single transform guesses wrong
    ("strikes" is -s, "foxes" is -es), so exact matching tries each candidate.
    """
    forms = []
    if text.endswith("ies") and len(text) > 4:
        forms.append(text[:-3] + "y")
    if text.endswith("s") and not text.endswith("ss") and len(text) > 3:
        forms.append(text[:-1])
    if text.endswith("es") and len(text) > 4:
        forms.append(text[:-2])
    return forms


@dataclass
class Result:
    """Outcome of one lookup."""
    kind: str                       # exact | fuzzy | ambiguous | miss
    entry: dict | None = None
    suggestions: list[dict] = field(default_factory=list)
    query: str = ""

    def describe(self) -> str:
        """Plain-text rendering, used by the CLI and tests."""
        if self.kind in ("exact", "fuzzy"):
            e = self.entry
            head = f"{e['name']}  [{e['category']}, p.{e['page']}]"
            if self.kind == "fuzzy":
                head += f"   (closest match for {self.query!r})"
            return f"{head}\n{e['text']}"
        if self.kind == "ambiguous":
            names = ", ".join(e["name"] for e in self.suggestions)
            return f"Ambiguous: {self.query!r} could be: {names}"
        names = ", ".join(e["name"] for e in self.suggestions)
        tail = f" Did you mean: {names}?" if names else ""
        return f"No rulebook entry found for {self.query!r}.{tail}"


class RuleIndex:
    def __init__(self, entries: list[dict]):
        self.entries = entries
        self._by_key: dict[str, dict] = {}
        for e in entries:
            self._by_key.setdefault(normalize(e["name"]), e)
            for alias in e.get("aliases", []):
                self._by_key.setdefault(normalize(alias), e)
        # Keys used for fuzzy matching map back to their entry.
        self._fuzzy_keys = list(self._by_key)

    @classmethod
    def load(cls, path: Path = DEFAULT_INDEX) -> "RuleIndex":
        data = json.loads(path.read_text(encoding="utf-8"))
        idx = cls(data["entries"])
        idx.rulebook = data.get("rulebook", "")
        return idx

    def names(self) -> list[str]:
        return [e["name"] for e in self.entries]

    def search(self, query: str) -> Result:
        q = normalize(query)
        if not q:
            return Result(kind="miss", query=query)

        # 1-2. Exact, then singular/plural candidates.
        for candidate in (q, *singular_forms(q)):
            if candidate in self._by_key:
                return Result(kind="exact", entry=self._by_key[candidate], query=query)

        # 3. Fuzzy. WRatio handles partials ("brutal" -> "brutal strike"),
        #    transpositions, and missing letters.
        scored = process.extract(
            q, self._fuzzy_keys, scorer=fuzz.WRatio, limit=6,
            score_cutoff=FUZZY_SUGGEST,
        )
        if not scored:
            return Result(kind="miss", query=query)

        top_score = scored[0][1]
        # Distinct entries near the top (aliases of the same entry don't
        # count as ambiguity).
        leaders: list[dict] = []
        for key, score, _ in scored:
            if top_score - score > AMBIGUITY_BAND:
                break
            entry = self._by_key[key]
            if entry not in leaders:
                leaders.append(entry)

        if top_score >= FUZZY_ACCEPT:
            if len(leaders) == 1:
                return Result(kind="fuzzy", entry=leaders[0], query=query)
            return Result(kind="ambiguous", suggestions=leaders[:4], query=query)

        # Not confident enough to answer: offer the nearest few.
        seen: list[dict] = []
        for key, _, _ in scored:
            entry = self._by_key[key]
            if entry not in seen:
                seen.append(entry)
        return Result(kind="miss", suggestions=seen[:3], query=query)
