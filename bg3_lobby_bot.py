import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
import json
from datetime import datetime
import logging

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
USER_ID = int(os.getenv('USER_ID'))  # Host user ID from .env
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))  # Channel ID from .env
BG3_NAME = "Baldur's Gate 3"
DATA_FILE = 'code_data.json'

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load and save utility for persistent data
def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'code': None, 'timestamp': None, 'party_info': None, 'message_id': None, 'ping_message_id': None}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

# Intents for presence tracking
intents = discord.Intents.default()
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix='!', intents=intents)
data = load_data()

async def update_message():
    """Send or update the persistent code embed in the configured channel."""
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error(f"Channel with ID {CHANNEL_ID} not found.")
        return

    # Permission checks
    bot_member = channel.guild.get_member(bot.user.id)
    perms = channel.permissions_for(bot_member)
    required = ['view_channel', 'read_message_history', 'send_messages', 'embed_links']
    missing = [p for p in required if not getattr(perms, p)]
    if missing:
        logger.error(f"Missing permissions in channel {CHANNEL_ID}: {', '.join(missing)}")
        return

    # Prepare embed data
    code_val = data.get('code') or 'None'
    party_val = data.get('party_info') or 'N/A'
    timestamp = data.get('timestamp')

    embed = discord.Embed(
        title="🔗 BG3 Multiplayer Connection",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Current Code", value=f"```{code_val}```", inline=False)
    embed.add_field(name="Party Size", value=party_val, inline=True)
    if data.get('party_info'):
        try:
            curr, max_ = map(int, data['party_info'].split('/'))
            availability = "Slots available ✅" if curr < max_ else "Party is full ❌"
            embed.add_field(name="Availability", value=availability, inline=True)
        except Exception:
            pass
    if timestamp:
        embed.add_field(name="Last updated", value=f"<t:{timestamp}:f>", inline=False)

    # Try to edit existing embed message
    if data.get('message_id'):
        try:
            msg = await channel.fetch_message(data['message_id'])
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.Forbidden) as e:
            data['message_id'] = None
            logger.error(f"Could not edit message: {e}")

    # Send a new embed if edit failed or no message_id
    try:
        msg = await channel.send(embed=embed)
        data['message_id'] = msg.id
        save_data(data)
    except discord.Forbidden:
        logger.error(f"Insufficient permissions to send embed in channel {CHANNEL_ID}: {perms}")

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    synced = await bot.tree.sync()
    logger.info(f"Synced {len(synced)} commands.")
    await update_message()

@bot.tree.command(name="setcode", description="Set the direct connection code for BG3 multiplayer")
async def setcode(interaction: discord.Interaction, code: str):
    if interaction.user.id != USER_ID:
        await interaction.response.send_message("❌ Only the party owner can set the code.", ephemeral=True)
        return
    if not (code.isalnum() and len(code) == 14):
        await interaction.response.send_message(
            "❌ Code must be exactly 14 alphanumeric characters (e.g. 72ARXMVCQG35Z6)", ephemeral=True
        )
        return

    data['code'] = code
    data['timestamp'] = int(datetime.now().timestamp())
    channel = bot.get_channel(CHANNEL_ID)
    ping_id = data.get('ping_message_id')
    if ping_id and channel:
        try:
            ping_msg = await channel.fetch_message(ping_id)
            await ping_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
    data['ping_message_id'] = None
    save_data(data)

    await update_message()
    await interaction.response.send_message(f"✅ Code set to **{code}**", ephemeral=True)

@bot.tree.command(name="getcode", description="Get the current BG3 multiplayer connection code and party status")
async def getcode(interaction: discord.Interaction):
    code = data.get('code') or 'None'
    party = data.get('party_info')
    timestamp = data.get('timestamp')
    content = f"🔑 **Code:** `{code}`"
    if party:
        try:
            curr, max_ = map(int, party.split('/'))
            availability = "✅" if curr < max_ else "❌"
        except:
            availability = ""
        content += f"\n👥 **Party:** {party} ({availability})"
    else:
        content += "\n👥 **Party:** N/A"
    if timestamp:
        content += f"\n⏰ **Last updated:** <t:{timestamp}:f>"
    await interaction.response.send_message(content, ephemeral=True)

@bot.tree.command(name="clearcode", description="Clear the current BG3 multiplayer connection code")
async def clearcode(interaction: discord.Interaction):
    if interaction.user.id != USER_ID:
        await interaction.response.send_message("❌ Only the party owner can clear the code.", ephemeral=True)
        return

    data['code'] = None
    data['timestamp'] = int(datetime.now().timestamp())
    channel = bot.get_channel(CHANNEL_ID)
    ping_id = data.get('ping_message_id')
    if ping_id and channel:
        try:
            ping_msg = await channel.fetch_message(ping_id)
            await ping_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
    data['ping_message_id'] = None
    save_data(data)

    await update_message()
    await interaction.response.send_message("✅ Code cleared.", ephemeral=True)

@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if after.id != USER_ID:
        return

    party_info = None
    is_now = False
    for act in after.activities:
        if getattr(act, 'name', None) == BG3_NAME and getattr(act, 'party', None):
            party = act.party
            party_info = f"{party.size[0]}/{party.size[1]}"
            is_now = True
            break

    was_in = bool(data.get('party_info'))

    if is_now and not was_in:
        data['party_info'] = party_info
        channel = bot.get_channel(CHANNEL_ID)
        bot_member = channel.guild.get_member(bot.user.id) if channel else None
        if channel and channel.permissions_for(bot_member).send_messages:
            msg = await channel.send(f"<@{USER_ID}> please set the direct connection code via `/setcode` (Party: {party_info})")
            data['ping_message_id'] = msg.id
        save_data(data)

    if not is_now and was_in:
        data['code'] = None
        data['party_info'] = None
        data['timestamp'] = int(datetime.now().timestamp())
        save_data(data)
        await update_message()

if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
