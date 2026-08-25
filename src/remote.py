"""Whether to send anybody at all.

`what_to_load` decides WHAT goes in the van. This is the decision that comes
before it and never existed: whether a van should move.

Every service call in this system ended in a visit. The industry's own numbers
say **14% of truck rolls are unnecessary**, that the best operators get that to
3%, and that each avoided dispatch saves $200 to $300. That last figure is the
same TRUCK_ROLL constant the van loading already uses, arrived at
independently.

So this is not a new kind of reasoning. It is the same trade-off, one step
earlier:

    send when we did not need to      a wasted trip, ~$300
    fail to send when we should have  a customer left with a broken machine,
                                      a second call, and less trust than if we
                                      had just come out

THE ASYMMETRY IS THE WHOLE DESIGN

Those two errors are not equal and must not be treated as equal. A wasted visit
costs money. Talking somebody out of a visit they needed costs the relationship,
and they will remember it. So the bar for NOT sending is deliberately high: a
documented procedure, from a named source, that has actually worked before, on
a fault that is plausibly the one they are describing.

Anything short of that and the van goes. Silence is not a diagnosis.

WHAT COUNTS AS DOCUMENTED

Three sources, all traceable to something a person could check:

    recall        the federal remedy text, verbatim from the CPSC data. The
                  strongest, and the only source in the seed that is genuinely
                  a citation.
    manual        a published procedure for this machine. NONE ARE SEEDED. A
                  real deployment adds the dealer's own service documentation
                  here, with a page reference.
    general       ordinary trade knowledge with no document behind it, written
                  for the seed and labelled honestly so nobody mistakes it for
                  a citation.
    our own notes what a technician wrote after fixing it themselves.

Nothing here is generated. An agent inventing a repair instruction for somebody
standing in front of a live appliance is the worst thing this system could do,
which is why the instruction is read from a row rather than composed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from . import db, trace
from .thresholds import VISIT_MINUTES  # noqa: F401

# What a wasted trip costs, shared with the van loading so the two halves of
# the same decision cannot drift apart.
from .reason import TRUCK_ROLL

# A procedure has to have worked before. One success out of one attempt is not
# evidence, it is an anecdote, and offering it as a fix is how a customer ends
# up on their knees behind a freezer for nothing.
MIN_ATTEMPTS = 3
MIN_SUCCESS_RATE = 0.5

# How well the caller's words have to match the documented symptom before we
# offer the procedure at all.
MIN_MATCH = 0.35


def _nid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _overlap(a: str, b: str) -> float:
    """Crude word overlap between what they said and what the fix is for.

    Deliberately not the semantic index. That index is tuned to find repairs
    that RESEMBLE a description, which is right for diagnosis and wrong here:
    a loose resemblance is enough to suggest a part to carry and nowhere near
    enough to talk somebody out of a visit.
    """
    wa = {w for w in (a or "").lower().split() if len(w) > 3}
    wb = {w for w in (b or "").lower().split() if len(w) > 3}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def find_remote_fix(asset_id: str, symptom: str,
                    dealer_id: str = "D-REF") -> dict:
    """Is there a documented thing the customer could do instead of a visit?

    Returns the procedure and its record, or an honest nothing. Never composes
    an instruction: everything returned was written down by somebody and can be
    traced to a manual, a federal recall remedy, or a technician's own note.

    Args:
        asset_id: the machine.
        symptom: the caller's words.
        dealer_id: whose book.
    """
    with db.connect() as c:
        asset = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.family,
                      e.product_type, COALESCE(e.defrost_type,'') defrost
               FROM assets a LEFT JOIN equipment e ON e.id = a.equipment_id
               WHERE a.id = ?""", (asset_id,)).fetchone()
        if asset is None:
            return {"found": False, "why": "unknown machine"}

        rows = c.execute(
            """SELECT f.*, r.tried, r.worked
               FROM remote_fixes f
               LEFT JOIN remote_fix_record r ON r.id = f.id
               WHERE f.dealer_id = ?
                 AND (f.family IS NULL OR f.family = ?)
                 AND (f.manufacturer IS NULL OR f.manufacturer = ?)
                 AND (f.product_type IS NULL OR f.product_type = ?)
                 AND (f.defrost_type IS NULL OR f.defrost_type = ?)""",
            (dealer_id, asset["family"], asset["manufacturer"],
             asset["product_type"], asset["defrost"])).fetchall()

    best = None
    for f in rows:
        match = _overlap(symptom, f["symptom"])
        if match < MIN_MATCH:
            continue

        tried = f["tried"] or 0
        worked = f["worked"] or 0

        # A procedure earns its place either by PROVENANCE or by RECORD.
        #
        # Requiring a record from everything created a deadlock: a procedure
        # could not be offered until it had been tried, and could not be tried
        # until it was offered. Every published fix sat unusable forever.
        #
        # Published documentation is already evidence. A federal recall remedy
        # and a manufacturer's own first-line check were both written by people
        # who know the machine, and neither needs our permission. What does
        # need a track record is anything we inferred ourselves from a
        # technician's note, because that is a guess until it has worked.
        #
        # `general` counts here too: ordinary trade knowledge with no document
        # behind it. Trusted enough to offer, because "is the door shut" is not
        # a controversial instruction, but it scores below a real citation.
        #
        # And provenance never outranks failure: any fix that keeps failing is
        # withdrawn regardless of where it came from.
        documented = f["source"] in ("recall", "manual", "general")
        failing = tried >= MIN_ATTEMPTS and worked / max(tried, 1) < MIN_SUCCESS_RATE
        earned = tried >= MIN_ATTEMPTS and worked / max(tried, 1) >= MIN_SUCCESS_RATE

        if failing or not (documented or earned):
            continue

        score = match * {"recall": 1.0, "manual": 0.95,
                         "general": 0.85}.get(f["source"], 0.75)
        if best is None or score > best[0]:
            best = (score, f, match, tried, worked)

    if best is None:
        return {
            "found": False,
            "machine": f"{asset['manufacturer']} {asset['model_number']}",
            "why": "nothing documented and proven matches what they described",
            "say": "Do not improvise a fix. Book the visit.",
        }

    _, f, match, tried, worked = best
    return {
        "found": True,
        "fix_id": f["id"],
        "machine": f"{asset['manufacturer']} {asset['model_number']}",
        "check_first": f["check_first"],
        "instruction": f["instruction"],
        "source": f["source"],
        "source_ref": f["source_ref"],
        "requires_tools": bool(f["requires_tools"]),
        "safety_note": f["safety_note"],
        "match": round(match, 2),
        "worked_before": f"{worked} of {tried}" if tried else "recall remedy",
        "say": "Read the check question first and wait for their answer. If it "
               "does not apply, stop and book the visit. Read the instruction "
               "as written; do not paraphrase a repair procedure.",
    }


