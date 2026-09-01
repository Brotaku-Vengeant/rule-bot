# Amtgard Rules Bot

A Scryfall-style Discord bot for Amtgard: type `[[Brutal Strike]]` or
`[[Insubstantial]]` in any message and the bot replies with that entry quoted
**verbatim** from the *Amtgard v8.08 "Spongy"* rulebook, with a page citation.
There's also a `/rule` slash command with autocomplete over every indexed term.

The bot never paraphrases or generates rule text. A one-time extraction pass
turns the rulebook PDF into `data/rules.json`; at runtime the bot only matches
a term and prints the stored text. Open the JSON to audit exactly what it can say.

**Coverage:** the Magic & Abilities glossary (179 abilities), States (8), Declarations (3),
Special Effects (8), the eight magic School definitions, ability mechanics (28), Magic Items — potions, scrolls, talismans, artifacts (29) — and equipment: armor types (11), weapon
definitions (12), melee weapon and shield types (8), projectiles (6), armor modifiers (4), weapon safety rules (3), arrow components (4),
and section rulesets incl. shield sizes, bows and siege weapons (11) — 321 entries.
Flavor text is excluded (the rulebook itself notes it is not rules).

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

**No rulebook text is in this repository.** `data/rules.json` is generated on
your machine from your own copy of the rulebook PDF, and is gitignored. See
[Building the index](#building-the-index) below.

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
| `/rule` | Slash command with autocomplete over all 321 terms |
| `/servers` | Lists every server the bot is in. Owner-only; the reply is private |

At most 5 `[[lookups]]` per message are answered, to keep spam impossible.

## Knowing which servers have added the bot

The invite link can be reused by anyone, so the bot reports its own membership
three ways:

- **At startup** it lists every server (name, ID, member count) in the console
  window. Reconnects log a one-line count instead of repeating the list.
- **On changes** it logs `ADDED to server:` / `REMOVED from server:` as they
  happen, so a server added while you were away is still in the scrollback.
- **`/servers`** shows the same list inside Discord. Only the application owner
  gets the list; the reply is ephemeral, so it is never posted into a channel.

## When a new rulebook version comes out

```
.venv\Scripts\python scripts\extract_pdf.py --pdf "path\to\new.pdf"
:: eyeball data\raw_text.txt, then:
.venv\Scripts\python scripts\build_index.py --pdf "path\to\new.pdf" --report
:: check the term list, then without --report to write data\rules.json
.venv\Scripts\python -m pytest
```

`scripts/build_index.py` detects entries by **font** (bold headings at specific
sizes), not regex, so it should survive layout-compatible revisions. The
`--report` output and the test suite are the safety net. If the page offset
changes, update `PAGE_OFFSET` there (printed folio = PDF page − offset).

## Deploying later (24/7)

The bot is stateless: `bot/` + `data/rules.json` + a `DISCORD_TOKEN` env var
is everything. `Dockerfile` builds a minimal image for Railway/Fly.io/any VPS;
set `DISCORD_TOKEN` in the host's secret store, never in the image.

## Layout

```
scripts/extract_pdf.py   PDF -> data/raw_text.txt   (auditable text dump)
scripts/build_index.py   raw text -> data/rules.json (+ --report / --query)
bot/lookup.py            matching engine (no Discord imports; unit-tested)
bot/formatting.py        embeds, incl. 4096-char truncation on a boundary
bot/main.py              Discord client: [[...]] listener + /rule command
tests/test_lookup.py     python -m pytest
```
