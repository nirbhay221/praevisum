"""What we advised, what was actually fitted, and what it cost us.

THE LOOP THAT WAS OPEN

Three quarters of it already worked. `build_briefing` computes `load_these`
and sends it to the engineer. The engineer texts back what they fitted.
`textback` writes that into `parts_used` AND into `repairs.parts_consumed`,
and `commonly_needed` reads the latter -- so the corpus genuinely does learn
which parts fix which fault, from real jobs this system handled.

What it could never learn is whether OUR OWN ADVICE WAS ANY GOOD, because the
advice was never written down. `load_these` was built fresh inside the message
that carried it and then discarded. We could tell you what fixed a freezer. We
could not tell you that we had sent the engineer out with four parts and they
fitted one, four times running.

And nothing was ever costed. `parts_used.qty` and `parts.unit_cost` have both
existed from the beginning and nothing multiplied them, so a complaint carried
the customer's words and no number, and a model that eats parts looked exactly
like one that does not.

WHAT THIS ADDS

    we_advised        write down what we told them to take, with the evidence
                      we had at the time
    what_it_cost      cost the visit at cost price when it closes, and pin it
                      to the complaint it answers
    how_good_was_our_advice
                      of what we said to take, how much got fitted; of what
                      got fitted, how much we had said. Both, because they
                      fail differently
    what_this_has_cost_us
                      per model and per complaint, which is the number that
                      decides whether a product is worth stocking

WHY BOTH DIRECTIONS OF THE ADVICE SCORE

They are different mistakes and mixing them hides both.

    told them to take 4, they fitted 1     we are loading the van with junk
    they fitted 3, we had named 1          we are causing second visits

The first wastes stock and van space. The second is the expensive one: it is
the engineer driving back. A single accuracy number averages a cheap failure
against an expensive one and reports something in the middle that is true of
neither.
"""

from __future__ import annotations

from datetime import datetime

from . import db

# What an hour in the field costs us, before parts. Not what we charge:
# this table is about loss, and billing a customer is a different question
# answered somewhere else.
LABOUR_COST_PER_HOUR = float(85.0)


def we_advised(visit_id: str, load_these: list[dict]) -> dict:
    """Write down what we told the engineer to take.

    Called at briefing time. Never raises: a recommendation that could not be
    recorded must not stop the briefing reaching the person driving to the job.
    """
    if not visit_id or not load_these:
        return {"ok": True, "recorded": 0}

    now = datetime.now().isoformat(timespec="seconds")
    kept = 0
    try:
        with db.txn() as c:
            for part in load_these:
                sku = (part or {}).get("sku")
                if not sku:
                    continue
                c.execute(
                    """INSERT INTO parts_recommended
                       (visit_id, sku, because, likelihood, told_on)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(visit_id, sku) DO UPDATE SET
                         because = excluded.because,
                         likelihood = excluded.likelihood""",
                    (visit_id, sku, part.get("why"), part.get("likelihood"),
                     now))
                kept += 1
    except Exception as e:
        print(f"[service_loop] could not record the advice for {visit_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": f"{type(e).__name__}"}

    return {"ok": True, "recorded": kept}


