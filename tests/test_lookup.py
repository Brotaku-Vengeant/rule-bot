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
        # Rulebook entries only; Dor Un Avathar entries come from a different
        # source document and are checked separately below.
        if "Spongy" not in (e.get("source") or ""):
            continue
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


# --- caster spell purchase data ---

@real
def test_caster_purchase_data_is_attached_to_abilities():
    """Cost/max/frequency belongs on the spell, not buried in a class table."""
    idx = RuleIndex.load()
    by_name = {e["name"]: e for e in idx.entries}

    priced = [e for e in idx.entries if e.get("purchase")]
    assert len(priced) > 120, f"only {len(priced)} spells carry purchase data"
    assert all(e["category"] == "ability" for e in priced)

    force_bolt = by_name["Force Bolt"]
    rows = {r["class"]: r for r in force_bolt["purchase"]}
    assert rows["Wizard"]["level"] == 1
    assert rows["Wizard"]["cost"] == "1"
    assert rows["Wizard"]["max"] == "8"
    assert rows["Wizard"]["frequency"] == "3 Balls / Unlimited"
    assert "Purchase:" in force_bolt["text"]

    # Long names must not bleed into the Cost column, nor wrapped
    # frequencies into Type - the two failure modes of x-position parsing.
    for e in priced:
        for r in e["purchase"]:
            assert r["cost"] and len(r["cost"]) < 6, (e["name"], r)
            assert r["level"] in range(1, 7), (e["name"], r)

    # A spell can be purchasable twice by one class at different levels.
    bard_armor = by_name["Equipment: Armor, 1 Point"]["purchase"]
    assert sorted(r["level"] for r in bard_armor if r["class"] == "Bard") == [2, 6]

    # Non-casters gain abilities by level, so they never appear as a purchase.
    assert {r["class"] for e in priced for r in e["purchase"]} == {
        "Bard", "Druid", "Healer", "Wizard"}


@real
def test_spell_table_cross_check_discriminates():
    """The build-time check must catch real errors, not just pass everything."""
    from scripts.build_index import cross_check_spell_tables

    entries = [{"name": "Raise Dead",
                "fields": {"Type": "Verbal", "School": "Death", "Range": "Touch"}},
               {"name": "Harden Armor",
                "fields": {"Type": "Enchantment", "School": "Protection",
                           "Range": "Self (Wa), Other (Dr)"}}]

    # Other/Touch are the same reach per the rulebook's own definitions, and
    # a per-class range must still match - neither is an error.
    assert not cross_check_spell_tables(entries, [
        {"Name": "Raise Dead", "Type": "Verbal", "School": "Death", "Range": "Other"}])
    assert not cross_check_spell_tables(entries, [
        {"Name": "Harden Armor", "Type": "Enchantment",
         "School": "Protection", "Range": "Other"}])

    # A genuine disagreement, and an unknown spell, both fail.
    assert cross_check_spell_tables(entries, [
        {"Name": "Raise Dead", "Type": "Magic Ball", "School": "Death", "Range": "Touch"}])
    assert cross_check_spell_tables(entries, [
        {"Name": "No Such Spell", "Type": "Verbal", "School": "Death", "Range": "Self"}])


# --- Dor Un Avathar (second source book) ---

