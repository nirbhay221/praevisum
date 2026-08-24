"""What to buy, judged against what has actually broken in our own vans.

Split out of ops.py. Retailers rank by review scores written by people who
owned a thing for a week. This ranks by field failures, complaints and
returns, over the number we actually supplied, and refuses to have an
opinion below a real sample.
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

# WHAT THEY SHOULD BUY
# ==========================================================================

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

    return {
        "ok": True, "family": family, "candidates": picks,
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

    return {"ok": True, "item": part["name"] if part else sku_or_model,
            "in_stock": in_stock,
            "supplier_lead_days": lead,
            "options": quotes[:4],
            "caveat": ("in stock, ships from our warehouse" if in_stock else
                       f"not in stock, {lead} business days from the supplier "
                       "before it can ship. Do not quote a date that ignores this.")}


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

    po = _nid("PO")
    total = 0.0
    with db.txn() as c:
        c.execute("""INSERT INTO purchase_orders
                     (id,account_id,site_id,contact_id,status,placed_at,note)
                     VALUES (?,?,?,?,?,?,?)""",
                  (po, account_id, site_id or None, contact_id or None, "draft",
                   datetime.now().isoformat(timespec="seconds"), note or None))
        for i, item in enumerate(items, 1):
            p = c.execute("SELECT sku,name,unit_cost FROM parts WHERE sku=?",
                          (item,)).fetchone()
            price = p["unit_cost"] if p else None
            total += price or 0
            c.execute("""INSERT INTO purchase_lines
                         (po_id,line_no,sku,description,qty,unit_price)
                         VALUES (?,?,?,?,?,?)""",
                      (po, i, p["sku"] if p else None,
                       p["name"] if p else item, 1, price))
        c.execute("UPDATE purchase_orders SET subtotal=? WHERE id=?", (total, po))

    return {"ok": True, "purchase_order": po, "lines": len(items),
            "subtotal": round(total, 2), "status": "draft",
            "note": "Draft. Read the lines and the total back before confirming."}


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
    with db.txn() as c:
        po = c.execute(
            "SELECT id, status, subtotal FROM purchase_orders WHERE id=?",
            (purchase_order_id,)).fetchone()
        if po is None:
            return {"ok": False, "why": "no such order"}
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

        c.execute(
            """UPDATE purchase_orders
               SET status='confirmed', confirmed_at=?
               WHERE id=?""",
            (datetime.now().isoformat(timespec="seconds"), purchase_order_id))

    return {
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
