# Amtgard Rules Bot

A Scryfall-style Discord bot for Amtgard: type `[[Brutal Strike]]` or
`[[Beastfolk]]` in any message and the bot replies with that entry quoted
**verbatim** from the rulebook or the monster book, with a citation. There's
also a `/rule` slash command with autocomplete over every indexed term.

The bot never paraphrases or generates rule text. A one-time extraction pass
turns each source book into a JSON index; at runtime the bot only matches a
term and prints the stored text. Open the JSON to audit exactly what it can say.

**Coverage: 439 entries across two books.**

*Amtgard Rules of Play v8.08 "Spongy"* — 350 entries:

| | |
|---|---|
| Abilities | 179, with caster purchase costs folded in |
| Classes | 15, overview and level progression |
| Mechanics · States · Special Effects | 28 · 8 · 8 |
| Magic Items | 29 — potions, scrolls, talismans, artifacts |
| Equipment | 58 — armor types and modifiers, weapon and shield types, projectiles, arrows, safety rules, shield sizes, bows, siege |
| Awards | 12 — Knighthood, Masterhood, the nine Ladder Awards |
| Schools · Declarations | 8 · 3 |

*Dor Un Avathar XI* — 89 entries:

| | |
|---|---|
| Monsters | 54 across all four tiers |
| Monster abilities | 17 — `Strong`, `Flying`, `Thick Skin`… |
| Terrain · Scenario mechanics · Monster mechanics | 8 · 8 · 2 |

Flavor text is excluded (the rulebook itself notes it is not rules), as are the
monster book's blank homebrew cards, design guidelines, and scenario write-ups.

All rule text belongs to [Amtgard](https://amtgard.com); this bot is a fan-made
lookup tool for club use.

## Why this source is public

So you can check it. A bot that answers rules questions is only worth trusting
if you can see that it isn't making things up, so the whole pipeline is here to
read: how the rulebook PDF is parsed, what ends up in the index, and the fact
that at runtime the bot only matches a term and prints stored text. It has no
ability to invent a rule.

This is source-available, not open source: the code is published for
inspection, and copyright is retained (see [LICENSE](LICENSE)). If you want to
run it for your own Amtgard group, ask — the answer is likely yes.

**Neither book's text is in this repository.** `data/rules.json` and
`data/dua.json` are generated on your machine from your own copy of the
sources, and are gitignored. See [Building the index](#building-the-index).

## Sources

The bot answers from two books, each indexed by its own pipeline into its own
file, so either can be rebuilt without touching the other:

| Book | Source | Built by | Citation style |
|---|---|---|---|
| Amtgard Rules of Play v8.08 "Spongy" | PDF | `scripts/build_index.py` | section + printed page |
| Dor Un Avathar XI | Google Doc | `scripts/build_dua.py` | section (a doc has no pages) |

Neither book's text is in this repository; both indexes are generated locally
and gitignored. The Dor Un Avathar build records a hash of the snapshot it read
and the date it was fetched, since a Google Doc can change under you.

## Building the index

The repository ships the tooling, not the rulebook. To produce the index the
bot reads:

1. Download the current rulebook PDF from [amtgard.com](https://amtgard.com)
   and save it as `rulebook/amtgard-rulebook.pdf` (or point `RULEBOOK_PDF` in
   your `.env` at wherever you keep it).
2. Extract and build:

   ```
   python scripts/extract_pdf.py
   python scripts/build_index.py
   ```

3. For the monster book, fetch and build it (no manual download needed):

   ```
   python scripts/build_dua.py --fetch
   ```

`extract_pdf.py` writes a plain-text dump you can eyeball, and
`build_index.py` writes `data/rules.json` and prints a summary of what it
found. Both are re-runnable when a new rulebook version comes out.

## Running it

1. **Python 3.12+** with the project venv:

   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

2. **Create the Discord application** (once):
   - Go to https://discord.com/developers/applications → **New Application**.
   - **Bot** tab → *Reset Token* → copy the token.
   - Still on **Bot**: under *Privileged Gateway Intents*, enable
     **Message Content Intent**. Without this, `[[...]]` lookups silently
     never fire — this is the #1 gotcha.
   - Copy `.env.example` to `.env` and paste the token in.

3. **Invite it to your server**: on **OAuth2 → URL Generator**, check the
   `bot` and `applications.commands` scopes, then under Bot Permissions check
   *Send Messages*, *Embed Links*, and *Read Message History*. Open the
   generated URL and pick your server.

4. **Start it**: double-click `run_bot.bat`, or:

   ```
   .venv\Scripts\python -m bot.main
   ```

## Using it

| You type | Bot does |
|---|---|
| `is [[Brutal Strike]] a wound trigger?` | Replies with the Brutal Strike entry, p.62 |
| `[[insubstantal]]` (typo) | Fuzzy-matches to Insubstantial, labeled "closest match" |
| `[[gift]]` | Lists the Gift of Air/Earth/Fire/Water choices |
| `[[flurbo]]` | **No Terms Found**, with nearest-term suggestions |
| `[[beastfolk]]` | Monster stat block from the *Dor Un Avathar XI* |
| `/rule` | Slash command with autocomplete over all 439 terms |

At most 5 `[[lookups]]` per message are answered, to keep spam impossible.

### What the host can see

Disclosed for completeness, since the point of publishing this source is that
nothing about the bot is hidden. None of it is unusual — Discord reports this
to every bot automatically — but you should not have to read the code to know
it exists.

- `/servers` lists the servers the bot is in (name, ID, member count, join
  date). Only the account that owns the Discord application can run it, and the
  reply is ephemeral, so the list is never posted into a channel.
- The console log records the servers at startup, and a line whenever the bot
  is added to or removed from one.

The bot reads message content only to find `[[...]]` lookups, and does not
store messages, log who asked what, or track users. It has no database; the
only thing it writes is that console log. See `bot/main.py`.

## When a new edition comes out

```
.venv\Scripts\python scripts\extract_pdf.py --pdf "path\to\new.pdf"
:: eyeball data\raw_text.txt, then:
.venv\Scripts\python scripts\build_index.py --pdf "path\to\new.pdf" --report
:: check the term list, then without --report to write data\rules.json
.venv\Scripts\python -m pytest
```

For the monster book, `python scripts/build_dua.py --fetch` re-downloads and
rebuilds; the printed snapshot hash tells you whether the doc actually changed.

`scripts/build_index.py` detects entries by **font** (bold headings at specific
sizes), not regex, so it should survive layout-compatible revisions. The
`--report` output and the test suite are the safety net. If the page offset
changes, update `PAGE_OFFSET` there (printed folio = PDF page − offset).

## Deploying later (24/7)

The bot is stateless: `bot/` + the two index files (`data/rules.json`,
`data/dua.json`) + a `DISCORD_TOKEN` env var is everything. `Dockerfile` builds a minimal image for Railway/Fly.io/any VPS;
set `DISCORD_TOKEN` in the host's secret store, never in the image.

## Layout

```
scripts/extract_pdf.py   rulebook PDF -> data/raw_text.txt (auditable dump)
scripts/build_index.py   raw text -> data/rules.json (+ --report / --query)
scripts/build_dua.py     monster book -> data/dua.json  (+ --fetch / --report)
bot/lookup.py            matching engine; merges both indexes (no Discord imports)
bot/formatting.py        embeds, incl. per-book citations and the 6000-char budget
bot/main.py              Discord client: [[...]] listener + /rule, /servers
tests/test_lookup.py     python -m pytest
```
