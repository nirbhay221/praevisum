"""The decision a senior technician makes in their head, made explicitly.

The rest of this system is a pipeline and that is the correct shape for most of
it: look up who is calling, look up the machine, look up the diary. Those are
facts, and a model that "reasons" about a fact it could have read is a model
inventing things.

This file is the part that is genuinely not a lookup.

THE VAN-LOADING PROBLEM
    A technician has finite space and the parts desk has finite stock. The
    corpus says a symptom splits, say, 55% one cause, 30% another, 15% a third.
    Carrying everything is not free: parts sit in a van instead of on a shelf
    where another job needs them. Carrying too little is the 51% of failed
    first visits that this whole product exists to reduce.

    The senior technician's judgment is an expected-value calculation they do
    without writing it down:

        carry it   if  P(needed) x cost of NOT having it  >  cost of carrying

    where the cost of not having it is a wasted truck roll, plus the customer
    waiting out the supplier lead time with a broken machine, plus the fact
    that a restaurant losing stock at 6pm is a different customer tomorrow.

    A junior technician takes the one part the ticket names. That is why
    first-visit-fix sits at 73%.

THE QUESTION-TO-ASK PROBLEM
    When the corpus splits evenly between two causes, the useful thing is not
    a guess. It is the one question whose answer separates them, chosen because
    it separates them rather than because it sounds thorough.

Both are computed from the dealer's own closed jobs. Nothing here is a prior
somebody typed in.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from . import db
from .memory import index_for

# Aberdeen: a failed first visit means 2.7 visits and 34% higher cost.
# TSIA puts a fully loaded dispatch near $1,000; $250-500 is the common
# direct-cost figure. Using the conservative end deliberately.
TRUCK_ROLL = 300.0

# What it costs to have a part in a van rather than on the shelf: tied-up
# capital plus the chance another job needs it while it is driving around.
CARRY_RATE = 0.04


# A machine we have never touched is not a machine we know nothing about.
#
# For a long time this system answered "we have never supplied one of those"
# for 32,730 of the 32,767 machines in the catalogue, and I kept calling that
# a hard ceiling. It was not. It was the wrong question.
#
# A defrost termination thermostat fits 49 different manufacturers. A control
# board fits 84. In this book the same part was replaced across 17 makes and 23
# models, and one symptom turned up across 15 makes. Faults do not belong to
# models. They belong to components.
#
# The federal catalogue records, for every certified machine, what kind of
# defrost it has and what refrigerant it runs. Those are the things that decide
# how a fridge fails. Matching on them instead of on the model badge takes the
# reach from 37 models to 21,533, using data that was already loaded and only
# ever used to spell model numbers correctly.
#
# Parts still never cross. The fitment guard downstream is unchanged: a fault
# seen on another make is a hint, a part number from another make is a
# technician holding something that does not fit.
# Above 1.0, because unrelated evidence scores 1.0 and a machine of the same
# design is better than that. Set below 1.0 at first, which quietly made
# comparable machines count for LESS than a stranger's, the opposite of the
# point. Ordered: unrelated 1.0 < same design < same equipment type < same make.
_PROFILE_WEIGHT = 1.15
_MODEL_WEIGHT = 1.4
_FAMILY_WEIGHT = 1.25


def _profile_of(manufacturer: str, model: str) -> tuple | None:
    """A machine's component fingerprint, from the certification data."""
    if not manufacturer:
        return None
    with db.connect() as c:
        row = c.execute(
            """SELECT product_type, COALESCE(defrost_type,'') d,
                      COALESCE(refrigerant,'') r
               FROM equipment
               WHERE brand = ? AND (? = '' OR model_number = ?)
                 AND product_type IS NOT NULL
               LIMIT 1""", (manufacturer, model, model)).fetchone()
    return (row["product_type"], row["d"], row["r"]) if row else None


def _models_sharing(profile: tuple) -> set:
    """Every make and model with the same component fingerprint.

    Product type and defrost type must match. Refrigerant is not required,
    because a door gasket does not care what is in the pipes, and demanding all
    three collapses the reach for very little accuracy.
    """
    if not profile:
        return set()
    with db.connect() as c:
        return {(r["brand"], r["model_number"]) for r in c.execute(
            """SELECT brand, model_number FROM equipment
               WHERE site_visit = 1 AND product_type = ?
                 AND COALESCE(defrost_type,'') = ?""", (profile[0], profile[1]))}


