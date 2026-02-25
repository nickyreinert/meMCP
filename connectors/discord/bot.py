import logging
import os

import discord
import httpx

PROXY_URL = os.getenv("PROXY_URL", "http://localhost:8001")
SECRET = os.getenv("PROXY_SECRET", "")
TOKEN = os.getenv("DISCORD_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True          # required: enable in Developer Portal
intents.dm_messages = True
client = discord.Client(intents=intents)


def call_proxy(chat_id: str, message: str) -> dict:
    with httpx.Client() as http:
        r = http.post(
            f"{PROXY_URL}/chat",
            json={"chat_id": chat_id, "message": message},
            headers={"X-Proxy-Secret": SECRET},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


@client.event
async def on_ready():
    log.info("Discord bot ready as %s (id=%s)", client.user, client.user.id)


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = client.user in message.mentions

    if not (is_dm or is_mention):
        return

    text = message.content
    if is_mention:
        text = text.replace(f"<@{client.user.id}>", "").strip()
    if not text:
        return

    chat_id = f"discord_{message.author.id}"
    log.info("message from %s: %r", chat_id, text[:60])

    async with message.channel.typing():
        try:
            data = call_proxy(chat_id, text)
            reply = data["reply"]
            starters = data.get("starters", [])
        except httpx.HTTPStatusError as e:
            reply = f"Proxy error {e.response.status_code}: {e.response.text}"
            starters = []
        except Exception as e:
            log.exception("proxy call failed")
            reply = f"Error: {e}"
            starters = []

    if starters:
        bullet_list = "\n".join(f"• {s}" for s in starters)
        await message.channel.send(f"{reply}\n\n**Try asking:**\n{bullet_list}")
    else:
        await message.channel.send(reply)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set")
    log.info("Discord bot starting…")
    client.run(TOKEN)


if __name__ == "__main__":
    main()
