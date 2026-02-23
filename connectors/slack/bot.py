import logging
import os

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

PROXY_URL = os.getenv("PROXY_URL", "http://localhost:8001")
SECRET = os.getenv("PROXY_SECRET", "")
BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")  # xapp-… for Socket Mode

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

app = App(token=BOT_TOKEN)


def call_proxy(chat_id: str, message: str) -> str:
    with httpx.Client() as client:
        r = client.post(
            f"{PROXY_URL}/chat",
            json={"chat_id": chat_id, "message": message},
            headers={"X-Proxy-Secret": SECRET},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["reply"]


@app.event("message")
def handle_message(event, say, logger):
    # Only handle direct messages (channel_type "im") to keep things simple.
    # Remove this check if you want the bot to respond in channels too.
    if event.get("channel_type") != "im":
        return
    # Ignore bot's own messages (subtype "bot_message")
    if event.get("subtype"):
        return

    user_id = event.get("user", "unknown")
    text = event.get("text", "")
    chat_id = f"slack_{user_id}"
    log.info("message from %s: %r", chat_id, text[:60])

    try:
        reply = call_proxy(chat_id, text)
    except httpx.HTTPStatusError as e:
        reply = f"Proxy error {e.response.status_code}: {e.response.text}"
    except Exception as e:
        log.exception("proxy call failed")
        reply = f"Error: {e}"

    say(reply)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN is not set")
    if not APP_TOKEN:
        raise RuntimeError("SLACK_APP_TOKEN is not set")
    log.info("Slack bot starting (Socket Mode)…")
    SocketModeHandler(app, APP_TOKEN).start()


if __name__ == "__main__":
    main()
