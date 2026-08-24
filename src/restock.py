"""What to put on the shelf, which is the van-loading decision slowed down.

Split out of ops.py. Deliberately uses the same two numbers as the van
loading: a wasted trip, and the cost of stock sitting still. If they
disagreed the desk would refuse to stock a part it would happily send out.
"""


from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from . import db
from .domain.geo import drive_minutes, miles
from .thresholds import *  # noqa: F401,F403



def _nid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:6].upper()}"


def _spoken_window(start: datetime, end: datetime) -> str:
    """A time window the way a person would say it down the phone.

    Built by hand rather than with strftime because the no-padding directive
    differs between platforms, and this runs on Windows in development and
    Linux in production.
    """
    def clock(t: datetime) -> str:
        hour = t.hour % 12 or 12
        suffix = "am" if t.hour < 12 else "pm"
        return f"{hour}{suffix}" if t.minute == 0 else f"{hour}:{t.minute:02d}{suffix}"

    today = datetime.now().date()
    if start.date() == today:
        day = "today"
    elif start.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = start.strftime("%A")
    return f"{day} {clock(start)} to {clock(end)}"

# WHAT TO PUT ON THE SHELF
# ==========================================================================

# The same numbers the van loading uses, deliberately. Carrying a part and
# stocking a part are the same decision at different timescales, and if the
# two used different costs the desk would contradict itself: refusing to hold
# a part it would happily send out in a van.
from .reason import CARRY_RATE, TRUCK_ROLL  # noqa: E402

# How often the owner is expected to look at this. Ordering has to cover the
# lead time plus the gap until somebody looks again, or the shelf runs dry
# between reviews no matter how good the arithmetic is.

# 1.65 standard deviations is a 95% service level: we accept running out
# roughly one time in twenty. Not 100%, because covering every conceivable
# spike means holding stock that mostly sits there, and the whole point of
# this is that stock sitting still has a cost.

# How long a complaint stays predictive. Measured on this book, a customer
# raises the grumble about 41 days before the repair closes, so a quarter is
# generous and anything older has already either been fixed or forgotten.

# How often the corpus, given a complaint's wording, names a part the repair
# actually used. Measured against the 35 complaints that genuinely preceded a
# repair: 66%, against roughly 20% for guessing. Not a guess and not a
# certainty, so it is applied as a discount rather than believed.


def _complaint_demand(dealer_id: str) -> dict:
    """Parts that open complaints are quietly pointing at.

    A service call is a lagging indicator: by the time somebody rings, the
    machine has already failed. A complaint is a leading one. Measured on this
    book, a customer raises the grumble about 41 days before the repair closes,
    which is more than enough warning to have the part on the shelf.

    The complaint is not labelled with a part, and could not be: the customer
    says "there is a rattle at the back that is getting worse", not
    "P-EVAPFAN". So the text goes through the same corpus retrieval the van
    loading uses, and comes back with the causes that description has
    historically turned out to be.

    Measured against the repairs these complaints actually preceded, the top
    two causes name the right part 66% of the time, against roughly 20% for
    guessing. Real, and nowhere near certain, which is why this is reported as
    a separate number rather than folded silently into the demand history.
    """
    from .reason import _fault_distribution

    expected: dict[str, float] = {}
    with db.connect() as c:
        # Only complaints still inside the warning window. The first version
        # counted every open complaint ever raised, which on this book is 51
        # of them going back years, and produced a $4,487 order off grumbles
        # that had long since either been fixed or forgotten. A complaint from
        # two years ago is not a warning, it is history.
        recent = c.execute(
            """SELECT what, manufacturer, model_number, family FROM complaints
               WHERE dealer_id = ? AND status = 'open'
                 AND JULIANDAY('now') - JULIANDAY(raised_at) <= ?
               ORDER BY raised_at DESC LIMIT 60""",
            (dealer_id, WARNING_WINDOW_DAYS)).fetchall()

        # How often a complaint actually turns into a job, measured rather
        # than assumed. Treating every grumble as a part sale in waiting is
        # what made the first version order stock for complaints about noise.
        row = c.execute(
            """SELECT COUNT(*) n,
                      SUM(CASE WHEN predicted_repair IS NOT NULL THEN 1 ELSE 0 END) became
               FROM complaints WHERE dealer_id = ?""", (dealer_id,)).fetchone()
    conversion = ((row["became"] or 0) / row["n"]) if row and row["n"] else 0.0

    for cm in recent:
        try:
            dist = _fault_distribution(dealer_id, cm["what"], cm["manufacturer"],
                                       cm["family"] or "", cm["model_number"] or "")
        except Exception as e:
            # Loudly, because a silent `except: continue` here hid a missing
            # column for weeks: every complaint raised a KeyError and the
            # forecast quietly became zero with nothing to show for it.
            print(f"[restock] complaint signal skipped: {type(e).__name__}: {e}",
                  flush=True)
            continue
        for cause in dist[:2]:
            for sku in cause["parts"]:
                # probability this cause, times the chance the complaint
                # becomes a job at all, times how often the retrieval names
                # the right part. Three honest discounts rather than one
                # confident number.
                expected[sku] = (expected.get(sku, 0.0)
                                 + cause["probability"] * conversion * PREDICTION_ACCURACY)
    return expected


