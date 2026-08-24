"""Tools. Plain functions - ADK builds FunctionTools from the signature and
docstring, so the docstrings are prompt surface rather than decoration.

Deterministic code owns every decision with a consequence: what is in stock,
who is qualified, whether a slot can be promised. The model narrates and picks
which tool to reach for. It never decides whether a part exists.

Everything here reads the database. That sentence is the whole point of this
file's most recent rewrite: for several days these functions read Python
dictionaries seeded at import, and the worst of it was not that they were
empty. `find_technician` cheerfully returned two technicians from fixture data
while eight real ones sat in SQL, so the system was confidently wrong rather
than obviously broken. There is no in-memory store any more.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from google.adk.tools import ToolContext

from . import db
from .domain.geo import drive_minutes, miles
from .memory import index_for

DEFAULT_DEALER = "D-REF"


def _dealer(tool_context: ToolContext | None = None) -> str:
    """Whose business this call belongs to.

    Comes from the number that was dialled, put into session state when the
    line opened. One service answers several companies' phones and they share
    nothing except the public equipment catalogue.
    """
    if tool_context is not None:
        d = tool_context.state.get("dealer_id")
        if d:
            return str(d)
    return DEFAULT_DEALER


def _nid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


class _PartUnavailable(Exception):
    """Raised inside the promise transaction so the whole thing rolls back."""

    def __init__(self, sku: str) -> None:
        super().__init__(sku)
        self.sku = sku


_COMPANY_NOISE = {"co", "inc", "llc", "ltd", "corp", "company", "the",
                  "supply", "supplies", "group"}


def _company_stem(name: str) -> str:
    """A vendor name reduced to what actually identifies it."""
    words = [w for w in "".join(
        ch.lower() if ch.isalnum() else " " for ch in (name or "")).split()
        if w not in _COMPANY_NOISE]
    return " ".join(words)


# --------------------------------------------------------------------------
# what this call is about
# --------------------------------------------------------------------------

INTENTS = {
    "service": "something is broken and they need a technician",
    "order": "they want to buy or reorder a part",
    "product": "a question about equipment, parts, compatibility or price",
    "supplier": "a vendor calling US to sell something or quote us",
}


def set_intent(intent: str, tool_context: ToolContext) -> dict:
    """Record what this call is about, as soon as it is clear.

    Call it once you know, usually after their first sentence. It does not end
    or restrict the conversation: a caller who starts with an order and then
    mentions a broken freezer can be re-routed by calling this again.

    Args:
        intent: one of "service", "order", "product", "supplier".
    """
    intent = (intent or "").strip().lower()
    if intent not in INTENTS:
        return {"ok": False, "valid": list(INTENTS), "got": intent}

    tool_context.state["intent"] = intent

    # Also onto the call row. This column has existed since the first schema
    # and never held anything, because writing it to session state looked like
    # recording it. Session state dies with the process; the row is what any
    # later question about how the desk did has to be answered from.
    from .review import record_intent

    record_intent(tool_context.state.get("call_id") or "", intent)

    nxt = {
        "service": "Establish which machine and the fault, then assess_job.",
        "order": "Find the part, check stock, then open_work_order for supply.",
        "product": "Answer from the catalogue only. Never invent a spec or price.",
        "supplier": "Take the offer down with log_supplier_offer. Commit to nothing.",
    }[intent]
    return {"ok": True, "intent": intent, "means": INTENTS[intent], "next": nxt}


# --------------------------------------------------------------------------
# which machine
# --------------------------------------------------------------------------

def identify_equipment(spoken: str, brand_hint: str = "") -> dict:
    """Work out which machine they have from a model number read down the phone.

    Searches the certified equipment catalogue, which is public federal data
    covering tens of thousands of models across 1,489 manufacturers.

    A model number spoken aloud never arrives clean, so matching runs against a
    normalised form with dashes, spaces and case stripped, then by prefix, then
    by containment. Exact matching would fail on almost every real call.

    Args:
        spoken: the model number as heard, however mangled.
        brand_hint: manufacturer if they mentioned one, which narrows it a lot.

    Returns:
        Candidates with type, defrost type and refrigerant. R-290 and R-600a
        are flammable and charge-limited, which the technician needs to know
        before opening a panel.
    """
    raw = (spoken or "").strip()
    if not raw:
        return {"found": False, "reason": "nothing heard"}

    norm = "".join(ch for ch in raw.upper() if ch.isalnum())
    if len(norm) < 3:
        return {"found": False, "reason": "too short to identify", "heard": raw}

    base = """
        SELECT brand, model_number,
               MIN(product_type) product_type, MIN(defrost_type) defrost_type,
               MIN(refrigerant) refrigerant, MIN(category) category
        FROM equipment
        WHERE site_visit = 1 AND {clause}
    """
    tail = " GROUP BY brand, model_number LIMIT 6"
    brand_sql, brand_params = "", []
    if brand_hint.strip():
        brand_sql = " AND brand LIKE ?"
        brand_params = [f"%{brand_hint.strip()}%"]

    with db.connect() as c:
        def run(clause: str, params: list):
            return c.execute(base.format(clause=clause) + brand_sql + tail,
                             params + brand_params).fetchall()

        exact = run("model_norm = ?", [norm])
        prefix = run("model_norm LIKE ?", [norm + "%"]) if not exact else []
        contains = run("model_norm LIKE ?", ["%" + norm + "%"]) if not (exact or prefix) else []

    hits = list(exact or prefix or contains)
    if not hits:
        return {
            "found": False, "heard": raw, "normalised": norm,
            "advice": "Ask them to read the model number off the data plate "
                      "again, letter by letter, or tell you the make.",
            "caveat": "This catalogue covers models certified with the EPA. "
                      "Plenty of real equipment is never certified, so a miss "
                      "does not mean the machine is not genuine.",
        }

    def render(r):
        return {
            "brand": r["brand"], "model": r["model_number"],
            "type": r["product_type"], "category": r["category"],
            "defrost": r["defrost_type"], "refrigerant": r["refrigerant"],
            "flammable_refrigerant": (r["refrigerant"] or "").upper() in
                                     {"R-290", "R290", "R-600A", "R600A"},
        }

    return {
        "found": True, "heard": raw,
        "match_quality": "exact" if exact else ("prefix" if prefix else "partial"),
        "candidates": [render(r) for r in hits],
        "confirm": len(hits) > 1,
        "advice": ("Several models match. Read the closest one back and ask if "
                   "that is theirs." if len(hits) > 1 else
                   "One match. Confirm it back to them in plain words."),
    }


def equipment_recalls(brand: str, model: str = "") -> dict:
    """Any published safety recall touching this machine.

    A technician walking up to a unit should know if it is under an active
    recall before they open it. Public CPSC data that currently reaches nobody
    in the field.

    Args:
        brand: manufacturer name.
        model: model number, if known.
    """
    b = (brand or "").strip()
    if not b:
        return {"checked": False}

    with db.connect() as c:
        rows = c.execute(
            """SELECT recall_number, recall_date, title, hazard, remedy, models, url
               FROM recalls WHERE brands LIKE ? OR title LIKE ?
               ORDER BY recall_date DESC LIMIT 5""",
            (f"%{b}%", f"%{b}%")).fetchall()

    out = [{
        "recall_date": r["recall_date"], "title": r["title"],
        "hazard": r["hazard"], "remedy": r["remedy"], "url": r["url"],
        "model_named": not (model and r["models"]
                            and model.upper() not in (r["models"] or "").upper()),
    } for r in rows]
    return {"checked": True, "brand": b, "count": len(out), "recalls": out}


# --------------------------------------------------------------------------
# the differentiator: history nobody hands the technician
# --------------------------------------------------------------------------

def prior_repairs(asset_id: str, symptom: str = "",
                  tool_context: ToolContext | None = None) -> dict:
    """What this machine and this model have needed before, and what fixed it.

    Reads closed visits for the exact machine first, then every machine of the
    same model across all sites, then searches the whole corpus by meaning so a
    caller's words find a technician's words even when they share none.

    Reports parts *consumed* rather than ordered: what was actually fitted is
    the signal.

    Args:
        asset_id: the machine's id.
        symptom: the caller's own words, used to weight what matters.

    Returns:
        This machine's history, the model-wide history, similar faults recalled
        from anywhere in this dealer's book, and `commonly_needed` - the parts
        that recur for this fault, filtered to those that physically fit.
    """
    dealer = _dealer(tool_context)

    with db.connect() as c:
        asset = c.execute(
            "SELECT id, manufacturer, model_number, family FROM assets WHERE id=?",
            (asset_id,)).fetchone()
        if asset is None:
            return {"found": False, "asset_id": asset_id}

        this_unit = c.execute(
            """SELECT closed_on, reported_symptom, error_code, found_cause,
                      tech_note, parts_consumed, labor_hours, first_visit_fix
               FROM repairs WHERE asset_id=? AND dealer_id=?
               ORDER BY closed_on DESC LIMIT 5""",
            (asset_id, dealer)).fetchall()

        model_wide = c.execute(
            """SELECT closed_on, reported_symptom, found_cause, parts_consumed,
                      labor_hours
               FROM repairs
               WHERE manufacturer=? AND model_number=? AND asset_id!=?
                 AND dealer_id=?
               ORDER BY closed_on DESC LIMIT 5""",
            (asset["manufacturer"], asset["model_number"], asset_id, dealer)).fetchall()

        # a part is only ever suggested when it physically fits this machine.
        # Recall crosses manufacturers on purpose because a failure mode on one
        # brand is a real hint about another; part numbers never do.
        fits = {r["sku"]: r["name"] for r in c.execute(
            """SELECT DISTINCT p.sku, p.name FROM parts p
               JOIN fitments f ON f.sku=p.sku
               WHERE p.dealer_id=? AND f.manufacturer=? AND ? LIKE f.model_pattern""",
            (dealer, asset["manufacturer"], asset["model_number"]))}

    semantic = []
    if symptom:
        for h in index_for(dealer).search(symptom, limit=6):
            if h.repair.serial == asset_id or h.score <= 0.05:
                continue
            semantic.append({
                "date": h.repair.closed_on,
                "on": f"{h.repair.manufacturer} {h.repair.model}",
                "found": h.repair.found_cause,
                "parts_consumed": list(h.repair.parts_consumed),
                "score": h.score,
            })

    counts: dict[str, int] = {}
    for row in list(this_unit) + list(model_wide):
        for sku in (row["parts_consumed"] or "").split(","):
            if sku and sku in fits:
                counts[sku] = counts.get(sku, 0) + 1
    for hit in semantic:
        for sku in hit["parts_consumed"]:
            if sku in fits:
                counts[sku] = counts.get(sku, 0) + 1

    def render(rows):
        return [{
            "date": r["closed_on"], "reported": r["reported_symptom"],
            "found": r["found_cause"],
            "parts_consumed": [s for s in (r["parts_consumed"] or "").split(",") if s],
            "labor_hours": r["labor_hours"],
        } for r in rows]

    return {
        "found": True,
        "asset_id": asset_id,
        "manufacturer": asset["manufacturer"],
        "model": asset["model_number"],
        "family": asset["family"],
        "this_unit": render(this_unit),
        "same_model_elsewhere": render(model_wide),
        "similar_faults_recalled": semantic[:4],
        "commonly_needed": [
            {"sku": sku, "name": fits.get(sku, sku), "seen_on_visits": n}
            for sku, n in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
        "corpus_size": index_for(dealer).size(),
    }


# --------------------------------------------------------------------------
# parts and people
# --------------------------------------------------------------------------

def check_stock(skus: list[str], tool_context: ToolContext | None = None) -> dict:
    """Live availability and lead time for specific part numbers.

    Availability is on-hand minus anything already held for another visit, so
    two jobs can never be promised the same last part.

    Args:
        skus: part numbers to check.
    """
    dealer = _dealer(tool_context)
    rows = []
    with db.connect() as c:
        for sku in (skus or []):
            p = c.execute(
                "SELECT sku,name,unit_cost,lead_time_days FROM parts WHERE sku=? AND dealer_id=?",
                (sku, dealer)).fetchone()
            if p is None:
                rows.append({"sku": sku, "known": False})
                continue
            free = c.execute(
                "SELECT COALESCE(SUM(free),0) f FROM stock_available WHERE sku=?",
                (sku,)).fetchone()["f"]
            rows.append({
                "sku": sku, "known": True, "name": p["name"],
                "available_now": free,
                "lead_time_days": 0 if free > 0 else p["lead_time_days"],
                "unit_cost": p["unit_cost"],
            })
    return {"parts": rows,
            "can_complete_today": bool(rows) and all(r.get("available_now", 0) > 0
                                                     for r in rows)}


def find_technician(family: str, asset_id: str = "",
                    tool_context: ToolContext | None = None) -> dict:
    """Technicians qualified on this equipment, nearest first.

    Dispatch is skills AND proximity: a qualified technician ninety minutes
    away is not a better answer than a qualified one fifteen minutes away.

    Args:
        family: e.g. "reach-in freezer", "walk-in cooler", "laptop".
        asset_id: the machine, used to locate the site.
    """
    dealer = _dealer(tool_context)
    with db.connect() as c:
        techs = c.execute(
            """SELECT t.id, t.name, t.home_base, t.lat, t.lon, t.van_location
               FROM technicians t JOIN technician_skills k ON k.technician_id=t.id
               WHERE t.dealer_id=? AND t.active=1 AND k.family=?""",
            (dealer, family)).fetchall()

        site = None
        if asset_id:
            site = c.execute(
                """SELECT s.label, s.lat, s.lon FROM assets a
                   JOIN sites s ON s.id=a.site_id WHERE a.id=?""",
                (asset_id,)).fetchone()

        rows = []
        for t in techs:
            van = [r["sku"] for r in c.execute(
                "SELECT sku FROM stock WHERE location_id=? AND on_hand>0",
                (t["van_location"],))] if t["van_location"] else []
            row = {"id": t["id"], "name": t["name"], "home_base": t["home_base"],
                   "van_stock": van}
            if site and site["lat"] is not None and t["lat"] is not None:
                d = miles(site["lat"], site["lon"], t["lat"], t["lon"])
                row["distance_mi"] = d
                row["drive_minutes"] = drive_minutes(d)
            rows.append(row)

    rows.sort(key=lambda r: r.get("drive_minutes", 999))
    return {"family": family, "site": site["label"] if site else None,
            "technicians": rows,
            "note": "none qualified on that equipment" if not rows else ""}


# --------------------------------------------------------------------------
# the catalogue and the commercial side
# --------------------------------------------------------------------------

def lookup_product(query: str, tool_context: ToolContext | None = None) -> dict:
    """Answer a question about a part or a machine from what we actually sell.

    Args:
        query: a part number, a model number, or a description.
    """
    dealer = _dealer(tool_context)
    q = (query or "").lower().strip()
    if not q:
        return {"parts": [], "models": []}

    with db.connect() as c:
        parts = [{
            "sku": r["sku"], "name": r["name"], "unit_cost": r["unit_cost"],
            "available_now": c.execute(
                "SELECT COALESCE(SUM(free),0) f FROM stock_available WHERE sku=?",
                (r["sku"],)).fetchone()["f"],
            "lead_time_days": r["lead_time_days"],
        } for r in c.execute(
            """SELECT sku,name,unit_cost,lead_time_days FROM parts
               WHERE dealer_id=? AND (LOWER(sku) LIKE ? OR LOWER(name) LIKE ?)
               LIMIT 8""", (dealer, f"%{q}%", f"%{q}%"))]

        models = [f"{r['manufacturer']} {r['model_number']} ({r['family']})"
                  for r in c.execute(
            """SELECT DISTINCT a.manufacturer, a.model_number, a.family
               FROM assets a JOIN sites s ON s.id=a.site_id
               JOIN accounts ac ON ac.id=s.account_id
               WHERE ac.dealer_id=? AND (LOWER(a.model_number) LIKE ?
                     OR LOWER(a.manufacturer) LIKE ? OR LOWER(a.family) LIKE ?)
               LIMIT 8""", (dealer, f"%{q}%", f"%{q}%", f"%{q}%"))]

    return {"parts": parts, "models": models,
            "note": "what this dealer sells and what their customers own. "
                    "Anything not listed here is not known."}


def current_deals(about: str = "", tool_context: ToolContext | None = None) -> dict:
    """What this dealer is actually running right now.

    Only live, dated promotions the owner put on the record through the
    console. There is nothing here to invent: an offer that is not on this
    list does not exist.

    Args:
        about: optional filter, e.g. "defrost", "fan motor".
    """
    dealer = _dealer(tool_context)
    today = datetime.now().date().isoformat()
    with db.connect() as c:
        rows = c.execute(
            """SELECT pr.id, pr.headline, pr.detail, pr.ends, pr.terms,
                      GROUP_CONCAT(p.name, ', ') parts
               FROM promotions pr
               LEFT JOIN promotion_parts pp ON pp.promotion_id=pr.id
               LEFT JOIN parts p ON p.sku=pp.sku
               WHERE pr.dealer_id=? AND pr.ends >= ?
               GROUP BY pr.id ORDER BY pr.ends""",
            (dealer, today)).fetchall()

    deals = [{"headline": r["headline"], "detail": r["detail"],
              "ends": r["ends"], "terms": r["terms"], "parts": r["parts"]}
             for r in rows]
    if about.strip():
        q = about.lower()
        filtered = [d for d in deals
                    if q in f"{d['headline']} {d['detail'] or ''} {d['parts'] or ''}".lower()]
        deals = filtered or deals

    return {"as_of": today, "deals": deals,
            "note": "these are the only live offers. Do not describe any other."}


def log_supplier_offer(company: str, contact: str, offering: str,
                       tool_context: ToolContext,
                       price_quoted: str = "", lead_time: str = "") -> dict:
    """Record an inbound sales call from a vendor, for the buyer to review.

    Suppliers ring service dealers constantly. The call is worth taking and
    almost never worth acting on immediately, so this captures it accurately
    and commits to nothing.

    Args:
        company: the vendor's company name.
        contact: their name and callback number as given.
        offering: what they are selling, in their words.
        price_quoted: any price they stated.
        lead_time: any lead time they stated.
    """
    dealer = _dealer(tool_context)
    offer_id = _nid("OFF")
    with db.txn() as c:
        # "Midway Parts" and "Midway Parts Co" are the same vendor. Matching on
        # a normalised name stops a new supplier row appearing every time a rep
        # introduces themselves slightly differently.
        stem = _company_stem(company)
        sup = next((r for r in c.execute(
            "SELECT id, name FROM suppliers WHERE dealer_id=?", (dealer,))
            if _company_stem(r["name"]) == stem), None)
        if sup is None:
            sup_id = _nid("SUP")
            c.execute("INSERT INTO suppliers (id,name,contact,dealer_id) VALUES (?,?,?,?)",
                      (sup_id, company, contact, dealer))
        else:
            sup_id = sup["id"]
        c.execute("""INSERT INTO supplier_offers
                     (id,supplier_id,offering,price_quoted,lead_time,logged_at,
                      status,committed) VALUES (?,?,?,?,?,?,?,0)""",
                  (offer_id, sup_id, offering, price_quoted or None,
                   lead_time or None, datetime.now().isoformat(timespec="seconds"),
                   "for buyer review"))
    return {"ok": True, "offer_id": offer_id, "supplier": company,
            "committed": False,
            "told_caller": "passed to our buyer, no commitment made"}


# --------------------------------------------------------------------------
# the artifact
# --------------------------------------------------------------------------

def open_work_order(asset_id: str, reported_symptom: str,
                    tool_context: ToolContext, error_code: str = "") -> dict:
    """Open a job. Call this once the machine and the fault are established.

    Args:
        asset_id: the affected machine.
        reported_symptom: the customer's own words.
        error_code: displayed code if they read one out.
    """
    dealer = _dealer(tool_context)
    who = tool_context.state.get("caller") or {}

    with db.txn() as c:
        asset = c.execute(
            """SELECT a.id, a.site_id, s.account_id FROM assets a
               JOIN sites s ON s.id=a.site_id WHERE a.id=?""", (asset_id,)).fetchone()
        if asset is None:
            return {"ok": False, "why": "unknown machine"}

        wo = _nid("WO")
        c.execute("""INSERT INTO work_orders
            (id,account_id,site_id,asset_id,contact_id,reported_symptom,
             error_code,status,opened_at,dealer_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (wo, asset["account_id"], asset["site_id"], asset_id,
             who.get("contact_id"), reported_symptom, error_code or None,
             "open", datetime.now().isoformat(timespec="seconds"), dealer))

    try:
        from . import events
        events.publish(dealer, "work_order",
                       text=f"{wo} opened: {reported_symptom[:60]}")
    except Exception:
        pass
    return {"ok": True, "work_order_id": wo, "asset_id": asset_id}