def _fault_distribution(dealer_id: str, symptom: str, manufacturer: str = "",
                        family: str = "", model: str = "",
                        limit: int = 25) -> list[dict]:
    """What this symptom has actually turned out to be, as a distribution.

    Weighted by retrieval score rather than counted flat, because a repair that
    matches the caller's description closely is better evidence than one that
    merely shares an equipment type.

    Evidence is ranked by how close it is to this machine: the same model
    first, then the same make, then the same equipment family, then anything
    sharing a component profile. That last tier is what lets the desk say
    something useful about a machine it has never been called out to.
    """
    hits = index_for(dealer_id).search(symptom, limit=limit)
    if not hits:
        return []

    profile = _profile_of(manufacturer, model)
    kin = _models_sharing(profile) if profile else set()

    weights: dict[str, float] = defaultdict(float)
    parts_for: dict[str, Counter] = defaultdict(Counter)
    sources: dict[str, set] = defaultdict(set)
    for h in hits:
        if h.score <= 0.02:
            continue
        r = h.repair
        w = h.score
        where = "elsewhere"
        # our own machines and makes are stronger evidence than a stranger's
        if family and getattr(r, "family", None) == family:
            w *= _FAMILY_WEIGHT
            where = "same equipment type"
        if kin and (r.manufacturer, r.model) in kin:
            w *= _PROFILE_WEIGHT
            where = "same defrost and cooling design"
        if manufacturer and r.manufacturer.lower() == manufacturer.lower():
            w *= _MODEL_WEIGHT
            where = "same make"
        cause = r.found_cause.split(".")[0].strip()[:90]
        weights[cause] += w
        sources[cause].add(where)
        for sku in r.parts_consumed:
            parts_for[cause][sku] += 1

    total = sum(weights.values()) or 1.0
    out = []
    for cause, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        out.append({
            "cause": cause,
            "probability": round(w / total, 3),
            "parts": [sku for sku, _ in parts_for[cause].most_common(3)],
            "evidence_from": sorted(sources[cause]),
        })
    return out


