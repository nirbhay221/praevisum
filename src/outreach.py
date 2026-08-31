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
import os
from datetime import datetime, timedelta

from . import db

# Absolute ordering. Lower number wins.
# A hazard we found ourselves outranks a federal notice. Both are safety,
# but ours is derived from complaints our own customers made and is the
# earlier of the two by definition: the recall comes later, if it comes.
PRIORITY = {"hazard": 5, "recall": 10, "prediction": 40, "offer": 80}

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
                      ac.name account_name,
                      (SELECT s.id FROM assets a JOIN sites s ON s.id = a.site_id
                        WHERE a.id = cm.asset_id) site_id
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

        # WHETHER THE NEXT FEW DAYS MAKE THIS URGENT.
        #
        # A condenser rejects heat into the air around it, so a machine that
        # is already marginal is a different proposition on the Tuesday
        # before a 92F weekend than it is in October. Every service manager
        # knows the phone rings on the hot days; this is the desk knowing it
        # in time to do something about it.
        #
        # It never claims the weather will break anything. Heat is a stressor
        # and not a cause, and the machine is already showing symptoms: that
        # is why it is on this list at all.
        heat = None
        try:
            from .weather import pressure_on_machines, where_they_are

            if cm["site_id"]:
                lat, lon = where_they_are(cm["site_id"])
                if lat and lon:
                    w = pressure_on_machines(lat, lon)
                    if w.get("ok") and w["level"] in ("high", "raised"):
                        heat = w
        except Exception as e:
            print(f"[outreach] could not read the forecast: "
                  f"{type(e).__name__}: {e}", flush=True)

        out.append({
            "kind": "prediction",
            "weather": heat,
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
            "say": ("Say what THEY told us first, then what it turned out to "
                    "be on other machines. Never state their machine is "
                    "failing: we are offering to look, not diagnosing down a "
                    "phone line at somebody who did not ring us."
                    + (f"\nAnd say why now: {heat['why']}. That is the reason "
                       "this call is happening this week rather than at some "
                       "point. Do NOT say the weather will break it."
                       if heat else "")),
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

# WHAT EACH KIND OF CALL NEEDS BEFORE IT MAY BE MADE.
#
# Two tiers was wrong, and wrong in a way that made recording spoken consent
# pointless. Safety bypassed the check entirely; everything else demanded
# WRITTEN consent. So a customer saying "yes, you can ring me" changed no
# outcome at all, and the tool that recorded it was decoration.
#
# The line that actually matters is whether the call SELLS SOMETHING.
#
#   none     a recall or a dangerous fault. Their health, not our revenue.
#   written  everything else, including a predicted failure. An AI voice is an
#            artificial or prerecorded voice under the TCPA and marketing with
#            one needs prior express WRITTEN consent. Nothing spoken on a call
#            can substitute for it.
#
# A PREDICTED FAILURE IS NOT A SERVICE CALL, though it is tempting to file it
# as one. It comes from a company that sells replacement machines, to somebody
# whose machine we say is about to fail. That is a sales opportunity wearing a
# warning and it is gated as one, which is the conservative reading and the
# one to keep while the exposure is the owner's rather than mine.
#
# The consequence, said plainly rather than engineered around: SPOKEN CONSENT
# CURRENTLY UNLOCKS NOTHING. Safety does not need it and everything else needs
# more than it. Recording it is still worth doing, because it is the customer's
# stated wish and the thing a written consent form is later attached to, but
# the tool that records it must not claim it bought anything.
NEEDS = {"hazard": "none", "recall": "none",
         "prediction": "written", "offer": "written"}


def _consent(c, account_id: str, marketing: bool = True,
             needs: str = "") -> dict:
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
    # `needs` is the real rule; `marketing` is kept so older callers still work
    # and means the same thing as needing written consent.
    want = (needs or ("written" if marketing else "oral")).strip().lower()
    if want == "written" and form != "written":
        return {"may_call": False,
                "why": f"{form} consent is not enough for a marketing call"}

    return {
        "may_call": True, "consent_form": form,
        "quiet_before": row["quiet_before"], "quiet_after": row["quiet_after"],
        "max_per_days": row["max_per_days"],
    }


def _asked_us_to_stop(c, account_id: str) -> str:
    """Their number, if any number on this account is on the do-not-call list.

    Checked against every number we hold for them, not just the first: a
    request made from the mobile is a request from the business.
    """
    try:
        from . import linetype

        for r in c.execute(
                """SELECT p.e164 FROM phones p JOIN contacts ct
                   ON ct.id = p.contact_id WHERE ct.account_id = ?""",
                (account_id,)):
            if linetype.on_our_do_not_call(r["e164"]).get("listed"):
                return r["e164"]
    except Exception as e:
        # FAIL CLOSED. If the list cannot be read we do not know whether they
        # asked us to stop, and ringing somebody who did is the error that
        # costs money and standing.
        print(f"[outreach] could not read the do-not-call list for "
              f"{account_id}: {type(e).__name__}: {e}", flush=True)
        return "unreadable"
    return ""


def _hand_safety_to_a_person(cand: dict, dealer_id: str, number: str) -> None:
    """A safety notice for somebody who asked not to be called automatically.

    Never raises. The point is that the obligation survives the refusal: they
    still own a machine we believe is dangerous, and somebody has to tell
    them in a way they did not opt out of.
    """
    try:
        from .escalate import raise_it

        raise_it(reason="other",
                 asset_id=cand.get("asset_id") or "",
                 detail=(f"SAFETY, and they are on our do-not-call list "
                         f"({number}). Do not use the automated line. "
                         f"{cand.get('reason', '')}"),
                 dealer_id=dealer_id)
    except Exception as e:
        print(f"[outreach] a safety notice for a do-not-call number could "
              f"not be escalated: {type(e).__name__}: {e}", flush=True)


def queue_outreach(candidates: list[dict], dealer_id: str = "D-REF",
                   at: datetime | None = None) -> dict:
    """Put justified calls in the queue, and refuse the rest with a reason.

    A safety recall is not marketing and is queued regardless of marketing
    consent. That exception is written here, in one place, rather than being
    an accident of how the consent check happens to be ordered. A customer who
    opted out of offers has not opted out of being told their oven can
    electrocute somebody.

    Args:
        candidates: what the sweep thinks is worth a call.
        dealer_id: whose queue.
        at: the moment to stamp due_after with. `due_now` and `take_next`
            already accept the same argument, and this did not, so a test that
            pinned the clock at 11:00 was reading a queue stamped with the
            real one. Every one of those tests passed in the morning and
            failed every afternoon, which is worse than failing outright:
            a suite that goes green depending on the hour teaches everybody
            to ignore it.
    """
    now = at or datetime.now()
    queued, blocked = [], []
    to_escalate: list[tuple[dict, str]] = []

    with db.txn() as c:
        for cand in sorted(candidates, key=lambda x: PRIORITY[x["kind"]]):
            account = cand["account_id"]
            # A federal hazard notice is not marketing and never was. A
            # predicted failure is a sales opportunity wearing a warning, so it
            # is treated as marketing. That line is drawn here, in one place,
            # rather than emerging from the order the checks happen to run in.
            # "STOP CALLING ME" OUTRANKS EVERYTHING, INCLUDING SAFETY.
            #
            # take_us_off_your_list writes a permanent row and its docstring
            # says "every outbound path checks it before anything else". That
            # was true of prospecting and NOT true here: an internal
            # do-not-call request revokes consent, safety kinds bypass
            # consent, so somebody who had explicitly asked never to be rung
            # was still queued for an automated hazard call.
            #
            # The duty of care does not disappear with the automated call. A
            # safety notice to somebody on the list is handed to a PERSON,
            # which honours the request and puts a human on the one call that
            # most needs judgement.
            listed = _asked_us_to_stop(c, account)
            if listed:
                blocked.append({**cand,
                                "blocked_because": "they asked us never to "
                                                   "contact them again"})
                if cand["kind"] in ("recall", "hazard"):
                    # AFTER the transaction, not inside it. raise_it opens its
                    # own, and nesting produced "cannot rollback, no
                    # transaction is active": the block still worked and the
                    # escalation silently did not, which is the worst half of
                    # the two to lose.
                    to_escalate.append((cand, listed))
                continue

            want = NEEDS.get(cand["kind"], "written")
            safety = want == "none"
            rule = _consent(c, account, needs=want)

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
                # HOW OFTEN ONE CUSTOMER MAY BE RUNG. Their own record
                # first, then the dealer default. Overridable by environment
                # so a demo can show a suggestion actually going out, rather
                # than asking somebody to take the pacing on trust for a
                # month. The floor is the customer's row, never below it.
                gap = int(os.getenv("OUTREACH_MIN_GAP_DAYS", "")
                          or rule.get("max_per_days") or 30)
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
    # Safety notices for people who asked not to be called automatically.
    # Outside the transaction for the same reason the announcements are.
    for cand, number in to_escalate:
        _hand_safety_to_a_person(cand, dealer_id, number)

    from . import bus
    for q in queued:
        bus.send_outreach(q, dealer_id)

    return {
        "ok": True, "queued": queued, "blocked": blocked,
        "counts": {k: sum(1 for q in queued if q["kind"] == k)
                   for k in PRIORITY},
        "note": "Safety calls, ours and the regulator's, bypass marketing "
                "consent because a hazard notice is not marketing. "
                "Everything else needs consent on record, and absence of a "
                "record is not consent.",
    }


def waiting_to_ring(dealer_id: str = "D-REF", limit: int = 40) -> dict:
    """Everything queued to ring an EXISTING customer about, and why.

    THE THING THIS MAKES VISIBLE. The nightly sweep decides who is worth
    ringing and writes it here, and the console showed none of it. `hunting`
    has its own screen but that is the opposite list: businesses who are NOT
    customers. The people we already serve, and the reasons we are about to
    interrupt them, had nowhere to be looked at.

    That matters most for the monthly suggestion. It is the one kind here that
    is not a safety notice or a fault, so it is the one an owner might
    reasonably want to cancel before it goes out, and the only way to cancel
    something is to be able to see it.

    Ordered the way the queue is worked: safety first, then faults, then
    anything commercial.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT q.id, q.kind, q.reason, q.evidence, q.due_after,
                      q.priority, q.asset_id, a.name account, a.id account_id,
                      (SELECT ct.name FROM contacts ct
                        WHERE ct.account_id = a.id LIMIT 1) contact
               FROM outreach_queue q JOIN accounts a ON a.id = q.account_id
               WHERE q.status = 'queued' AND q.dealer_id = ?
               ORDER BY q.priority, q.due_after LIMIT ?""",
            (dealer_id, limit))]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1

    return {
        "queued": rows,
        "counts": counts,
        "total": len(rows),
        "what_each_is": {
            "hazard": "our own customers reported something dangerous on a "
                      "model this customer also owns",
            "recall": "a federal notice on equipment they own",
            "prediction": "something they told us matches a fault that "
                          "preceded a failure elsewhere",
            "offer": "equipment customers like them run and they do not. The "
                     "only commercial one here, capped at once a month",
        },
    }


