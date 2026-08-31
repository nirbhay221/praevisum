"""Extended cover somebody actually bought, as against cover we quoted.

THE GAP THIS CLOSES

`aftercare.warranty_options` is a live tool. It reads the manufacturer's
published term, works out what a few more years would cost, and says it. That
part worked.

Nothing recorded the answer. There was no table, no column and no function for
a customer saying yes. So the desk could price extended cover on a sales call,
the customer could buy it, and when they rang eighteen months later with a
fault the coverage was computed from the manufacturer term alone and they were
told they were out of warranty.

`assets.warranty_until` looked like the place for it and is NULL on all 428
rows: a column that exists and nothing has ever written.

WHY A SEPARATE TABLE AND NOT THAT COLUMN

The same argument standing.py makes about install dates. WHERE A TERM CAME
FROM changes what it is worth. A manufacturer term is published, dated, and
carries a source URL anybody can check. An extension is something we sold and
owe. Collapsing both into one date loses which of the two is being relied on,
and they fail differently: a manufacturer can refuse a claim, and we cannot
refuse our own.

WHAT IT COVERS IS NOT ASSUMED

Extended cover in this trade is usually parts-only. Selling "five years cover"
that turns out to exclude labour is how a warranty becomes an argument on a
kitchen floor, so parts and labour are recorded separately and the default for
labour is NO.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from . import db


def _nid() -> str:
    return f"COV-{uuid.uuid4().hex[:8].upper()}"


def _plus_years(start: str, years: float) -> str:
    """Add years to a date without pulling in a date library.

    Whole years shift the year; a half year adds six months. Clamped to the
    28th so 29 February never produces an invalid date.
    """
    y, m, d = (int(x) for x in start[:10].split("-"))
    whole = int(years)
    months = round((years - whole) * 12)
    y += whole
    m += months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}-{min(d, 28):02d}"



def _what_they_mean(asset_id: str, po_id: str) -> tuple[str, str]:
    """Turn a catalogue handle into the order it was actually bought on.

    OBSERVED LIVE, ONE MINUTE AFTER THE HANDLES STARTED WORKING.

    The desk sold a ThinkPad, confirmed PO-20CD33, offered three more years of
    cover, the customer said yes, and it called this with

        asset_id="STK-366"

    STK-366 is a row on the price list. It is not a machine anybody owns and
    it is not an order. This refused, correctly, and the desk then tried to
    RECOVER by calling register_asset -- which would have invented a machine
    standing at a customer site for a laptop that has not shipped.

    The handle was the right idea used in the wrong slot, and the honest fix
    is to accept it: a catalogue handle plus a confirmed order for that same
    machine is enough to know what cover is being sold against.
    """
    ident = (asset_id or "").strip()
    if not ident.upper().startswith("STK-") or po_id:
        return asset_id, po_id

    from .supply import the_row_behind
    from .tenancy import the_desk

    row = the_row_behind(ident)
    if row is None:
        return asset_id, po_id

    name = " ".join(x for x in (row["manufacturer"] or "",
                                row["model_number"] or "") if x).strip()

    with db.connect() as c:
        # The order this call just raised for this machine. Newest first, and
        # only this company's, so a handle can never reach across the wall.
        found = c.execute(
            """SELECT po.id FROM purchase_orders po
               JOIN purchase_lines pl ON pl.po_id = po.id
               WHERE po.dealer_id = ? AND po.status IN ('draft','confirmed')
                 AND pl.description = ?
               ORDER BY po.placed_at DESC LIMIT 1""",
            (the_desk(), name)).fetchone()

    if found is None:
        # A REAL MACHINE ON OUR SHELF, WITH NOTHING BOUGHT YET.
        #
        # OBSERVED LIVE. The desk quoted our own three year plan on a filing
        # cabinet at $66.10, the customer said yes, and this was called with
        # the CATALOGUE handle before any order existed. There was nothing to
        # attach cover to, the call failed, and the desk recovered by asking
        # the customer to read the model number off a filing cabinet they had
        # not bought and did not have.
        #
        # The handle is good and the order simply has not been raised yet, so
        # the answer is to raise it. Marked distinctly so `sell_cover` can say
        # that plainly instead of refusing into a dead end.
        return f"NOT-ORDERED-YET:{name}", ""

    print(f"[extended] {ident} is a price-list row, not a machine; selling "
          f"cover against {found['id']}", flush=True)
    return found["id"], ""


def sell_cover(asset_id: str, extra_years: float, price: float = 0.0,
               covers_labour: bool = False, sold_by: str = "",
               po_id: str = "", note: str = "") -> dict:
    """Record that a customer bought extended cover on a machine.

    Call this when they SAY YES, not when the option is quoted. The end date is
    computed once and stored, so it cannot drift if the install date is later
    corrected.

    Args:
        asset_id: the machine being covered.
        extra_years: years beyond the manufacturer term, e.g. 2.
        price: what they are paying for it.
        covers_labour: only true if labour was actually included. The default
            is parts-only, which is what this trade normally sells.
        sold_by: who agreed it, in their words.
        po_id: the order it was sold against, if there is one.
        note: anything they said about it.
    """
    if extra_years <= 0:
        return {"ok": False, "why": "extended cover has to be some years long"}

    asset_id, po_id = _what_they_mean(asset_id, po_id)

    # NOTHING HAS BEEN ORDERED, so there is nothing for cover to sit on.
    #
    # Cover is a line on an order, and `create_purchase_order` prices it off
    # the machine on that same order. So the desk is told to do the one thing
    # that works rather than being left to invent a recovery -- which, on the
    # call this comes from, meant asking a customer to read a model number off
    # a cabinet still sitting in our warehouse.
    if asset_id.startswith("NOT-ORDERED-YET:"):
        machine = asset_id.split(":", 1)[1]
        years = int(extra_years) if extra_years == int(extra_years) else extra_years
        return {
            "ok": False,
            "why": f"no order has been raised for the {machine} yet, so there "
                   "is nothing to put cover on",
            "say": f"Do NOT ask them for a model number: they have not got it "
                   f"and we have. Raise the order first, with the cover as a "
                   f"second line, by calling create_purchase_order with "
                   f"['{machine}', '{years}-year Essential cover']. The cover "
                   "is priced off the machine on that same order and the "
                   "total comes back with both on it.",
            "then": [machine, f"{years}-year Essential cover"],
        }

    with db.connect() as c:
        a = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.installed_on,
                      s.account_id
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE a.id = ?""", (asset_id,)).fetchone()

    if a is None:
        # THEY ARE BUYING IT RIGHT NOW, so there is no machine to attach to.
        #
        # Heard on a live call: the desk offered three extra years on a laptop
        # at the moment of sale, the customer said yes, and this refused with
        # "I can't add the warranty to that purchase order yet". It was right
        # to refuse and the shape was wrong.
        #
        # A machine only becomes an asset when the order is DELIVERED, and the
        # only moment somebody will ever say yes to extended cover is when
        # they are buying. Those two facts do not fit together unless cover
        # can be sold against the ORDER and settle onto the machine later.
        return _sell_against_an_order(asset_id, extra_years, price,
                                      covers_labour, sold_by, note)
    if not a["installed_on"]:
        return {"ok": False,
                "why": "we have no install date for that machine, so there is "
                       "nothing to extend from"}

    with db.connect() as c:
        already = c.execute(
            "SELECT id, ends_on FROM cover_sold WHERE asset_id = ?",
            (asset_id,)).fetchone()
    if already is not None:
        # Selling a second extension on one machine is either a mistake or a
        # customer being charged twice. Both are worth stopping.
        return {"ok": False,
                "why": f"that machine already carries extended cover to "
                       f"{already['ends_on']}",
                "cover": already["id"]}

    from .cover import published_terms

    terms = published_terms(a["manufacturer"], a["model_number"])
    base = float((terms or {}).get("parts_years") or 0) if terms else 0.0

    # EXTENDS THE MANUFACTURER TERM, does not restart the clock. Two years on
    # top of a six year term ends at eight from install, not two from today,
    # and a customer who is told otherwise has been sold two years of nothing.
    ends = _plus_years(a["installed_on"], base + extra_years)

    cid = _nid()
    with db.txn() as c:
        c.execute(
            """INSERT INTO cover_sold
               (id, asset_id, account_id, po_id, extra_years, price,
                starts_on, ends_on, covers_parts, covers_labour, sold_on,
                sold_by, note)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)""",
            (cid, asset_id, a["account_id"], po_id or None, extra_years,
             price or None, a["installed_on"], ends,
             1 if covers_labour else 0,
             datetime.now().isoformat(timespec="seconds"),
             sold_by or None, note or None))

    return {"ok": True, "cover": cid, "asset": asset_id,
            "machine": f"{a['manufacturer']} {a['model_number']}",
            "manufacturer_term_years": base,
            "extra_years": extra_years,
            "covered_until": ends,
            "parts": True, "labour": bool(covers_labour),
            "price": price,
            "say": (f"Covered to {ends}. That is the manufacturer's "
                    f"{base:g} years plus the {extra_years:g} you bought, "
                    "counted from installation rather than from today."
                    + ("" if covers_labour else
                       " Parts only: the labour to fit them is not included, "
                       "and say so now rather than at the first fault."))}


