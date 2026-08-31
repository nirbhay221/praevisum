"""Applying a live offer at the moment of the quote, instead of when asked.

WHAT WAS HAPPENING

The owner puts promotions on the record through the console. `promotion_parts`
maps them to the exact SKUs they cover, and six of those links exist and are
correct. Nothing in any pricing path read them.

Grep for who touches `promotions`: the console that writes them, outreach that
rings customers about them, and `current_deals`, which the desk calls only when
a caller thinks to ask "what offers are on?". So a customer ringing for a door
gasket was quoted $92.00 while a live promotion took 15% off door gaskets, and
the only way to get the discount was to already know it existed.

That is worse than leaving money on the table. It is a customer finding out
afterwards that there was an offer they were not told about, from a desk whose
entire proposition is that it does not do that.

WHY THIS DOES NOT ALWAYS PRODUCE A PRICE

A promotion is a headline written by a human. Two of the four in the book
reduce a unit price and can be computed:

    "10% off defrost components"
    "15% off door gaskets"

The other two do not:

    "Evaporator fan motors, buy 3 pay for 2"
    "Free first-year labour on planned maintenance"

A buy-three-pay-for-two depends on quantity, and free labour is not a discount
on a part at all. There is no discount column to read, so the only honest thing
is to compute the ones that are arithmetic, hand back the terms verbatim for
the ones that are not, and never guess a number in between. A desk that invents
"so that is about 30% off" has done the one thing this project exists to stop.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import db

# "10% off", "15 % off", "15% OFF". Deliberately narrow: this only fires on a
# phrasing that unambiguously means a percentage off a unit price, and
# everything else falls through to the terms being read out instead.
PERCENT_OFF = re.compile(r"(\d{1,2})\s*%\s*off", re.I)


def _percentage(headline: str, detail: str = "") -> int:
    m = PERCENT_OFF.search(headline or "") or PERCENT_OFF.search(detail or "")
    if not m:
        return 0
    pct = int(m.group(1))
    return pct if 0 < pct < 100 else 0


def offer_on(sku: str, dealer_id: str, tier: str = "unknown") -> dict:
    """The live offer covering a part, and what it does to the price.

    Args:
        sku: the part being quoted.
        dealer_id: whose promotions to look at.
        tier: what this caller is to us, from standing(): "on_account",
            "known", "new" or "unknown". A trade-only offer is withheld from
            "known" and "new", and deliberately allowed for "unknown", because
            at that point we have not established who they are and staying
            quiet at somebody who turns out to hold an account is a lost sale
            we caused.
    """
    from .tools import _qualifies

    today = datetime.now().date().isoformat()
    with db.connect() as c:
        rows = c.execute(
            """SELECT pr.id, pr.headline, pr.detail, pr.ends, pr.terms,
                      p.unit_cost, p.name
               FROM promotion_parts pp
               JOIN promotions pr ON pr.id = pp.promotion_id
               JOIN parts p ON p.sku = pp.sku
               WHERE pp.sku = ? AND pr.dealer_id = ?
                 AND pr.ends >= ?
                 AND (pr.starts IS NULL OR pr.starts <= ?)""",
            (sku, dealer_id, today, today)).fetchall()

    for r in rows:
        eligible, why_not = _qualifies(r["terms"] or "", tier)
        if not eligible:
            # Not an error and not something to mention. Reading somebody an
            # offer and then withdrawing it is worse than never raising it.
            continue

        pct = _percentage(r["headline"], r["detail"])
        out = {
            "applies": True,
            "promotion": r["headline"],
            "terms": r["terms"],
            "ends": r["ends"],
            "part": r["name"],
            "list_price": r["unit_cost"],
        }

        if pct and r["unit_cost"]:
            was = float(r["unit_cost"])
            now = round(was * (100 - pct) / 100.0, 2)
            out.update({
                "computed": True, "percent_off": pct,
                "was": round(was, 2), "now": now,
                "saving": round(was - now, 2),
                "say": (f"Quote {now:.2f} rather than {was:.2f} and say why: "
                        f"{r['headline']}, which runs to {r['ends']}. Tell "
                        "them about it before they ask, not after they have "
                        "agreed the full price."),
            })
        else:
            out.update({
                "computed": False,
                "say": (f"There is a live offer on this: {r['headline']}"
                        + (f" ({r['terms']})" if r["terms"] else "")
                        + f", running to {r['ends']}. Read the offer out in "
                          "those words and do NOT work out a new price from "
                          "it: this one depends on quantity or on labour, and "
                          "a number you calculated yourself is a number we "
                          "have not agreed to honour."),
            })
        return out

    return {"applies": False}


def offers_on_many(skus: list[str], dealer_id: str,
                   tier: str = "unknown") -> dict:
    """Every live offer touching a list of parts, for a whole quote."""
    hits = []
    for sku in skus:
        got = offer_on(sku, dealer_id, tier)
        if got.get("applies"):
            hits.append({"sku": sku, **got})

    if not hits:
        return {"any": False}

    saved = sum(h.get("saving", 0) for h in hits)
    return {
        "any": True, "offers": hits,
        "total_saving": round(saved, 2) if saved else 0,
        "say": "Say these before reading the total, not after. An offer "
               "mentioned after somebody has agreed a price sounds like an "
               "apology.",
    }
