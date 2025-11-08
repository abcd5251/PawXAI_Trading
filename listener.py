# discord_listener.py
import os
import asyncio
import json
import aiohttp
import discord
from dotenv import load_dotenv

# load environment variables from .env if present
load_dotenv()

# Optional: limit to specific channels (replace with your channel IDs)
# Use integers for channel IDs to match Discord's types
# Robust parsing: if CHANNEL_ID is missing/invalid, don't filter (empty set)
_channel_env = os.getenv("CHANNEL_ID")
try:
    TARGET_CHANNEL_IDS = {int(_channel_env)} if _channel_env and _channel_env.strip() else set()
except Exception:
    TARGET_CHANNEL_IDS = set()

# Optional: forward each message to your HTTP endpoint
# Default to container port if FORWARD_URL not set (Render uses $PORT)
FORWARD_URL = os.getenv("FORWARD_URL") or f"http://localhost:{os.getenv('PORT', '8000')}/ingest"

intents = discord.Intents.default()
intents.message_content = True  # REQUIRED to read message content
intents.guilds = True
intents.guild_messages = True  # ensure guild message events are delivered

class MessageListener(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, intents=intents, **kwargs)
        self.http_session = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def on_ready(self):
        print(f"Logged in as {self.user} (id: {self.user.id})")
        print(f"Forward URL: {FORWARD_URL}")
        print(f"Filtering channel IDs: {', '.join(str(cid) for cid in TARGET_CHANNEL_IDS) or 'none'}")
        # Enumerate guilds & channels so you can grab IDs easily
        for guild in self.guilds:
            print(f"Guild: {guild.name} ({guild.id})")
            for ch in guild.channels:
                ch_name = getattr(ch, "name", str(ch))
                print(f" - {ch_name} [{ch.type}] id={ch.id}")




    async def on_message(self, message: discord.Message):
        # Ignore only our own bot to avoid loops; allow other bots/webhooks
        if message.author.id == self.user.id:
            return

        # Filter by channel if configured
        if TARGET_CHANNEL_IDS and message.channel.id not in TARGET_CHANNEL_IDS:
            return

        payload = {
            "guild_id": message.guild.id if message.guild else None,
            "guild_name": message.guild.name if message.guild else None,
            "channel_id": message.channel.id,
            "channel_name": getattr(message.channel, "name", str(message.channel)),
            "message_id": message.id,
            "author_id": message.author.id,
            "author_name": message.author.name,
            "author_display": message.author.display_name,
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "embeds": [e.to_dict() for e in message.embeds],
            "mentions": [u.id for u in message.mentions],
            "created_at": message.created_at.isoformat(),
            "jump_url": message.jump_url,
            "event": "create",
        }

        print(json.dumps(payload, ensure_ascii=False, indent=2))

        # Forward to your API if configured
        if FORWARD_URL and (payload["content"].strip() or payload["embeds"]):
            try:
                async with self.http_session.post(FORWARD_URL, json=payload, timeout=10) as resp:
                    if resp.status >= 300:
                        text = await resp.text()
                        print(f"Forward error {resp.status}: {text}")
            except Exception as e:
                print(f"Forward exception: {e}")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Same filtering for edits; sometimes embeds arrive via edit
        if TARGET_CHANNEL_IDS and after.channel.id not in TARGET_CHANNEL_IDS:
            return

        # Ignore only our own bot's edits
        if after.author.id == self.user.id:
            return

        payload = {
            "guild_id": after.guild.id if after.guild else None,
            "guild_name": after.guild.name if after.guild else None,
            "channel_id": after.channel.id,
            "channel_name": getattr(after.channel, "name", str(after.channel)),
            "message_id": after.id,
            "author_id": after.author.id,
            "author_name": after.author.name,
            "author_display": after.author.display_name,
            "content": after.content,
            "attachments": [a.url for a in after.attachments],
            "embeds": [e.to_dict() for e in after.embeds],
            "mentions": [u.id for u in after.mentions],
            "created_at": after.created_at.isoformat(),
            "jump_url": after.jump_url,
            "event": "edit",
        }

        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if FORWARD_URL and (payload["content"].strip() or payload["embeds"]):
            try:
                async with self.http_session.post(FORWARD_URL, json=payload, timeout=10) as resp:
                    if resp.status >= 300:
                        text = await resp.text()
                        print(f"Forward error {resp.status}: {text}")
            except Exception as e:
                print(f"Forward exception: {e}")

async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN env var to your bot token.")

    client = MessageListener()
    await client.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass