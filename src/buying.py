"""What to buy, judged against what has actually broken in our own vans.

Split out of ops.py. Retailers rank by review scores written by people who
owned a thing for a week. This ranks by field failures, complaints and
returns, over the number we actually supplied, and refuses to have an
opinion below a real sample.
"""


from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta

from . import db
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

# WHAT THEY SHOULD BUY
# ==========================================================================

# What a customer says, and the field it actually means.
#
# Every one of these is a REAL column on the EnergyStar catalogue, sitting on
# 88,544 rows, and none of it was searchable: recommend_equipment filtered on
# family and budget and nothing else. So somebody who said "glass door" or
# "it cannot be propane, we have no ventilation" got whatever the ranking
# happened to put first, and had to be talked out of it afterwards.
#
# The refrigerant one is not a preference. R-290 is flammable and charge
# limited, and this system already refuses to send an uncertified technician
# near it. It could not take the same fact into account when SELLING one.
DOORS = {
    "glass": "%Transparent Door%",
    "transparent": "%Transparent Door%",
    "solid": "%Solid Door%",
}

FLAMMABLE_REFRIGERANTS = ("R-290", "R-600a", "R-170", "R-441A", "R-1234")


def find_equipment(family: str = "", door: str = "", min_cuft: float = 0.0,
                   max_cuft: float = 0.0, max_daily_kwh: float = 0.0,
                   refrigerant: str = "", no_flammable_refrigerant: bool = False,
                   budget_max: float = 0.0, limit: int = 5) -> dict:
    """Find machines matching what the customer actually asked for.

    Everything here is a real field on the certification catalogue, so a
    preference either matches something or it does not, and we say which.
    Nothing is inferred and nothing is invented.

    Args:
        family: reach-in freezer, reach-in cooler, display cooler, ice machine.
        door: "glass" if they want to see the stock, "solid" if they do not.
        min_cuft: smallest usable size, in cubic feet.
        max_cuft: largest that will fit the space they have.
        max_daily_kwh: a ceiling on running cost, in kilowatt hours a day.
        refrigerant: a specific one, if they asked by name.
        no_flammable_refrigerant: true if their kitchen cannot take R-290 or
            similar. Some cannot: it is flammable and charge limited, which is
            a ventilation and insurance question rather than a preference.
        budget_max: a price ceiling. 0 means no limit.
        limit: how many to return.
    """
    # THE COLUMN IS CALLED daily_kwh AND HOLDS TWO DIFFERENT UNITS.
    #
    # ENERGY STAR publishes commercial equipment in kWh per DAY and
    # residential in kWh per YEAR, and the loader poured both into one
    # column. Counted across the catalogue:
    #
    #     Commercial Refrigerators and Freezers   3,856    0.26 .. 33.58
    #     Certified Residential Refrigerators     4,525      42 .. 805
    #     Certified Room Air Conditioners           447     260 .. 2096
    #
    # So this filter has been comparing kWh-a-day against kWh-a-year in the
    # same ORDER BY. A residential fridge at "42" looked nine times worse
    # than a commercial one at "4.6" when it actually uses about a tenth as
    # much, and max_daily_kwh silently excluded almost every residential unit
    # while admitting commercial ones that should have failed.
    #
    # Restricting to the commercial datasets fixes the arithmetic AND is the
    # right catalogue anyway: market.py already screens domestic machines out
    # of commercial kitchens, because a household freezer in a restaurant has
    # no NSF rating and a warranty void on business use. Offering one from
    # the certification catalogue was the same mistake by another route.
    where = ["e.site_visit = 1", "e.daily_kwh IS NOT NULL",
             "e.dataset LIKE '%Commercial%'",
             # A row with no model number cannot be quoted, ordered or looked
             # up later. It was being offered as the cheapest match.
             "e.model_number IS NOT NULL", "TRIM(e.model_number) != ''",
             "e.product_type IS NOT NULL"]
    params: list = []

    fam = (family or "").strip().lower()
    if fam:
        where.append("e.product_type LIKE ?")
        params.append(_family_like(fam))

    d = (door or "").strip().lower()
    if d in DOORS:
        where.append("e.product_type LIKE ?")
        params.append(DOORS[d])

    # CAST, because the catalogue stores these as TEXT. Without it the
    # comparison is done on strings: asking for 15 cubic feet and up returned
    # a 2.3 cubic foot countertop, because "2.3" sorts after "15".
    if min_cuft:
        where.append("CAST(e.capacity AS REAL) >= ?")
        params.append(min_cuft)
    if max_cuft:
        where.append("CAST(e.capacity AS REAL) <= ?")
        params.append(max_cuft)
    if max_daily_kwh:
        where.append("CAST(e.daily_kwh AS REAL) <= ?")
        params.append(max_daily_kwh)
    if refrigerant.strip():
        where.append("UPPER(e.refrigerant) LIKE ?")
        params.append(f"%{refrigerant.strip().upper()}%")
    if no_flammable_refrigerant:
        for r in FLAMMABLE_REFRIGERANTS:
            where.append("UPPER(COALESCE(e.refrigerant,'')) NOT LIKE ?")
            params.append(f"%{r.upper()}%")

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT e.brand, e.model_number, e.product_type, e.capacity,
                       e.daily_kwh, e.refrigerant, e.defrost_type,
                       e.ice_lbs_day, e.water_gal_100lbs
                FROM equipment e
                WHERE {' AND '.join(where)}
                GROUP BY e.brand, e.model_number
                ORDER BY CAST(e.daily_kwh AS REAL) ASC
                LIMIT ?""", (*params, limit * 6)).fetchall()

    asked = {k: v for k, v in (
        ("family", family), ("door", door), ("min_cuft", min_cuft),
        ("max_cuft", max_cuft), ("max_daily_kwh", max_daily_kwh),
        ("refrigerant", refrigerant),
        ("no_flammable_refrigerant", no_flammable_refrigerant)) if v}

    if not rows:
        return {
            "ok": True, "matches": [], "asked_for": asked,
            "say": "Nothing in the certification catalogue matches all of "
                   "that. Say which condition is the tight one and ask if "
                   "they can move on it, rather than quietly dropping one of "
                   "their requirements and offering something that does not "
                   "do what they asked for.",
        }

    # Everything below here is the existing evidence: our own fault history,
    # complaints, returns and federal recalls. A preference filter that
    # ignored those would cheerfully offer a recalled machine because it had
    # the right door.
    ranked = _with_our_own_evidence([dict(r) for r in rows], limit, fam)
    ranked, over = _inside_the_budget(ranked, budget_max)

    if budget_max and not ranked:
        return {
            "ok": True, "matches": [], "asked_for": asked,
            "over_their_ceiling": over[:5],
            "say": f"Nothing we can sell comes in at or under "
                   f"${budget_max:,.2f}. Say so plainly and say what the "
                   "nearest one actually costs. Do NOT offer a machine over "
                   "their ceiling as though it met it, and do not read out a "
                   "price for something we cannot price.",
        }

    # REGISTERED, LIKE options_under DOES.
    #
    # The register only knew about one of the two ways the desk puts machines
    # in front of somebody, so a caller who was read a list from HERE and then
    # picked one had their order refused as "not one of the ones you read out
    # to them". True of the register, false of the conversation, and the desk
    # ended up apologising for an item it had itself offered.
    from .shortlist import we_offered

    ranked = we_offered(ranked)

    return {
        "ok": True,
        "asked_for": asked,
        "matches": ranked,
        "over_their_ceiling": over[:3] if budget_max else [],
        "say": "Read back WHICH of their requirements each one meets, in their "
               "own terms: the size, the door, the refrigerant, and what it "
               "costs. A list of model numbers is not an answer to somebody "
               "who told you what they needed.",
    }



def _what_it_costs(row: dict) -> float | None:
    """Our shelf price for a candidate, or nothing if we do not sell it.

    Our own shelf price first, because for something we stock that IS the
    price. Then whatever a previous lookup already found and cached, which
    covers the certification catalogue: those rows carry efficiency and
    refrigerant and nothing about money, so almost none of them are things we
    hold, and refusing to use a price we already have would leave every
    ceiling matching nothing at all.

    Never a fresh web search. `price_for` runs one per machine, which is slow
    enough to be heard on a phone call and is billed per query; doing it for
    every row of a five-result search would be five of them.
    """
    # The two searches hand their rows over under different names:
    # find_equipment renames model_number to model on the way out, and
    # recommend_equipment keeps the catalogue's own column names.
    make = row.get("brand") or row.get("manufacturer") or ""
    model = row.get("model") or row.get("model_number") or ""
    try:
        from .market import _cached, _our_own_price

        got = _our_own_price(make, model)
        if got:
            _remember_we_priced(make, model, got["price"], "our price list")
            return got["price"]

        hit = _cached(make, model)
        if hit and hit["median_price"]:
            _remember_we_priced(make, model, hit["median_price"], "market median")
            return float(hit["median_price"])
    except Exception:
        pass
    return None


def _remember_we_priced(make: str, model: str, price, where_from: str) -> None:
    """Keep a search result's price against this call.

    A search hands the desk five machines with prices on them and the customer
    picks one by name. Without this the order goes back to guessing a make and
    model out of "order the Continental UC24N refrigerator", which is the
    lookup that wrote $0.00 onto a confirmed order.
    """
    try:
        from .quoted import we_said

        we_said(make, model, float(price or 0), where_from)
    except Exception:
        pass


def _inside_the_budget(rows: list[dict], budget_max: float) -> tuple[list[dict], list[dict]]:
    """Split candidates by whether we can actually sell them at that price.

    THE CEILING WAS A DECORATION.

    `budget_max` has been an argument on this function and on
    recommend_equipment since they were written, is documented as "a price
    ceiling", and was never once read. Both search `equipment`, the EPA
    certification catalogue, which carries efficiency and refrigerant and no
    price at all, so the number a customer gave went into the call and
    straight back out of it.

    HEARD ON A LIVE CALL. The caller asked for a refrigerator "under $200".
    The desk searched, found nothing priced, asked whether there was any
    flexibility in the budget, and then offered a Continental UC24N at
    $3,100.23 -- fifteen times what they said they could spend -- and took the
    order. Nothing was broken in the sense of throwing an error. The filter
    simply was not there.

    A machine we have no price for is NOT quietly kept. When somebody names a
    ceiling, "we cannot tell you what this costs" is not evidence that it is
    under the ceiling, and offering it as though it were is how the above
    happened.
    """
    if not budget_max or budget_max <= 0:
        for r in rows:
            price = _what_it_costs(r)
            if price is not None:
                r["our_price"] = price
        return rows, []

    keep: list[dict] = []
    out: list[dict] = []
    for r in rows:
        price = _what_it_costs(r)
        if price is None:
            r["our_price"] = None
            r["why_not"] = "we have no price for this one"
            out.append(r)
        elif price > budget_max:
            r["our_price"] = price
            r["why_not"] = f"${price:,.2f}, over their ceiling"
            out.append(r)
        else:
            r["our_price"] = price
            keep.append(r)
    return keep, out


def _with_our_own_evidence(rows: list[dict], limit: int,
                           family: str = "") -> list[dict]:
    """Annotate matched machines with what WE know about them.

    A preference filter on its own is a catalogue search, and a catalogue
    search will cheerfully offer a machine the government has recalled because
    it happens to have the right door. Our own service record, the complaints,
    the returns and the federal recalls are the entire reason this desk is
    worth ringing instead of reading a spec sheet.

    Recalled machines are not ranked down, they are marked and pushed last,
    because the desk must be able to say WHY it is not offering one.
    """
    with db.connect() as c:
        installed = {(r["manufacturer"], r["model_number"]): r["installed"]
                     for r in c.execute("SELECT * FROM model_installed")}
        faults = {(r["manufacturer"], r["model_number"]): r["faults"]
                  for r in c.execute("SELECT * FROM model_reliability")}
        gripes = {(r["manufacturer"], r["model_number"]): r["complaints"]
                  for r in c.execute("SELECT * FROM model_complaints")}
        recalled = {}
        for r in c.execute("""SELECT brands, title, hazard, recall_date, url
                              FROM recalls WHERE brands IS NOT NULL"""):
            recalled.setdefault((r["brands"] or "").lower(), r)

    out = []
    for r in rows:
        key = (r["brand"], r["model_number"])
        n_in = installed.get(key, 0) or 0
        against = (faults.get(key, 0) or 0) + (gripes.get(key, 0) or 0)
        # The family MUST be passed. _recall_for requires the recall to be
        # about this kind of machine, so calling it with an empty family
        # silently matched nothing and a recalled Kelvinator was offered as a
        # recommendation.
        recall = _recall_for(recalled, r["brand"], r["model_number"], family)

        is_recalled = bool(recall and recall.get("kind") == "machine")
        if is_recalled:
            rr = recall["row"]
            note = (f"UNDER SAFETY RECALL ({rr['recall_date']}): "
                    f"{(rr['hazard'] or rr['title'] or '')[:120]}")
        elif not n_in:
            note = "not one we have supplied, so we have no history on it"
        elif n_in < MIN_SAMPLE:
            note = (f"only {n_in} in service with us, too few for a real "
                    "opinion")
        elif against == 0:
            note = f"{n_in} in service and nothing raised against them"
        else:
            note = f"{n_in} in service, {against} thing(s) raised"

        m = {
            "brand": r["brand"], "model": r["model_number"],
            "type": r["product_type"], "capacity_cuft": r["capacity"],
            "daily_kwh": r["daily_kwh"], "refrigerant": r["refrigerant"],
            "defrost": r["defrost_type"],
            "recalled": is_recalled,
            "units_in_service": n_in,
            "our_experience": note,
        }

        # An ice machine is not sized in cubic feet. It is sized in pounds of
        # ice a day, and the trade rule of thumb is 1.5 to 2 lb per cover, so
        # this is the number that answers "will it keep up on a Saturday".
        # Water goes with it because an ice machine is a plumbed appliance and
        # its consumption is a running cost people are not expecting.
        if _row_get(r, "ice_lbs_day"):
            m["ice_lbs_day"] = r["ice_lbs_day"]
            m["sizing"] = (f"makes {r['ice_lbs_day']:.0f} lb of ice a day, "
                           "which covers roughly "
                           f"{int(r['ice_lbs_day'] / 2)} to "
                           f"{int(r['ice_lbs_day'] / 1.5)} covers at the "
                           "usual 1.5 to 2 lb a cover")
            if _row_get(r, "water_gal_100lbs"):
                m["water_gal_100lbs"] = r["water_gal_100lbs"]

        out.append(m)

    # Recalled last, then the ones we actually know something about, then by
    # running cost.
    out.sort(key=lambda x: (x["recalled"], x["units_in_service"] == 0,
                            x["daily_kwh"] or 999))
    return out[:limit]


# Nobody says "reach-in freezer" on the phone. They say "a freezer", "the
# big fridge", "an ice machine". The exact-phrase table below used to fall
# back to "%" for anything it did not recognise, which is a wildcard: asking
# for a "freezer" matched every refrigerator, ice machine and air conditioner
# in the catalogue, and the desk read the cheapest one out as a freezer.
#
# Longest phrase first, because "display cooler" has to beat "cooler".
_FAMILY_PHRASES = [
    ("reach-in freezer", "%Solid Door Freezer%"),
    ("reach-in cooler", "%Solid Door Refrigerator%"),
    ("display freezer", "%Transparent Door Freezer%"),
    ("display cooler", "%Transparent Door Refrigerator%"),
    ("glass door freezer", "%Transparent Door Freezer%"),
    ("glass door cooler", "%Transparent Door Refrigerator%"),
    ("walk-in cooler", "%Refrigerator%"),
    ("ice machine", "%Ice Making%"),
    ("ice maker", "%Ice Making%"),
    ("freezer", "%Freezer%"),
    ("cooler", "%Refrigerator%"),
    ("refrigerator", "%Refrigerator%"),
    ("fridge", "%Refrigerator%"),
    ("ice", "%Ice Making%"),
]

# What this catalogue is. ENERGY STAR certifies commercial refrigeration, and
# nothing else this desk sells: no chairs, no laptops, no displays. Since the
# desk went multi-vendor that matters, because a furniture call reaching this
# tool must be told there is no catalogue rather than shown a fridge.
NOT_IN_THIS_CATALOGUE = "__no_such_family__"


def _row_get(r, key):
    """A column that only exists once the ice backfill has run.

    sqlite3.Row raises rather than returning None for a name it does not have,
    so a database that has not been migrated yet must not crash the catalogue.
    """
    try:
        return r[key]
    except (IndexError, KeyError):
        return None


def _family_like(fam: str) -> str:
    """The catalogue phrase behind what somebody actually said.

    Returns NOT_IN_THIS_CATALOGUE for a family this catalogue does not carry,
    so an unrecognised request finds nothing instead of finding everything.
    """
    f = (fam or "").strip().lower()
    if not f:
        return "%"
    for phrase, like in _FAMILY_PHRASES:
        if phrase in f:
            return like
    return NOT_IN_THIS_CATALOGUE


def recommend_equipment(family: str, budget_max: float = 0.0,
                        limit: int = 5) -> dict:
    """Suggest machines, ranked by what has actually broken in our own vans.

    Every retailer ranks by review score. We rank by field failures, because we
    are the ones who drove out to fix them. A model with repeated faults in our
    own book is a bad recommendation regardless of its rating anywhere else.

    Args:
        family: what they want, e.g. "reach-in freezer", "ice machine".
        budget_max: optional ceiling. 0 means no limit.
        limit: how many to return.

    Returns:
        Candidates with efficiency from the EPA certification data, and our own
        fault history where we have any. Says plainly when we have no history.
    """
    fam = (family or "").strip().lower()
    if not fam:
        return {"ok": False, "why": "no equipment type given"}

    like = {
        "reach-in freezer": "%Solid Door Freezer%",
        "reach-in cooler": "%Solid Door Refrigerator%",
        "display cooler": "%Transparent Door Refrigerator%",
        "walk-in cooler": "%Refrigerator%",
    }.get(fam, "%")

    # Ranking by fewest faults sounds right and is useless: everything we have
    # never touched scores zero and floats to the top, so the answer is a list
    # of machines we know nothing about. What a customer actually wants is our
    # experience, so models we have in service rank first, worst-performing
    # ones are surfaced as warnings rather than hidden, and machines we have no
    # history for come last and say so.
    with db.connect() as c:
        rows = c.execute(
            """SELECT e.brand, e.model_number, e.product_type, e.daily_kwh,
                      e.refrigerant, e.capacity,
                      r.faults, r.units_affected, r.first_visit_fix_pct,
                      i.installed,
                      -- Smoothed, not raw. A raw rate of zero over one machine
                      -- beats a rate of 0.1 over forty, so the model we know
                      -- nothing about outranks the one we have genuinely
                      -- proven. Adding a notional one problem and two units
                      -- pulls tiny samples back towards the middle, which is
                      -- where honest uncertainty belongs.
                      (CAST(COALESCE(r.faults,0) + COALESCE(g.complaints,0)
                            AS REAL) + 1.0)
                        / (COALESCE(i.installed,0) + 2.0) AS trouble_rate
               FROM equipment e
               LEFT JOIN model_reliability r
                      ON r.manufacturer = e.brand AND r.model_number = e.model_number
               LEFT JOIN model_installed i
                      ON i.manufacturer = e.brand AND i.model_number = e.model_number
               LEFT JOIN model_complaints g
                      ON g.manufacturer = e.brand AND g.model_number = e.model_number
               WHERE e.site_visit = 1 AND e.product_type LIKE ?
                 AND e.daily_kwh IS NOT NULL
               GROUP BY e.brand, e.model_number
               ORDER BY
                 CASE WHEN i.installed IS NULL THEN 1 ELSE 0 END,   -- known first
                 trouble_rate ASC,                                  -- proven first
                 e.daily_kwh ASC                                    -- then cheap to run
               LIMIT ?""", (like, limit * 4)).fetchall()

    # Complaints are the other half of the evidence and they arrive by a
    # different route. A service call means something broke badly enough to
    # send a van; a complaint is everything else the customer told us. Both
    # count against a machine, so both are counted.
    with db.connect() as c:
        gripes = {(r["manufacturer"], r["model_number"]):
                  (r["complaints"], r["severe"], r["categories"])
                  for r in c.execute("SELECT * FROM model_complaints")}

        # Returns, counted separately and weighted heavier. A complaint is
        # annoyance; a return is a customer deciding they would rather have
        # nothing than keep it. Only returns blamed on the machine count:
        # somebody ordering the wrong thing says nothing about the model.
        given_back = {(r["manufacturer"], r["model_number"]):
                      r["blamed_on_machine"] or 0
                      for r in c.execute("SELECT * FROM model_returns")}

        # Federal safety recalls. We have been loading these since the start
        # and only the service side ever read them, so the buying side could
        # recommend a machine that the government has recalled. That is not a
        # thin recommendation, it is a wrong one, and it outranks every other
        # signal here: no fault rate is good enough to offset a shock hazard.
        recalled = {}
        for r in c.execute(
                """SELECT brands, title, hazard, recall_date, url
                   FROM recalls WHERE brands IS NOT NULL"""):
            recalled.setdefault((r["brands"] or "").lower(), r)

    picks = []
    for r in rows:
        faults = r["faults"] or 0
        installed = r["installed"] or 0
        n_gripe, severe, cats = gripes.get((r["brand"], r["model_number"]),
                                           (0, 0, None))
        n_back = given_back.get((r["brand"], r["model_number"]), 0)
        against = faults + n_gripe + n_back * RETURN_WEIGHT

        recall = _recall_for(recalled, r["brand"], r["model_number"], fam)

        if recall is not None and recall["kind"] == "machine":
            # Checked before anything else. A clean service record on a
            # recalled machine is not reassurance, it just means the hazard
            # has not reached our customers yet.
            rr = recall["row"]
            note = (f"UNDER SAFETY RECALL ({rr['recall_date']}): "
                    f"{(rr['hazard'] or rr['title'] or '')[:120]}")
            verdict = "recalled, do not recommend"
        elif not installed:
            note = "not one we have supplied, so we have nothing to tell you about it"
            verdict = "no history"
        elif installed < MIN_SAMPLE:
            # The bug this replaced: one machine, no faults, "recommended".
            # A clean record across a handful of units is not a clean record,
            # it is an absence of information, and saying so is the honest
            # answer to somebody about to spend thousands.
            note = (f"we have only {installed} of these in service, which is "
                    f"too few for us to have a real opinion"
                    + (f". {against} thing{'s' if against != 1 else ''} raised "
                       f"so far" if against else ", and nothing raised so far"))
            verdict = "too few to judge"
        elif against == 0:
            note = (f"{installed} in service and not one service call, "
                    f"complaint or return between them")
            verdict = "recommended"
        elif n_back and installed and n_back / installed >= 0.15:
            # People giving a machine back is the loudest signal there is.
            note = (f"{n_back} of {installed} were returned with the machine "
                    f"blamed. That is customers deciding they would rather "
                    f"have nothing")
            verdict = "avoid"
        elif severe or (against / installed) >= 2:
            note = (f"{against} problems across {installed} in service"
                    + (f", including {severe} where the machine was unusable"
                       if severe else "")
                    + ". That is more than we would like")
            verdict = "avoid"
        else:
            note = (f"{installed} in service, {faults} service call"
                    f"{'s' if faults != 1 else ''}"
                    + (f" and {n_gripe} complaint{'s' if n_gripe != 1 else ''}"
                       if n_gripe else "")
                    + " between them")
            verdict = "fine"

        row = {
            "brand": r["brand"], "model": r["model_number"],
            "type": r["product_type"], "daily_kwh": r["daily_kwh"],
            "refrigerant": r["refrigerant"],
            "recalled": bool(recall and recall["kind"] == "machine"),
            "units_in_service": installed,
            "service_calls": faults,
            "complaints": n_gripe,
            "returned": n_back,
            "our_experience": note, "verdict": verdict,
        }
        if cats:
            row["complained_about"] = cats
        if recall is not None:
            rr = recall["row"]
            row["recall"] = {"hazard": rr["hazard"], "dated": rr["recall_date"],
                             "url": rr["url"], "concerns": recall["kind"]}
            if recall["kind"] == "accessory":
                # Real, and worth a sentence, but not a mark against the
                # machine. Said plainly so the agent does not overstate it.
                row["recall"]["note"] = (
                    "this recall is on an accessory for this brand, not on the "
                    "machine itself")
        picks.append(row)
        if len(picks) >= limit:
            break

    # Sort recalled machines to the bottom regardless of everything else. They
    # are returned rather than hidden so the agent can warn a customer who
    # names one, but they must never head a list of suggestions.
    picks.sort(key=lambda p: 1 if p["recalled"] else 0)

    # The same ceiling that was never read on find_equipment was never read
    # here either. A recommendation over what somebody said they could spend
    # is not a recommendation.
    picks, over = _inside_the_budget(picks, budget_max)
    if budget_max and not picks:
        return {
            "ok": True, "family": family, "candidates": [],
            "over_their_ceiling": over[:5],
            "say": f"Nothing we sell in that family comes in at or under "
                   f"${budget_max:,.2f}. Say what the nearest one costs and "
                   "let them decide, rather than offering one over the "
                   "ceiling as though it met it.",
        }

    return {
        "ok": True, "family": family, "candidates": picks,
        "over_their_ceiling": over[:3] if budget_max else [],
        "how_ranked": f"service calls and complaints per machine in service, "
                      f"smoothed so a model with only one or two units cannot "
                      f"top the list on a clean record. Below {MIN_SAMPLE} in "
                      f"service we say we do not know rather than guessing. "
                      f"Ties broken by running cost from EPA certification data",
        "advice": "Lead with what we have actually seen, including the sample "
                  "size. 'We have fixed four of those this year' is worth more "
                  "than any star rating. 'We only have two, so I honestly "
                  "cannot tell you' is worth more than a confident guess, and "
                  "it is the sentence that makes the rest believable.",
    }


def _profile_experience(manufacturer: str, model_number: str) -> dict | None:
    """What we have seen on machines of the same design, whoever made them.

    Product type and defrost type must match. Refrigerant is deliberately not
    required: a door gasket does not care what is in the pipes, and demanding
    all three collapses the reach for very little accuracy.
    """
    with db.connect() as c:
        prof = c.execute(
            """SELECT product_type, COALESCE(defrost_type,'') d FROM equipment
               WHERE brand=? AND (?='' OR model_number=?)
                 AND product_type IS NOT NULL LIMIT 1""",
            (manufacturer, model_number, model_number)).fetchone()
        if prof is None:
            return None

        kin = [(r["brand"], r["model_number"]) for r in c.execute(
            """SELECT DISTINCT brand, model_number FROM equipment
               WHERE site_visit=1 AND product_type=? AND COALESCE(defrost_type,'')=?""",
            (prof["product_type"], prof["d"]))]
        if not kin:
            return None

        marks = ",".join("?" * len(kin))
        pairs = [x for pair in kin for x in pair]
        row = c.execute(
            f"""SELECT COUNT(*) faults, COUNT(DISTINCT model_number) models
                FROM repairs
                WHERE (manufacturer, model_number) IN
                      (VALUES {','.join(['(?,?)'] * len(kin))})""", pairs).fetchone()
        if not row or not row["faults"]:
            return None

        units = c.execute(
            f"""SELECT COALESCE(SUM(units),0) n FROM model_supplied
                WHERE (manufacturer, model_number) IN
                      (VALUES {','.join(['(?,?)'] * len(kin))})""", pairs).fetchone()["n"]

        causes = [{"cause": r["found_cause"][:80], "times": r["n"]} for r in c.execute(
            f"""SELECT found_cause, COUNT(*) n FROM repairs
                WHERE (manufacturer, model_number) IN
                      (VALUES {','.join(['(?,?)'] * len(kin))})
                GROUP BY found_cause ORDER BY n DESC LIMIT 3""", pairs)]

    return {"profile": f"{prof['product_type']}, {prof['d'] or 'unspecified'} defrost",
            "models": row["models"], "units": units,
            "faults": row["faults"], "causes": causes}


def what_we_know_about(manufacturer: str, model_number: str = "") -> dict:
    """Our own service record for a machine the customer is considering.

    The most useful thing a parts desk can say to somebody about to spend four
    thousand dollars is "we have been out to four of those this year". No
    review site can say it, because no review site sent a van.

    Args:
        manufacturer: the make they mentioned.
        model_number: the model, if they have it.

    Returns:
        What we have actually seen, and a plain verdict. Says so honestly when
        we have never touched one.
    """
    with db.connect() as c:
        if model_number:
            hist = c.execute(
                """SELECT r.faults, r.units_affected, r.first_visit_fix_pct,
                          r.avg_hours, r.last_fault, i.installed
                   FROM model_reliability r LEFT JOIN model_installed i
                     ON i.manufacturer=r.manufacturer AND i.model_number=r.model_number
                   WHERE r.manufacturer LIKE ? AND r.model_number LIKE ?""",
                (f"%{manufacturer}%", f"%{model_number}%")).fetchone()
        else:
            hist = c.execute(
                """SELECT SUM(r.faults) faults, SUM(r.units_affected) units_affected,
                          ROUND(AVG(r.first_visit_fix_pct)) first_visit_fix_pct,
                          ROUND(AVG(r.avg_hours),2) avg_hours,
                          MAX(r.last_fault) last_fault, NULL installed
                   FROM model_reliability r WHERE r.manufacturer LIKE ?""",
                (f"%{manufacturer}%",)).fetchone()

        common = c.execute(
            """SELECT found_cause, COUNT(*) n FROM repairs
               WHERE manufacturer LIKE ? AND (? = '' OR model_number LIKE ?)
               GROUP BY found_cause ORDER BY n DESC LIMIT 3""",
            (f"%{manufacturer}%", model_number, f"%{model_number}%")).fetchall()

    faults = (hist["faults"] if hist else 0) or 0
    installed = (hist["installed"] if hist else 0) or 0

    # Complaints are evidence too, and they arrive without a van. Checking
    # faults alone meant a model with five unhappy owners and no breakdown
    # came back as "we have never seen it", which is worse than useless: it
    # reports the absence of one signal as the absence of all of them.
    # Imported here rather than at module scope: complaints live in their own
    # module now and a top-level import would be a cycle waiting to happen the
    # first time feedback needs anything from buying.
    from .feedback import complaints_about

    gripes = complaints_about(manufacturer, model_number)
    n_gripe = gripes.get("complaints", 0)

    if faults == 0 and n_gripe == 0:
        # Never serviced this exact model. That used to be the end of the
        # answer, and it was the end of the answer for 32,730 of the 32,767
        # machines in the catalogue.
        #
        # But a machine is not a mystery just because the badge is unfamiliar.
        # The certification data says what kind of defrost it has and what
        # refrigerant it runs, and those are the things that decide how it
        # fails. A defrost thermostat fits 49 makes. Matching on the component
        # profile instead of the badge reaches 21,533 models instead of 37.
        kin = _profile_experience(manufacturer, model_number)
        if kin:
            return {
                "known": False, "known_by_profile": True,
                "manufacturer": manufacturer, "model": model_number,
                "profile": kin["profile"],
                "comparable_models": kin["models"],
                "units_in_service": kin["units"],
                "service_calls": kin["faults"],
                "what_goes_wrong": kin["causes"],
                "say": (f"We have never supplied that exact model. We have "
                        f"{kin['units']} machines of the same type and defrost "
                        f"design though, across {kin['models']} models, and "
                        f"what goes wrong on those is "
                        f"{kin['causes'][0]['cause'][:60] if kin['causes'] else 'nothing notable'}."),
                "caveat": "Say clearly this is from comparable machines, not "
                          "from that model. It is a fair guide to how this kind "
                          "of equipment fails and it is not experience of that "
                          "badge.",
            }
        return {"known": False, "manufacturer": manufacturer, "model": model_number,
                "units_in_service": gripes.get("units_in_service", 0),
                "say": "We have never had a service call or a complaint on that "
                       "one. That is not the same as saying it is good, only "
                       "that we have not seen it."}

    against = faults + n_gripe
    per_unit = (against / installed) if installed else None
    if per_unit is not None and per_unit >= 2:
        verdict = "we would steer you away from it"
    elif per_unit is not None and per_unit >= 1:
        verdict = "it is not the one we would pick"
    elif installed and installed < MIN_SAMPLE:
        verdict = ("we have too few of them to say much either way")
    else:
        verdict = "nothing on it that worries us"

    return {
        "known": True,
        "manufacturer": manufacturer,
        "model": model_number or "all models",
        "service_calls": faults,
        "complaints": n_gripe,
        "in_their_words": gripes.get("in_their_words", []),
        "machines_involved": hist["units_affected"] if hist else 0,
        "installed_with_our_customers":
            installed or gripes.get("units_in_service") or None,
        "typical_repair_hours": hist["avg_hours"] if hist else None,
        "most_recent": hist["last_fault"] if hist else None,
        "what_goes_wrong": [{"cause": r["found_cause"], "times": r["n"]} for r in common],
        "verdict": verdict,
        "say": (f"We have been out to that model {faults} time"
                f"{'s' if faults != 1 else ''}"
                + (f" and had {n_gripe} complaint{'s' if n_gripe != 1 else ''} "
                   f"about it" if n_gripe else "")
                + (f" across {installed} machines our customers own" if installed else "")
                + f". {verdict.capitalize()}."),
    }


def quote_delivery(sku_or_model: str, urgency: str = "normal") -> dict:
    """What it costs and when it arrives, from the carrier table.

    Delivery dates come from a table of real service levels rather than from a
    model's sense of how long shipping takes.
    """
    with db.connect() as c:
        opts = c.execute(
            "SELECT * FROM carrier_options ORDER BY days_max ASC, cost ASC").fetchall()
        part = c.execute("SELECT sku,name,unit_cost,lead_time_days FROM parts WHERE sku=?",
                         (sku_or_model,)).fetchone()
        free = c.execute(
            "SELECT SUM(free) f FROM stock_available WHERE sku=?", (sku_or_model,)).fetchone()

    in_stock = bool(free and (free["f"] or 0) > 0)
    lead = 0 if in_stock else (part["lead_time_days"] if part else 0)
    what = part["name"] if part else sku_or_model

    if part is None:
        # IT IS A MACHINE, NOT A SPARE PART, and machines live in a different
        # table. This looked only in `parts`, found nothing, and read that as
        # a lead time of ZERO -- so a Dell XPS 14 nobody had in stock was
        # quoted with "free same-day delivery, arriving August 31st" in the
        # same breath as "the lead time is about 21 days".
        #
        # Both sentences came out of this desk on one call. A customer hears
        # the one they like, and we miss it by three weeks.
        from .supply import _find_on_the_floor, the_row_behind
        from .tenancy import the_desk

        row = (the_row_behind(sku_or_model)
               or _find_on_the_floor(the_desk(), sku_or_model))
        if row is not None:
            in_stock = (row["on_hand"] or 0) > 0
            lead = 0 if in_stock else int(row["lead_time_days"] or 21)
            what = " ".join(x for x in (row["manufacturer"] or "",
                                        row["model_number"] or "") if x).strip()

    today = datetime.now().date()
    quotes = []
    for o in opts:
        if urgency == "normal" and o["service_level"] in ("Next Day Air", "Priority Overnight"):
            continue
        quotes.append({
            "carrier": o["carrier"], "service": o["service_level"],
            "cost": o["cost"],
            "arrives": str(today + timedelta(days=lead + o["days_max"])),
            "business_days": lead + o["days_max"],
        })

    return {"ok": True, "item": what,
            "in_stock": in_stock,
            "supplier_lead_days": lead,
            "options": quotes[:4],
            "caveat": ("in stock, ships from our warehouse" if in_stock else
                       f"not in stock, {lead} business days from the supplier "
                       "before it can ship. Do not quote a date that ignores this.")}


def _price_the_line(c, item: str):
    """What this line is and what it costs, from wherever the thing lives.

    IT ONLY KNEW HOW TO PRICE A SPARE PART.

    Every line was looked up like this:

        SELECT sku, name, unit_cost FROM parts WHERE sku = ?

    `parts` is the spare parts bin. A Surface Laptop, a Continental freezer
    and a Brother printer are machines, and machines live in product_stock. So
    the lookup missed, the price came back None, and the line was written at
    zero.

    Three orders were confirmed on a live call at $0.00 each, after the desk
    had read the real prices out loud: $1,780 for the Surface, $187.56 for the
    Brother. It said one number and wrote another, and the orders would have
    reached fulfilment and invoicing with nothing on them.

    Parts are still tried first, because a SKU is exact and unambiguous when
    there is one. A machine is matched on make and model, which is how anybody
    orders one out loud.
    """
    text = (item or "").strip()
    if not text:
        return None, text, None, ""

    from .tenancy import the_desk as _desk

    # THE ROW WE ALREADY FOUND, if the desk was handed one and kept hold of
    # it. This is the whole point: a machine that was quoted from our own
    # catalogue must be ordered from that same row, not found a second time by
    # whatever words the conversation reached for. Searching twice is what put
    # a 21 day lead on something we had eleven of.
    from .supply import the_row_behind

    row = the_row_behind(text)
    if row is not None:
        name = " ".join(x for x in (row["manufacturer"] or "",
                                    row["model_number"] or "") if x).strip()
        return (None, name or text, row["list_price"],
                "our own price list, from the row that was quoted")

    if re.search(r"STK-\d+", text.upper()):
        # A HANDLE WE CANNOT READ IS NOT A THING TO PRICE.
        #
        # On a live call the desk was selling a $2,059 freezer and passed
        # STK-412, a row belonging to the IT company. It did not resolve here,
        # fell through to the market lookup below, and came back with a median
        # of five listings -- $19.65 -- which went onto the order as the price
        # of a commercial freezer.
        #
        # Every fallback under this line is for a machine described in WORDS.
        # A handle that does not resolve is a mistake, and pricing a mistake
        # is how a customer is quoted twenty dollars for a two thousand dollar
        # machine.
        return (None, text, None,
                "that reference is not one of ours; look the machine up by "
                "make and model instead of by reference")

    part = c.execute("SELECT sku,name,unit_cost FROM parts WHERE sku=? "
                     "AND dealer_id=?", (text, _desk())).fetchone()
    if part is not None:
        return part["sku"], part["name"], part["unit_cost"], "our parts list"

    # A machine, by whatever the caller called it. Dearest match first so a
    # phrase matching several lines does not silently pick the cheapest.
    low = text.lower()
    # ONE MATCHER, NOT TWO.
    #
    # This had its own LIKE query and it was the brittle version: "lenovo
    # ideapad" never matched "IdeaPad 15.6\" Full HD", so a machine on the
    # shelf priced as though we did not stock it. supply._find_on_the_floor
    # already solved exactly this, and having a second copy meant fixing it
    # once fixed it in one place only.
    #
    # It is also scoped to the company, which this was not: a refrigeration
    # call could be quoted the IT company's price for something refrigeration
    # does not sell.
    from .supply import _find_on_the_floor
    from .tenancy import the_desk

    machine = _find_on_the_floor(the_desk(), text)

    if machine is not None:
        return (None,
                f"{machine['manufacturer']} {machine['model_number']}",
                machine["list_price"], "our own price list")

    # THE PRICE WE READ OUT TO THEM, MINUTES AGO, ON THIS CALL.
    #
    # Before guessing at a make and model from the words of the order, use the
    # figure the desk actually quoted. It was looked up with the make and
    # model spelled properly, the customer heard it, and they said yes to it,
    # which makes it the only price this line has any business carrying.
    #
    # It sits BELOW our own shelf price on purpose. If we stock the thing,
    # our price is the price and there is nothing to remember.
    from .quoted import the_price_we_said

    quoted = the_price_we_said(text)
    if quoted:
        return (None,
                f"{quoted['manufacturer']} {quoted['model_number']}",
                quoted["price"],
                f"the price quoted to them on this call"
                + (f", {quoted['where_from']}" if quoted.get("where_from") else ""))

    # NOT ON OUR SHELF, WHICH IS NOT THE SAME AS UNPRICEABLE.
    #
    # This used to stop here and write the line at no price, so an order for
    # anything we do not stock sat at $0.00 and could never be confirmed. On a
    # live call somebody asked for a Razer Blade 18, was quoted one, said yes,
    # and the order would not go through. The desk can source almost anything
    # in; the pricing could not follow it.
    #
    # market.py already answers this from real listings. It is NOT our quote
    # and must never be read as one, so the price is carried with where it
    # came from and the line says so.
    try:
        from .market import price_for

        parts_of = text.split()
        seen = price_for(parts_of[0] if parts_of else text,
                         " ".join(parts_of[1:]) if len(parts_of) > 1 else "")
        median = seen.get("median") if isinstance(seen, dict) else None
        if median:
            return (None, text, round(float(median), 2),
                    f"market median of {len(seen.get('sellers') or [])} "
                    f"listings, not our own quote")
    except Exception as e:
        print(f"[buying] could not price {text!r} from the market: "
              f"{type(e).__name__}: {e}", flush=True)

    # Genuinely unpriceable. Written with no price rather than a guessed one,
    # and the caller is told, because a line nobody can price is a line nobody
    # should confirm.
    return None, text, None, ""



def _the_same_draft(account_id: str, items: list[str]) -> str:
    """An open draft for this customer with exactly these lines, if there is one.

    Matched on WHAT WAS ORDERED rather than on the words used to order it,
    because the second request never uses the same sentence as the first.
    """
    from .tenancy import the_desk

    wanted = []
    with db.connect() as c:
        for it in items:
            _, name, _, _ = _price_the_line(c, it)
            wanted.append((name or it).strip().lower())
        wanted_key = sorted(w for w in wanted if w)
        if not wanted_key:
            return ""

        for row in c.execute(
                """SELECT id FROM purchase_orders
                   WHERE status = 'draft' AND dealer_id = ?
                     AND (account_id = ? OR ? = '')
                     AND placed_at >= datetime('now', '-2 hours')
                   ORDER BY placed_at DESC LIMIT 8""",
                (the_desk(), account_id or "", account_id or "")):
            have = sorted(
                (r["description"] or "").strip().lower()
                for r in c.execute(
                    "SELECT description FROM purchase_lines WHERE po_id = ?",
                    (row["id"],)))
            if have == wanted_key:
                return row["id"]
    return ""


def _as_it_stands(po_id: str) -> dict:
    """The existing draft, in the shape a freshly raised one comes back in."""
    with db.connect() as c:
        lines = [dict(r) for r in c.execute(
            """SELECT line_no, sku, description, qty, unit_price
               FROM purchase_lines WHERE po_id = ? ORDER BY line_no""",
            (po_id,))]
        head = c.execute(
            "SELECT subtotal, status FROM purchase_orders WHERE id = ?",
            (po_id,)).fetchone()

    return {"ok": True, "purchase_order": po_id, "lines": len(lines),
            "subtotal": round(head["subtotal"] or 0, 2),
            "status": head["status"],
            "already_open": True,
            "note": "This order was already drafted for them a moment ago. "
                    "It has NOT been duplicated. Read the total back and "
                    "confirm this one."}


# What a line naming our own protection plan looks like when a model writes
# it out. Deliberately narrow: this decides that a line is NOT a product, and
# reading a real product as cover would price a machine off a percentage.
# "plan" is in here because the desk says "a 2-year Essential plan" at least
# as often as it says warranty -- our tiers are literally named Essential and
# Complete, so that is the natural phrasing. Missing it cost a live sale: the
# customer was quoted $279.99 plus $42.00 cover, the cover line could not be
# recognised so it went on unpriced, the order was cancelled as unpriceable,
# and the retry quietly dropped the cover and confirmed the TV alone. The
# customer heard one total and was charged another.
_COVER_WORDS = ("warranty", "cover", "protection plan", "protection", "plan")

# A tier name on its own is enough. Nothing on any shelf is called Essential
# or Complete; those are our products.
_OUR_TIERS = ("essential", "complete")


def _is_our_cover(item: str) -> bool:
    """Is this line our own protection plan rather than something on a shelf."""
    low = (item or "").lower()
    if not any(w in low for w in _COVER_WORDS):
        return False
    # A tier name, or a term in years, is what separates OUR plan from a
    # customer saying "the freezer with the good warranty".
    return (any(t in low for t in _OUR_TIERS)
            or bool(re.search(r"\b(\d+)\s*[- ]?\s*year", low)))


# Carriage, as a model writes it when it mistakes shipping for a product.
_CARRIAGE = ("shipping", "delivery", "2nd day air", "next day air",
             "priority mail", "ground", "freight", "postage", "carriage")


def _is_carriage(item: str) -> bool:
    """Is this line a delivery service rather than a thing on a shelf."""
    low = (item or "").lower()
    if any(w in low for w in ("projector", "laptop", "chair", "desk",
                              "freezer", "cooler", "printer", "cabinet")):
        return False          # a machine that happens to mention delivery
    return any(w in low for w in _CARRIAGE)


def _family_behind(c, description: str) -> str:
    """The family of a machine we just priced, for the cover rates."""
    try:
        row = c.execute(
            """SELECT family FROM product_stock
               WHERE LOWER(?) LIKE '%' || LOWER(manufacturer) || '%'
                 AND LOWER(?) LIKE '%' || LOWER(model_number) || '%'
               LIMIT 1""", (description, description)).fetchone()
        return row["family"] if row else ""
    except Exception:
        return ""


def _price_our_cover(item: str, machine_total: float, family: str):
    """Price our own plan against the machines on the same order.

    Returns the same four-tuple shape as `_price_the_line`. Refuses rather
    than guessing when there is no machine to price against: cover is a share
    of something, and a share of nothing is not a number to put on an invoice.
    """
    if not machine_total:
        # THE MACHINE IS ON THE ORDER WE JUST CONFIRMED.
        #
        # HEARD LIVE. The desk confirmed a freezer at $1,999, the customer
        # said yes to three years of cover, and it raised a SECOND order
        # carrying only the cover line. Cover is a share of something and this
        # order had nothing on it, so the line went on unpriced and the order
        # was refused -- seconds after the customer had agreed to $225.89.
        #
        # Adding cover after the machine is confirmed is the natural way to
        # buy it, not a mistake. The order this call raised is on the record,
        # so the figure to take a share of is right there.
        try:
            from .shortlist import the_order_on_this_call

            po_id = the_order_on_this_call()
            if po_id:
                with db.connect() as c:
                    row = c.execute(
                        """SELECT po.subtotal, pl.description
                           FROM purchase_orders po
                           JOIN purchase_lines pl ON pl.po_id = po.id
                           WHERE po.id = ? ORDER BY pl.line_no LIMIT 1""",
                        (po_id,)).fetchone()
                    if row and row["subtotal"]:
                        machine_total = float(row["subtotal"])
                        family = family or _family_behind(c, row["description"])
                        print(f"[buying] cover on its own; pricing it against "
                              f"{po_id} at ${machine_total:,.2f}", flush=True)
        except Exception as e:
            print(f"[buying] could not find a machine for the cover: "
                  f"{type(e).__name__}: {e}", flush=True)

    if not machine_total:
        return (None, item, None,
                "there is no machine on this order to price cover against")

    low = (item or "").lower()
    want_years = 0
    found = re.search(r"\b(\d+)\s*[- ]?\s*year", low)
    if found:
        want_years = int(found.group(1))
    tier = "Complete" if "complete" in low else "Essential"

    try:
        from .our_cover import plans_for

        plans = plans_for(machine_total, family).get("plans") or []
    except Exception as e:
        print(f"[buying] could not price our cover: {type(e).__name__}: {e}",
              flush=True)
        return None, item, None, ""

    # THE FIGURE THEY WERE ACTUALLY TOLD, if cover was quoted on this call.
    # Recomputing here is what charged somebody $45.00 for the $22.60 plan
    # they had agreed to.
    try:
        from .quoted import the_price_we_said

        said = the_price_we_said(f"OurCover {tier} {want_years}yr")
        if said:
            return (None, f"{tier} cover, {want_years} years", said["price"],
                    "the cover price quoted to them on this call")
    except Exception:
        pass

    match = [p for p in plans if p["tier"] == tier
             and (not want_years or p["years"] == want_years)]
    if not match:
        # The tier and term they were quoted is not one we can sell at this
        # price. Named rather than substituted: quoting three years and
        # invoicing five is the kind of small swap nobody notices until a
        # claim.
        return (None, item, None,
                f"we do not sell {tier} cover for {want_years or 'that'} "
                f"year(s) on something at this price")

    plan = match[0]
    return (None,
            f"{plan['tier']} cover, {plan['years']} years",
            plan["price"],
            f"our own plan at {plan['share_of_price'] * 100:.0f}% of the "
            f"machine price")


def _add_cover_to(po_id: str, items: list[str]) -> dict:
    """Put a cover line on an order that already carries the machine.

    Priced off what is already on that order, which is the only figure a
    share of the purchase price can honestly be taken from.
    """
    with db.connect() as c:
        po = c.execute(
            "SELECT id, status, subtotal FROM purchase_orders WHERE id = ?",
            (po_id,)).fetchone()
        if po is None:
            return {"ok": False, "why": f"no order {po_id!r}"}
        lines = [dict(r) for r in c.execute(
            "SELECT line_no, description, unit_price FROM purchase_lines "
            "WHERE po_id = ? ORDER BY line_no", (po_id,))]

    machines = round(sum((l["unit_price"] or 0) for l in lines
                         if not _is_our_cover(l["description"] or "")), 2)
    if not machines:
        return {"ok": False,
                "why": f"{po_id} has nothing on it to price cover against",
                "say": "Do not tell them the cover is added. Take the order "
                       "for the machine first."}

    already = [l for l in lines if _is_our_cover(l["description"] or "")]
    if already:
        return {"ok": True, "purchase_order": po_id, "already": True,
                "subtotal": po["subtotal"],
                "say": f"{po_id} already carries cover. Nothing was added "
                       "twice, and the total is unchanged."}

    family = ""
    with db.connect() as c:
        for line in lines:
            if not _is_our_cover(line["description"] or ""):
                family = _family_behind(c, line["description"] or "")
                break

    added = []
    total = float(po["subtotal"] or machines)
    with db.txn() as c:
        n = max((l["line_no"] for l in lines), default=0)
        for item in items:
            _, description, price, why = _price_our_cover(item, machines, family)
            n += 1
            c.execute(
                """INSERT INTO purchase_lines
                   (po_id,line_no,sku,description,qty,unit_price,sourced_by)
                   VALUES (?,?,?,?,?,?,?)""",
                (po_id, n, None, description, 1, price, why or None))
            total += price or 0
            added.append({"description": description, "price": price})
        c.execute("UPDATE purchase_orders SET subtotal = ? WHERE id = ?",
                  (round(total, 2), po_id))

    unpriced = [a["description"] for a in added if not a["price"]]
    out = {"ok": not unpriced, "purchase_order": po_id,
           "added_to_the_existing_order": added,
           "subtotal": round(total, 2), "status": po["status"]}
    if unpriced:
        out["unpriced"] = unpriced
        out["say"] = ("That cover could not be priced, so do NOT read out a "
                      "total and do not say it is added.")
    else:
        out["say"] = (f"The cover is on {po_id} with the machine, and the "
                      f"total is now ${total:,.2f}. Read that back. One "
                      "order, one invoice.")
    return out


def create_purchase_order(account_id: str, items: list[str], site_id: str = "",
                          contact_id: str = "", note: str = "") -> dict:
    """Raise a supply order. Draft until a human confirms it.

    Args:
        account_id: who is buying.
        items: SKUs or descriptions, one per line item.
        site_id: where it goes.
        contact_id: who ordered it.
        note: anything they said about it.
    """
    if not items:
        return {"ok": False, "why": "nothing to order"}

    # IT MUST BE ONE OF THE ONES WE ACTUALLY READ OUT.
    #
    # HEARD ON A LIVE CALL. The desk read out three chairs and the customer
    # said "I want the third one". The third one it had said was a WorkPro at
    # $399.99. It ordered a FlexiSpot C7 at $319.99 -- real, on our own floor,
    # and never once mentioned to the caller. It came from an earlier search
    # still sitting in the conversation, and a position is meaningless across
    # two lists.
    #
    # Only handles are checked. Somebody naming a machine in words is not
    # making a positional reference and must never be blocked by this; the
    # failure being closed is specifically the handle picked out of the wrong
    # list, and when no list has been offered nothing is checked at all.
    try:
        from .shortlist import was_offered, what_we_offered

        from .shortlist import on_our_own_floor

        astray = [it for it in items if not was_offered(it)]

        # NOT ON THE LIST IS NOT THE SAME AS NOT OURS, and the first version
        # of this treated them alike and refused both.
        #
        # A handle that resolves to a real row on our own floor was offered to
        # the customer somehow, even if the register did not see it happen.
        # Refusing that is how a desk ends up apologising for a filing cabinet
        # it had just quoted. It goes through, and the desk is told to read the
        # item and the price back first, which is the protection that actually
        # matters.
        ours = [it for it in astray if on_our_own_floor(it)]
        astray = [it for it in astray if it not in ours]

        if ours and not astray:
            print(f"[buying] {', '.join(ours)} was not on the last shortlist "
                  f"but is on our own floor; allowing it with a read-back",
                  flush=True)

        if astray:
            offered = what_we_offered()
            return {
                "ok": False,
                "why": f"{', '.join(astray)} is not one of ours and was not "
                       f"read out to them",
                "we_offered": [
                    {"number": o.get("number"), "ref": o.get("ref"),
                     "what": f"{o.get('manufacturer','')} "
                             f"{o.get('model_number','')}".strip(),
                     "price": o.get("list_price")}
                    for o in offered],
                "say": "Do NOT order that. It is not on the list you just "
                       "gave them. If they picked one by position, take it "
                       "from the numbered list here. If you are unsure which "
                       "they meant, read the list again and ask.",
            }
    except Exception as e:
        print(f"[buying] could not check the shortlist: "
              f"{type(e).__name__}: {e}", flush=True)

    # THE SAME ORDER, ASKED FOR TWICE, IS ONE ORDER.
    #
    # OBSERVED LIVE, AND IT BILLS SOMEBODY TWICE. The desk drafted PO-B407A6
    # for a Serta chair at $139.99 and read the price back. The customer said
    # confirm. `supply` is a sub-agent and every call to it is a fresh
    # conversation, so it had no memory of the draft it had raised ninety
    # seconds earlier: given "confirm the order for the Serta chair" with no
    # number, the only thing it could do was raise a NEW one, PO-385A31, and
    # confirm that. Two orders, two chairs, two invoices, and the first left
    # orphaned as a draft forever.
    #
    # The agent cannot be told to remember across a boundary that discards
    # memory. So the DATABASE refuses the duplicate instead: an open draft
    # with the same lines for the same customer IS this order, and is handed
    # back rather than cloned.
    # COVER GOES ON THE MACHINE'S ORDER, NOT ON AN ORDER OF ITS OWN.
    #
    # HEARD LIVE, AND IT IS WRONG IN TWO WAYS AT ONCE. The desk confirmed
    # a freezer at $1,999, the customer said yes to three years of cover,
    # and it raised a SECOND order carrying only the cover line. That
    # order had no machine on it, so nothing could price the line, and it
    # sat on the board reading "not priced" next to the freezer it
    # belonged to.
    #
    # Even priced it would be wrong. A protection plan is not a thing you
    # buy on its own: it is a line on the sale of the machine it protects.
    # Two orders means two invoices, two delivery notes, and a customer
    # who cancels one and keeps the other.
    #
    # So a cover-only order is not raised at all. The line is added to the
    # order this call already made, which is where it should have gone.
    if items and all(_is_our_cover(it) for it in items):
        from .shortlist import the_order_on_this_call

        onto = the_order_on_this_call()
        if onto:
            return _add_cover_to(onto, items)

    same = _the_same_draft(account_id, items)
    if same:
        print(f"[buying] the same draft is already open, returning {same} "
              f"rather than raising a second order", flush=True)
        # AND IT IS STILL THIS CALL'S ORDER.
        #
        # This returned early without recording it, so everything that asks
        # "which order is this call about" got nothing: the cover line had
        # no machine to attach to and went onto an order of its own, which
        # is the exact failure the redirect above exists to stop.
        try:
            from .shortlist import we_raised

            we_raised(same)
        except Exception:
            pass
        return _as_it_stands(same)

    po = _nid("PO")
    total = 0.0
    with db.txn() as c:
        # WHICH COMPANY SOLD IT. Taken from the vendor this call was routed
        # to, not from the account: an account belongs to whichever business
        # the caller first rang, so a laptop bought from the IT company was
        # being filed as a refrigeration sale because the conversation
        # started there.
        from .tenancy import the_desk

        c.execute("""INSERT INTO purchase_orders
                     (id,account_id,site_id,contact_id,status,placed_at,note,
                      dealer_id)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (po, account_id, site_id or None, contact_id or None, "draft",
                   datetime.now().isoformat(timespec="seconds"), note or None,
                   the_desk()))
        # TWO PASSES, BECAUSE COVER IS PRICED OFF THE MACHINE.
        #
        # HEARD ON A LIVE CALL. The desk quoted our own three year plan at
        # $23.73, the customer said yes, and the model put "3-year Essential
        # warranty" on the order as a line. It is not a product, nothing could
        # price it, and the unpriced-line gate then refused the whole order:
        #
        #     "I'm sorry, I'm unable to confirm the warranty price."
        #
        # Our own cover is a real thing we sell for a real number, and that
        # number is a share of what the machine costs -- which is knowable
        # only once the machine lines are priced. So the machines go first and
        # anything that is cover is priced against them afterwards.
        # SHIPPING IS NOT A LINE ON THE ORDER.
        #
        # Heard live: the desk put "UPS 2nd Day Air shipping" in with the
        # machine and the cover. It is not a product, nothing can price it,
        # and the unpriced-line gate then refused the whole order -- so a
        # customer who had said yes three times was told, three times, that
        # the price could not be confirmed. Carriage is quoted separately by
        # quote_delivery and settled at despatch.
        carriage = [it for it in items if _is_carriage(it)]
        if carriage:
            print(f"[buying] dropping {carriage} from the order: carriage is "
                  f"quoted separately, not an order line", flush=True)
        items = [it for it in items if not _is_carriage(it)]
        if not items:
            return {"ok": False,
                    "why": "there is nothing but carriage on that order",
                    "say": "Shipping is not something to order. Put the "
                           "machine on the order and quote the carriage "
                           "separately."}

        priced: list = []
        for item in items:
            if _is_our_cover(item):
                priced.append((item, None))
                continue
            priced.append((item, _price_the_line(c, item)))

        machines = round(sum((p[2] or 0) for _, p in priced if p), 2)
        family = ""
        for _, p in priced:
            if p and p[1]:
                family = _family_behind(c, p[1]) or family
                break

        for i, (item, got) in enumerate(priced, 1):
            if got is None:
                sku, description, price, priced_from = _price_our_cover(
                    item, machines, family)
            else:
                sku, description, price, priced_from = got
            total += price or 0
            c.execute("""INSERT INTO purchase_lines
                         (po_id,line_no,sku,description,qty,unit_price,
                          sourced_by)
                         VALUES (?,?,?,?,?,?,?)""",
                      (po, i, sku, description, 1, price, priced_from or None))
        c.execute("UPDATE purchase_orders SET subtotal=? WHERE id=?", (total, po))

    with db.connect() as c:
        unpriced = [r["description"] for r in c.execute(
            "SELECT description FROM purchase_lines "
            "WHERE po_id=? AND (unit_price IS NULL OR unit_price = 0)", (po,))]

    # THEY HAVE CHOSEN. Recorded so the rest of the call -- the warranty
    # question, the delivery question, the total -- does not have to re-derive
    # the machine from whatever words come next.
    try:
        from .shortlist import they_picked, we_raised

        we_raised(po)
        for item in items:
            if they_picked(item):
                break
    except Exception:
        pass

    # THE CARRIERS, WITH THE ORDER, so nobody has to go and ask.
    #
    # HEARD LIVE: "Will it be delivered by UPS?" The desk reached for
    # ask_suppliers, which answers a different question entirely, and told the
    # customer it could not see a carrier -- for an order it had just raised,
    # from a table of real service levels sitting one call away.
    #
    # Attached here rather than left to a second tool call. The delivery
    # question follows the order every single time, and a fact the desk
    # already holds cannot be reached for wrongly.
    carriers = []
    try:
        first = next((it for it in items if not _is_our_cover(it)), "")
        if first:
            got = quote_delivery(first)
            carriers = got.get("options", [])[:4]
    except Exception as e:
        print(f"[buying] could not attach carriers to {po}: "
              f"{type(e).__name__}: {e}", flush=True)

    out = {"ok": True, "purchase_order": po, "lines": len(items),
           "subtotal": round(total, 2), "status": "draft",
           "delivery_options": carriers,
           "note": "Draft. Read the lines and the total back before confirming."}

    # HAVE THEY BEEN OFFERED THE COVER YET.
    #
    # The instruction says to offer it before the order, and the model obeys
    # about two calls in three. On the third the customer is sold the machine
    # and asked about cover afterwards, which is the moment it gets refused:
    # somebody who has finished buying hears an extra charge, not an option.
    #
    # A reminder in an instruction loses to the flow of a conversation. A line
    # in the tool's own answer, at the exact moment the order is raised, does
    # not -- and it is a fact about this call rather than a rule to remember.
    try:
        from .aftercare import cover_was_quoted

        if not cover_was_quoted():
            out["cover_not_offered_yet"] = True
            out["say"] = (
                "Before you confirm this: they have NOT been offered extended "
                "cover on this call. Call warranty_options now, say what the "
                "manufacturer gives and what more costs, and read one total "
                "covering both. Asking after they have bought is how the "
                "offer gets refused.")
    except Exception:
        pass

    if unpriced:
        out["unpriced"] = unpriced
        out["say"] = (
            "Some of these have no price on them: "
            + ", ".join(unpriced) + ". Do NOT confirm this order and do NOT "
            "read out a total, because it is wrong. Say you want to check the "
            "price before taking the order, and find the machine by make and "
            "model. An order confirmed at zero reaches invoicing at zero.")
    return out


def cancel_purchase_order(purchase_order_id: str = "", why: str = "") -> dict:
    """Cancel an order the customer no longer wants.

    THE DESK HAD NO WAY TO DO THIS AND SAID IT HAD DONE IT ANYWAY.

    HEARD ON A LIVE CALL. The customer asked for a draft to be deleted. There
    was no cancel tool, so the model reached for the only ordering tool it
    had and called create_purchase_order with

        items = ["delete PO-3B3C0F"]

    which tried to BUY a product called "delete PO-3B3C0F". It then told the
    customer "I have canceled the draft order for you." Nothing was cancelled.
    A desk that reports an action it cannot perform is worse than one that
    says it cannot: the customer stops chasing something that is still open.

    Cancelled, not deleted, for the same reason the console does it that way:
    somebody ringing next week to ask what happened deserves an answer, and
    "there is no record of it" is not one.

    Args:
        purchase_order_id: which order. Blank means the one raised on this
            call, which is what "cancel that" means.
        why: what they said, kept on the record.
    """
    from .shortlist import the_order_on_this_call

    po_id = _which_draft(purchase_order_id) or the_order_on_this_call()
    if not po_id:
        return {"ok": False,
                "why": "no order named and none raised on this call",
                "say": "Ask which order they mean. Do NOT say you have "
                       "cancelled anything until this tool says so."}

    with db.connect() as c:
        po = c.execute(
            "SELECT id, status, subtotal FROM purchase_orders WHERE id = ?",
            (po_id,)).fetchone()
    if po is None:
        return {"ok": False, "why": f"no order {po_id!r}",
                "say": "Do not tell them it is cancelled. It does not exist."}
    if po["status"] == "cancelled":
        return {"ok": True, "purchase_order": po["id"], "already": True,
                "say": "It was already cancelled. Nothing was done twice."}
    if po["status"] == "delivered":
        return {"ok": False, "purchase_order": po["id"],
                "why": "that one has been delivered",
                "say": "A delivered machine comes back as a RETURN, not a "
                       "cancellation. Do not tell them it is cancelled."}

    with db.txn() as c:
        c.execute("UPDATE purchase_orders SET status = 'cancelled' WHERE id = ?",
                  (po_id,))
        try:
            c.execute(
                """UPDATE supply_orders SET status = 'cancelled'
                   WHERE for_purchase_order = ? AND status = 'placed'""",
                (po_id,))
        except Exception:
            pass

    print(f"[buying] {po_id} cancelled on the call: {why or 'no reason given'}",
          flush=True)
    return {"ok": True, "purchase_order": po_id, "was": po["status"],
            "say": f"Tell them {po_id} is cancelled. It stays on the record "
                   "as cancelled rather than vanishing, so anybody asking "
                   "later gets an answer."}


