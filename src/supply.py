"""Ordering from a supplier, which is the direction that was missing.

WHAT WAS THERE, AND WHY IT WAS ONLY HALF

`restock_advice` works out what to reorder, how many, and what a stockout
costs, from parts this dealer has actually consumed. It is careful work: the
cost of being short is a truck roll rather than the price of the part, which
is why a cheap part with a long lead time can be more urgent than an expensive
one.

Then it stopped. `purchase_orders` is the customer's side, where `account_id`
is who is buying from us. Nothing anywhere recorded this dealer ordering from
a supplier, so the advice was handed to a person and the system never learned
whether anything happened.

That made two completely different situations look identical from inside:

    we knew and did not order
    we ordered and it is late

The first is a process failure and the second is a supplier problem, and until
now the shelf ran empty the same way in both.

WHY WHAT WAS ADVISED IS KEPT NEXT TO WHAT WAS ORDERED

`advised_qty` is what the arithmetic said and `qty` is what somebody actually
bought. A buyer who consistently orders half the recommendation is either
wiser than the model or costing the company truck rolls, and there is no way
to tell which without both numbers side by side.

MACHINES ARE STOCKED FOR THE OPPOSITE REASON TO PARTS

Parts are held because a missing one fails a service call, so availability
beats cost efficiency. A machine is held at real capital cost against a sale
that may not come, so cost efficiency beats availability. Two tables, two
policies, because one of each would force the wrong answer on the other.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from . import db
from .tenancy import the_desk


def _nid() -> str:
    return f"SO-{uuid.uuid4().hex[:6].upper()}"


def place_supply_order(sku: str, qty: int, dealer_id: str = "",
                       advised_qty: int = 0, reason: str = "",
                       stockout_cost: float = 0.0,
                       supplier_id: str = "") -> dict:
    """Order a part from a supplier, and record why.

    Args:
        sku: the part.
        qty: how many are actually being ordered.
        dealer_id: whose shelf.
        advised_qty: what restock_advice recommended, kept so the two can be
            compared later.
        reason: why, in the same money the van loading uses.
        stockout_cost: what being short of this one costs.
        supplier_id: who from. Taken from the part if omitted.
    """
    dealer_id = the_desk(dealer_id)
    if qty <= 0:
        return {"ok": False, "why": "nothing to order"}

    with db.connect() as c:
        part = c.execute(
            """SELECT sku, name, unit_cost, lead_time_days, supplier_id
               FROM parts WHERE sku = ? AND dealer_id = ?""",
            (sku, dealer_id)).fetchone()
    if part is None:
        return {"ok": False, "why": "no such part on this dealer's catalogue"}

    supplier = supplier_id or part["supplier_id"]
    lead = part["lead_time_days"] or 0
    oid = _nid()
    now = datetime.now()

    with db.txn() as c:
        c.execute(
            """INSERT INTO supply_orders
               (id,dealer_id,supplier_id,sku,advised_qty,qty,unit_cost,reason,
                stockout_cost,status,placed_at,expected_at)
               VALUES (?,?,?,?,?,?,?,?,?,'placed',?,?)""",
            (oid, dealer_id, supplier, sku, advised_qty or None, qty,
             part["unit_cost"], reason or None, stockout_cost or None,
             now.isoformat(timespec="seconds"),
             (now + timedelta(days=lead)).isoformat(timespec="seconds")))

    return {"ok": True, "order": oid, "sku": sku, "name": part["name"],
            "qty": qty, "advised": advised_qty or None,
            "expected": (now + timedelta(days=lead)).date().isoformat(),
            "cost": round((part["unit_cost"] or 0) * qty, 2)}


def receive(order_id: str, qty: int = 0) -> dict:
    """Stock arrived. Puts it on the shelf and closes the order.

    Args:
        order_id: the supply order.
        qty: how many actually turned up, if it was short.
    """
    with db.connect() as c:
        o = c.execute("SELECT * FROM supply_orders WHERE id = ?",
                      (order_id,)).fetchone()
    if o is None:
        return {"ok": False, "why": "no such order"}
    if o["status"] == "received":
        return {"ok": True, "already": True, "order": order_id}

    got = qty or o["qty"]
    now = datetime.now().isoformat(timespec="seconds")

    with db.txn() as c:
        c.execute("""UPDATE supply_orders SET status='received', received_at=?
                     WHERE id=?""", (now, order_id))
        if o["sku"]:
            # Onto the main shelf rather than a van. A delivery arrives at the
            # counter, and which van carries it is a separate decision the van
            # loading already makes per visit.
            loc = c.execute(
                """SELECT id FROM stock_locations
                   WHERE dealer_id = ? AND kind <> 'van' ORDER BY id LIMIT 1""",
                (o["dealer_id"],)).fetchone()
            if loc is not None:
                c.execute(
                    """INSERT INTO stock (location_id,sku,on_hand)
                       VALUES (?,?,?)
                       ON CONFLICT(location_id,sku) DO UPDATE SET
                         on_hand = on_hand + excluded.on_hand""",
                    (loc["id"], o["sku"], got))

    return {"ok": True, "order": order_id, "sku": o["sku"], "received": got,
            "short_by": (o["qty"] - got) if got < o["qty"] else 0}


def on_order(dealer_id: str = "") -> dict:
    """What is coming, what is late, and what was never ordered at all.

    The distinction the missing table made impossible. A shelf that runs empty
    because nobody placed the order is a different problem from one that runs
    empty because a supplier is late, and they need different phone calls.
    """
    dealer_id = the_desk(dealer_id)
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as c:
        open_orders = c.execute(
            """SELECT o.*, p.name FROM supply_orders o
               LEFT JOIN parts p ON p.sku = o.sku
               WHERE o.dealer_id = ? AND o.status NOT IN ('received','cancelled')
               ORDER BY o.expected_at""", (dealer_id,)).fetchall()

    late = [o for o in open_orders if (o["expected_at"] or "") < now]
    return {
        "open": len(open_orders),
        "late": len(late),
        "coming": [{"order": o["id"], "sku": o["sku"], "name": o["name"],
                    "qty": o["qty"], "expected": (o["expected_at"] or "")[:10],
                    "late": (o["expected_at"] or "") < now}
                   for o in open_orders],
        "say": ("A part that is short and not on this list was never ordered, "
                "which is a different problem from a supplier being late and "
                "needs a different phone call."),
    }


def advised_but_not_ordered(dealer_id: str = "") -> list[dict]:
    """What the arithmetic asked for and nobody bought.

    The gap the missing table hid. Every row here is a truck roll the desk
    already priced and somebody quietly declined to prevent.
    """
    dealer_id = the_desk(dealer_id)
    from .restock import restock_advice

    advice = restock_advice(dealer_id)
    if not advice.get("ok", True):
        return []

    with db.connect() as c:
        pending = {r["sku"] for r in c.execute(
            """SELECT DISTINCT sku FROM supply_orders
               WHERE dealer_id = ? AND status NOT IN ('received','cancelled')""",
            (dealer_id,))}

    out = []
    for row in advice.get("order", []):
        if row["sku"] in pending:
            continue
        out.append({
            "sku": row["sku"], "name": row["name"],
            "advised": row.get("order_qty") or row.get("target"),
            "why": row.get("note") or row.get("why"),
            "say": "Advised and not ordered. Nothing is coming.",
        })
    return out


# --------------------------------------------------------------------------
# whole machines, which are stocked for the opposite reason to parts
# --------------------------------------------------------------------------


def _in_the_catalogue(manufacturer: str, model_number: str) -> bool:
    """Is this a real machine at all, or a mis-heard model number.

    Offering to order something that exists nowhere is worse than saying we
    do not carry it: the customer waits for a machine that was never made.
    """
    if not model_number:
        return False
    norm = model_number.upper().replace("-", "").replace(" ", "").replace("/", "")
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT 1 FROM equipment
                   WHERE brand LIKE ? AND model_norm = ? LIMIT 1""",
                (f"%{manufacturer}%", norm)).fetchone()
        return row is not None
    except Exception:
        return False


