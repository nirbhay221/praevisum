"""Three reasons to ring somebody who did not ring us.

Everything else in this system waits for a phone to ring. This is the half
that goes the other way, and the three reasons are deliberately ranked, because
they are not interchangeable.

    RECALL      a machine they own is under a federal safety recall.
                We load 324 of these and know exactly who owns what. Nobody
                cross-references the two by hand, which is why four customers
                currently own equipment recalled for electrocution and fire and
                have never been told.

    PREDICTION  a complaint they raised matches what preceded a failure on
                other units. Measured on this book, customers raise the grumble
                about 41 days before the repair closes, and the corpus names
                the right part 66% of the time against about 20% for guessing.

    OFFER       something their inventory suggests they need and do not have,
                with any live promotion attached.

The ranking is absolute, not a weighting. A safety recall outranks a sales call
every time, and a system that cannot tell them apart will eventually ring
somebody about a discount while sitting on an electrocution notice for a
machine in their kitchen.

CONSENT IS NOT OPTIONAL

`outreach_consent` was in the schema long before anything could use it, with
quiet hours and a frequency cap already modelled. No consent row, a revoked
one, or a call too recently, and nobody is queued. Safety recalls are the one
exception and they are handled explicitly rather than by accident: a hazard
notice is not marketing, and the code says so where you can read it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from . import db

# Absolute ordering. Lower number wins.
PRIORITY = {"recall": 10, "prediction": 40, "offer": 80}

# How far ahead a prediction is worth acting on. Beyond this the customer will
# think we are inventing problems; inside it they have already noticed
# something and will recognise what we are describing.
PREDICTION_WINDOW_DAYS = 120

# A complaint has to point somewhere with at least this much weight before it
# justifies ringing somebody. Below it we are guessing out loud at a customer.
PREDICTION_FLOOR = 0.35

# Which complaints are describing a machine misbehaving, as opposed to
# describing us. A customer saying "quoted nearly four hundred for a control
# board, that is absurd" is complaining about a PRICE, and the corpus happily
# matched it to control board failures at 0.68 confidence because it contains
# the words "control board". Acting on that means ringing somebody to warn
# their machine is failing because they grumbled about an invoice.
#
# Cost, support and installation complaints are about the business. Only a
# complaint about how the machine behaves can predict how it will behave.
SYMPTOM_CATEGORIES = {"reliability", "noise", "design", None, ""}


def _nid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


# --------------------------------------------------------------------------
# 1. safety recalls
# --------------------------------------------------------------------------

def sweep_recalls(dealer_id: str = "D-REF") -> list[dict]:
    """Customers who own a machine under an active federal safety recall.

    The matcher is the one the buying advice already uses, including the
    distinction between a recall of the machine and a recall of an accessory
    for it. Only machine-level recalls reach a customer here: ringing somebody
    about a recalled power bank to tell them their laptop is dangerous would
    be worse than not ringing at all.
    """
    from .ops import _recall_for

    with db.connect() as c:
        recalled = {}
        for r in c.execute(
                """SELECT brands, title, hazard, remedy, recall_date, url
                   FROM recalls WHERE brands IS NOT NULL"""):
            recalled.setdefault((r["brands"] or "").lower(), r)

        owned = c.execute(
            """SELECT a.id asset_id, a.manufacturer, a.model_number, a.family,
                      s.account_id, s.label site, ac.name account_name
               FROM assets a
               JOIN sites s ON s.id = a.site_id
               JOIN accounts ac ON ac.id = s.account_id
               WHERE a.retired_on IS NULL AND ac.dealer_id = ?""",
            (dealer_id,)).fetchall()

    out = []
    for a in owned:
        hit = _recall_for(recalled, a["manufacturer"], a["model_number"],
                          a["family"])
        if not hit or hit["kind"] != "machine":
            continue
        r = hit["row"]
        out.append({
            "kind": "recall",
            "account_id": a["account_id"],
            "account_name": a["account_name"],
            "asset_id": a["asset_id"],
            "machine": f"{a['manufacturer']} {a['model_number']}",
            "site": a["site"],
            "reason": f"{a['manufacturer']} {a['model_number']} at "
                      f"{a['site']} is under a federal safety recall",
            "evidence": f"{r['recall_date']}: {r['title']}. "
                        f"Hazard: {r['hazard']}",
            "remedy": r["remedy"],
            "url": r["url"],
            "say": "Lead with the hazard, plainly and without drama. They own "
                   "this machine and nobody has told them. Offer to come out. "
                   "Do not sell them anything on this call.",
        })
    return out


# --------------------------------------------------------------------------
# 2. predicted failures
# --------------------------------------------------------------------------

def sweep_predictions(dealer_id: str = "D-REF") -> list[dict]:
    """Customers whose own complaint matches what preceded a failure elsewhere.

    Uses the same corpus retrieval as the van loading, so this is not a second
    opinion invented for outreach: it is the same evidence path, asked earlier.

    Deliberately conservative. Only recent complaints, only where the corpus
    points somewhere with real weight, and never on a machine that already has
    an open job. Ringing somebody to warn about a fault they have already
    reported is how an outreach system teaches people to ignore it.
    """
    from .reason import _fault_distribution

    cutoff = (datetime.now() - timedelta(days=PREDICTION_WINDOW_DAYS)).isoformat()

    with db.connect() as c:
        rows = c.execute(
            """SELECT cm.id, cm.what, cm.manufacturer, cm.model_number,
                      cm.family, cm.asset_id, cm.account_id, cm.raised_at,
                      ac.name account_name
               FROM complaints cm
               JOIN accounts ac ON ac.id = cm.account_id
               WHERE cm.dealer_id = ? AND cm.status = 'open'
                 AND cm.raised_at >= ?
                 AND cm.asset_id IS NOT NULL
                 AND (cm.category IS NULL OR cm.category IN
                      ('reliability','noise','design'))
                 AND NOT EXISTS (
                     SELECT 1 FROM work_orders w
                     WHERE w.asset_id = cm.asset_id
                       AND w.status NOT IN ('closed','cancelled'))
               ORDER BY cm.raised_at DESC""",
            (dealer_id, cutoff)).fetchall()

    out = []
    for cm in rows:
        try:
            dist = _fault_distribution(dealer_id, cm["what"], cm["manufacturer"],
                                       cm["family"] or "", cm["model_number"] or "")
        except Exception:
            continue
        if not dist or dist[0]["probability"] < PREDICTION_FLOOR:
            continue

        top = dist[0]
        out.append({
            "kind": "prediction",
            "account_id": cm["account_id"],
            "account_name": cm["account_name"],
            "asset_id": cm["asset_id"],
            "machine": f"{cm['manufacturer']} {cm['model_number']}",
            "reason": f"what they described on {cm['raised_at'][:10]} matches "
                      f"what came before {top['cause'][:60]} elsewhere",
            "evidence": f"They said: \"{cm['what']}\". Our own jobs put that at "
                        f"{int(top['probability'] * 100)}% "
                        f"{top['cause'][:70]}",
            "likely_parts": top["parts"],
            "confidence": round(top["probability"], 2),
            "say": "Say what THEY told us first, then what it turned out to be "
                   "on other machines. Never state their machine is failing: "
                   "we are offering to look, not diagnosing down a phone line "
                   "at somebody who did not ring us.",
        })
    return out


# --------------------------------------------------------------------------
# 3. what their inventory suggests they lack
# --------------------------------------------------------------------------

def sweep_offers(dealer_id: str = "D-REF", min_support: int = 3) -> list[dict]:
    """Something their kit suggests they need and do not have.

    Not the same product again. A customer with two reach-in freezers does not
    want a third; the useful suggestion is the thing customers like them own
    and they do not.

    Worked out from co-occurrence in this dealer's own book: which families
    turn up together across customers, and which of those are missing here.
    `min_support` stops a single unusual customer generating suggestions for
    everybody else.
    """
    with db.connect() as c:
        have: dict[str, set] = {}
        for r in c.execute(
                """SELECT f.account_id, f.family FROM account_families f
                   JOIN accounts a ON a.id = f.account_id
                   WHERE a.dealer_id = ?""", (dealer_id,)):
            have.setdefault(r["account_id"], set()).add(r["family"])

        names = {r["id"]: r["name"] for r in c.execute(
            "SELECT id, name FROM accounts WHERE dealer_id=?", (dealer_id,))}

        spend = {r["account_id"]: r["total"] for r in c.execute(
            """SELECT p.account_id, SUM(p.subtotal) total
               FROM purchase_orders p WHERE p.subtotal IS NOT NULL
               GROUP BY p.account_id""")}

        live = {}
        today = datetime.now().date().isoformat()
        for r in c.execute(
                """SELECT headline, ends FROM promotions
                   WHERE dealer_id=? AND ends >= ?""", (dealer_id, today)):
            live[r["headline"]] = r["ends"]

    # how often each pair of families is owned by the same customer
    pairs: dict[tuple, int] = {}
    for fams in have.values():
        for a in fams:
            for b in fams:
                if a != b:
                    pairs[(a, b)] = pairs.get((a, b), 0) + 1

    out = []
    for account, fams in have.items():
        best = None
        for owned in fams:
            for (a, b), n in pairs.items():
                if a != owned or b in fams or n < min_support:
                    continue
                if best is None or n > best[1]:
                    best = (b, n, owned)
        if best is None:
            continue

        family, support, because = best
        out.append({
            "kind": "offer",
            "account_id": account,
            "account_name": names.get(account, account),
            "asset_id": None,
            "suggest_family": family,
            "reason": f"they run {because} and have no {family}",
            "evidence": f"{support} of our customers who run {because} also "
                        f"run a {family}",
            "past_spend": spend.get(account),
            "live_offers": list(live)[:2],
            "say": "Lead with what they already run, not with the product. If "
                   "there is no live offer, do not invent one. If they are not "
                   "interested, note it and do not raise it again.",
        })
    return out


# --------------------------------------------------------------------------
# consent, and the queue
# --------------------------------------------------------------------------

def _consent(c, account_id: str, marketing: bool = True) -> dict:
    """Whether we may ring this customer, and whether the consent is good enough.

    Absence of a row is absence of consent. Defaulting to "nobody said no" is
    how outreach systems end up calling people who never agreed.

    The second gate is the one that was missing. An AI-generated voice counts
    as an artificial or prerecorded voice under the TCPA, and a marketing call
    with one needs prior express WRITTEN consent. Oral consent taken on a
    service call is real, and it is not sufficient for an offer. It is
    sufficient for a call that is not marketing.
    """
    row = c.execute(
        """SELECT granted, revoked_on, quiet_before, quiet_after, max_per_days,
                  consent_form
           FROM outreach_consent WHERE account_id = ?""",
        (account_id,)).fetchone()
    if row is None:
        return {"may_call": False, "why": "no consent on record"}
    if not row["granted"] or row["revoked_on"]:
        return {"may_call": False, "why": "consent withheld or revoked"}

    form = (row["consent_form"] or "oral").strip().lower()
    if marketing and form != "written":
        return {"may_call": False,
                "why": f"{form} consent is not enough for a marketing call"}

    return {
        "may_call": True, "consent_form": form,
        "quiet_before": row["quiet_before"], "quiet_after": row["quiet_after"],
        "max_per_days": row["max_per_days"],
    }


def queue_outreach(candidates: list[dict], dealer_id: str = "D-REF") -> dict:
    """Put justified calls in the queue, and refuse the rest with a reason.

    A safety recall is not marketing and is queued regardless of marketing
    consent. That exception is written here, in one place, rather than being
    an accident of how the consent check happens to be ordered. A customer who
    opted out of offers has not opted out of being told their oven can
    electrocute somebody.
    """
    now = datetime.now()
    queued, blocked = [], []

    with db.txn() as c:
        for cand in sorted(candidates, key=lambda x: PRIORITY[x["kind"]]):
            account = cand["account_id"]
            # A federal hazard notice is not marketing and never was. A
            # predicted failure is a sales opportunity wearing a warning, so it
            # is treated as marketing. That line is drawn here, in one place,
            # rather than emerging from the order the checks happen to run in.
            safety = cand["kind"] == "recall"
            rule = _consent(c, account, marketing=not safety)

            if not rule["may_call"] and not safety:
                blocked.append({**cand, "blocked_because": rule["why"]})
                continue

            # Not too often. A recall ignores the marketing cap but not
            # duplication: telling somebody twice about the same hazard on the
            # same machine is noise, and noise is how a real warning gets
            # tuned out.
            # Matched on the reason as well as the kind. Without the reason,
            # every offer to an account looks like every other offer, because
            # they share a kind and have no asset, so a customer would receive
            # exactly one suggestion ever and never another. Pacing is the
            # frequency cap's job; this only stops the same thing twice.
            dupe = c.execute(
                """SELECT 1 FROM outreach_queue
                   WHERE account_id = ? AND kind = ?
                     AND COALESCE(asset_id,'') = COALESCE(?, '')
                     AND reason = ?
                     AND status IN ('queued','called')""",
                (account, cand["kind"], cand.get("asset_id"),
                 cand["reason"])).fetchone()
            if dupe:
                blocked.append({**cand, "blocked_because": "already raised"})
                continue

            if not safety:
                gap = rule.get("max_per_days") or 30
                recent = c.execute(
                    """SELECT 1 FROM outreach_queue
                       WHERE account_id = ? AND status = 'called'
                         AND called_at >= ?""",
                    (account,
                     (now - timedelta(days=gap)).isoformat())).fetchone()
                if recent:
                    blocked.append({**cand,
                                    "blocked_because": f"called within {gap} days"})
                    continue

            oid = _nid("OUT")
            c.execute(
                """INSERT INTO outreach_queue
                   (id,account_id,reason,due_after,status,kind,evidence,
                    asset_id,dealer_id,priority)
                   VALUES (?,?,?,?,'queued',?,?,?,?,?)""",
                (oid, account, cand["reason"],
                 now.isoformat(timespec="minutes"), cand["kind"],
                 cand.get("evidence"), cand.get("asset_id"), dealer_id,
                 PRIORITY[cand["kind"]]))
            queued.append({**cand, "outreach_id": oid})

    # Announce the queued calls once the transaction has committed, so nothing
    # is published that a rollback would have undone. The sweep decides at
    # midnight; the calls happen in business hours, which is a queue whether
    # or not it is called one.
    from . import bus
    for q in queued:
        bus.send_outreach(q, dealer_id)

    return {
        "ok": True, "queued": queued, "blocked": blocked,
        "counts": {k: sum(1 for q in queued if q["kind"] == k)
                   for k in PRIORITY},
        "note": "Safety recalls bypass marketing consent because a hazard "
                "notice is not marketing. Everything else needs consent on "
                "record, and absence of a record is not consent.",
    }


def due_now(dealer_id: str = "D-REF", at: datetime | None = None) -> dict:
    """What should be rung right now, in priority order, inside quiet hours.

    Quiet hours are checked here rather than at queue time, because a call
    queued at 2am is fine and a call PLACED at 2am is not.
    """
    at = at or datetime.now()
    minutes = at.hour * 60 + at.minute

    with db.connect() as c:
        rows = c.execute(
            """SELECT q.*, a.name account_name,
                      COALESCE(oc.quiet_before, 540) qb,
                      COALESCE(oc.quiet_after, 1020) qa
               FROM outreach_queue q
               JOIN accounts a ON a.id = q.account_id
               LEFT JOIN outreach_consent oc ON oc.account_id = q.account_id
               WHERE q.status = 'queued' AND q.dealer_id = ?
                 AND q.due_after <= ?
               ORDER BY q.priority ASC, q.due_after ASC""",
            (dealer_id, at.isoformat())).fetchall()

    ready, holding = [], []
    for r in rows:
        item = {"outreach_id": r["id"], "kind": r["kind"],
                "account": r["account_name"], "reason": r["reason"],
                "evidence": r["evidence"], "priority": r["priority"]}
        if r["qb"] <= minutes <= r["qa"]:
            ready.append(item)
        else:
            holding.append({**item, "held": "outside their quiet hours"})

    return {"ok": True, "at": at.strftime("%A %H:%M"),
            "ready": ready, "held_for_quiet_hours": holding,
            "note": "Ring in this order. A recall outranks everything below it."}


def run_sweep(dealer_id: str = "D-REF") -> dict:
    """The whole scan: recalls, then predictions, then offers, then queue them.

    This is the thing a scheduler runs. It takes no arguments a human has to
    think about and it is safe to run twice: duplicates are refused at the
    queue rather than being prevented by remembering when it last ran.
    """
    found = (sweep_recalls(dealer_id)
             + sweep_predictions(dealer_id)
             + sweep_offers(dealer_id))
    result = queue_outreach(found, dealer_id)
    result["scanned"] = {"recalls": sum(1 for f in found if f["kind"] == "recall"),
                         "predictions": sum(1 for f in found if f["kind"] == "prediction"),
                         "offers": sum(1 for f in found if f["kind"] == "offer")}
    return result


# --------------------------------------------------------------------------
# actually placing the calls
# --------------------------------------------------------------------------

def take_next(dealer_id: str = "D-REF", at: datetime | None = None) -> dict:
    """Claim the highest-priority call that is due, and hand over the brief.

    The queue had no consumer. The sweep decided every night who was worth
    ringing and nothing ever rang anybody, which made the whole thing a very
    well-tested list.

    Claiming and marking are one transaction so two workers cannot take the
    same call, the same way two callers cannot be promised the same part.

    Args:
        dealer_id: whose queue.
        at: pretend it is this time, for testing quiet hours.
    """
    at = at or datetime.now()
    ready = due_now(dealer_id, at)["ready"]
    if not ready:
        return {"ok": True, "call": None,
                "why": "nothing due inside quiet hours right now"}

    top = ready[0]
    with db.txn() as c:
        claimed = c.execute(
            """UPDATE outreach_queue SET status='called', called_at=?
               WHERE id=? AND status='queued'""",
            (at.isoformat(timespec="minutes"), top["outreach_id"]))
        if claimed.rowcount == 0:
            return {"ok": True, "call": None,
                    "why": "another worker took it first"}

        row = c.execute(
            """SELECT q.*, a.name account_name,
                      (SELECT p.e164 FROM phones p
                       JOIN contacts ct ON ct.id = p.contact_id
                       WHERE ct.account_id = q.account_id LIMIT 1) phone
               FROM outreach_queue q JOIN accounts a ON a.id = q.account_id
               WHERE q.id = ?""", (top["outreach_id"],)).fetchone()

    call = {
        "outreach_id": row["id"], "kind": row["kind"],
        "account_id": row["account_id"], "account": row["account_name"],
        "phone": row["phone"], "reason": row["reason"],
        "evidence": row["evidence"], "priority": row["priority"],
        "opening": _opening_line(row),
        "disclosure": "You must say you are an automated assistant before "
                      "anything else. Several states require it and it is the "
                      "right thing regardless.",
    }

    from . import bus
    bus.send_outreach(call, dealer_id)

    return {"ok": True, "call": call}


def _opening_line(row) -> str:
    """What to open with, written here rather than left to the model.

    An outbound call to somebody who did not ring us is the least forgiving
    thing this system does. The first sentence decides whether they listen or
    hang up, and it goes out unattended.
    """
    if row["kind"] == "recall":
        return ("This is an automated call from your service company about a "
                "safety notice on equipment you own. It is not a sales call. "
                + (row["evidence"] or ""))
    if row["kind"] == "prediction":
        return ("This is an automated assistant from your service company. "
                "You mentioned something recently and it matches a fault we "
                "have seen on other machines. We are offering to look, not "
                "telling you it has failed.")
    return ("This is an automated assistant from your service company. You "
            "agreed we could call about equipment you might need. Say so and "
            "we will not call again.")


def record_outcome(outreach_id: str, outcome: str, note: str = "") -> dict:
    """What happened on the call, including somebody asking us to stop.

    "Do not call again" has to be able to arrive through this path, or the
    only way out of the queue is to keep answering it.
    """
    outcome = (outcome or "").strip().lower()
    if outcome not in ("answered", "no_answer", "declined", "opted_out",
                       "booked", "wrong_number"):
        return {"ok": False, "why": "unrecognised outcome"}

    with db.txn() as c:
        row = c.execute("SELECT account_id FROM outreach_queue WHERE id=?",
                        (outreach_id,)).fetchone()
        if row is None:
            return {"ok": False, "why": "no such call"}

        c.execute("UPDATE outreach_queue SET outcome=? WHERE id=?",
                  (f"{outcome}: {note}".strip(": "), outreach_id))

        if outcome in ("opted_out", "wrong_number"):
            # Honoured immediately and permanently. A queue you cannot get out
            # of is not a queue, it is harassment with a schedule.
            c.execute(
                """UPDATE outreach_consent SET granted=0, revoked_on=?
                   WHERE account_id=?""",
                (datetime.now().date().isoformat(), row["account_id"]))
            c.execute(
                """UPDATE outreach_queue SET status='blocked'
                   WHERE account_id=? AND status='queued'""",
                (row["account_id"],))

    return {"ok": True, "outreach_id": outreach_id, "outcome": outcome,
            "consent_revoked": outcome in ("opted_out", "wrong_number")}
