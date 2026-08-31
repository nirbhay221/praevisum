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

import re
import uuid
from datetime import datetime

from google.adk.tools import ToolContext

from . import db, dispatch
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

    # A SUB-AGENT HAS ITS OWN CONTEXT and does not see what route_to_vendor
    # wrote into the caller's session state. Without this it silently answers
    # about the wrong business.
    from .tenancy import routed

    return routed() or DEFAULT_DEALER


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

        # ROAD DISTANCE FOR THE WHOLE SHORTLIST IN ONE MATRIX.
        #
        # This is the dispatch tool the desk actually calls, so it is the main
        # place the river matters: engineers are based on both banks of the
        # Mississippi and a straight line across it is a bridge somebody has
        # to drive to. Falls back to the straight line when no key is set.
        legs = []
        if site and site["lat"] is not None:
            from .roads import legs_to
            legs = legs_to((site["lat"], site["lon"]),
                           [(t["lat"], t["lon"]) for t in techs])

        rows = []
        for i, t in enumerate(techs):
            van = [r["sku"] for r in c.execute(
                "SELECT sku FROM stock WHERE location_id=? AND on_hand>0",
                (t["van_location"],))] if t["van_location"] else []
            row = {"id": t["id"], "name": t["name"], "home_base": t["home_base"],
                   "van_stock": van}
            if legs and legs[i]["miles"] is not None:
                row["distance_mi"] = legs[i]["miles"]
                row["drive_minutes"] = legs[i]["minutes"]
                row["measured"] = legs[i]["source"]
            rows.append(row)

    rows.sort(key=lambda r: r.get("drive_minutes", 999))

    # WHAT THE CUSTOMER ASKED FOR, applied after skills and drive time and
    # after certification, never before. An exclusion removes somebody
    # outright; a preference only reorders what is left, because holding a job
    # for three days waiting for one engineer while a freezer is warm serves
    # nobody. See preference.py.
    removed, pref_say = [], ""
    try:
        from .preference import apply_to

        account_id = ""
        if asset_id:
            with db.connect() as c:
                acc = c.execute(
                    """SELECT s.account_id FROM assets a
                       JOIN sites s ON s.id = a.site_id WHERE a.id = ?""",
                    (asset_id,)).fetchone()
            account_id = acc["account_id"] if acc else ""
        if account_id:
            out = apply_to(rows, account_id)
            rows, removed, pref_say = (out["candidates"], out["removed"],
                                       out["say"])
    except Exception as e:
        print(f"[tools] could not apply crew preferences: "
              f"{type(e).__name__}: {e}", flush=True)

    return {"family": family, "site": site["label"] if site else None,
            "not_sent_at_their_request": removed,
            "about_the_crew": pref_say,
            "technicians": rows,
            "note": "none qualified on that equipment" if not rows else ""}


# --------------------------------------------------------------------------
# the catalogue and the commercial side
# --------------------------------------------------------------------------

def _norm_model(v: str) -> str:
    """A model number with the punctuation people never say out loud removed.

    HL-L2400DW, HL L2400DW and hll2400dw are one machine. Matching on the
    raw string treats them as three.
    """
    return re.sub(r"[^a-z0-9]", "", (v or "").lower())


def _query_tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[\s,/]+", q or "") if t]


# How much of a model number has to agree before it is worth mentioning as a
# near miss. Five characters keeps "HL-L2400D" against "HL-L2400DW" and
# rejects "E14" against "E1400", which share three and are different machines.
ENOUGH_OF_A_MODEL = 5


def _near_model_matches(c, dealer: str, query: str) -> list[dict]:
    """Machines whose model number ALMOST matches what was asked for.

    THE VARIANT SUFFIX PROBLEM

    Manufacturers distinguish variants by a trailing letter or two: HL-L2400D
    and HL-L2400DW are the same printer, the W being wireless. A caller reads
    the number off a box or a quote and says the long one; the shelf holds the
    short one; the LIKE finds nothing, because "%hl-l2400dw%" cannot match the
    shorter stored string. The desk then says we do not stock it while it is
    sitting on the floor.

    This finds those, in BOTH directions, and deliberately does not merge them
    into the exact results. A near match is a question to ask the customer, not
    an answer to give them: the wireless one and the non-wireless one are
    different prices and only one of them does what they want.
    """
    asked = [_norm_model(t) for t in _query_tokens(query)
             if any(ch.isdigit() for ch in t) and len(t) >= 4]
    if not asked:
        return []

    out = []
    rows = c.execute(
        """SELECT manufacturer, model_number, family, list_price,
                  on_hand, on_order
           FROM product_stock WHERE dealer_id=?""", (dealer,)).fetchall()

    for r in rows:
        stored = _norm_model(r["model_number"])
        if not stored:
            continue
        for a in asked:
            if stored == a:
                continue                      # the exact search already has it
            short, long_ = sorted((stored, a), key=len)
            if len(short) < ENOUGH_OF_A_MODEL or not long_.startswith(short):
                continue
            extra = long_[len(short):]
            out.append({
                "manufacturer": r["manufacturer"],
                "model_number": r["model_number"],
                "family": r["family"],
                "list_price": r["list_price"],
                "on_hand": r["on_hand"],
                "on_order": r["on_order"],
                "differs_by": extra.upper(),
                "we_hold_the": ("shorter" if stored == short else "longer")
                               + " variant",
            })
            break
    return out[:5]


