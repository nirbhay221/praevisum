"""Telling somebody to come and collect it, which nothing did.

THE HOLE THIS FILLS

`carrier_delivered` handles the carrier reporting that a parcel landed. That
is the END of the shipping leg. The beginning of it did not exist: the
`shipments` table has columns for carrier, service level, tracking, ship date
and cost, and no code anywhere wrote a single row into it.

So an order could be placed, confirmed, and then nothing happened to it until
somebody manually told the carrier, out of band, in a way the system never
saw.

WHY EMAIL AND NOT THE UPS API

UPS, FedEx and DHL all publish shipping APIs, and a real deployment at volume
should use one: you get a label, a rate and a tracking number back in the same
call. That needs a carrier account, OAuth credentials and a rate contract.

What a small dealer with four vans actually does is email the depot or the
supplier and ask them to collect, because the volume does not justify an
integration and the relationship is a person. That is what this does, using
the same mail path that already sends engineers their jobs.

The tracking number therefore arrives LATER, from the carrier, rather than
immediately. `note_tracking` is how it gets attached, and until it does the
shipment honestly says it does not have one.

WHAT THIS DELIBERATELY DOES NOT DO

It does not invent a tracking number to make the screen look finished. A
tracking number a customer cannot type into ups.com is worse than an empty
field, because they will try.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import db

# What the dealer offers. Not the carrier's full product list: these are the
# three a service business actually picks between, and the lead times are what
# gets quoted to the customer.
SERVICES = {
    "ground": ("Ground", 4),
    "two_day": ("2nd Day Air", 2),
    "overnight": ("Next Day Air", 1),
}


def _nid() -> str:
    return f"SHP-{uuid.uuid4().hex[:8].upper()}"


def book_collection(dealer_id: str, po_id: str, carrier: str = "UPS",
                    service_level: str = "ground", send: bool = True) -> dict:
    """Ask a carrier to collect an order, and record that we asked.

    Writes the shipment first and mails second, so a mail failure leaves a
    shipment somebody can chase rather than losing the fact that we tried.

    Args:
        dealer_id: whose order.
        po_id: the customer order being shipped.
        carrier: who is collecting. UPS unless somebody says otherwise.
        service_level: ground, two_day, or overnight.
        send: set False to record without mailing, for testing.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)
    service_level = (service_level or "ground").strip().lower()
    if service_level not in SERVICES:
        return {"ok": False,
                "why": f"{service_level!r} is not a service we book",
                "choices": list(SERVICES)}

    label, days = SERVICES[service_level]

    with db.connect() as c:
        po = c.execute(
            """SELECT po.id, po.status, po.account_id, po.site_id,
                      a.name account, s.address, s.label site
               FROM purchase_orders po
               JOIN accounts a ON a.id = po.account_id
               LEFT JOIN sites s ON s.id = po.site_id
               WHERE po.id = ? AND a.dealer_id = ?""",
            (po_id, dealer_id)).fetchone()
        if po is None:
            return {"ok": False, "why": f"no order {po_id!r} on this book"}

        # AN ORDER NOBODY HAS CONFIRMED IS NOT AN ORDER. Shipping a draft is
        # how a customer receives a machine they were still deciding about.
        if po["status"] == "draft":
            return {"ok": False,
                    "why": "that order is still a draft. Confirm it first",
                    "status": po["status"]}

        already = c.execute("SELECT id, carrier, tracking FROM shipments "
                            "WHERE po_id = ?", (po_id,)).fetchone()
        if already is not None:
            return {"ok": False,
                    "why": f"{po_id} was already booked with "
                           f"{already['carrier']}",
                    "shipment": already["id"],
                    "tracking": already["tracking"] or "not issued yet"}

        lines = [dict(r) for r in c.execute(
            "SELECT description, qty FROM purchase_lines WHERE po_id = ?",
            (po_id,))]

    if not po["address"]:
        return {"ok": False,
                "why": "we have no address for that site, so nobody can be "
                       "asked to collect anything"}

    sid = _nid()
    eta = (date.today() + timedelta(days=days)).isoformat()

    with db.txn() as c:
        c.execute(
            """INSERT INTO shipments
               (id, po_id, carrier, service_level, tracking, shipped_at,
                eta_date) VALUES (?,?,?,?,?,?,?)""",
            (sid, po_id, carrier, label, None,
             datetime.now().isoformat(timespec="seconds"), eta))
        c.execute("UPDATE purchase_orders SET status='shipped' WHERE id=?",
                  (po_id,))

    mailed = {"sent": False, "why": "not attempted"}
    if send:
        mailed = _mail_the_depot(dealer_id, sid, carrier, label, po, lines, eta)

    from . import events
    events.publish(dealer_id, "shipping",
                   what=f"{carrier} {label} booked for {po_id} to "
                        f"{po['account']}")

    return {"ok": True, "shipment": sid, "order": po_id,
            "carrier": carrier, "service": label,
            "to": f"{po['account']}, {po['address']}",
            "eta": eta, "collection_request": mailed,
            "tracking": "",
            "note": "No tracking number yet. It comes back from the carrier, "
                    "and inventing one now would give the customer something "
                    "to type into ups.com that does not exist."}