def _find_on_the_floor(dealer_id: str, manufacturer: str,
                      model_number: str = ""):
    """Find a machine the way a person would, not by exact string equality.

    OBSERVED ON A LIVE CALL. A caller was told a display cooler was not in
    stock, 21 day lead time, while eleven of them sat on the floor. The lookup
    was `manufacturer=? AND model_number=?`, both exact.

    Two things defeat that, and both are normal.

    THE DATA IS SPLIT WRONG. Product titles were divided at the first space,
    so the floor holds manufacturer='ESM-13R' model='1-Door Merchandiser
    Refrigerator', and manufacturer='Global' model='Industrial Nexel
    Merchandiser Refrigerator'. Global Industrial is the maker and ESM-13R is
    a model code sitting in the maker column. 24 of 212 rows look like this.

    THE AGENT DOES NOT KNOW WHICH HALF IS WHICH, and passed them the other way
    round on that call. It had no way to know: the data itself is ambiguous.

    So this matches on the WHOLE NAME rather than on the halves. Exact first,
    because when the fields are right that is the correct answer. Then the two
    inputs joined and compared against the two columns joined, in either
    order, which is what a person does when they read a label.
    """
    said = " ".join(x for x in (manufacturer or "", model_number or "") if x).strip()
    if not said:
        return None

    # A HANDLE BEATS EVERY KIND OF MATCHING, and it belongs HERE rather than
    # only in the ordering path.
    #
    # Heard live, minutes after the ordering path learned to read handles. The
    # desk offered a Serta mesh chair at $139.99 and said we had several. The
    # customer asked for it. The desk went to CHECK AVAILABILITY first, which
    # comes through this function, which did not understand handles -- so
    # STK-509 fell through to fuzzy matching, missed, and the customer was
    # told we did not stock it. Twelve were on the floor. They said:
    #
    #     "You just said it was in stock. Then you said it wasn't in stock."
    #
    # Teaching one caller to read handles was the mistake. Every lookup goes
    # through this one function, so this is where it has to be known.
    if "STK-" in said.upper():
        row = the_row_behind(said, dealer_id)
        if row is not None:
            return row

        # A HANDLE THAT DOES NOT RESOLVE IS AN ERROR, NOT A HINT.
        #
        # Heard on a live call. The desk was selling a $2,059 Koolmore
        # freezer and passed STK-412, which is a row belonging to the IT
        # company: an HPE server at $4,343. Scoped to the vendor, this
        # correctly found nothing -- and then fell through to the word
        # matching below, where the bare number "412" landed on an unrelated
        # refrigeration item, and the order was written at $15.95.
        #
        # Reading a handle loosely is the one thing a handle exists to
        # prevent. If it does not resolve, say so and let the desk look the
        # machine up by name.
        print(f"[supply] {said!r} carries a handle that is not ours; "
              "refusing to guess at a machine from it", flush=True)
        return None

    with db.connect() as c:
        if manufacturer and model_number:
            row = c.execute(
                """SELECT * FROM product_stock
                   WHERE dealer_id=? AND manufacturer=? AND model_number=?""",
                (dealer_id, manufacturer, model_number)).fetchone()
            if row is not None:
                return row
            # the same two words the other way round
            row = c.execute(
                """SELECT * FROM product_stock
                   WHERE dealer_id=? AND manufacturer=? AND model_number=?""",
                (dealer_id, model_number, manufacturer)).fetchone()
            if row is not None:
                return row

        # The whole name against the whole name. Prefer something we hold.
        row = c.execute(
            """SELECT * FROM product_stock
               WHERE dealer_id=?
                 AND LOWER(TRIM(manufacturer || ' ' || model_number)) =
                     LOWER(TRIM(?))
               ORDER BY on_hand DESC LIMIT 1""", (dealer_id, said)).fetchone()
        if row is not None:
            return row

        # Then a contains match, still preferring stock, so "the Beverage-Air
        # HR1HC" finds HR1HC***G******** the way a person reading a shelf does.
        row = c.execute(
            """SELECT * FROM product_stock
               WHERE dealer_id=?
                 AND (LOWER(manufacturer || ' ' || model_number) LIKE LOWER(?)
                      OR LOWER(?) LIKE LOWER(manufacturer || ' ' || model_number)
                      OR LOWER(model_number) LIKE LOWER(?)
                      OR LOWER(manufacturer) LIKE LOWER(?))
               ORDER BY on_hand DESC LIMIT 1""",
            (dealer_id, f"%{said}%", said, f"%{said}%", f"%{said}%")).fetchone()
        if row is not None:
            return row

        # LAST TIER: THE SAME MACHINE DESCRIBED IN DIFFERENT WORDS.
        #
        # Heard on a live call. The desk itself offered "15.5 cu ft single
        # glass door" out of its own catalogue, the customer said yes, and the
        # desk went back to look it up as
        #
        #     "15.5 cu ft Single Glass Door walk-in cooler"
        #
        # which is its own sentence with two words added. Every tier above is
        # a substring test, and a substring test cannot survive an added word.
        # So it missed, reported a 21 day lead on something sitting on the
        # floor, and then asked the CUSTOMER for the manufacturer and model
        # number of a machine we had just quoted them.
        #
        # 87 of our 923 rows carry a description and no maker at all, like
        # "12.5 Cu. Ft. Single Door". Those rows have nothing but their words,
        # so words are what has to match.
        return _by_the_words(c, dealer_id, said)


