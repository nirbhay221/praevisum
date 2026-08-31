"""What this business actually has for sale, with what we know about each one.

THE GAP THIS FILLS

The console listed 36 parts and not one machine. Counted across the four
businesses on this desk:

    D-REF     212 models,  546 in stock, $529 to $46,966
    D-FURN    278 models,  789 in stock
    D-AV      272 models,  692 in stock
    D-IT      161 models,  415 in stock

923 models and 2,442 units, worth up to forty-seven thousand dollars each,
and none of it was visible anywhere. A shop floor the owner could not see.

WHY A PLAIN PRODUCT LIST WOULD BE THE WRONG ANSWER

Anyone can print a table of stock. The reason to build this HERE is that this
company knows things about those machines that a catalogue does not:

  ITS OWN COMPLAINTS. 85 of them, in customers' own words, attached to a
  manufacturer and a model. "The fan rattles constantly, staff have started
  unplugging it" is worth more than a star rating.

  ITS OWN RETURNS. A model that comes back is a model to stop stocking.

  FEDERAL RECALLS. 324 from the CPSC. A recalled machine must never be
  offered, and `buying.py` already refuses to recommend one; the shop floor
  should show the owner the same thing before a customer ever asks.

  WHAT THE WORLD SAYS, kept separate. Outside ratings and market prices are
  somebody else's opinion, so they sit in their own column and never blend
  with our own record. Same separation reviews.py draws.

So this is not a stock list. It is a stock list that can say "we hold four of
these, we have had two complaints about them, and one is under a federal
recall", which is the sentence an owner actually needs before deciding what to
push.
"""

from __future__ import annotations

from . import db

# Below this, a rating is one person's bad afternoon rather than a signal.
ENOUGH_REVIEWS = 3


def _recall_index(c) -> list[tuple[str, dict]]:
    """Recalls, keyed by the brand words they name.

    Matched on brand rather than model because CPSC notices name a brand and a
    product line, not a model number, and a recall missed because the string
    did not match exactly is the expensive direction to be wrong in.
    """
    out = []
    for r in c.execute("""SELECT brands, title, hazard, recall_date, url
                          FROM recalls WHERE brands IS NOT NULL"""):
        for brand in (r["brands"] or "").split(","):
            b = brand.strip().lower()
            if len(b) > 3:
                out.append((b, {"title": r["title"], "hazard": r["hazard"],
                                "on": r["recall_date"], "url": r["url"]}))
    return out


def whats_on_the_floor(dealer_id: str = "D-REF", family: str = "",
                       limit: int = 40) -> dict:
    """The machines this business is holding, and what we know about them.

    Args:
        dealer_id: whose floor.
        family: narrow to one kind, e.g. "office chair".
        limit: how many rows.
    """
    where = ["ps.dealer_id = ?"]
    params: list = [dealer_id]
    if family.strip():
        where.append("LOWER(ps.family) = LOWER(?)")
        params.append(family.strip())

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT ps.manufacturer, ps.model_number, ps.family,
                       ps.on_hand, ps.on_order, ps.list_price, ps.unit_cost,
                       ps.lead_time_days, ps.price_source, ps.image_url
                FROM product_stock ps
                WHERE {' AND '.join(where)}
                ORDER BY ps.on_hand DESC, ps.list_price DESC
                LIMIT ?""", (*params, limit)).fetchall()

        gripes = {}
        for r in c.execute(
                """SELECT manufacturer, model_number, COUNT(*) n,
                          MIN(what) said
                   FROM complaints WHERE dealer_id = ?
                   GROUP BY manufacturer, model_number""", (dealer_id,)):
            gripes[(r["manufacturer"], r["model_number"])] = (r["n"], r["said"])

        sent_back = {}
        for r in c.execute(
                """SELECT a.manufacturer, a.model_number, COUNT(*) n
                   FROM returns rt JOIN assets a ON a.id = rt.asset_id
                   WHERE rt.dealer_id = ? GROUP BY a.manufacturer,
                         a.model_number""", (dealer_id,)):
            sent_back[(r["manufacturer"], r["model_number"])] = r["n"]

        outside = {}
        for r in c.execute("""SELECT manufacturer, model_number, rating,
                                     review_count, source
                              FROM outside_reviews"""):
            outside[(r["manufacturer"], r["model_number"])] = dict(r)

        market = {}
        for r in c.execute("""SELECT manufacturer, model_number, median_price,
                                     listings FROM market_prices"""):
            market[(r["manufacturer"], r["model_number"])] = dict(r)

        recalls = _recall_index(c)

    out = []
    for r in rows:
        key = (r["manufacturer"], r["model_number"])
        made = (r["manufacturer"] or "").lower()

        hit = next((rec for brand, rec in recalls
                    if brand and brand in made), None)

        item = {
            "manufacturer": r["manufacturer"],
            "model": r["model_number"],
            "family": r["family"],
            "on_hand": r["on_hand"] or 0,
            "on_order": r["on_order"] or 0,
            "price": r["list_price"],
            "lead_time_days": r["lead_time_days"],
            "price_source": r["price_source"],
            "image": r["image_url"],
            # Deliberately not shown to a customer. It is here because the
            # owner is the one reading this screen and margin is their
            # decision, while sourcing.py exists to stop it reaching a caller.
            "our_cost": r["unit_cost"],
        }

        if key in gripes:
            n, said = gripes[key]
            item["complaints"] = n
            item["a_customer_said"] = said

        if key in sent_back:
            item["returned"] = sent_back[key]

        if hit:
            item["recalled"] = hit

        seen = outside.get(key)
        if seen and (seen.get("review_count") or 0) >= ENOUGH_REVIEWS:
            item["outside"] = {"rating": seen["rating"],
                               "reviews": seen["review_count"],
                               "source": seen["source"]}

        priced = market.get(key)
        if priced and priced.get("median_price"):
            item["market_median"] = priced["median_price"]
            item["market_listings"] = priced.get("listings")

        out.append(item)

    # A recalled machine to the top, because it is the one thing here that
    # must be acted on rather than browsed.
    out.sort(key=lambda p: (0 if p.get("recalled") else 1,
                            -(p.get("complaints") or 0)))

    return {
        "ok": True,
        "count": len(out),
        "products": out,
        "say": ("Stock with our own record attached. A recalled machine sorts "
                "first and must not be sold. Complaints are OUR customers in "
                "their own words; ratings and market prices are somebody "
                "else's opinion and are kept in their own column."),
    }


def families_on_the_floor(dealer_id: str = "D-REF") -> list[dict]:
    """What kinds of thing this business sells, with how many of each."""
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            """SELECT family, COUNT(*) models, SUM(on_hand) held,
                      MIN(list_price) cheapest, MAX(list_price) dearest
               FROM product_stock WHERE dealer_id = ? AND family IS NOT NULL
               GROUP BY family ORDER BY held DESC""", (dealer_id,))]