def _mail_the_depot(dealer_id: str, sid: str, carrier: str, service: str,
                    po, lines: list[dict], eta: str) -> dict:
    """The collection request itself. Never raises.

    A shipment that is recorded but whose mail failed is a thing somebody can
    see and chase. A crash here would lose both.
    """
    what = "\n".join(f"  {l['qty']} x {l['description']}" for l in lines) \
        or "  see the order"

    body = (
        f"Collection request {sid}\n\n"
        f"Carrier   {carrier}\n"
        f"Service   {service}\n"
        f"Order     {po['id']}\n\n"
        f"Collect from us and deliver to:\n"
        f"  {po['account']}\n"
        f"  {po['site'] or 'site'}\n"
        f"  {po['address']}\n\n"
        f"Contents:\n{what}\n\n"
        f"Expected with the customer by {eta}.\n\n"
        "Reply to this message with the tracking number once it is raised.\n"
        "Delivery confirmation should be posted to the tracking webhook we "
        "already gave you; we do not read replies automatically."
    )

    to = _depot_address(dealer_id)
    if not to:
        return {"sent": False,
                "why": "no depot address configured for this dealer"}

    try:
        from .email_out import send

        out = send(to, f"Collection request {sid} for {po['id']}", body,
                   kind="transactional", dealer_id=dealer_id)
        return {"sent": bool(out.get("ok")), "to": to,
                "why": out.get("why") or "sent"}
    except Exception as e:
        print(f"[shipping] collection request {sid} could not be mailed: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"sent": False, "to": to, "why": f"{type(e).__name__}"}


def _depot_address(dealer_id: str) -> str:
    """Where collection requests go. Configured, never guessed."""
    import os

    return (os.getenv("SHIPPING_DEPOT_EMAIL", "")
            or os.getenv("EMAIL_FROM", "")).strip()


def note_tracking(dealer_id: str, po_id: str, tracking: str) -> dict:
    """Attach the tracking number the carrier came back with.

    Separate from booking because it genuinely arrives later. The customer can
    be told a shipment exists before anybody has a number for it, and pretending
    otherwise means inventing one.
    """
    tracking = (tracking or "").strip()
    if not tracking:
        return {"ok": False, "why": "no tracking number given"}

    with db.connect() as c:
        row = c.execute(
            """SELECT sh.id FROM shipments sh
               JOIN purchase_orders po ON po.id = sh.po_id
               JOIN accounts a ON a.id = po.account_id
               WHERE sh.po_id = ? AND a.dealer_id = ?""",
            (po_id, dealer_id)).fetchone()
    if row is None:
        return {"ok": False, "why": f"nothing is booked for {po_id!r}"}

    with db.txn() as c:
        c.execute("UPDATE shipments SET tracking = ? WHERE id = ?",
                  (tracking, row["id"]))

    from . import events
    events.publish(dealer_id, "shipping",
                   what=f"tracking {tracking} on {po_id}")
    return {"ok": True, "shipment": row["id"], "order": po_id,
            "tracking": tracking}


def in_transit(dealer_id: str = "D-REF") -> dict:
    """What is out with a carrier and not yet reported delivered."""
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT sh.id, sh.po_id, sh.carrier, sh.service_level,
                      sh.tracking, sh.shipped_at, sh.eta_date,
                      a.name account, s.address
               FROM shipments sh
               JOIN purchase_orders po ON po.id = sh.po_id
               JOIN accounts a ON a.id = po.account_id
               LEFT JOIN sites s ON s.id = po.site_id
               LEFT JOIN deliveries d ON d.po_id = sh.po_id
               WHERE a.dealer_id = ? AND d.id IS NULL
               ORDER BY sh.shipped_at DESC""", (dealer_id,))]

    today = date.today().isoformat()
    for r in rows:
        r["overdue"] = bool(r["eta_date"] and r["eta_date"] < today)
        r["tracking"] = r["tracking"] or ""

    return {"shipments": rows,
            "overdue": sum(1 for r in rows if r["overdue"]),
            "without_tracking": sum(1 for r in rows if not r["tracking"])}