def _local_now(dealer_id: str) -> datetime:
    """The time where the CUSTOMER is, not where the server happens to run.

    THE CLOCK THIS WAS MEASURED AGAINST WAS THE WRONG ONE.

    Quiet hours are stored as minutes past midnight in the customer's local
    time, and this compared them against `datetime.now()`. On a laptop in the
    Central time zone that is correct by coincidence. The VM runs Etc/UTC, and
    the two are five hours apart:

        VM clock              03:27 UTC
        Chicago               22:27

    So a queue item was held at 03:27 for looking like the middle of the
    night, which was the right answer for the wrong reason. The damaging half
    is the other direction: at 22:00 UTC it is 17:00 in Chicago, a perfectly
    ordinary time to ring a restaurant, and the comparison refused it for
    being past a 20:00 cutoff. The bug silently blocked the entire US
    afternoon and only looked correct in the evening window it was tested in.

    prospect.py already did this properly for approaching a stranger. The path
    for ringing our own customers did not, which is the wrong way round.
    """
    from zoneinfo import ZoneInfo

    tz = None
    try:
        with db.connect() as c:
            row = c.execute("SELECT timezone FROM dealers WHERE id = ?",
                            (dealer_id,)).fetchone()
        tz = (row["timezone"] if row else None) or None
    except Exception as e:
        print(f"[outreach] could not read the dealer timezone: "
              f"{type(e).__name__}: {e}", flush=True)

    try:
        return datetime.now(ZoneInfo(tz or "America/Chicago"))
    except Exception:
        # A bad timezone name must not stop the queue, and server time is a
        # worse answer than a named default for a desk that serves one metro.
        return datetime.now()


