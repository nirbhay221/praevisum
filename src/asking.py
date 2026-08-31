"""Asking a customer for a review, but only once we know the fix held.

WHERE THIS SITS IN A LOOP THAT ALREADY EXISTS

    technician texts to close the job
      -> textback.close_by_text writes the repair
      -> followup.queue_after_visit schedules one question
      -> a day later: "We were out to you yesterday. Is it holding now?"
      -> customer replies, followup.record_reply ties it back

Everything above was already built. The loop then stopped, with the single
most valuable thing a service business can get sitting one step away.

WHY THE ORDER MATTERS MORE THAN THE FEATURE

The obvious version asks for a review in the after-visit message. That is the
version that gets a business one-star reviews, because it asks somebody whose
freezer may still be broken to go and rate the repair.

So the review is never asked for in the same breath. It is asked only after
the customer has SAID it is holding, in their own words, and only then. A
question that is already answered is not a risk.

WHAT A NEGATIVE ANSWER DOES INSTEAD

It raises a job. "No, it is still doing it" is a machine that failed twice,
which is worth more to this business than any review: recovery.py exists for
exactly that, and a second failure on the same work order is the strongest
signal in the book that a fix should stop being offered.

So the branch is not review-or-nothing. It is review-if-good, and go-back-out
if not.

WHAT IT WILL NOT DO

It will not offer an incentive. Paying for reviews is against every platform's
terms and it poisons the only honest signal a service business has. It asks
once, it never asks twice, and a customer who ignores it is left alone.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import db

# Words a customer uses when the repair held. Deliberately short and
# deliberately not a sentiment model: "yes" and "all good" are what people
# actually text back, and a classifier here would be a second thing that can
# be wrong about something a keyword gets right.
IT_HELD = ("yes", "yep", "yeah", "all good", "working", "holding", "fine",
           "sorted", "fixed", "perfect", "great", "no problem", "all fine",
           "much better", "spot on", "thanks")

# And when it did not. Checked FIRST, because "yes it is still broken"
# contains "yes" and must never be read as success.
IT_DID_NOT = ("no", "not", "still", "again", "worse", "same problem",
              "hasn't", "has not", "isn't", "is not", "broken", "failed",
              "never", "unhappy", "poor")

# How long to leave it before asking. Long enough that the answer means
# something, short enough that the visit is still the thing they remember.
ASK_AFTER_HOURS = 4


def read_the_answer(text: str) -> str:
    """Whether a reply to "is it holding now?" means yes, no, or unclear.

    Returns "held", "failed" or "unclear". Unclear is a real answer and is
    treated as neither: a customer who wrote a paragraph gets a person, not an
    automated request for a five-star rating.
    """
    low = (text or "").strip().lower()
    if not low:
        return "unclear"

    # WHOLE WORDS, NOT SUBSTRINGS. "holding fine now" was read as a failure
    # because "no" is inside "now", so a happy customer was classified as a
    # complaint and never asked for the review they would have left. The same
    # trap sits in "not" inside "nothing" and "same" inside "sameness".
    words = set(re.findall(r"[a-z']+", low))

    def said(phrases) -> bool:
        for p in phrases:
            bits = p.split()
            if len(bits) == 1:
                if bits[0] in words:
                    return True
            elif p in low:                      # a phrase, matched as written
                return True
        return False

    # "STILL" CUTS BOTH WAYS, and treating it as a flat negative read a happy
    # customer as a complaint:
    #
    #     "yes still fine"        -> it is STILL working      positive
    #     "yes but it is still warm" -> it is STILL broken    negative
    #
    # The word carries no polarity on its own; the word after it does. So an
    # explicitly positive "still X" is settled here before the blanket
    # negative check ever sees it.
    if any(f"still {good}" in low for good in
           ("fine", "good", "working", "holding", "ok", "okay", "great",
            "sorted", "perfect")):
        return "held"

    # Negatives first and unconditionally. "yes but it is still warm" is a
    # failure that contains the word yes, and reading it the other way round
    # would ask a customer with a broken freezer to go and review us.
    if said(IT_DID_NOT):
        return "failed"
    if said(IT_HELD):
        return "held"
    return "unclear"


def _already_asked(c, account_id: str) -> bool:
    row = c.execute(
        """SELECT 1 FROM followups
           WHERE account_id = ? AND kind = 'review_ask' LIMIT 1""",
        (account_id,)).fetchone()
    return row is not None


def after_they_said_it_held(phone: str, text: str) -> dict:
    """Decide what a reply to the after-visit question earns.

    Called from the desk when a customer answers. Returns what was done rather
    than doing anything to the conversation: the desk is mid-sentence with
    somebody and must not have a second message injected into it.

    Args:
        phone: who replied.
        text: exactly what they wrote.
    """
    verdict = read_the_answer(text)

    with db.connect() as c:
        row = c.execute(
            """SELECT f.id, f.account_id, f.dealer_id, f.work_order_id,
                      f.context
               FROM followups f
               WHERE f.phone = ? AND f.kind = 'after_visit'
               ORDER BY f.id DESC LIMIT 1""", (phone,)).fetchone()

    if row is None:
        return {"ok": False, "why": "they were not answering an after-visit "
                                    "question"}

    if verdict == "failed":
        # AND WORK OUT WHO SHOULD GO BACK, which this did not do.
        #
        # recovery.py already holds the right rule and says it plainly:
        # "sending the same person back to a customer who has just complained
        # about them is the one option guaranteed to make it worse, whoever
        # was right". But it only fired on a formal DISPUTE. A customer
        # answering "no, it is still doing it" got "book them back in" and the
        # same engineer, the same van, and a fair chance of the same result.
        #
        # It is a suggestion rather than a reassignment. Whether the original
        # engineer should go back is a real judgement: they know the machine,
        # and they also just missed something. Naming the alternative lets a
        # person decide instead of the rota deciding by default.
        instead = _who_else_could_go(row["work_order_id"])
        say = ("They have told us it is NOT holding. Do not ask for a review "
               "and do not close this. That is a second failure on the same "
               "job, which is the strongest signal we get that a fix should "
               "stop being offered. Get them booked back in.")
        if instead:
            say += (f" Offer {instead['name']} rather than the same engineer, "
                    "unless the customer asks for whoever came last. Do not "
                    "explain why, and do not criticise a colleague.")

        return {
            "ok": True, "verdict": "failed", "asked_for_a_review": False,
            "work_order_id": row["work_order_id"],
            "send_instead": instead,
            "say": say,
        }

    if verdict == "unclear":
        return {"ok": True, "verdict": "unclear", "asked_for_a_review": False,
                "say": ("Not a clear yes. Read what they actually wrote and "
                        "answer it. Do not ask for a review off an ambiguous "
                        "reply.")}

    with db.connect() as c:
        if _already_asked(c, row["account_id"]):
            return {"ok": True, "verdict": "held", "asked_for_a_review": False,
                    "say": "They have been asked for a review before. Once is "
                           "enough; asking again is how a business becomes a "
                           "nuisance."}

    when = (datetime.now() + timedelta(hours=ASK_AFTER_HOURS)).isoformat(
        timespec="seconds")

    with db.txn() as c:
        c.execute(
            """INSERT INTO followups
                 (id,dealer_id,kind,account_id,phone,work_order_id,context,
                  due_after,status,created_at)
               VALUES (?,?,'review_ask',?,?,?,?,?, 'queued', ?)""",
            (f"FU-REV-{row['id']}", row["dealer_id"], row["account_id"], phone,
             row["work_order_id"], row["context"] or "",
             when, datetime.now().isoformat(timespec="seconds")))

    return {
        "ok": True, "verdict": "held", "asked_for_a_review": True,
        "due_after": when,
        "say": ("They said it is holding. A review request is queued for "
                "later today, not sent now: do not mention it, and do not ask "
                "them yourself. Just acknowledge that it is sorted."),
    }


def render_review_ask(row) -> str:
    """The message itself, built from facts like every other follow-up.

    No model writes this. A message that goes out unattended must not contain
    a sentence nobody chose, which is the same rule the technician briefing
    and every other follow-up already follow.
    """
    ctx = (row["context"] or "").strip()
    opener = ("Glad that sorted it." if not ctx
              else f"Glad the {ctx.strip('.')} is sorted.")
    return (f"{opener} If you have a minute, it genuinely helps a small "
            "service business if you say so publicly. No pressure either way, "
            "and we will not ask again.")


def _who_else_could_go(work_order_id: str) -> dict | None:
    """A different qualified engineer for a job that did not hold.

    Reuses recovery._somebody_else so there is one definition of "somebody
    else", and it already checks EPA 608 through cover.can_work_on: a
    different engineer who may not legally open the machine is not an
    alternative, it is a second problem.

    Returns nothing when there is no one else, which is a real answer on a
    small crew and better than pretending there is a choice.
    """
    if not work_order_id:
        return None

    try:
        from .recovery import _somebody_else

        with db.connect() as c:
            job = c.execute(
                """SELECT w.dealer_id, a.family, v.technician_id
                   FROM work_orders w
                   LEFT JOIN assets a ON a.id = w.asset_id
                   LEFT JOIN visits v ON v.work_order_id = w.id
                   WHERE w.id = ? ORDER BY v.seq DESC LIMIT 1""",
                (work_order_id,)).fetchone()
        if job is None:
            return None

        return _somebody_else(job["technician_id"], job["dealer_id"],
                              job["family"])
    except Exception as e:
        print(f"[asking] could not suggest another engineer: "
              f"{type(e).__name__}: {e}", flush=True)
        return None