def what_it_cost(visit_id: str, complaint_id: str = "") -> dict:
    """Cost a closed visit at cost price, and pin it to what it was about.

    Args:
        visit_id: the visit.
        complaint_id: the complaint this visit answers, if it answers one.

    Stored rather than recomputed later, because cost prices move. What a
    gasket cost us in March is what that March visit cost; recalculating it
    against today's price rewrites history to match the present.
    """
    with db.connect() as c:
        visit = c.execute(
            """SELECT v.id, v.work_order_id, v.labor_hours,
                      w.dealer_id, w.asset_id,
                      a.manufacturer, a.model_number, a.family
               FROM visits v
               LEFT JOIN work_orders w ON w.id = v.work_order_id
               LEFT JOIN assets a ON a.id = w.asset_id
               WHERE v.id = ?""", (visit_id,)).fetchone()
        if visit is None:
            return {"ok": False, "why": f"no visit {visit_id!r}"}

        lines = [dict(r) for r in c.execute(
            """SELECT u.sku, u.qty, p.name, p.unit_cost
               FROM parts_used u LEFT JOIN parts p ON p.sku = u.sku
               WHERE u.visit_id = ?""", (visit_id,))]

    parts_cost = 0.0
    unpriced = []
    for line in lines:
        cost = line.get("unit_cost")
        if cost is None:
            # A part with no cost on it is not free, it is unknown, and
            # counting it as zero would report a cheap visit. Named instead.
            unpriced.append(line["sku"])
            continue
        parts_cost += float(cost) * int(line.get("qty") or 1)

    hours = float(visit["labor_hours"] or 0)
    labour_cost = round(hours * LABOUR_COST_PER_HOUR, 2)
    parts_cost = round(parts_cost, 2)

    with db.txn() as c:
        c.execute(
            """INSERT INTO visit_cost
               (visit_id, work_order_id, dealer_id, parts_cost, labour_hours,
                labour_cost, manufacturer, model_number, family, complaint_id,
                costed_on)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(visit_id) DO UPDATE SET
                 parts_cost = excluded.parts_cost,
                 labour_hours = excluded.labour_hours,
                 labour_cost = excluded.labour_cost,
                 complaint_id = COALESCE(excluded.complaint_id,
                                         visit_cost.complaint_id),
                 costed_on = excluded.costed_on""",
            (visit_id, visit["work_order_id"], visit["dealer_id"], parts_cost,
             hours, labour_cost, visit["manufacturer"], visit["model_number"],
             visit["family"], complaint_id or None,
             datetime.now().isoformat(timespec="seconds")))

    # ON THE LEDGER, so it can be set against what the model earned.
    #
    # The cost was being stored and nothing joined it to returns, claims or
    # sales, so "is this model worth stocking" had no way to be asked.
    try:
        from .ledger import post_a_visit

        post_a_visit(visit_id)
    except Exception as e:
        print(f"[service_loop] could not post visit {visit_id} to the "
              f"ledger: {type(e).__name__}: {e}", flush=True)

    return {
        "ok": True, "visit": visit_id,
        "parts": [{"sku": ln["sku"], "name": ln.get("name"),
                   "qty": ln.get("qty"), "unit_cost": ln.get("unit_cost")}
                  for ln in lines],
        "parts_cost": parts_cost,
        "labour_hours": hours,
        "labour_cost": labour_cost,
        "total": round(parts_cost + labour_cost, 2),
        "unpriced": unpriced,
        "machine": f"{visit['manufacturer'] or ''} "
                   f"{visit['model_number'] or ''}".strip(),
        "complaint_id": complaint_id or None,
    }


