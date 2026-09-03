"""Matching behavior tests. Run with:  python -m pytest

Uses the real generated index when present (integration-flavored), plus a tiny
synthetic index for behavior that shouldn't depend on rulebook content.
"""

import pytest

from bot.lookup import DEFAULT_INDEX, RuleIndex, normalize, singular_forms

TOY = [
    {"name": "Brutal Strike", "aliases": [], "category": "ability",
     "page": 61, "section": "Magic and Abilities", "text": "Target is Cursed."},
    {"name": "Insubstantial", "aliases": [], "category": "state",
     "page": 31, "section": "States Defined", "text": "May not interact."},
    {"name": "Gift of Air", "aliases": [], "category": "ability",
     "page": 64, "section": "Magic and Abilities", "text": "Air."},
    {"name": "Gift of Fire", "aliases": [], "category": "ability",
     "page": 64, "section": "Magic and Abilities", "text": "Fire."},
    {"name": "Coup de Grace", "aliases": ["coup", "cdg"], "category": "ability",
     "page": 62, "section": "Magic and Abilities", "text": "Finish them."},
]


@pytest.fixture
def idx():
    return RuleIndex(TOY)


def test_normalize_strips_punctuation_and_case():
    assert normalize("  Brutal   Strike!! ") == "brutal strike"
    assert normalize("Bearer's") == "bearer"


def test_singular():
    assert "wound" in singular_forms("wounds")
    assert "ability" in singular_forms("abilities")
    assert singular_forms("harness") == []   # -ss is not a plural


def test_exact_hit(idx):
    r = idx.search("brutal strike")
    assert r.kind == "exact" and r.entry["name"] == "Brutal Strike"


def test_exact_is_case_and_punctuation_insensitive(idx):
    assert idx.search("  INSUBSTANTIAL. ").kind == "exact"


def test_plural_query_finds_singular_entry(idx):
    r = idx.search("brutal strikes")
    assert r.kind == "exact" and r.entry["name"] == "Brutal Strike"


def test_alias_hit(idx):
    r = idx.search("cdg")
    assert r.kind == "exact" and r.entry["name"] == "Coup de Grace"


def test_fuzzy_misspelling(idx):
    r = idx.search("insubstantal")
    assert r.kind == "fuzzy" and r.entry["name"] == "Insubstantial"


def test_ambiguous_offers_choices_not_a_guess(idx):
    r = idx.search("gift")
    assert r.kind == "ambiguous"
    names = {e["name"] for e in r.suggestions}
    assert {"Gift of Air", "Gift of Fire"} <= names


def test_miss_on_nonsense(idx):
    r = idx.search("zzzzqqqq")
    assert r.kind == "miss" and r.entry is None


def test_empty_query_is_a_miss(idx):
    assert idx.search("   ").kind == "miss"


# --- against the real index, when it has been built ---

real = pytest.mark.skipif(not DEFAULT_INDEX.exists(),
                          reason="data/rules.json not built")


@real
def test_real_index_loads_and_has_expected_anchors():
    idx = RuleIndex.load()
    for term, cat in [("Brutal Strike", "ability"), ("Insubstantial", "state"),
                      ("Cursed", "state"), ("Word of Mending", "ability")]:
        r = idx.search(term)
        assert r.kind == "exact", term
        assert r.entry["category"] == cat, term
        assert len(r.entry["text"]) > 30, term


@real
def test_real_index_texts_are_verbatim_ascii_clean():
    # Ligatures/smart quotes must have been normalized at extraction time.
    idx = RuleIndex.load()
    bad = [e["name"] for e in idx.entries
           if any(ch in e["text"] for ch in "ﬁﬂ’“”")]
    assert not bad, f"unnormalized typography in: {bad[:5]}"


# --- embed budget (needs discord.py; skipped if unavailable) ---

discord_available = pytest.importorskip("discord", reason="discord.py not installed")


