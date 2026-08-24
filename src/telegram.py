"""Telegram, as a second door onto the same desk.

WHY A SECOND DOOR AT ALL

Because customers pick the channel and we do not. Somebody who lives in one
app is not going to install another because a refrigeration dealer would
prefer it, and a door that is not there is just a customer who could not
reach us.

The cost of a door is only low because desk.py exists. This file decides
nothing. It proves the request came from Telegram, downloads whatever it
carried, hands `(identity, text, media)` to the desk, and sends the reply
back. Every rule about what the desk will and will not say is one file over,
and shared, which is the only reason two channels cannot contradict each
other.

WHAT TELEGRAM ADDS THAT WHATSAPP DOES NOT

Nothing, for a customer. It is the same modalities: words and a photograph.
What it adds is that it costs nothing at all, needs no business verification,
no template approval and no join code, so it works the moment a token exists.
WhatsApp is the channel a US kitchen actually uses; this is the one that is
guaranteed to work when it is needed.

IDENTITY IS A PROBLEM HERE, AND IT IS NOT PRETENDED OTHERWISE

Telegram does not give out phone numbers. A chat id is not a phone number, so
a technician messaging from Telegram is NOT recognised as themselves the way
they are on WhatsApp, and their reply cannot close a job.

The honest options were to guess by name, or to make people link their account
once. Guessing loses: a wrong match writes a repair against another
technician's visit and corrupts the corpus that every future briefing is built
from. So a chat id is matched only against a link somebody actually made, and
an unlinked sender is treated as a customer, which is both true and safe.

SECURITY

Telegram authenticates a webhook with a secret token of our choosing, sent
back in a header on every request. Same rule as everywhere else in this
project: absent configuration means the endpoint is closed, never open.
"""

from __future__ import annotations

import hmac
import json
import os
import urllib.parse
import urllib.request

from . import db, desk

API = "https://api.telegram.org"

MEDIA_TIMEOUT = 15

# Telegram allows a bot to download files up to 20MB. A rating plate is far
# under that, and the cap stops a stranger making the server pull a large one.
MAX_MEDIA_BYTES = 8 * 1024 * 1024

# Telegram sends several sizes of every photo, smallest first. The largest is
# the last, and it is the only one worth reading characters off a sticker from.
LARGEST = -1


def token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def configured() -> bool:
    return bool(token())


def secret_ok(header_value: str) -> bool:
    """Is this really Telegram calling?

    Telegram echoes a secret of our choosing in a header on every webhook
    request. Absent configuration closes the endpoint rather than opening it,
    which is the rule the stream socket taught this project the hard way.
    """
    if os.getenv("PRAEVISUM_OPEN_TELEGRAM") == "1":
        return True
    want = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not want or not header_value:
        return False
    return hmac.compare_digest(want, header_value)


def _call(method: str, **params) -> dict:
    """One Bot API call. Returns an empty dict rather than raising."""
    url = f"{API}/bot{token()}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=MEDIA_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def fetch_photo(file_id: str) -> tuple[bytes, str]:
    """Download a photo in the two steps the Bot API requires.

    getFile returns a path, and the file itself lives on a different host. The
    link is guaranteed valid for at least an hour, which is far longer than
    this needs.
    """
    if not file_id or not token():
        return b"", ""

    got = _call("getFile", file_id=file_id)
    path = ((got.get("result") or {}).get("file_path") or "")
    if not path:
        return b"", ""

    try:
        with urllib.request.urlopen(f"{API}/file/bot{token()}/{path}",
                                    timeout=MEDIA_TIMEOUT) as r:
            blob = r.read(MAX_MEDIA_BYTES)
    except Exception:
        return b"", ""

    # Telegram serves photos as .jpg and does not always set a useful type, so
    # the extension is the more reliable signal here.
    ext = path.rsplit(".", 1)[-1].lower()
    return blob, {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")


def send(chat_id: int | str, text: str) -> bool:
    return bool(_call("sendMessage", chat_id=chat_id, text=text).get("ok"))


def _identity(chat_id: int | str) -> str:
    """Who this chat belongs to, as the rest of the system understands identity.

    A linked chat resolves to the technician's phone number, so their reply
    closes a job exactly as it would from WhatsApp. An unlinked one resolves
    to the chat id, which is a customer as far as the desk is concerned.

    Nothing is inferred from a display name. Matching "Curtis" to a technician
    called Curtis would write a repair against somebody else's visit and
    corrupt the corpus every future briefing is built from, and it would do it
    silently.
    """
    with db.connect() as c:
        row = c.execute(
            "SELECT phone FROM channel_links WHERE channel='telegram' AND handle=?",
            (str(chat_id),)).fetchone()
    return row["phone"] if row else f"telegram:{chat_id}"


def link(chat_id: int | str, phone: str) -> dict:
    """Tie a Telegram chat to a phone number already on file.

    Deliberately refuses a number that is not a technician's. This is the only
    way to become a technician on this channel, so it is the one place worth
    being strict: an open version would let anybody close another dealer's
    jobs by claiming a number.
    """
    phone = (phone or "").strip()
    with db.connect() as c:
        tech = c.execute("SELECT id, name FROM technicians WHERE phone=?",
                         (phone,)).fetchone()
    if tech is None:
        return {"ok": False, "why": "that number is not a technician on file"}

    with db.txn() as c:
        c.execute(
            """INSERT INTO channel_links (channel, handle, phone)
               VALUES ('telegram', ?, ?)
               ON CONFLICT(channel, handle) DO UPDATE SET phone=excluded.phone""",
            (str(chat_id), phone))
    return {"ok": True, "technician": tech["name"]}


def handle(update: dict) -> tuple[str, str] | None:
    """One Telegram update. Returns (chat_id, reply) or None if there is nothing.

    Args:
        update: the JSON body Telegram posted.
    """
    message = (update or {}).get("message") or (update or {}).get("edited_message")
    if not message:
        return None

    chat_id = str(((message.get("chat") or {}).get("id") or "")).strip()
    if not chat_id:
        return None

    text = (message.get("caption") or message.get("text") or "").strip()

    # The one command this channel needs. Everything else is a conversation.
    if text.lower().startswith("/start"):
        return chat_id, ("This is an automated assistant for the service desk. "
                         "Send a photo of the rating plate, or tell me the "
                         "model number and what the machine is doing.")
    if text.lower().startswith("/link"):
        parts = text.split()
        out = link(chat_id, parts[1] if len(parts) > 1 else "")
        return chat_id, (f"Linked, thanks {out['technician'].split()[0]}."
                         if out["ok"] else
                         "Send /link followed by the mobile number we have on "
                         "file for you.")

    media = []
    sizes = message.get("photo") or []
    if sizes:
        blob, mime = fetch_photo((sizes[LARGEST] or {}).get("file_id", ""))
        if blob:
            media.append((blob, mime))

    return chat_id, desk.answer(_identity(chat_id), text, media,
                                channel="telegram")