def how_good_was_our_advice(manufacturer: str = "", model_number: str = "",
                            dealer_id: str = "") -> dict:
    """Whether what we tell engineers to take is what they end up fitting.

    Scored in both directions, because they are different mistakes:

        fitted_of_advised   we said take these; how much got used.
                            Low means we are filling the van with junk.
        advised_of_fitted   they fitted these; how much we had named.
                            Low means second visits, which is the costly one.

    Args:
        manufacturer: narrow to one make, or blank for everything.
        model_number: narrow to one model.
        dealer_id: whose book. Blank means the company on this call.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)

    where = ["w.dealer_id = ?"]
    params: list = [dealer_id]
    if manufacturer:
        where.append("LOWER(a.manufacturer) = LOWER(?)")
        params.append(manufacturer)
    if model_number:
        where.append("LOWER(a.model_number) = LOWER(?)")
        params.append(model_number)

    with db.connect() as c:
        visits = [r["id"] for r in c.execute(
            f"""SELECT v.id FROM visits v
                JOIN work_orders w ON w.id = v.work_order_id
                LEFT JOIN assets a ON a.id = w.asset_id
                WHERE {' AND '.join(where)} AND v.completed_at IS NOT NULL""",
            tuple(params))]

        advised_total = fitted_total = both = 0
        wasted: dict[str, int] = {}
        missed: dict[str, int] = {}
        for vid in visits:
            advised = {r["sku"] for r in c.execute(
                "SELECT sku FROM parts_recommended WHERE visit_id = ?", (vid,))}
            fitted = {r["sku"] for r in c.execute(
                "SELECT sku FROM parts_used WHERE visit_id = ?", (vid,))}
            # A VISIT WE NEVER ADVISED ON CANNOT JUDGE OUR ADVICE.
            #
            # The book carries hundreds of closed visits from before this was
            # recorded. Counting them scored 460 fitted parts against zero
            # recommendations and reported "0% of fitted parts were on the
            # van" -- which reads as catastrophically bad advice and is
            # actually no advice at all. Slandering yourself with a backfill
            # is still reporting a number that is not true.
            if not advised:
                continue
            if not fitted:
                continue
            advised_total += len(advised)
            fitted_total += len(fitted)
            both += len(advised & fitted)
            for sku in advised - fitted:
                wasted[sku] = wasted.get(sku, 0) + 1
            for sku in fitted - advised:
                missed[sku] = missed.get(sku, 0) + 1

    if not advised_total and not fitted_total:
        return {"ok": True, "visits": len(visits), "judged": 0,
                "say": "No closed visit here has both a recorded "
                       "recommendation and a recorded fitting, so there is "
                       "nothing to score yet. Say that rather than implying "
                       "the advice is good."}

    return {
        "ok": True,
        "visits": len(visits),
        "advised": advised_total,
        "fitted": fitted_total,
        "both": both,
        "fitted_of_advised": round(both / advised_total, 3) if advised_total else None,
        "advised_of_fitted": round(both / fitted_total, 3) if fitted_total else None,
        # The expensive failure first: a part fitted that we never named is a
        # part that was not on the van.
        "we_did_not_name": sorted(missed.items(), key=lambda kv: -kv[1])[:5],
        "we_named_and_nobody_used": sorted(wasted.items(),
                                           key=lambda kv: -kv[1])[:5],
        "say": "Two numbers, not one. Parts fitted that we never named are "
               "the ones that cause a second visit, and that is the costly "
               "failure. Parts we named that nobody used only waste van "
               "space.",
    }


def what_this_has_cost_us(manufacturer: str = "", model_number: str = "",
                          complaint_id: str = "", dealer_id: str = "") -> dict:
    """What we have actually spent servicing this model, or this complaint.

    The number a complaint never carried. A customer's words told us a machine
    was trouble; this says how much trouble, in money, which is what decides
    whether it stays on the shelf.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)

    where = ["dealer_id = ?"]
    params: list = [dealer_id]
    if complaint_id:
        where.append("complaint_id = ?")
        params.append(complaint_id)
    if manufacturer:
        where.append("LOWER(manufacturer) = LOWER(?)")
        params.append(manufacturer)
    if model_number:
        where.append("LOWER(model_number) = LOWER(?)")
        params.append(model_number)

    with db.connect() as c:
        row = c.execute(
            f"""SELECT COUNT(*) visits,
                       ROUND(SUM(parts_cost), 2) parts,
                       ROUND(SUM(labour_cost), 2) labour,
                       ROUND(SUM(labour_hours), 2) hours
                FROM visit_cost WHERE {' AND '.join(where)}""",
            tuple(params)).fetchone()

    visits = row["visits"] or 0
    parts = row["parts"] or 0.0
    labour = row["labour"] or 0.0
    total = round(parts + labour, 2)

    return {
        "ok": True,
        "visits": visits,
        "parts_cost": parts,
        "labour_cost": labour,
        "labour_hours": row["hours"] or 0.0,
        "total": total,
        "per_visit": round(total / visits, 2) if visits else None,
        "say": ("Nothing costed here yet." if not visits else
                f"{visits} visit(s), ${total:,.2f} at cost. Say the figure "
                "plainly if somebody asks whether a model is worth keeping, "
                "and say how many visits it is built on: one expensive visit "
                "is not a pattern."),
    }
