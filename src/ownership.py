"""What a sale is supposed to leave behind.

THE HOLE THIS FILLS

`confirm_purchase_order` updated a status and ordered in what we did not
hold, and stopped. It never recorded that the customer now OWNS the thing.

Which quietly broke the most important distinction in this whole system.
standing.py draws it explicitly:

    OURS   = "sold_by_us"        the date is ours, the cover is ours to grant
    THEIRS = "customer_stated"   it is a claim, quote chargeable, credit later

and date_provenance reads `assets.installed_source` to tell them apart. But
nothing ever wrote `sold_by_us`, because the only moment that could -- the
sale -- did not write anything at all. So every customer who bought from us
was treated on their next call exactly like somebody who turned up with a
machine from a competitor: cover became a claim, the visit was quoted
chargeable, and they were asked to produce paperwork WE issued.

The machinery for the honest version was already built. It just had no source
of truth to read, because the sale threw the fact away.

WHY THE DATE IS THE DELIVERY DATE

A warranty runs from when the machine reaches them, not from when they agreed
to buy it on the phone. For anything sourced in, those are weeks apart, and
using the wrong one shortens their cover by exactly the lead time and does it
in our favour, which is the kind of error nobody notices until a claim.

WHAT IS NOT A MACHINE

An order line can be a door gasket. Registering a gasket as a machine on
somebody's site puts a thing on their account that cannot be serviced, cannot
fail, and will confuse every later call. Parts are skipped, using the same
judgement backorder already makes about what a line actually is.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from . import db

# What standing.py will accept as OURS. Kept here as the value we write so
# that the two halves cannot drift apart silently.
SOLD_BY_US = "sold_by_us"


def _is_a_machine(description: str, sku: str | None,
                  dealer_id: str = "") -> bool:
    """A whole machine, rather than a part off the shelf.

    Reuses backorder's judgement rather than inventing a second one. It
    already knows that "Compressor overload relay" is a fifty dollar shelf
    part and a "Traulsen G12010 reach-in freezer" is not, and having two
    functions disagree about that is how a gasket ends up on somebody's asset
    register.
    """
    from .backorder import A_MACHINE, ACTUALLY_A_PART

    low = (description or "").lower()
    if any(w in low for w in ACTUALLY_A_PART):
        return False

    # ASK THE CATALOGUE BEFORE GUESSING FROM WORDS.
    #
    # A_MACHINE is a refrigeration word list: freezer, cooler, chiller,
    # merchandiser, walk-in. It was written when there was one company, and
    # there are now four. A laptop, an office chair and a projector match none
    # of those words, so on delivery they were all filed as "a part, not a
    # machine" and NOTHING was ever put on the customer's account.
    #
    # A confirmed ThinkPad was delivered to a customer and their machine list
    # did not change. Even "15.5 ft Single Glass Door" failed, which is a
    # cooler, because the words in its name are not the words in the list.
    #
    # We do not have to guess. If the line resolves to a row on our own price
    # list then it is a thing we SELL, and a thing we sell is a machine. The
    # word lists stay as the fallback for a line typed by hand that matches
    # nothing.
    try:
        from .supply import _find_on_the_floor
        from .tenancy import the_desk

        # THE ORDER'S OWN COMPANY, not whatever this thread happens to be
        # routed to. Reading the ambient vendor here meant a webhook or a
        # background job looked for a furniture chair on the REFRIGERATION
        # floor, did not find it, and filed it as a part -- so the chair never
        # reached the customer's account while identical refrigeration orders
        # worked, because for those the fallback happened to be right.
        if _find_on_the_floor(the_desk(dealer_id), description) is not None:
            return True
    except Exception:
        pass

    return any(w in low for w in A_MACHINE)


def _split(description: str) -> tuple[str, str]:
    """Make and model out of an order line, as best anybody can."""
    words = (description or "").split()
    if not words:
        return "", ""
    return words[0], " ".join(words[1:3])[:60]


def _family_of(description: str, dealer_id: str = "") -> str:
    """Which family this belongs to.

    THE CATALOGUE FIRST, because we sold the thing and its row says what it
    is. Asking the vendor router instead returned nothing for a laptop, so a
    delivered ThinkPad was written onto the customer's account with NO family
    at all -- and family is what `next_available_slot` matches an engineer's
    qualification against.

    A machine with no family can never be scheduled. It is not that no
    engineer is free; it is that the question cannot be asked. That is the
    quietest way to make a customer unserviceable, and it happens at the
    moment we hand them the machine.
    """
    try:
        from .supply import _find_on_the_floor
        from .tenancy import the_desk

        row = _find_on_the_floor(the_desk(dealer_id), description or "")
        if row is not None and (row["family"] or "").strip():
            return row["family"]
    except Exception:
        pass

    try:
        from .desk import _vendor_for

        found = _vendor_for(description or "")
        if found.get("found") and found.get("matched") != "category":
            return found.get("handles") or ""
    except Exception:
        pass
    return ""


def _attach_cover_bought_earlier(po_id: str, asset_id: str,
                                 installed_on: str) -> None:
    """Move cover sold against an order onto the machine it turned out to be.

    Never raises. A machine arriving is worth recording even if the cover
    cannot be settled, and the unattached row stays visible rather than being
    lost.
    """
    try:
        from .cover import published_terms
        from .extended import _plus_years

        with db.connect() as c:
            row = c.execute(
                """SELECT id, extra_years, covers_labour FROM cover_sold
                   WHERE po_id = ? AND asset_id IS NULL LIMIT 1""",
                (po_id,)).fetchone()
            if row is None:
                return
            a = c.execute("SELECT manufacturer, model_number FROM assets "
                          "WHERE id = ?", (asset_id,)).fetchone()

        terms = published_terms(a["manufacturer"], a["model_number"]) if a else None
        base = float((terms or {}).get("parts_years") or 0) if terms else 0.0
        ends = _plus_years(installed_on, base + float(row["extra_years"]))

        with db.txn() as c:
            c.execute(
                """UPDATE cover_sold SET asset_id = ?, starts_on = ?,
                   ends_on = ? WHERE id = ?""",
                (asset_id, installed_on, ends, row["id"]))
        print(f"[ownership] extended cover {row['id']} now covers {asset_id} "
              f"to {ends}", flush=True)
    except Exception as e:
        print(f"[ownership] could not attach cover bought on {po_id}: "
              f"{type(e).__name__}: {e}", flush=True)


def becomes_theirs(purchase_order_id: str, delivered_on: str = "") -> dict:
    """Put what we just sold onto the customer's account, as ours.

    Called when an order is confirmed. Every line that is a machine becomes an
    asset at their site, dated from delivery, and marked as sold by us so that
    the next call knows the cover is ours to grant rather than a claim they
    have to prove.

    Args:
        purchase_order_id: the confirmed order.
        delivered_on: the date it reaches them. Defaults to the promised date
            on the order, then to today.
    """
    with db.connect() as c:
        po = c.execute(
            """SELECT p.id, p.account_id, p.status, p.dealer_id
               FROM purchase_orders p WHERE p.id=?""",
            (purchase_order_id,)).fetchone()
        if po is None:
            return {"ok": False, "why": "no such order"}

        # The promised date lives on the supply order pegged to the line, not
        # on the line, because a line we fill off the shelf was never promised
        # anything: it goes out now.
        lines = c.execute(
            """SELECT l.line_no, l.sku, l.description, l.qty,
                      (SELECT s.promised_by FROM supply_orders s
                        WHERE s.for_purchase_order = l.po_id
                          AND s.for_line = l.line_no) promised_by
               FROM purchase_lines l
               WHERE l.po_id=? ORDER BY l.line_no""",
            (purchase_order_id,)).fetchall()

        site = c.execute(
            "SELECT id, label FROM sites WHERE account_id=? ORDER BY label "
            "LIMIT 1", (po["account_id"],)).fetchone()

    if site is None:
        return {"ok": False,
                "why": "this account has no site, so there is nowhere to put it",
                "say": "Ask where it is being delivered before promising cover."}

    # ALREADY DONE IS NOT DO IT AGAIN.
    #
    # This had no guard, so every extra call minted fresh assets: running it
    # twice on one order put two identical ThinkPads on a customer's account,
    # and the second one carried none of the cover attached to the first.
    #
    # Deliveries get reported more than once in real life. A carrier retries a
    # webhook, somebody clicks the console button after the carrier already
    # posted, an operator re-runs a job. All three are ordinary, and none of
    # them means the customer received a second machine.
    with db.connect() as c:
        done = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.family
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.from_order = ?""",
            (po["account_id"], purchase_order_id)).fetchall()
    if done:
        return {"ok": True, "purchase_order": purchase_order_id,
                "site": site["label"],
                "already_registered": [dict(r) for r in done],
                "registered": [], "skipped": [],
                "say": "This order was already put on their account. Nothing "
                       "was duplicated."}

    registered, skipped = [], []
    for line in lines:
        desc = line["description"] or ""
        if not _is_a_machine(desc, line["sku"], po["dealer_id"]):
            skipped.append({"line": line["line_no"], "description": desc,
                            "why": "a part, not a machine"})
            continue

        when = (delivered_on or line["promised_by"]
                or date.today().isoformat())[:10]
        make, model = _split(desc)
        family = _family_of(desc, po["dealer_id"])

        for _ in range(max(1, int(line["qty"] or 1))):
            asset_id = f"AST-{uuid.uuid4().hex[:8].upper()}"
            try:
                with db.txn() as c:
                    c.execute(
                        """INSERT INTO assets
                           (id, site_id, manufacturer, model_number, family,
                            installed_on, installed_source, location_note,
                            from_order)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (asset_id, site["id"], make, model, family or None,
                         when, SOLD_BY_US, None, purchase_order_id))
            except Exception as e:
                skipped.append({"line": line["line_no"], "description": desc,
                                "why": f"{type(e).__name__}: {e}"})
                continue

            # COVER BOUGHT AT THE TILL LANDS ON THE MACHINE NOW.
            #
            # Extended cover is sold while somebody is buying, which is the
            # only moment they will ever say yes to it, and a machine does not
            # exist as an asset until this runs. So the cover was written
            # against the ORDER with no asset, and this is where the two meet.
            #
            # Dated from the install rather than from the day it was sold: a
            # customer who buys three extra years should get three extra
            # years of cover, not three years minus however long delivery took.
            _attach_cover_bought_earlier(purchase_order_id, asset_id, when)

            registered.append({"asset_id": asset_id, "description": desc,
                               "manufacturer": make, "model_number": model,
                               "family": family, "cover_starts": when})

    # A MONTH FROM NOW, ASK WHETHER WE MAY TELL THEM ABOUT OFFERS.
    #
    # Once, after they have actually received something, rather than after
    # every call. Never raises: a machine landing on their account is worth
    # recording whether or not the question gets queued.
    try:
        from .staying_in_touch import ask_after_delivery

        ask_after_delivery(purchase_order_id)
    except Exception as e:
        print(f"[ownership] could not queue the offers question for "
              f"{purchase_order_id}: {type(e).__name__}: {e}", flush=True)

    return {
        "ok": True,
        "purchase_order": purchase_order_id,
        "site": site["label"],
        "registered": registered,
        "skipped": skipped,
        "say": ("This is now on their account and the cover is OURS, dated "
                "from delivery. On any later call about one of these machines "
                "the warranty is a record and not a claim: do not ask them to "
                "produce paperwork we issued ourselves."
                if registered else
                "Nothing on this order was a machine, so there is nothing to "
                "put on their account."),
    }


def what_we_sold_them(account_id: str) -> dict:
    """Everything on this account that came from us, and when cover started.

    The answer to "have I got a warranty on this" for somebody who bought from
    us, without asking them for a receipt.
    """
    with db.connect() as c:
        rows = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.family,
                      a.installed_on, s.label site
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.installed_source = ?
                 AND a.retired_on IS NULL
               ORDER BY a.installed_on DESC""",
            (account_id, SOLD_BY_US)).fetchall()

    out = []
    for r in rows:
        item = dict(r)
        try:
            from .cover import published_terms

            terms = published_terms(r["manufacturer"], r["model_number"])
            if terms and r["installed_on"]:
                years = terms.get("parts_years") or 0
                ends = (date.fromisoformat(r["installed_on"][:10])
                        + timedelta(days=int(365.25 * years)))
                item["cover_until"] = ends.isoformat()
                item["still_covered"] = ends >= date.today()
                item["terms"] = terms
        except Exception:
            pass
        out.append(item)

    # AND WHAT THEY HAVE BOUGHT BUT NOT RECEIVED YET.
    #
    # A machine only becomes an asset on delivery, so this listed nothing
    # until the van arrived. On a live call somebody bought four things in
    # twenty minutes, asked "what have I bought today", and the desk searched
    # the repair corpus six times and told them it could see no purchase
    # orders at all. It had four.
    #
    # The order stage was invisible to the one person who most wants to see
    # it. Same tool, whole life of a sale.
    ordered = []
    try:
        with db.connect() as c:
            for r in c.execute(
                    """SELECT po.id, po.status, po.subtotal, po.placed_at,
                              po.dealer_id,
                              GROUP_CONCAT(pl.description, '; ') items
                       FROM purchase_orders po
                       LEFT JOIN purchase_lines pl ON pl.po_id = po.id
                       LEFT JOIN deliveries d ON d.po_id = po.id
                       WHERE po.account_id = ? AND d.id IS NULL
                         AND po.status NOT IN ('cancelled')
                       GROUP BY po.id
                       ORDER BY po.placed_at DESC""", (account_id,)):
                ordered.append(dict(r))
    except Exception as e:
        print(f"[ownership] could not read open orders for {account_id}: "
              f"{type(e).__name__}: {e}", flush=True)

    waiting = [o for o in ordered if o["status"] != "draft"]
    drafts = [o for o in ordered if o["status"] == "draft"]

    say = ("Everything under machines we sold ourselves, so the dates are "
           "ours and the cover is ours to grant.")
    if waiting:
        say += (f" They also have {len(waiting)} order(s) placed and not "
                "delivered yet: read those back before anything else, "
                "because that is what somebody asking what they bought "
                "today means.")
    if drafts:
        say += (f" {len(drafts)} more is still a DRAFT and is not an order "
                "until they say yes. Do not call it placed.")

    return {"account_id": account_id, "count": len(out), "machines": out,
            "on_order": waiting, "drafts": drafts,
            "ordered_not_delivered": len(waiting),
            "say": say}