def what_to_load(dealer_id: str, asset_id: str, symptom: str,
                 technician_id: str = "") -> dict:
    """Decide what actually goes in the van, and say why in money.

    Not a frequency count. For each candidate part this weighs the chance the
    job needs it against what a wasted trip costs, and reports the two numbers
    so a human can disagree with the arithmetic rather than with a hunch.

    Args:
        dealer_id: whose corpus and whose stock.
        asset_id: the machine.
        symptom: the caller's own words.
        technician_id: if known, parts already in their van are free to bring.

    Returns:
        A load list with the reasoning, the parts deliberately left behind and
        why, and the one question that would most reduce the uncertainty.
    """
    with db.connect() as c:
        asset = c.execute(
            """SELECT id, manufacturer, model_number, family
               FROM assets WHERE id=?""", (asset_id,)).fetchone()
        if asset is None:
            return {"ok": False, "why": "unknown machine"}

        fits = {r["sku"]: r["name"] for r in c.execute(
            """SELECT DISTINCT p.sku, p.name FROM parts p
               JOIN fitments f ON f.sku=p.sku
               WHERE p.dealer_id=? AND f.manufacturer=? AND ? LIKE f.model_pattern""",
            (dealer_id, asset["manufacturer"], asset["model_number"]))}
        if not fits:
            fits = {r["sku"]: r["name"] for r in c.execute(
                "SELECT sku,name FROM parts WHERE dealer_id=?", (dealer_id,))}

        cost = {r["sku"]: (r["unit_cost"] or 0.0, r["lead_time_days"] or 0)
                for r in c.execute(
                    "SELECT sku,unit_cost,lead_time_days FROM parts WHERE dealer_id=?",
                    (dealer_id,))}

        free = {r["sku"]: r["free"] for r in c.execute(
            "SELECT sku, SUM(free) free FROM stock_available GROUP BY sku")}

        in_van: set[str] = set()
        if technician_id:
            in_van = {r["sku"] for r in c.execute(
                """SELECT s.sku FROM stock s JOIN technicians t
                          ON t.van_location = s.location_id
                   WHERE t.id=? AND s.on_hand > 0""", (technician_id,))}

    dist = _fault_distribution(dealer_id, symptom, asset["manufacturer"],
                               asset["family"], asset["model_number"])
    if not dist:
        return {"ok": True, "asset": asset_id, "confident": False,
                "distribution": [], "load": [], "left_behind": [],
                "reasoning": "Nothing in our own history matches this "
                             "description, so there is no basis to pick parts. "
                             "Say so rather than guessing.",
                "ask": "Ask what the display shows and whether it is iced up, "
                       "so the visit at least starts with something."}

    # probability each part is needed = sum over causes that consume it
    p_needed: dict[str, float] = defaultdict(float)
    because: dict[str, list[str]] = defaultdict(list)
    for d in dist:
        for sku in d["parts"]:
            if sku in fits:
                p_needed[sku] += d["probability"]
                because[sku].append(f"{d['cause'][:56]} ({int(d['probability']*100)}%)")

    load, left = [], []
    for sku, p in sorted(p_needed.items(), key=lambda kv: -kv[1]):
        price, lead = cost.get(sku, (0.0, 0))
        available = (free.get(sku) or 0) > 0
        carrying = price * CARRY_RATE

        # what it costs to be wrong: the wasted trip, made worse when the part
        # then has to be ordered and the customer waits
        miss = TRUCK_ROLL * (1 + min(lead, 10) / 10)
        expected_saving = p * miss
        verdict = expected_saving > carrying

        row = {
            "sku": sku, "name": fits.get(sku, sku),
            "probability": round(p, 3),
            "unit_cost": price, "lead_time_days": lead,
            "in_van_already": sku in in_van,
            "in_stock": available,
            "expected_saving": round(expected_saving, 2),
            "carrying_cost": round(carrying, 2),
            "because": because[sku][:2],
        }

        if sku in in_van:
            row["note"] = "already on the van, costs nothing to bring"
            load.append(row)
        elif not available:
            row["note"] = (f"not in stock, {lead} day lead. Cannot be carried, "
                           "and if this is the fault the job cannot finish today")
            left.append(row)
        elif verdict:
            row["note"] = (f"worth ${expected_saving:.0f} in avoided return trips "
                           f"against ${carrying:.0f} to carry it")
            load.append(row)
        else:
            row["note"] = (f"only ${expected_saving:.0f} of expected value "
                           f"against ${carrying:.0f} to carry. Leave it")
            left.append(row)

    top = dist[0]
    confident = top["probability"] >= 0.55
    ask = _best_question(dist) if not confident else None

    covered = sum(r["probability"] for r in load)
    reasoning = (
        f"Our own jobs put this at {int(top['probability']*100)}% "
        f"{top['cause'][:60]}"
        + (f", with {int(dist[1]['probability']*100)}% {dist[1]['cause'][:44]}"
           if len(dist) > 1 else "")
        + f". Loading {len(load)} part(s) covers roughly "
          f"{int(min(covered,1.0)*100)}% of what it is likely to be."
    )

    return {
        "ok": True,
        "asset": asset_id,
        "machine": f"{asset['manufacturer']} {asset['model_number']}",
        "confident": confident,
        "distribution": dist[:4],
        "load": load,
        "left_behind": left,
        "reasoning": reasoning,
        "ask": ask,
        "assumptions": {
            "wasted_truck_roll": TRUCK_ROLL,
            "carrying_rate": CARRY_RATE,
            "note": "A part is carried when the chance of needing it, times "
                    "the cost of a wasted trip, beats the cost of it riding "
                    "around instead of sitting on the shelf.",
        },
    }


def _best_question(dist: list[dict]) -> str | None:
    """The one question that separates the leading candidates.

    Chosen because it splits the distribution, not because it sounds thorough.
    A question whose answer cannot change what goes in the van is a question
    that wastes a caller's time while their freezer warms up.
    """
    if len(dist) < 2:
        return None

    # what actually distinguishes refrigeration failures, in the order a
    # dispatcher would reach for them
    probes = [
        ({"defrost", "ice", "frost", "coil"},
         "Is there ice or frost built up on the coil at the back, or is it "
         "just warm with no ice?"),
        ({"fan", "motor", "seized", "noisy", "rattl"},
         "Can you hear the fan running when it is cooling, or is it silent?"),
        ({"door", "gasket", "seal", "sweat", "mullion"},
         "Does the door seal properly, and is there condensation around the "
         "frame?"),
        ({"board", "control", "display", "error", "power"},
         "Is anything showing on the display, or is it completely dead?"),
        ({"condenser", "grease", "dirty", "blocked", "cycling"},
         "When was the condenser last cleaned? These get packed with grease "
         "in a kitchen."),
        ({"water", "drain", "leak", "pooling", "scale"},
         "Is there any water pooling underneath or inside it?"),
    ]

    a, b = dist[0]["cause"].lower(), dist[1]["cause"].lower()
    for keys, question in probes:
        in_a = any(k in a for k in keys)
        in_b = any(k in b for k in keys)
        if in_a != in_b:          # true for one, false for the other: it splits
            return question
    return ("What exactly is it doing, and when did it start? Our records are "
            "split on this one.")
