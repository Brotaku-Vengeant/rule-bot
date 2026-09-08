"""Turn lookup Results into Discord embeds."""

from __future__ import annotations

import discord

from bot.lookup import Result

DESCRIPTION_LIMIT = 4096  # Discord's hard cap on one embed description
MESSAGE_LIMIT = 5800     # combined cap is 6000; leave headroom

CATEGORY_COLORS = {
    "ability": discord.Colour.blurple(),
    "state": discord.Colour.orange(),
    "mechanic": discord.Colour.teal(),
    "special effect": discord.Colour.purple(),
    "declaration": discord.Colour.dark_teal(),
    "school": discord.Colour.dark_purple(),
    "trinket": discord.Colour.green(),
    "talisman": discord.Colour.gold(),
    "artifact": discord.Colour.dark_gold(),
    "armor": discord.Colour.dark_grey(),
    "weapon": discord.Colour.dark_red(),
    "weapon term": discord.Colour.dark_orange(),
    "shield": discord.Colour.dark_blue(),
    "projectile": discord.Colour.dark_green(),
    "armor modifier": discord.Colour.light_grey(),
    "weapon rule": discord.Colour.dark_magenta(),
    "arrow": discord.Colour.dark_teal(),
    "equipment rule": discord.Colour.greyple(),
    # Dor Un Avathar (monster book) - greens and browns, visually distinct
    # from the rulebook's blues and golds.
    "monster": discord.Colour.dark_green(),
    "monster ability": discord.Colour.green(),
    "monster mechanic": discord.Colour.dark_teal(),
    "terrain": discord.Colour.dark_orange(),
    "scenario mechanic": discord.Colour.magenta(),
    "class": discord.Colour.blue(),
    "class rule": discord.Colour.dark_blue(),
    "award": discord.Colour.fuchsia(),
}
MISS_COLOR = discord.Colour.red()


def entry_where(e: dict) -> str:
    """Where to send a reader for an entry's full text.

    Only the rulebook has printed pages; the Dor Un Avathar is a Google Doc,
    so its entries point at a section instead.
    """
    if e.get("page"):
        return f"p.{e['page']}"
    return e.get("section") or e.get("source") or "the source book"


def truncate(text: str, where: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Cut on a line/sentence boundary, never mid-sentence, and say so."""
    if len(text) <= limit:
        return text
    notice = f"\n\n*...truncated - see {where} for the rest.*"
    room = limit - len(notice)
    cut = text[:room]
    # Prefer the last paragraph break, then sentence end, then word break.
    for sep in ("\n", ". ", " "):
        pos = cut.rfind(sep)
        if pos > room * 0.5:
            cut = cut[: pos + (1 if sep == ". " else 0)]
            break
    return cut.rstrip() + notice


def entry_embed(result: Result, rulebook: str) -> discord.Embed:
    e = result.entry
    title = e["name"]
    if e.get("availability"):
        title += f"   ({e['availability']})"

    embed = discord.Embed(
        title=title,
        description=truncate(e["text"], entry_where(e)),
        colour=CATEGORY_COLORS.get(e["category"], discord.Colour.greyple()),
    )
    if result.kind == "fuzzy":
        embed.set_author(name=f'Closest match for "{result.query}"')
    # Each entry cites its own book: the rulebook has printed page numbers,
    # the Dor Un Avathar is a Google Doc with none, so it cites its section.
    book = e.get("source") or rulebook
    cite = f"{book} - {e['section']}"
    if e.get("page"):
        cite += f", p.{e['page']}"
    embed.set_footer(text=cite)
    return embed


def ambiguous_embed(result: Result) -> discord.Embed:
    lines = [f"- **{e['name']}**  ({e['category']}, {entry_where(e)})"
             for e in result.suggestions]
    return discord.Embed(
        title=f'"{result.query}" matches several entries',
        description="\n".join(lines) + "\n\nTry again with the full name.",
        colour=discord.Colour.gold(),
    )


def miss_embed(result: Result) -> discord.Embed:
    desc = f'Nothing in the rulebook matches **"{result.query}"**.'
    if result.suggestions:
        names = ", ".join(f"`{e['name']}`" for e in result.suggestions)
        desc += f"\nDid you mean: {names}?"
    return discord.Embed(
        title="No Terms Found",
        description=desc,
        colour=MISS_COLOR,
    )


def embed_cost(embed: discord.Embed) -> int:
    """Characters this embed counts against Discord's per-message budget."""
    return sum(len(part) for part in (
        embed.title or "",
        embed.description or "",
        (embed.footer.text if embed.footer else None) or "",
        (embed.author.name if embed.author else None) or "",
    ))


def fit_embeds(embeds: list[discord.Embed],
               limit: int = MESSAGE_LIMIT) -> list[discord.Embed]:
    """Shrink descriptions so all embeds fit one message.

    Discord caps the COMBINED text of every embed in a message at 6000
    characters; exceeding it rejects the whole reply, so several long entries
    in one message would otherwise fail outright. The longest description is
    trimmed repeatedly until the total fits, which keeps every requested
    lookup present rather than dropping some silently.
    """
    if not embeds or sum(embed_cost(e) for e in embeds) <= limit:
        return embeds

    # Water-filling: find the largest per-description cap that fits, so short
    # entries stay whole and only the long ones give ground - trimming the
    # single longest repeatedly would gut the first entries while later ones
    # kept full length.
    overhead = sum(embed_cost(e) - len(e.description or "") for e in embeds)
    budget = max(limit - overhead, 0)
    lengths = [len(e.description or "") for e in embeds]

    lo, hi, cap = 0, max(lengths), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if sum(min(n, mid) for n in lengths) <= budget:
            cap, lo = mid, mid + 1
        else:
            hi = mid - 1

    for embed in embeds:
        if len(embed.description or "") <= cap:
            continue
        where = "the source book"
        footer = embed.footer.text if embed.footer else ""
        if ", p." in footer:
            where = "p." + footer.rsplit("p.", 1)[-1]
        elif " - " in footer:
            where = footer.split(" - ", 1)[-1]
        embed.description = truncate(embed.description, where, limit=cap)
    return embeds


def render(result: Result, rulebook: str) -> discord.Embed:
    if result.kind in ("exact", "fuzzy"):
        return entry_embed(result, rulebook)
    if result.kind == "ambiguous":
        return ambiguous_embed(result)
    return miss_embed(result)


def guild_list_embed(guilds, rulebook: str) -> discord.Embed:
    """One embed listing every server the bot is in.

    Takes a plain sequence rather than the client so it can be built and
    tested without a live connection. Each guild needs .name, .id and
    .member_count; .me.joined_at is used when present.
    """
    guilds = sorted(guilds, key=lambda g: (-(g.member_count or 0), g.name))

    lines = []
    for g in guilds:
        joined = getattr(getattr(g, "me", None), "joined_at", None)
        when = f" - joined {joined:%Y-%m-%d}" if joined else ""
        members = f"{g.member_count:,}" if g.member_count else "?"
        lines.append(f"- **{g.name}** ({members} members){when}\n  `{g.id}`")

    total = len(guilds)
    embed = discord.Embed(
        title=f"In {total} server{'' if total == 1 else 's'}",
        description="\n".join(lines) or "Not in any servers yet.",
        colour=discord.Colour.blurple(),
    )
    embed.set_footer(text=rulebook)
    return fit_embeds([embed])[0]
