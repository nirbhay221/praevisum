"""What each product has cost us after we sold it, and what to do about it.

THE QUESTION NOTHING COULD ANSWER

A dealer decides twice a year which machines to keep buying. The number that
decides it is not the margin on the sale, it is the margin minus everything
the machine costs after it leaves: the visits, the parts, the returns, the
claims.

Every one of those was recorded and none of them were together. Service cost
sits in visit_cost, a machine sent back in returns, a claim in
warranty_claims, and a complaint carries the customer's words and no figure at
all. Answering "is this model making us money" meant joining four tables that
share nothing but a make and a model, and nothing did.

So `restock_advice` -- which is good, and does proper reorder-point control on
spare parts using real consumption -- has never once been able to say STOP
BUYING THIS FREEZER. It reorders the gaskets that freezer keeps eating without
ever asking why it keeps eating them.

WHAT THIS IS

A ledger. One row per event that cost money, posted once, attributed to a make
and model. Then two readings of it:

    what_each_product_costs_us   the roll-up, per model
    worth_restocking             that cost set against what we sold, which is
                                 the only comparison that means anything

WHY LOSS ALONE IS NOT A VERDICT

A model we have sold two hundred of will show a bigger loss than one we sold
three of, and be the better product. Cost has to be read per unit sold, and
against the margin that unit earned. The published position on total cost of
ownership is the same point: acquisition price is the smaller half, and a
supplier whose kit costs less to keep running can be worth more despite a
higher quote.

AND WHY IT REFUSES TO JUDGE SMALL SAMPLES

Three units and one expensive callout is not evidence that a model is bad. It
is one expensive callout. The same smoothing `recommend_equipment` already
applies to fault rates applies here for the same reason: without it, the
worst-looking product on the shelf is always the one we have barely sold.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from . import db

# Below this many sold, we say we do not know rather than ranking it. Matches
# the sample floor the reliability ranking already uses: the honest answer to
# "should we keep stocking this" after three sales is that it is too early.
ENOUGH_SOLD = 4


def _nid() -> str:
    return f"LOSS-{uuid.uuid4().hex[:8].upper()}"


def post(kind: str, amount: float, *, source_table: str, source_id: str,
         manufacturer: str = "", model_number: str = "", family: str = "",
         dealer_id: str = "", happened_on: str = "", account_id: str = "",
         complaint_id: str = "", note: str = "") -> dict:
    """Record one loss. Posting the same source twice is a no-op.

    Never raises. A ledger entry that could not be written is worth a log
    line; it is not worth failing the visit, return or claim that produced it.
    """
    try:
        if not amount or amount <= 0:
            return {"ok": True, "posted": False, "why": "nothing to post"}

        with db.txn() as c:
            c.execute(
                """INSERT INTO losses
                   (id, dealer_id, happened_on, kind, manufacturer,
                    model_number, family, amount, source_table, source_id,
                    account_id, complaint_id, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_table, source_id) DO UPDATE SET
                     amount = excluded.amount,
                     complaint_id = COALESCE(excluded.complaint_id,
                                             losses.complaint_id)""",
                (_nid(), dealer_id or None,
                 happened_on or datetime.now().date().isoformat(), kind,
                 manufacturer or None, model_number or None, family or None,
                 round(float(amount), 2), source_table, source_id,
                 account_id or None, complaint_id or None, note or None))
        return {"ok": True, "posted": True, "amount": round(float(amount), 2)}
    except Exception as e:
        print(f"[ledger] could not post {kind} {source_table}:{source_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": f"{type(e).__name__}"}


def post_a_visit(visit_id: str) -> dict:
    """Put a costed service visit on the ledger."""
    with db.connect() as c:
        row = c.execute(
            """SELECT visit_id, dealer_id, parts_cost, labour_cost,
                      manufacturer, model_number, family, complaint_id,
                      costed_on
               FROM visit_cost WHERE visit_id = ?""", (visit_id,)).fetchone()
    if row is None:
        return {"ok": False, "why": "that visit has not been costed"}

    total = (row["parts_cost"] or 0) + (row["labour_cost"] or 0)
    return post("service_visit", total,
                source_table="visit_cost", source_id=visit_id,
                manufacturer=row["manufacturer"] or "",
                model_number=row["model_number"] or "",
                family=row["family"] or "",
                dealer_id=row["dealer_id"] or "",
                happened_on=(row["costed_on"] or "")[:10],
                complaint_id=row["complaint_id"] or "",
                note="parts and labour at cost")


def what_each_product_costs_us(dealer_id: str = "", since: str = "",
                               limit: int = 15) -> dict:
    """What we have spent, per model, after the sale. Dearest first."""
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)

    where = ["dealer_id = ?"]
    params: list = [dealer_id]
    if since:
        where.append("happened_on >= ?")
        params.append(since)

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            f"""SELECT manufacturer, model_number, family,
                       COUNT(*) events,
                       ROUND(SUM(amount), 2) cost,
                       SUM(kind = 'service_visit') visits,
                       SUM(kind = 'return') returns_,
                       SUM(kind = 'warranty_claim') claims
                FROM losses
                WHERE {' AND '.join(where)}
                GROUP BY manufacturer, model_number
                ORDER BY cost DESC LIMIT ?""", (*params, limit))]

    return {"ok": True, "products": rows,
            "total": round(sum(r["cost"] or 0 for r in rows), 2),
            "say": "This is money spent AFTER the sale, at cost. Read it "
                   "against how many we sold before calling any of it bad: a "
                   "model we have sold two hundred of will always top a list "
                   "like this."}


def worth_restocking(dealer_id: str = "", limit: int = 12) -> dict:
    """Whether each model earns more than it costs us to keep running.

    The comparison `restock_advice` could never make. That function does
    proper reorder-point control on SPARE PARTS -- it reorders the gaskets a
    freezer keeps eating and has no way to ask why it keeps eating them. This
    asks the other question: should we still be buying the freezer.

    Margin here is the sale margin we have actually taken on that model, and
    the loss is everything the ledger has against it. Per unit sold, because
    a total is a measure of how many we sold rather than of how good it is.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)

    with db.connect() as c:
        sold = {(r["manufacturer"], r["model_number"]): r for r in c.execute(
            """SELECT ps.manufacturer, ps.model_number, ps.family,
                      COUNT(pl.po_id) units,
                      ROUND(SUM(COALESCE(pl.unit_price, 0)
                                - COALESCE(ps.unit_cost, 0)), 2) margin
               FROM purchase_lines pl
               JOIN purchase_orders po ON po.id = pl.po_id
               JOIN product_stock ps
                 ON LOWER(pl.description) LIKE
                    '%' || LOWER(ps.manufacturer) || '%'
                AND LOWER(pl.description) LIKE
                    '%' || LOWER(ps.model_number) || '%'
               WHERE po.dealer_id = ? AND po.status <> 'cancelled'
               GROUP BY ps.manufacturer, ps.model_number""",
            (dealer_id,))}

        cost = {(r["manufacturer"], r["model_number"]): r for r in c.execute(
            """SELECT manufacturer, model_number,
                      ROUND(SUM(amount), 2) cost, COUNT(*) events
               FROM losses WHERE dealer_id = ?
               GROUP BY manufacturer, model_number""", (dealer_id,))}

    verdicts = []
    for key, s in sold.items():
        make, model = key
        spent = (cost.get(key) or {}).get("cost", 0.0) or 0.0
        units = s["units"] or 0
        margin = s["margin"] or 0.0
        net = round(margin - spent, 2)

        row = {
            "manufacturer": make, "model_number": model,
            "family": s["family"], "units_sold": units,
            "margin": round(margin, 2), "cost_after_sale": round(spent, 2),
            "net": net,
            "cost_per_unit": round(spent / units, 2) if units else None,
        }

        # TOO FEW TO JUDGE, said outright rather than ranked quietly.
        #
        # Three sold and one bad callout is one bad callout, not a verdict on
        # a product. Ranking it anyway puts whatever we have barely sold at
        # the bottom of the list every time.
        if units < ENOUGH_SOLD:
            row["verdict"] = "too few sold to judge"
            row["keep_stocking"] = None
        elif net < 0:
            row["verdict"] = ("costs more after the sale than it earned on it")
            row["keep_stocking"] = False
        elif spent > margin * 0.5:
            row["verdict"] = "over half the margin goes back out in service"
            row["keep_stocking"] = True
        else:
            row["verdict"] = "earns more than it costs"
            row["keep_stocking"] = True
        verdicts.append(row)

    verdicts.sort(key=lambda v: (v["keep_stocking"] is not False, v["net"]))

    losing = [v for v in verdicts if v["keep_stocking"] is False]
    return {
        "ok": True,
        "products": verdicts[:limit],
        "stop_stocking": [f"{v['manufacturer']} {v['model_number']}"
                          for v in losing],
        "say": ("Nothing here is losing money once service is counted."
                if not losing else
                f"{len(losing)} model(s) cost more after the sale than they "
                "earned on it. Say the figure and the number of units it is "
                "built on, never the verdict alone: a recommendation to drop "
                "a product should be arguable by whoever hears it."),
    }