# Words that carry no information about WHICH machine this is. Unit words like
# cu and ft are deliberately NOT here: both sides say them, so they help.
_NOISE = {"the", "a", "an", "and", "or", "of", "for", "with", "about",
          "approximately", "around", "some", "that", "this", "one", "it",
          "please", "would", "like", "want", "need"}


def _words(text: str) -> tuple[set[str], set[str]]:
    """Split into meaningful words and, separately, the numbers.

    Numbers are kept apart because they are the whole difference between a
    12.5 and a 15.5 cubic foot cooler, and treating them as just more words
    lets the wrong one win on the strength of "cu ft single door".
    """
    flat = re.sub(r"[^a-z0-9.]+", " ", (text or "").lower())
    parts = [w.strip(".") for w in flat.split() if w.strip(".")]
    nums = {w for w in parts if any(ch.isdigit() for ch in w)}
    words = {w for w in parts if w not in nums and w not in _NOISE and len(w) > 1}
    return words, nums


def _by_the_words(c, dealer_id: str, said: str):
    """The best row whose own name was substantially spoken.

    Scored on how much of the ROW was said, not how much of what was said
    matched the row: the caller adds words ("walk-in cooler", "for the back
    room") and those extra words must not count against anything.
    """
    want, want_nums = _words(said)
    if len(want) + len(want_nums) < 2:
        return None

    best, best_score = None, 0.0
    for r in c.execute(
            """SELECT * FROM product_stock WHERE dealer_id = ?""", (dealer_id,)):
        have, have_nums = _words(f"{r['manufacturer'] or ''} {r['model_number'] or ''}")
        if not have and not have_nums:
            continue

        # A ROW WITH A NUMBER IN ITS NAME MUST HAVE THAT NUMBER SAID. This is
        # the rule that stops "15.5 cu ft single glass door" from cheerfully
        # returning the 12.5, which shares every other word it has.
        if have_nums and not (have_nums & want_nums):
            continue

        shared = len(have & want) + len(have_nums & want_nums)
        score = shared / float(len(have) + len(have_nums))
        if score < 0.7 or shared < 2:
            continue

        # Ties go to what is actually on the floor: of two equally good
        # readings, the one we can hand over today is the better answer.
        rank = (round(score, 3), 1 if (r["on_hand"] or 0) > 0 else 0)
        if rank > (round(best_score, 3), 1 if best is not None
                   and (best["on_hand"] or 0) > 0 else 0):
            best, best_score = r, score

    return best


