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

    # CHECKED AGAINST EVERY URL THIS REQUEST COULD HONESTLY HAVE ARRIVED AT.
    #
    # Twilio signs the URL IT was configured with. We reconstruct ours from
    # the request, and behind a proxy the two can differ by a scheme, a port,
    # or a trailing slash while describing the same call. When they do, the
    # HMAC fails, we return 403, and Twilio records error 11200 -- which is
    # what happened to every customer reply: the message reached Twilio, our
    # endpoint refused it, and the reply was lost.
    #
    # This does NOT weaken the check. Every candidate is still verified by
    # HMAC under the account token; we are only allowing for the fact that we
    # do not know which spelling of our own address Twilio holds.
    from urllib.parse import urlparse

    body = "".join(f"{k}{params[k]}" for k in sorted(params)
                   if params[k] is not None)

    tried = [url]
    if url.endswith("/"):
        tried.append(url[:-1])
    else:
        tried.append(url + "/")

    # getattr, because the tests replace settings with a bare namespace
    # carrying only the token, and a missing attribute must not turn a
    # signature check into an exception.
    base = (getattr(settings, "public_ws_base", "") or "").replace(
        "wss://", "https://").rstrip("/")
    if base:
        try:
            tried.append(base + urlparse(url).path)
        except Exception:
            pass

    for candidate in tried:
        try:
            want = base64.b64encode(
                hmac.new(token.encode(), (candidate + body).encode("utf-8"),
                         hashlib.sha1).digest()).decode()
            if hmac.compare_digest(want, signature):
                return True
        except Exception:
            continue

    print(f"[whatsapp] signature did not match any spelling of this address; "
          f"tried {tried}", flush=True)
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


def send(to: str, text: str) -> dict:
    """Start a conversation, rather than answer one.

    Every other WhatsApp path here is a reply: Twilio posts an inbound message
    and the answer rides back in the TwiML. A follow-up has nobody to reply
    to, so it goes out through the REST API like an SMS does.

    Meta charges nothing for a free-form message inside the twenty-four hours
    after a customer messaged us, which is exactly when a dropped-call resume
    goes out. Outside that window it needs an approved template, which this
    deployment does not have, so it will simply fail and the sender will try
    the next channel.

    Args:
        to: E.164 number, without the whatsapp: prefix.
        text: the message, already assembled from facts.
    """
    from .outbound import _post, configured as _twilio_ready

    if not _twilio_ready() or not to or not text:
        return {"ok": False, "why": "no credentials, number or message"}

    # The WhatsApp sender, which is NOT necessarily our phone number. On the
    # sandbox it is Twilio's shared number; with an approved Business sender it
    # is ours. Building it from twilio_from assumed the second case and broke
    # the first, silently, on the reply rather than on the receipt.
    # Falls back to the voice number here as well as in config, because
    # settings is swappable and a fallback that lives in only one of the two
    # places is a fallback that disappears when somebody substitutes the
    # object. An approved Business sender IS our own number, so the fallback
    # is correct rather than merely defensive.
    sender = (getattr(settings, "twilio_whatsapp_from", "")
              or getattr(settings, "twilio_from", "") or "")
    if not sender:
        return {"ok": False,
                "why": "no WhatsApp sender configured. Set "
                       "TWILIO_WHATSAPP_FROM to the sandbox number "
                       "(+14155238886) or to an approved Business sender. "
                       "Our voice number is not one unless Meta has approved "
                       "it."}

    out = _post("Messages.json", {
        "To": f"whatsapp:{_number(to)}",
        "From": f"whatsapp:{sender}",
        "Body": text[:1500],
    })
    if out.get("ok"):
        return {"ok": True, "sid": out["response"].get("sid"), "to": to}
    return out


def handle(from_number: str, body: str = "",
           media: list[tuple[bytes, str]] | None = None,
           to_number: str = "") -> str:
    """One inbound WhatsApp message, handed straight to the desk.

    Args:
        from_number: the sender, with or without Twilio's whatsapp: prefix.
        body: what they typed.
        media: attachments already downloaded, as (bytes, content type).
        to_number: the number they messaged. This is how the desk knows whose
            business they reached, and it was being discarded: only `From` and
            the body were read, so a first-time customer of the OTHER dealer
            was served as a refrigeration customer.
    """
    return desk.answer(_number(from_number), body, media,
                       channel="whatsapp", dialled=_number(to_number))