@real
def test_dua_entries_are_verbatim_from_the_google_doc():
    """Same fidelity rule as the rulebook, against the monster book's source.

    The Dor Un Avathar is a Google Doc rather than a PDF, so the snapshot is
    the HTML export. Tags are stripped and whitespace ignored; a dropped or
    altered WORD still fails.
    """
    import html as htmlmod
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    snapshot = root / "rulebook" / "dua11.html"
    if not snapshot.exists():
        pytest.skip("dua11.html snapshot not present")

    from scripts.build_dua import REPLACEMENTS

    raw = re.sub(r"data:image/[^\"']+", "IMG",
                 snapshot.read_text(encoding="utf-8", errors="replace"))
    text = htmlmod.unescape(re.sub(r"<[^>]+>", " ", raw))
    # The build maps the doc's typographic characters to ASCII (curly quotes,
    # en dashes, ligatures); apply the same map here so the comparison ignores
    # that documented substitution while still catching an altered WORD.
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    squash = lambda s: re.sub(r"\s+", "", s)
    haystack = squash(text)

    idx = RuleIndex.load()
    dua = [e for e in idx.entries if e.get("source") == "Dor Un Avathar XI"]
    assert len(dua) > 80, f"only {len(dua)} DUA entries loaded"

    checked, bad = 0, []
    for e in dua:
        # Every stat-block value, plus the leading description paragraph -
        # the parts of an entry that are quoted rather than composed.
        values = list((e.get("fields") or {}).values())
        first_line = (e.get("text") or "").splitlines()[0] if e.get("text") else ""
        if not first_line.startswith("**"):
            values.append(first_line)
        for v in values:
            if len(v) <= 12:
                continue
            checked += 1
            if squash(v) not in haystack:
                bad.append((e["name"], v[:70]))
    assert checked > 190, f"only {checked} values checked"
    assert not bad, f"{len(bad)} altered value(s): {bad[:5]}"


@real
def test_both_books_load_and_cite_themselves():
    idx = RuleIndex.load()
    by_name = {e["name"]: e for e in idx.entries}

    assert by_name["Brutal Strike"]["source"].startswith("Amtgard")
    assert by_name["Beastfolk"]["source"] == "Dor Un Avathar XI"

    # Monsters have no printed page; the rulebook does.
    assert by_name["Brutal Strike"].get("page")
    assert not by_name["Beastfolk"].get("page")

    # Every entry knows which book it came from.
    assert all(e.get("source") for e in idx.entries)

    # Tier coverage, and the abilities monsters reference.
    monsters = [e for e in idx.entries if e["category"] == "monster"]
    assert len(monsters) == 54
    assert len({e["section"] for e in monsters}) == 4
    assert by_name["Strong"]["category"] == "monster ability"


@real
def test_no_monster_is_missing_its_stat_block():
    """Completeness, not just fidelity.

    Checking that stored values are verbatim cannot catch a parser that
    silently DROPS a row -- which is how 45 of 54 monsters once lost their
    Abilities while every remaining value still passed the verbatim sweep.
    Every monster must carry the four core stats and its ability list.
    """
    idx = RuleIndex.load()
    monsters = [e for e in idx.entries if e["category"] == "monster"]
    assert len(monsters) == 54

    required = ("Garb", "Armor", "Shields", "Weapons", "Abilities")
    missing = [(e["name"], f) for e in monsters for f in required
               if not (e.get("fields") or {}).get(f)]
    assert not missing, f"{len(missing)} monster field(s) missing: {missing[:8]}"

    # Ability lists are the substantive part; they must not be empty stubs.
    thin = [e["name"] for e in monsters if len(e["fields"]["Abilities"]) < 8]
    assert not thin, f"suspiciously short ability lists: {thin}"

    # Homebrew Notes exist on many monsters and were dropped wholesale once.
    with_notes = [e for e in monsters if e["fields"].get("Homebrew Note")]
    assert len(with_notes) > 15, f"only {len(with_notes)} homebrew notes"

    dragon = next(e for e in monsters if e["name"] == "Dragon")
    for ability in ("Flying (T)", "Monstrous Resistance (3)",
                    "Fireball 10 Balls / Unlimited (m)", "Throw 2/Life"):
        assert ability in dragon["fields"]["Abilities"], ability
    assert "underwater dragon" in dragon["fields"]["Homebrew Note"]


# --- list commands ---