def product_availability(manufacturer: str, model_number: str = "",
                         dealer_id: str = "") -> dict:
    """Whether we actually have a machine, and how long if not.

    The desk could recommend a Traulsen over a Beverage-Air, weigh their
    running costs from federal data and quote the delivery, and had no way to
    answer whether one was in the building. Its own hard rule is never to say
    something is available unless a tool said so, and for machines no tool
    could say anything at all.

    Args:
        manufacturer: the make.
        model_number: the model, if they have it.
        dealer_id: whose shelf.
    """
    dealer_id = the_desk(dealer_id)
    row = _find_on_the_floor(dealer_id, manufacturer, model_number)

    if row is None:
        # NOT ON OUR PRICE LIST IS NOT A DEAD END.
        #
        # This used to say "we do not carry it, offer to price it in", which a
        # customer hears as no. On a live call somebody asked to buy a machine
        # three times and was told we do not stock it three times, because
        # this instruction fought the rule that says we do not have to hold
        # something to sell it. The tool won, and the order was never taken.
        #
        # We can order almost anything in. What we must not do is invent a
        # lead time, so the figure comes from what the thing actually is,
        # which is the same reasoning the back-to-back sourcing uses.
        from .backorder import _lead_days

        days, why = _lead_days(f"{manufacturer} {model_number}", True)
        real = _in_the_catalogue(manufacturer, model_number)

        return {
            "stocked": False,
            "can_order": True,
            "manufacturer": manufacturer, "model": model_number,
            "lead_time_days": days,
            "in_certification_catalogue": real,
            "why": "not on our own price list, but it can be ordered in",
            "say": (
                "Do NOT stop at 'we do not stock it'. Say we do not keep it on "
                f"the floor and that we can order it in, about {days} days. "
                "Then RAISE THE ORDER YOURSELF with create_purchase_order: "
                "it drafts it, you read the lines and the total back, and "
                "confirm_purchase_order places it once they agree. "
                "Confirming raises the supply order against it "
                "automatically. Do not ask whether they would like a "
                "quote first. They asked to buy it.\n"
                + ("" if real else
                   "We cannot find that model in the certification catalogue "
                   "either, so read the model number back to them and check "
                   "it before promising anything.")),
        }

    return {
        "stocked": True,
        "manufacturer": row["manufacturer"], "model": row["model_number"],
        "on_hand": row["on_hand"], "on_order": row["on_order"],
        "lead_time_days": row["lead_time_days"],
        "price": row["list_price"],
        "say": (f"{row['on_hand']} in stock." if row["on_hand"] else
                f"None in stock. {row['lead_time_days']} days from the "
                f"supplier, and {row['on_order']} already on order."
                if row["on_order"] else
                f"None in stock, {row['lead_time_days']} days to get one."),
    }



