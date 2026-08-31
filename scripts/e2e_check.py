"""Drive a whole sale, end to end, the way a call does. No model, no phone.

WHY THIS EXISTS

Every fault in the last three hours was plumbing, not judgement: a ref dropped
by a full stop, a routing variable that does not cross a thread, a price list
searched twice, a rule that reached the front agent and not the one taking the
order. All of it is deterministic, and all of it was found by a person on a
live call reading symptoms back down the phone.

This walks the same path the tools walk, for every company, and says which
step broke. Run it before deploying:

    python -m scripts.e2e_check
"""

from __future__ import annotations

import sys

from src import db, tenancy, trace

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"    {'ok  ' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(f"{name} {detail}".strip())


def a_customer(dealer: str) -> tuple[str, str]:
    with db.connect() as c:
        r = c.execute(
            """SELECT a.id, s.id site FROM accounts a
               JOIN sites s ON s.account_id = a.id
               WHERE a.dealer_id = ? LIMIT 1""", (dealer,)).fetchone()
    return (r["id"], r["site"]) if r else ("", "")


def sell(dealer: str, family: str, want_in_stock: bool) -> None:
    """Quote something, order it by its ref, and check the price survived."""
    from src import buying, supply

    tenancy.routed_to(dealer, f"E2E-{dealer}")
    label = "in stock" if want_in_stock else "not in stock"

    opts = supply.options_under(999999, family,
                                dearest_first=not want_in_stock)
    rows = [o for o in (opts.get("options") or [])
            if (o["on_hand"] or 0) > 0] if want_in_stock else \
           [o for o in (opts.get("options") or []) if not (o["on_hand"] or 0)]
    if not rows:
        rows = opts.get("options") or []
    if not rows:
        check(f"{dealer} {family} {label}: anything to sell", False, "no options")
        return

    it = rows[0]
    check(f"{dealer} {family} {label}: quote carries a ref",
          bool(it.get("ref")), str(it.get("ref")))
    quoted = it["list_price"]

    account, site = a_customer(dealer)
    if not account:
        check(f"{dealer}: has a customer to sell to", False)
        return

    # ORDERED THE WAY THE AGENT ORDERS IT: the ref inside a sentence, with the
    # punctuation that broke it live.
    said = f"I want to order the {it['model_number']}, model {it['ref']}."
    po = buying.create_purchase_order(account, [said], site_id=site)
    check(f"{dealer} {family} {label}: order raised", bool(po.get("ok")),
          str(po.get("why") or po.get("purchase_order") or ""))
    if not po.get("ok"):
        return

    with db.connect() as c:
        line = c.execute(
            "SELECT description, unit_price FROM purchase_lines WHERE po_id=?",
            (po["purchase_order"],)).fetchone()
        head = c.execute("SELECT dealer_id FROM purchase_orders WHERE id=?",
                         (po["purchase_order"],)).fetchone()

    check(f"{dealer} {family} {label}: line is priced",
          line["unit_price"] is not None,
          f"got {line['unit_price']}")
    check(f"{dealer} {family} {label}: price matches the quote",
          line["unit_price"] == quoted,
          f"quoted {quoted}, wrote {line['unit_price']}")
    check(f"{dealer} {family} {label}: filed under the right company",
          head["dealer_id"] == dealer, f"got {head['dealer_id']}")
    check(f"{dealer} {family} {label}: description is the machine, not the ref",
          "STK-" not in (line["description"] or ""), line["description"])

    conf = buying.confirm_purchase_order(po["purchase_order"])
    check(f"{dealer} {family} {label}: order confirms", bool(conf.get("ok")),
          str(conf.get("why") or ""))


def off_thread_routing() -> None:
    """The bug that made a laptop invisible: routing must cross a thread."""
    import concurrent.futures as f

    tenancy.routed_to("D-IT", "E2E-THREAD")
    with f.ThreadPoolExecutor(1) as ex:
        got = ex.submit(tenancy.the_desk).result()
    check("routing survives a worker thread", got == "D-IT", f"got {got}")

    # RE-ROUTING MID-CALL, WHICH IS WHAT EVERY CALL DOES.
    #
    # A call opens on the number that was dialled and is re-routed the moment
    # the caller says what they want. The re-route used to move only the
    # context variable, so a worker thread still read the DIALLED company: a
    # laptop enquiry answered by the refrigeration desk, which correctly
    # reported it sells no laptops and offered the customer another
    # retailer's instead. Forty seconds after quoting them our own.
    tenancy.call_started("E2E-REROUTE", "D-REF")
    trace.call_context("E2E-REROUTE")
    tenancy.routed_to("D-IT")
    with f.ThreadPoolExecutor(1) as ex:
        after = ex.submit(tenancy.the_desk).result()
    check("a re-route reaches the worker thread too", after == "D-IT",
          f"got {after}")
    tenancy.call_ended("E2E-REROUTE")
    tenancy.call_ended("E2E-THREAD")