@real
def test_five_long_entries_fit_one_discord_message():
    """Five max-length lookups must not exceed Discord's 6000-char cap."""
    from bot.formatting import embed_cost, fit_embeds, render

    idx = RuleIndex.load()
    longest = sorted(idx.entries, key=lambda e: len(e["text"]), reverse=True)[:5]
    embeds = [render(idx.search(e["name"]), idx.rulebook) for e in longest]
    assert sum(embed_cost(e) for e in embeds) > 6000, "pick longer fixtures"

    fitted = fit_embeds(embeds)
    assert len(fitted) == 5                      # nothing dropped
    assert sum(embed_cost(e) for e in fitted) <= 6000
    # Budget is shared fairly rather than gutting the first entries.
    lengths = [len(e.description) for e in fitted]
    assert min(lengths) > 600


@real
def test_short_replies_are_left_alone():
    from bot.formatting import fit_embeds, render

    idx = RuleIndex.load()
    embeds = [render(idx.search(q), idx.rulebook) for q in ("brutal strike", "frozen")]
    before = [e.description for e in embeds]
    assert [e.description for e in fit_embeds(embeds)] == before


# --- server listing ---

@real
def test_guild_list_embed_reports_every_server():
    """The /servers embed must name every guild and count them correctly."""
    import datetime as _dt
    from types import SimpleNamespace

    from bot.formatting import guild_list_embed

    def fake(name, gid, members, joined=None):
        me = SimpleNamespace(joined_at=joined)
        return SimpleNamespace(name=name, id=gid, member_count=members, me=me)

    guilds = [
        fake("Amtgard Club", 111, 240, _dt.datetime(2026, 8, 31)),
        fake("Test Server", 222, 3),
        fake("Another Park", 333, 57, _dt.datetime(2026, 9, 1)),
    ]
    embed = guild_list_embed(guilds, 'Amtgard v8.08 "Spongy"')

    assert "3 servers" in embed.title
    for g in guilds:
        assert g.name in embed.description
        assert str(g.id) in embed.description
    assert "2026-08-31" in embed.description   # join date shown when known
    assert embed.description.index("Amtgard Club") < \
           embed.description.index("Another Park")  # sorted by size


@real
def test_guild_list_embed_handles_one_and_none():
    from bot.formatting import guild_list_embed
    from types import SimpleNamespace

    one = [SimpleNamespace(name="Solo", id=1, member_count=5,
                           me=SimpleNamespace(joined_at=None))]
    assert "1 server" in guild_list_embed(one, "rb").title

    empty = guild_list_embed([], "rb")
    assert "0 servers" in empty.title
    assert "Not in any servers" in empty.description


@real
def test_guild_list_embed_fits_discord_limit():
    """A large server list must be trimmed, not rejected by Discord."""
    from types import SimpleNamespace

    from bot.formatting import embed_cost, guild_list_embed

    many = [SimpleNamespace(name=f"Server Number {i} With A Long Name",
                            id=10_000_000_000_000_000 + i, member_count=i * 7,
                            me=SimpleNamespace(joined_at=None))
            for i in range(200)]
    assert embed_cost(guild_list_embed(many, "rb")) <= 6000


# --- award standards (Appendix A) ---

