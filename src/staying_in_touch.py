"""Asking a customer whether we may send them offers, and proving they said yes.

THE HOLE THIS CLOSES

`sweep_offers` works out what a customer's own kit suggests they need and do
not have. It is careful work and it could never ring anybody, because
outreach.py correctly refuses a marketing call without prior express WRITTEN
consent -- the FCC treats an AI voice as an artificial or prerecorded voice --
and there was NO WAY IN THE PRODUCT TO OBTAIN THAT CONSENT.

Not on a call: the desk is told, twice, "record it when they OFFER it, never
ask for it". Not on the website. Not by text. The only route was a human
running scripts/grant_consent.py by hand, which no customer will ever do.

So the marketing half of this system had a legal precondition it could not
satisfy, and would have sat there computing recommendations nobody was allowed
to hear.

WHEN WE ASK, WHICH IS THE PART THAT MATTERS

Once, after they have BOUGHT something and it has been delivered. Not after
every call. A customer who rang because their freezer is broken does not want
to be asked for marketing permission while they are waiting for an engineer,
and asking then is how a service desk starts feeling like a call centre.

After a delivery there is a natural moment: they have just received something,
they are pleased or they are not, and "may I let you know when there is an
offer on the rest of your kit" is a fair question to ask somebody who has
chosen to buy from us.

HOW THE CONSENT BECOMES WRITTEN, WHICH IS THE PART THAT IS LEGAL

Saying yes on the phone is ORAL. It is real and it is not enough for
marketing, and this does not pretend otherwise: agreeing on the call only
sends them a text. Their REPLY to that text is the written record, because a
text message somebody typed is writing, and the message itself is the
evidence -- their words, their number, timestamped.

The agent can start that conversation. It cannot finish it. The row that
permits marketing is only ever written when a human types a reply, which is
the same line scripts/grant_consent.py draws and the reason nothing in src/
was allowed to write one before now.

AND IF THEY SAY NO

Then no. A refusal is recorded as loudly as an agreement, so nobody asks them
again, and "no" is not a reason to keep asking on later sales.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta

from . import db

# What we accept as agreement in a reply. Deliberately narrow: this is a legal
# record, and a maybe is not a yes.
A_YES = ("yes", "y", "yeah", "yep", "ok", "okay", "sure", "please do",
         "go ahead", "agreed", "i agree", "fine")

# And what plainly is not.
A_NO = ("no", "n", "nope", "stop", "don't", "dont", "no thanks",
        "not interested", "unsubscribe", "opt out", "remove me")

# How long before we ACT on consent. They asked to be told about offers, not
# to be rung the same week about the thing they have just bought.
#
# This is the gap before the first OFFER, not before the question. Asking
# belongs at the moment the order completes, while they are still thinking
# about us; a permission request arriving out of nowhere a month later reads
# as a cold approach, which is exactly what it is asking permission to avoid.
QUIET_FIRST_DAYS = 30

# How long before we ASK. Long enough not to land in the same breath as the
# confirmation, short enough to still be about the order they just placed.
#
# Configurable because a demo cannot wait two hours to show a loop that takes
# two hours, and because the right gap is a judgement about a business rather
# than a fact about the code.
ASK_AFTER_HOURS = float(os.getenv("PRAEVISUM_ASK_AFTER_HOURS", "2") or 2)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z']+", (text or "").lower()) if w]


def asked_already(account_id: str) -> dict:
    """Have we put this question to them before, and what did they say.

    Asking twice is worse than not asking: it tells a customer their answer
    was not recorded.
    """
    with db.connect() as c:
        row = c.execute(
            """SELECT id, state, asked_on, answered_on, answer
               FROM offer_consent_asks
               WHERE account_id = ? ORDER BY rowid DESC LIMIT 1""",
            (account_id,)).fetchone()
    return dict(row) if row else {}


def they_said_we_may_ask(account_id: str, said: str, phone: str = "",
                         contact_id: str = "", dealer_id: str = "",
                         call_id: str = "") -> dict:
    """They agreed ON THE CALL that we may text them about offers.

    This does NOT grant marketing consent. It sends the text whose reply will.

    Args:
        account_id: whose account.
        said: their own words, kept as the evidence for the ask.
        phone: where to send it. Taken from the contact if blank.
        contact_id: who agreed.
        dealer_id: whose offers.
        call_id: the call it was agreed on.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)

    before = asked_already(account_id)
    if before.get("state") == "agreed":
        return {"ok": True, "already": True,
                "say": "They have already agreed and it is on file. Do not "
                       "ask again."}
    if before.get("state") == "refused":
        return {"ok": False,
                "why": "they have already said no to this",
                "say": "Do not ask again. They said no once and that stands."}

    if not phone:
        with db.connect() as c:
            got = c.execute(
                """SELECT p.e164 FROM phones p
                   JOIN contacts ct ON ct.id = p.contact_id
                   WHERE ct.account_id = ?
                   ORDER BY p.verified DESC LIMIT 1""",
                (account_id,)).fetchone()
        phone = got["e164"] if got else ""

    if not phone:
        return {"ok": False, "why": "no number on this account to text"}

    from .followup import _opted_out

    if _opted_out(phone):
        return {"ok": False,
                "why": "they have asked us not to contact them",
                "say": "Say nothing further about offers. They are on the "
                       "do-not-call list and that outranks this."}

    aid = f"ASK-{datetime.now().strftime('%H%M%S')}{abs(hash(account_id)) % 1000:03d}"
    with db.txn() as c:
        c.execute(
            """INSERT INTO offer_consent_asks
               (id, account_id, contact_id, dealer_id, phone, state,
                asked_on, asked_via, said_on_the_call, from_call)
               VALUES (?,?,?,?,?, 'texted', ?, 'sms', ?, ?)""",
            (aid, account_id, contact_id or None, dealer_id, phone,
             datetime.now().isoformat(timespec="seconds"), said or None,
             call_id or None))

    return {"ok": True, "ask": aid, "to": phone,
            "message": the_text(dealer_id),
            "say": "Tell them a text is on its way and that replying YES to "
                   "it is what puts it on file. Saying yes to you is not "
                   "enough on its own, and it is worth saying so plainly: it "
                   "is their protection as much as ours."}


