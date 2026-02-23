import logging
import os

import httpx
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

PROXY_URL = os.getenv("PROXY_URL", "http://localhost:8001")
SECRET = os.getenv("PROXY_SECRET", "")
TOKEN = os.getenv("TELEGRAM_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = f"tg_{update.effective_chat.id}"
    text = update.message.text or ""
    log.info("message from %s: %r", chat_id, text[:60])
    try:
        reply = call_proxy(chat_id, text)
    except httpx.HTTPStatusError as e:
        reply = f"Proxy error {e.response.status_code}: {e.response.text}"
    except Exception as e:
        log.exception("proxy call failed")
        reply = f"Error: {e}"
    await update.message.reply_text(reply)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot starting (polling)…")
    app.run_polling()


if __name__ == "__main__":
    main()
