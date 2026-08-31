"""Replay a real call's tool sequence, through the guard, against live data.

WHY THIS IS DIFFERENT FROM THE OTHER CHECKS

e2e_check drives the tools directly with clean arguments. That catches
plumbing and misses the thing that actually goes wrong on a phone call: the
model passes a value that is WRONG in a specific way, the guard is supposed to
correct it, and the correction happens somewhere the tests never look.

This takes the arguments a live call really produced -- including the invented
asset id and the literal string "None" -- and pushes them through
`guard_tool`, which is what sits between the model and every tool. Then it
calls the tool with whatever the guard left behind.

The sequence below is transcribed from a recorded call in which the desk:

  - resolved a chair complaint onto the customer's LAPTOP
  - invented asset_id="AST-037", which looks exactly like one of ours
  - failed to open a work order four times
  - asked the customer for a model number and then for their address
  - offered a visit for a work order that did not exist

    python scripts/replay_the_call.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db, guards, tenancy, tools, trace  # noqa: E402

ACCOUNT = "A-9DE130"          # Brady Street Bakery, the number that rang in
DEALER = "D-FURN"             # what "office chair" routes to

FAILS: list[str] = []


class Tool:
    def __init__(self, name):
        self.__name__ = name


class Ctx:
    def __init__(self, dealer):
        self.state = {"intent": "service", "language": "",
                      "dealer_id": dealer, "caller_phone": "+18573187009",
                      "caller": {"account_id": ACCOUNT}}


def check(what: str, ok: bool, detail: str = "") -> None:
    print(f"    {'ok  ' if ok else 'FAIL'}  {what}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(what)


def through_the_guard(name: str, args: dict, dealer: str) -> dict:
    """What the tool actually receives, after the guard has had it."""
    before = dict(args)
    try:
        guards.guard_tool(Tool(name), args, Ctx(dealer))
    except Exception as e:
        print(f"      guard raised {type(e).__name__}: {e}")
    changed = {k: (before.get(k), v) for k, v in args.items()
               if before.get(k) != v}
    if changed:
        for k, (was, now) in changed.items():
            print(f"      guard: {k} {was!r} -> {now!r}")
    return args


def a_call_row() -> str:
    """A FRESH call to hang the replay on.

    Not the customer's most recent real call, which was the first version of
    this and quietly broke every case. The guard resolves an asset from the
    job already opened ON THIS CALL before it looks at anything else, and that
    precedence is correct: inside one conversation, the work order you just
    opened is what you are talking about.

    Reusing a real past call therefore inherited that call's work order, and
    every question -- a chair complaint, an ambiguous one -- came back holding
    the laptop from the previous conversation. The code was right and the test
    was contaminated.
    """
    import uuid

    from datetime import datetime

    cid = f"REPLAY-{uuid.uuid4().hex[:6].upper()}"
    with db.connect() as c:
        ct = c.execute("SELECT id FROM contacts WHERE account_id = ? LIMIT 1",
                       (ACCOUNT,)).fetchone()
    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO calls (id, from_e164, contact_id, started_at,
                                      intent, dealer_id)
                   VALUES (?,?,?,?,?,?)""",
                (cid, "+18573187009", ct["id"] if ct else None,
                 datetime.now().isoformat(timespec="seconds"), "service",
                 DEALER))
    except Exception as e:
        print(f"  could not open a replay call: {type(e).__name__}: {e}")
        return ""
    return cid


def main() -> None:
    call_id = a_call_row()
    print(f"  replaying against call {call_id or '(none found)'}")
    trace.call_context(call_id or "REPLAY")
    tenancy.routed_to(DEALER, call_id or "REPLAY")

    with db.connect() as c:
        chair = c.execute(
            """SELECT a.id FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.family = 'office chair'""",
            (ACCOUNT,)).fetchone()
        laptop = c.execute(
            """SELECT a.id FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.family = 'laptop'""",
            (ACCOUNT,)).fetchone()
    chair_id = chair["id"] if chair else ""
    laptop_id = laptop["id"] if laptop else ""
    print(f"  the chair is {chair_id}, the laptop is {laptop_id}")
    print()

    symptom = "Chair not holding height and is tilted"

    print("  1. should_send_someone, as the call sent it")
    a = through_the_guard("should_send_someone",
                          {"symptom": symptom, "asset_id": "None"}, DEALER)
    check("lands on the chair, not the laptop", a.get("asset_id") == chair_id,
          f"got {a.get('asset_id')}")

    print("  2. can_we_serve, same bad id")
    a = through_the_guard("can_we_serve",
                          {"asset_id": "None", "dealer_id": ""}, DEALER)
    check("still the chair", a.get("asset_id") == chair_id,
          f"got {a.get('asset_id')}")

    print("  3. open_work_order, with the id the desk invented")
    a = through_the_guard("open_work_order",
                          {"asset_id": "AST-037",
                           "reported_symptom": symptom}, DEALER)
    check("the invented id is replaced", a.get("asset_id") == chair_id,
          f"got {a.get('asset_id')}")

    wo = ""
    if a.get("asset_id"):
        out = tools.open_work_order(a["asset_id"], symptom, Ctx(DEALER))
        wo = out.get("work_order_id") or out.get("work_order") or ""
        check("the work order actually opens", bool(wo),
              str(out.get("why") or wo))

    print("  4. the laptop, said in the same words a customer would use")
    a = through_the_guard("should_send_someone",
                          {"symptom": "the laptop will not charge",
                           "asset_id": "None"}, "D-IT")
    # Two laptops now sit on this account, so the honest answer is either the
    # one they meant or nothing at all. What it must never be is the CHAIR,
    # which is the machine the job on this call was opened against.
    check("naming a laptop never comes back with the chair",
          a.get("asset_id") != chair_id,
          f"got {a.get('asset_id') or 'nothing, so it asks which laptop'}")

    print("  5. an ambiguous complaint, opening a fresh call")
    # From a CLEAN call. Within one conversation the guard reuses the machine
    # already settled on, which is right: a customer who has been talking
    # about their chair and then says "it is still broken" means the chair.
    # The claim being checked here is the other one -- that with nothing to go
    # on at all, it asks rather than picking.
    guards.forget_the_machine(call_id or "REPLAY")
    a = through_the_guard("should_send_someone",
                          {"symptom": "something is broken",
                           "asset_id": "None"}, DEALER)
    check("refuses to guess between two machines",
          not a.get("asset_id"), f"got {a.get('asset_id')!r}")

    print("  6. scheduling, which is where it asked for an address")
    from src import scheduling

    for who, aid in (("chair", chair_id), ("laptop", laptop_id)):
        if not aid:
            continue
        offers = scheduling.next_available_slot(aid).get("offers") or []
        check(f"an engineer can be offered for the {who}", bool(offers),
              (f"{offers[0]['technician']} {offers[0]['window']}"
               if offers else "nobody"))

    print("  7. calling the slot tool with nothing at all")
    empty = scheduling.next_available_slot()
    check("says what it needs instead of asking the customer",
          empty.get("ok") is False and "id" not in str(empty.get("say", "")).lower()
          or "Do NOT ask" in str(empty.get("say", "")),
          str(empty.get("why"))[:60])

    print()
    if FAILS:
        print(f"  {len(FAILS)} STILL BROKEN:")
        for f in FAILS:
            print(f"    - {f}")
        sys.exit(1)
    print("  the whole sequence that failed on the call now works")


if __name__ == "__main__":
    main()
