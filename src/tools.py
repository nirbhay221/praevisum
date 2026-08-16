"""Tools. Plain functions - ADK turns these into FunctionTools from the
signature and docstring, so the docstrings are prompt surface, not decoration.

Deterministic code owns every decision with a consequence: what is in stock,
who is qualified, whether a slot can be promised. The model narrates and
decides *which* tool to reach for. It never decides whether a part exists.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from .domain import seed  # noqa: F401  (import loads the seed data)
from .domain.geo import drive_minutes, miles
from .domain.models import Repair, WorkOrder
from .domain.store import STORE
from .memory import INDEX


# --------------------------------------------------------------------------
# who is calling, and about what
# --------------------------------------------------------------------------

def identify_caller(phone: str) -> dict:
    """Look up a caller by phone number and list the equipment at their site.

    Args:
        phone: caller's number in E.164 form, e.g. "+13095550101".

    Returns:
        The customer record and every unit installed at their site, so the
        agent can ask "is this the reach-in again?" instead of "what is your
        account number".
    """
    customer = STORE.customer_by_phone(phone)
    if customer is None:
        return {"known": False, "phone": phone}
    units = STORE.units_for_customer(customer.id)
    return {
        "known": True,
        "customer_id": customer.id,
        "name": customer.name,
        "address": customer.address,
        "channel_pref": customer.channel_pref,
        "units": [
            {
                "serial": u.serial,
                "manufacturer": u.manufacturer,
                "model": u.model,
                "family": u.family,
                "installed": u.installed,
                "where": u.location_note,
            }
            for u in units
        ],
    }


# --------------------------------------------------------------------------
# THE DIFFERENTIATOR - history nobody currently hands to the technician
# --------------------------------------------------------------------------

def prior_repairs(serial: str, symptom: str = "") -> dict:
    """What this unit and this model have needed before, and what actually fixed it.

    Reads closed visits for the exact unit first, then widens to every unit of
    the same model across all sites. Reports parts *consumed* on those visits,
    not parts ordered - what the technician actually fitted is the signal.

    Args:
        serial: the unit's serial number.
        symptom: optional free-text complaint, used to bias which past visits matter.

    Returns:
        This unit's history, the model-wide history, and `commonly_needed` - 
        the parts that recur across visits for this fault. That last field is
        the whole reason this tool exists: 51% of failed first visits are
        caused by the technician not having the right part in the van.
    """
    unit = STORE.units.get(serial)
    if unit is None:
        return {"found": False, "serial": serial}

    this_unit = STORE.repairs_for_unit(serial)
    model_wide = [
        r for r in STORE.repairs_for_model(unit.manufacturer, unit.model)
        if r.serial != serial
    ]

    words = {w for w in symptom.lower().split() if len(w) > 3}

    def relevant(r) -> bool:
        if not words:
            return True
        hay = f"{r.reported_symptom} {r.found_cause}".lower()
        return any(w in hay for w in words)

    # Semantic recall across everything the company has ever closed. This is
    # what catches the case where the caller and the technician describe the
    # same fault in completely different words.
    semantic = [
        h for h in INDEX.search(symptom or unit.family, limit=6)
        if h.repair.serial != serial and h.score > 0.05
    ] if symptom else []

    pool = [r for r in this_unit + model_wide if relevant(r)] or (this_unit + model_wide)
    pool = pool + [h.repair for h in semantic if h.repair not in pool]

    # Semantic recall crosses manufacturers on purpose - a failure mode on one
    # brand is a real hint about another. Parts do not cross. A part is only
    # ever suggested if it physically fits THIS model, otherwise the briefing
    # sends a technician out with a box that will not go in the machine.
    fits = {p.sku for p in STORE.parts_fitting(unit.model)}

    counts = Counter(
        sku for r in pool for sku in r.parts_consumed if sku in fits
    )
    commonly_needed = [
        {
            "sku": sku,
            "name": STORE.parts[sku].name if sku in STORE.parts else sku,
            "seen_on_visits": n,
        }
        for sku, n in counts.most_common()
    ]

    def render(rs):
        return [
            {
                "date": r.closed_on,
                "reported": r.reported_symptom,
                "error_code": r.error_code,
                "found": r.found_cause,
                "parts_consumed": list(r.parts_consumed),
                "labor_hours": r.labor_hours,
            }
            for r in rs
        ]

    return {
        "found": True,
        "serial": serial,
        "manufacturer": unit.manufacturer,
        "model": unit.model,
        "this_unit": render(this_unit),
        "same_model_elsewhere": render(model_wide),
        "similar_faults_recalled": [
            {
                "date": h.repair.closed_on,
                "on": f"{h.repair.manufacturer} {h.repair.model}",
                "found": h.repair.found_cause,
                "parts_consumed": list(h.repair.parts_consumed),
                "score": h.score,
                "why": h.why,
            }
            for h in semantic
        ],
        "commonly_needed": commonly_needed,
        "corpus_size": INDEX.size(),
    }


# --------------------------------------------------------------------------
# parts
# --------------------------------------------------------------------------

def check_stock(skus: list[str]) -> dict:
    """Check live availability and lead time for specific part numbers.

    Availability is on-hand minus anything already claimed by another open
    work order, so two jobs can never be promised the same last part.

    Args:
        skus: part numbers to check.

    Returns:
        Per-SKU availability, and `can_complete_today` - true only if every
        requested part is physically available now.
    """
    rows = []
    for sku in skus:
        part = STORE.parts.get(sku)
        if part is None:
            rows.append({"sku": sku, "known": False})
            continue
        avail = STORE.available(sku)
        rows.append({
            "sku": sku,
            "known": True,
            "name": part.name,
            "available_now": avail,
            "lead_time_days": 0 if avail > 0 else part.lead_time_days,
            "unit_cost": part.unit_cost,
        })
    return {
        "parts": rows,
        "can_complete_today": all(r.get("available_now", 0) > 0 for r in rows) and bool(rows),
    }


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def find_technician(family: str, serial: str = "") -> dict:
    """Find technicians qualified on this equipment, nearest first.

    Dispatch is skills AND proximity. A qualified technician ninety minutes
    away is not a better answer than a qualified one fifteen minutes away, so
    results are ordered by drive time from the site.

    Args:
        family: e.g. "reach-in freezer", "walk-in cooler", "ice machine".
        serial: the unit being serviced, used to locate the site.

    Returns:
        Qualified technicians ordered by drive time, with what is already in
        each van so the briefing can say "bring X" instead of listing parts the
        technician is already carrying.
    """
    qualified = [t for t in STORE.technicians.values() if family in t.skills]

    site = None
    unit = STORE.units.get(serial)
    if unit is not None:
        site = STORE.customers.get(unit.customer_id)

    rows = []
    for t in qualified:
        row = {
            "id": t.id,
            "name": t.name,
            "home_base": t.home_base,
            "van_stock": list(t.van_stock),
        }
        if site is not None:
            d = miles(site.lat, site.lon, t.lat, t.lon)
            row["distance_mi"] = d
            row["drive_minutes"] = drive_minutes(d)
        rows.append(row)

    rows.sort(key=lambda r: r.get("drive_minutes", 999))
    return {
        "family": family,
        "site": site.name if site else None,
        "technicians": rows,
    }


# --------------------------------------------------------------------------
# the artifact
# --------------------------------------------------------------------------

def open_work_order(customer_id: str, serial: str, reported_symptom: str,
                    error_code: str = "") -> dict:
    """Open a work order. Call this once the fault and the unit are established.

    Args:
        customer_id: from identify_caller.
        serial: the affected unit.
        reported_symptom: the customer's own words.
        error_code: displayed error code if the customer read one out.

    Returns:
        The new work order id.
    """
    wo = WorkOrder(
        id=f"WO-{uuid.uuid4().hex[:6].upper()}",
        customer_id=customer_id,
        serial=serial,
        reported_symptom=reported_symptom,
        error_code=error_code or None,
    )
    wo.log("opened on call")
    STORE.work_orders[wo.id] = wo
    return {"work_order_id": wo.id}


def promise_slot(work_order_id: str, technician_id: str, window: str,
                 skus_to_reserve: list[str]) -> dict:
    """Commit a visit window to the customer and reserve the parts behind it.

    This is the only place a promise is made, and it is refused rather than
    softened: if a part cannot be reserved, the slot is not promised. Every
    reservation is what the commitment keeper later watches.

    Args:
        work_order_id: from open_work_order.
        technician_id: from find_technician.
        window: human-readable, e.g. "Thursday 2-4pm".
        skus_to_reserve: parts that must be held for this job.

    Returns:
        Whether the promise was made, and if not, exactly which part blocked it.
    """
    wo = STORE.work_orders.get(work_order_id)
    if wo is None:
        return {"promised": False, "reason": "unknown work order"}

    taken = []
    for sku in skus_to_reserve:
        if STORE.reserve(sku, work_order_id):
            taken.append(sku)
        else:
            for t in taken:
                STORE.release(t)
            wo.log(f"promise refused, {sku} unavailable")
            return {
                "promised": False,
                "reason": f"{sku} is not available",
                "blocking_sku": sku,
            }

    wo.technician_id = technician_id
    wo.promised_window = window
    wo.parts_reserved = taken
    wo.status = "promised"
    wo.log(f"promised {window} to {technician_id} holding {taken}")
    return {"promised": True, "work_order_id": wo.id, "window": window,
            "technician_id": technician_id, "parts_reserved": taken}


def build_briefing(work_order_id: str) -> dict:
    """Assemble what the technician receives before leaving.

    Args:
        work_order_id: the promised work order.

    Returns:
        The briefing: unit, history, likely cause, and the parts to load - 
        excluding anything already in that technician's van.
    """
    wo = STORE.work_orders.get(work_order_id)
    if wo is None:
        return {"ok": False, "reason": "unknown work order"}

    unit = STORE.units[wo.serial]
    history = prior_repairs(wo.serial, wo.reported_symptom)
    tech = STORE.technicians.get(wo.technician_id or "")
    in_van = set(tech.van_stock) if tech else set()

    to_load = [
        p for p in history.get("commonly_needed", [])
        if p["sku"] not in in_van
    ]

    wo.status = "briefed"
    wo.log("briefing built")

    return {
        "ok": True,
        "work_order_id": wo.id,
        "technician": tech.name if tech else None,
        "window": wo.promised_window,
        "site": STORE.customers[wo.customer_id].name,
        "unit": f"{unit.manufacturer} {unit.model} ({unit.family}), {unit.location_note}",
        "serial": unit.serial,
        "reported": wo.reported_symptom,
        "error_code": wo.error_code,
        "prior_visits_this_unit": history.get("this_unit", []),
        "same_model_elsewhere": history.get("same_model_elsewhere", []),
        "similar_faults_recalled": history.get("similar_faults_recalled", []),
        "load_these": to_load,
        "already_in_van": sorted(in_van),
    }


# --------------------------------------------------------------------------
# the loop closing: what the technician learned goes back in
# --------------------------------------------------------------------------

def close_work_order(work_order_id: str, found_cause: str,
                     parts_consumed: list[str], labor_hours: float,
                     tech_note: str = "", first_visit_fix: bool = True) -> dict:
    """Close a job with what the technician actually found, and learn from it.

    This is the half of the system that makes it improve. The technician is
    the only person who knows what the fault really was; until that is written
    down it helps nobody. On close, the job becomes a retrievable repair
    record, so the next caller who describes this fault - in their own words,
    on any unit of this model - gets a briefing shaped by it.

    Any part that was reserved but not fitted is released back to stock
    immediately, so a held part never silently blocks the next job.

    Args:
        work_order_id: the job being closed.
        found_cause: what the fault actually was, in the technician's words.
        parts_consumed: SKUs actually fitted, not those ordered.
        labor_hours: time on site.
        tech_note: anything the next person should know.
        first_visit_fix: whether it was resolved on this visit.

    Returns:
        Confirmation, the parts released, and the new size of the corpus.
    """
    wo = STORE.work_orders.get(work_order_id)
    if wo is None:
        return {"ok": False, "reason": "unknown work order"}

    unit = STORE.units.get(wo.serial)
    if unit is None:
        return {"ok": False, "reason": "unknown unit"}

    released = [s for s in wo.parts_reserved if s not in parts_consumed]
    for sku in released:
        STORE.release(sku)
    for sku in parts_consumed:
        STORE.release(sku)

    wo.found_cause = found_cause
    wo.parts_consumed = list(parts_consumed)
    wo.labor_hours = labor_hours
    wo.tech_note = tech_note or None
    wo.first_visit_fix = first_visit_fix
    wo.closed_on = datetime.now().date().isoformat()
    wo.status = "closed"
    wo.log(f"closed by {wo.technician_id}: {found_cause}")

    # the record the next briefing will be built from
    narrative = found_cause if not tech_note else f"{found_cause}. {tech_note}"
    repair = Repair(
        id=f"r-{uuid.uuid4().hex[:6]}",
        serial=wo.serial,
        manufacturer=unit.manufacturer,
        model=unit.model,
        reported_symptom=wo.reported_symptom,
        error_code=wo.error_code,
        found_cause=narrative,
        parts_consumed=tuple(parts_consumed),
        labor_hours=labor_hours,
        closed_on=wo.closed_on,
        technician_id=wo.technician_id or "",
    )
    STORE.repairs.append(repair)
    INDEX.add(repair)

    return {
        "ok": True,
        "work_order_id": wo.id,
        "closed_on": wo.closed_on,
        "first_visit_fix": first_visit_fix,
        "parts_released_back_to_stock": released,
        "learned": narrative,
        "corpus_size": INDEX.size(),
    }


def attach_transcript(work_order_id: str, transcript: str) -> dict:
    """Store the call transcript on the work order.

    Kept because the caller's own words are how the *next* caller will describe
    the same fault, and that is what semantic retrieval matches against.
    """
    wo = STORE.work_orders.get(work_order_id)
    if wo is None:
        return {"ok": False, "reason": "unknown work order"}
    wo.call_transcript = transcript
    wo.log("transcript attached")
    return {"ok": True, "chars": len(transcript)}
