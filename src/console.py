"""The dealer's own side of the system: prices, stock and promotions.

The phone agent works for the customer. This one works for the owner, and it is
the same pattern pointed at a different person. They type or say

    "put the fan motors on buy three pay for two until the end of the month"

and it becomes rows, rather than a form with eleven fields.

WHY THE PRICING RULE IS IN CODE
    An agent that can decide to offer a discount is an agent whose every price
    becomes untrustworthy, including the honest ones. The FTC has specific
    rules about was-and-now claims where the "was" was never real, and a
    service business lives on repeat customers who find out.

    So the split is absolute: the OWNER sets promotions here, deliberately,
    with dates and amounts. The PHONE agent may only read them. It cannot
    create one, extend one, or apply a discount that is not on the record. Warm
    delivery of a true offer is good salesmanship; inventing the offer is not
    salesmanship at all.
"""

from __future__ import annotations

import uuid
import re
from datetime import date, datetime, timedelta

from . import db, events


def _nid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:6].upper()}"


def find_part(c, dealer_id: str, needle: str):
    """Find a part the way an owner refers to it, not the way it is stored.

    They say "condenser fan motors" and the row reads "Condenser fan motor".
    A LIKE on the whole phrase misses on the plural alone, and the agent then
    correctly refuses to guess, which reads as the console being broken. So
    match on SKU, then the exact name, then on shared words with plurals
    stripped, and only give up when nothing overlaps.
    """
    needle = (needle or "").strip()
    if not needle:
        return None

    row = c.execute(
        "SELECT sku,name,unit_cost FROM parts WHERE dealer_id=? AND sku=?",
        (dealer_id, needle.upper())).fetchone()
    if row:
        return row

    row = c.execute(
        "SELECT sku,name,unit_cost FROM parts WHERE dealer_id=? AND LOWER(name)=?",
        (dealer_id, needle.lower())).fetchone()
    if row:
        return row

    def words(text: str) -> set[str]:
        out = set()
        for w in re.findall(r"[a-z]+", text.lower()):
            if len(w) < 3:
                continue
            out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)
        return out

    want = words(needle)
    if not want:
        return None

    best, score = None, 0
    for r in c.execute("SELECT sku,name,unit_cost FROM parts WHERE dealer_id=?",
                       (dealer_id,)):
        overlap = len(want & words(r["name"]))
        if overlap > score:
            best, score = r, overlap
    # one shared word is enough only when that is all they gave us
    return best if score >= 2 or (score == 1 and len(want) == 1) else None


# ==========================================================================
# what the owner can change
# ==========================================================================

def upsert_part(dealer_id: str, name: str, unit_cost: float,
                sku: str = "", lead_time_days: int = 0,
                on_hand: int = 0) -> dict:
    """Add a part to the catalogue, or change one that is already there.

    Args:
        dealer_id: which business this belongs to.
        name: what it is called on the shelf.
        unit_cost: what we charge.
        sku: leave blank and one is generated from the name.
        lead_time_days: how long from the supplier when it is not in stock.
        on_hand: quantity to set at the main warehouse.
    """
    if not name.strip():
        return {"ok": False, "why": "a part needs a name"}
    if unit_cost is None or unit_cost < 0:
        return {"ok": False, "why": "a part needs a price"}

    sku = (sku or "").strip().upper()
    if not sku:
        stub = "".join(ch for ch in name.upper() if ch.isalnum())[:10]
        sku = f"{dealer_id.split('-')[-1][:3]}-{stub}"

    with db.txn() as c:
        existing = c.execute("SELECT sku FROM parts WHERE sku=?", (sku,)).fetchone()
        c.execute("""INSERT INTO parts (sku,name,unit_cost,lead_time_days,dealer_id)
                     VALUES (?,?,?,?,?)
                     ON CONFLICT(sku) DO UPDATE SET
                       name=excluded.name, unit_cost=excluded.unit_cost,
                       lead_time_days=excluded.lead_time_days""",
                  (sku, name.strip(), float(unit_cost), int(lead_time_days), dealer_id))

        if on_hand:
            loc = c.execute(
                """SELECT id FROM stock_locations
                   WHERE dealer_id=? AND kind='warehouse' LIMIT 1""",
                (dealer_id,)).fetchone()
            if loc:
                c.execute("""INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)
                             ON CONFLICT(location_id,sku) DO UPDATE SET
                               on_hand=excluded.on_hand""",
                          (loc["id"], sku, int(on_hand)))

    events.publish(dealer_id, "console",
                   what=f"{'updated' if existing else 'added'} {name} at ${unit_cost:.2f}")
    return {"ok": True, "sku": sku, "name": name,
            "action": "updated" if existing else "added",
            "unit_cost": unit_cost, "on_hand": on_hand}


