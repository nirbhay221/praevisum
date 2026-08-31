"""Actually reaching somebody. The last mile that did not exist.

WHAT WAS ALREADY THERE, AND WHY IT WAS NOT ENOUGH

`sweep_recalls` finds customers who own a machine under an active federal
safety recall. `queue_outreach` puts them at priority 10, above every
prediction and every offer, because an electrocution notice outranks a
discount absolutely. `take_next` claims the highest-priority call that is due,
inside quiet hours, and hands over an opening line written by a person rather
than a model.

Then nothing rang anybody.

A safety recall was correctly identified, correctly prioritised, and left in a
queue. That is worse than not having the sweep at all, because the system
reports having handled it.

WHAT THIS REFUSES TO DO

It will not place a call or send a message unless the queue said to. Every
consent rule, every quiet-hours check and every frequency cap lives in
outreach.py and has already run by the time anything here executes. This file
does not get to decide who is worth ringing; it only knows how to ring.

And it will not dial without the disclosure. An AI voice is an artificial or
prerecorded voice under the TCPA, several states require the caller to be told
before anything else, and it is the right thing regardless. The opening line
that carries it comes from the queue, not from here and not from a model.

WHY OUTBOUND VOICE IS A DIFFERENT SHAPE FROM INBOUND

Inbound, Twilio calls us and we answer a websocket. Outbound, we ask Twilio to
place a call and tell it where to fetch instructions. Same media stream at the
other end, different direction of the first request, and one extra thing to
carry: which queued item this call is, so the agent knows why it is ringing
somebody who did not ring us.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request

from .config import settings

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"

# Long enough for somebody to reach a phone in a kitchen, short enough not to
# sit in a voicemail box announcing itself.
RING_SECONDS = 25


def configured() -> bool:
    """Whether a call could actually be placed, rather than merely attempted.

    THE NUMBER WE RING FROM COUNTS AS CONFIGURATION.

    This checked the account, the token and the public URL, and not the From
    number, so it answered True on a deployment that had no From at all. Every
    call it then reported as ready died at Twilio with error 21603, "no From",
    which is a message about a field rather than about a missing deployment
    step, and the queue looked healthy the whole time.

    Found on the live VM: credentials and public URL present, TWILIO_FROM
    absent, and configured() saying yes. A readiness check that is optimistic
    about its own deployment is the same failure this project keeps finding
    in other places, so it is spelled out here.
    """
    return bool(settings.twilio_account_sid and settings.twilio_auth_token
                and settings.public_ws_base and settings.twilio_from)


def _post(path: str, fields: dict) -> dict:
    """One Twilio REST call. Returns what happened rather than raising.

    Nothing here is allowed to take down the sweep that called it: a queue
    entry that could not be dialled is a queue entry that stays queued, which
    is recoverable, and an exception in a nightly job is not.
    """
    sid = settings.twilio_account_sid
    auth = base64.b64encode(
        f"{sid}:{settings.twilio_auth_token}".encode()).decode()
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        f"{TWILIO_API}/{sid}/{path}", data=body,
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"ok": True, "response": json.loads(r.read().decode())}
    except Exception as e:
        detail = ""
        if hasattr(e, "read"):
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
        print(f"[outbound] {path} failed: {type(e).__name__}: {e} {detail}",
              flush=True)
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def send_sms(to: str, body: str, from_number: str = "") -> dict:
    """A message to a technician or a customer.

    The briefing goes out this way, and the technician replies to the same
    thread to close the job, which is the loop that makes the corpus grow.

    Args:
        to: E.164 number.
        body: the message, already assembled from facts.
        from_number: which line it comes from. The dealer's, if omitted.
    """
    if not configured():
        return {"ok": False, "why": "no Twilio credentials on this deployment"}
    if not to or not body:
        return {"ok": False, "why": "nothing to send"}

    out = _post("Messages.json", {
        "To": to,
        "From": from_number or settings.twilio_from or "",
        "Body": body[:1500],
    })
    if out.get("ok"):
        return {"ok": True, "sid": out["response"].get("sid"), "to": to}
    return out


def place_call(to: str, outreach_id: str, from_number: str = "") -> dict:
    """Ring somebody the queue decided was worth ringing.

    Deliberately takes an outreach id rather than a reason. Everything about
    whether this call should happen was settled by outreach.py before this ran,
    and re-deciding it here would put the consent rules in two places.

    Args:
        to: E.164 number.
        outreach_id: the queued item, carried so the agent knows why it rang.
        from_number: which line it comes from.
    """
    if not configured():
        return {"ok": False, "why": "no Twilio credentials on this deployment"}
    if not to or not outreach_id:
        return {"ok": False, "why": "no number or no queued reason"}

    base = settings.public_ws_base.replace("wss://", "https://")
    out = _post("Calls.json", {
        "To": to,
        "From": from_number or settings.twilio_from or "",
        # Twilio fetches the instructions from us when the call connects, so
        # the outreach id travels in the URL rather than in the request.
        "Url": f"{base}/outbound-voice?outreach={urllib.parse.quote(outreach_id)}",
        "StatusCallback": f"{base}/call-status",
        "Timeout": str(RING_SECONDS),
        # No voicemail. An unattended AI voice leaving a recorded message about
        # a safety recall, to a machine nobody may check, is worse than ringing
        # again later.
        "MachineDetection": "Enable",
    })
    if out.get("ok"):
        return {"ok": True, "sid": out["response"].get("sid"), "to": to,
                "outreach_id": outreach_id}
    return out


def run_queue(dealer_id: str = "D-REF", limit: int = 5) -> dict:
    """Dial what is due, in priority order, and stop.

    The consumer the queue never had. Recalls come first because the priority
    ordering says so, not because anything here knows what a recall is.

    Args:
        dealer_id: whose queue.
        limit: how many to place in one pass, so a sweep cannot empty the
            whole queue into somebody's evening.
    """
    from .outreach import record_outcome, take_next

    placed, skipped = [], []
    for _ in range(max(1, limit)):
        claimed = take_next(dealer_id)
        call = claimed.get("call")
        if not call:
            break

        if not call.get("phone"):
            record_outcome(call["outreach_id"], "wrong_number",
                           "no number on the account")
            skipped.append({"outreach_id": call["outreach_id"],
                            "why": "no number on file"})
            continue

        out = place_call(call["phone"], call["outreach_id"])
        if out.get("ok"):
            placed.append({"outreach_id": call["outreach_id"],
                           "kind": call["kind"], "to": call["phone"],
                           "sid": out["sid"]})
        else:
            # Left claimed rather than marked done. A call that could not be
            # placed is not a call that was made, and a recall that silently
            # counts as handled is the failure this whole file exists to fix.
            skipped.append({"outreach_id": call["outreach_id"],
                            "why": out.get("why")})

    return {
        "placed": len(placed), "skipped": len(skipped),
        "calls": placed, "not_placed": skipped,
        "say": ("Nothing here decided who to ring. Consent, quiet hours and "
                "the frequency cap all ran in outreach.py before this did."),
    }