def the_text(dealer_id: str = "") -> str:
    """The message whose reply becomes the record.

    Says who is asking, what they would get, how often, and how to stop. A
    consent text that does not say those four things is not consent anybody
    could rely on.
    """
    name = "us"
    try:
        with db.connect() as c:
            row = c.execute("SELECT name FROM dealers WHERE id = ?",
                            (dealer_id,)).fetchone()
        if row and row["name"]:
            name = row["name"]
    except Exception:
        pass

    return (f"{name}: you said we could let you know about offers on the kit "
            "you already own. Reply YES and we will, no more than once a "
            "month. Reply NO and we will not ask again. Standard message "
            "rates apply.")


def their_reply(phone: str, text: str) -> dict:
    """A reply to that text. THE ONLY THING THAT GRANTS MARKETING CONSENT.

    Written, because they typed it. Their exact words are stored as the
    evidence, and the row records that it came from a text rather than from
    anybody's recollection of a phone call.
    """
    words = _words(text)
    if not words:
        return {"ok": False, "why": "empty reply"}

    with db.connect() as c:
        ask = c.execute(
            """SELECT id, account_id, dealer_id FROM offer_consent_asks
               WHERE phone = ? AND state = 'texted'
               ORDER BY rowid DESC LIMIT 1""", (phone,)).fetchone()
    if ask is None:
        return {"ok": False, "why": "nothing was asked of that number"}

    said_yes = any(w in A_YES for w in words[:3]) or \
        " ".join(words[:2]) in A_YES
    said_no = any(w in A_NO for w in words[:3]) or \
        " ".join(words[:2]) in A_NO

    # A REPLY THAT IS NEITHER IS NOT A YES.
    #
    # "maybe later", "what kind of offers", "who is this" are all real replies
    # and none of them is permission. Left open, so a person can follow it up,
    # rather than guessed either way.
    if said_no or not said_yes:
        state = "refused" if said_no else "unclear"
        with db.txn() as c:
            c.execute(
                """UPDATE offer_consent_asks
                   SET state = ?, answered_on = ?, answer = ?
                   WHERE id = ?""",
                (state, datetime.now().isoformat(timespec="seconds"),
                 text.strip(), ask["id"]))
        return {"ok": True, "granted": False, "state": state,
                "why": ("they said no" if said_no else
                        "that reply is not a yes, and a maybe is not consent")}

    when = date.today().isoformat()
    with db.txn() as c:
        c.execute(
            """UPDATE offer_consent_asks
               SET state = 'agreed', answered_on = ?, answer = ?
               WHERE id = ?""",
            (datetime.now().isoformat(timespec="seconds"), text.strip(),
             ask["id"]))

        # THE CONSENT ROW ITSELF. Written, with their own words as evidence,
        # and dated from the reply rather than from the call.
        c.execute(
            """INSERT INTO outreach_consent
               (account_id, granted, granted_on, granted_via, consent_form,
                evidence_ref)
               VALUES (?,1,?,?, 'written', ?)
               ON CONFLICT(account_id) DO UPDATE SET
                 granted = 1, granted_on = excluded.granted_on,
                 granted_via = excluded.granted_via,
                 consent_form = 'written',
                 evidence_ref = excluded.evidence_ref,
                 revoked_on = NULL""",
            (ask["account_id"], when,
             "replied YES to our consent text",
             f"{ask['id']}: {text.strip()[:120]}"))

    return {"ok": True, "granted": True, "account": ask["account_id"],
            "form": "written", "on": when,
            "evidence": text.strip()[:120],
            "not_before": (date.today()
                           + timedelta(days=QUIET_FIRST_DAYS)).isoformat(),
            "say": "That is on file as written consent, dated today, with "
                   "their own words as the evidence."}