def set_price(dealer_id: str, sku_or_name: str, unit_cost: float) -> dict:
    """Change what we charge for something.

    Args:
        dealer_id: which business.
        sku_or_name: the part, by number or by name.
        unit_cost: the new price.
    """
    with db.txn() as c:
        row = find_part(c, dealer_id, sku_or_name)
        if row is None:
            return {"ok": False, "why": f"no part matching '{sku_or_name}'"}
        c.execute("UPDATE parts SET unit_cost=? WHERE sku=?", (float(unit_cost), row["sku"]))

    events.publish(dealer_id, "console",
                   what=f"{row['name']} ${row['unit_cost']:.2f} to ${unit_cost:.2f}")
    return {"ok": True, "sku": row["sku"], "name": row["name"],
            "was": row["unit_cost"], "now": unit_cost}


def set_stock(dealer_id: str, sku_or_name: str, on_hand: int,
              location: str = "") -> dict:
    """Set how many we physically have.

    Args:
        dealer_id: which business.
        sku_or_name: the part.
        on_hand: the count.
        location: warehouse by default, or a van's label.
    """
    with db.txn() as c:
        part = find_part(c, dealer_id, sku_or_name)
        if part is None:
            return {"ok": False, "why": f"no part matching '{sku_or_name}'"}

        if location:
            loc = c.execute(
                "SELECT id,label FROM stock_locations WHERE dealer_id=? AND label LIKE ? LIMIT 1",
                (dealer_id, f"%{location}%")).fetchone()
        else:
            loc = c.execute(
                "SELECT id,label FROM stock_locations WHERE dealer_id=? AND kind='warehouse' LIMIT 1",
                (dealer_id,)).fetchone()
        if loc is None:
            return {"ok": False, "why": "no such stock location"}

        c.execute("""INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)
                     ON CONFLICT(location_id,sku) DO UPDATE SET on_hand=excluded.on_hand""",
                  (loc["id"], part["sku"], int(on_hand)))

    events.publish(dealer_id, "console",
                   what=f"{part['name']} stock set to {on_hand} at {loc['label']}")
    return {"ok": True, "sku": part["sku"], "name": part["name"],
            "location": loc["label"], "on_hand": on_hand}


def create_promotion(dealer_id: str, headline: str, ends: str,
                     detail: str = "", terms: str = "",
                     applies_to: list[str] | None = None) -> dict:
    """Put a real offer on the record, with an end date.

    This is the only way a discount can exist. The phone agent reads this table
    and may never write to it, which is what stops it inventing a sale to close
    a call.

    Args:
        dealer_id: which business is running it.
        headline: what the customer is told, e.g. "10% off defrost components".
        ends: last day it is valid, YYYY-MM-DD. Required.
        detail: the fuller sentence.
        terms: conditions, e.g. "trade accounts, while stock lasts".
        applies_to: part SKUs or names it covers. Empty means it is a general
            offer the agent can mention but not apply to a line item.
    """
    if not headline.strip():
        return {"ok": False, "why": "an offer needs a headline"}
    try:
        end = date.fromisoformat(ends)
    except ValueError:
        return {"ok": False, "why": "ends must be a date like 2026-09-30"}
    if end < date.today():
        return {"ok": False, "why": f"{ends} is in the past"}

    pid = _nid("P")
    matched, missed = [], []
    with db.txn() as c:
        c.execute("""INSERT INTO promotions (id,headline,detail,starts,ends,terms,dealer_id)
                     VALUES (?,?,?,?,?,?,?)""",
                  (pid, headline.strip(), detail or None,
                   date.today().isoformat(), ends, terms or None, dealer_id))
        for item in (applies_to or []):
            row = find_part(c, dealer_id, str(item))
            if row:
                c.execute("INSERT OR IGNORE INTO promotion_parts (promotion_id,sku) VALUES (?,?)",
                          (pid, row["sku"]))
                matched.append(row["name"])
            else:
                missed.append(str(item))

    events.publish(dealer_id, "console", what=f"offer live: {headline} (to {ends})")
    return {"ok": True, "promotion_id": pid, "headline": headline, "ends": ends,
            "applies_to": matched, "not_found": missed,
            "note": "The phone agent can now mention this. It could not have "
                    "invented it."}