def lookup_product(query: str, tool_context: ToolContext | None = None) -> dict:
    """Answer a question about a part or a machine from what we actually sell.

    IT DID NOT LOOK AT WHAT WE SELL.

    It searched `parts`, which is the spare parts bin, and `assets`, which is
    other customers' equipment. The one table holding the machines this
    business has for sale, `product_stock`, was never queried, while the note
    it returned said "what this dealer sells". On a live call that produced,
    in one conversation:

        "I'm not finding any ASUS laptops in our system at the moment."
        "I don't see any Dell laptops in our system either."
        "I don't see any Lenovo IdeaPad laptops in our system."

    Four ASUS laptops were on the shelf the whole time. Worse, `supply` was
    asked for one of them a minute later and found it immediately, because
    supply.py reads product_stock. So the desk told the same caller that the
    same machine both did not exist and was in stock and deliverable today.

    A caller asking "do you have" means the shop floor. That is what this
    answers first now, and what is on order counts too: a machine arriving on
    Thursday is a better answer than silence.

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

        # THE SHOP FLOOR. Matched on manufacturer, model or family, because a
        # caller says "ASUS laptop" and means all three at once.
        stock = [{
            "ref": f"STK-{r['rowid']}",
            "manufacturer": r["manufacturer"],
            "model_number": r["model_number"],
            "family": r["family"],
            "list_price": r["list_price"],
            "on_hand": r["on_hand"],
            "on_order": r["on_order"],
        } for r in c.execute(
            """SELECT rowid, manufacturer, model_number, family, list_price,
                      on_hand, on_order
               FROM product_stock
               WHERE dealer_id=? AND (LOWER(manufacturer) LIKE ?
                     OR LOWER(model_number) LIKE ? OR LOWER(family) LIKE ?
                     OR LOWER(manufacturer || ' ' || model_number) LIKE ?
                     OR LOWER(manufacturer || ' ' || family) LIKE ?)
               ORDER BY on_hand DESC, list_price DESC
               LIMIT 8""",
            (dealer, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))]

        # EVERY WORD HAS TO LAND SOMEWHERE, rather than the whole sentence
        # having to appear in one column. "ASUS Vivobook laptop" is three
        # facts about one machine, and as a single LIKE it matched nothing
        # because no column contains all three.
        toks = [t.lower() for t in _query_tokens(q) if len(t) > 1]
        if not stock and len(toks) > 1:
            clause = " AND ".join(
                ["(LOWER(manufacturer) LIKE ? OR LOWER(model_number) LIKE ? "
                 "OR LOWER(family) LIKE ?)"] * len(toks))
            args: list = [dealer]
            for t in toks:
                args += [f"%{t}%"] * 3
            stock = [{
                "manufacturer": r["manufacturer"],
                "model_number": r["model_number"],
                "family": r["family"],
                "list_price": r["list_price"],
                "on_hand": r["on_hand"],
                "on_order": r["on_order"],
            } for r in c.execute(
                f"""SELECT manufacturer, model_number, family, list_price,
                           on_hand, on_order
                    FROM product_stock
                    WHERE dealer_id=? AND {clause}
                    ORDER BY on_hand DESC, list_price DESC
                    LIMIT 8""", args)]

        close = _near_model_matches(c, dealer, q) if not stock else []

    if close:
        return {
            "parts": parts, "models": models, "stock": [],
            "in_stock_now": [], "close_matches": close,
            "note": "NOT what they asked for. The model numbers differ by "
                    + ", ".join(sorted({m["differs_by"] for m in close}))
                    + ", which on most makers means a different variant: "
                      "wireless, a different voltage, a different door. Say "
                      "the exact model we hold, say how it differs from the "
                      "one they named, and ASK whether that is the one they "
                      "want. Do not book it as the same machine.",
        }

    return {"parts": parts, "models": models, "stock": stock,
            "in_stock_now": [m for m in stock if (m["on_hand"] or 0) > 0],
            "note": "stock is DEAREST FIRST, because somebody asking what we "
                    "have wants to know what we can do, not what is cheapest. "
                    "It was the other way round and a caller with two thousand "
                    "dollars was offered a $365 machine and told it was the "
                    "best we had, while a $1,649 one sat in stock. If they "
                    "gave you a budget, call options_under instead. "
                    "stock is what we have to sell, models is what our "
                    "customers already own, parts is the spare parts bin. "
                    "If stock is empty we do not hold that today, which is "
                    "NOT the same as not selling it: say we can source it "
                    "and ask if they want it ordered.",
            "say": ("Answer from stock. Do not say we do not sell something "
                    "merely because it is not on the shelf today.")}


def current_deals(about: str = "", tool_context: ToolContext | None = None) -> dict:
    """What this dealer is actually running right now.

    Only live, dated promotions the owner put on the record through the
    console. There is nothing here to invent: an offer that is not on this
    list does not exist.

    AND NOT ONE THAT HAS NOT STARTED YET. This checked `ends` and never
    `starts`, so an offer the owner scheduled for next week was read out to
    callers today. offers.py already had the start check and this did not, so
    the same promotion was live in a quote and not yet live in the catalogue,
    which is the worst kind of disagreement: both halves confident.

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
                 AND (pr.starts IS NULL OR pr.starts <= ?)
               GROUP BY pr.id ORDER BY pr.ends""",
            (dealer, today, today)).fetchall()

    deals = [{"headline": r["headline"], "detail": r["detail"],
              "ends": r["ends"], "terms": r["terms"], "parts": r["parts"]}
             for r in rows]
    if about.strip():
        q = about.lower()
        filtered = [d for d in deals
                    if q in f"{d['headline']} {d['detail'] or ''} {d['parts'] or ''}".lower()]
        deals = filtered or deals

    # WHO IS ASKING. Two of these offers say "trade accounts only" and nothing
    # read that line: every caller was told about every live promotion,
    # including the ones they cannot have. Reading somebody an offer and then
    # withdrawing it at the counter is the same failure as quoting zero on a
    # warranty nobody has verified, and it is the more annoying of the two
    # because they came in for it.
    tier = _caller_tier(dealer)
    for d in deals:
        d["eligible"], d["why_not"] = _qualifies(d.get("terms") or "", tier)

    open_to_them = [d for d in deals if d["eligible"]]
    restricted = [d for d in deals if not d["eligible"]]

    return {
        "as_of": today,
        "deals": open_to_them,
        "not_open_to_them": restricted,
        "customer": tier,
        "note": ("These are the only live offers. Do not describe any other, "
                 "and do not read out anything under not_open_to_them as "
                 "though they could have it. If one of those is genuinely "
                 "worth their while, say what it would take to qualify rather "
                 "than dangling it: 'that one is for trade accounts, and "
                 "opening one takes a couple of minutes' is useful. Naming an "
                 "offer they cannot have and moving on is not."),
    }


# Terms that mean an offer is only for customers who buy on account. Matched
# on the promotion's own terms text, which is what the owner typed into the
# console, so this reads their words rather than requiring a new column.
_TRADE_ONLY = ("trade account", "trade only", "account holders", "on account")


def _caller_tier(dealer_id: str) -> str:
    """What the person on this call is to us, if we know yet."""
    from .standing import standing
    from .trace import CALL

    call_id = CALL.get()
    if not call_id:
        return "unknown"
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT ct.account_id FROM calls cl
                   JOIN contacts ct ON ct.id = cl.contact_id
                   WHERE cl.id = ?""", (call_id,)).fetchone()
    except Exception:
        return "unknown"
    if row is None or not row["account_id"]:
        return "unknown"
    return standing(row["account_id"], dealer_id)["tier"]