def ask_after_delivery(purchase_order_id: str) -> dict:
    """Queue the offers question once they have bought something.

    Called when the ORDER IS CONFIRMED rather than when it lands. They have
    just chosen to buy from us and the conversation is fresh; a permission
    request arriving weeks later, out of nowhere, reads as the cold approach
    it is asking permission to avoid.

    NOT AT THE MOMENT OF DELIVERY, and not after every call. They have just
    taken delivery of one thing; a month is long enough that the next message
    is about the rest of their kit rather than an upsell on the box they are
    still unpacking, and it is the gap they asked for.
    """
    with db.connect() as c:
        po = c.execute(
            """SELECT po.id, po.account_id, po.dealer_id, po.contact_id
               FROM purchase_orders po WHERE po.id = ?""",
            (purchase_order_id,)).fetchone()
    if po is None:
        return {"ok": False, "why": "no such order"}

    before = asked_already(po["account_id"])
    if before.get("state") in ("agreed", "refused", "texted"):
        return {"ok": True, "already": before.get("state"),
                "why": "they have been asked once already"}

    from .followup import _queue

    with db.connect() as c:
        got = c.execute(
            """SELECT p.e164 FROM phones p JOIN contacts ct ON ct.id = p.contact_id
               WHERE ct.account_id = ? ORDER BY p.verified DESC LIMIT 1""",
            (po["account_id"],)).fetchone()
    phone = got["e164"] if got else ""

    if not phone:
        # THE PERSON IS ON THE PHONE. USE THAT NUMBER.
        #
        # Giving up here meant the offers question was NEVER asked on two of
        # the four companies. Counted across the book:
        #
        #     D-AV     22 accounts,  0 with a phone
        #     D-FURN   26 accounts,  0 with a phone
        #     D-IT     37 accounts, 36 with a phone
        #     D-REF    79 accounts, 77 with a phone
        #
        # So every projector and every desk sold went through a confirm that
        # correctly reported "no number on this account to text" and stopped.
        # The consent loop was unreachable for half the business and nothing
        # said so out loud, because refusing was the honest answer to the
        # question being asked -- it was just the wrong question.
        #
        # We are mid-call with them. Their number arrived with the call and is
        # on the call row. Texting the person who just bought something, at
        # the number they are speaking to us from, is not a guess.
        try:
            from .trace import here

            call_id = here()
            if call_id:
                with db.connect() as c:
                    row = c.execute(
                        "SELECT from_e164 FROM calls WHERE id = ?",
                        (call_id,)).fetchone()
                if row and row["from_e164"]:
                    phone = row["from_e164"]
                    print(f"[staying_in_touch] no number on {po['account_id']}; "
                          f"asking on the number they are calling from",
                          flush=True)
        except Exception as e:
            print(f"[staying_in_touch] could not read the caller's number: "
                  f"{type(e).__name__}: {e}", flush=True)

    if not phone:
        return {"ok": False, "why": "no number on this account to text"}

    due = (datetime.now() + timedelta(hours=ASK_AFTER_HOURS)).isoformat(
        timespec="seconds")
    try:
        out = _queue("offer_consent", phone,
                     dealer_id=po["dealer_id"] or "D-REF",
                     account_id=po["account_id"],
                     contact_id=po["contact_id"] or None,
                     context=f"they bought {purchase_order_id}",
                     delay=timedelta(hours=ASK_AFTER_HOURS))
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}

    return {"ok": True, "queued": out, "due_after": due,
            "why": "asked once, shortly after they took delivery. The first OFFER is a month out"}


