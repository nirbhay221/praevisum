"""Point a Telegram bot at this server, once.

    .venv/Scripts/python.exe scripts/setup_telegram.py

Needs two things in .env:

    TELEGRAM_BOT_TOKEN        from BotFather: /newbot, answer two questions
    TELEGRAM_WEBHOOK_SECRET   invent one, any string; Telegram echoes it back
                              on every request and it is what proves the call
                              really came from them
    PUBLIC_WS_BASE            already set for Twilio, reused here

Telegram will not deliver to plain HTTP, so the server has to be reachable
over HTTPS. That is already true of this deployment because Twilio requires
the same thing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import telegram  # noqa: E402
from src.config import settings  # noqa: E402


def main() -> None:
    if not telegram.configured():
        print("  no TELEGRAM_BOT_TOKEN in .env")
        print("  open Telegram, message @BotFather, send /newbot")
        return

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        print("  no TELEGRAM_WEBHOOK_SECRET in .env")
        print("  invent any string and put it there; without it the webhook")
        print("  refuses every request, which is deliberate")
        return

    base = (settings.public_ws_base or "").replace("wss://", "https://")
    if not base:
        print("  no PUBLIC_WS_BASE in .env, so there is no address to give them")
        return

    me = telegram._call("getMe").get("result") or {}
    if not me:
        print("  that token was refused by Telegram")
        return
    print(f"  bot: @{me.get('username')} ({me.get('first_name')})")

    out = telegram._call("setWebhook", url=f"{base}/telegram",
                         secret_token=secret,
                         allowed_updates='["message","edited_message"]')
    if not out.get("ok"):
        print(f"  setWebhook failed: {out.get('description') or out}")
        return

    print(f"  webhook set to {base}/telegram")

    info = telegram._call("getWebhookInfo").get("result") or {}
    if info.get("last_error_message"):
        print(f"  last error from Telegram: {info['last_error_message']}")
    print(f"  pending updates: {info.get('pending_update_count', 0)}")
    print()
    print(f"  message @{me.get('username')} and send a photo of any data plate")
    print("  technicians send /link followed by their mobile number, once")


if __name__ == "__main__":
    main()
