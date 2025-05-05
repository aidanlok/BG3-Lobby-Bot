import os
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands
import json
from json import JSONDecodeError
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import asyncio

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
USER_ID = int(os.getenv('USER_ID', '0'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
BG3_NAME = "Baldur's Gate 3"
DATA_FILE = 'code_data.json'
LOG_FILE = 'bg3_lobby_bot.log'
BOT_VERSION = '1.2.1'

# Logging setup: rotating file and console
logger = logging.getLogger('bg3_lobby_bot')
logger.setLevel(logging.INFO)
rfh = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
rfh.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter(fmt='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
rfh.setFormatter(formatter)
ch.setFormatter(formatter)
logger.addHandler(rfh)
logger.addHandler(ch)

# Debounced save_data using asyncio
_save_handle: asyncio.TimerHandle = None

def _immediate_save(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
        logger.info("Data saved successfully.")
    except Exception as e:
        logger.error(f"Failed to write data: {e}")

def save_data(data):
    global _save_handle
    try:
        if _save_handle and not _save_handle.cancelled():
            _save_handle.cancel()
    except Exception:
        pass
    loop = asyncio.get_event_loop()
    _save_handle = loop.call_later(2, lambda: _immediate_save(data))

# Load utility with schema validation and subscribers list
def load_data():
    default = {
        'code': None,
        'timestamp': None,
        'party_info': None,
        'message_id': None,
        'ping_message_id': None,
        'subscribers': []
    }
    try:
        with open(DATA_FILE, 'r') as f:
            raw = json.load(f)
    except (FileNotFoundError, JSONDecodeError) as e:
        logger.warning(f"Data error ({e}), using defaults.")
        return default.copy()
    if not isinstance(raw, dict):
        logger.warning("Data file not a dict, resetting.")
        return default.copy()
    data = default.copy()
    for k, v in raw.items():
        if k in data:
            data[k] = v
    subs = data.get('subscribers')
    if not isinstance(subs, list):
        data['subscribers'] = []
    else:
        data['subscribers'] = [int(u) for u in subs if isinstance(u, (int, str))]
    return data

# Bot setup
intents = discord.Intents.default()
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Global state
data = load_data()
_lobby_channel: discord.TextChannel = None
_lobby_perms = None

# Helper: parse party info
def parse_party_info(activities) -> str | None:
    """
    Inspect a list of activities, return a 'current/max' party string if BG3 party detected.
    """
    for act in activities:
        if getattr(act, 'name', None) == BG3_NAME and getattr(act, 'party', None):
            party = act.party
            size = party.get('size') if isinstance(party, dict) else getattr(party, 'size', None)
            if isinstance(size, (list, tuple)) and len(size) == 2:
                return f"{size[0]}/{size[1]}"
    return None

# Helper: build the embed
def build_embed():
    """
    Build the embed for the lobby channel.
    """
    code_val = data.get('code') or 'None'
    party_info = data.get('party_info') or 'N/A'
    ts = data.get('timestamp')
    color = discord.Color.blurple()
    if data.get('party_info'):
        try:
            curr, max_ = map(int, party_info.split('/'))
            color = discord.Color.green() if curr < max_ else discord.Color.red()
        except Exception:
            logger.exception("Invalid party_info for color")
    embed = discord.Embed(title="🔗 BG3 Multiplayer Connection", color=color)
    embed.add_field(name="Current Code", value=f"```{code_val}```", inline=False)
    if data.get('party_info'):
        try:
            curr, max_ = map(int, party_info.split('/'))
            status = "Slots available ✅" if curr < max_ else "Party is full ❌"
            embed.add_field(name="Party Status", value=f"{curr}/{max_} ({status})", inline=True)
        except Exception:
            logger.exception("Invalid party_info for status")
    else:
        embed.add_field(name="Party Status", value=party_info, inline=True)
    if ts:
        embed.add_field(name="Last updated", value=f"<t:{ts}:f>", inline=False)
    embed.set_footer(text=f"Use /party info • Bot v{BOT_VERSION}")
    return embed

# Helper: send or edit message
async def send_or_edit_message(embed):
    global _lobby_channel, _lobby_perms
    if not _lobby_channel:
        logger.error("Lobby channel not initialized.")
        return
    if not (_lobby_perms.send_messages and _lobby_perms.embed_links):
        logger.error("Missing permissions in lobby channel.")
        return
    msg_id = data.get('message_id')
    if msg_id:
        try:
            msg = await _lobby_channel.fetch_message(msg_id)
            await msg.edit(embed=embed)
            logger.info("Edited existing lobby message.")
            return
        except (discord.NotFound, discord.Forbidden) as e:
            logger.warning(f"Edit failed: {e}")
            data['message_id'] = None
    try:
        msg = await _lobby_channel.send(embed=embed)
        data['message_id'] = msg.id
        save_data(data)
        logger.info("Sent new lobby message.")
    except discord.Forbidden:
        logger.error("Cannot send lobby embed.")

# Core update
async def update_message():
    embed = build_embed()
    await send_or_edit_message(embed)

# Slash commands group
code_group = app_commands.Group(name="party", description="Manage BG3 party info")

@code_group.command(name="subscribe", description="Subscribe for code change DMs")
async def subscribe(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid in data['subscribers']:
        return await interaction.response.send_message("🔔 You are already subscribed.", ephemeral=True)
    data['subscribers'].append(uid)
    logger.info(f"User {uid} subscribed to code changes.")
    save_data(data)
    await interaction.response.send_message("🔔 You will now receive DMs when the code changes. You may unsubscribe at any time by using `/party unsubscribe`.", ephemeral=True)

@code_group.command(name="unsubscribe", description="Unsubscribe from code change DMs")
async def unsubscribe(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid not in data['subscribers']:
        return await interaction.response.send_message("🔕 You are not subscribed. You may subscribe by using `/party subscribe`.", ephemeral=True)
    data['subscribers'].remove(uid)
    logger.info(f"User {uid} unsubscribed from code changes.")
    save_data(data)
    await interaction.response.send_message("🔕 You have been unsubscribed. You may resubscribe at any time.", ephemeral=True)

@code_group.command(name="set", description="Set the direct connection code for BG3 multiplayer")
@app_commands.describe(notify="Whether to notify subscribers of the changed code")
async def code_set(interaction: discord.Interaction, code: str, notify: bool = True):
    if interaction.user.id != USER_ID:
        return await interaction.response.send_message("❌ Only the host can use this command.", ephemeral=True)
    if not (code.isalnum() and len(code) == 14):
        return await interaction.response.send_message("❌ Code must be 14 alphanumeric (e.g. 72ARXMVCQG35Z6).", ephemeral=True)
    old_code = data.get('code')
    data['code'] = code
    data['timestamp'] = int(datetime.now().timestamp())
    data['party_info'] = data.get('party_info')

    # delete any existing ping
    if data.get('ping_message_id'):
        try:
            old = await _lobby_channel.fetch_message(data['ping_message_id'])
            await old.delete()
        except:
            pass
    data['ping_message_id'] = None
    save_data(data)
    await update_message()

    # DM subscribers if code changed and notify is True
    if notify and old_code != code:
        for uid in data['subscribers']:
            try:
                user = await bot.fetch_user(uid)
                await user.send(f"🔑 New BG3 party code: `{code}`")
            except discord.Forbidden:
                logger.warning(f"Cannot DM user {uid}")
    return await interaction.response.send_message(f"✅ Code set to **{code}** (Notify subs: {notify})", ephemeral=True)
    if not (code.isalnum() and len(code) == 14):
        return await interaction.response.send_message("❌ Code must be 14 alphanumeric (e.g. 72ARXMVCQG35Z6).", ephemeral=True)
    old_code = data.get('code')
    data['code'] = code
    data['timestamp'] = int(datetime.now().timestamp())
    data['party_info'] = data.get('party_info')
    if data.get('ping_message_id'):
        try:
            old = await _lobby_channel.fetch_message(data['ping_message_id'])
            await old.delete()
        except:
            pass
    data['ping_message_id'] = None
    save_data(data)
    await update_message()

    # DM subscribers if code changed
    if old_code != code:
        for uid in data['subscribers']:
            try:
                user = await bot.fetch_user(uid)
                await user.send(f"🔑 New BG3 party code: `{code}`")
            except discord.Forbidden:
                logger.warning(f"Cannot DM user {uid}")
    await interaction.response.send_message(f"✅ Code set to **{code}**", ephemeral=True)

@code_group.command(name="info", description="Get the current BG3 multiplayer connection code and party status")
async def code_info(interaction: discord.Interaction):
    code = data.get('code') or 'None'
    party = data.get('party_info')
    ts = data.get('timestamp')
    lines = [f"🔑 **Code:** `{code}`"]
    if party:
        try:
            curr, max_ = map(int, party.split('/'))
            avail = "✅" if curr < max_ else "❌"
        except:
            avail = ''
        lines.append(f"👥 **Party:** {party} ({avail})")
    else:
        lines.append("👥 **Party:** N/A")
    if ts:
        lines.append(f"⏰ **Last updated:** <t:{ts}:f>")
    # Subscription status hint
    if interaction.user.id in data['subscribers']:
        lines.append("🔔 You are subscribed to code updates. Use `/party unsubscribe` to opt out.")
    else:
        lines.append("ℹ️ Use `/party subscribe` to receive DMs when the code changes.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@code_group.command(name="clear", description="Clear the current BG3 multiplayer connection code")
async def code_clear(interaction: discord.Interaction):
    if interaction.user.id != USER_ID:
        return await interaction.response.send_message("❌ Only the host can clear the code.", ephemeral=True)
    data['code'] = None
    data['timestamp'] = int(datetime.now().timestamp())
    data['party_info'] = None
    if data.get('ping_message_id'):
        try:
            old = await _lobby_channel.fetch_message(data['ping_message_id'])
            await old.delete()
        except:
            pass
    data['ping_message_id'] = None
    save_data(data)
    await update_message()
    await interaction.response.send_message("✅ Code cleared.", ephemeral=True)

# Presence event
@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if after.id != USER_ID:
        return

    # Parse current and previous party info
    new_party = parse_party_info(after.activities)
    old_party = data.get('party_info')

    # If party info changed (join, leave, or size change)
    if new_party != old_party:
        # Update stored party info and timestamp
        data['party_info'] = new_party
        data['timestamp'] = int(datetime.now().timestamp())

        # If party ended, clear the code and any pending ping
        if new_party is None:
            data['code'] = None
            if data.get('ping_message_id'):
                try:
                    old_ping = await _lobby_channel.fetch_message(data['ping_message_id'])
                    await old_ping.delete()
                except:
                    pass
                data['ping_message_id'] = None

        # Only send a ping when the party first appears AND no code is set
        if new_party and old_party is None and data.get('code') is None:
            if _lobby_perms.send_messages:
                ping = await _lobby_channel.send(
                    f"<@{USER_ID}> please set code via `/party set` (Party: {new_party})"
                )
                data['ping_message_id'] = ping.id

        save_data(data)
        # Finally, refresh the embed message
        await update_message()

# New top-level status command
@bot.tree.command(name="bg3lb_status", description="Show host, subscribers, and current host activity")
async def bg3lb_status(interaction: discord.Interaction):
    # Host info
    try:
        host = await bot.fetch_user(USER_ID)
        host_mention = host.mention
    except Exception:
        host_mention = f"<@{USER_ID}>"

    # Subscribers list
    subs = data.get('subscribers', [])
    subs_list = ", ".join(f"<@{u}>" for u in subs) if subs else "None"

    # Host activity
    activity = "None"
    guild = _lobby_channel.guild if _lobby_channel else None
    if guild:
        member = guild.get_member(USER_ID)
        if member:
            acts = [getattr(a, 'name', str(a)) for a in member.activities if type(a) == discord.Activity]
            if acts:
                activity = ", ".join(acts)

    content = (
        f"🔰 **Host:** {host_mention}\n"
        f"👥 **Subscribers:** {subs_list}\n"
        f"🎮 **Host activities:** {activity}\n"
        f"📋 **Lobby channel:** <#{CHANNEL_ID}>\n"
        f"🔗 **Current code:** `{data.get('code') or 'None'}`\n"
        f"👥 **Party info:** {data.get('party_info') or 'N/A'}\n"   
        f"⏰ **Code last updated:** <t:{data.get('timestamp')}:f>\n"   
        f"🤖 **Version number:** {BOT_VERSION}\n"  
    )
    await interaction.response.send_message(content, ephemeral=True)

# Bot ready
@bot.event
async def on_ready():
    global _lobby_channel, _lobby_perms
    logger.info(f"Logged in as {bot.user} ({bot.user.id})")

    bot.tree.add_command(code_group)
    synced = await bot.tree.sync()
    logger.info(f"Synced {len(synced)} command(s).")

    _lobby_channel = bot.get_channel(CHANNEL_ID)
    if _lobby_channel:
        _lobby_perms = _lobby_channel.permissions_for(_lobby_channel.guild.me)
        logger.info(f"Cached lobby channel and permissions: {_lobby_perms}")

    await update_message()

if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
