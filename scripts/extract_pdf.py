"""Extract text from the Amtgard rulebook PDF into an auditable plain-text dump.

This is stage 1 of the pipeline. It deliberately produces a human-readable
intermediate file rather than going straight to structured JSON, so that
extraction problems (column order, ligatures, dropped headings) are visible
and cheap to fix before any parser is written against the output.

Usage:
    python scripts/extract_pdf.py --probe          # diagnostics only, no write
    python scripts/extract_pdf.py                  # full extraction
    python scripts/extract_pdf.py --pages 30-45    # subset, for fast iteration
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit(
        "pdfplumber is not installed.\n"
        "Run:  python -m pip install -r requirements.txt"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = REPO_ROOT / "rulebook" / "amtgard-spongey.pdf"
DEFAULT_OUT = REPO_ROOT / "data" / "raw_text.txt"

PAGE_MARKER = "=== PDF PAGE {n} ==="

# Ligatures and typographic characters that InDesign emits, normalised so that
# searching the dump (and matching terms later) behaves predictably.
REPLACEMENTS = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "--", "\u2026": "...", "\u00a0": " ",
}


def clean(text: str) -> str:
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    # Collapse trailing whitespace per line but preserve blank-line structure.
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(lines)


def keep_obj(obj) -> bool:
    """Filter predicate applied to every page before extraction.

    Drops two kinds of non-rule content at the source:
    - Rotated glyphs: every page has a vertical sidebar ("Amtgard 8 - ...",
      revision date) that pdfplumber would otherwise emit reversed and mangled.
    - Flavor text: story quotes set in MinionPro-Italic at 10pt (rule-body
      italic, e.g. incantations, is 9pt). The rulebook states flavor text "are
      not rules", and the full-width quote blocks also break two-column gutter
      detection (they span the gutter), so excluding them is correct twice over.
    """
    if not obj.get("upright", True):
        return False
    font = obj.get("fontname") or ""
    if "MinionPro-It" in font and (obj.get("size") or 0) > 9.5:
        return False
    if "AquilineTwo" in font:  # decorative calligraphy: flavor-story titles
        return False
    # Figure callout labels ("(A) 2\" diameter...") on the arrow and weapon
    # construction diagrams. Set in 8pt MinionPro-Regular, a face used nowhere
    # in rule bodies (those are MinionPro-Capt at 9pt), so the size+face pair
    # identifies them exactly. They otherwise trail into the preceding entry.
    if "MinionPro-Regular" in font and (obj.get("size") or 0) < 8.5:
        return False
    return True


def find_gutter(page, min_frac: float = 0.35, max_frac: float = 0.65):
    """Return the x-coordinate of a vertical whitespace gutter, or None.

    Detects a two-column layout by looking for a sustained vertical band in the
    middle of the page that no word overlaps. Returns None for single-column
    pages, which is the common case for headings and full-bleed art pages.

    Large display headings are ignored when testing the band: a chapter title
    ("Weapon Types, Shields, and Equipment" at 19pt) is set across the full
    width above a two-column body, and letting it veto the gutter would
    interleave the two columns for the whole page.
    """
    words = [w for w in page.extract_words(extra_attrs=["size"])
             if (w.get("size") or 0) <= 12]
    if len(words) < 25:
        return None

    left, right = page.bbox[0], page.bbox[2]
    width = right - left
    lo = left + width * min_frac
    hi = left + width * max_frac

    # Sample candidate split points; a gutter is a column of x with no word spanning it.
    best = None
    step = max(width / 200.0, 0.5)
    x = lo
    while x <= hi:
        if not any(w["x0"] < x < w["x1"] for w in words):
            # Measure how many words fall on each side; a real gutter has both populated.
            l_count = sum(1 for w in words if w["x1"] <= x)
            r_count = sum(1 for w in words if w["x0"] >= x)
            if l_count >= 10 and r_count >= 10:
                balance = min(l_count, r_count) / max(l_count, r_count)
                if best is None or balance > best[1]:
                    best = (x, balance)
        x += step

    return best[0] if best else None


def cluster_rows(words, tol: float = 3.0) -> list[list[dict]]:
    """Group words into rows by shared baseline, each sorted left to right."""
    rows: list[list[dict]] = []
    row: list[dict] = []
    for w in sorted(words, key=lambda w: (w["bottom"], w["x0"])):
        if row and abs(w["bottom"] - row[0]["bottom"]) > tol:
            rows.append(sorted(row, key=lambda w: w["x0"]))
            row = []
        row.append(w)
    if row:
        rows.append(sorted(row, key=lambda w: w["x0"]))
    return rows


def tolerant_gutter(page, max_straddle: int = 2, min_frac: float = 0.35,
                    max_frac: float = 0.65):
    """Like find_gutter but allows a couple of straddling words.

    Used only on sub-regions already suspected of being two-column. A single
    overhanging word (a wide right-column word starting slightly left of the
    gutter) otherwise vetoes an obviously columnar block - printed p.12's
    armor entries split 172/172 words but for one such word.
    """
    words = [w for w in page.extract_words(extra_attrs=["size"])
             if (w.get("size") or 0) <= 12]
    if len(words) < 25:
        return None
    left, right = page.bbox[0], page.bbox[2]
    width = right - left
    step = max(width / 200.0, 0.5)

    best = None
    x = left + width * min_frac
    hi = left + width * max_frac
    while x <= hi:
        straddle = sum(1 for w in words if w["x0"] < x < w["x1"])
        if straddle <= max_straddle:
            l_count = sum(1 for w in words if w["x1"] <= x)
            r_count = sum(1 for w in words if w["x0"] >= x)
            if l_count >= 10 and r_count >= 10:
                balance = min(l_count, r_count) / max(l_count, r_count)
                if balance >= 0.5:
                    key = (straddle, -balance)
                    if best is None or key < best[0]:
                        best = (key, x)
        x += step
    return None if best is None else best[1]


def split_at_gutter(rows, gutter, strict: bool = True) -> list[list[dict]]:
    """Order rows around a known gutter: left column, then right.

    A row is only treated as full-width when a single WORD straddles the
    gutter - a row merely having words on both sides is the normal
    two-column case (aligned baselines) and must be split, not kept whole.
    Full-width rows (headings inside a two-column page) act as barriers, so
    text never migrates across them.

    With strict=False a straddling word is assigned to whichever side it
    overlaps more and creates no barrier; that suits a region already known
    to be columnar, where a lone overhanging word is noise rather than a
    genuine full-width line.
    """
    out: list[list[dict]] = []
    block: list[tuple[list, list]] = []

    def drain():
        if not block:
            return
        out.extend(l for l, _ in block if l)
        out.extend(r for _, r in block if r)
        block.clear()

    def side(w):
        if w["x1"] <= gutter:
            return "l"
        if w["x0"] >= gutter:
            return "r"
        return "l" if (gutter - w["x0"]) >= (w["x1"] - gutter) else "r"

    for row in rows:
        if strict and any(w["x0"] < gutter < w["x1"] for w in row):
            drain()
            out.append(row)
        else:
            block.append(([w for w in row if side(w) == "l"],
                          [w for w in row if side(w) == "r"]))
    drain()
    return out


def region_rows(region, gutter=None) -> list[list[dict]]:
    """Rows of a page region, column-cropped when a gutter is given.

    Cropping to each column and re-extracting inside it (rather than
    splitting whole-page rows) is what keeps a left-column line from being
    clustered together with a right-column line at a near-identical baseline.
    """
    def rows_of(area):
        return cluster_rows(area.extract_words(
            extra_attrs=["fontname", "size"], x_tolerance=1.5, y_tolerance=3))

    if gutter is None:
        return rows_of(region)
    x0, top, x1, bottom = region.bbox
    return (rows_of(region.crop((x0, top, gutter, bottom)))
            + rows_of(region.crop((gutter, top, x1, bottom))))


def ordered_rows(page) -> list[list[dict]]:
    """Rows of the page in true reading order.

    Strategy, in order of preference:
    1. A strict whole-page gutter (no word straddles it anywhere) - the
       common case, handled exactly as before by cropping into two columns.
    2. Otherwise the page may be MIXED: a full-width table or chapter title
       above a two-column body (printed p.12), or a two-column body above
       full-width prose (printed p.19). Split the page horizontally and treat
       only the columnar part as two columns.
    3. Otherwise genuinely single-column - plain top-to-bottom order.

    Only case 2 is new behaviour; pages that already worked take path 1 and
    are byte-identical to the previous crop-based implementation.
    """
    gutter = find_gutter(page)
    if gutter is not None:
        return region_rows(page, gutter)

    x0, top, x1, bottom = page.bbox
    plain = region_rows(page)
    if not plain:
        return plain

    # Scan candidate horizontal splits at row boundaries. A two-column region
    # must be a decent slice of the page, so tiny fragments are skipped.
    for row in plain:
        y = row[0]["top"] - 1
        if y <= top + 40 or y >= bottom - 80:
            continue
        g = tolerant_gutter(page.crop((x0, y, x1, bottom)))
        if g is not None:
            return (region_rows(page.crop((x0, top, x1, y)))
                    + region_rows(page.crop((x0, y, x1, bottom)), g))

    for row in reversed(plain):
        y = row[-1]["bottom"] + 1
        if y <= top + 80 or y >= bottom - 40:
            continue
        g = tolerant_gutter(page.crop((x0, top, x1, y)))
        if g is not None:
            return (region_rows(page.crop((x0, top, x1, y)), g)
                    + region_rows(page.crop((x0, y, x1, bottom))))

    return plain


def extract_page(page, mode: str) -> tuple[str, str]:
    """Return (text, layout_label) for one page.

    Applies keep_obj first - see its docstring for what gets dropped and why.
    """
    page = page.filter(keep_obj)
    rows = ordered_rows(page)
    text = "\n".join(" ".join(w["text"] for w in r) for r in rows)
    gutter = find_gutter(page)
    return (text, "1col" if gutter is None else f"2col@{gutter:.0f}")


def parse_page_range(spec: str, total: int) -> range:
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", spec.strip())
    if not m:
        sys.exit(f"Bad --pages value: {spec!r}. Use e.g. '12' or '30-45'.")
    start = int(m.group(1))
    end = int(m.group(2) or m.group(1))
    return range(max(start, 1), min(end, total) + 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path,
                    default=Path(os.environ.get("RULEBOOK_PDF") or DEFAULT_PDF))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--pages", help="Page or range, e.g. 30-45 (1-based, PDF order)")
    ap.add_argument("--columns", choices=["auto", "1", "2"], default="auto")
    ap.add_argument("--probe", action="store_true",
                    help="Print diagnostics and the cover page; write nothing.")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"Rulebook PDF not found at: {args.pdf}")

    print(f"Opening {args.pdf}  ({args.pdf.stat().st_size / 1e6:.1f} MB)")
    chunks: list[str] = []
    stats: list[tuple[int, int, int, str]] = []

    with pdfplumber.open(args.pdf) as pdf:
        total = len(pdf.pages)
        pages = parse_page_range(args.pages, total) if args.pages else range(1, total + 1)
        print(f"{total} pages total; extracting {len(pages)} of them.\n")

        for n in pages:
            page = pdf.pages[n - 1]
            text, layout = extract_page(page, args.columns)
            text = clean(text)
            stats.append((n, len(text), len(text.split()), layout))
            chunks.append(f"{PAGE_MARKER.format(n=n)}\n{text}\n")
            if n % 10 == 0 or n == pages[-1]:
                print(f"  ...page {n}/{pages[-1]}", flush=True)
            page.flush_cache()

    empty = [n for n, chars, _, _ in stats if chars < 20]
    two_col = sum(1 for *_, lay in stats if lay.startswith("2col"))

    print("\n--- extraction summary ---")
    print(f"pages processed : {len(stats)}")
    print(f"two-column      : {two_col}")
    print(f"single-column   : {len(stats) - two_col}")
    print(f"total words     : {sum(w for _, _, w, _ in stats):,}")
    print(f"near-empty pages: {empty if empty else 'none'}")

    if args.probe:
        print("\n--- first non-empty page (cover / version check) ---")
        for chunk in chunks:
            body = chunk.split("\n", 1)[1].strip()
            if body:
                print(body[:1500])
                break
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(chunks), encoding="utf-8")
    print(f"\nWrote {args.out}  ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
