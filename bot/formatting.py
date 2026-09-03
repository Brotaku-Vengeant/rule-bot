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
    "class": discord.Colour.blue(),
    "class rule": discord.Colour.dark_blue(),
    "award": discord.Colour.fuchsia(),
}
MISS_COLOR = discord.Colour.red()


def truncate(text: str, page: int | str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Cut on a line/sentence boundary, never mid-sentence, and say so."""
    if len(text) <= limit:
        return text
    notice = f"\n\n*...truncated - see p.{page} of the rulebook for the rest.*"
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
        description=truncate(e["text"], e["page"]),
        colour=CATEGORY_COLORS.get(e["category"], discord.Colour.greyple()),
    )
    if result.kind == "fuzzy":
        embed.set_author(name=f'Closest match for "{result.query}"')
    embed.set_footer(text=f"{rulebook} - {e['section']}, p.{e['page']}")
    return embed


def ambiguous_embed(result: Result) -> discord.Embed:
    lines = [f"- **{e['name']}**  ({e['category']}, p.{e['page']})"
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
        page = "?"
        if embed.footer and embed.footer.text:
            page = embed.footer.text.rsplit("p.", 1)[-1] or "?"
        embed.description = truncate(embed.description, page, limit=cap)
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