def restock_advice(dealer_id: str = "D-REF", horizon_days: int = 365) -> dict:
    """What to reorder, how many, and what it costs to be wrong.

    Reorder-point stock control, with the demand rate taken from parts this
    dealer has actually consumed rather than from a number somebody typed in.

    The cost of being short is not the price of the part. It is a technician
    driving out and not being able to finish, which is the same wasted trip
    the van loading prices at a few hundred dollars. That is why a cheap part
    with a long lead time can be more urgent than an expensive one.

    Args:
        dealer_id: whose shelf.
        horizon_days: how far back to measure consumption. A year smooths out
            seasonality without letting a part that stopped being used three
            years ago keep generating orders.

    Returns:
        Lines to order with the arithmetic behind each one, and the parts that
        are fine, so the owner can see what was considered rather than only
        what was flagged.
    """
    cutoff = (datetime.now() - timedelta(days=horizon_days)).date().isoformat()

    with db.connect() as c:
        parts = {r["sku"]: r for r in c.execute(
            """SELECT sku, name, unit_cost, lead_time_days, families
               FROM parts WHERE dealer_id=?""", (dealer_id,))}
        if not parts:
            return {"ok": False, "why": "this dealer has no catalogue"}

        free = {r["sku"]: r["f"] or 0 for r in c.execute(
            """SELECT s.sku, SUM(s.free) f FROM stock_available s
               JOIN stock_locations l ON l.id = s.location_id
               WHERE l.dealer_id=? GROUP BY s.sku""", (dealer_id,))}

        # Consumption, from closed jobs. parts_consumed is a comma separated
        # list rather than a join table, so it is unpacked here rather than in
        # SQL: readable beats clever, and the corpus is small.
        used: dict[str, int] = {}
        for r in c.execute(
                """SELECT parts_consumed FROM repairs
                   WHERE dealer_id=? AND closed_on >= ?""", (dealer_id, cutoff)):
            for sku in (r["parts_consumed"] or "").split(","):
                sku = sku.strip()
                if sku:
                    used[sku] = used.get(sku, 0) + 1

        # Parts that came back and went on the shelf. Without this the advice
        # reorders something already sitting in a box by the door, which is
        # exactly the money this feature exists to stop wasting.
        returned = {r["sku"]: r["back_on_shelf"] or 0
                    for r in c.execute("SELECT sku, back_on_shelf FROM parts_returned")}

        on_order = {r["sku"]: r["q"] for r in c.execute(
            """SELECT l.sku, SUM(l.qty) q FROM purchase_lines l
               JOIN purchase_orders p ON p.id = l.po_id
               WHERE p.status IN ('draft','confirmed','picked')
               GROUP BY l.sku""")}

        # Where to actually buy it. Vendors ring this desk to pitch and their
        # quotes were being filed and forgotten, while the reorder used the
        # catalogue lead time. A supplier who quoted three days against a nine
        # day catalogue is the difference between a job finishing this week
        # and a customer waiting a fortnight.
        quotes: dict[str, list] = {}
        for r in c.execute(
                """SELECT s.name, s.phone, o.offering, o.price_quoted, o.lead_time
                   FROM supplier_offers o
                   JOIN suppliers s ON s.id = o.supplier_id
                   WHERE s.dealer_id = ? AND o.status <> 'expired'
                   ORDER BY o.logged_at DESC""", (dealer_id,)):
            quotes.setdefault(r["offering"] or "", []).append(r)

    coming = _complaint_demand(dealer_id)

    order, fine = [], []
    for sku, p in parts.items():
        consumed = used.get(sku, 0)
        per_day = consumed / horizon_days
        lead = p["lead_time_days"] or 0
        have = free.get(sku, 0) + (on_order.get(sku) or 0)

        # Cover the lead time AND the wait until somebody looks again.
        #
        # The first version of this covered only the lead time, which is the
        # continuous-review model: correct when stock is watched every day.
        # Nobody watches this every day, which is why REVIEW_DAYS exists. With
        # a monthly review and a lead-time-only trigger, a part gets reordered
        # when it drops to about one unit, then runs out three weeks before
        # anyone looks again. The arithmetic was right and the model was
        # answering a question nobody had asked.
        exposure = lead + REVIEW_DAYS
        during_exposure = per_day * exposure

        # What customers have already told us is coming. Added on top of the
        # historical rate rather than replacing it, because the history is
        # what usually happens and this is what is unusual about right now.
        warned = coming.get(sku, 0.0)
        during_exposure += warned
        # Poisson: for count data the variance equals the mean, so the spread
        # is the square root. Cheap, standard, and honest about the fact that
        # demand is lumpy rather than smooth.
        safety = SERVICE_Z * math.sqrt(during_exposure) if during_exposure > 0 else 0
        reorder_point = during_exposure + safety
        target = reorder_point + per_day * REVIEW_DAYS

        came_back = returned.get(sku, 0)

        row = {
            "sku": sku, "name": p["name"],
            "used_in_last_year": consumed,
            "returned_to_shelf": came_back,
            "per_month": round(per_day * 30, 2),
            "lead_time_days": lead,
            "in_stock": free.get(sku, 0),
            "on_order": on_order.get(sku) or 0,
            "reorder_at": round(reorder_point, 1),
            "unit_cost": p["unit_cost"],
        }
        if warned >= 0.5:
            row["warned_by_complaints"] = round(warned, 1)
            row["complaint_note"] = (
                f"recent complaints point at this part, worth about "
                f"{warned:.1f} extra units once discounted for how often a "
                f"complaint becomes a job. Customers notice roughly six weeks "
                f"before they ring, so this has not reached the repair "
                f"history yet")

        if consumed == 0 and warned < 1:
            row["note"] = ("not used once in the last year. Do not reorder, "
                           "and ask whether it should be on the shelf at all")
            fine.append(row)
            continue

        if have > reorder_point:
            row["note"] = (f"{have} on hand covers the {lead} day lead time "
                           f"plus the {REVIEW_DAYS} days until the next review, "
                           f"at {row['per_month']} a month")
            fine.append(row)
            continue

        qty = max(1, math.ceil(target - have))
        # What it costs to be caught short: a technician arrives and cannot
        # finish. Priced at the same wasted trip the van loading uses.
        shortfall_risk = TRUCK_ROLL * (1 + min(lead, 10) / 10)
        holding = (p["unit_cost"] or 0) * CARRY_RATE * (REVIEW_DAYS / 30)

        # Match a quote to this part by name or code appearing in what the
        # vendor said. Deliberately loose, because a rep says "defrost
        # thermostats" not "P-DEFROSTTHE", and deliberately advisory: a quote
        # is a quote, not a delivery.
        matched = []
        for offering, rows in quotes.items():
            low = offering.lower()
            if sku.lower() in low or (p["name"] or "").lower() in low:
                for q in rows[:1]:
                    matched.append({"supplier": q["name"], "phone": q["phone"],
                                    "quoted": q["offering"],
                                    "price": q["price_quoted"],
                                    "lead_time": q["lead_time"]})

        row.update({
            "order_qty": qty,
            "line_cost": round(qty * (p["unit_cost"] or 0), 2),
            "why": (f"{have} left, and we get through {row['per_month']} a "
                    f"month with a {lead} day wait to replace them"),
            "cost_of_being_short": round(shortfall_risk, 2),
            "cost_of_holding_it": round(holding * qty, 2),
            "urgency": ("out of stock" if have <= 0 else
                        "below the reorder point"),
        })
        if matched:
            row["suppliers_who_quoted"] = matched
            row["note"] = ("a supplier has quoted on this recently. Check "
                           "whether they beat the catalogue lead time before "
                           "ordering, but do not treat a quote as a delivery")
        order.append(row)

    order.sort(key=lambda r: (r["in_stock"] > 0, -r["cost_of_being_short"]))

    return {
        "ok": True,
        "dealer": dealer_id,
        "order": order,
        "no_action": fine,
        "total_cost": round(sum(r["line_cost"] for r in order), 2),
        "how_it_was_worked_out": {
            "reorder_point": f"consumption per day x (lead time + "
                             f"{REVIEW_DAYS} days until the next review), plus "
                             f"a safety margin of 1.65 standard deviations for "
                             f"a 95% service level. The review period is in "
                             f"there because stock that only covers the lead "
                             f"time runs out between reviews",
            "order_quantity": f"enough to cover the lead time plus {REVIEW_DAYS} "
                              f"days until the next review",
            "cost_of_being_short": f"a wasted trip at ${TRUCK_ROLL:.0f}, worse "
                                   f"when the part then has to be waited for",
            "measured_over": f"{horizon_days} days of closed jobs",
            "complaint_signal": "open complaints are run through the repair "
                                "corpus to see which parts that description "
                                "has historically turned out to need. Measured "
                                "against the repairs those complaints actually "
                                "preceded, it names the right part 66% of the "
                                "time, against about 20% for guessing",
        },
        "advice": "These are the parts whose stock will not survive their own "
                  "lead time at the rate we actually use them. Nothing here is "
                  "a forecast of the future; it is what the last year did.",
    }


# ==========================================================================