def _qualifies(terms: str, tier: str) -> tuple[bool, str]:
    """Can this customer actually have this offer.

    An unknown caller is treated as eligible. We have not established who they
    are yet, and withholding an offer from somebody who turns out to hold an
    account is worse than mentioning one they later cannot use: the first is a
    lost sale we caused, the second is a conversation.
    """
    low = (terms or "").lower()
    if not any(w in low for w in _TRADE_ONLY):
        return True, ""
    if tier in ("on_account", "unknown"):
        return True, ""
    return False, ("this one is for trade accounts and they do not have one "
                   "with us yet")


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

def _call_row_if_it_exists(c, tool_context) -> str | None:
    """The call id, but only when a row for it actually exists.

    `opened_from_call` carries a foreign key to `calls`. Writing the id
    blindly turns a missing call row into an IntegrityError THAT CANCELS THE
    JOB, which is a far worse failure than the empty column this was added to
    fix: the customer is on the phone and the work order silently does not
    happen.

    A call id in session state with no row behind it is not hypothetical. The
    row is written when the line connects, and anything that opens a job
    outside a live call, or after a session was restored, has the id without
    the row.
    """
    cid = (tool_context.state.get("call_id") or "").strip() if tool_context else ""
    if not cid:
        return None
    try:
        return cid if c.execute("SELECT 1 FROM calls WHERE id = ?",
                                (cid,)).fetchone() else None
    except Exception:
        return None



