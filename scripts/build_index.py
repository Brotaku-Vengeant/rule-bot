"""Build the searchable rules index from the Amtgard v8.7 rulebook PDF.

Stage 2 of the pipeline. Structure is detected from *font*, not regex: the
rulebook sets every term name in MinionPro-Bold at a size that differs from body
text, so headings are identified exactly rather than guessed. This matters
because definition bodies contain lines like "Example: A player is enchanted..."
that a naive `^Word:` pattern would wrongly split into a new entry.

Font roles (confirmed by probing the PDF):
    MinionPro-Bold   @11  ability name        ("Blessed Aura")
    MinionPro-Bold   @10  term / state name   ("Insubstantial:")
    MinionPro-Bold    @9  ability field label ("T:", "S:", "E:")
    MinionPro-Capt    @9  body text
    TrajanPro-Bold   @14  subsection header   ("States Defined")
    TrajanPro-Regular@10  printed page folio  (used for citations)

Usage:
    python scripts/build_index.py                    # write data/rules.json
    python scripts/build_index.py --report           # list every term found
    python scripts/build_index.py --query "brutal"   # test lookup, no Discord
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber missing. Run: python -m pip install -r requirements.txt")

from scripts.extract_pdf import (DEFAULT_PDF, REPLACEMENTS, find_gutter,
                                 keep_obj, ordered_rows, region_rows,
                                 tolerant_gutter)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "rules.json"

RULEBOOK_VERSION = 'Amtgard v8.08 "Spongy"'
PAGE_OFFSET = 2  # printed folio = PDF page - 2 (v8.08; verified against folios on p25/p62)

# Ability records use single-letter field labels. Expanded for display.
FIELD_NAMES = {
    "T": "Type", "S": "School", "R": "Range", "I": "Incantation",
    "M": "Material", "E": "Effect", "L": "Limitations", "N": "Notes",
    "U": "Use",   # magic items open with "Use:" instead of "T:"/"S:"
}

# Bold lines that are layout furniture, not real entries.
NOT_ENTRIES = {
    "abilities format key", "class abilities", "states defined",
    "special effects defined", "magic and ability mechanics defined",
    # Table column headers and a heading too generic to look up on its own.
    "tier maximum points armor types", "material minimum standard superior",
    "tier maximum points", "armor types", "general", "general note",
}

# Keyed by squashed (space-free, lowercase) subsection name - display headers
# are letter-spaced and can split mid-word, so lookups ignore spacing entirely.
SUBSECTION_CATEGORY = {
    "magicandabilitymechanicsdefined": "mechanic",
    "statesdefined": "state",
    "specialeffectsdefined": "special effect",
    # New subsection in v8.08 "Spongy" (Upon Engagement, Upon Request, ...).
    "declarations": "declaration",
    "declarationsdefined": "declaration",
    # Magic item tiers (v8.08 printed pp.76-78).
    "trinkets": "trinket",
    "talismans": "talisman",
    "artifacts": "artifact",
    # Equipment chapters (printed pp.12-16).
    "shields": "shield",
    "projectiles": "projectile",
}

SECTIONS = [
    {
        "label": "Magic, Abilities, States and Special Effects",
        "pages": range(28, 36),      # PDF pages; printed 26-33
        "heading_size": 10.0,
        "default_category": "mechanic",
        "kind": "term",
    },
    {
        "label": "Magic and Abilities",
        "pages": range(62, 78),      # PDF pages; printed 60-75
        "heading_size": 11.0,
        "default_category": "ability",
        "kind": "ability",
    },
    {
        "label": "Armor Types",
        "pages": range(14, 15),      # PDF page; printed 12
        "heading_size": 11.0,
        "default_category": "armor",
        "kind": "term",
        "fixed_section": True,
        "crop_to_first_heading": True,
        "tolerant_columns": True,
    },
    {
        "label": "Weapon Definitions",
        "fixed_section": True,
        "pages": range(15, 16),      # PDF page; printed 13
        "heading_size": 10.0,
        "default_category": "weapon term",
        "kind": "term",
    },
    {
        "label": "Melee Weapon Types",
        "fixed_section": True,
        "pages": range(16, 18),      # PDF pages; printed 14-15 (incl. Shields)
        "heading_size": 10.0,
        "default_category": "weapon",
        "kind": "term",
    },
    {
        "label": "Projectiles",
        "fixed_section": True,
        "pages": range(18, 19),      # PDF page; printed 16
        "heading_size": 10.0,
        "default_category": "projectile",
        "kind": "term",
    },
    {
        # Armor modifiers, set as bold 9pt run-in headings.
        "label": "Armor Rating and Safety",
        "fixed_section": True,
        "pages": range(13, 14),      # PDF page; printed 11
        "heading_size": 9.0,
        "default_category": "armor modifier",
        "kind": "term",
    },
    {
        # Weapon safety rules, bold 9pt run-in headings.
        "label": "Weapon Safety",
        "fixed_section": True,
        "pages": range(15, 16),      # PDF page; printed 13
        "heading_size": 9.0,
        "default_category": "weapon rule",
        "kind": "term",
    },
    {
        # Arrow component definitions, bold 9pt run-in headings.
        "label": "Arrows",
        "fixed_section": True,
        "pages": range(19, 20),      # PDF page; printed 17
        "heading_size": 9.0,
        "default_category": "arrow",
        "kind": "term",
    },
    {
        # Section-level rulesets that carry substantive numbered rules rather
        # than run-in headings - Shields (small/medium/large sizes), Arrows,
        # Bows, Siege Weapons. Keyed off the 14pt Trajan subsection headers.
        "label": "Equipment Rules",
        "pages": range(12, 21),      # PDF pages; printed 10-18
        "heading_size": 14.0,
        "default_category": "equipment rule",
        "kind": "chapter",
    },
    {
        # Appendix A: Knighthood, Masterhood and the nine Ladder Awards.
        # Definitions are numbered ("3. Lion: Awarded for..."), so heading
        # detection has to look past the non-bold list marker.
        "label": "Award Standards",
        "fixed_section": True,
        "pages": range(82, 85),      # PDF pages; printed 80-82
        "heading_size": 9.0,
        "default_category": "award",
        "kind": "term",
        "numbered_headings": True,
        "tolerant_columns": True,
    },
    {
        # Potions (Trinkets), Talismans/Scrolls, and Artifacts share the
        # ability record format, with "Use:" in place of "T:"/"S:".
        "label": "Magic Items",
        "pages": range(78, 81),      # PDF pages; printed 76-78
        "heading_size": 11.0,
        "default_category": "magic item",
        "kind": "ability",
    },
]

# Nested sub-definition handling (all on printed p.30 in v8.08):
# School's children are the eight magic schools; Resistant's are its three
# resistance cases, whose bare names are too generic to stand alone.
# Leading list markers that precede a bold run-in heading: "3.", "a.", "IV."
LIST_MARKER_RE = re.compile(r"[0-9]{1,2}\.|[a-z]\.|[IVXivx]{1,5}\.")

# The nine Ladder Awards (printed pp.80-82), stored as "Order of the <name>".
# Fixed by the Circle of Monarchs: per Appendix A itself, this list can only
# change by ninety percent approval of all kingdoms, so it is safe to pin.
LADDER_AWARDS = {"Rose", "Smith", "Lion", "Crown", "Owl",
                 "Dragon", "Garber", "Warrior", "Battle"}

QUALIFY_CHILDREN = {"Resistant"}
CHILD_CATEGORY = {"School": "school"}

# Verbatim repairs for extraction artifacts that no ordering heuristic can fix.
# Each is (entry name, wrong text, correct text per the printed book); the
# build FAILS if a pattern stops matching, forcing a review on the next
# rulebook revision instead of silently keeping a stale patch.
PATCHES = [
    # v8.08, printed p.29: the 'a' of "strike a player" is a vertically
    # displaced glyph in the PDF; it extracts out of reading order and lands
    # at the end of the entry.
    ("Magic Balls", "which are thrown and strike player or object",
     "which are thrown and strike a player or object"),
    ("Magic Balls", "Song of Deflection, or similar abilities. a",
     "Song of Deflection, or similar abilities."),
]

# Shorthand the club actually says out loud, mapped to canonical entry names.
EXTRA_ALIASES = {
    # v8.08 folded the Charge Incantation definition into the Charge mechanic.
    "Charge": ["charge incantation"],
    "Coup de Grace": ["coup", "cdg"],
    "Sphere of Annihilation": ["sphere", "soa"],
    "Lightning Bolt": ["lb"],
    "Shake It Off": ["sio"],
    "Dispel Magic": ["dispel"],
    "Protection from Magic": ["pfm"],
    "Blessing Against Wounds": ["baw"],
    "Blessing Against Harm": ["bah"],
}


def clean(text: str) -> str:
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def is_bold(w, size: float, tol: float = 0.6) -> bool:
    return "MinionPro-Bold" in w["fontname"] and abs(w["size"] - size) < tol


def squash(text: str) -> str:
    """Lowercase with all spaces removed.

    Display headers are letter-spaced, and pdfplumber sometimes splits them
    mid-word ("Declar ations"), so subsection names are compared space-free.
    """
    return re.sub(r"\s+", "", text.lower())


# Canonical subsection names, keyed by their squashed form, so a split header
# still stores a readable section name in the index.
CANONICAL_SUBSECTIONS = {
    squash(n): n for n in (
        "Magic and Ability Mechanics Defined",
        "States Defined",
        "Special Effects Defined",
        "Declarations",
        "Magic and Abilities",
        "Trinkets",
        "Talismans",
        "Artifacts",
        "Weapon Definitions",
        "Armor Types and Values",
        "Armor Combat Rules",
        "Armor Rating and Safety",
        "Armor Types and Modifiers",
        "General Note",
        "Weapon Safety",
        "Melee",
        "Arrows",
        "Bows",
        "Siege Weapons",
        "Construction Requirements",
        "Shields",
        "Projectiles",
        "Magic Item Rules",
        "Battlegaming With Magic Items",
        "Creating New Magic Items",
    )
}


def crop_above_made_easy(page):
    """Drop everything from the '<X> Made Easy' box downward.

    Made Easy boxes are the rulebook's own summaries, explicitly not the rules
    text, and their multi-column tables also defeat two-column gutter
    detection, interleaving the definitions above them. Cropping at the box
    header fixes both.
    """
    words = page.extract_words(extra_attrs=["fontname", "size"])
    headers = [w for w in words
               if "TrajanPro-Bold" in w["fontname"] and w["size"] > 12]
    if not headers:
        return page
    # Group header words into rows, look for one ending in "made easy".
    rows: dict[int, list] = {}
    for w in headers:
        rows.setdefault(round(w["bottom"] / 4.0), []).append(w)
    cuts = [min(w["top"] for w in row) for row in rows.values()
            if squash(" ".join(x["text"] for x in row)).endswith("madeeasy")]
    if not cuts:
        return page
    cut = min(cuts)
    if cut <= page.bbox[1] + 5:   # box is the whole page; nothing above it
        return page
    return page.crop((page.bbox[0], page.bbox[1], page.bbox[2], cut - 2))


def strip_hanging_marker(row, min_gap: float = 15.0):
    """Drop a neighbouring column's hanging list marker from the row end.

    Numbered lists hang their markers outside the text block, so the right
    column's "8." can sit a few points LEFT of the whitespace gutter and get
    clustered into the left column's row - landing mid-sentence ("beyond the
    call 8. of duty"). A real trailing word follows normal spacing; these sit
    behind a 20-45pt gap, which distinguishes them safely.
    """
    if len(row) < 2:
        return row
    last, prev = row[-1], row[-2]
    if (LIST_MARKER_RE.fullmatch(last["text"])
            and last["x0"] - prev["x1"] >= min_gap):
        return row[:-1]
    return row


def page_lines(page, gutter):
    """Return page words grouped into lines, in reading order.

    Two-column pages are read column by column; grouping is by vertical
    position within each column so that a wrapped definition stays contiguous.
    """
    regions = []
    if gutter is None:
        regions.append(page)
    else:
        top, bottom = page.bbox[1], page.bbox[3]
        regions.append(page.crop((page.bbox[0], top, gutter, bottom)))
        regions.append(page.crop((gutter, top, page.bbox[2], bottom)))

    lines = []
    for region in regions:
        words = region.extract_words(
            extra_attrs=["fontname", "size"], x_tolerance=1.5, y_tolerance=3
        )
        # Cluster on the baseline, not the top edge. An 11pt bold ability name
        # and the 9pt availability text beside it share a baseline but have
        # different top edges, so bucketing by top splits "Brutal Strike" from
        # "Ap 4, Bn 5" onto separate lines.
        row: list = []
        for w in sorted(words, key=lambda w: (w["bottom"], w["x0"])):
            if row and abs(w["bottom"] - row[0]["bottom"]) > 3.0:
                lines.append(sorted(row, key=lambda w: w["x0"]))
                row = []
            row.append(w)
        if row:
            lines.append(sorted(row, key=lambda w: w["x0"]))
    return lines


def join_body(parts: list[str]) -> str:
    """Join wrapped lines, repairing hyphenation and spacing.

    A line-wrap hyphen before a lowercase letter is a soft break ("re-/declare"
    -> "redeclare"); before an uppercase letter it is a hard hyphen in a
    compound word ("Meta-/Magic" -> "Meta-Magic"), so the hyphen stays.
    """
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not out:
            out = part
        elif out.endswith("-") and not out.endswith(" -") and part[:1].islower():
            out = out[:-1] + part
        elif out.endswith("-") and not out.endswith(" -") and part[:1].isupper():
            out = out + part
        else:
            out += " " + part
    return re.sub(r"\s{2,}", " ", out).strip()


FIELD_ORDER = "TSURIMELN"


def label_at(words, i, after: str | None = None) -> tuple[str, int] | None:
    """If words[i] begins a bold field label, return (letter, tokens_consumed).

    A label is usually one token ("E:"), but the book occasionally sets the
    colon in a different font (after an italic incantation, "E" is bold and
    ":" is italic), which splits it into its own token. The colon's font is
    therefore deliberately not checked - a bold single letter immediately
    followed by any ":" token is a label. Without this, the marker leaks into
    the previous field's text and the new field is lost entirely (this bit
    Rage and Poison in v8.08).

    The book is also inconsistent the other way: in ~10 entries (Guardian,
    Phoenix Tears, Berserker, ...) some labels are set entirely in the BODY
    font. A non-bold label token is accepted only under strict conditions -
    a bold label has already been seen in this entry (given via `after`), and
    the letter strictly advances the canonical T-S-R-I-M-E-L-N field order -
    so a literal "E:" inside prose can never split a field.
    """
    w = words[i]
    letter = consumed = None
    if re.fullmatch(r"[TSRIMELN]:", w["text"]):
        letter, consumed = w["text"][0], 1
    elif w["text"] == "Use:":              # magic items' opening label
        letter, consumed = "U", 1
    elif (re.fullmatch(r"[TSRIMELN]", w["text"]) and i + 1 < len(words)
            and words[i + 1]["text"] == ":"):
        letter, consumed = w["text"], 2
    elif (w["text"] == "Use" and i + 1 < len(words)
            and words[i + 1]["text"] == ":"):
        letter, consumed = "U", 2
    if letter is None:
        return None
    if is_bold(w, 9.0):
        return letter, consumed
    if after is not None and FIELD_ORDER.index(letter) > FIELD_ORDER.index(after):
        return letter, consumed
    return None


def join_tokens(tokens: list[str]) -> str:
    """Join word tokens, reattaching standalone quote marks.

    A quote that tokenized as its own word must join without a space on one
    side: an opening quote hugs the following word, a closing quote hugs the
    preceding one. Which is which is tracked by quote parity across the value
    ('" May ... thee " x3'  ->  '"May ... thee" x3'); adjacency alone cannot
    distinguish the two cases.
    """
    out = ""
    open_q = False
    glue_next = False
    for tok in (clean(t) for t in tokens):
        if tok == '"':
            if open_q:
                out += '"'            # closing: hug previous word
            else:
                out += (" " if out else "") + '"'
                glue_next = True      # opening: hug next word
            open_q = not open_q
            continue
        if glue_next:
            out += tok
            glue_next = False
        elif out.endswith("-") and not out.endswith(" -") and tok[:1].islower():
            out = out[:-1] + tok      # soft line-wrap hyphen: "re-/declare"
        elif out.endswith("-") and not out.endswith(" -") and tok[:1].isupper():
            out = out + tok           # hard compound hyphen: "Meta-/Magic"
        else:
            out += (" " if out else "") + tok
        if tok.count('"') % 2:
            open_q = not open_q       # attached quote also flips parity
    out = re.sub(r"\s{2,}", " ", out).strip()
    # A kern gap can split trailing punctuation into its own token
    # ('"sanctuary" .') - reattach it.
    return re.sub(r" ([.,;:!?])(\s|$)", r"\1\2", out)


def parse_ability_fields(words) -> tuple[dict, str]:
    """Split an ability body into its T/S/R/I/M/E/L/N fields.

    Field labels are identified by their bold font, so a literal "E:" inside
    prose cannot be mistaken for a field boundary. Labels also appear several
    to a line ("T: Verbal S: Death R: Unlimited"), which this handles.
    """
    fields: dict[str, list[str]] = {}
    order: list[str] = []
    current = None
    bold_seen = False
    i = 0
    while i < len(words):
        hit = label_at(words, i, current if bold_seen else None)
        if hit:
            bold_seen = bold_seen or is_bold(words[i], 9.0)
            current, consumed = hit
            i += consumed
            if current not in fields:
                fields[current] = []
                order.append(current)
            continue
        if current:
            fields[current].append(words[i]["text"])
        i += 1
    parsed = {k: join_tokens(v) for k, v in fields.items()}
    raw = "\n".join(f"{FIELD_NAMES[k]}: {parsed[k]}" for k in order if parsed.get(k))
    return parsed, raw


def child_entry_name(parent: str, child: str) -> str:
    """Public name for a nested sub-definition.

    School's children (Death, Flame, ...) stand alone; Resistant's children
    (Wounds, School, Source) are too generic bare - "School" would even
    collide with the School entry itself - so they get qualified names.
    """
    if parent in QUALIFY_CHILDREN:
        return f"{parent} ({child})"
    return child


def build(pdf_path: Path) -> dict:
    entries: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        for spec in SECTIONS:
            subsection = None
            current: dict | None = None
            body_lines: list[str] = []
            body_words: list = []
            children: list[tuple[str, int]] = []  # (name, index into body_lines)

            def flush():
                nonlocal current, body_lines, body_words, children
                if not current:
                    return
                if spec["kind"] == "ability":
                    fields, raw = parse_ability_fields(body_words)
                    current["fields"] = {FIELD_NAMES[k]: v for k, v in fields.items()}
                    current["text"] = raw or join_body(body_lines)
                else:
                    # A term name's trailing colon sometimes tokenizes as its
                    # own word and lands at the start of the body - drop it.
                    current["text"] = join_body(body_lines).lstrip(": ").strip()
                if current["text"]:
                    entries.append(current)
                    # Nested sub-definitions (the 8 Schools under School, the
                    # three Resistant cases) also become entries of their own,
                    # so [[flame]] answers directly. The parent keeps the full
                    # text including every child's.
                    bounds = [idx for _, idx in children] + [len(body_lines)]
                    for n, (cname, start) in enumerate(children):
                        ctext = join_body(body_lines[start:bounds[n + 1]])
                        ctext = re.sub(r"^" + re.escape(cname) + r"\s*:\s*", "",
                                       ctext).strip()
                        if not ctext:
                            continue
                        public = child_entry_name(current["name"], cname)
                        entries.append({
                            "name": public,
                            "slug": slugify(public),
                            "category": CHILD_CATEGORY.get(
                                current["name"], current["category"]),
                            "section": current["section"],
                            "page": current["page"],
                            "parent": current["name"],
                            "text": ctext,
                        })
                current, body_lines, body_words, children = None, [], [], []

            for pno in spec["pages"]:
                page = pdf.pages[pno - 1].filter(keep_obj)
                page = crop_above_made_easy(page)
                if spec.get("crop_to_first_heading"):
                    # Printed p.12 opens with prose and a wide materials table
                    # above the armor entries. Both are reference data rather
                    # than definitions, and the table's own bold header would
                    # otherwise register as an entry ("Standard Superior") and
                    # spill its cells into the last real entry.
                    tops = [w["top"] for w in page.extract_words(
                                extra_attrs=["fontname", "size"])
                            if is_bold(w, spec["heading_size"])]
                    if tops and min(tops) > page.bbox[1] + 5:
                        page = page.crop((page.bbox[0], min(tops) - 2,
                                          page.bbox[2], page.bbox[3]))
                folio = pno - PAGE_OFFSET

                if spec.get("tolerant_columns"):
                    # This region is known to be two-column; a lone overhanging
                    # word must not veto the split (see tolerant_gutter).
                    g = tolerant_gutter(page) or find_gutter(page)
                    # Printed p.12 closes with a full-width footnote spanning
                    # both columns. Column cropping would slice it in half and
                    # glue a fragment onto the last entry of each column
                    # (splitting "construction" mid-word), so trim it off.
                    if g is not None:
                        straddlers = [w for w in page.extract_words()
                                      if w["x0"] < g < w["x1"]]
                        if straddlers:
                            cut = min(w["top"] for w in straddlers)
                            if cut > page.bbox[1] + 20:
                                page = page.crop((page.bbox[0], page.bbox[1],
                                                  page.bbox[2], cut - 1))
                    page_rows = region_rows(page, g)
                else:
                    page_rows = ordered_rows(page)

                for row in page_rows:
                    if spec.get("tolerant_columns"):
                        row = strip_hanging_marker(row)
                    first = row[0]

                    # Subsection header, e.g. "States Defined".
                    if "TrajanPro-Bold" in first["fontname"] and first["size"] > 12:
                        flush()
                        if spec["kind"] == "chapter":
                            # These headers ARE the entries: their bodies are
                            # numbered rulesets (shield sizes, arrow and bow
                            # construction) with no run-in headings to key off.
                            raw = clean(" ".join(w["text"] for w in row)).strip()
                            name = CANONICAL_SUBSECTIONS.get(squash(raw), raw)
                            if (abs(first["size"] - spec["heading_size"]) > 1.0
                                    or name.lower() in NOT_ENTRIES
                                    or len(name) < 3):
                                continue
                            current = {
                                "name": name,
                                "slug": slugify(name),
                                "category": spec["default_category"],
                                "section": spec["label"],
                                "page": folio,
                            }
                            body_lines = []
                            continue
                        if spec.get("fixed_section"):
                            # Equipment chapter titles are set across the page
                            # and get sliced by column cropping ("Weapon Types,
                            # Shield" / "ds, and Equipment"), so they are not
                            # usable as labels; the section name is fixed.
                            continue
                        raw_name = clean(" ".join(w["text"] for w in row))
                        subsection = CANONICAL_SUBSECTIONS.get(
                            squash(raw_name), raw_name)
                        continue

                    # Printed folio number - drop it, we already track the page.
                    if ("TrajanPro" in first["fontname"]
                            and "".join(w["text"] for w in row).isdigit()):
                        continue

                    if spec["kind"] == "chapter":
                        # A run-in bold heading ends the chapter body; that
                        # material belongs to its own entry from another
                        # section (e.g. Madu ends the Shields ruleset).
                        if any(is_bold(first, sz) for sz in (9.0, 10.0, 11.0)):
                            flush()
                            continue
                        if current:
                            body_lines.append(
                                clean(" ".join(w["text"] for w in row)))
                        continue

                    # Some sections number their definitions ("3. Lion: Awarded
                    # for..."), and the marker is NOT bold, so skip past a
                    # leading list marker before looking for the bold name.
                    start = 0
                    if spec.get("numbered_headings"):
                        while (start < len(row)
                               and not is_bold(row[start], spec["heading_size"])
                               and LIST_MARKER_RE.fullmatch(row[start]["text"])):
                            start += 1

                    if start < len(row) and is_bold(row[start], spec["heading_size"]):
                        lead = []
                        i = start
                        while i < len(row) and is_bold(row[i], spec["heading_size"]):
                            lead.append(row[i]["text"])
                            i += 1
                        name = clean(" ".join(lead)).rstrip(":").strip()

                        if name.lower() in NOT_ENTRIES or len(name) < 2:
                            # Furniture still ENDS the open entry. Without
                            # this, a skipped table header lets its cells go
                            # on appending to the preceding definition (the
                            # armor-types table ran into "Helm Bonus").
                            flush()
                            continue

                        flush()
                        category = SUBSECTION_CATEGORY.get(
                            squash(subsection or ""), spec["default_category"]
                        )
                        rest = row[i:]
                        current = {
                            "name": name,
                            "slug": slugify(name),
                            "category": category,
                            "section": subsection or spec["label"],
                            "page": folio,
                        }
                        if spec["kind"] == "ability":
                            # Text on the name line before the first field label
                            # is the class/level availability, e.g. "Ap 4, Bn 5".
                            cut = len(rest)
                            for j in range(len(rest)):
                                if label_at(rest, j):
                                    cut = j
                                    break
                            current["availability"] = clean(
                                " ".join(w["text"] for w in rest[:cut])
                            ).strip()
                            body_words = list(rest[cut:])
                        else:
                            body_lines = [clean(" ".join(w["text"] for w in rest))]
                        continue

                    if current:
                        if spec["kind"] == "ability":
                            body_words.extend(row)
                        else:
                            # Nested sub-definition heading: a bold@9 run
                            # opening a row inside a term entry ("Death:",
                            # "Wounds:"). The colon may be its own token or
                            # set in another font ("Protection" + ":").
                            if is_bold(first, 9.0) and not is_bold(first, spec["heading_size"]):
                                lead = []
                                i = 0
                                while i < len(row) and is_bold(row[i], 9.0):
                                    lead.append(row[i]["text"])
                                    i += 1
                                cname = clean(" ".join(lead)).rstrip(":").strip()
                                if 2 <= len(cname) <= 30:
                                    children.append((cname, len(body_lines)))
                            body_lines.append(clean(" ".join(w["text"] for w in row)))

                page.flush_cache()

            flush()

    # The rulebook prints ladder awards as bare run-in headings ("Warrior:")
    # but refers to them throughout as "Order of the Warrior". Storing the bare
    # word would let an award shadow the far more commonly asked-about thing
    # of the same name - the Warrior class, the Crown, a Dragon - so they take
    # the full title the book itself uses. Deliberately NOT aliased back to the
    # bare word, which must stay free for those other meanings.
    for e in entries:
        if e["category"] == "award" and e["name"] in LADDER_AWARDS:
            e["name"] = f"Order of the {e['name']}"
            e["slug"] = slugify(e["name"])

    # Some names legitimately appear in two chapters - "Magic Balls" is both a
    # game mechanic (printed p.29) and an equipment spec (p.16). The first
    # occurrence keeps the bare name; later ones are qualified by section, so
    # both stay reachable instead of one shadowing the other.
    seen_names: set[str] = set()
    for e in entries:
        if e["name"] in seen_names:
            e["name"] = f"{e['name']} ({e['section']})"
            e["slug"] = slugify(e["name"])
        seen_names.add(e["name"])

    for name, wrong, right in PATCHES:
        e = next((x for x in entries if x["name"] == name), None)
        if e is None or wrong not in e["text"]:
            raise ValueError(
                f"PATCHES entry for {name!r} no longer matches - the rulebook "
                f"PDF changed; re-verify and update PATCHES.")
        e["text"] = e["text"].replace(wrong, right)

    for e in entries:
        aliases = set(EXTRA_ALIASES.get(e["name"], []))
        if e.get("parent") == "School":
            aliases.add(f"{e['name'].lower()} school")
        elif e.get("parent") == "Resistant":
            inner = e["name"][e["name"].find("(") + 1:e["name"].rfind(")")]
            aliases.add(f"resistant to {inner.lower()}")
        aliases.discard(e["name"].lower())
        e["aliases"] = sorted(aliases)

    return {
        "rulebook": RULEBOOK_VERSION,
        "source_pdf": pdf_path.name,
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path,
                    default=Path(os.environ.get("RULEBOOK_PDF") or DEFAULT_PDF))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--report", action="store_true", help="List every entry found.")
    ap.add_argument("--query", help="Test a lookup against the built index.")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"Rulebook PDF not found at: {args.pdf}")

    index = build(args.pdf)
    entries = index["entries"]

    by_cat: dict[str, list[str]] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e["name"])

    print(f"{index['rulebook']}  ->  {len(entries)} entries")
    for cat in sorted(by_cat):
        print(f"  {cat:<16} {len(by_cat[cat])}")

    dupes = {n for n in (e["name"] for e in entries)
             if [e["name"] for e in entries].count(n) > 1}
    if dupes:
        print(f"\n  duplicate names: {sorted(dupes)}")

    empty = [e["name"] for e in entries if len(e["text"]) < 15]
    if empty:
        print(f"  suspiciously short: {empty}")

    if args.report:
        for cat in sorted(by_cat):
            print(f"\n--- {cat} ({len(by_cat[cat])}) ---")
            for name in sorted(by_cat[cat]):
                print(f"  {name}")
        return 0

    if args.query:
        from bot.lookup import RuleIndex
        result = RuleIndex(entries).search(args.query)
        print(f"\n--- query: {args.query!r} ---")
        print(result.describe())
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {args.out}  ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
