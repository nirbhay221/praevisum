"""Sending email, and knowing which kind of email it is.

WHY EMAIL AT ALL, WHEN THERE IS A PHONE LINE

Two reasons, and the second is the real one.

A2P 10DLC. US business SMS has to be registered with the carriers, and this
number is not, so every SMS reply the desk has ever sent to a US mobile came
back error 30034, undelivered. On 28 August a customer texted in, the desk
answered, and the carrier dropped it. The conversation looked one-sided to
them and complete to us.

AND A TECHNICIAN IS NOT A CUSTOMER. `desk.py` routes on whether the sender is
in the technicians table, which means one phone number cannot be both. Email
gives the crew an identity of their own, so a briefing can go out and be
replied to without competing with the customer channel.

THE LINE THAT DECIDES THE RULES

CAN-SPAM exempts transactional and relationship messages: an ongoing
transaction, an employment relationship, an account update. It does not exempt
anything whose primary purpose is promoting the business, and a message that
MIXES the two is judged on its primary purpose.

    briefing to an engineer      employment relationship   exempt
    "your part arrives Tuesday"  ongoing transaction       exempt
    "would you leave a review"   promoting the business    NOT exempt

So a commercial message must carry a working unsubscribe and a real physical
postal address, and an opt-out has to be honoured within 10 business days.
This module refuses to send a commercial message without both, rather than
trusting whoever wrote the copy to remember.

That is the same line outreach.py already draws between a safety recall and an
offer, through a different door.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings

# What a message is FOR, which decides what it must carry.
TRANSACTIONAL = "transactional"
COMMERCIAL = "commercial"

# A physical postal address is not optional on a commercial message, and a
# blank one is worse than none because it looks compliant.
MIN_ADDRESS = 12


def configured() -> bool:
    return bool(getattr(settings, "smtp_host", "")
                and getattr(settings, "smtp_user", "")
                and getattr(settings, "smtp_password", "")
                and getattr(settings, "email_from", ""))


def _looks_like_an_address(text: str) -> bool:
    """Enough of a postal address to be one.

    Deliberately crude. The point is to catch a blank or a placeholder, not to
    validate deliverability, and a stricter check would reject real addresses
    in formats nobody anticipated.
    """
    t = (text or "").strip()
    return len(t) >= MIN_ADDRESS and any(ch.isdigit() for ch in t)


def check_before_sending(kind: str, body: str, unsubscribe: str,
                         postal_address: str) -> dict | None:
    """Whether this message may be sent. None means yes.

    Separated from the sending so the rule can be tested without an SMTP
    server, and so it reads as a rule rather than as plumbing.
    """
    if kind == TRANSACTIONAL:
        return None

    if kind != COMMERCIAL:
        return {"blocked": True,
                "why": f"{kind!r} is not a kind of message this can send. It "
                       "is transactional or commercial, and the difference "
                       "decides what the law requires."}

    missing = []
    if not (unsubscribe or "").strip():
        missing.append("a working unsubscribe")
    if not _looks_like_an_address(postal_address):
        missing.append("a real physical postal address")

    if missing:
        return {
            "blocked": True,
            "why": ("A commercial message needs " + " and ".join(missing)
                    + ". CAN-SPAM exempts transactional and relationship mail "
                      "and does not exempt anything promoting the business."),
            "do_this": ("If this is genuinely transactional, send it as "
                        "transactional and say why. If it is promotion, it "
                        "carries the unsubscribe and the address or it does "
                        "not go."),
        }

    return None


def send(to: str, subject: str, body: str, kind: str = TRANSACTIONAL,
         unsubscribe: str = "", postal_address: str = "",
         reply_to: str = "") -> dict:
    """Send one email, refusing anything the law would not permit.

    Args:
        to: the recipient.
        subject: the subject line.
        body: plain text. No HTML: a service message is not a newsletter.
        kind: "transactional" or "commercial". See the module docstring.
        unsubscribe: required for commercial. A URL or an email address.
        postal_address: required for commercial. A real postal address.
        reply_to: where a reply should go, if not the sending address.
    """
    stop = check_before_sending(kind, body, unsubscribe, postal_address)
    if stop is not None:
        return {"ok": False, **stop}

    if not configured():
        return {"ok": False,
                "why": "no mail credentials on this deployment. Set "
                       "SMTP_HOST, SMTP_USER, SMTP_PASSWORD and EMAIL_FROM."}
    if not (to or "").strip() or "@" not in to:
        return {"ok": False, "why": "not an email address"}

    text = body
    if kind == COMMERCIAL:
        # Appended here rather than trusted to the caller's copy. A footer
        # that is sometimes present is a footer that will be missing on the
        # message somebody complains about.
        text = (f"{body}\n\n--\n"
                f"To stop receiving these: {unsubscribe}\n"
                f"{postal_address}\n")

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    if kind == COMMERCIAL and unsubscribe:
        # The header mail clients render as a one-click unsubscribe, which is
        # a better opt-out than a link buried in a footer.
        msg["List-Unsubscribe"] = (f"<{unsubscribe}>"
                                   if unsubscribe.startswith("http")
                                   else f"<mailto:{unsubscribe}>")
    msg.set_content(text)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                          timeout=20) as s:
            s.starttls(context=ctx)
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception as e:
        print(f"[email] could not send to {to}: {type(e).__name__}: {e}",
              flush=True)
        return {"ok": False, "why": f"the mail server refused it: {e}"}

    return {"ok": True, "to": to, "kind": kind}


def brief_the_engineer(technician_email: str, brief: dict) -> dict:
    """Send a technician their briefing by email.

    An employment relationship, so transactional: no unsubscribe, no postal
    address, and none is added. Adding one would be worse than useless, since
    an engineer cannot opt out of being told where the job is.

    Args:
        technician_email: where to send it.
        brief: what build_briefing returned.
    """
    lines = [
        f"{brief.get('customer', 'Job')} - {brief.get('window', '')}".strip(),
        "",
        f"Where: {brief.get('site', '')}, {brief.get('address', '')}",
    ]
    if brief.get("access_note"):
        lines.append(f"Access: {brief['access_note']}")
    lines += [
        f"Machine: {brief.get('machine', '')}",
        f"On site: {brief.get('where_on_site') or 'not recorded'}",
        "",
        f"Reported: {brief.get('reported', '')}",
    ]
    if brief.get("safety"):
        lines += ["", f"SAFETY: {brief['safety']}"]

    if brief.get("load_these"):
        lines += ["", "Take:"]
        for p in brief["load_these"]:
            lines.append(f"  {p.get('name')} ({p.get('sku')}) - {p.get('why')}")

    if brief.get("likely_causes"):
        lines += ["", "What this has turned out to be before:"]
        for cause in brief["likely_causes"]:
            lines.append(f"  {cause}")

    if brief.get("general_checks"):
        lines += ["", "First-line checks, general trade knowledge:"]
        for g in brief["general_checks"]:
            lines.append(f"  {g.get('check')}")

    lines += ["", "Reply to this email when the job is done: what you found, "
                  "what you fitted, and the hours. That closes it and the "
                  "next person who meets this fault gets what you learned."]

    return send(technician_email,
                f"Job {brief.get('work_order_id', '')}: "
                f"{brief.get('customer', '')}",
                "\n".join(lines), kind=TRANSACTIONAL)
