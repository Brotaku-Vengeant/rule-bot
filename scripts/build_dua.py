"""Build the monster index from the Dor Un Avathar (Google Doc).

The second source book. Unlike the rulebook PDF -- where structure comes from
font -- this is a Google Doc, so the HTML export supplies the structure
instead, and it is a stronger signal than font: each monster is an <h2>/<h3>
heading immediately followed by a <table> stat block whose cells are already
label/value pairs. The plain-text export flattens those tables inconsistently
(one monster's name gains a leading tab, the next does not), so HTML is used.

Entries carry their own "source", because the bot now answers from two books
with different citation styles: the rulebook cites a printed page, the Dor Un
Avathar has none and cites its section instead.

Usage:
    python scripts/build_dua.py --fetch     # re-download, then build
    python scripts/build_dua.py             # build from the local snapshot
    python scripts/build_dua.py --report    # list every entry found
"""

from __future__ import annotations

import argparse
import hashlib
import html as htmlmod
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "dua.json"
SNAPSHOT = REPO_ROOT / "rulebook" / "dua11.html"

DOC_ID = "1xP77PZcGFT9DUwipJQ4EEAl_770c_EicFRAYplgUN_s"
EXPORT_URL = f"https://docs.google.com/document/d/{DOC_ID}/export?format=html"
SOURCE_NAME = "Dor Un Avathar XI"

# Sections to index, mapped to the category their entries take. Anything not
# listed is skipped: the blank homebrew card templates, the prose design
# guidelines, and the scenario write-ups are planning material, not lookups.
SECTION_CATEGORY = {
    "Standard Monsters (Tier 1)": "monster",
    "Advanced Monsters (Tier 2)": "monster",
    "Scenario Monsters (Tier 3)": "monster",
    "Legendary Monsters (Tier 4)": "monster",
    "Monster Abilities": "monster ability",
    "Monster Mechanics": "monster mechanic",
    "Terrain Types": "terrain",
    "Scenario Mechanics": "scenario mechanic",
}

# Stat-block rows whose label is a real field. "Frequency" is the header of the
# abilities column, not a field of its own.
STAT_LABELS = {"Garb", "Armor", "Shields", "Weapons", "Abilities",
               "Note", "Notes", "Homebrew Note", "Homebrew Notes"}

REPLACEMENTS = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...", " ": " ",
}


def clean(text: str) -> str:
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def untag(fragment: str) -> str:
    """Strip tags from an HTML fragment, keeping paragraph breaks as newlines."""
    fragment = re.sub(r"</p>|<br\s*/?>|</li>", "\n", fragment)
    text = htmlmod.unescape(re.sub(r"<[^>]+>", "", fragment))
    return "\n".join(clean(line) for line in text.split("\n") if clean(line))


def fetch(dest: Path) -> None:
    print(f"Fetching {EXPORT_URL}")
    with urlopen(EXPORT_URL) as resp:      # noqa: S310 - fixed, known URL
        body = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    print(f"  saved {dest} ({len(body) / 1024:.0f} KB)")


def table_rows(table_html: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, re.S):
        cells = [untag(td) for td in
                 re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)]
        if any(c for c in cells):
            rows.append(cells)
    return rows


def parse_stat_block(table_html: str) -> tuple[dict, str]:
    """Turn one monster's table into {field: value} plus its description.

    Rows come in three shapes: a full-width description row, ordinary
    label/value pairs, and the abilities list, which sits in a full-width row
    UNDER an "Abilities | Frequency" header rather than beside its label.
    """
    fields: dict[str, str] = {}
    description = ""
    expecting_abilities = False

    for cells in table_rows(table_html):
        if len(cells) == 1:
            body = cells[0]
            if body.startswith("Description:"):
                description = clean(body[len("Description:"):])
            elif expecting_abilities:
                fields["Abilities"] = body
                expecting_abilities = False
            continue

        label, value = clean(cells[0]).rstrip(":"), cells[1]
        if label == "Abilities" and clean(value) == "Frequency":
            expecting_abilities = True
        elif label in STAT_LABELS:
            fields[label] = value

    return fields, description


def build(snapshot: Path) -> dict:
    raw = snapshot.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    # Images are base64-inlined and dwarf the markup; dropping them keeps the
    # regex scan fast and cannot affect structure.
    html = re.sub(r"data:image/[^\"']+", "IMG",
                  raw.decode("utf-8", errors="replace"))

    blocks = list(re.finditer(r"<(h[1-6]|table)\b[^>]*>(.*?)</\1>", html, re.S))
    entries: list[dict] = []
    section = None

    for i, block in enumerate(blocks):
        tag, inner = block.group(1), block.group(2)
        text = clean(untag(inner).replace("\n", " "))

        if tag == "h1":
            section = text or section
            continue
        if tag == "table" or not text:
            continue

        # Section names in the doc carry stray spacing ("Legendary Monsters
        # (Tier 4)"), so match on a squashed form.
        key = next((k for k in SECTION_CATEGORY
                    if re.sub(r"\s+", "", k.lower())
                    == re.sub(r"\s+", "", (section or "").lower())), None)
        if key is None:
            continue

        category = SECTION_CATEGORY[key]
        entry = {
            "name": text,
            "slug": slugify(text),
            "category": category,
            "section": key,
            "source": SOURCE_NAME,
            "aliases": [],
        }

        follows_table = (i + 1 < len(blocks)
                         and blocks[i + 1].group(1) == "table")
        if category == "monster":
            if not follows_table:
                continue          # a heading with no stat block is not a monster
            fields, description = parse_stat_block(blocks[i + 1].group(2))
            if not fields:
                continue
            entry["fields"] = fields
            entry["text"] = "\n".join(
                [description] +
                [f"**{k}:** {v}" for k, v in fields.items() if k != "Abilities"] +
                ([f"**Abilities:**\n{fields['Abilities']}"]
                 if fields.get("Abilities") else [])
            ).strip()
        else:
            # Reference definitions: prose paragraphs up to the next heading.
            end = blocks[i + 1].start() if i + 1 < len(blocks) else len(html)
            body = untag(html[block.end():end])
            if not body:
                continue
            entry["text"] = body

        if entry.get("text"):
            entries.append(entry)

    # A later duplicate would shadow an earlier one; qualify it by section
    # rather than dropping it.
    seen: set[str] = set()
    for e in entries:
        if e["name"] in seen:
            e["name"] = f"{e['name']} ({e['section']})"
            e["slug"] = slugify(e["name"])
        seen.add(e["name"])

    return {
        "rulebook": SOURCE_NAME,
        "source_doc": f"Google Doc {DOC_ID}",
        "snapshot_sha256": digest,
        "built": date.today().isoformat(),
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true",
                    help="Re-download the doc before building.")
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    if args.fetch or not args.snapshot.exists():
        fetch(args.snapshot)
    if not args.snapshot.exists():
        sys.exit(f"No snapshot at {args.snapshot}; run with --fetch")

    index = build(args.snapshot)
    entries = index["entries"]

    by_cat: dict[str, list[str]] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e["name"])

    print(f"{index['rulebook']}  ->  {len(entries)} entries "
          f"(snapshot {index['snapshot_sha256']})")
    for cat in sorted(by_cat):
        print(f"  {cat:<18} {len(by_cat[cat])}")

    short = [e["name"] for e in entries if len(e["text"]) < 25]
    if short:
        print(f"  suspiciously short: {short}")

    if args.report:
        for cat in sorted(by_cat):
            print(f"\n--- {cat} ({len(by_cat[cat])}) ---")
            for n in by_cat[cat]:
                print(f"  {n}")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nWrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
