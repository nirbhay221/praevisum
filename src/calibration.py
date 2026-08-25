"""What a probability from this desk has actually meant.

WHY THIS CAN EXIST NOW AND COULD NOT BEFORE

The desk says "44% evaporator fan motor seized". A technician goes out, opens
the machine, and writes down what it really was. Those two facts have always
both existed and were never once compared, because the prediction was never
written down: it went into a dict, the model read it, and it was gone.

`decisions` keeps it now. `repairs.found_cause` has always kept the answer. So
the question "when this desk says 44%, is it right 44% of the time" is finally
a query rather than a wish.

WHY THIS IS NOT A BANDIT PROBLEM

The obvious framing is exploration: try a part, see if it was needed, learn.
That is wrong here, and the reason matters.

A bandit exists because you only learn about the arm you pulled. Here the
technician reports `found_cause`, which is the truth about the machine rather
than a verdict on the part we sent. So a visit where we carried the fan motor
and it turned out to be the defrost heater teaches us about the fan motor, the
heater AND the thermostat at once.

That is full-information feedback. With it there is nothing hidden to explore,
so exploration would mean deliberately sending a part we believe is wrong to a
customer with a broken freezer, and learning nothing we were not going to be
told anyway.

WHY IT MEASURES AND DOES NOT CORRECT

The scores this desk turns into probabilities are normalised retrieval
similarities, which are not probabilities and were never going to be. The
honest response is to say what they have meant, not to scale them until they
look right.

Applying a correction learned from a corpus this size, on a book where the
repairs were generated, produces a well calibrated number about a fiction. It
would read as more rigorous and be less true. So this reports the curve and
the sample behind every point, and changes nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher

from . import db

# How alike a predicted cause and the technician's own words have to be to
# count as the same fault.
#
# Never an exact match. The desk predicts from the corpus, in the phrasing of
# whoever closed those jobs, and the technician writes fresh prose with grease
# on their hands. "evaporator fan motor seized, no air across the coil" and
# "evap fan had seized" are the same finding and share few characters.
SAME_FAULT = 0.55

# Bands the predictions are grouped into. Coarse on purpose: the literature is
# blunt that expected calibration error depends heavily on binning, and finer
# bands on a few hundred visits would report noise as structure.
BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01))

# Below this many visits a band says nothing. The same rule as MIN_SAMPLE in
# the buying advice: this whole module exists to stop the desk being
# confidently wrong, so it must not become the thing it is measuring.
ENOUGH_IN_A_BAND = 5


def _same_fault(predicted: str, actual: str) -> bool:
    a = " ".join((predicted or "").lower().split())
    b = " ".join((actual or "").lower().split())
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= SAME_FAULT


def _predictions(dealer_id: str, days: int) -> list[dict]:
    """Every prediction that a technician later gave a verdict on.

    Joined through the call, which is the only thing tying a thing the desk
    said to a thing that later turned out to be true.
    """
    import json

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db.connect() as c:
        rows = c.execute(
            """SELECT d.subject predicted, d.numbers, d.at,
                      r.found_cause actual, r.id repair_id
               FROM decisions d
               JOIN work_orders w ON w.opened_from_call = d.call_id
               JOIN visits v      ON v.work_order_id = w.id
               JOIN repairs r     ON r.visit_id = v.id
               WHERE d.dealer_id = ? AND d.kind = 'fault_distribution'
                 AND d.subject IS NOT NULL AND r.found_cause IS NOT NULL
                 AND d.at >= ?""", (dealer_id, cutoff)).fetchall()

    out = []
    for r in rows:
        try:
            p = (json.loads(r["numbers"] or "{}") or {}).get("probability")
        except Exception:
            p = None
        if p is None:
            continue
        out.append({"probability": float(p), "predicted": r["predicted"],
                    "actual": r["actual"], "repair": r["repair_id"]})
    return out


def reliability(dealer_id: str = "D-REF", days: int = 365) -> dict:
    """What this desk's probabilities have been worth, band by band.

    Reports and does not correct. A number that says 70 and is right 48 times
    in a hundred is a finding a dealer can act on; the same number quietly
    scaled to 48 is a claim about a corpus this small that nobody can check.

    Args:
        dealer_id: whose desk.
        days: how far back.
    """
    rows = _predictions(dealer_id, days)
    if not rows:
        return {
            "checked": 0,
            "say": "No prediction has yet been followed by a technician saying "
                   "what it really was. This is not a good result or a bad "
                   "one, it is an empty one.",
        }

    bands = []
    for lo, hi in BANDS:
        inside = [r for r in rows if lo <= r["probability"] < hi]
        if not inside:
            continue
        right = sum(1 for r in inside if _same_fault(r["predicted"], r["actual"]))
        band = {
            "said": f"{int(lo * 100)} to {int(hi * 100)}%",
            "visits": len(inside),
            "right": right,
            "claimed": round(sum(r["probability"] for r in inside) / len(inside), 2),
        }
        if len(inside) >= ENOUGH_IN_A_BAND:
            band["actually"] = round(right / len(inside), 2)
            band["gap"] = round(band["actually"] - band["claimed"], 2)
        else:
            band["actually"] = None
            band["why"] = f"only {len(inside)} visits, too few to say"
        bands.append(band)

    scored = [b for b in bands if b["actually"] is not None]
    total = sum(b["visits"] for b in scored)

    return {
        "checked": len(rows),
        "bands": bands,
        # One number, over the bands that had enough behind them. Weighted by
        # how many visits sat in each, so a band of six does not outvote a
        # band of sixty.
        "overconfident_by": (
            round(-sum(b["gap"] * b["visits"] for b in scored) / total, 2)
            if total else None),
        "say": ("Each band is what the desk claimed against what the technician "
                "found. Nothing here is corrected: these scores are normalised "
                "retrieval similarities and never were probabilities, so the "
                "honest response is to report what they have meant rather than "
                "to scale them until they look right."),
    }


def worst_misses(dealer_id: str = "D-REF", days: int = 365,
                 limit: int = 10) -> list[dict]:
    """Confident predictions that turned out wrong, worst first.

    The band table says whether the desk is overconfident. This says where,
    which is what somebody can actually go and look at.
    """
    rows = [r for r in _predictions(dealer_id, days)
            if not _same_fault(r["predicted"], r["actual"])]
    rows.sort(key=lambda r: -r["probability"])
    return [{
        "we_said": f"{int(r['probability'] * 100)}% {r['predicted'][:70]}",
        "it_was": r["actual"][:70],
        "repair": r["repair"],
    } for r in rows[:limit]]