def the_row_behind(ref: str, dealer_id: str = ""):
    """The exact row a quote came from, by the handle that quote carried.

    WHY THIS EXISTS AT ALL.

    Every tool that shows a machine to the desk had been showing WORDS, and
    every tool that acts on one had been taking WORDS. So the desk would find
    a row, read its price down the phone, and then, when the customer said
    yes, throw the row away and go looking for it again by whatever phrase it
    happened to use the second time.

    On a live call it offered a cooler out of its own catalogue, the customer
    agreed, and it searched for

        "15.5 cu ft Single Glass Door walk-in cooler"

    which is its own sentence with two words added. It missed, decided we did
    not stock the thing it had just quoted, quoted a 21 day lead instead, and
    then asked the CUSTOMER to confirm the manufacturer and model number of a
    machine that was sitting on our floor.

    None of that is a matching problem. Matching was never supposed to happen
    twice. The row was in its hand and the shape of the tools made it let go.

    So a quote now carries a handle, and ordering against that handle reads
    the row back with no searching of any kind.
    """
    # PULLED OUT OF WHATEVER IT IS SITTING IN, rather than required to be the
    # whole string. Observed live: the desk passed "I want to order the Dell
    # XPS 14 Laptop, model STK-367." and this refused it, because "367." is
    # not a number once the sentence's full stop is attached. The handle was
    # carried correctly the entire way and was thrown out at the last step by
    # one character of punctuation, and the customer was told we had no price
    # for a machine listed at $2,099.99.
    found = re.search(r"STK-(\d+)", (ref or "").upper())
    if not found:
        return None
    n = found.group(1)

    from .tenancy import the_desk

    with db.connect() as c:
        # Scoped to the vendor even though the handle is unique, because a
        # handle that reaches across companies is a handle somebody can guess
        # their way across companies with.
        return c.execute(
            "SELECT rowid, * FROM product_stock WHERE rowid = ? AND dealer_id = ?",
            (int(n), the_desk(dealer_id))).fetchone()


def _shown(r) -> dict:
    """One stock row as the desk should see it: with its handle attached."""
    d = {k: r[k] for k in r.keys() if k != "rowid"}
    d["ref"] = f"STK-{r['rowid']}"
    d["order_by"] = "Order this by its ref. Do not describe it again."
    return d


def _who_else_carries(family: str, budget: float, at_least: float) -> str:
    """Which of our other books stocks this, if the current one does not.

    Only ever OUR books. This moves a call between suppliers that already sit
    behind one number; it is not a lookup of what other shops have, and it can
    never reach outside the group.

    Returns the company with the most of them, or nothing. Most, rather than
    cheapest or nearest, because the question being answered is "who actually
    sells these" and a single stray row is a filing error rather than a range.
    """
    big = [w for w in re.split(r"[^a-z0-9]+", (family or "").lower())
           if len(w) > 3]
    if not big:
        return ""
    clause = " OR ".join(["LOWER(family) LIKE ?"] * len(big))
    params = [f"%{w}%" for w in big]

    where = f"list_price IS NOT NULL AND ({clause})"
    if budget and budget > 0:
        where += " AND list_price <= ?"
        params.append(budget)
    if at_least and at_least > 0:
        where += " AND list_price >= ?"
        params.append(at_least)

    try:
        with db.connect() as c:
            row = c.execute(
                f"""SELECT dealer_id, COUNT(*) n FROM product_stock
                    WHERE {where}
                    GROUP BY dealer_id ORDER BY n DESC LIMIT 1""",
                tuple(params)).fetchone()
        if row and row["n"]:
            return row["dealer_id"]
    except Exception as e:
        print(f"[supply] could not check the other books for {family!r}: "
              f"{type(e).__name__}: {e}", flush=True)
        return ""

    # NOBODY'S FAMILY CONTAINS THAT WORD, WHICH IS NOT THE SAME AS NOBODY
    # SELLING IT.
    #
    # We call them reach-in coolers and display coolers. A caller says
    # refrigerator. Asked on the IT desk, the word check finds no book with
    # "refrigerator" in a family name and gives up, so a customer wanting a
    # commercial fridge is told nothing matches -- while a floor full of them
    # sits on the refrigeration book.
    #
    # Same reason meaning.py exists at all, applied across the books rather
    # than within one. It refuses to choose between two close families, so a
    # phrase that genuinely sits between two kinds of machine moves nowhere
    # and the desk asks, which is right.
    try:
        from .meaning import closest_family

        with db.connect() as c:
            owners = {}
            for r in c.execute(
                    """SELECT DISTINCT dealer_id, family FROM product_stock
                       WHERE family IS NOT NULL AND family != ''
                         AND list_price IS NOT NULL"""):
                owners.setdefault(r["family"], r["dealer_id"])

        near = closest_family(family, list(owners))
        if near.get("family"):
            print(f"[supply] {family!r} reads as {near['family']!r} "
                  f"({near['score']}) on {owners[near['family']]}'s book",
                  flush=True)
            return owners[near["family"]]
        if near.get("ambiguous"):
            # SITTING BETWEEN TWO KINDS OF MACHINE IS NOT THE SAME AS US NOT
            # HAVING ANY. "refrigerator" reads as both display cooler and
            # reach-in cooler, and refusing to choose is right -- they are
            # different machines at different prices. What was wrong was what
            # came next: the desk reported "we have nothing", when the honest
            # answer is to ask which one they mean.
            return "ASK:" + "|".join(near["ambiguous"])
    except Exception as e:
        print(f"[supply] could not match {family!r} by meaning across the "
              f"books: {type(e).__name__}: {e}", flush=True)
    return ""


