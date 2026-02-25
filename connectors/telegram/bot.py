import logging
import os

import httpx
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

PROXY_URL = os.getenv("PROXY_URL", "http://localhost:8001")
SECRET = os.getenv("PROXY_SECRET", "")
TOKEN = os.getenv("TELEGRAM_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def call_proxy(chat_id: str, message: str) -> dict:
    with httpx.Client() as client:
        r = client.post(
            f"{PROXY_URL}/chat",
            json={"chat_id": chat_id, "message": message},
            headers={"X-Proxy-Secret": SECRET},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = f"tg_{update.effective_chat.id}"
    text = update.message.text or ""
    log.info("message from %s: %r", chat_id, text[:60])
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
        # Pair starters into rows of 2 for a compact keyboard
        rows = [[KeyboardButton(s) for s in starters[i:i + 2]] for i in range(0, len(starters), 2)]
        keyboard = ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(reply, reply_markup=keyboard)
    else:
        await update.message.reply_text(reply)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    log.info("Telegram bot starting (polling)…")
    app.run_polling()


if __name__ == "__main__":
    main()
