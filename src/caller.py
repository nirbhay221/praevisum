"""Who is on the phone. Resolved when the line connects, before anyone speaks.

This does not belong in a tool. A tool is something the agent chooses to call,
and by then it has already said hello to a stranger it could have greeted by
name. The number arrives in the Twilio `start` event, milliseconds before the
first word, so the lookup happens there and the answer is waiting in session
state when the agent opens its mouth.

Two outcomes, and both are handled here rather than left to the model:

  KNOWN    load their account, sites, machines and last visit, so the first
           sentence can be "is this the Traulsen in the back again?"

  UNKNOWN  register them. A provisional contact and account row exist before
           the greeting finishes, so anything said during the call has
           somewhere to attach. Nobody is left as a floating phone number.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from . import db


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def resolve(e164: str) -> dict:
    """Look up a caller, registering them if we have never spoken before.

    Returns a dict that goes straight into session state. `known` tells the
    agent which conversation it is having: continuing a relationship, or
    starting one.
    """
    e164 = (e164 or "").strip()
    if not e164 or e164 == "unknown":
        return {"known": False, "registered": False, "phone": e164,
                "why": "no caller id on this call"}

    with db.connect() as c:
        row = c.execute(
            """SELECT ct.id contact_id, ct.name, ct.role, ct.channel_pref,
                      a.id account_id, a.name account_name, a.kind account_kind,
                      p.label phone_label
               FROM phones p
               JOIN contacts ct ON ct.id = p.contact_id
               JOIN accounts a  ON a.id  = ct.account_id
               WHERE p.e164 = ?""", (e164,)).fetchone()

        if row is None:
            return _register(e164)

        sites = c.execute(
            """SELECT id, label, address, access_note
               FROM sites WHERE account_id = ? ORDER BY label""",
            (row["account_id"],)).fetchall()

        assets = c.execute(
            """SELECT ast.id, ast.manufacturer, ast.model_number, ast.family,
                      ast.location_note, s.label site_label, s.id site_id
               FROM assets ast JOIN sites s ON s.id = ast.site_id
               WHERE s.account_id = ? AND ast.retired_on IS NULL
               ORDER BY s.label, ast.family""",
            (row["account_id"],)).fetchall()

        # The most useful single fact on a service call: what happened last
        # time, and whether it took more than one trip.
        last = c.execute(
            """SELECT w.id, w.reported_symptom, w.opened_at, ast.id asset_id,
                      ast.manufacturer, ast.model_number, ast.family,
                      (SELECT COUNT(*) FROM visits v WHERE v.work_order_id = w.id) visits,
                      (SELECT v.found_cause FROM visits v
                        WHERE v.work_order_id = w.id AND v.found_cause IS NOT NULL
                        ORDER BY v.seq DESC LIMIT 1) found_cause
               FROM work_orders w
               LEFT JOIN assets ast ON ast.id = w.asset_id
               WHERE w.account_id = ? ORDER BY w.opened_at DESC LIMIT 1""",
            (row["account_id"],)).fetchone()

    out = {
        "known": True,
        "registered": False,
        "phone": e164,
        "phone_label": row["phone_label"],
        "contact_id": row["contact_id"],
        "contact_name": row["name"],
        "contact_role": row["role"],
        "channel_pref": row["channel_pref"],
        "account_id": row["account_id"],
        "account_name": row["account_name"],
        "account_kind": row["account_kind"],
        "sites": [dict(s) for s in sites],
        "assets": [dict(a) for a in assets],
        "single_site": len(sites) == 1,
        "single_asset": len(assets) == 1,
    }
    if last:
        out["last_job"] = {
            "work_order": last["id"],
            "when": (last["opened_at"] or "")[:10],
            "symptom": last["reported_symptom"],
            "asset_id": last["asset_id"],
            "machine": f"{last['manufacturer']} {last['model_number']}" if last["manufacturer"] else None,
            "family": last["family"],
            "visits": last["visits"],
            "found_cause": last["found_cause"],
            "took_two_trips": (last["visits"] or 0) > 1,
        }
    return out


def _register(e164: str) -> dict:
    """A number we have never heard from. Give it somewhere to live, now.

    Provisional on purpose: we do not know their name yet, and the agent will
    ask during the call. What matters is that a work order opened two minutes
    from now has an account to hang off, instead of being orphaned or invented.
    """
    now = datetime.now().isoformat(timespec="seconds")
    account_id = _new_id("A")
    contact_id = _new_id("C")

    with db.txn() as c:
        c.execute(
            "INSERT INTO accounts (id,kind,name,opened_on,notes) VALUES (?,?,?,?,?)",
            (account_id, "person", f"New caller {e164}", now[:10],
             "created automatically on first call, details not yet confirmed"))
        c.execute(
            "INSERT INTO contacts (id,account_id,name,role,channel_pref) VALUES (?,?,?,?,?)",
            (contact_id, account_id, "unknown", "caller", "sms"))
        c.execute(
            "INSERT INTO phones (e164,contact_id,label,verified) VALUES (?,?,?,?)",
            (e164, contact_id, "inbound", 0))

    return {
        "known": False,
        "registered": True,
        "phone": e164,
        "contact_id": contact_id,
        "account_id": account_id,
        "sites": [],
        "assets": [],
        "collect": ["their name", "the business name if there is one",
                    "the site address", "what equipment it is"],
        "advice": "First time this number has called. A record exists already, "
                  "so open a work order normally. Get their name early and use "
                  "it. Do not ask for an account number, they do not have one.",
    }


def confirm_details(contact_id: str, name: str = "", account_name: str = "",
                    site_label: str = "", address: str = "",
                    role: str = "") -> dict:
    """Fill in a provisional record once the caller has told us who they are.

    Called mid-conversation, not at the end. If the line drops after this, the
    details survive.
    """
    with db.txn() as c:
        row = c.execute("SELECT account_id FROM contacts WHERE id=?",
                        (contact_id,)).fetchone()
        if row is None:
            return {"ok": False, "why": "unknown contact"}
        account_id = row["account_id"]

        if name:
            c.execute("UPDATE contacts SET name=? WHERE id=?", (name, contact_id))
        if role:
            c.execute("UPDATE contacts SET role=? WHERE id=?", (role, contact_id))
        if account_name:
            c.execute("UPDATE accounts SET name=?, kind=?, notes=NULL WHERE id=?",
                      (account_name, "business", account_id))
        elif name:
            c.execute("UPDATE accounts SET name=?, notes=NULL WHERE id=?",
                      (name, account_id))

        site_id = None
        if site_label or address:
            existing = c.execute("SELECT id FROM sites WHERE account_id=? LIMIT 1",
                                 (account_id,)).fetchone()
            if existing:
                site_id = existing["id"]
                c.execute("UPDATE sites SET label=COALESCE(NULLIF(?,''),label), "
                          "address=COALESCE(NULLIF(?,''),address) WHERE id=?",
                          (site_label, address, site_id))
            else:
                site_id = _new_id("S")
                c.execute("INSERT INTO sites (id,account_id,label,address) VALUES (?,?,?,?)",
                          (site_id, account_id, site_label or (name or "site"), address))
                c.execute("UPDATE contacts SET site_id=? WHERE id=?", (site_id, contact_id))

    return {"ok": True, "contact_id": contact_id, "account_id": account_id,
            "site_id": site_id, "saved": True}


def register_asset(site_id: str, manufacturer: str, model_number: str,
                   family: str = "", location_note: str = "") -> dict:
    """Record a machine we did not know a customer had.

    Links to the certified catalogue when the model is found there, and works
    perfectly well when it is not: plenty of real equipment was never
    submitted for certification.
    """
    asset_id = _new_id("AST")
    with db.txn() as c:
        eq = c.execute(
            """SELECT id, product_type FROM equipment
               WHERE brand LIKE ? AND model_norm =
                 UPPER(REPLACE(REPLACE(REPLACE(?,'-',''),' ',''),'/',''))
               LIMIT 1""", (f"%{manufacturer}%", model_number)).fetchone()

        c.execute("""INSERT INTO assets
                     (id,site_id,manufacturer,model_number,equipment_id,family,
                      installed_on,location_note)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (asset_id, site_id, manufacturer, model_number,
                   eq["id"] if eq else None,
                   family or (eq["product_type"] if eq else None),
                   None, location_note or None))

    return {"ok": True, "asset_id": asset_id,
            "matched_catalogue": eq is not None,
            "note": "matched a certified model" if eq else
                    "not in the certification catalogue, which is normal and fine"}
