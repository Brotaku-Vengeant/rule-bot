"""Amtgard rulebook lookup bot.

Two ways to look up a rule:
  - [[Term]] anywhere in a normal message (Scryfall style)
  - /rule term   (slash command, with autocomplete over every indexed term)

Requires the Message Content Intent to be enabled BOTH here (below) and in the
Discord Developer Portal (Bot -> Privileged Gateway Intents). If the portal
toggle is off, message.content arrives empty and [[...]] silently never fires.
"""

from __future__ import annotations

import logging
import os
import re
import sys

import discord
from discord import app_commands
from dotenv import load_dotenv

from bot.formatting import fit_embeds, guild_list_embed, render
from bot.lookup import RuleIndex

log = logging.getLogger("amtgard-bot")

BRACKET_RE = re.compile(r"\[\[([^\[\]]{1,80})\]\]")
MAX_LOOKUPS_PER_MESSAGE = 5


class RulebookClient(discord.Client):
    def __init__(self, index: RuleIndex):
        intents = discord.Intents.default()
        intents.message_content = True  # portal toggle must match
        super().__init__(intents=intents)
        self.index = index
        self.tree = app_commands.CommandTree(self)
        self.owner_id: int | None = None
        self._listed_guilds = False

    async def setup_hook(self) -> None:
        # Resolve who owns this application, so /servers can be restricted to
        # them. Team-owned apps have no plain .owner, hence the team branch.
        info = await self.application_info()
        if info.team:
            self.owner_id = info.team.owner_id
        elif info.owner:
            self.owner_id = info.owner.id
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%d entries indexed, %s)",
                 self.user, len(self.index.entries), self.index.rulebook)
        # on_ready fires again on every reconnect; list the servers once
        # rather than re-dumping them into the console each time.
        if self._listed_guilds:
            log.info("Reconnected - in %d server(s)", len(self.guilds))
            return
        self._listed_guilds = True
        log.info("In %d server(s):", len(self.guilds))
        for g in self.guilds:
            log.info("  - %s (id %s, %s members)",
                     g.name, g.id, g.member_count or "?")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("ADDED to server: %s (id %s, %s members) - now in %d",
                 guild.name, guild.id, guild.member_count or "?",
                 len(self.guilds))

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("REMOVED from server: %s (id %s) - now in %d",
                 guild.name, guild.id, len(self.guilds))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:  # covers ourselves and every other bot
            return
        queries = BRACKET_RE.findall(message.content)[:MAX_LOOKUPS_PER_MESSAGE]
        if not queries:
            return
        embeds = fit_embeds([render(self.index.search(q), self.index.rulebook)
                             for q in queries])
        await message.reply(embeds=embeds, mention_author=False)


def build_client(index: RuleIndex) -> RulebookClient:
    client = RulebookClient(index)

    @client.tree.command(name="rule", description="Look up a rulebook term")
    @app_commands.describe(term="Ability, state, or rule term to look up")
    async def rule(interaction: discord.Interaction, term: str) -> None:
        result = index.search(term)
        await interaction.response.send_message(
            embed=render(result, index.rulebook)
        )

    @client.tree.command(name="servers",
                         description="List the servers this bot is in (owner only)")
    @app_commands.default_permissions(administrator=True)
    async def servers(interaction: discord.Interaction) -> None:
        # default_permissions only hides the command in the UI; the owner
        # check below is the actual authorization.
        if client.owner_id is None or interaction.user.id != client.owner_id:
            await interaction.response.send_message(
                "Only the bot's owner can use this.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=guild_list_embed(client.guilds, index.rulebook),
            ephemeral=True,
        )

    @rule.autocomplete("term")
    async def rule_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.lower()
        names = index.names()
        starts = [n for n in names if n.lower().startswith(current)]
        contains = [n for n in names
                    if current in n.lower() and n not in starts]
        return [app_commands.Choice(name=n, value=n)
                for n in (starts + contains)[:25]]

    return client


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token or token == "paste_your_bot_token_here":
        print(
            "DISCORD_TOKEN is not set.\n"
            "Copy .env.example to .env and paste your bot token into it.\n"
            "(Get one at https://discord.com/developers/applications)",
            file=sys.stderr,
        )
        return 1

    index = RuleIndex.load()
    build_client(index).run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