def options_under(budget: float = 0.0, family: str = "", at_least: float = 0.0,
                  dearest_first: bool = False,
                  tool_context=None, dealer_id: str = "",
                  _already_moved: bool = False) -> dict:
    """What we can actually sell them for the money they have.

    THE GAP THIS FILLS. On a live call somebody said a five and a half
    thousand dollar freezer was too dear and asked for something cheaper.
    Nothing could answer that: product_availability takes a brand or a model,
    and there was no way to ask what we hold under a price. So the desk asked
    whether they would like it to look four separate times, looked up three
    brands one at a time, and never gave them a list.

    Asking a customer permission to do the thing they just asked for is worse
    than saying no. They have already said yes by asking.

    IT ASKED THE WRONG VENDOR, AND GOT AWAY WITH IT FOR A WHILE.

    The vendor was a default argument reading `dealer_id: str = ""`,
    while every other tool on this desk resolves it from the live call. That
    was invisible for as long as one vendor happened to hold everything,
    including the IT catalogue, which was itself a filing error.

    Correcting the filing broke this immediately: laptops moved to D-IT, this
    kept asking D-REF, and a caller with two thousand dollars was told there
    was nothing. Two faults that had been cancelling each other out.

    A default that silently names one tenant in a multi-tenant system is not a
    convenience. It is a wrong answer waiting for the data to be right.

    Args:
        budget: the most they want to spend.
        family: reach-in freezer, walk-in cooler, laptop. Optional.
        at_least: the LEAST they want to spend. Use it when somebody asks for
            something dearer, or for anything above a figure.
        dearest_first: put the most expensive first and ignore what is on the
            floor. Use it when somebody asks for your best one.
        dealer_id: override, for callers with no live call to read.

    THERE WAS NO WAY TO ASK FOR THE TOP OF THE RANGE, AND IT COST A SALE.

    Every stock tool here took a CEILING and nothing took a floor. On a live
    call somebody said they were looking to spend more than two thousand
    dollars on a laptop. The desk asked for everything under $999,999, which
    is the right question with the only tool it had, and got back the five
    best FOR the money: in stock first, so five cheap laptops. It concluded,
    out loud, "I am not seeing any laptops in our system above $2000."

    We had two, a ThinkPad X1 Carbon at $2,159.75 and a Dell XPS 14 at
    $2,099.99. Both sat at zero on hand, which is not the same as not existing
    and is exactly what a purchase order is for.

    It then fell through to the market and offered the customer a Dell XPS 14
    from another retailer at $1,940. Our own machine, quoted from somebody
    else's shelf, on our own phone line.

    A customer who says they want to spend MORE is the easiest sale there is,
    and this was the one question the desk could not ask.
    """
    dealer_id = the_desk(dealer_id)

    # NO CEILING IS A REAL QUESTION, AND budget WAS REQUIRED.
    #
    # "anything above two thousand" has a floor and no ceiling. The model
    # passed at_least and left budget out, which is exactly right, and a
    # required positional argument turned that into
    #
    #     TypeError: options_under() missing 1 required positional argument
    #
    # so the desk told the customer "I am not able to look that up right now"
    # about a question it could answer perfectly.
    #
    # Same shape as next_available_slot: an argument the model cannot always
    # supply must not be the thing that stops it calling the tool at all.
    if budget <= 0:
        if at_least > 0 or family:
            budget = 10_000_000.0        # no ceiling, which is what they said
        else:
            return {"ok": False,
                    "why": "no budget and nothing else to go on",
                    "say": "Ask what they are looking for or what they want "
                           "to spend. Do not tell them the lookup failed."}

    if not dealer_id:
        from .tools import _dealer

        dealer_id = _dealer(tool_context)

    where = "dealer_id = ? AND list_price IS NOT NULL AND list_price <= ?"
    params: list = [dealer_id, budget]
    if at_least and at_least > 0:
        where += " AND list_price >= ?"
        params.append(at_least)
    if family:
        # NOT `family = ?`. HEARD ON A LIVE CALL.
        #
        # A caller asked for a gaming laptop under five thousand dollars. Our
        # families are laptop, desktop, monitor, printer, server, headset, ups
        # -- there is no row whose family is literally "gaming laptop". So the
        # exact match returned nothing, the desk read that as "nothing in
        # budget", raised the budget to seven thousand, found nothing again,
        # raised it to ten, and went round three times while a floor full of
        # laptops sat there.
        #
        # People do not speak in our column values. They say gaming laptop,
        # commercial freezer, walk-in cooler. Either side containing the other
        # is what a person means.
        where += " AND (LOWER(family) LIKE LOWER(?) OR LOWER(?) LIKE '%' || LOWER(family) || '%')"
        params += [f"%{family}%", family]

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT rowid, manufacturer, model_number, family, on_hand,
                       list_price, lead_time_days
                FROM product_stock WHERE {where}
                -- Best FOR the money, which is the dearest thing under the
                -- ceiling, not the cheapest. Somebody with two thousand
                -- dollars wants the best machine two thousand dollars buys,
                -- and being shown the cheapest reads as being fobbed off.
                -- What is on the floor still comes first: available beats
                -- slightly better and three weeks away.
                --
                -- UNLESS THEY ASKED FOR THE TOP. Then stock is the wrong
                -- first sort, because it buries everything dear behind five
                -- cheap things we happen to hold, and the desk reports that
                -- the dear ones do not exist.
                {"ORDER BY list_price DESC, on_hand DESC"
                 if (dearest_first or at_least)
                 else "ORDER BY on_hand DESC, list_price DESC"} LIMIT 5""",
            tuple(params)).fetchall()

        # What the cheapest thing IS, when nothing fits. Telling somebody we
        # have nothing in budget and stopping there leaves them with no next
        # move; telling them the nearest one lets them decide.
        # THE CHEAPEST ONE, FOUND THE SAME WAY THE MAIN SEARCH FINDS THINGS.
        #
        # This used the strict family clause while the search above had a
        # widened one, so a caller asking for a "reach-in refrigerator" -- a
        # perfectly ordinary way to say it, and not one of our family names --
        # was told we had nothing under their budget AND could not be told
        # what the cheapest was, because the fallback matched nothing either.
        #
        # The whole point of this row is that somebody who cannot afford
        # anything still leaves the call knowing a number.
        big = [w for w in re.split(r"[^a-z0-9]+", (family or "").lower())
               if len(w) > 3]
        if family and big:
            clause = " OR ".join(["LOWER(family) LIKE ?"] * len(big))
            nearest = c.execute(
                f"""SELECT rowid, manufacturer, model_number, family, on_hand,
                           list_price
                    FROM product_stock
                    WHERE dealer_id = ? AND list_price IS NOT NULL
                      AND ({clause})
                    ORDER BY list_price ASC LIMIT 1""",
                tuple([dealer_id] + [f"%{w}%" for w in big])).fetchone()
        else:
            nearest = c.execute(
                """SELECT rowid, manufacturer, model_number, family, on_hand,
                          list_price
                   FROM product_stock
                   WHERE dealer_id = ? AND list_price IS NOT NULL
                   ORDER BY list_price ASC LIMIT 1""",
                (dealer_id,)).fetchone()

    # ANOTHER OF OUR OWN BOOKS CARRIES IT, AND WE ARE ABOUT TO SAY WE DO NOT.
    #
    # HEARD ON A LIVE CALL. The caller asked for electronics, routing picked
    # the audio-visual company, and the desk correctly listed everything the
    # group sells -- headsets among them, because we do sell headsets. They
    # asked for a headset. This looked on the AV floor, found none, and the
    # desk said "I'm not seeing any headsets in our system", two turns after
    # saying we carry them. Both statements were true and the pair is a lie.
    #
    # BEFORE the meaning fallback, deliberately. An EXACT family match on
    # another of our books is far better evidence than a fuzzy match on this
    # one: asked for a headset, "we have projectors, which are sort of
    # similar" is not an answer and moving the call is.
    #
    # Done in code because "notice you have moved between suppliers" is
    # exactly what a model does not notice mid-sentence. The caller never
    # hears about it: from their side there is one desk and it just answered.
    if not rows and family and not _already_moved:
        moved = _who_else_carries(family, budget, at_least)
        if moved.startswith("ASK:"):
            a, b = (moved[4:].split("|") + ["", ""])[:2]
            return {"ok": True, "options": [], "between": [a, b],
                    "say": f"They could mean a {a} or a {b}. Ask which, in "
                           "their words. Do NOT say we have none: we carry "
                           "both, and they are different machines at "
                           "different prices."}
        if moved and moved != dealer_id:
            print(f"[supply] {family!r} is not on {dealer_id}'s floor but is "
                  f"on {moved}'s; moving this call across", flush=True)
            from .tenancy import routed_to

            routed_to(moved)
            return options_under(budget=budget, family=family,
                                 at_least=at_least,
                                 dearest_first=dearest_first,
                                 tool_context=tool_context, dealer_id=moved,
                                 _already_moved=True)

    if not rows and family:
        # STILL NOTHING AFTER THE WORDS, SO TRY THE MEANING.
        #
        # "refrigerator", "a fridge for drinks", "somewhere to sit" share no
        # word with any family we sell, so every match above fails and the
        # desk truthfully reports we have nothing -- then offers the customer
        # another retailer's stock. That happened on live calls.
        #
        # Embeddings answer the question the words cannot. And when the phrase
        # sits between two families, this says so rather than picking: a
        # refrigerator really could be a display cooler or a reach-in, and the
        # right move is the one a good salesperson makes, which is to ask.
        try:
            from .meaning import closest_family

            with db.connect() as c:
                known = [r[0] for r in c.execute(
                    """SELECT DISTINCT family FROM product_stock
                       WHERE dealer_id = ? AND family IS NOT NULL
                         AND family != ''""", (dealer_id,))]

            near = closest_family(family, known)
            if near.get("family"):
                print(f"[supply] {family!r} reads as {near['family']!r} "
                      f"({near['score']})", flush=True)
                family = near["family"]
                where = ("dealer_id = ? AND list_price IS NOT NULL "
                         "AND list_price <= ? AND family = ?")
                params = [dealer_id, budget, family]
                if at_least and at_least > 0:
                    where += " AND list_price >= ?"
                    params.append(at_least)
                with db.connect() as c:
                    rows = c.execute(
                        f"""SELECT rowid, manufacturer, model_number, family,
                                   on_hand, list_price, lead_time_days
                            FROM product_stock WHERE {where}
                            ORDER BY list_price DESC LIMIT 5""",
                        tuple(params)).fetchall()
            elif near.get("ambiguous"):
                a, b = near["ambiguous"]
                return {"ok": True, "options": [],
                        "between": near["ambiguous"],
                        "say": f"They could mean a {a} or a {b}. Ask which, "
                               "in their words, and do not guess: those are "
                               "different machines at different prices."}
        except Exception as e:
            print(f"[supply] meaning lookup failed, using words only: "
                  f"{type(e).__name__}: {e}", flush=True)

    if not rows:
        # STILL NOTHING, so widen to the head noun and no further.
        #
        # "commercial freezer" contains none of our family names and none of
        # them contain it, so the tier above cannot see that a reach-in
        # freezer is what they are asking for. Any shared word longer than
        # three letters gets us there.
        #
        # This is deliberately the SECOND attempt and not the first. Widened
        # from the start, "walk-in cooler" would also return reach-in coolers,
        # which are a different machine at a different price, and a customer
        # who asked for a walk-in would be read four wrong quotes. Narrow
        # first, widen only when narrow found nothing at all.
        big = [w for w in re.split(r"[^a-z0-9]+", (family or "").lower())
               if len(w) > 3]
        if big:
            clause = " OR ".join(["LOWER(family) LIKE ?"] * len(big))
            with db.connect() as c:
                rows = c.execute(
                    f"""SELECT rowid, manufacturer, model_number, family,
                               on_hand, list_price, lead_time_days
                        FROM product_stock
                        WHERE dealer_id = ? AND list_price IS NOT NULL
                          AND list_price <= ? AND ({clause})
                        ORDER BY on_hand DESC, list_price DESC LIMIT 5""",
                    tuple([dealer_id, budget] + [f"%{w}%" for w in big])
                ).fetchall()

    if rows:
        # NUMBERED, AND HELD TO THE NUMBERS.
        #
        # This used to return five and tell the desk to read out "the two or
        # three cheapest", so what the customer heard as a list of three was a
        # subset of a list of five and the two were never the same. Asked for
        # "the third one", the model had no anchored list to count down and
        # ordered a chair from an earlier search that had never been said out
        # loud. Registering the list makes the positions mean something.
        from .shortlist import we_offered

        options = we_offered([_shown(r) for r in rows])
        return {
            "ok": True,
            "budget": budget,
            "options": options,
            "say": "Read these out IN THIS ORDER with their prices, and say "
                   "which are on the floor now. If they pick one by position "
                   "-- the second one, the last one -- it is the one with "
                   "that number here, and nothing from any earlier search. "
                   "Do not ask whether they would like you to look: they "
                   "already asked.",
        }

    return {
        "ok": True,
        "budget": budget,
        "options": [],
        "nearest": _shown(nearest) if nearest else None,
        "say": (
            f"We have nothing at or under ${budget:,.0f}"
            + (f". The cheapest we carry is the {nearest['manufacturer']} "
               f"{nearest['model_number']} at ${nearest['list_price']:,.0f}."
               if nearest else ".")
            + " Say the number plainly and let them decide. Do not keep "
              "asking whether they want you to look at other brands: you have "
              "looked at all of them, and asking a third time sounds like "
              "stalling."),
    }