def test_states_command_lists_the_category_not_one_entry():
    toy = RuleIndex(TOY + [
        {"name": "Custom States", "aliases": [], "category": "scenario mechanic",
         "section": "Scenario Mechanics", "text": "Game-specific states."},
    ])
    r = toy.search("States")
    assert r.kind == "list"
    assert [e["name"] for e in r.suggestions] == ["Insubstantial"]
    assert [e["name"] for e in r.related] == ["Custom States"]
    # Case and punctuation insensitive, like every other lookup.
    assert toy.search("  STATES! ").kind == "list"
    # The individual state is still an ordinary lookup.
    assert toy.search("insubstantial").kind == "exact"


@real
def test_states_command_against_the_real_index():
    from bot.formatting import render

    idx = RuleIndex.load()
    r = idx.search("states")
    names = [e["name"] for e in r.suggestions]
    assert r.kind == "list"
    assert names == sorted(["Cursed", "Fragile", "Frozen", "Insubstantial",
                            "Invulnerable", "Stopped", "Stunned", "Suppressed"])

    embed = render(r, idx.rulebook)
    assert embed.title == "States (8)"
    for n in names:
        assert n in embed.description
    assert "Custom States" in embed.description          # see-also kept
    assert "States Defined, p.31" in embed.footer.text   # shared citation


# --- Declarations Made Easy ---

@real
def test_upon_engagement_lists_what_must_be_declared():
    idx = RuleIndex.load()
    r = idx.search("Upon Enagement")          # misspelled, as it gets typed
    assert r.entry["name"] == "Upon Engagement"
    assert [(i["level"], i["text"]) for i in r.entry["declares"]] == [
        (0, "Dead or Invulnerable"),
        (0, "Posting or Hobbling"),
        (0, "Special Effects on melee attacks"),
        (0, "Enchantments or a summary of their effects"),
        (1, "Whether the enchantment is Persistent"),
        (1, "Choices made at the time of casting, such as weapons or shields for Harden"),
        (1, "You are not required to share the number of uses remaining on multi-use enchantments"),
        (0, "Presence of Magic Armor"),
    ]
    assert "Declared Upon Engagement" in r.entry["text"]


@real
def test_all_three_declaration_columns_are_attached():
    idx = RuleIndex.load()
    by = {e["name"]: e for e in idx.entries}
    request = [(i["level"], i["text"]) for i in by["Upon Request"]["declares"]]
    assert request[0] == (0, "Your name, class, and which team you are on")
    assert (1, "Which Monster you're playing or Archetype you have purchased") in request
    interaction = [(i["level"], i["text"]) for i in by["Upon Interaction"]["declares"]]
    assert interaction[0] == (0, "Calling dead or alive")
    assert (1, "hand on weapon") in interaction

    r = idx.search("declarations")
    assert r.kind == "list"
    assert [e["name"] for e in r.suggestions] == [
        "Upon Engagement", "Upon Interaction", "Upon Request"]


@real
def test_schools_and_special_effects_list_commands():
    from bot.formatting import render

    idx = RuleIndex.load()

    schools = idx.search("schools")
    assert schools.kind == "list"
    assert [e["name"] for e in schools.suggestions] == [
        "Command", "Death", "Flame", "Neutral",
        "Protection", "Sorcery", "Spirit", "Subdual"]
    assert [e["name"] for e in schools.related] == ["School"]
    # The heading is printed on two lines; the citation must carry all of it.
    assert "Magic and Ability Mechanics Defined, p.30" in render(schools, idx.rulebook).footer.text
    # The singular still reaches the definition the plural used to.
    assert idx.search("school").entry["name"] == "School"

    effects = idx.search("Special Effects")
    assert effects.kind == "list"
    assert [e["name"] for e in effects.suggestions] == [
        "Armor Breaking", "Armor Destroying", "Phasing", "Shield Crushing",
        "Shield Destroying", "Siege", "Weapon Destroying", "Wounds Kill"]
    assert idx.search("special effect").kind == "list"

    embed = render(effects, idx.rulebook)
    assert embed.title == "Special Effects (8)"
    assert "Special Effects Defined, p.32" in embed.footer.text


