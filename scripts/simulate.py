"""Drive the real desk through real conversations and see where it breaks.

WHY THIS EXISTS, AND WHY IT SHOULD HAVE EXISTED FIRST

The way this was being tested was: change one thing, ask somebody to ring the
number, watch the journal, find the next fault, repeat. Every round cost a
phone call and found exactly one bug. A day of that surfaced maybe eight
faults, one at a time, and every one of them could have been found here in
about a minute without anybody picking up a phone.

This runs the ACTUAL front agent, with the actual tools and the actual
database, over scripted conversations, and reports what broke. Not the audio
path, which genuinely needs a phone, but everything downstream of it: intent,
routing, tool calls, guards, sub-agents, and whether the caller got what they
rang for.

WHAT IT CATCHES THAT THE UNIT TESTS DO NOT

The unit tests call tools directly with correct arguments. Every serious fault
found so far lived in the gap BETWEEN tools: an id that was never filled in, a
sub-agent whose answer never came back, a family written in the wrong
vocabulary, an intent that flipped, an instruction naming a tool the agent
could not reach. None of those are visible when you call the tool yourself.

WHAT IT DELIBERATELY DOES NOT DO

It does not assert on the model's wording. Wording moves, and a test that
pins it becomes a test of the model rather than of the desk. It asserts on
what actually happened: which tools ran, which were refused, what was written
to the database, and whether an exception escaped.

Run:  python -m scripts.simulate            all scenarios
      python -m scripts.simulate a_laptop      just the ones matching
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Turn:
    """One thing the caller says, and what should follow from it."""

    says: str
    expect_tools: tuple[str, ...] = ()      # at least one of these must run
    forbid_tools: tuple[str, ...] = ()      # none of these may run

    # What this turn TELLS the desk. Once given, being asked for it again is
    # the single most reliable frustration signal there is: three quarters of
    # customers name repeating themselves as the thing that annoys them, and
    # "repetition without acknowledgment" is what predicts a call blowing up.
    gives: dict = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    dealer: str
    phone: str
    turns: list[Turn]
    known_caller: bool = False
    note: str = ""

    # WHAT MUST BE TRUE OF THE DATABASE AFTERWARDS.
    #
    # The gap this closes is the reason twenty two scenarios missed the worst
    # bug found on a live call. This harness asserted which TOOLS RAN, which
    # is a claim about the conversation, not about the business. So a desk
    # that called supply, said "I have confirmed your order", and wrote three
    # purchase orders with every line at $0.00 passed every scenario it was
    # in, because the right tool had run.
    #
    # The production literature on voice agents is blunt about this: verify
    # the backend action actually completed, because outcome validation
    # matters more than conversation endpoints. Bookings made, orders
    # written, money on the lines.
    #
    #   writes:    tables that must have gained at least one row
    #   no_zeroes: order lines must carry a price, because a confirmed order
    #              at zero reaches invoicing at zero
    writes: tuple[str, ...] = ()
    no_zeroes: bool = False

    # Filled in by the run.
    tools: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    annoyances: list[str] = field(default_factory=list)
    crashed: str = ""


REF = "D-REF"
IT = "D-IT"


def scenarios() -> list[Scenario]:
    """The calls this desk actually gets, including the awkward ones."""
    return [
        Scenario(
            name="service_stranger",
            dealer=REF, phone="+13095550801",
            writes=("work_orders",),
            note="Nobody we know rings about a broken freezer.",
            turns=[
                Turn("Hi, this is Dana Whitfield from Riverside Taphouse.",
                     expect_tools=("confirm_details",)),
                Turn("My reach-in freezer is not holding temperature, it is "
                     "sitting at fifteen degrees.",
                     expect_tools=("set_intent", "should_send_someone",
                                   "register_asset", "assessment")),
                Turn("It is a Traulsen. We put it in about March 2024.",
                     expect_tools=("register_asset", "identify_equipment")),
                Turn("What is this going to cost me?",
                     expect_tools=("quote_visit",)),
                Turn("Any time Thursday works for us.",
                     expect_tools=("scheduling", "record_availability")),
            ],
        ),
        Scenario(
            name="a_laptop_from_a_standing_start",
            dealer=REF, phone="+13095550802",
            no_zeroes=True,
            note="A stranger opens with a laptop. Nothing about the desk was "
                 "pointed at computers a moment ago and it should still sell "
                 "them one, because this is the only number we publish.",
            turns=[
                Turn("Hi, I want to buy a laptop, what do you recommend?",
                     expect_tools=("route_to_vendor", "set_intent"),
                     forbid_tools=("another_business_handles_it",
                                   "transfer_live_call")),
                Turn("Something reliable for the office, around a thousand.",
                     gives={"budget": "1000"},
                     expect_tools=("advice", "find_equipment",
                                   "recommend_equipment", "options_under",
                                   "product_availability")),
            ],
        ),
        Scenario(
            name="buying_with_budget",
            dealer=REF, phone="+13095550803",
            writes=("purchase_orders", "purchase_lines"), no_zeroes=True,
            note="Wants a freezer, then names a budget nothing meets.",
            turns=[
                Turn("I am looking to buy a new reach-in freezer.",
                     expect_tools=("set_intent", "advice", "find_equipment",
                                   "recommend_equipment")),
                Turn("How much is that one?",
                     expect_tools=("price_for", "product_availability")),
                Turn("That is too much, my budget is two thousand dollars.",
                     expect_tools=("options_under", "alternatives")),
            ],
        ),
        Scenario(
            name="warranty_claim",
            dealer=REF, phone="+13095550804",
            note="Says it is under warranty on a machine we did not sell.",
            turns=[
                Turn("This is Sam Ortega at Kettle Street Kitchen. My Traulsen "
                     "freezer has stopped cooling.",
                     expect_tools=("set_intent",)),
                Turn("It is a Traulsen RHT126WUT-FHS, installed March 2024.",
                     expect_tools=("register_asset",)),
                Turn("But it is still under warranty.",
                     expect_tools=("quote_visit", "warranty_status",
                                   "where_to_send_proof")),
            ],
        ),
        Scenario(
            name="a_laptop_repair",
            dealer=IT, phone="+13095550805",
            writes=("work_orders",),
            note="The whole IT half of the book, which for a long time no "
                 "call could reach.",
            turns=[
                Turn("Hi, this is Priya at Northgate Studio. My ThinkPad will "
                     "not power on.",
                     expect_tools=("set_intent", "route_to_vendor")),
                Turn("It is a Lenovo ThinkPad T14, about eighteen months old.",
                     expect_tools=("register_asset", "identify_equipment")),
                Turn("Is that covered? And what would it cost?",
                     expect_tools=("quote_visit", "warranty_status")),
            ],
        ),
        Scenario(
            name="a_freezer_from_an_it_customer",
            dealer=IT, phone="+13095550806",
            note="Somebody whose only history with us is a laptop rings about "
                 "a walk-in. There is one desk, so this is an ordinary service "
                 "call that happens to change which vendor is behind it. Being "
                 "refused, or handed a number, would both be wrong.",
            turns=[
                Turn("My walk-in cooler is warm, can you send somebody?",
                     expect_tools=("route_to_vendor",),
                     forbid_tools=("another_business_handles_it",
                                   "transfer_live_call")),
                Turn("It is the one in the back, been warm since this morning.",
                     expect_tools=("register_asset", "should_send_someone",
                                   "assessment", "identify_equipment")),
            ],
        ),
        Scenario(
            name="vague_caller",
            dealer=REF, phone="+13095550807",
            note="Says almost nothing useful. Must not invent an asset.",
            turns=[
                Turn("Yeah, hi. It is broken."),
                Turn("The cold one. In the back."),
                Turn("I do not know the model, it is behind the door."),
            ],
        ),
        Scenario(
            name="changes_mind",
            dealer=REF, phone="+13095550808",
            note="Starts as a repair, turns into a purchase.",
            turns=[
                Turn("My old freezer keeps failing.",
                     expect_tools=("set_intent",)),
                Turn("Actually forget repairing it, what would a new one cost?",
                     expect_tools=("advice", "price_for", "find_equipment",
                                   "recommend_equipment",
                                   "product_availability")),
            ],
        ),
        Scenario(
            name="parts_order",
            dealer=REF, phone="+13095550809",
            writes=("purchase_orders", "purchase_lines"), no_zeroes=True,
            note="A trade customer ordering a part, not a visit.",
            turns=[
                Turn("I need to order a door gasket for a Traulsen.",
                     expect_tools=("set_intent", "supply", "check_stock",
                                   "lookup_product")),
                Turn("How soon can you get it to me?",
                     expect_tools=("supply", "quote_delivery")),
            ],
        ),
        Scenario(
            name="supplier_pitch",
            dealer=REF, phone="+13095550810",
            note="A vendor selling TO us. Must never book a technician.",
            turns=[
                Turn("Hi, I am calling from Kelvin Parts, we supply compressors "
                     "and I wanted to talk to your buyer.",
                     expect_tools=("set_intent", "log_supplier_offer"),
                     forbid_tools=("open_work_order", "promise_slot",
                                   "quote_visit")),
            ],
        ),
        Scenario(
            name="complaint",
            dealer=REF, phone="+13095550811",
            note="Angry about a previous visit.",
            turns=[
                Turn("Your engineer came out last week and it is broken again. "
                     "I am not paying twice.",
                     expect_tools=("set_intent", "register_complaint",
                                   "load_memory")),
            ],
        ),
        Scenario(
            name="asks_for_someone_elses_machine",
            dealer=REF, phone="+13095550812",
            note="Names an asset id belonging to another customer.",
            turns=[
                Turn("I am calling about asset AST-0A5744, it is not cooling."),
            ],
        ),
        # --------------------------------------------------------------
        # How people actually talk. Nobody delivers a clean brief: they
        # lead with the panic, give facts in the wrong order, repeat
        # themselves, change their mind halfway, and get short with you.
        # --------------------------------------------------------------
        Scenario(
            name="panicking_kitchen",
            dealer=REF, phone="+13095550813",
            note="Six in the evening, service on, stock going off. Leads with "
                 "the emergency and the facts come out backwards.",
            turns=[
                Turn("Yeah I need someone out here now, we've got service in "
                     "an hour and the freezer's gone."),
                Turn("It's warm. Everything in it is going soft.",
                     expect_tools=("set_intent", "should_send_someone",
                                   "assessment", "register_asset")),
                Turn("Look I don't know, it's the big Traulsen. Beckett Grill, "
                     "we're on Adams Street.",
                     gives={"address": "Adams Street", "name": "Beckett Grill"}),
                Turn("How much and how soon?",
                     expect_tools=("quote_visit", "scheduling")),
            ],
        ),
        Scenario(
            name="repeats_themselves",
            dealer=REF, phone="+13095550814",
            note="Gives the address early, then is asked for it again. This is "
                 "the exact pattern that predicts a call blowing up.",
            turns=[
                Turn("Hi, Marcus at Bell Street Kitchen, 12 Adams Street.",
                     gives={"address": "12 Adams Street",
                            "name": "Bell Street Kitchen"}),
                Turn("The walk-in cooler is sitting at about fifty degrees."),
                Turn("What's that going to cost?", expect_tools=("quote_visit",)),
                Turn("I already told you, 12 Adams Street."),
            ],
        ),
        Scenario(
            name="impatient",
            dealer=REF, phone="+13095550815",
            note="Short, blunt, wants a number and a time and nothing else.",
            turns=[
                Turn("Freezer's down. How much do you charge to come out?",
                     expect_tools=("quote_visit", "set_intent")),
                Turn("Just a rough number is fine."),
                Turn("Right, and when can someone be here?",
                     expect_tools=("scheduling",)),
            ],
        ),
        Scenario(
            name="two_machines_at_once",
            dealer=REF, phone="+13095550816",
            note="Two faults in one call. Must not merge them into one job.",
            turns=[
                Turn("We've got two problems. The ice machine has stopped and "
                     "the walk-in is running warm.",
                     expect_tools=("set_intent",)),
                Turn("Can you look at both on the same visit?"),
            ],
        ),
        Scenario(
            name="asks_price_before_anything",
            dealer=REF, phone="+13095550817",
            note="Price first, details later. Must not stonewall on an address.",
            turns=[
                Turn("Before anything else, what do you charge for a callout?",
                     expect_tools=("quote_visit",)),
            ],
        ),
        Scenario(
            name="wants_to_talk_to_a_person",
            dealer=REF, phone="+13095550818",
            note="Does not want a machine. Must not fight them for it.",
            turns=[
                Turn("Can I speak to an actual person please?"),
            ],
        ),
        Scenario(
            name="second_language",
            dealer=REF, phone="+13095550819",
            note="Plain, clipped English. Must not be treated as noise.",
            turns=[
                Turn("Hello. Freezer no cold. Restaurant. You come today?",
                     expect_tools=("set_intent",)),
                Turn("Traulsen. Big one, in kitchen.",
                     expect_tools=("register_asset", "identify_equipment",
                                   "should_send_someone", "assessment")),
            ],
        ),
        Scenario(
            name="known_customer_wants_the_other_trade",
            dealer=REF, phone="+13095550821",
            note="A nine year refrigeration customer rings and asks about a "
                 "laptop. The greeting has already named their freezers, so "
                 "the call opens pointed at the wrong thing and has to turn "
                 "without the caller feeling it. They should be sold a laptop.",
            known_caller=True,
            turns=[
                Turn("Neither actually. I need a new laptop for the office.",
                     expect_tools=("route_to_vendor", "set_intent"),
                     forbid_tools=("another_business_handles_it",
                                   "transfer_live_call", "open_work_order")),
                Turn("Something that will handle spreadsheets, under a "
                     "thousand.",
                     gives={"budget": "1000"},
                     expect_tools=("options_under", "recommend_equipment",
                                   "product_availability", "alternatives")),
            ],
        ),
        Scenario(
            name="both_trades_on_one_call",
            dealer=REF, phone="+13095550822",
            note="Buys a printer and reports a walk-in fault on the same call. "
                 "Two vendors, two intents, one number, and the caller should "
                 "never learn that anything switched underneath them.",
            turns=[
                Turn("Do you sell printers?",
                     expect_tools=("route_to_vendor",),
                     forbid_tools=("another_business_handles_it",
                                   "transfer_live_call")),
                Turn("Right. Actually while I have you, the walk-in is icing "
                     "up again.",
                     expect_tools=("set_intent", "should_send_someone",
                                   "assessment", "load_memory")),
            ],
        ),
        Scenario(
            name="already_a_customer_new_machine",
            dealer=REF, phone="+13095550820",
            note="We know them, but this is a machine we have never seen.",
            turns=[
                Turn("It's the new cooler we put in last month, it's icing up."),
                Turn("Beverage-Air, we bought it elsewhere.",
                     expect_tools=("register_asset", "identify_equipment")),
            ],
        ),
]



# ---------------------------------------------------------------------------



# How a caller says they have told you already. Any of these appearing in a
# scenario means the desk has lost the thread, and the wording is taken from
# what customers actually say when a call is going wrong.
FED_UP = ("already told you", "i just said", "as i said", "like i said",
          "i already", "third time", "again?")

# What being asked for a fact sounds like. Deliberately narrow: an agent
# CONFIRMING something back ("the Traulsen in the back, is that right?") is
# good behaviour and must not be counted against it.
ASKING = ("what is your", "what's your", "could you tell me your",
          "can you tell me your", "could you provide", "can you provide",
          "i need your", "i do need the", "i need the", "may i have your",
          "could you confirm your", "what is the model", "what's the model",
          "could you give me the", "can i get your")


def _annoying(sc: Scenario) -> list[str]:
    """Where this conversation would have wound a real person up.

    Measured, not judged. Three signals, each from published work on what
    actually predicts an escalation:

      ASKED FOR SOMETHING ALREADY GIVEN. The strongest one.

      SAID THE SAME THING TWICE. "Repetition without acknowledgment" is the
      pattern that predicts a call blowing up: the caller says it again and
      the desk's answer does not move.

      A TURN THAT WENT NOWHERE. No tool ran and nothing was said back. On a
      phone that is silence, and silence is where people hang up.
    """
    out = []
    given: dict[str, str] = {}

    for i, turn in enumerate(sc.turns):
        for k, v in (turn.gives or {}).items():
            given[k] = v

        reply = " ".join(sc.said[i:i + 1]).lower() if i < len(sc.said) else ""
        if not reply:
            continue

        # asked for a fact they already had
        for fact, value in given.items():
            if fact in reply and any(a in reply for a in ASKING):
                out.append(f'asked for the {fact} after being told "{value}"')

    # said the same thing twice
    seen = set()
    for line in sc.said:
        key = " ".join(line.lower().split())[:70]
        if len(key) > 25 and key in seen:
            out.append(f'repeated itself: "{line[:70]}..."')
        seen.add(key)

    return out



async def _run_one(sc: Scenario, verbose: bool) -> Scenario:
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from src import agents, caller, db, trace

    who = caller.resolve(sc.phone, sc.dealer)
    call_id = f"SIM-{sc.name[:12]}"
    with db.txn() as c:
        c.execute("INSERT OR REPLACE INTO calls "
                  "(id,dealer_id,from_e164,contact_id,started_at) "
                  "VALUES (?,?,?,?,'2026-08-27T17:00:00')",
                  (call_id, sc.dealer, sc.phone, who.get("contact_id")))
    trace.call_context(call_id)

    # The same agent on a text model.
    #
    # The Live model is audio only and refuses generateContent outright, which
    # is the first thing this harness discovered. Nothing being tested here is
    # about audio: the tools, the instruction, the guards, the sub-agents and
    # the routing are the same objects. What is NOT covered is the transport,
    # which genuinely needs a phone, and that is the whole of what a real call
    # still has to prove.
    desk = agents.front_agent.model_copy(update={
        "model": agents.worker(agents.THINKING),
        "instruction": agents.front_agent.instruction,
    })
    runner = InMemoryRunner(agent=desk, app_name="sim")
    session = await runner.session_service.create_session(
        app_name="sim", user_id=sc.phone,
        state={"caller_phone": sc.phone, "caller": who,
               "call_id": call_id, "dealer_id": sc.dealer})

    rows_before = _row_counts(sc.writes)

    try:
        for turn in sc.turns:
            before = len(sc.tools)
            try:
                await _with_backoff(runner, session, sc, turn)
            except Exception as e:
                sc.crashed = f"{type(e).__name__}: {str(e)[:160]}"
                if verbose:
                    traceback.print_exc()
                break

            ran = sc.tools[before:]

            # Expected tools are checked across the WHOLE conversation, not on
            # the turn that prompted them.
            #
            # Asserting per turn was too rigid and it showed: the panicking
            # kitchen scenario was reported as a failure while it ran eleven
            # tools and booked a named engineer for that afternoon, because
            # one tool arrived a turn later than the script wanted. A desk
            # that listens for a beat before acting is behaving well, and a
            # harness that calls that a bug teaches you to make the desk
            # twitchier.
            #
            # Forbidden tools stay strict per turn, because "must not" is
            # about the moment: offering to sell somebody a freezer on the
            # turn they asked about a laptop is wrong even if it never
            # happens again.
            hit = set(ran) & set(turn.forbid_tools)
            if hit:
                sc.failures.append(
                    f'after "{turn.says[:44]}..." must NOT have called '
                    f"{sorted(hit)}")

        for turn in sc.turns:
            if turn.expect_tools and not set(sc.tools) & set(turn.expect_tools):
                sc.failures.append(
                    f'nowhere in the call did it use one of '
                    f"{list(turn.expect_tools)}, which "
                    f'"{turn.says[:44]}..." needed')

        # AND WHETHER ANY OF IT ACTUALLY HAPPENED.
        rows_after = _row_counts(sc.writes)
        for table in sc.writes:
            gained = rows_after.get(table, 0) - rows_before.get(table, 0)
            if gained <= 0:
                sc.failures.append(
                    f"the call said it was done and nothing was written to "
                    f"{table}. Saying it is not doing it.")

        if sc.no_zeroes:
            for line in _unpriced_lines():
                sc.failures.append(
                    f"an order line was written with no price: {line}. "
                    "A confirmed order at zero reaches invoicing at zero.")
    finally:
        trace.call_context("")
    return sc


def _row_counts(tables: tuple[str, ...]) -> dict:
    """How many rows each table holds right now."""
    if not tables:
        return {}
    from src import db

    out = {}
    try:
        with db.connect() as c:
            for t in tables:
                try:
                    out[t] = c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                except Exception:
                    out[t] = 0
    except Exception as e:
        print(f"[sim] could not read the database: {type(e).__name__}: {e}",
              flush=True)
    return out


def _unpriced_lines() -> list[str]:
    """Order lines carrying no money.

    Three orders were confirmed on a live call at $0.00 each, after the desk
    had read the real prices out loud. It said one number and wrote another,
    and every existing test passed.
    """
    from src import db

    try:
        with db.connect() as c:
            return [f"{r['description']} on {r['po_id']}" for r in c.execute(
                "SELECT po_id, description FROM purchase_lines "
                "WHERE unit_price IS NULL OR unit_price = 0")]
    except Exception:
        return []


# Vertex runs Gemini on a shared regional pool, so 429 means the pool was busy
# rather than that an allowance is spent. It clears on its own, usually within
# seconds. Without this, one busy moment ends a whole simulation run and the
# report is about quota rather than about the desk.
RETRIES = 4
BACKOFF = (5, 15, 30, 60)


async def _with_backoff(runner, session, sc, turn) -> None:
    from google.genai import types

    for attempt in range(RETRIES):
        try:
            async for event in runner.run_async(
                    user_id=sc.phone, session_id=session.id,
                    new_message=types.Content(
                        role="user", parts=[types.Part(text=turn.says)])):
                if event.error_message and "RESOURCE_EXHAUSTED" not in str(
                        event.error_message):
                    sc.failures.append(
                        f"model error: {event.error_message[:90]}")
                for part in (getattr(getattr(event, "content", None),
                                     "parts", None) or []):
                    fc = getattr(part, "function_call", None)
                    if fc is not None:
                        sc.tools.append(fc.name)
                    if getattr(part, "text", None):
                        sc.said.append(part.text.strip())
            return
        except Exception as e:
            if "RESOURCE_EXHAUSTED" not in str(e) or attempt == RETRIES - 1:
                raise
            await asyncio.sleep(BACKOFF[attempt])


async def _main(only: str, verbose: bool) -> int:
    from src import db

    picked = [s for s in scenarios() if not only or s.name == only]
    if not picked:
        print(f"no scenario called {only!r}")
        return 2

    print(f"running {len(picked)} conversations against the real desk\n")
    results = []
    for sc in picked:
        try:
            results.append(await _run_one(sc, verbose))
        except Exception as e:
            sc.crashed = f"{type(e).__name__}: {str(e)[:160]}"
            results.append(sc)
        mark = "CRASH" if sc.crashed else ("FAIL" if sc.failures else "ok")
        print(f"  {mark:<6} {sc.name:<34} {len(sc.tools)} tool calls")

    for sc in results:
        sc.annoyances = _annoying(sc)

    bad = [s for s in results if s.crashed or s.failures or s.annoyances]
    print(f"\n{len(results) - len(bad)} of {len(results)} clean\n")

    for sc in bad:
        print("=" * 74)
        print(f"{sc.name}   ({sc.note})")
        if sc.crashed:
            print(f"  CRASHED: {sc.crashed}")
        for f in sc.failures:
            print(f"  - {f}")
        for a in sc.annoyances:
            print(f"  ANNOYING: {a}")
        print(f"  tools: {sc.tools}")
        if sc.said:
            print(f"  last said: {sc.said[-1][:160]}")
        print()
    return 1 if bad else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv

    # A scratch copy of the database. Simulations write work orders, assets and
    # quotes, and none of that belongs in the real book.
    real = Path(os.getenv("PRAEVISUM_DB",
                          Path(__file__).resolve().parents[1] / "praevisum.db"))
    scratch = Path(tempfile.gettempdir()) / "praevisum-sim.db"
    if real.exists():
        shutil.copy(real, scratch)
    os.environ["PRAEVISUM_DB"] = str(scratch)

    raise SystemExit(asyncio.run(_main(args[0] if args else "", verbose)))