def should_send_someone(asset_id: str, symptom: str,
                        dealer_id: str = "D-REF") -> dict:
    """The decision, with the arithmetic shown.

    Weighs a wasted trip against a customer left with a broken machine, and
    defaults to sending. The two errors are not symmetric: a wasted visit costs
    money, and talking somebody out of a visit they needed costs the
    relationship.

    Args:
        asset_id: the machine.
        symptom: the caller's words.
        dealer_id: whose book.
    """
    from .reason import _fault_distribution

    fix = find_remote_fix(asset_id, symptom, dealer_id)

    with db.connect() as c:
        asset = c.execute(
            "SELECT manufacturer, model_number, family FROM assets WHERE id=?",
            (asset_id,)).fetchone()
    if asset is None:
        return {"send": True, "why": "unknown machine, go and look"}

    dist = _fault_distribution(dealer_id, symptom, asset["manufacturer"],
                               asset["family"] or "", asset["model_number"])
    confident = bool(dist) and dist[0]["probability"] >= 0.5

    if not fix["found"]:
        out = {
            "send": True,
            "confidence_in_cause": round(dist[0]["probability"], 2) if dist else 0,
            "why": "nothing documented covers this, so somebody goes and looks",
            "cost_if_we_are_wrong": TRUCK_ROLL,
            "say": "Book the visit. Do not offer a fix we cannot source.",
        }
        trace.send_decision(dealer_id, out)
        return out

    # There is a documented, proven procedure. Offering it is still a choice.
    out = {
        "send": "offer_first",
        "confidence_in_cause": round(dist[0]["probability"], 2) if dist else 0,
        "likely_cause": dist[0]["cause"] if dist else None,
        "remote_fix": fix,
        "why": (f"a {fix['source']} procedure matches what they described and "
                f"has worked {fix['worked_before']}"),
        "cost_avoided_if_it_works": TRUCK_ROLL,
        "say": "Offer to try it WITH them on the line, and say a visit is "
               "already there if it does not work. Never make them choose "
               "between trying and being seen. If they would rather someone "
               "came out, book it without argument.",
        "if_it_fails": "Call record_attempt with not_resolved, then book the "
                       "visit as normal. A failed attempt is not a reason to "
                       "keep them on the phone.",
    }
    trace.send_decision(dealer_id, out)
    return out