@real
def test_classes_list_command():
    from bot.formatting import render

    idx = RuleIndex.load()
    r = idx.search("classes")
    assert r.kind == "list"
    assert [e["name"] for e in r.suggestions] == [
        "Anti-Paladin", "Archer", "Assassin", "Barbarian", "Bard", "Color",
        "Druid", "Healer", "Monk", "Monster", "Paladin", "Peasant", "Scout",
        "Warrior", "Wizard"]
    assert [e["name"] for e in r.related] == ["Credits and Levels", "Portraying A Class"]
    # The singular was ambiguous before; it now lists the classes too.
    assert idx.search("class").kind == "list"
    # A single class is still an ordinary lookup.
    assert idx.search("warrior").entry["category"] == "class"

    embed = render(r, idx.rulebook)
    assert embed.title == "Classes (15)"
    # Entries on many pages of one section cite the span.
    assert embed.footer.text.endswith("Classes, pp.35-58")


@real
def test_monsters_list_command_groups_by_tier():
    from bot.formatting import embed_cost, render

    idx = RuleIndex.load()
    r = idx.search("monsters")
    assert r.kind == "list"
    assert len(r.suggestions) == 54
    assert [e["name"] for e in r.related] == ["Monster"]
    # The singular is still the rulebook's Monster class.
    single = idx.search("monster").entry
    assert single["name"] == "Monster" and single["category"] == "class"

    # Tiers in book order, names alphabetical within each tier.
    tiers = []
    for e in r.suggestions:
        if not tiers or tiers[-1][0] != e["section"]:
            tiers.append((e["section"], []))
        tiers[-1][1].append(e["name"])
    assert [t for t, _ in tiers] == [
        "Standard Monsters (Tier 1)", "Advanced Monsters (Tier 2)",
        "Scenario Monsters (Tier 3)", "Legendary Monsters (Tier 4)"]
    assert [len(n) for _, n in tiers] == [8, 30, 12, 4]
    assert all(n == sorted(n) for _, n in tiers)
    assert tiers[3][1] == ["Dragon", "Hydra/Kraken", "Lich", "Phoenix"]

    embed = render(r, idx.rulebook)
    assert embed.title == "Monsters (54)"
    d = embed.description
    assert d.index("Standard Monsters (Tier 1)") < d.index("Advanced Monsters (Tier 2)") \
        < d.index("Scenario Monsters (Tier 3)") < d.index("Legendary Monsters (Tier 4)")
    assert embed.footer.text == "Dor Un Avathar XI"
    assert embed_cost(embed) <= 6000


@real
def test_single_section_lists_are_not_grouped():
    from bot.formatting import render

    idx = RuleIndex.load()
    for q in ("states", "schools", "special effects", "declarations", "classes"):
        assert "__" not in render(idx.search(q), idx.rulebook).description, q


@real
def test_magic_items_list_groups_by_type():
    from bot.formatting import render

    idx = RuleIndex.load()
    r = idx.search("magic items")
    assert r.kind == "list"
    assert len(r.suggestions) == 29

    groups = []
    for e in r.suggestions:
        if not groups or groups[-1][0] != e["section"]:
            groups.append((e["section"], []))
        groups[-1][1].append(e["name"])
    # The book's own three types, in book order, A-Z within each.
    assert [g for g, _ in groups] == ["Trinkets", "Talismans", "Artifacts"]
    assert [len(n) for _, n in groups] == [10, 10, 9]
    assert all(n == sorted(n) for _, n in groups)

    # "Relics" is the book's synonym for Magic Items, not a fourth type.
    for alias in ("magic item", "relics", "Magic Items"):
        assert [e["name"] for e in idx.search(alias).suggestions] == \
               [e["name"] for e in r.suggestions], alias

    embed = render(r, idx.rulebook)
    assert embed.title == "Magic Items (29)"
    d = embed.description
    assert d.index("__Trinkets__") < d.index("__Talismans__") < d.index("__Artifacts__")
    assert embed.footer.text == 'Amtgard v8.08 "Spongy", pp.76-78'