def _who_can_service(asset_id: str) -> str:
    """Which company should own a job on this machine.

    NOT WHICHEVER ONE THE CALL IS ROUTED TO, AND NOT THE ACCOUNT'S.

    An account belongs to whoever the customer first rang. A bakery on the
    refrigeration book buys a laptop, reports a fault, and the job was filed
    under REFRIGERATION -- who sell no laptops and employ nobody qualified on
    one. The scheduler then offered an IT engineer, correctly, and the booking
    refused them for belonging to the wrong company. The visit could not be
    made at all.

    A job belongs to whoever can send somebody: the company whose engineers
    are qualified on that family. Falls through to the caller's own choice
    when that is not exactly one company, because guessing between two is how
    a job lands on a book that cannot serve it.
    """
    from . import db

    try:
        with db.connect() as c:
            row = c.execute(
                "SELECT family FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None or not (row["family"] or "").strip():
                return ""
            owners = [r[0] for r in c.execute(
                """SELECT DISTINCT t.dealer_id FROM technicians t
                   JOIN technician_skills k ON k.technician_id = t.id
                   WHERE k.family = ? AND t.active = 1""", (row["family"],))]
    except Exception:
        return ""

    return owners[0] if len(owners) == 1 else ""


def open_work_order(asset_id: str, reported_symptom: str,
                    tool_context: ToolContext, error_code: str = "") -> dict:
    """Open a job. Call this once the machine and the fault are established.

    Args:
        asset_id: the affected machine.
        reported_symptom: the customer's own words.
        error_code: displayed code if they read one out.
    """
    dealer = _dealer(tool_context)
    dealer = _who_can_service(asset_id) or dealer
    who = tool_context.state.get("caller") or {}

    with db.txn() as c:
        asset = c.execute(
            """SELECT a.id, a.site_id, s.account_id FROM assets a
               JOIN sites s ON s.id=a.site_id WHERE a.id=?""", (asset_id,)).fetchone()
        if asset is None:
            return {"ok": False, "why": "unknown machine"}

        wo = _nid("WO")
        # WHICH CALL OPENED THIS. The column has existed since the first
        # schema and was NULL on all 673 jobs, because this insert never
        # carried it while `call_id` sat in session state the whole time.
        #
        # calibration.py joins work_orders to decisions ON THIS COLUMN to ask
        # whether a 44% prediction is right 44% of the time. With it empty the
        # join matches nothing, so that screen reported "no prediction has yet
        # been followed by a technician saying what it really was" and always
        # would have, however many jobs were finished. Honest about being
        # empty, and structurally unable to stop being empty.
        c.execute("""INSERT INTO work_orders
            (id,account_id,site_id,asset_id,contact_id,reported_symptom,
             error_code,status,opened_at,dealer_id,opened_from_call)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (wo, asset["account_id"], asset["site_id"], asset_id,
             who.get("contact_id"), reported_symptom, error_code or None,
             "open", datetime.now().isoformat(timespec="seconds"), dealer,
             _call_row_if_it_exists(c, tool_context)))

    from . import events
    events.publish(dealer, "work_order",
                   text=f"{wo} opened: {reported_symptom[:60]}")
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
        # THE JOB'S OWN COMPANY, NOT WHICHEVER ONE THIS CALL IS ROUTED TO.
        #
        # An account belongs to whoever the customer first rang; a machine
        # belongs to whoever sells that kind. A bakery on the refrigeration
        # book buys a laptop, and the work order is filed under refrigeration
        # while the call is routed to IT. This looked for the job under the
        # ROUTED company, did not find it, and returned "unknown work order"
        # for a job opened ninety seconds earlier on the same call.
        #
        # The engineer is then checked against the job's company rather than
        # the call's, which is the check that actually matters: the person
        # going has to work for whoever owns the job.
        wo = c.execute(
            "SELECT id, asset_id, site_id, dealer_id FROM work_orders WHERE id=?",
            (work_order_id,)).fetchone()
        if wo is None:
            return {"ok": False, "why": "unknown work order"}
        dealer = wo["dealer_id"] or dealer

        # WHO IS ACTUALLY GOING.
        #
        # This went straight to the INSERT and let the foreign key decide. On
        # a live call the desk passed technician_id="14" -- it had just read
        # two engineers out by NAME and invented a number for one of them --
        # and the insert raised IntegrityError. The exception killed the turn,
        # and the model, having lost the failure, told the customer
        #
        #     "Great, I have scheduled Roy Nyquist to arrive today between
        #      1:58 PM and 3:58 PM"
        #
        # There was no appointment. A crash that becomes a promise is worse
        # than a crash, because the customer waits in.
        who = c.execute("SELECT id, name FROM technicians WHERE id=? AND dealer_id=?",
                        (technician_id, dealer)).fetchone()
        if who is None:
            free = [dict(r) for r in c.execute(
                "SELECT id, name FROM technicians WHERE dealer_id=? AND active=1 "
                "ORDER BY name LIMIT 6", (dealer,))]
            return {"ok": False,
                    "why": f"{technician_id!r} is not one of our engineers",
                    "our_engineers": free,
                    "say": "Do NOT tell them it is booked: it is not. Get a "
                           "fresh slot from the scheduler and use the "
                           "technician_id it gives you, exactly as given."}

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

    from . import events
    events.publish(dealer, "promise",
                   text=f"{work_order_id} promised {start.strftime('%A %H:%M')}, "
                        f"{len(held)} part(s) held")
    return {"ok": True, "work_order_id": work_order_id, "visit_id": visit,
            "window": start.strftime("%A %d %B, %H:%M"),
            "technician_id": technician_id,
            "parts_reserved": [s for s, _ in held]}



def _what_they_photographed(work_order_id: str) -> list[dict]:
    """Photos the customer sent about this job. Never raises."""
    try:
        from . import job_photos

        return job_photos.for_the_engineer(work_order_id)
    except Exception as e:
        print(f"[tools] could not read the photos for {work_order_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        return []


def _email_the_brief(brief: dict) -> dict:
    """Send the finished briefing to whoever is going.

    `brief_the_engineer` was written, tested and CALLED BY NOTHING. So the
    briefing -- the parts to load, the fault history, what the customer sent
    in, the safety note -- was assembled on every job and existed only inside
    a tool result. The person driving to the site never saw it.

    PRAEVISUM_BRIEF_TO overrides the destination. The technicians on this book
    carry no email address, and a briefing nobody receives is the thing this
    function exists to stop.
    """
    import os

    to = (os.getenv("PRAEVISUM_BRIEF_TO", "") or "").strip()
    if not to:
        return {"ok": False, "why": "no address to send the briefing to"}
    try:
        from .email_out import brief_the_engineer, configured

        if not configured():
            return {"ok": False, "why": "no mail server on this deployment"}
        out = brief_the_engineer(to, brief)
        print(f"[tools] briefing for {brief.get('work_order_id')} emailed to "
              f"{to}: {out.get('ok')}", flush=True)
        return out
    except Exception as e:
        print(f"[tools] could not email the briefing: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": f"{type(e).__name__}"}


def _write_down_the_advice(visit_id: str, load_these: list[dict]) -> list[dict]:
    """Keep what we told them to take, and hand it straight back.

    THE ADVICE ONLY EVER EXISTED INSIDE THE MESSAGE THAT CARRIED IT.

    This list is worked out from the fault history on every briefing and was
    never written anywhere. So the one number field service actually runs on
    -- did the engineer have the part -- could not be computed: we could say
    what fixed a freezer and could not say that we had sent somebody out with
    four parts and they fitted one, four times running.

    Written here rather than in a caller, because this is the only place the
    recommendation exists as data before it becomes prose.
    """
    try:
        from .service_loop import we_advised

        we_advised(visit_id, load_these)
    except Exception as e:
        print(f"[tools] could not record the advice for {visit_id}: "
              f"{type(e).__name__}: {e}", flush=True)
    return load_these


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
                      t.name tech_name, t.email tech_email,
                      a.name customer, s.label site,
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
        # WHAT THE CUSTOMER SENT IN. This is the reason the desk asks for a
        # photo at all: it decides which part goes on the van. It was being
        # read for a rating plate and then dropped, so the engineer arrived
        # knowing the model number and nothing about what they were walking
        # into.
        "they_sent_us": _what_they_photographed(work_order_id),
        "error_code": row["error_code"],
        "safety": (f"{row['refrigerant']} - flammable refrigerant, charge limited"
                   if flammable else None),
        "load_these": _write_down_the_advice(
            row["visit_id"],
            [{"sku": p["sku"], "name": p["name"],
              "why": p["note"], "likelihood": p["probability"]}
             for p in decision.get("load", [])]),
        "left_behind": [{"name": p["name"], "why": p["note"]}
                        for p in decision.get("left_behind", [])],
        "likely_causes": decision.get("distribution", [])[:3],
        "reasoning": decision.get("reasoning"),
        # The one thing this whole project opens with: a part that was worked
        # out, held, and then never confirmed onto the van. A reservation is a
        # claim on stock, not a fact about a vehicle.
        "confirm_parts": dispatch.ask_line(row["visit_id"]),
        "prior_visits_this_machine": history.get("this_unit", []),
        "same_model_elsewhere": history.get("same_model_elsewhere", []),
    }

    # WHAT THE TRADE KNOWS, WHICH THE ENGINEER COULD ONLY GET BY ASKING.
    #
    # `remote_fixes` holds first-line procedures for this kind of machine and
    # the only path to them was `should_send_someone`, which is the check that
    # runs BEFORE anybody is dispatched and is aimed at the customer. So the
    # knowledge existed, and the one person qualified to act on it, standing in
    # front of the open machine, was the one person never shown it.
    #
    # Deliberately last in the briefing and deliberately labelled. Our own
    # repair history is evidence about THIS model from THIS company; a general
    # trade check is not, and an engineer choosing what to do next is entitled
    # to know which is which. Same separation reviews.py draws between what we
    # know and what the world says.
    brief["general_checks"] = _trade_checks(
        dealer, row["family"], row["reported_symptom"])

    from . import events
    parts = ", ".join(p["name"] for p in brief["load_these"]) or "nothing to load"
    events.publish(dealer, "briefing",
                   text=f"{row['tech_name'] or 'technician'}: take {parts}")

    # Hand it to whatever delivers messages. Until this existed the briefing
    # was computed in full and then returned to a caller who never sent it,
    # which meant the one thing this product is named for did not happen.
    #
    # Published rather than sent inline because the customer is still on the
    # line at this point, and an SMS API call on the conversational path is
    # dead air the caller hears. Off unless PRAEVISUM_BUS=1.
    from . import bus
    sent = bus.send_briefing(brief, dealer)
    if sent.get("published"):
        brief["dispatched"] = sent["message_id"]

    # AND ACTUALLY SEND IT, which the bus was not doing.
    #
    # `PRAEVISUM_BUS` is unset on this deployment and on the VM, so
    # send_briefing published nowhere and the briefing was computed in full and
    # never reached the engineer. The one thing this product is named for did
    # not happen, quietly, because the switch that turns it on was off.
    #
    # Email rather than SMS because A2P 10DLC blocks US business SMS from this
    # number, and because a technician cannot share a phone number with a
    # customer: desk.py routes on exactly that.
    #
    # In a THREAD because the customer is still on the line here. An SMTP
    # round trip is a second or two, and a second of silence mid-sentence is
    # the dead air comfort.py exists to paper over. Fire and forget: the
    # briefing is already computed and returned, and a mail failure must not
    # take the call down.
    if row["tech_email"]:
        import threading

        def _post_it(to: str, payload: dict) -> None:
            try:
                from .email_out import brief_the_engineer

                out = brief_the_engineer(to, payload)
                if not out.get("ok"):
                    print(f"[briefing] not emailed to {to}: "
                          f"{out.get('why')}", flush=True)
            except Exception as e:
                print(f"[briefing] could not email {to}: "
                      f"{type(e).__name__}: {e}", flush=True)

        threading.Thread(target=_post_it, args=(row["tech_email"], dict(brief)),
                         daemon=True).start()
        brief["emailed_to"] = row["tech_email"]

    # AND IT ACTUALLY GOES TO THE ENGINEER.
    brief["emailed"] = _email_the_brief(brief)

    return brief


def sell_extended_cover(asset_id: str, extra_years: float,
                        price: float = 0.0, covers_labour: bool = False,
                        said: str = "", tool_context: ToolContext = None) -> dict:
    """Record that the customer bought extra warranty years on a machine.

    Call this when they SAY YES, not when you quote the option. Until this is
    called there is no record, and a fault eighteen months later is priced as
    if they had never bought it.

    Extends the manufacturer term rather than restarting it: two years on top
    of a six year term ends at eight years from installation, not two from
    today.

    Only set covers_labour when labour was genuinely included. Most extended
    cover in this trade is parts-only, and "five years cover" that turns out
    to exclude labour becomes an argument on a kitchen floor.

    Args:
        asset_id: the machine.
        extra_years: years on top of the manufacturer term.
        price: what they are paying.
        covers_labour: true only if labour was included.
        said: what they said when they agreed, in their words.
    """
    from .extended import sell_cover

    out = sell_cover(asset_id, extra_years, price,
                     covers_labour=covers_labour, sold_by=said)

    # NOTHING TO ATTACH IT TO, SO RAISE THE ORDER. IN CODE.
    #
    # HEARD ON A LIVE CALL, TWICE, AND THE SECOND TIME WAS MY DOING.
    #
    # First: this was called with a catalogue handle before any order existed,
    # failed, and the desk recovered by asking a customer to read a model
    # number off a projector still in our warehouse.
    #
    # So the failure was changed to return an instruction -- "raise the order
    # with the cover as a second line". The model read that instruction and
    # told the customer "I have placed your order, your total is $260.45."
    # NO ORDER WAS WRITTEN. It announced work it had not done, which is worse
    # than the bug it replaced.
    #
    # Handing a model a recovery step is not a recovery. Every time that has
    # been tried today the model has either ignored it or narrated it as done.
    # The order is raised here, by code, and what comes back is a fact.
    if out.get("why", "").startswith("no order has been raised") or             (out.get("then") and not out.get("ok")):
        state = getattr(tool_context, "state", {}) or {}
        account_id = state.get("account_id") or ""

        # NOT IN STATE, BUT WE KNOW WHO IS ON THE PHONE.
        #
        # The guard fills ids into a tool's ARGUMENTS, and this tool has no
        # account_id argument to fill, so state was empty and the fallback
        # gave up with "no account to raise one for" -- which the desk read
        # out to a customer as "I can't add the coverage, which machine is it
        # for?", about a laptop it had quoted twenty seconds earlier.
        #
        # The call row carries the number they rang from and the contact it
        # resolved to. That is the same route `ask_after_delivery` takes.
        if not account_id:
            try:
                from .trace import here

                call_id = here()
                if call_id:
                    row = c_row = None
                    with db.connect() as c:
                        row = c.execute(
                            """SELECT ct.account_id FROM calls cl
                               JOIN contacts ct ON ct.id = cl.contact_id
                               WHERE cl.id = ?""", (call_id,)).fetchone()
                        if row is None:
                            c_row = c.execute(
                                """SELECT ct.account_id FROM calls cl
                                   JOIN phones p ON p.e164 = cl.from_e164
                                   JOIN contacts ct ON ct.id = p.contact_id
                                   WHERE cl.id = ?""", (call_id,)).fetchone()
                    got = row or c_row
                    if got and got["account_id"]:
                        account_id = got["account_id"]
                        print(f"[tools] no account in state; taking "
                              f"{account_id} from the call", flush=True)
            except Exception as e:
                print(f"[tools] could not resolve the account from the call: "
                      f"{type(e).__name__}: {e}", flush=True)

        if not account_id:
            return {"ok": False,
                    "why": "no order to put cover on and no account to raise "
                           "one for",
                    "say": "Do NOT say anything is ordered and do NOT ask "
                           "them which machine: you have just quoted it. "
                           "Confirm who they are, then take the order with "
                           "the cover as a second line."}

        from .buying import create_purchase_order

        raised = create_purchase_order(account_id, out["then"])
        if not raised.get("ok"):
            return {"ok": False, "why": raised.get("why"),
                    "say": "Do NOT tell them it is ordered. " 
                           + (raised.get("say") or "")}
        return {
            "ok": True,
            "purchase_order": raised["purchase_order"],
            "subtotal": raised["subtotal"],
            "cover_included": True,
            "say": f"The order is raised as {raised['purchase_order']} with "
                   f"the cover on it, and the total is "
                   f"${raised['subtotal']:,.2f}. Read that total back and get "
                   "their yes before confirming it. It is a DRAFT until you "
                   "call confirm_purchase_order.",
        }

    return out


def they_agreed_we_may_call(said: str,
                            tool_context: ToolContext = None) -> dict:
    """Record that the caller said we may contact them, and what that permits.

    THE ASYMMETRY THIS FIXES. `take_us_off_your_list` was a live tool from the
    start, so somebody could opt OUT on a call. Nothing could record them
    opting IN: only a seed script wrote a consent row. A customer could say
    "yes, ring me if something comes up" and it went nowhere.

    WHAT ORAL CONSENT DOES AND DOES NOT BUY

    Recorded as oral, because that is what it is, with the call id as the
    evidence. Oral consent is real and it is enough for a SERVICE call.

    It is NOT enough for a marketing call, and this does not pretend
    otherwise. An AI voice counts as an artificial or prerecorded voice under
    the TCPA, and a marketing call using one needs prior express WRITTEN
    consent. So this row lets us ring them about their own equipment and
    still refuses to ring them about an offer, which is the correct outcome
    and the whole reason the two are stored separately.

    Do not ask for this. Record it when they offer it.

    Args:
        said: what they actually said, in their words, as the evidence.
    """
    from datetime import datetime

    if not tool_context:
        return {"ok": False, "why": "no call to attach this to"}

    who = tool_context.state.get("caller") or {}
    account_id = (who.get("account_id") or "").strip()
    call_id = (tool_context.state.get("call_id") or "").strip()
    if not account_id:
        return {"ok": False, "why": "nobody is identified on this call yet"}

    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO outreach_consent
                     (account_id,granted,granted_on,granted_via,consent_form,
                      evidence_ref)
                   VALUES (?,1,?,?,'oral',?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     granted=1, revoked_on=NULL,
                     granted_on=excluded.granted_on,
                     granted_via=excluded.granted_via,
                     consent_form=excluded.consent_form,
                     evidence_ref=excluded.evidence_ref""",
                (account_id, datetime.now().date().isoformat(),
                 f"said on a call: {said[:160]}", call_id or None))
    except Exception as e:
        return {"ok": False, "why": f"could not record it: {type(e).__name__}"}

    return {
        "ok": True, "account_id": account_id, "form": "oral",
        "evidence": call_id or "this call",
        "permits": ("nothing extra today. Safety calls never needed it, and "
                    "offers and predicted-failure calls need written consent, "
                    "which this is not"),
        "does_not_permit": "offers, or a call about a fault we can see coming",
        "why_record_it_then": ("it is their stated wish, it is what a written "
                              "consent form gets attached to later, and it is "
                              "evidence if they are ever asked"),
        "say": "Thank them and move on. Do not now offer them something: "
               "spoken consent does not cover a sales call, and treating it "
               "as though it does is the thing the rule exists to stop.",
    }


def they_answered_our_question(said: str,
                               tool_context: ToolContext = None) -> dict:
    """The caller is answering something we asked them, not starting fresh.

    THE HOLE THIS FILLS. A day after a visit the desk texts "is it still
    working?". If they reply BY TEXT, desk.py ties the answer back and acts on
    it. If they RING instead, which people do, nothing did: the answer was
    heard, the follow-up stayed open forever, and the one piece of feedback
    the database cannot produce for itself was thrown away.

    A yes is the only moment a review is worth asking for. A no is a second
    failure on the same job, which matters more than the first.

    Call this whenever they refer back to something we asked: "yes it is
    fine", "no it went off again last night", "that engineer sorted it".

    Args:
        said: their answer in their own words, not summarised.
    """
    phone = _caller_number(tool_context)
    if not phone:
        return {"ok": False,
                "why": "no number on this call to tie the answer to"}

    out = {"ok": True, "phone": phone}
    try:
        from .followup import record_reply

        out["tied_back"] = record_reply(phone, said)
    except Exception as e:
        out["tied_back"] = {"ok": False, "why": f"{type(e).__name__}"}
        print(f"[tools] could not tie a spoken answer back: {e}", flush=True)

    try:
        from .asking import after_they_said_it_held

        out["earned"] = after_they_said_it_held(phone, said)
    except Exception as e:
        out["earned"] = {"ok": False, "why": f"{type(e).__name__}"}
        print(f"[tools] could not act on a spoken answer: {e}", flush=True)

    return out


def customer_disputes_the_visit(work_order_id: str, customer_says: str,
                                technician_says: str = "",
                                tool_context: ToolContext = None) -> dict:
    """The customer describes a visit differently from the engineer.

    Record it, do not argue it. Both accounts are written down as given and a
    person settles it later. Arguing on the phone about whose version is right
    is how a repairable relationship becomes a lost account.

    Args:
        work_order_id: the job they are describing.
        customer_says: their account, in their words, not summarised.
        technician_says: what we already have from the engineer, if anything.
    """
    from .recovery import raise_dispute

    return raise_dispute(work_order_id, customer_says, technician_says)


def note_how_the_visit_went(work_order_id: str, still_working: bool = True,
                            on_time: bool = True, said: str = "",
                            tool_context: ToolContext = None) -> dict:
    """What the customer said about how a completed visit actually went.

    Attaches to whoever did the work. Without this a technician whose repairs
    come back twice as often as anybody else's looks identical to one whose
    never do.

    Args:
        work_order_id: the job.
        still_working: is the machine still fixed.
        on_time: did the engineer arrive when promised.
        said: their own words, kept rather than summarised.
    """
    from .recovery import record_workmanship

    return record_workmanship(work_order_id, still_working=still_working,
                              on_time=on_time, customer_said=said)


def confirm_delivery(order_id: str, condition: str = "ok",
                     said: str = "", tool_context: ToolContext = None) -> dict:
    """Close an order the customer has confirmed arrived properly.

    An order is not finished when the carrier drops it, it is finished when
    the person who paid says the right machine arrived in one piece. The
    carrier has already reported it; this is the customer agreeing.

    Refuses if no carrier has reported a delivery, because closing an order
    that may still be on a van is how somebody stops looking for it.

    Args:
        order_id: the order, e.g. PO-1234.
        condition: ok, damaged, wrong, or missing. Use their word.
        said: anything they said worth keeping, in their words.
    """
    from .delivery import close_order

    return close_order(order_id, confirmed_by=said or "the customer",
                       condition=condition, note=said)


def orders_on_the_way(tool_context: ToolContext = None) -> dict:
    """Orders that have shipped and are not confirmed as arrived yet.

    Answers "where is my order" from what the carrier actually reported,
    rather than from the promised date.
    """
    from .delivery import open_orders

    return open_orders(_dealer(tool_context))


def _caller_number(tool_context) -> str:
    """The number this call is coming from, whatever the session called it.

    THE BUG THIS ENDS. A live call seeds state as `caller_phone`. Two tools
    read `caller_e164` and `from_number`, and NEITHER of those is written
    anywhere in this codebase: they were only ever read.

    One of the two was `take_us_off_your_list`, whose own docstring calls
    "stop calling me" the one sentence no other rule may override. It could
    never find the number, so it answered "I could not tell which number you
    are calling from" every single time and recorded nothing. The most
    important rule in the system, defeated by a key name.

    Every spelling is accepted here so this cannot happen again by rename.
    """
    if tool_context is None:
        return ""
    st = getattr(tool_context, "state", None) or {}
    for key in ("caller_phone", "caller_e164", "from_number", "from_e164"):
        got = (st.get(key) or "").strip()
        if got:
            return got
    who = st.get("caller") or {}
    return (who.get("phone") or who.get("e164") or "").strip()


def take_us_off_your_list(reason: str = "",
                          tool_context: ToolContext | None = None) -> dict:
    """Record that this caller has asked never to be contacted again.

    WHY THIS IS A TOOL AND NOT AN APOLOGY

    "Stop calling me" is the one sentence in this system that no other rule may
    override, and until now the desk could only be sorry about it. Being sorry
    is not a record, and a request that leaves no record has not been honoured:
    the next sweep finds the same number and rings it again.

    The obligation is specific. An internal do-not-call request is separate
    from the federal registry, has to be honoured within ten business days,
    survives the end of any business relationship, and the record is kept for
    four years. So this writes a row that is never deleted, and every outbound
    path checks it before anything else, before the clock and before it will
    even pay to look a number up.

    Say it plainly and do not argue, do not offer a smaller frequency instead,
    and do not ask them to confirm. Somebody who has asked once has asked.

    Args:
        reason: what they said, if they gave one. Never required.
    """
    from . import linetype

    caller = _caller_number(tool_context)

    if not caller:
        return {"ok": False,
                "say": "I could not tell which number you are calling from, so "
                       "ask them to read it out and record it before you "
                       "promise them anything."}

    out = linetype.stop_calling(
        caller, asked_by="the caller", heard_on=_now_iso(), note=reason)

    return {**out,
            "say": "Tell them it is done and that it is permanent. Then stop "
                   "selling: this call is now only about whatever they "
                   "actually rang for."}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def offer_on_this(sku: str, tool_context: ToolContext | None = None) -> dict:
    """Any live offer covering a part, and what it does to the price.

    CALL THIS BEFORE QUOTING A PART, every time, without being asked.

    The owner records promotions through the console and `promotion_parts`
    maps them to exact SKUs. Until now nothing in any pricing path read that
    mapping, so a customer ringing about a door gasket was quoted the full
    $92.00 with a live 15% offer on gaskets sitting in the database, and the
    only way to get it was to already know to ask "what deals are on?".

    Eligibility is handled here, so a trade-only offer is never read out to
    somebody who cannot have it.

    Some offers cannot be turned into a price: a buy-three-pay-for-two depends
    on quantity and free labour is not a discount on a part. When `computed` is
    false, read the offer out in its own words and do NOT work out a new price
    from it.

    Args:
        sku: the part about to be quoted.
    """
    from . import offers

    dealer = _dealer(tool_context)
    return offers.offer_on(sku, dealer, _caller_tier(dealer))


def _trade_checks(dealer_id: str, family: str, symptom: str,
                  how_many: int = 3) -> list[dict]:
    """First-line procedures for this kind of machine, for the briefing.

    Not our own history. These are the checks the trade does before pulling
    anything apart, and they are worth carrying because the commonest causes
    are the free ones: a blocked condenser, a door not seating, a shelf against
    a vent. An engineer who drives out, pulls a panel and finds a grille packed
    with lint has spent an hour on something the briefing could have said.

    Returns nothing rather than something vague when no procedure matches. A
    briefing padded with generic advice trains people to stop reading it.
    """
    from .remote import _overlap

    with db.connect() as c:
        rows = c.execute(
            """SELECT symptom, check_first, instruction, safety_note, source_ref
               FROM remote_fixes
               WHERE (dealer_id IS NULL OR dealer_id = ?)
                 AND (family IS NULL OR family = ?)""",
            (dealer_id, family)).fetchall()

    scored = []
    for r in rows:
        score = _overlap(symptom or "", r["symptom"] or "")
        if score <= 0:
            continue
        scored.append((score, {
            "for": r["symptom"],
            "check": r["check_first"] or r["instruction"],
            "safety": r["safety_note"],
            "this_is": "a general trade check, not from our own jobs",
        }))

    scored.sort(key=lambda s: s[0], reverse=True)
    return [r for _, r in scored[:how_many]]


def note_how_it_will_be_used(family: str, hours_per_day: float = 0.0,
                             people_sharing: int = 0, where_it_goes: str = "",
                             tool_context: ToolContext | None = None) -> dict:
    """Record how a customer will actually use the thing they are buying.

    CALL THIS BEFORE QUOTING A CHAIR OR A SCREEN. The order will be refused
    until you have, and the refusal will say which question is missing.

    It is not paperwork. Two families cannot be recommended honestly without
    it, because the answer changes which product is correct and whether the
    warranty covers them at all:

      A CHAIR has a duty rating. One rated for a single shift fails in a 24
      hour dispatch office and the warranty is void for exceeding it, so
      "how many hours, and how many people" decides which chair is even
      allowed to be offered.

      A TELEVISION's consumer warranty EXCLUDES commercial and public display
      use. A set mounted in a dining room is uncovered from the day it goes
      up, and the customer does not find out until it fails. So "where is it
      going" decides between the consumer line and the commercial one.

    Ask it plainly and in their words. Do not read the warranty clause out.

    Args:
        family: what they are buying, e.g. "office chair", "television".
        hours_per_day: hours a day it will be in use.
        people_sharing: how many people will use it. 1 if it is one person's.
        where_it_goes: where it will live, and whether the public sees it.
    """
    from . import suitability

    fam = suitability.ALIASES.get((family or "").strip().lower(),
                                  (family or "").strip().lower())
    if not fam:
        return {"ok": False, "why": "say which product this is about"}

    answer = {"hours_per_day": hours_per_day or 0.0,
              "people_sharing": people_sharing or 0,
              "where_it_goes": (where_it_goes or "").strip()}

    if tool_context is not None:
        known = dict(tool_context.state.get("use_established") or {})
        known[fam] = answer
        tool_context.state["use_established"] = known

    # Kept as well as held in session state. Session state dies with the
    # process, and "they told us this in August" is exactly the sort of thing
    # that should not have to be asked twice on the next call.
    try:
        from .recall import _remember

        phone = _caller_number(tool_context)
        said = ", ".join(
            bit for bit in (
                f"{fam}: {hours_per_day} hours a day" if hours_per_day else "",
                f"{people_sharing} people sharing" if people_sharing else "",
                f"going in {answer['where_it_goes']}" if answer["where_it_goes"] else "",
            ) if bit)
        if phone and said:
            _remember(phone, said, _dealer(tool_context))
    except Exception as e:
        print(f"[tools] could not keep how it will be used: "
              f"{type(e).__name__}: {e}", flush=True)

    q = suitability.what_must_be_asked(fam)
    still = [f for f in (q.fields if q else ()) if not answer.get(f)]
    if still:
        return {"ok": False, "recorded": answer, "still_needed": still,
                "say": f"Still need {', '.join(still)} before this can be "
                       "quoted. Ask for it plainly."}

    return {"ok": True, "recorded": answer, "family": fam,
            "say": "Noted. Now recommend against what they actually told you, "
                   "not against price alone."}


def remember_who_they_want(technician_name: str, prefer_or_exclude: str,
                           because: str = "",
                           tool_context: ToolContext | None = None) -> dict:
    """Record that a customer wants, or does not want, a particular engineer.

    CALL THIS THE MOMENT THEY SAY IT. "Can you send the same chap as last
    time" and "please not him again" are the two commonest requests a service
    desk gets, and until now there was nowhere to put either.

    An exclusion is honoured absolutely: that person is never sent to them
    again. A preference is not a promise, because a freezer that is warm on
    Friday should not wait until Tuesday for one particular van, so say we
    will send them where we can rather than committing to it.

    Do not ask them to justify an exclusion and do not offer to investigate
    unless they ask. Somebody who has said it once has said it.

    Args:
        technician_name: who they mean, as they said it.
        prefer_or_exclude: "prefer" or "exclude".
        because: their reason, in their words, if they gave one.
    """
    from .preference import remember

    state = getattr(tool_context, "state", {}) or {}
    account_id = state.get("account_id") or ""
    if not account_id:
        return {"ok": False,
                "say": "I do not know whose account this is yet. Confirm who "
                       "they are first, then record it."}

    dealer = _dealer(tool_context)
    name = (technician_name or "").strip()
    with db.connect() as c:
        who = c.execute(
            """SELECT id, name FROM technicians
               WHERE dealer_id = ? AND LOWER(name) LIKE ?""",
            (dealer, f"%{name.lower()}%")).fetchall()

    if not who:
        return {"ok": False,
                "say": f"We have nobody called {name!r} on this desk. Ask "
                       "which visit they mean rather than guessing."}
    if len(who) > 1:
        return {"ok": False,
                "say": f"More than one engineer matches {name!r}. Ask which "
                       "one, by first and last name."}

    return remember(account_id, who[0]["id"],
                    (prefer_or_exclude or "").strip().lower(), because,
                    state.get("call_id") or "")