@real
def test_ladder_awards_are_indexed():
    """All nine Ladder Awards, plus Knighthood and Masterhood."""
    idx = RuleIndex.load()
    by_name = {e["name"]: e for e in idx.entries}

    # Stored under the full title the rulebook uses, so a bare "Warrior" or
    # "Crown" stays free for the class / other meanings of the same word.
    ladder = [f"Order of the {n}" for n in
              ("Rose", "Smith", "Lion", "Crown", "Owl",
               "Dragon", "Garber", "Warrior", "Battle")]
    for name in ladder + ["Knighthood", "Masterhood", "Ladder Awards"]:
        assert name in by_name, f"{name} missing from the index"
        assert len(by_name[name]["text"]) > 80

    assert all(by_name[n]["category"] == "award" for n in ladder)
    assert by_name["Order of the Lion"]["section"] == "Award Standards"
    # No AWARD may occupy a bare common word - those belong to the class,
    # the equipment term, or whatever else shares the name. Other categories
    # are welcome to them: "Warrior" is correctly the Warrior class.
    bare = {"Rose", "Smith", "Lion", "Crown", "Owl", "Dragon",
            "Garber", "Warrior", "Battle"}
    squatters = [n for n in bare & set(by_name)
                 if by_name[n]["category"] == "award"]
    assert not squatters, f"awards squatting on bare names: {squatters}"
    assert by_name["Warrior"]["category"] == "class"


@real
def test_award_entries_have_no_column_bleed():
    """A neighbouring column's hanging list marker must not land mid-sentence."""
    import re

    idx = RuleIndex.load()
    for e in idx.entries:
        if e["category"] != "award":
            continue
        # "the call 8. of duty" - a marker wedged between two lowercase words.
        assert not re.search(r"[a-z] [0-9]{1,2}\. [a-z]", e["text"]), e["name"]


# --- verbatim fidelity ---

@real
def test_every_field_value_appears_verbatim_in_the_source():
    """No stored value may differ from the rulebook, not even by a word.

    The bot's whole claim is that it quotes the book exactly, so every field
    and progression line is checked against the extraction dump. Comparison
    ignores whitespace only: the PDF's curly-quote glyphs extract with stray
    padding (I: " Thy burdens...) which the index legitimately normalises, but
    a dropped or altered WORD still fails.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    dump = (root / "data" / "raw_text.txt")
    if not dump.exists():
        pytest.skip("raw_text.txt not built")

    squash = lambda s: re.sub(r"\s+", "", s)
    haystack = squash(dump.read_text(encoding="utf-8"))

    idx = RuleIndex.load()
    checked, bad = 0, []
    for e in idx.entries:
        values = list((e.get("fields") or {}).values())
        values += list(e.get("progression") or [])
        for v in values:
            if len(v) <= 12:
                continue
            checked += 1
            if squash(v) not in haystack:
                bad.append((e["name"], v[:70]))

    assert checked > 500, f"expected a substantial sweep, checked {checked}"
    assert not bad, f"{len(bad)} values not verbatim: {bad[:5]}"


@real
def test_classes_carry_overview_only_not_ability_writeups():
    """Class entries must stop at the 'Class Abilities' boundary.

    The per-class pages repeat full write-ups of that class's abilities, which
    are already indexed from the master glossary. Only the overview belongs
    here, so the ability record markers must be absent entirely.
    """
    idx = RuleIndex.load()
    by_name = {e["name"]: e for e in idx.entries}

    for name in ("Anti-Paladin", "Archer", "Assassin", "Barbarian", "Monk",
                 "Paladin", "Scout", "Warrior", "Bard", "Druid", "Healer",
                 "Wizard", "Monster", "Peasant", "Color"):
        assert name in by_name, f"{name} missing"
        assert by_name[name]["category"] == "class"

    # Insult's write-up begins immediately after Warrior's cut point.
    warrior = by_name["Warrior"]
    assert "I command thy attention" not in warrior["text"]
    for marker in ("T: Verbal", "S: Protection", "I enchant thee",
                   "Name Cost Max"):
        for name in ("Warrior", "Wizard", "Archer"):
            assert marker not in by_name[name]["text"], f"{marker} leaked into {name}"

    # Stat block and progression both survive the cut.
    assert warrior["fields"]["Armor"] == "6pts"
    assert warrior["fields"]["Shields"] == "Large"
    assert any(line.startswith("1st") for line in warrior["progression"])
    # A bare class name resolves to the class, not the ladder award.
    assert idx.search("warrior").entry["category"] == "class"
    assert idx.search("order of the warrior").entry["category"] == "award"