def due_now(dealer_id: str = "D-REF", at: datetime | None = None) -> dict:
    """What should be rung right now, in priority order, inside quiet hours.

    Quiet hours are checked here rather than at queue time, because a call
    queued at 2am is fine and a call PLACED at 2am is not.

    "Now" is the customer's local time. See _local_now: this used server time,
    which is UTC in production and refused every call through the US
    afternoon.
    """
    at = at or _local_now(dealer_id)
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

    ready, holding, dropped = [], [], []
    with db.connect() as c:
        for r in rows:
            item = {"outreach_id": r["id"], "kind": r["kind"],
                    "account": r["account_name"], "reason": r["reason"],
                    "evidence": r["evidence"], "priority": r["priority"]}

            # CONSENT IS CHECKED AGAIN HERE, NOT ONLY AT QUEUE TIME.
            #
            # It was checked once, when the sweep put the item in, and never
            # again. So withdrawing consent stopped FUTURE items being queued
            # and did nothing to the ones already sitting there: revoke at
            # 09:00 and the call queued yesterday still went out at 11:00.
            #
            # Withdrawal has to take effect on the next call, not on the next
            # sweep. That is the whole point of withdrawing it.
            #
            # A safety recall is exempt for the same reason it is exempt at
            # queue time: a hazard notice on equipment somebody owns is not
            # marketing, and consent was never what permitted it.
            if r["kind"] not in ("recall", "hazard"):
                say = _consent(c, r["account_id"], marketing=True)
                if not say.get("may_call"):
                    dropped.append({**item, "dropped": say.get("why")})
                    continue

            if r["qb"] <= minutes <= r["qa"]:
                ready.append(item)
            else:
                holding.append({**item, "held": "outside their quiet hours"})

    return {"ok": True, "at": at.strftime("%A %H:%M"),
            "ready": ready, "held_for_quiet_hours": holding,
            # Reported rather than silently skipped. A queue that quietly
            # shrinks is one nobody can audit, and "we stopped ringing them
            # the moment they asked" is the claim that has to be evidenced.
            "dropped_since_queued": dropped,
            "note": "Ring in this order. A recall outranks everything below "
                    "it. Anything in dropped_since_queued had consent when it "
                    "was queued and does not now, and must not be rung."}