@real
def test_armor_list_groups_modifiers_before_types():
    from bot.formatting import render

    idx = RuleIndex.load()
    r = idx.search("armor")
    assert r.kind == "list"
    assert len(r.suggestions) == 15

    groups = []
    for e in r.suggestions:
        if not groups or groups[-1][0] != e["section"]:
            groups.append((e["section"], []))
        groups[-1][1].append(e["name"])
    # Book order by printed page: modifiers (p.11) precede types (p.12).
    assert [g for g, _ in groups] == ["Armor Rating and Safety", "Armor Types"]
    assert [len(n) for _, n in groups] == [4, 11]
    assert groups[0][1] == ["Appearance", "Construction", "Helm Bonus", "Layered Armor"]
    assert "Chainmail" in groups[1][1]

    assert [e["name"] for e in r.related] == [
        "Armor Combat Rules", "Armor Types and Modifiers", "Magic Armor"]
    for alias in ("armors", "armour"):
        assert idx.search(alias).kind == "list", alias

    # Entries that used to be offered for [[armor]] are still their own lookups.
    assert idx.search("magic armor").entry["category"] == "mechanic"
    assert idx.search("armor breaking").entry["category"] == "special effect"

    embed = render(r, idx.rulebook)
    assert embed.title == "Armor (15)"
    assert embed.footer.text == 'Amtgard v8.08 "Spongy", pp.11-12'


@real
def test_weapons_list_command():
    from bot.formatting import render

    idx = RuleIndex.load()
    r = idx.search("weapons")
    assert r.kind == "list"
    assert len(r.suggestions) == 13

    groups = []
    for e in r.suggestions:
        if not groups or groups[-1][0] != e["section"]:
            groups.append((e["section"], []))
        groups[-1][1].append(e["name"])
    assert [g for g, _ in groups] == ["Melee Weapon Types", "Projectiles"]
    assert [len(n) for _, n in groups] == [7, 6]
    assert all(n == sorted(n) for _, n in groups)
    assert "Dagger" in groups[0][1] and "Javelins" in groups[1][1]

    assert [e["name"] for e in r.related] == [
        "Heavy Padding Substitution", "Weapon Safety", "Shields",
        "Bows", "Siege Weapons", "Arrows"]
    assert idx.search("weapon").kind == "list"

    # Construction terms and safety rules stay their own lookups, not weapons.
    assert idx.search("strike-legal").entry["category"] == "weapon term"
    assert idx.search("the ring rule").entry["category"] == "weapon rule"
    assert idx.search("dagger").entry["category"] == "weapon"

    # Printed in the Melee Weapon Types section, but it is an allowance for
    # building a weapon rather than a weapon, so it is not listed as one.
    hps = idx.search("heavy padding substitution").entry
    assert hps["category"] == "weapon modifier"
    assert hps["section"] == "Melee Weapon Types"      # cited where it prints
    assert "Heavy Padding Substitution" not in [e["name"] for e in r.suggestions]

    embed = render(r, idx.rulebook)
    assert embed.title == "Weapons (13)"
    assert embed.footer.text == 'Amtgard v8.08 "Spongy", pp.14-16'


@real
def test_see_also_names_the_book_only_when_it_differs():
    from bot.formatting import render

    idx = RuleIndex.load()
    # Same book: no source in parentheses.
    weapons = render(idx.search("weapons"), idx.rulebook).description
    assert "**Weapon Safety**," in weapons
    assert 'Weapon Safety** (Amtgard' not in weapons
    # Different book: the source is kept.
    states = render(idx.search("states"), idx.rulebook).description
    assert "**Custom States** (Dor Un Avathar XI)" in states