def promise_slot(work_order_id: str, technician_id: str, starts_at: str,
                 skus_to_reserve: list[str], tool_context: ToolContext) -> dict:
    """Commit a visit to the customer and hold the parts behind it.

    The only place a promise is made, and it is refused rather than softened:
    if a part cannot be held or the slot has gone, nothing is committed. One
    transaction, so two concurrent calls cannot both be given the last one.

    Args:
        work_order_id: from open_work_order.
        technician_id: from find_technician or the scheduler.
        starts_at: ISO datetime from the scheduler, not one you invented.
        skus_to_reserve: parts that must be held for this visit.
    """
    from datetime import timedelta

    dealer = _dealer(tool_context)
    try:
        start = datetime.fromisoformat(starts_at)
    except ValueError:
        return {"ok": False, "why": "unreadable time, use one the scheduler gave you"}
    end = start + timedelta(minutes=120)
    now = datetime.now().isoformat(timespec="seconds")

    try:
      with db.txn() as c:
        wo = c.execute("SELECT id, asset_id, site_id FROM work_orders WHERE id=? AND dealer_id=?",
                       (work_order_id, dealer)).fetchone()
        if wo is None:
            return {"ok": False, "why": "unknown work order"}

        clash = c.execute(
            """SELECT id FROM appointments WHERE technician_id=?
               AND starts_at < ? AND ends_at > ?""",
            (technician_id, end.isoformat(timespec="minutes"),
             start.isoformat(timespec="minutes"))).fetchone()
        if clash:
            return {"ok": False, "why": "that slot was taken while we were talking",
                    "advice": "Get a fresh slot from the scheduler and offer that."}

        seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 n FROM visits WHERE work_order_id=?",
                        (work_order_id,)).fetchone()["n"]
        visit = _nid("V")
        c.execute("""INSERT INTO visits
                     (id,work_order_id,seq,technician_id,promised_window,promised_at)
                     VALUES (?,?,?,?,?,?)""",
                  (visit, work_order_id, seq, technician_id,
                   start.strftime("%A %d %B, %H:%M"), now))

        held = []
        for sku in (skus_to_reserve or []):
            loc = c.execute(
                """SELECT sa.location_id FROM stock_available sa
                   JOIN stock_locations sl ON sl.id=sa.location_id
                   WHERE sa.sku=? AND sa.free>0 AND sl.dealer_id=?
                   ORDER BY sl.kind='warehouse' DESC LIMIT 1""",
                (sku, dealer)).fetchone()
            if loc is None:
                # Raise rather than return: db.txn rolls the whole thing back,
                # so the visit row, the reservations taken so far and the
                # appointment all disappear together. Hand-unwinding each one
                # is how a half-written promise gets left in the database.
                raise _PartUnavailable(sku)
            c.execute("""INSERT INTO reservations (sku,location_id,visit_id,qty,reserved_at)
                         VALUES (?,?,?,1,?)""", (sku, loc["location_id"], visit, now))
            held.append((sku, loc["location_id"]))

        c.execute("""INSERT INTO appointments (id,technician_id,visit_id,starts_at,
                     ends_at,kind,site_id,note) VALUES (?,?,?,?,?,?,?,?)""",
                  (_nid("AP"), technician_id, visit,
                   start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"),
                   "visit", wo["site_id"], f"promised on a call for {work_order_id}"))
        c.execute("UPDATE work_orders SET status='scheduled' WHERE id=?", (work_order_id,))
    except _PartUnavailable as e:
        return {"ok": False, "why": f"{e.sku} is not available",
                "blocking_sku": e.sku,
                "advice": "Nothing was committed. Tell them the truth about "
                          "which part is short and offer the next honest option."}

    try:
        from . import events
        events.publish(dealer, "promise",
                       text=f"{work_order_id} promised {start.strftime('%A %H:%M')}, "
                            f"{len(held)} part(s) held")
    except Exception:
        pass
    return {"ok": True, "work_order_id": work_order_id, "visit_id": visit,
            "window": start.strftime("%A %d %B, %H:%M"),
            "technician_id": technician_id,
            "parts_reserved": [s for s, _ in held]}