def we_have_now_asked(account_id: str, phone: str, dealer_id: str = "",
                      contact_id: str = "", from_followup: str = "") -> dict:
    """Record that the offers question has gone out, so a reply can land.

    THE LINK THAT WAS MISSING, AND IT BROKE THE WHOLE LOOP.

    `their_reply` is the only thing that can grant marketing consent, and it
    matches an inbound message against an `offer_consent_asks` row in state
    'texted'. Only `they_said_we_may_ask` ever wrote one of those -- the path
    where the desk asks ON A CALL.

    The path that actually runs is the other one: confirming an order queues
    an `offer_consent` follow-up, the sender delivers it, and NOTHING wrote
    the ask row. So the text went out, the customer replied YES, and it came
    back "nothing was asked of that number". The consent could never be
    granted, which means `sweep_offers` could never legally ring anybody, and
    the entire marketing half of this system was unreachable from the only
    route a real customer would take.

    Both halves were tested. The join between them was not.
    """
    try:
        before = asked_already(account_id)
        if before.get("state") in ("agreed", "refused", "texted"):
            return {"ok": True, "already": before.get("state")}

        aid = (f"ASK-{datetime.now().strftime('%H%M%S')}"
               f"{abs(hash(account_id)) % 1000:03d}")
        with db.txn() as c:
            c.execute(
                """INSERT INTO offer_consent_asks
                   (id, account_id, contact_id, dealer_id, phone, state,
                    asked_on, asked_via, said_on_the_call, from_call)
                   VALUES (?,?,?,?,?, 'texted', ?, 'sms', ?, ?)""",
                (aid, account_id, contact_id or None, dealer_id or None, phone,
                 datetime.now().isoformat(timespec="seconds"),
                 "queued after an order was confirmed", from_followup or None))
        return {"ok": True, "ask": aid}
    except Exception as e:
        print(f"[staying_in_touch] could not record that we asked "
              f"{account_id}: {type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": f"{type(e).__name__}"}


def ask_after_complaint(account_id: str, dealer_id: str = "",
                        call_id: str = "") -> dict:
    """Queue the offers question off the back of a complaint.

    WHY A COMPLAINT IS A FAIR MOMENT AND WHY IT IS ALSO A DELICATE ONE.

    The question was only ever queued when an ORDER was confirmed, so a
    customer who never bought anything on a call -- who rang because something
    was wrong -- was never asked at all. That is most of a service desk's
    conversations.

    It is asked ONCE per account ever, and a previous no stands, so ringing in
    twice with two faults cannot mean being asked twice. The delay is longer
    than after a sale: somebody whose machine has just failed does not want a
    marketing question in the same hour, and the honest gap is measured from
    their problem being taken seriously rather than from our convenience.

    Everything else is the same machinery as `ask_after_delivery`, including
    the opt-out check and the fact that only their written reply grants
    anything.
    """
    if not account_id:
        return {"ok": False, "why": "no account to ask"}

    before = asked_already(account_id)
    if before.get("state") in ("agreed", "refused", "texted"):
        return {"ok": True, "already": before.get("state"),
                "why": "they have been asked once already"}

    with db.connect() as c:
        got = c.execute(
            """SELECT p.e164 FROM phones p JOIN contacts ct ON ct.id = p.contact_id
               WHERE ct.account_id = ? ORDER BY p.verified DESC LIMIT 1""",
            (account_id,)).fetchone()
    phone = got["e164"] if got else ""

    if not phone and call_id:
        # The number they are ringing from, for the same reason as a sale:
        # two of the four companies hold no phone number on any account.
        try:
            with db.connect() as c:
                row = c.execute("SELECT from_e164 FROM calls WHERE id = ?",
                                (call_id,)).fetchone()
            if row and row["from_e164"]:
                phone = row["from_e164"]
        except Exception:
            pass

    if not phone:
        return {"ok": False, "why": "no number to ask on"}

    from .followup import _queue

    try:
        out = _queue("offer_consent", phone,
                     dealer_id=dealer_id or "D-REF",
                     account_id=account_id,
                     context="they raised a complaint",
                     delay=timedelta(hours=ASK_AFTER_HOURS * 2))
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}

    return {"ok": True, "queued": out,
            "why": "asked once, a while after their complaint was taken"}