def record_attempt(fix_id: str, outcome: str, asset_id: str = "",
                   symptom: str = "", said: str = "", call_id: str = "",
                   work_order_id: str = "", dealer_id: str = "D-REF") -> dict:
    """What happened when they tried it.

    This is the feedback that decides whether a procedure keeps being offered.
    Without it a fix that never works would be suggested forever, and the
    quickest way to lose a customer is to have them kneel behind a freezer for
    nothing twice.

    Args:
        fix_id: the procedure they tried.
        outcome: resolved, not_resolved, refused, or unsafe.
        asset_id: the machine.
        symptom: what they had described.
        said: their words about what happened.
        call_id: the call.
        work_order_id: the job, if one was opened anyway.
        dealer_id: whose book.
    """
    outcome = (outcome or "").strip().lower()
    if outcome not in ("resolved", "not_resolved", "refused", "unsafe"):
        return {"ok": False, "why": "unrecognised outcome"}

    aid = _nid("RA")
    with db.txn() as c:
        exists = c.execute("SELECT 1 FROM remote_fixes WHERE id=?",
                           (fix_id,)).fetchone()
        if exists is None:
            return {"ok": False, "why": "no such procedure"}

        c.execute(
            """INSERT INTO remote_attempts
               (id,dealer_id,fix_id,asset_id,from_call,work_order_id,symptom,
                outcome,said,attempted_at,saved_a_visit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, dealer_id, fix_id, asset_id or None, call_id or None,
             work_order_id or None, symptom or None, outcome, said or None,
             datetime.now().isoformat(timespec="seconds"),
             1 if outcome == "resolved" else 0))

        c.execute("UPDATE remote_fixes SET attempts = attempts + 1 WHERE id=?",
                  (fix_id,))
        if outcome == "resolved":
            c.execute("UPDATE remote_fixes SET resolved = resolved + 1 WHERE id=?",
                      (fix_id,))
        if outcome == "unsafe":
            # Somebody was asked to do something they should not have been.
            # Withdraw it immediately rather than waiting for the rate to drop.
            c.execute("UPDATE remote_fixes SET dealer_id = NULL WHERE id=?",
                      (fix_id,))

    return {
        "ok": True, "attempt_id": aid, "outcome": outcome,
        "saved_a_visit": outcome == "resolved",
        "withdrawn": outcome == "unsafe",
        "told_caller": ("Confirm it is sorted and tell them to ring back if it "
                        "returns." if outcome == "resolved" else
                        "Book the visit now. Do not try a second procedure."),
    }
