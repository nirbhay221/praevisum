"""The customers, the crew, and turning a lead into both.

WHAT WAS READ ONLY

Three of the four things this business is made of could not be edited by the
person who owns it:

  111 customers   written only by caller.py, when somebody rings in
  19 engineers    written only by the seed scripts, never at runtime
  prospects       found by hunting.py, rung by prospect.py, and then nothing

The crew is the worst of the three. crew.py already reports that Dale Hutchins
holds a certification that does not cover what he is being sent to, and there
was no way to correct it, hire anybody, change a phone number, or stand down
somebody who left. A dispatch list you cannot edit is a dispatch list that
goes stale and then sends the wrong person.

THE LEAD HAD NO CLOSING MOVE

This is the real gap. The chain runs: a promotion or a public complaint gives
hunting.py a reason to ring, prospect.py rings, somebody says yes, and then
the prospect stays a prospect forever. `wishlist`, the table that holds what a
customer said they wanted, had zero rows in it.

`win_the_lead` is that closing move. It writes the account, the site, the
person's name, their phone and what they asked for in one transaction, and
marks the prospect so it is never hunted again.

TWO THINGS IT REFUSES TO DO QUIETLY

CONSENT DOES NOT COME ALONG FOR THE RIDE. Being rung as a prospect and
agreeing to be a customer are different permissions, and a conversion that
silently granted marketing consent would turn every won lead into a
subscription nobody asked for. It is a separate argument, it defaults to no,
and what is recorded is who said it and when.

CLOSING IS NOT DELETING. 673 work orders point at accounts(id). A customer who
stopped trading keeps their row and leaves the book, the same rule the shop
floor follows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from . import db, events


def _nid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:6].upper()}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _one_match(rows: list, what: str, needle: str):
    """Refuse an ambiguous name rather than picking one of them.

    The same rule the shop floor follows. Two engineers called Dave and a
    silent pick sends the wrong one to a job.
    """
    if len(rows) > 1:
        return None, {"ok": False,
                      "why": f"{len(rows)} {what} match {needle!r}, so nothing "
                             "was changed. Be more specific",
                      "which": [r["name"] for r in rows[:6]]}
    return (rows[0] if rows else None), None


# --------------------------------------------------------------------------
# customers
# --------------------------------------------------------------------------

def set_customer(dealer_id: str, name: str, account_id: str = "",
                 kind: str = "", trade_terms: str = "",
                 notes: str = "") -> dict:
    """Add a customer, or correct one that exists.

    Matched by id when given, otherwise by name, because an owner says
    "Vasquez Catering" and not "A-1042". Only the fields actually passed are
    written, so correcting a name cannot wipe the trade terms.
    """
    name = (name or "").strip()
    account_id = (account_id or "").strip()
    if not (name or account_id):
        return {"ok": False, "why": "which customer? Give a name"}

    kind = (kind or "").strip().lower()
    if kind and kind not in ("business", "person"):
        return {"ok": False, "why": "a customer is a business or a person",
                "kind": ["business", "person"]}

    with db.connect() as c:
        if account_id:
            rows = c.execute(
                "SELECT id, name FROM accounts WHERE id = ? AND dealer_id = ?",
                (account_id, dealer_id)).fetchall()
            if not rows:
                return {"ok": False,
                        "why": f"no customer {account_id!r} on this book"}
        else:
            rows = c.execute(
                "SELECT id, name FROM accounts WHERE dealer_id = ? "
                "AND name LIKE ? AND closed_on IS NULL",
                (dealer_id, f"%{name}%")).fetchall()

    row, refusal = _one_match(rows, "customers", name)
    if refusal:
        return refusal

    if row is None:
        # CREATING NEEDS A KIND, for the same reason adding a machine needs a
        # manufacturer: without it a mistyped name silently opens a second
        # account for a customer who already exists, and their history splits
        # in two.
        if not kind:
            return {"ok": False,
                    "why": f"no customer matching {name!r}. To ADD them say "
                           "whether it is a business or a person; to correct "
                           "one that exists, check the name",
                    "adding_needs": ["kind"]}
        new_id = _nid("A")
        with db.txn() as c:
            c.execute(
                "INSERT INTO accounts (id,dealer_id,kind,name,trade_terms,"
                "opened_on,notes) VALUES (?,?,?,?,?,?,?)",
                (new_id, dealer_id, kind, name, trade_terms or None,
                 _now()[:10], notes or None))
        events.publish(dealer_id, "console", what=f"customer added: {name}")
        return {"ok": True, "added": True, "account_id": new_id, "name": name}

    sets, vals = [], []
    if account_id and name:
        # Renaming only makes sense when the match was by id. Matched by name,
        # the name IS the search term and writing it back changes nothing.
        sets.append("name = ?")
        vals.append(name)
    if kind:
        sets.append("kind = ?")
        vals.append(kind)
    if trade_terms:
        sets.append("trade_terms = ?")
        vals.append(trade_terms)
    if notes:
        sets.append("notes = ?")
        vals.append(notes)

    if not sets:
        return {"ok": True, "account_id": row["id"], "changed": [],
                "why": "nothing to change was given"}

    with db.txn() as c:
        c.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?",
                  (*vals, row["id"]))

    changed = [s.split(" =")[0] for s in sets]
    events.publish(
        dealer_id, "console",
        what=f"customer changed: {row['name']} ({', '.join(changed)})")
    return {"ok": True, "account_id": row["id"], "name": row["name"],
            "changed": changed}


def close_customer(dealer_id: str, name: str, why: str = "") -> dict:
    """Take a customer off the book without deleting what they bought.

    NOT a delete. 673 work orders, their complaints, returns and quotes all
    point at accounts(id), and the record of what somebody bought is how you
    answer a warranty claim two years after they stopped trading.
    """
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, name FROM accounts WHERE dealer_id = ? AND name LIKE ? "
            "AND closed_on IS NULL",
            (dealer_id, f"%{(name or '').strip()}%")).fetchall()

    row, refusal = _one_match(rows, "customers", name)
    if refusal:
        return refusal
    if row is None:
        return {"ok": False, "why": f"no open customer matching {name!r}"}

    with db.txn() as c:
        c.execute("UPDATE accounts SET closed_on = ?, closed_why = ? "
                  "WHERE id = ?",
                  (_now()[:10], why or "closed by the owner", row["id"]))
        kept = c.execute(
            "SELECT COUNT(*) n FROM work_orders WHERE account_id = ?",
            (row["id"],)).fetchone()["n"]

    events.publish(dealer_id, "console", what=f"customer closed: {row['name']}")
    return {"ok": True, "account_id": row["id"], "name": row["name"],
            "closed": True, "history_kept": kept,
            "note": "off the book, and their history is still there"}


# --------------------------------------------------------------------------
# the crew
# --------------------------------------------------------------------------

def set_engineer(dealer_id: str, name: str, phone: str = "", email: str = "",
                 home_base: str = "") -> dict:
    """Hire an engineer, or correct one who is already on the crew.

    Adding one needs a phone or an email. An engineer the desk cannot reach
    cannot be dispatched, cannot be sent a briefing, and shows on the crew
    list as somebody available who is not.
    """
    name = (name or "").strip()
    if not name:
        return {"ok": False, "why": "which engineer? Give a name"}

    with db.connect() as c:
        rows = c.execute(
            "SELECT id, name, phone, email FROM technicians "
            "WHERE dealer_id = ? AND name LIKE ?",
            (dealer_id, f"%{name}%")).fetchall()

    row, refusal = _one_match(rows, "engineers", name)
    if refusal:
        return refusal

    if row is None:
        if not (phone.strip() or email.strip()):
            return {"ok": False,
                    "why": f"no engineer matching {name!r}. To ADD them give a "
                           "phone or an email, because the desk has to be able "
                           "to reach whoever it dispatches",
                    "adding_needs": ["phone or email"]}
        new_id = _nid("T")
        with db.txn() as c:
            c.execute(
                "INSERT INTO technicians (id,dealer_id,name,phone,email,"
                "home_base,active) VALUES (?,?,?,?,?,?,1)",
                (new_id, dealer_id, name, phone.strip() or None,
                 email.strip() or None, home_base.strip() or None))
        events.publish(dealer_id, "console", what=f"engineer added: {name}")
        return {"ok": True, "added": True, "engineer_id": new_id, "name": name,
                "note": "no certifications recorded yet, so dispatch will not "
                        "send them to work that needs one"}

    sets, vals = [], []
    for col, val in (("phone", phone), ("email", email),
                     ("home_base", home_base)):
        if val.strip():
            sets.append(f"{col} = ?")
            vals.append(val.strip())

    if not sets:
        return {"ok": True, "engineer_id": row["id"], "changed": [],
                "why": "nothing to change was given"}

    with db.txn() as c:
        c.execute(f"UPDATE technicians SET {', '.join(sets)} WHERE id = ?",
                  (*vals, row["id"]))
        # Coming back on the crew is a real edit. Somebody stood down and then
        # rehired must not stay invisible because the flag was never reset.
        c.execute("UPDATE technicians SET active = 1 WHERE id = ?",
                  (row["id"],))

    changed = [s.split(" =")[0] for s in sets]
    events.publish(
        dealer_id, "console",
        what=f"engineer changed: {row['name']} ({', '.join(changed)})")
    return {"ok": True, "engineer_id": row["id"], "name": row["name"],
            "changed": changed}


def stand_down_engineer(dealer_id: str, name: str) -> dict:
    """Take an engineer off the crew without erasing the jobs they did.

    NOT a delete. Appointments, visits, work orders and their certifications
    all point at technicians(id). Somebody who left still did the repair a
    customer is ringing about.
    """
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, name FROM technicians WHERE dealer_id = ? "
            "AND name LIKE ? AND active = 1",
            (dealer_id, f"%{(name or '').strip()}%")).fetchall()

    row, refusal = _one_match(rows, "engineers", name)
    if refusal:
        return refusal
    if row is None:
        return {"ok": False, "why": f"no active engineer matching {name!r}"}

    with db.txn() as c:
        c.execute("UPDATE technicians SET active = 0 WHERE id = ?",
                  (row["id"],))
        booked = c.execute(
            "SELECT COUNT(*) n FROM appointments WHERE technician_id = ? "
            "AND starts_at >= ?", (row["id"], _now())).fetchone()["n"]

    events.publish(dealer_id, "console",
                   what=f"engineer stood down: {row['name']}")
    out = {"ok": True, "engineer_id": row["id"], "name": row["name"],
           "stood_down": True}
    if booked:
        # Said rather than silently reassigned. Moving somebody else's diary
        # without being asked is how two engineers arrive at one site.
        out["still_booked"] = booked
        out["note"] = (f"{booked} appointment(s) are still in their diary and "
                       "were not moved. Reassign them")
    return out


# --------------------------------------------------------------------------
# the lead, from a signal to a customer
# --------------------------------------------------------------------------

def _find_lead(c, dealer_id: str, needle: str):
    """A lead by its id OR by its name, because an owner says "Corner Grocers".

    THE BUG THIS FIXES. These two were the only console tools that demanded an
    opaque id: parts match on a name, machines on a model number, customers
    and engineers on a name. Told "Corner Grocers are not interested", the
    agent passed the name, the tool refused, and the agent reported success
    anyway. Nothing was closed and the owner was told it had been.
    """
    needle = (needle or "").strip()
    if not needle:
        return None, {"ok": False, "why": "which lead? Give a name"}

    row = c.execute("SELECT * FROM prospects WHERE id = ? AND dealer_id = ?",
                    (needle, dealer_id)).fetchone()
    if row is not None:
        return row, None

    rows = c.execute(
        "SELECT * FROM prospects WHERE dealer_id = ? AND name LIKE ?",
        (dealer_id, f"%{needle}%")).fetchall()
    if len(rows) > 1:
        return None, {"ok": False,
                      "why": f"{len(rows)} leads match {needle!r}, so nothing "
                             "was changed. Be more specific",
                      "which": [r["name"] for r in rows[:6]]}
    if not rows:
        return None, {"ok": False, "why": f"no lead matching {needle!r} on "
                                          "this book"}
    return rows[0], None


def win_the_lead(dealer_id: str, prospect_id: str, contact_name: str,
                 wants: str = "", agreed_to_contact: bool = False,
                 site_label: str = "") -> dict:
    """Book a lead in as a customer: their name, their site, and what they want.

    THE CHAIN THIS FINISHES. A promotion or a public complaint gives hunting.py
    a reason to ring. prospect.py rings under a checked consent basis. Somebody
    says yes. Before this, that was where it stopped: the prospect stayed a
    prospect and `wishlist`, the table holding what a customer asked for, had
    nothing in it.

    Everything is written in one transaction, so a half-converted lead cannot
    exist. An account with no contact is a customer nobody can ring back.

    Consent is a separate argument and defaults to no. Agreeing to become a
    customer is not agreeing to be marketed at, and a conversion that granted
    it silently would turn every won lead into a subscription nobody asked for.
    """
    contact_name = (contact_name or "").strip()
    if not contact_name:
        return {"ok": False,
                "why": "who agreed? A customer with no name is a row nobody "
                       "can ring back"}

    with db.connect() as c:
        p, refusal = _find_lead(c, dealer_id, prospect_id)
    if refusal:
        return refusal
    prospect_id = p["id"]
    if (p["outcome"] or "").startswith("won"):
        return {"ok": False, "why": f"{p['name']} was already booked in"}

    phone = (p["phone_e164"] or "").strip()
    site_id, wish_id = "", ""

    with db.txn() as c:
        # THE PHONE MAY ALREADY BE KNOWN. phones.e164 is the primary key, and
        # a lead who rang in before being converted already has a row. Writing
        # blind fails the whole transaction mid-conversion, so an existing
        # number joins the account it already belongs to instead.
        seen = c.execute(
            "SELECT ct.account_id, ct.id contact_id FROM phones ph "
            "JOIN contacts ct ON ct.id = ph.contact_id WHERE ph.e164 = ?",
            (phone,)).fetchone() if phone else None

        if seen:
            account_id, contact_id = seen["account_id"], seen["contact_id"]
            joined = True
            c.execute("UPDATE accounts SET won_from_prospect = ?, "
                      "dealer_id = COALESCE(dealer_id, ?) WHERE id = ?",
                      (prospect_id, dealer_id, account_id))
            c.execute("UPDATE contacts SET name = ? WHERE id = ? "
                      "AND (name IS NULL OR name = 'unknown')",
                      (contact_name, contact_id))
        else:
            account_id, contact_id = _nid("A"), _nid("C")
            joined = False
            # The signal is carried onto the account. Losing it at the moment
            # it pays off makes it impossible to tell which hunting run earned
            # back its search spend.
            why_rung = (p["signal_seen"] or p["signal"] or "").strip()
            c.execute(
                "INSERT INTO accounts (id,dealer_id,kind,name,opened_on,notes,"
                "won_from_prospect) VALUES (?,?,?,?,?,?,?)",
                (account_id, dealer_id, "business", p["name"], _now()[:10],
                 (f"won from a lead. Rung because: {why_rung}"[:400]
                  if why_rung else "won from a lead"), prospect_id))
            c.execute(
                "INSERT INTO contacts (id,account_id,name,role,channel_pref) "
                "VALUES (?,?,?,?,?)",
                (contact_id, account_id, contact_name, "the person who agreed",
                 "sms"))
            if phone:
                c.execute(
                    "INSERT INTO phones (e164,contact_id,label,verified,"
                    "line_type) VALUES (?,?,?,?,?)",
                    (phone, contact_id, "from the lead", 1, p["line_type"]))
            if p["address"]:
                site_id = _nid("S")
                c.execute(
                    "INSERT INTO sites (id,account_id,label,address,lat,lon) "
                    "VALUES (?,?,?,?,?,?)",
                    (site_id, account_id, site_label.strip() or "main site",
                     p["address"], p["lat"], p["lon"]))

        if wants.strip():
            wish_id = _nid("W")
            c.execute(
                "INSERT INTO wishlist (id,account_id,want,reason,noted_at,"
                "status) VALUES (?,?,?,?,?,?)",
                (wish_id, account_id, wants.strip(),
                 f"said when the lead was booked in by {contact_name}",
                 _now(), "open"))

        if agreed_to_contact:
            c.execute(
                "INSERT OR REPLACE INTO outreach_consent "
                "(account_id,granted,granted_on,granted_via,evidence_ref) "
                "VALUES (?,1,?,?,?)",
                (account_id, _now()[:10],
                 f"{contact_name} agreed when the lead was booked in",
                 prospect_id))

        # approached_on AS WELL AS outcome. hunting.py and prospect.py both
        # select on `approached_on IS NULL`, so a lead won after the owner rang
        # it themselves would otherwise keep appearing on tomorrow's hunt list.
        c.execute("UPDATE prospects SET outcome = ?, "
                  "approached_on = COALESCE(approached_on, ?) WHERE id = ?",
                  (f"won: {contact_name}", _now()[:10], prospect_id))

    events.publish(dealer_id, "console",
                   what=f"lead booked in: {p['name']} ({contact_name})")
    out = {"ok": True, "account_id": account_id, "contact_id": contact_id,
           "customer": p["name"], "contact": contact_name,
           "joined_an_existing_customer": joined,
           "rung_because": p["signal"] or "",
           "marketing_consent": bool(agreed_to_contact)}
    if site_id:
        out["site_id"] = site_id
    if wish_id:
        out["wants"] = wants.strip()
    if not agreed_to_contact:
        out["note"] = ("booked in, but NOT signed up for outreach. Becoming a "
                       "customer is not agreeing to be marketed at")
    return out


def lose_the_lead(dealer_id: str, prospect_id: str, why: str = "") -> dict:
    """Close a lead that went nowhere, so it stops coming back.

    Kept rather than deleted, for a reason that costs money: the search that
    found them was billable, and a deleted lead is found again by the next
    hunting run and rung a second time.
    """
    with db.connect() as c:
        p, refusal = _find_lead(c, dealer_id, prospect_id)
    if refusal:
        return refusal
    prospect_id = p["id"]

    with db.txn() as c:
        c.execute("UPDATE prospects SET outcome = ?, "
                  "approached_on = COALESCE(approached_on, ?) WHERE id = ?",
                  (f"lost: {why.strip() or 'no reason given'}",
                   _now()[:10], prospect_id))

    events.publish(dealer_id, "console", what=f"lead closed: {p['name']}")
    return {"ok": True, "lead": p["name"], "outcome": "lost", "why": why,
            "note": "kept, so the next hunt does not find and ring them again"}


# --------------------------------------------------------------------------
# what the console shows
# --------------------------------------------------------------------------

def the_book(dealer_id: str = "D-REF", limit: int = 12) -> dict:
    """Customers, crew, open leads and what people asked for."""
    with db.connect() as c:
        customers = [dict(r) for r in c.execute(
            """SELECT a.id, a.name, a.kind, a.trade_terms, a.won_from_prospect,
                      (SELECT COUNT(*) FROM work_orders w
                        WHERE w.account_id = a.id) jobs
               FROM accounts a
               WHERE a.dealer_id = ? AND a.closed_on IS NULL
               -- A lead just booked in has no job history yet, so ordering by
               -- jobs alone buries the one customer the owner is actively
               -- working under twenty regulars. Won leads come first.
               ORDER BY (a.won_from_prospect IS NOT NULL) DESC,
                        jobs DESC, a.name LIMIT ?""", (dealer_id, limit))]

        crew = [dict(r) for r in c.execute(
            """SELECT id, name, phone, email, home_base FROM technicians
               WHERE dealer_id = ? AND active = 1 ORDER BY name""",
            (dealer_id,))]

        leads = [dict(r) for r in c.execute(
            """SELECT id, name, kind, phone_e164, signal, signal_kind,
                      signal_score, approached_on, outcome
               FROM prospects WHERE dealer_id = ?
                 AND (outcome IS NULL OR outcome NOT LIKE 'lost%')
               ORDER BY (outcome IS NOT NULL), signal_score DESC LIMIT ?""",
            (dealer_id, limit))]

        wants = [dict(r) for r in c.execute(
            """SELECT w.want, w.reason, a.name customer
               FROM wishlist w JOIN accounts a ON a.id = w.account_id
               WHERE a.dealer_id = ? AND w.status = 'open'
               ORDER BY w.noted_at DESC LIMIT ?""", (dealer_id, limit))]

        closed = c.execute(
            "SELECT COUNT(*) n FROM accounts WHERE dealer_id = ? "
            "AND closed_on IS NOT NULL", (dealer_id,)).fetchone()["n"]

    return {"customers": customers, "crew": crew, "leads": leads,
            "wants": wants, "closed_customers": closed}