def _sell_against_an_order(po_id: str, extra_years: float, price: float,
                           covers_labour: bool, sold_by: str,
                           note: str) -> dict:
    """Cover bought at the till, before the machine exists.

    Written against the order. `ownership.becomes_theirs` moves it onto the
    asset when the order lands, so the customer is covered from the install
    date rather than from whenever somebody remembered to write it down.
    """
    with db.connect() as c:
        po = c.execute(
            """SELECT po.id, po.status, po.account_id, a.name
               FROM purchase_orders po JOIN accounts a ON a.id = po.account_id
               WHERE po.id = ?""", (po_id,)).fetchone()
    if po is None:
        return {"ok": False,
                "why": f"{po_id!r} is neither a machine on the book nor an "
                       "order. Give the order number they are buying under"}

    with db.connect() as c:
        already = c.execute(
            "SELECT id FROM cover_sold WHERE po_id = ? AND asset_id IS NULL",
            (po_id,)).fetchone()
    if already is not None:
        return {"ok": False, "why": f"extended cover is already on {po_id}",
                "cover": already["id"]}

    cid = _nid()
    with db.txn() as c:
        c.execute(
            """INSERT INTO cover_sold
               (id, asset_id, account_id, po_id, extra_years, price,
                starts_on, ends_on, covers_parts, covers_labour, sold_on,
                sold_by, note)
               VALUES (?,NULL,?,?,?,?,'','',1,?,?,?,?)""",
            (cid, po["account_id"], po_id, extra_years, price or None,
             1 if covers_labour else 0,
             datetime.now().isoformat(timespec="seconds"),
             sold_by or None, note or None))

    return {"ok": True, "cover": cid, "order": po_id,
            "customer": po["name"], "extra_years": extra_years,
            "price": price, "parts": True, "labour": bool(covers_labour),
            "starts_when": "the machine is delivered",
            "say": (f"{extra_years:g} extra years added to the order, at "
                    f"${price:,.2f}. It runs from the day it is installed, "
                    "not from today."
                    + ("" if covers_labour else
                       " Parts only: the labour to fit them is not included, "
                       "and say so now rather than at the first fault."))}


def cover_on(asset_id: str) -> dict:
    """Extended cover on this machine, if any, and whether it is still live."""
    with db.connect() as c:
        row = c.execute(
            "SELECT * FROM cover_sold WHERE asset_id = ?",
            (asset_id,)).fetchone()
    if row is None:
        return {"has_cover": False}

    today = date.today().isoformat()
    return {"has_cover": True,
            "cover": row["id"],
            "live": row["ends_on"] >= today,
            "ends_on": row["ends_on"],
            "extra_years": row["extra_years"],
            "parts": bool(row["covers_parts"]),
            "labour": bool(row["covers_labour"]),
            "price": row["price"],
            "sold_by": row["sold_by"]}


def sold_to(account_id: str) -> dict:
    """Every extension on one account, for the console."""
    today = date.today().isoformat()
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT cs.*, a.manufacturer, a.model_number
               FROM cover_sold cs JOIN assets a ON a.id = cs.asset_id
               WHERE cs.account_id = ? ORDER BY cs.ends_on DESC""",
            (account_id,))]
    for r in rows:
        r["live"] = r["ends_on"] >= today
    return {"cover": rows, "live": sum(1 for r in rows if r["live"])}
