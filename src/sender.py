"""Getting a queued message to the person it was written for.

THE SAME GAP, TWICE

`outreach.py` decided every night who was worth ringing and nothing rang
anybody until outbound.py was written. `followup.py` has been doing the same
thing quietly since it was built: a missed call, a dropped call and an
after-visit check all get queued, rendered into a sentence assembled from
facts, and left in a table.

`followup.due()` returns them. Nothing sends them.

So a customer whose call dropped mid-sentence gets a message written for them
that never leaves the building, and the desk records having followed up.

WHICH CHANNEL A MESSAGE GOES OUT ON

Whichever one they used to reach us, because that is the only evidence we have
about where they read things. `contacts.channel_pref` is consulted first since
somebody who said they prefer WhatsApp said so, and a linked Telegram chat is
tried before SMS because it costs nothing.

A number we have never heard from on any channel falls back to SMS, which is
the one that works without them having joined anything.

WHAT IT WILL NOT DO

It will not decide whether a message should be sent. That was settled when the
follow-up was queued, including the opt-out check, and re-deciding it here
would put the rule in two places.

It will not compose anything. Every message was rendered from recorded facts
by followup.render, for the same reason the technician briefing is assembled
rather than narrated: nothing reaches a customer unattended carrying a clause
nobody chose.
"""

from __future__ import annotations

import os

from . import db

# WHATSAPP ONLY. THIS DEPLOYMENT SENDS NOWHERE ELSE.
#
# It used to try telegram, then whatsapp, then SMS, honouring whatever
# preference was stored against the contact. Both of the others are wrong here
# and for different reasons:
#
#   SMS is not registered for A2P 10DLC on this number. Twilio ACCEPTS those
#   messages and never delivers them, so they were marked sent, the console
#   showed the question as asked, and the customer's phone stayed silent. A
#   message recorded as delivered and never received is the worst of the three
#   outcomes.
#
#   Telegram delivered to a linked handle rather than to the phone the person
#   is actually holding, so the consent text for a live call went somewhere
#   the caller was not looking.
#
# One channel, and it is the one that works. A stored channel_pref no longer
# jumps the queue: a preference for something this deployment cannot deliver
# is not evidence, it is a field somebody filled in.
ORDER = ("whatsapp",)


def _reachable(phone: str) -> list[str]:
    """How we reach this person. WhatsApp, and nothing else.

    There is no channel choice on this deployment any more, so there is
    nothing to work out. The old version read a stored channel_pref and a
    telegram link and built a list from them, which is the right shape for a
    system with three working channels and a lie in a system with one: it
    produced "sms" for numbers that cannot receive SMS and "telegram" for a
    caller who was holding a phone.

    Kept as a function rather than inlined because whether a person is
    reachable is a real question that will need a real answer again the moment
    a second channel is registered.
    """
    return list(ORDER)


def _deliver(channel: str, phone: str, text: str) -> dict:
    """One attempt on one channel. Never raises."""
    try:
        if channel == "telegram":
            from . import telegram

            with db.connect() as c:
                row = c.execute(
                    """SELECT handle FROM channel_links
                       WHERE channel='telegram' AND phone=?""",
                    (phone,)).fetchone()
            if row is None or not telegram.configured():
                return {"ok": False, "why": "no linked telegram chat"}
            return {"ok": telegram.send(row["handle"], text)}

        if channel == "whatsapp":
            from . import whatsapp

            return whatsapp.send(phone, text)

        from . import outbound

        return outbound.send_sms(phone, text)
    except Exception as e:
        print(f"[sender] {channel} failed for {phone}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": f"{type(e).__name__}"}


def send_followups(dealer_id: str = "D-REF", limit: int = 20,
                   now: bool = False) -> dict:
    """Deliver what followup.due() has been quietly holding.

    Args:
        dealer_id: whose queue.
        limit: how many in one pass.
        now: send everything queued rather than only what the timer says is
            ready. Set when a person presses send, never by the sweeper.
    """
    from .followup import due, mark_sent

    waiting = due(dealer_id, ignore_timer=now)[:max(1, limit)]
    sent, failed = [], []

    for item in waiting:
        # A message nobody wrote is not a message. render() returns empty for
        # a follow-up kind it has no wording for, and sending that would put a
        # blank text in front of a customer. Left queued, so it shows up as
        # undelivered rather than disappearing.
        if not (item.get("message") or "").strip():
            failed.append({"id": item["id"], "kind": item["kind"],
                           "why": "no message is written for this kind"})
            continue

        for channel in _reachable(item["phone"]):
            out = _deliver(channel, item["phone"], item["message"])
            if out.get("ok"):
                mark_sent(item["id"], via=channel)

                # AND THE ASK IS ON THE RECORD, so their reply can land.
                #
                # Without this the consent text went out and the YES that came
                # back matched nothing: `their_reply` looks for an ask in
                # state 'texted' and only the on-a-call path ever wrote one.
                # Recorded HERE, at the moment it genuinely went out, rather
                # than when it was queued -- an ask that never sent is not an
                # ask anybody can be held to.
                if item["kind"] == "offer_consent":
                    try:
                        from .staying_in_touch import we_have_now_asked

                        we_have_now_asked(item.get("account_id") or "",
                                          item["phone"],
                                          dealer_id=dealer_id,
                                          contact_id=item.get("contact_id") or "",
                                          from_followup=item["id"])
                    except Exception as e:
                        print(f"[sender] could not record the consent ask for "
                              f"{item['id']}: {type(e).__name__}: {e}",
                              flush=True)

                sent.append({"id": item["id"], "kind": item["kind"],
                             "to": item["phone"], "via": channel})
                break
        else:
            # Left queued rather than marked sent. A follow-up that could not
            # be delivered is not a follow-up that happened, and the desk
            # recording otherwise is the failure this file exists to fix.
            failed.append({"id": item["id"], "kind": item["kind"],
                           "why": "no channel accepted it"})

    return {
        "waiting": len(waiting), "sent": len(sent), "failed": len(failed),
        "delivered": sent, "not_delivered": failed,
        "say": ("Nothing here decided whether to send. That was settled when "
                "the follow-up was queued, opt-out included."),
    }
