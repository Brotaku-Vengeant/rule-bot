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
DUA_INDEX = Path(__file__).resolve().parent.parent / "data" / "dua.json"

# Below this score a candidate is noise; above ACCEPT it's a confident match.
FUZZY_ACCEPT = 88.0
FUZZY_SUGGEST = 65.0
AMBIGUITY_BAND = 4.0  # runners-up within this much of the top score tie it

# List commands: a query that names a whole category returns every entry in it
# rather than a single definition. Checked before term matching, so the word
# can't fuzzy-match something else ("states" used to land on the Dor Un
# Avathar's "Custom States"). Each maps to (category, display title, related
# entries worth pointing at).
LIST_COMMANDS = {
    "states": ("state", "States", ("Custom States",)),
    "declarations": ("declaration", "Declarations", ()),
    # "schools" used to reach the School definition by plural matching; that
    # stays one lookup away as [[school]] and as the see-also.
    "schools": ("school", "Schools", ("School",)),
    "special effects": ("special effect", "Special Effects", ()),
    "special effect": ("special effect", "Special Effects", ()),
    # Both forms: "class" alone was ambiguous between two class-rule entries.
    "classes": ("class", "Classes", ("Credits and Levels", "Portraying A Class")),
    "class": ("class", "Classes", ("Credits and Levels", "Portraying A Class")),
    # Plural only: [[monster]] stays the rulebook's Monster class, which the
    # plural used to reach and which is kept as the see-also.
    "monsters": ("monster", "Monsters", ("Monster",)),
    # Three categories, grouped by the book's own sections. The rulebook says
    # "Magic Items, or Relics, fall into one of three categories" - Trinkets,
    # Talismans, Artifacts - so "relics" is the same list, not a fourth type.
    "magic items": (("trinket", "talisman", "artifact"), "Magic Items", ()),
    "magic item": (("trinket", "talisman", "artifact"), "Magic Items", ()),
    "relics": (("trinket", "talisman", "artifact"), "Magic Items", ()),
}


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
    kind: str                       # exact | fuzzy | ambiguous | miss | list
    entry: dict | None = None
    suggestions: list[dict] = field(default_factory=list)
    query: str = ""
    title: str = ""                 # list results: the category's display name
    related: list[dict] = field(default_factory=list)   # list results: see-also

    def describe(self) -> str:
        """Plain-text rendering, used by the CLI and tests."""
        if self.kind == "list":
            names = ", ".join(e["name"] for e in self.suggestions)
            return f"{self.title} ({len(self.suggestions)}): {names}"
        if self.kind in ("exact", "fuzzy"):
            e = self.entry
            # Only the rulebook has printed pages; the Dor Un Avathar is a
            # Google Doc and cites its section instead.
            where = f"p.{e['page']}" if e.get("page") else e.get("section", "")
            head = f"{e['name']}  [{e['category']}{', ' + where if where else ''}]"
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
    def load(cls, *paths: Path) -> "RuleIndex":
        """Load and merge every index file that exists.

        The bot answers from two books - the rulebook PDF and the Dor Un
        Avathar - built by separate pipelines into separate files, so either
        can be re-indexed without rebuilding the other. Each entry carries its
        own "source", which is what the citation footer uses; the index-level
        name is only a fallback for entries predating that field.

        The rulebook is loaded first, so if a future edition of either book
        ever introduces a name the other already uses, the rulebook keeps the
        bare term and the later one is reachable by its qualified name.
        """
        paths = paths or (DEFAULT_INDEX, DUA_INDEX)
        entries: list[dict] = []
        sources: list[str] = []
        for path in paths:
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            book = data.get("rulebook", "")
            for entry in data["entries"]:
                entry.setdefault("source", book)
            entries.extend(data["entries"])
            if book:
                sources.append(book)
        if not entries:
            raise FileNotFoundError(
                f"No index found. Looked in: {', '.join(str(p) for p in paths)}. "
                "Run scripts/build_index.py (and scripts/build_dua.py)."
            )
        idx = cls(entries)
        idx.rulebook = sources[0] if sources else ""
        idx.sources = sources
        return idx

    def names(self) -> list[str]:
        return [e["name"] for e in self.entries]

    def search(self, query: str) -> Result:
        q = normalize(query)
        if not q:
            return Result(kind="miss", query=query)

        # 0. List commands name a whole category, e.g. [[states]].
        if q in LIST_COMMANDS:
            category, title, related_names = LIST_COMMANDS[q]
            # A command names one category or several (magic items span three).
            categories = (category,) if isinstance(category, str) else category
            # Sections keep book order (so monster tiers run 1 to 4), names are
            # alphabetical within each. A single-section list is plain A-Z.
            section_rank: dict[str, int] = {}
            for e in self.entries:
                section_rank.setdefault(e.get("section") or "", len(section_rank))
            members = sorted((e for e in self.entries if e["category"] in categories),
                             key=lambda e: (section_rank[e.get("section") or ""],
                                            e["name"]))
            related = [self._by_key[normalize(n)] for n in related_names
                       if normalize(n) in self._by_key]
            return Result(kind="list", suggestions=members, query=query,
                          title=title, related=related)

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