def note_wishlist(account_id: str, want: str, reason: str = "",
                  family: str = "", call_id: str = "") -> dict:
    """Write down something they mentioned wanting, in their own words.

    Not a marketing list. This is only ever filled from something a customer
    actually said, which is why `reason` carries their phrasing.
    """
    wid = _nid("W")
    with db.txn() as c:
        c.execute("""INSERT INTO wishlist
                     (id,account_id,from_call,want,family,reason,noted_at,status)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (wid, account_id, call_id or None, want, family or None,
                   reason or None, datetime.now().isoformat(timespec="seconds"), "open"))
    return {"ok": True, "wishlist_id": wid, "noted": want}


# ==========================================================================


# What a recall has to be talking about for it to concern this machine. A
# recall of a laptop battery pack is not a recall of the laptop, so the brand
# matching alone is never enough.
_FAMILY_WORDS = {
    "reach-in freezer": {"freezer", "refrigerat", "cooler"},
    "reach-in cooler": {"refrigerat", "cooler", "freezer"},
    "display cooler": {"refrigerat", "cooler", "merchandiser", "display"},
    "walk-in cooler": {"refrigerat", "cooler", "walk-in"},
    "ice machine": {"ice"},
    "dishwasher": {"dishwasher"},
    "oven": {"oven", "range"},
    "fryer": {"fryer"},
    "hot holding cabinet": {"holding", "warmer", "cabinet"},
    "laptop": {"laptop", "notebook"},
    "desktop": {"desktop", "computer"},
    "printer": {"printer"},
    "ups": {"power bank", "battery", "power supply", "ups"},
}

# Things that plug into a machine rather than being one. A recalled power bank
# is worth telling a Lenovo owner about; it is not a reason to avoid the
# laptop, and treating it as one would have us steering customers away from
# perfectly good machines over an accessory they may not even own.
_ACCESSORY_WORDS = ("power bank", "battery pack", "batteries", "battery",
                    "charger", "adapter", "power cord", "docking")

# Families where that accessory IS the product. A UPS is a battery.
_BATTERY_IS_THE_MACHINE = {"ups"}


def _recall_kind(text: str, family: str) -> str:
    """Whether a recall is about the machine itself or something plugged into it."""
    if (family or "").strip().lower() in _BATTERY_IS_THE_MACHINE:
        return "machine"
    return "accessory" if any(w in text for w in _ACCESSORY_WORDS) else "machine"



def _recall_for(recalled: dict, brand: str, model: str, family: str = ""):
    """Match a machine against published recalls, conservatively.

    The feed names brands as free text written by a government press office,
    so "Galanz Retro Refrigerators" has to match a Galanz asset. Two things
    made a first attempt at this worse than useless.

    Substring matching put a recall on BUNN because "bunn" appears inside
    "Woven Bunny Baskets". So the brand has to match on whole words.

    And matching the brand alone flagged Dell over a battery-module recall,
    Lenovo over power banks, and Panasonic over battery packs fitted to Sony
    laptops. Right company, entirely different product. So the recall also has
    to be talking about the kind of machine we are looking at.

    A false recall warning tells a customer not to buy something that is
    perfectly fine, which is the same class of error as inventing a fault.
    Where the evidence is thin this returns nothing.
    """
    b = (brand or "").strip().lower()
    if len(b) < 4:
        return None

    def words(s: str) -> set[str]:
        return {w.strip(".,;:()-") for w in (s or "").lower().split()}

    brand_words = words(b)
    m = (model or "").strip().lower()
    want = _FAMILY_WORDS.get((family or "").strip().lower())

    for brands, row in recalled.items():
        text = f"{brands} {row['title'] or ''}".lower()
        if not (brand_words & words(brands)):
            continue

        # The model named outright is the strongest evidence there is, and it
        # overrides the family check: if they printed our model number, it is
        # our machine whatever the press release calls it.
        if m and len(m) >= 4 and m in text:
            return {"row": row, "kind": _recall_kind(text, family)}

        if want and not any(w in text for w in want):
            continue      # their product, not this kind of machine
        if not want:
            continue      # no family to check against, so do not guess
        return {"row": row, "kind": _recall_kind(text, family)}
    return None




def _which_draft(given: str) -> str:
    """The order to confirm, when the desk described one instead of naming it.

    OBSERVED LIVE. The customer agreed to a Dell XPS 14 at $2,099.99, the desk
    raised PO-E9F9B9, read the total back, the customer said confirm, and the
    desk called this with

        "confirm the order for the Dell XPS 14 Laptop for $2099.99"

    There is no order number in that sentence. The order stayed a draft, and
    the customer rang off believing they had bought a laptop.

    It is the same failure as quoting a machine and then searching for it
    again by description: an identifier existed, was handed over, and was let
    go of. The rule here matches the one in tenancy: take the obvious answer
    when there is exactly one, and refuse to guess when there is not, because
    confirming the WRONG order spends somebody's money.
    """
    given = (given or "").strip()
    found = re.search(r"PO-[A-Z0-9]+", given.upper())
    if found:
        # AN ORDER NUMBER THAT LOOKS RIGHT AND DOES NOT EXIST.
        #
        # Heard live: the desk drafted a real order, then called this with
        # "PO-1234". It has the right shape and it is not one of ours, and
        # trusting the shape meant refusing a confirmation the customer had
        # just given out loud.
        #
        # Same rule as an invented asset id: shape is not existence. If it is
        # not a real order, it carries no information, and the open draft on
        # this call is what they meant.
        with db.connect() as c:
            real = c.execute("SELECT 1 FROM purchase_orders WHERE id = ?",
                             (found.group(0),)).fetchone()
        if real:
            # A REAL ORDER IS NOT NECESSARILY THIS CALL'S ORDER.
            #
            # HEARD LIVE. The desk sold a projector and tried to confirm
            # PO-44EF8D, which exists, belongs to this customer, and is a
            # standing desk from a call half an hour earlier. Every existence
            # check passed it because it does exist. The customer said "yes,
            # confirm" three times and was told three times that the price
            # could not be confirmed, because the stale order carried an
            # unpriced line.
            #
            # What this conversation raised is a fact. What the model produces
            # is a recollection, and the fact wins.
            try:
                from .shortlist import the_order_on_this_call

                ours = the_order_on_this_call()
                if ours and ours != found.group(0):
                    print(f"[buying] {found.group(0)} is a real order from "
                          f"another call; this call raised {ours} and that is "
                          f"the one being confirmed", flush=True)
                    return ours
            except Exception:
                pass
            return found.group(0)
        print(f"[buying] {found.group(0)} is not one of our orders; looking "
              "for the draft open on this call instead", flush=True)

    from .tenancy import the_desk

    with db.connect() as c:
        drafts = c.execute(
            """SELECT id FROM purchase_orders
               WHERE status = 'draft' AND dealer_id = ?
                 AND placed_at >= datetime('now', '-2 hours')
               ORDER BY placed_at DESC""", (the_desk(),)).fetchall()

    if len(drafts) == 1:
        print(f"[buying] {given!r} names no order; confirming the only draft "
              f"open, {drafts[0]['id']}", flush=True)
        return drafts[0]["id"]
    if len(drafts) > 1:
        print(f"[buying] {given!r} names no order and {len(drafts)} drafts are "
              f"open; refusing to guess which", flush=True)
    return given



# Calls where the desk has already been told to offer cover. Told once, then
# let through: a gate that refuses twice is a gate that loses a sale.
_NUDGED: set = set()


def _already_nudged(call_id: str) -> bool:
    if not call_id:
        return True
    return call_id in _NUDGED


def confirm_purchase_order(purchase_order_id: str, agreed_by: str = "") -> dict:
    """Turn a draft order into a placed one, after the customer said yes.

    Orders were raised as drafts and nothing ever confirmed them, so every
    order this desk has ever taken sat in the table forever, waiting for a step
    that did not exist. The draft stage is right, because a customer should
    hear the lines and the total read back before anything is placed. It just
    needed the other half.

    Args:
        purchase_order_id: the draft.
        agreed_by: who agreed to it on the call.
    """
    purchase_order_id = _which_draft(purchase_order_id)

    # COVER IS OFFERED BEFORE THE SALE CLOSES, NOT AFTER.
    #
    # The same check sits on create_purchase_order and was lost: that order is
    # usually raised inside the `supply` sub-agent, which summarises its
    # result back and drops the instruction. This is the last gate before a
    # sale closes and the front desk calls it directly, so it is the one place
    # the check cannot be summarised away.
    #
    # It refuses ONCE. Offer the cover, then confirm again and it goes
    # through. One extra turn against a customer who is never offered the
    # cover at all, or is offered it after they have finished buying, which
    # is when it gets refused.
    #
    # Only during a live call: the console approves orders too, and nobody is
    # on the phone to be offered anything.
    try:
        from .aftercare import cover_was_quoted, remember_we_asked
        from .trace import here

        on_a_call = bool(here())
        if on_a_call and not cover_was_quoted() and not _already_nudged(here()):
            remember_we_asked(here())
            return {"ok": False,
                    "why": "they have not been offered extended cover yet",
                    "purchase_order": purchase_order_id,
                    "say": "Do NOT tell them it is confirmed. Call "
                           "warranty_options now, say what the manufacturer "
                           "gives and what more would cost, and read one "
                           "total covering both. Then confirm again and it "
                           "will go through."}
    except Exception:
        pass

    with db.txn() as c:
        po = c.execute(
            "SELECT id, status, subtotal FROM purchase_orders WHERE id=?",
            (purchase_order_id,)).fetchone()
        if po is None:
            return {"ok": False,
                    "why": "no such order, and there is not exactly one draft "
                           "open on this call to assume you meant"}
        if po["status"] != "draft":
            # Not an error worth alarming anyone about. Saying yes twice on a
            # phone call is ordinary and must not produce a second order.
            return {"ok": True, "purchase_order": po["id"],
                    "status": po["status"],
                    "note": "already confirmed, nothing was duplicated"}

        lines = c.execute(
            """SELECT line_no, sku, description, qty, unit_price
               FROM purchase_lines WHERE po_id=? ORDER BY line_no""",
            (purchase_order_id,)).fetchall()
        if not lines:
            return {"ok": False, "why": "that order has no lines on it"}

        # A LINE WITH NO PRICE ON IT IS NOT AN ORDER.
        #
        # OBSERVED ON THE BOARD: "1 x Razer Blade 18 laptop, confirmed,
        # $0.00", and "1 x STK-412, confirmed, $15.95" -- a two thousand
        # dollar freezer priced from a handle that belonged to another
        # company.
        #
        # `_price_the_line` already refuses to guess, and
        # `create_purchase_order` already says so in its answer. Both were
        # correct and neither could stop anything: the order is usually raised
        # inside the `supply` sub-agent, which summarises the result back and
        # drops the warning with it, and the model then confirmed an order it
        # had been told not to confirm.
        #
        # So the refusal moves here, to the last gate before a sale closes,
        # which the front desk calls directly. Unlike the cover nudge below
        # this one does not relent after a turn and does not care whether
        # anybody is on the phone: an order that reaches invoicing at zero is
        # wrong on a call, wrong on the console, and wrong tomorrow.
        no_price = [r["description"] for r in lines
                    if r["unit_price"] is None or r["unit_price"] == 0]
        if no_price:
            return {"ok": False,
                    "purchase_order": purchase_order_id,
                    "why": "these lines have no price on them: "
                           + ", ".join(no_price),
                    "say": "Do NOT tell them it is confirmed and do NOT read "
                           "out a total. Look the machine up by make and "
                           "model, raise the order from the row you get back, "
                           "and confirm that one."}

        c.execute(
            """UPDATE purchase_orders
               SET status='confirmed', confirmed_at=?
               WHERE id=?""",
            (datetime.now().isoformat(timespec="seconds"), purchase_order_id))

    # ANYTHING WE DO NOT HOLD IS ORDERED NOW, pegged to this order.
    #
    # Confirming used to stop here, so an order could be confirmed for a
    # machine nobody owned and nothing would ever buy one. The customer waited
    # for a delivery that was never coming.
    # ONCE THEY HAVE BOUGHT, ASK IF WE MAY TELL THEM ABOUT OFFERS.
    #
    # At the confirmation, not at the delivery: this is the moment they chose
    # to buy from us and the conversation is fresh. Never raises, and asks
    # only once per account however many things they buy.
    try:
        from .staying_in_touch import ask_after_delivery

        ask_after_delivery(purchase_order_id)
    except Exception as e:
        print(f"[buying] could not queue the offers question for "
              f"{purchase_order_id}: {type(e).__name__}: {e}", flush=True)

    from .backorder import source_order

    sourcing = source_order(purchase_order_id)

    # AND IT IS NOW THEIRS.
    #
    # Confirming used to record that we would send a thing, and never that
    # they owned it. So standing.py's whole distinction between cover that is
    # OURS to grant and cover that is a CLAIM they must prove had no source of
    # truth: nothing ever wrote sold_by_us, because the sale threw the fact
    # away. Customers who bought from us were asked, on their next call, to
    # produce paperwork we issued ourselves.
    from .ownership import becomes_theirs

    owned = becomes_theirs(purchase_order_id)

    out = {
        "ok": True, "purchase_order": purchase_order_id, "status": "confirmed",
        "agreed_by": agreed_by or None,
        "lines": [{"sku": l["sku"], "description": l["description"],
                   "qty": l["qty"], "unit_price": l["unit_price"]}
                  for l in lines],
        "subtotal": po["subtotal"],
        "told_caller": "Confirm the order number and the total back to them. "
                       "Do not promise a delivery date beyond what "
                       "quote_delivery returned.",
    }

    if sourcing.get("ok") and sourcing.get("sourced"):
        out["being_sourced"] = sourcing["sourced"]
        out["ready_by"] = sourcing["ready_by"]
        out["told_caller"] = sourcing["say"]
    elif not sourcing.get("ok"):
        out["sourcing_failed"] = sourcing.get("why")
        out["told_caller"] = sourcing.get("say", out["told_caller"])

    if owned.get("registered"):
        out["now_theirs"] = owned["registered"]
        out["cover_is_ours"] = True
    return out



def supplier_options(sku: str) -> dict:
    """What suppliers have recently quoted us for a part, and how fast.

    Vendors ring this desk to pitch, and their offers were written down and
    then never read by anything. Meanwhile the parts desk tells customers a
    part is nine days out based only on the catalogue lead time.

    Both facts were already in the database and nothing put them side by side.
    A supplier who quoted three days last week is the difference between a
    customer waiting a fortnight and a job finishing this week.

    Args:
        sku: the part that is short.
    """
    with db.connect() as c:
        part = c.execute(
            "SELECT sku, name, lead_time_days FROM parts WHERE sku=?",
            (sku,)).fetchone()
        offers = c.execute(
            """SELECT s.name supplier, s.phone, o.offering, o.price_quoted,
                      o.lead_time, o.logged_at
               FROM supplier_offers o
               JOIN suppliers s ON s.id = o.supplier_id
               WHERE o.status <> 'expired'
                 AND (o.offering LIKE ? OR o.offering LIKE ?)
               ORDER BY o.logged_at DESC LIMIT 5""",
            (f"%{sku}%", f"%{(part['name'] if part else sku)}%")).fetchall()

    catalogue_days = part["lead_time_days"] if part else None
    quotes = [{"supplier": o["supplier"], "phone": o["phone"],
               "offered": o["offering"], "price": o["price_quoted"],
               "lead_time": o["lead_time"], "quoted_on": o["logged_at"]}
              for o in offers]

    if not quotes:
        return {"sku": sku, "catalogue_lead_days": catalogue_days, "quotes": [],
                "say": "No supplier has quoted us on that recently, so the "
                       "catalogue lead time is the honest answer."}

    return {
        "sku": sku, "part": part["name"] if part else None,
        "catalogue_lead_days": catalogue_days,
        "quotes": quotes,
        "say": "A supplier has quoted us on this. Say we will check whether we "
               "can get it faster and come back to them. Do not promise the "
               "supplier's lead time as ours: they quoted us, they have not "
               "shipped it.",
    }
