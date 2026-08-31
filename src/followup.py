"""Calls that never connected, conversations that got cut off, and jobs worth
checking on afterwards.

THREE THINGS, ONE RULE

The rule is that we already know something, and making the customer say it
again is the cost. A person whose line dropped after reading their model
number out twice will not read it out a third time; they will ring somebody
else. So every message assembled here carries what we already had.

    missed_call    they rang and never got through. Invisible until now: the
                   call row is written inside the media stream's start event,
                   so a caller who hung up before it connected produced no row
                   anywhere and the call did not happen as far as we knew.

    dropped_call   they got through, told us what was wrong, and the line
                   went. `review.settle` already detects this as an intent
                   with no outcome. What was missing was doing anything.

    after_visit    the technician closed the job. A day later, on the channel
                   they chose, one question: is it holding?

WHY A MESSAGE AND NOT A CALL BACK

Ringing somebody whose line just dropped assumes they are free, and a kitchen
manager whose freezer died is not. A message lets them answer when they can,
carries what they already told us so it is not lost, and cannot interrupt
anything.

WHY THE ONLY FEEDBACK QUESTION IS AFTER THE VISIT

The obvious version of feedback is asking at the end of the call. It is the
worst possible moment: they want off the phone, and a line that cuts during
"how did I do" turns a resolved call into an unresolved one.

It is also weaker evidence than we already have. review.py derives what became
of a call from what the call actually wrote. A self-reported score on top of
that adds nothing and is answered mostly by people at the extremes.

"Is it holding?" a day after a repair is different. It is three words to
answer, and it tells us something the database genuinely cannot: whether the
fix held. That is the one question worth a customer's time.

CONSENT

Deliberately NOT governed by the marketing consent rule in outreach.py. That
rule exists because an AI voice making a marketing call needs prior express
written consent under the TCPA. None of these are marketing: two are finishing
a conversation the customer started, and the third is about a job they paid
for. An opt-out is still honoured, because somebody who said stop meant it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from . import db

# How long after a job closes before asking whether it held.
#
# Same day is too soon to know: a cabinet that has just been repaired is cold
# because it was serviced an hour ago, not because the repair worked. A day
# later it has been through a service and a defrost cycle.
AFTER_VISIT_HOURS = 24

# A dropped call is answered now, not tomorrow. They are still standing in
# front of the machine.
DROPPED_DELAY_MINUTES = 2

# Twilio statuses that mean nobody was served.
NEVER_CONNECTED = {"no-answer", "busy", "failed", "canceled"}


def _nid() -> str:
    return f"FU-{uuid.uuid4().hex[:6].upper()}"


def _queue(kind: str, phone: str, *, dealer_id: str = "D-REF",
           account_id: str | None = None, contact_id: str | None = None,
           from_call: str | None = None, work_order_id: str | None = None,
           context: str = "", delay: timedelta) -> dict:
    """Put one follow-up on the list, or leave the existing one alone.

    The unique index does the deduplication rather than a read-then-write,
    because a redelivered status webhook arrives concurrently with the first
    one and a check-then-insert would let both through.
    """
    if not phone:
        return {"ok": False, "why": "no number to reach them on"}

    if _opted_out(phone):
        return {"ok": False, "why": "they asked us not to contact them"}

    fid = _nid()
    try:
        with db.txn() as c:
            # One outstanding message per person per kind. The unique index
            # cannot express this on its own: every missed call creates a NEW
            # call row, so keying on from_call let somebody who rang three
            # times in a minute collect three apologies. Somebody redialling
            # is one event, not three.
            waiting = c.execute(
                """SELECT id FROM followups
                   WHERE phone = ? AND kind = ? AND status = 'queued'""",
                (phone, kind)).fetchone()
            if waiting is not None:
                return {"ok": True, "already_queued": True, "id": waiting["id"]}

            c.execute(
                """INSERT INTO followups
                   (id,dealer_id,kind,account_id,contact_id,phone,from_call,
                    work_order_id,context,due_after,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, dealer_id, kind, account_id, contact_id, phone,
                 from_call, work_order_id, context or None,
                 (datetime.now() + delay).isoformat(timespec="seconds"),
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        # ONLY A UNIQUE VIOLATION MEANS "ALREADY QUEUED".
        #
        # This matched any error containing the word "constraint", and
        # "CHECK constraint failed: kind IN (...)" contains it. So when a new
        # kind of follow-up was added without extending that CHECK, every
        # insert failed and every failure was reported as a row that already
        # existed. The console showed nothing waiting, the customer was never
        # asked, and the code said it was fine.
        #
        # A duplicate and a rejected row are different outcomes and must not
        # share a branch.
        text = str(e).lower()
        if "unique" in text:
            return {"ok": True, "already_queued": True}
        print(f"[followup] could not queue a {kind} for {phone}: "
              f"{type(e).__name__}: {e}", flush=True)
        raise
    return {"ok": True, "id": fid, "kind": kind}


def _opted_out(phone: str) -> bool:
    """Did this number ever tell us to stop?

    Read across accounts rather than for one, because somebody who opted out
    said it about being contacted, not about a particular account record.
    """
    # THE DO-NOT-CALL LIST FIRST, and this was the hole. `take_us_off_your_list`
    # writes to that list and does NOT touch outreach_consent, while this only
    # read outreach_consent. So somebody who said "never contact me again" had
    # their CALLS stopped and kept receiving TEXTS, which is not what they
    # asked for and not what they would describe to a regulator.
    try:
        from . import linetype

        if linetype.on_our_do_not_call(phone).get("listed"):
            return True
    except Exception as e:
        # Fail closed. Not knowing whether they opted out is not a reason to
        # message them.
        print(f"[followup] could not read the do-not-call list for {phone}: "
              f"{type(e).__name__}: {e}", flush=True)
        return True

    with db.connect() as c:
        row = c.execute(
            """SELECT 1 FROM outreach_consent oc
               JOIN contacts ct ON ct.account_id = oc.account_id
               JOIN phones p ON p.contact_id = ct.id
               WHERE p.e164 = ? AND (oc.revoked_on IS NOT NULL
                                     OR oc.granted = 0)""",
            (phone,)).fetchone()
    return row is not None


# --------------------------------------------------------------------------
# 1. they rang and never got through
# --------------------------------------------------------------------------

def record_call_status(twilio_sid: str, status: str, from_e164: str,
                       duration: int = 0, dealer_id: str = "D-REF") -> dict:
    """Twilio's verdict on a call, including the ones that never reached us.

    The only way to see a call that produced no row. Everything else in this
    system learns about a call from the media stream, which by definition did
    not happen here.

    Args:
        twilio_sid: Twilio's CallSid.
        status: completed, no-answer, busy, failed, or canceled.
        from_e164: who rang.
        duration: seconds, as Twilio counted them.
        dealer_id: whose line they rang.
    """
    status = (status or "").strip().lower()

    with db.connect() as c:
        ours = c.execute("SELECT id FROM calls WHERE twilio_sid = ?",
                         (twilio_sid,)).fetchone()

    if ours is not None:
        # The stream ran, so the call exists and review.settle has already
        # said what became of it. Nothing to add.
        return {"ok": True, "known": True, "call": ours["id"]}

    if status not in NEVER_CONNECTED and duration > 0:
        # Completed, but we never saw it. Rare and worth knowing about rather
        # than swallowing: it means the stream failed while the caller was
        # connected to something.
        return {"ok": True, "known": False, "odd": True,
                "why": "Twilio says this call completed but no stream reached us"}

    # A call this desk never answered. The most expensive thing that can
    # happen to a service business, and until now it left no trace at all.
    who = _who_is(from_e164)
    call_id = f"CALL-{uuid.uuid4().hex[:8].upper()}"
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,contact_id,started_at,ended_at,
                                  outcome,dealer_id,twilio_sid,connected)
               VALUES (?,?,?,?,?,?,?,?,0)""",
            (call_id, from_e164, who.get("contact_id"),
             datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds"),
             f"missed_{status or 'unknown'}", dealer_id, twilio_sid))

    queued = _queue("missed_call", from_e164, dealer_id=dealer_id,
                    account_id=who.get("account_id"),
                    contact_id=who.get("contact_id"), from_call=call_id,
                    context=_last_machine(who.get("account_id")),
                    delay=timedelta(minutes=DROPPED_DELAY_MINUTES))

    return {"ok": True, "known": False, "missed": True, "call": call_id,
            "status": status, "followup": queued.get("id")}


def _who_is(phone: str) -> dict:
    with db.connect() as c:
        row = c.execute(
            """SELECT ct.id contact_id, ct.name, ct.account_id, ac.name account
               FROM phones p JOIN contacts ct ON ct.id = p.contact_id
               JOIN accounts ac ON ac.id = ct.account_id
               WHERE p.e164 = ? LIMIT 1""", (phone,)).fetchone()
    return dict(row) if row else {}


def _last_machine(account_id: str | None) -> str:
    """What they most recently had trouble with, for the opening line.

    A missed call carries no words at all, so the only thing that can make the
    message specific is what we already hold about them.
    """
    if not account_id:
        return ""
    with db.connect() as c:
        row = c.execute(
            """SELECT a.manufacturer, a.model_number, a.family
               FROM work_orders w JOIN assets a ON a.id = w.asset_id
               WHERE w.account_id = ? ORDER BY w.opened_at DESC LIMIT 1""",
            (account_id,)).fetchone()
    if row is None:
        return ""
    return f"last job was on their {row['manufacturer']} {row['family']}"


# --------------------------------------------------------------------------
# 2. the line went mid-conversation
# --------------------------------------------------------------------------

def queue_dropped(call_id: str) -> dict:
    """A call that had an intent and produced nothing. Finish it.

    Called from review.settle, which already works out that this happened.
    Only queues where there is something worth resuming: a call that dropped
    before anybody said anything useful has nothing to pick up from, and a
    message about it would be noise.

    Args:
        call_id: the call that got cut off.
    """
    with db.connect() as c:
        call = c.execute(
            """SELECT c.id, c.from_e164, c.contact_id, c.dealer_id,
                      c.transcript, ct.account_id
               FROM calls c LEFT JOIN contacts ct ON ct.id = c.contact_id
               WHERE c.id = ?""", (call_id,)).fetchone()
    if call is None:
        return {"ok": False, "why": "no such call"}

    said = _what_they_told_us(call["transcript"] or "")
    if not said:
        return {"ok": False, "why": "nothing was said worth resuming"}

    return _queue("dropped_call", call["from_e164"],
                  dealer_id=call["dealer_id"] or "D-REF",
                  account_id=call["account_id"], contact_id=call["contact_id"],
                  from_call=call_id, context=said,
                  delay=timedelta(minutes=DROPPED_DELAY_MINUTES))


# Below this a caller has said hello and not much else, and there is nothing
# to resume from.
ENOUGH_TO_RESUME = 25


def _what_they_told_us(transcript: str) -> str:
    """The caller's own longest sentence, which is usually the fault.

    Their words rather than a summary, for the same reason the repair corpus
    keeps them: it is how they will describe it again, and reading it back is
    what proves we were listening rather than that we logged a ticket.
    """
    lines = [t for w, _, t in
             (line.partition(": ") for line in transcript.splitlines())
             if w == "caller"]
    best = max(lines, key=len, default="")
    return best.strip() if len(best.strip()) >= ENOUGH_TO_RESUME else ""


# --------------------------------------------------------------------------
# 3. did the repair hold
# --------------------------------------------------------------------------

def queue_after_visit(work_order_id: str) -> dict:
    """A day after the job closed, ask the one question worth asking.

    Not a satisfaction score. Whether it is still working, which is the only
    thing about a repair that the database cannot already tell us and the only
    feedback that changes anything: a fix that failed twice should stop being
    offered.

    Args:
        work_order_id: the job that closed.
    """
    with db.connect() as c:
        job = c.execute(
            """SELECT w.id, w.account_id, w.contact_id, w.dealer_id,
                      a.manufacturer, a.model_number, a.family,
                      r.found_cause, t.name technician
               FROM work_orders w
               LEFT JOIN assets a ON a.id = w.asset_id
               LEFT JOIN visits v ON v.work_order_id = w.id
               LEFT JOIN repairs r ON r.visit_id = v.id
               LEFT JOIN technicians t ON t.id = v.technician_id
               WHERE w.id = ? ORDER BY v.id DESC LIMIT 1""",
            (work_order_id,)).fetchone()
        if job is None:
            return {"ok": False, "why": "no such job"}

        # phones is keyed on the number itself, not an id. A verified number
        # first, since that is the one somebody actually answers.
        phone = c.execute(
            """SELECT e164 FROM phones WHERE contact_id = ?
               ORDER BY verified DESC, e164 LIMIT 1""",
            (job["contact_id"],)).fetchone()

    if phone is None:
        return {"ok": False, "why": "no number on the contact for that job"}

    # Always a whole sentence, whichever parts are missing. Built subject
    # first: without a technician's name the earlier version began "to the
    # Traulsen reach-in freezer", which is a sentence starting mid-clause and
    # reads as a broken template rather than a message from a person.
    machine = (f" to your {job['manufacturer']} {job['family']}"
               if job["manufacturer"] and job["family"] else "")
    # "We were out to you" plus " to your Traulsen" gave two prepositions in a
    # row, so the fallback subject depends on whether the machine follows it.
    who = (f"{job['technician'].split()[0]} came out" if job["technician"]
           else ("We were out" if machine else "We were out to you"))
    found = f" and found {job['found_cause']}" if job["found_cause"] else ""
    context = f"{who}{machine}{found}"

    return _queue("after_visit", phone["e164"],
                  dealer_id=job["dealer_id"] or "D-REF",
                  account_id=job["account_id"], contact_id=job["contact_id"],
                  work_order_id=work_order_id, context=context,
                  delay=timedelta(hours=AFTER_VISIT_HOURS))


# --------------------------------------------------------------------------
# what to send, and when
# --------------------------------------------------------------------------

def render(row) -> str:
    """The message itself, assembled from facts rather than narrated.

    No model writes these. Every one is built from what is already recorded,
    for the same reason the technician briefing is: a message that goes out
    unattended to a customer must not contain a sentence nobody chose.
    """
    ctx = (row["context"] or "").strip()

    if row["kind"] == "missed_call":
        line = "Sorry we missed your call just now."
        if ctx:
            line += f" I can see your {ctx.replace('last job was on their ', '')}."
        return line + (" Tell me what is happening and I will pick it up from "
                       "here, or ring back and somebody will answer.")

    if row["kind"] == "dropped_call":
        return (f"Sorry, we got cut off. You had told me: \"{ctx}\". Reply "
                "here and I will carry on from that, or ring back and I will "
                "have it in front of me. No need to go through it again.")

    if row["kind"] == "escalation":
        # Added when escalations were built, and NOT added here, so an
        # escalation fell through to the after_visit branch below and would
        # have gone out as "Dale Brenner will ring you back within 2 hours.
        # Is it holding now?" to somebody we had just told we could not staff
        # their job. The fall-through is the dangerous part of this function:
        # a new kind is silently rendered as the wrong message rather than
        # failing.
        return (ctx or "We are arranging cover for your job") + (
            " I will confirm as soon as it is booked. If anything changes in "
            "the meantime, reply here.")

    if row["kind"] == "review_ask":
        # Only ever queued after the customer has SAID it is holding. See
        # asking.py: asking somebody whose freezer may still be broken to go
        # and rate the repair is how a business earns one-star reviews.
        from .asking import render_review_ask

        return render_review_ask(row)

    if row["kind"] == "delivery_check_in":
        # An order is not finished when the carrier drops it, it is finished
        # when the person who paid says the right thing arrived intact. The
        # carrier already told us it landed, so this asks the only three
        # things a tracking number cannot answer.
        #
        # DELIBERATELY NOT `ctx`. Every other kind stores context written for
        # the customer, so reusing it here looked right and was not: the
        # delivery context is a BRIEF FOR THE AGENT, and it ends "do not argue
        # on the phone: record what they say and raise it." Sending that to
        # the person who just took the delivery hands them our internal
        # instructions and reads as though we expect a fight.
        return ("Your order was delivered. Did it arrive undamaged, is it the "
                "right machine, and is anything missing? Reply here and I "
                "will close it off, or tell me what is wrong and I will sort "
                "it.")

    if row["kind"] == "offer_consent":
        # THE MESSAGE WHOSE REPLY IS THE CONSENT.
        #
        # Says who is asking, what they would get, how often, and how to stop.
        # A consent text missing any of those four is not consent anybody
        # could rely on, and the reply to it is the only written record this
        # system will ever have.
        from .staying_in_touch import the_text

        # Defensive about the column: render() is called with whatever shape
        # the caller has, and a message must not fail to exist because one
        # field is missing from the row.
        try:
            whose = row["dealer_id"] or ""
        except (KeyError, IndexError, TypeError):
            whose = ""
        return the_text(whose)

    if row["kind"] != "after_visit":
        # A kind nobody wrote a message for. Sending the wrong sentence is
        # worse than sending none, so this refuses and says so in the log
        # rather than quietly borrowing the after-visit wording.
        print(f"[followup] no message written for kind {row['kind']!r}, "
              "not sending", flush=True)
        return ""

    # after_visit. One question, answerable in three words.
    opener = ctx or "We were out to you yesterday"
    return f"{opener}. Is it holding now?"


def due(dealer_id: str = "D-REF", at: datetime | None = None,
        ignore_timer: bool = False) -> list[dict]:
    """Follow-ups ready to go, oldest first.

    No quiet hours check. A dropped call is answered within minutes because
    they are still standing in front of the machine, and the after-visit
    question inherits the time of day the job closed, which was a working
    hour by definition.

    Args:
        dealer_id: whose queue.
        at: the moment to judge readiness against.
        ignore_timer: return everything queued, due or not. Only ever set by
            a person pressing send on the console. The timer exists so a
            message does not land in the same breath as the thing it is about,
            and a human choosing to send now has already made that judgement.
            It does NOT skip the opt-out check, which happens at queue time
            and is not a timing question.
    """
    at = at or datetime.now()
    when = "" if ignore_timer else "AND due_after <= ?"
    params = ([dealer_id] if ignore_timer
              else [dealer_id, at.isoformat(timespec="seconds")])
    with db.connect() as c:
        rows = c.execute(
            f"""SELECT * FROM followups
                WHERE dealer_id = ? AND status = 'queued' {when}
                ORDER BY due_after""",
            tuple(params)).fetchall()

    return [{"id": r["id"], "kind": r["kind"], "phone": r["phone"],
             "message": render(r), "from_call": r["from_call"],
             "work_order": r["work_order_id"],
             # Carried so the sender can record WHO was asked. Without it the
             # consent ask could not be tied back to an account, and the reply
             # had nothing to match against.
             "account_id": r["account_id"],
             "contact_id": r["contact_id"]} for r in rows]


def mark_sent(followup_id: str, via: str = "whatsapp") -> dict:
    with db.txn() as c:
        c.execute(
            """UPDATE followups SET status='sent', sent_at=?, sent_via=?
               WHERE id=? AND status='queued'""",
            (datetime.now().isoformat(timespec="seconds"), via, followup_id))
    return {"ok": True, "id": followup_id, "via": via}


def record_reply(phone: str, text: str) -> dict:
    """Tie an inbound message back to whatever we last asked them.

    Without this the after-visit question is rhetorical: somebody answers "yes
    all good" into the desk and it is read as a fresh conversation, and the
    one piece of feedback the database cannot produce is thrown away.
    """
    # A REPLY TO THE CONSENT TEXT IS NOT ORDINARY FEEDBACK.
    #
    # It is the only thing in this system that grants permission to market,
    # because a text somebody typed is writing and a phone call is not. It has
    # to be recognised before the general follow-up matching below, or a "YES"
    # meant as consent gets filed as an answer to "did the repair hold".
    try:
        from .staying_in_touch import their_reply

        consent = their_reply(phone, text)
        if consent.get("ok"):
            return {"ok": True, "kind": "offer_consent", **consent}
    except Exception as e:
        print(f"[followup] consent reply check failed: "
              f"{type(e).__name__}: {e}", flush=True)

    with db.txn() as c:
        row = c.execute(
            """SELECT id FROM followups
               WHERE phone = ? AND status = 'sent' AND reply IS NULL
               ORDER BY sent_at DESC LIMIT 1""", (phone,)).fetchone()
        if row is None:
            return {"ok": False, "why": "nothing outstanding for that number"}
        c.execute("UPDATE followups SET status='answered', reply=? WHERE id=?",
                  (text[:1000], row["id"]))
    return {"ok": True, "id": row["id"]}