def end_promotion(dealer_id: str, promotion_id: str) -> dict:
    """Stop an offer now rather than waiting for its end date.

    `ends` is the last day the offer is valid, and every reader of it asks for
    `ends >= today`. Setting it to today therefore kept the offer live for the
    rest of the day: the owner said stop and the phone desk carried on quoting
    it until midnight. Yesterday is the honest value, because what is being
    recorded is that the last valid day has already passed.
    """
    with db.txn() as c:
        row = c.execute("SELECT headline FROM promotions WHERE id=? AND dealer_id=?",
                        (promotion_id, dealer_id)).fetchone()
        if row is None:
            return {"ok": False, "why": "no such offer"}
        c.execute("UPDATE promotions SET ends=? WHERE id=?",
                  ((date.today() - timedelta(days=1)).isoformat(), promotion_id))
    events.publish(dealer_id, "console", what=f"offer ended: {row['headline']}")
    return {"ok": True, "ended": row["headline"]}


# ==========================================================================
# what the owner can see
# ==========================================================================

def snapshot(dealer_id: str) -> dict:
    """Everything the console shows in one query pass."""
    today = date.today().isoformat()
    with db.connect() as c:
        dealer = c.execute("SELECT * FROM dealers WHERE id=?", (dealer_id,)).fetchone()
        parts = c.execute(
            """SELECT p.sku, p.name, p.unit_cost, p.lead_time_days,
                      COALESCE(SUM(s.on_hand),0) on_hand,
                      COALESCE((SELECT SUM(free) FROM stock_available sa
                                WHERE sa.sku=p.sku),0) free
               FROM parts p LEFT JOIN stock s ON s.sku=p.sku
               WHERE p.dealer_id=? GROUP BY p.sku ORDER BY p.name""",
            (dealer_id,)).fetchall()
        promos = c.execute(
            """SELECT pr.id, pr.headline, pr.detail, pr.ends, pr.terms,
                      GROUP_CONCAT(p.name, ', ') parts
               FROM promotions pr
               LEFT JOIN promotion_parts pp ON pp.promotion_id = pr.id
               LEFT JOIN parts p ON p.sku = pp.sku
               WHERE pr.dealer_id=? AND pr.ends >= ?
               GROUP BY pr.id ORDER BY pr.ends""",
            (dealer_id, today)).fetchall()
        stats = c.execute(
            """SELECT
                 (SELECT COUNT(*) FROM accounts WHERE dealer_id=?) customers,
                 (SELECT COUNT(*) FROM technicians WHERE dealer_id=? AND active=1) techs,
                 (SELECT COUNT(*) FROM repairs WHERE dealer_id=?) repairs,
                 (SELECT COUNT(*) FROM work_orders WHERE dealer_id=? AND status!='closed') open_jobs,
                 (SELECT COUNT(*) FROM calls WHERE dealer_id=?) calls""",
            (dealer_id,)*5).fetchone()
        fvf = c.execute(
            """SELECT COUNT(*) n, SUM(f.fixed_first_time) fixed
               FROM first_visit_fix f JOIN work_orders w ON w.id=f.work_order_id
               WHERE w.dealer_id=?""", (dealer_id,)).fetchone()
        open_jobs = c.execute(
            """SELECT w.id, w.reported_symptom, w.status, a.name customer,
                      ast.manufacturer, ast.model_number
               FROM work_orders w
               JOIN accounts a ON a.id=w.account_id
               LEFT JOIN assets ast ON ast.id=w.asset_id
               WHERE w.dealer_id=? AND w.status!='closed'
               ORDER BY w.opened_at DESC LIMIT 10""", (dealer_id,)).fetchall()

    rate = (100 * (fvf["fixed"] or 0) / fvf["n"]) if fvf and fvf["n"] else None
    return {
        "dealer": dict(dealer) if dealer else {},
        "parts": [dict(p) for p in parts],
        "promotions": [dict(p) for p in promos],
        "stats": dict(stats) if stats else {},
        "first_visit_fix": round(rate, 1) if rate is not None else None,
        "open_jobs": [dict(j) for j in open_jobs],
    }


def dealers() -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, trade, phone_e164 FROM dealers ORDER BY name")]
