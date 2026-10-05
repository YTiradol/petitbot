import asyncio
import json
import os

import discord
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]
DATA_FILE = "stats_channels.json"  # {guild_id: channel_id}
NOM_SALON = "Membre: {count}"  # format du nom du salon
MIN_INTERVAL = 300  # secondes entre 2 renommages (limite Discord : 2 / 10 min)

intents = discord.Intents.default()
intents.members = True  # nécessaire pour on_member_join / on_member_remove


def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.stats_channels: dict = load_data()
        self.last_update: dict[int, float] = {}
        self.pending: set[int] = set()

    async def setup_hook(self):
        await self.tree.sync()

    async def update_stats_channel(self, guild: discord.Guild) -> None:
        """Renomme le salon stats avec le nombre de membres actuel."""
        channel_id = self.stats_channels.get(str(guild.id))
        if channel_id is None:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        nouveau_nom = NOM_SALON.format(count=guild.member_count)
        if channel.name == nouveau_nom:
            return
        await channel.edit(name=nouveau_nom, reason="Mise à jour du nombre de membres")
        self.last_update[guild.id] = asyncio.get_running_loop().time()

    async def request_update(self, guild: discord.Guild) -> None:
        """Met à jour le salon en respectant la limite de Discord.

        Si plusieurs arrivées/départs ont lieu coup sur coup, une seule
        mise à jour est planifiée avec le nombre final de membres.
        """
        if str(guild.id) not in self.stats_channels or guild.id in self.pending:
            return
        self.pending.add(guild.id)
        try:
            loop = asyncio.get_running_loop()
            ecoule = loop.time() - self.last_update.get(guild.id, -MIN_INTERVAL)
            if ecoule < MIN_INTERVAL:
                await asyncio.sleep(MIN_INTERVAL - ecoule)
        finally:
            self.pending.discard(guild.id)
        try:
            await self.update_stats_channel(guild)
        except discord.HTTPException as e:
            print(f"Erreur de mise à jour ({guild.name}) : {e}")


bot = Bot()


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} (id: {bot.user.id})")
    # Resynchronise tous les salons configurés au démarrage
    for guild in bot.guilds:
        await bot.request_update(guild)


@bot.event
async def on_member_join(member: discord.Member):
    await bot.request_update(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    await bot.request_update(member.guild)


@bot.tree.command(name="ping", description="Affiche la latence du bot")
async def ping(interaction: discord.Interaction):
    latence = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong ! {latence} ms")


@bot.tree.command(
    name="stats",
    description="Définit le salon qui affiche le nombre de membres (mis à jour automatiquement)",
)
@app_commands.describe(salon_id="L'ID du salon à utiliser")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
async def stats(interaction: discord.Interaction, salon_id: str):
    if not salon_id.isdigit():
        await interaction.response.send_message("❌ ID de salon invalide.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(int(salon_id))
    if channel is None:
        await interaction.response.send_message(
            "❌ Salon introuvable sur ce serveur.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Enregistre le salon (persisté dans stats_channels.json)
    bot.stats_channels[str(interaction.guild.id)] = channel.id
    save_data(bot.stats_channels)

    try:
        await bot.update_stats_channel(interaction.guild)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Je n'ai pas la permission « Gérer les salons » pour ce salon."
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Erreur Discord : {e}")
        return

    await interaction.followup.send(
        f"✅ Le salon <#{channel.id}> affichera désormais le nombre de membres "
        f"(**{interaction.guild.member_count}** actuellement) et sera mis à jour automatiquement."
    )


bot.run(TOKEN)