def run_sweep(dealer_id: str = "D-REF") -> dict:
    """The whole scan: recalls, then predictions, then offers, then queue them.

    This is the thing a scheduler runs. It takes no arguments a human has to
    think about and it is safe to run twice: duplicates are refused at the
    queue rather than being prevented by remembering when it last ran.

    LEARNING HAPPENS FIRST, AND THE ORDER IS THE POINT.

    sweep_predictions works by matching a customer's own complaint against what
    preceded a failure elsewhere, so it is only as good as the corpus it reads.
    Until close_the_loop existed, one finished job in five never reached that
    corpus: 851 visits were completed and diagnosed and 670 had a repairs row,
    because the technician's text reply was the only route in and plenty of
    jobs close some other way.

    So the night's predictions were computed against a book that was missing
    the most recent, least tidy work. Learning before predicting rather than
    after means tonight's scan can already see yesterday's jobs.
    """
    learned = {}
    try:
        from .learning import close_the_loop

        learned = close_the_loop(dealer_id)
    except Exception as e:
        # A sweep that cannot learn must still ring the people it already
        # knows about.
        print(f"[outreach] could not close the learning loop: "
              f"{type(e).__name__}: {e}", flush=True)

    # THE STATION KEEPS ITSELF STOCKED.
    #
    # One 32 second loop meant anybody held for two minutes heard it four
    # times. The music is generated rather than licensed, so the library can
    # refresh itself: one new track when it is short or when the newest is
    # more than a rotation old, and the oldest retired past the cap.
    #
    # ONE track, and only when it is wanted. Lyria is billable and this runs
    # with nobody watching, so it is bounded by KEEP_TRACKS and gated on
    # needs_a_new_one rather than firing every night.
    music = {}
    try:
        from .station import refresh

        music = refresh()
    except Exception as e:
        print(f"[outreach] could not refresh the hold music: "
              f"{type(e).__name__}: {e}", flush=True)

    # OUR OWN HAZARD EVIDENCE, BEFORE THE REGULATOR'S.
    #
    # hazard.py could read every complaint this dealer's customers made,
    # group them per model, weigh them for danger, and find every other owner
    # of that model. It was reachable from one seed script and from no part
    # of the running system, so on the live book a Beverage-Air HR1HC with
    # three dangerous reports across three sites had twenty-six other owners
    # and not one of them was going to hear about it.
    #
    # It runs first because it is the earliest signal there is: the federal
    # notice for the same fault arrives later, if it arrives. It queues its
    # own warnings and assigns its own engineers rather than returning
    # candidates, because a hazard also has to dispatch somebody and the rest
    # of this sweep does not.
    hazards = {}
    try:
        from .hazard import act_on_hazards

        hazards = act_on_hazards(dealer_id)
    except Exception as e:
        print(f"[outreach] could not act on hazards: "
              f"{type(e).__name__}: {e}", flush=True)

    found = (sweep_recalls(dealer_id)
             + sweep_predictions(dealer_id)
             + sweep_offers(dealer_id))
    result = queue_outreach(found, dealer_id)
    result["hazards"] = hazards
    result["scanned"] = {"recalls": sum(1 for f in found if f["kind"] == "recall"),
                         "predictions": sum(1 for f in found if f["kind"] == "prediction"),
                         "offers": sum(1 for f in found if f["kind"] == "offer")}
    result["learned"] = {"new_repairs": learned.get("written", 0),
                         "still_unlearned": learned.get("still_unlearned")}
    result["music"] = {"generated": music.get("generated", False),
                       "track": music.get("track"),
                       "why": music.get("why")}
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
    # The customer's clock, not the server's. This is the function that
    # actually claims a call to dial, so it carried the same UTC bug as
    # due_now and would have refused every afternoon in production.
    at = at or _local_now(dealer_id)
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