def build_briefing(work_order_id: str, tool_context: ToolContext) -> dict:
    """Assemble what the technician receives before leaving.

    Not a parts list. The van contents are decided by weighing how likely each
    part is against what a wasted trip costs, from this dealer's own closed
    jobs, and anything already on the technician's van is free to bring.

    Args:
        work_order_id: the promised job.
    """
    from .reason import what_to_load

    dealer = _dealer(tool_context)
    with db.connect() as c:
        row = c.execute(
            """SELECT w.id, w.reported_symptom, w.error_code, w.asset_id,
                      v.id visit_id, v.promised_window, v.technician_id,
                      t.name tech_name, a.name customer, s.label site,
                      s.address, s.access_note,
                      ast.manufacturer, ast.model_number, ast.family,
                      ast.location_note,
                      e.refrigerant
               FROM work_orders w
               LEFT JOIN visits v ON v.work_order_id=w.id
               LEFT JOIN technicians t ON t.id=v.technician_id
               JOIN accounts a ON a.id=w.account_id
               JOIN sites s ON s.id=w.site_id
               LEFT JOIN assets ast ON ast.id=w.asset_id
               LEFT JOIN equipment e ON e.id=ast.equipment_id
               WHERE w.id=? AND w.dealer_id=?
               ORDER BY v.seq DESC LIMIT 1""",
            (work_order_id, dealer)).fetchone()
    if row is None:
        return {"ok": False, "why": "unknown work order"}

    decision = what_to_load(dealer, row["asset_id"], row["reported_symptom"],
                            row["technician_id"] or "")
    history = prior_repairs(row["asset_id"], row["reported_symptom"], tool_context)

    flammable = (row["refrigerant"] or "").upper() in {"R-290", "R290", "R-600A", "R600A"}

    brief = {
        "ok": True,
        "work_order_id": work_order_id,
        "visit_id": row["visit_id"],
        "technician": row["tech_name"],
        "window": row["promised_window"],
        "customer": row["customer"],
        "site": row["site"],
        "address": row["address"],
        "access_note": row["access_note"],
        "machine": f"{row['manufacturer']} {row['model_number']} ({row['family']})",
        "where_on_site": row["location_note"],
        "reported": row["reported_symptom"],
        "error_code": row["error_code"],
        "safety": (f"{row['refrigerant']} - flammable refrigerant, charge limited"
                   if flammable else None),
        "load_these": [{"sku": p["sku"], "name": p["name"],
                        "why": p["note"], "likelihood": p["probability"]}
                       for p in decision.get("load", [])],
        "left_behind": [{"name": p["name"], "why": p["note"]}
                        for p in decision.get("left_behind", [])],
        "likely_causes": decision.get("distribution", [])[:3],
        "reasoning": decision.get("reasoning"),
        "prior_visits_this_machine": history.get("this_unit", []),
        "same_model_elsewhere": history.get("same_model_elsewhere", []),
    }

    try:
        from . import events
        parts = ", ".join(p["name"] for p in brief["load_these"]) or "nothing to load"
        events.publish(dealer, "briefing",
                       text=f"{row['tech_name'] or 'technician'}: take {parts}")
    except Exception:
        pass

    # Hand it to whatever delivers messages. Until this existed the briefing
    # was computed in full and then returned to a caller who never sent it,
    # which meant the one thing this product is named for did not happen.
    #
    # Published rather than sent inline because the customer is still on the
    # line at this point, and an SMS API call on the conversational path is
    # dead air the caller hears. Off unless PRAEVISUM_BUS=1.
    try:
        from . import bus
        sent = bus.send_briefing(brief, dealer)
        if sent.get("published"):
            brief["dispatched"] = sent["message_id"]
    except Exception:
        pass

    return brief
