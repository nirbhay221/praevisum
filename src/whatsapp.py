"""WhatsApp, as a door onto the desk. An adapter, not a second product.

WHAT THIS FILE IS ALLOWED TO DO

Three things: prove a request really came from Twilio, download whatever it
carried, and hand `(identity, text, media)` to `desk.answer`. Everything that
decides anything lives in desk.py, because a customer who is told here that a
part is in stock and told on the phone that it is not has caught the desk
lying, and it does not matter which answer was right.

WHY WHATSAPP FIRST

Because it carries a PHOTOGRAPH. `identify_equipment` says why that matters: a
model number never arrives clean, so it matches on a normalised form, then by
prefix, then by containment, because exact matching would fail on almost every
real call. A picture of the rating plate skips all three, and it is the single
most error-prone thing a customer is ever asked to do.

IDENTITY IS THE PHONE NUMBER

Twilio prefixes the sender with `whatsapp:` and the rest of the system stores
E.164, so the prefix comes off here rather than anywhere downstream. That is
what lets a technician reply from WhatsApp and be recognised as the same
person who would have replied by text.

FAIL CLOSED

The signature check is the second security boundary in this project and it is
written with the first one's mistake in mind. The stream ticket originally
keyed its HMAC on `settings.twilio_auth_token`, which is an empty string on
the live machine, so every ticket verified against an empty key and the socket
was open to the world while every test passed.

So there is no path here where missing configuration means an open endpoint.
No auth token means requests are refused, and the only way past that is an
explicit PRAEVISUM_OPEN_WHATSAPP=1 that somebody has to type on purpose.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import urllib.request

from . import desk
from .config import settings

MEDIA_TIMEOUT = 15

# A rating plate photograph off a phone is well under this. The cap is here so
# a stranger who finds the webhook cannot make the server pull a large file.
MAX_MEDIA_BYTES = 8 * 1024 * 1024

IMAGE_TYPES = desk.IMAGE_TYPES


def configured() -> bool:
    return bool(settings.twilio_auth_token)


def _number(raw: str) -> str:
    """Twilio prefixes WhatsApp senders. The rest of the system stores E.164."""
    return (raw or "").replace("whatsapp:", "").strip()


def signature_ok(url: str, params: dict, signature: str) -> bool:
    """Twilio's request signature, checked in constant time.

    The scheme is theirs: the full request URL, then every POST field appended
    as name immediately followed by value in alphabetical order by name, HMAC
    SHA1 under the account auth token, base64.

    Returns False on anything malformed, and False when no token is held. An
    unsigned endpoint here would let a stranger close another dealer's jobs.
    """
    if os.getenv("PRAEVISUM_OPEN_WHATSAPP") == "1":
        return True
    token = settings.twilio_auth_token
    if not token or not signature:
        return False
    try:
        payload = url + "".join(
            f"{k}{params[k]}" for k in sorted(params) if params[k] is not None)
        want = base64.b64encode(
            hmac.new(token.encode(), payload.encode("utf-8"),
                     hashlib.sha1).digest()).decode()
        return hmac.compare_digest(want, signature)
    except Exception:
        return False


def fetch_media(url: str) -> tuple[bytes, str]:
    """Pull an attachment off Twilio, which needs the account credentials.

    Returns empty on any failure rather than raising. A photo that will not
    download is a photo we ask them to send again, not an error page.
    """
    if not url:
        return b"", ""
    auth = base64.b64encode(
        f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()
    ).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=MEDIA_TIMEOUT) as r:
            return r.read(MAX_MEDIA_BYTES), (r.headers.get("Content-Type") or "")
    except Exception:
        return b"", ""


def handle(from_number: str, body: str = "",
           media: list[tuple[bytes, str]] | None = None) -> str:
    """One inbound WhatsApp message, handed straight to the desk.

    Args:
        from_number: the sender, with or without Twilio's whatsapp: prefix.
        body: what they typed.
        media: attachments already downloaded, as (bytes, content type).
    """
    return desk.answer(_number(from_number), body, media, channel="whatsapp")