def complaint(dealer: str) -> None:
    """The half nobody has tested: somebody rings up with a fault."""
    from src import tools

    tenancy.routed_to(dealer, f"E2E-C-{dealer}")
    with db.connect() as c:
        asset = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number FROM assets a
               JOIN sites s ON s.id = a.site_id
               JOIN accounts ac ON ac.id = s.account_id
               WHERE ac.dealer_id = ? LIMIT 1""", (dealer,)).fetchone()
    if asset is None:
        check(f"{dealer}: has a machine to complain about", False)
        return

    class Ctx:
        def __init__(self, d):
            self.state = {"dealer_id": d, "caller_phone": "+15550001111"}

    try:
        wo = tools.open_work_order(asset["id"], "not cooling, water on the floor",
                                   Ctx(dealer))
    except Exception as e:
        check(f"{dealer} complaint: work order opens", False,
              f"{type(e).__name__}: {e}")
        return
    check(f"{dealer} complaint: work order opens", bool(wo.get("ok")),
          str(wo.get("why") or wo.get("work_order") or wo.get("id") or ""))



def seams() -> None:
    """The joins between parts, which is where every fault today actually was.

    None of these were caught by the 1110 unit tests, and all of them were
    caught by a person on the phone. The tests call functions with clean
    arguments: `the_desk("D-IT")`, `_price_the_line(c, "STK-367")`. Every
    fault lived in the handover instead -- across a thread, across a sub-agent
    boundary, or across the gap between a sentence and an identifier.
    """
    from src import agents, buying, supply
    from src.language import _really_that_language

    # A HANDLE INSIDE A SENTENCE. The desk says "model STK-367." and the full
    # stop made "367." not a number, so the handle was thrown away one step
    # from working and the customer was told we had no price.
    tenancy.routed_to("D-IT", "E2E-SEAM")
    with db.connect() as c:
        for said in ("STK-366", "model STK-366.", "the (STK-366) one",
                     "I want to order the ThinkPad, model STK-366."):
            _, _, price, _ = buying._price_the_line(c, said)
            check(f"ref survives: {said[:34]!r}", price is not None,
                  f"got {price}")

    # THE SAME HANDLE, THROUGH THE LOOKUP PATH RATHER THAN THE ORDER PATH.
    # The desk checks availability before it orders, and that goes through a
    # different function. Teaching only the order path to read handles meant
    # a chair with twelve on the floor was reported out of stock.
    for dealer, family in (("D-FURN", "chair"), ("D-IT", "laptop"),
                           ("D-REF", "walk-in cooler"), ("D-AV", "projector")):
        tenancy.routed_to(dealer, "E2E-SEAM")
        opts = supply.options_under(999999, family)
        held = [o for o in (opts.get("options") or []) if (o["on_hand"] or 0) > 0]
        if not held:
            continue
        it = held[0]
        said = f"{it['model_number']}, model {it['ref']}."
        row = supply._find_on_the_floor(dealer, said)
        check(f"{dealer}: a handle is readable by the lookup, not just the order",
              row is not None and (row["on_hand"] or 0) > 0,
              f"{it['ref']} -> {'found' if row else 'NOT FOUND'}")
    tenancy.routed_to("D-IT", "E2E-SEAM")

    # AN ORDER NAMED IN PROSE. The customer said confirm, the desk called
    # confirm with a description and no PO number, and the order stayed a
    # draft while they rang off believing they had bought a laptop.
    # A REAL order number is lifted out of a sentence; an invented one is
    # discarded, because shape is not existence. Both halves matter: the
    # first lost a confirmed sale to a wrong argument name, the second wrote
    # "PO-1234" onto a customer's order.
    with db.connect() as c:
        real = c.execute(
            "SELECT id FROM purchase_orders ORDER BY placed_at DESC LIMIT 1"
        ).fetchone()
    if real:
        got = buying._which_draft(f"confirm {real['id']} for the laptop")
        check("a real order number is found inside a sentence",
              got == real["id"], got)
    made_up = buying._which_draft("confirm PO-ABC123 for the laptop")
    check("an invented order number is not acted on",
          made_up != "PO-ABC123", made_up)

    # A LEAD TIME FOR A MACHINE. quote_delivery only knew the parts table, so
    # a machine came back with a lead time of zero and got quoted same-day
    # delivery in the same breath as a 21 day lead.
    o = supply.options_under(999999, "laptop", at_least=2000)
    if o.get("options"):
        q = buying.quote_delivery(o["options"][0]["ref"])
        soon = (q.get("options") or [{}])[0].get("arrives", "")
        check("an out-of-stock machine is not quoted for today",
              q["supplier_lead_days"] > 0 and soon > str(__import__("datetime").date.today()),
              f"lead {q['supplier_lead_days']}d, soonest {soon}")

    # A RULE THAT REACHED ONE AGENT. "Never ask for a model number" was on the
    # front agent and not on `supply`, which is the one that took the order
    # and asked.
    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": "D-IT"}

    for name in sorted(n for n in dir(agents) if n.endswith("_agent")):
        a = getattr(agents, name)
        if not hasattr(a, "instruction"):
            continue
        t = a.instruction(Ctx()) if callable(a.instruction) else str(a.instruction)
        check(f"{name} carries the never-ask rule",
              ("OURS TO KNOW" in t) or ("NEVER ASK THE CUSTOMER" in t))

    # A LANGUAGE SWITCHED ON A MISHEARD WORD. The caller said one token the
    # transcriber rendered as "jaye" and the whole call turned Arabic.
    for code, heard, want_allowed in (
            ("ar", "jaye", False),
            ("ar", "How will I confirm it to you?", False),
            ("ar", "Can you speak Arabic please", True),
            ("es", "I want to order that laptop for you", False)):
        allowed = _really_that_language(code, heard) is None
        check(f"language {code} on {heard[:26]!r}", allowed == want_allowed)

    tenancy.call_ended("E2E-SEAM")



def the_whole_life_of_a_machine(dealer: str, family: str) -> None:
    """Buy it, deliver it, break it, and get somebody sent out. For real.

    WHY THIS RUNS FOR EVERY COMPANY

    Each of these steps was tested on its own and passed, and the chain was
    broken anyway: `carrier_delivered` never called `becomes_theirs`, so a
    delivered machine never reached the customer's account, so a later fault
    call could not find it. Nothing raised. Every part worked. Nobody had
    walked the whole thing.

    And it was found on ONE company with ONE product. Proving a laptop works
    for the IT desk says nothing about a chair, a projector or a walk-in
    cooler: the families differ, the engineers differ, and the qualification
    and working-hours data dispatch depends on was missing for two of the four
    companies while the other two were fine.
    """
    from src import buying, scheduling, supply, tools
    from src.delivery import carrier_delivered

    tenancy.routed_to(dealer, "LIFE-" + dealer)
    trace.call_context("LIFE-" + dealer)
    label = dealer + " " + family

    account, site = a_customer(dealer)
    if not account:
        check(label + ": has a customer", False)
        return

    rows = supply.options_under(999999, family).get("options") or []
    if not rows:
        check(label + ": has something to sell", False)
        return
    it = rows[0]

    po = buying.create_purchase_order(account, [it["ref"]], site_id=site)
    if not po.get("ok"):
        check(label + ": order raised", False, str(po.get("why")))
        return
    buying.confirm_purchase_order(po["purchase_order"])

    landed = carrier_delivered(po["purchase_order"], carrier="UPS")
    theirs = (landed.get("now_theirs") or []) + (landed.get("already_theirs") or [])
    check(label + ": delivering puts it on their account", bool(theirs),
          str(landed.get("why") or landed.get("not_machines") or ""))
    if not theirs:
        return

    asset = theirs[0].get("asset_id") or theirs[0].get("id")
    with db.connect() as c:
        row = c.execute("SELECT family FROM assets WHERE id = ?",
                        (asset,)).fetchone()
    fam = (row["family"] if row else "") or ""
    check(label + ": the machine knows what it is", bool(fam.strip()),
          "family=" + repr(fam))

    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": dealer, "caller_phone": "+15550001111"}

    wo = tools.open_work_order(asset, "it has stopped working properly", Ctx())
    check(label + ": a fault opens a job", bool(wo.get("work_order_id")),
          str(wo.get("why") or wo.get("work_order_id")))
    if not wo.get("work_order_id"):
        return

    offers = scheduling.next_available_slot(asset).get("offers") or []
    check(label + ": somebody can be sent", bool(offers),
          (offers[0]["technician"] + " " + offers[0]["window"]) if offers
          else "nobody qualified or free")
    if not offers:
        return

    o = offers[0]
    held = tools.promise_slot(wo["work_order_id"], o["technician_id"],
                              o["starts_at"], [], Ctx())
    check(label + ": the visit is actually booked", bool(held.get("ok")),
          str(held.get("why") or "held"))


def main() -> None:
    with db.connect() as c:
        dealers = [r[0] for r in c.execute("SELECT id FROM dealers ORDER BY id")]

    print("SEAMS (where every fault today actually was)")
    off_thread_routing()
    seams()

    families = {"D-REF": "walk-in cooler", "D-IT": "laptop",
                "D-FURN": "chair", "D-AV": "projector"}
    for d in dealers:
        fam = families.get(d, "")
        print(f"\nSELLING AS {d} ({fam or 'anything'})")
        sell(d, fam, want_in_stock=True)
        sell(d, fam, want_in_stock=False)

    print("\nBUY IT, DELIVER IT, BREAK IT, GET SOMEBODY SENT")
    for d, fam in (("D-REF", "walk-in cooler"), ("D-IT", "laptop"),
                   ("D-FURN", "office chair"), ("D-AV", "projector")):
        the_whole_life_of_a_machine(d, fam)

    print("\nCOMPLAINTS")
    for d in dealers:
        complaint(d)

    print()
    if FAILS:
        print(f"  {len(FAILS)} BROKEN:")
        for f in FAILS:
            print(f"    - {f}")
        sys.exit(1)
    print("  everything above works end to end")


if __name__ == "__main__":
    main()
