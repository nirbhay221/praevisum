"""The arithmetic, made visible while it is happening.

WHY THIS EXISTS

Every consequential decision in this system is a calculation, not a judgment.
What goes in the van is `P(needed) x cost of not having it > cost of carrying`.
Whether to send anybody is the same trade one step earlier. What to reorder is
the same constants over a longer horizon.

All of it was computed on every call and shown to nobody. The numbers went
into a dict, the model read them, and they were gone. The console printed
`tool: assess_job()`, which is the NAME of a thing that happened rather than
the thing itself.

That is backwards. The reason to prefer this desk over a person guessing is
that you can disagree with its arithmetic, and you cannot disagree with
arithmetic you were never shown.

WHAT IS PUBLISHED, AND WHAT IS NOT

Only values that were already calculated. Nothing here computes anything, so a
trace line can never disagree with the decision it describes, and turning the
whole feed off cannot change what the desk does.

Both sides of every inequality, always. "Carry the defrost heater" is an
assertion. "Carry it: 0.555 x $390 = $216 against $5.92 to hold it" is a claim
somebody can check, and being checkable is the entire point.

NEVER IN THE WAY OF A CALL

`events.publish` is fire and forget and drops slow subscribers, and every
function here is wrapped so a formatting mistake cannot reach a caller. A
dashboard is not allowed to be the reason a phone call fails.
"""

from __future__ import annotations

import contextvars
import functools
import threading
import json
from datetime import datetime

from . import db, events

# Which call the reasoning belongs to.
#
# A context variable rather than an argument threaded through nine functions.
# `what_to_load(dealer_id, asset_id, symptom)` has no business knowing about
# calls, and adding a call_id parameter to every reasoning function to satisfy
# a log would be the log dictating the shape of the code.
#
# A context variable follows an asyncio task. It does NOT follow a worker
# thread: this file used to claim it did, and the claim was wrong in a way
# that quietly disabled a safety guard.
#
#     call_context("CALL-1")
#     CALL.get()                    -> "CALL-1"
#     executor.submit(CALL.get)     -> ""
#
# The framework runs some tool calls on a worker thread. guards.py opens with
# `call_id = CALL.get(); if not call_id: return`, so on that thread the guard
# returned immediately and filled in nothing. The scheduling agent was then
# handed a work order id and no asset or account, and did the one thing its
# instruction forbids in capital letters: it asked the customer for an Asset
# ID and an Account ID.
#
# So there is a plain dictionary beside the context variable, which any thread
# can read.
CALL = contextvars.ContextVar("praevisum_call_id", default="")

_ON_THE_LINE: dict[str, bool] = {}
_LINE_LOCK = threading.Lock()


def call_context(call_id: str):
    """Tie every decision made from here on to one call.

    Set when the line opens. The context variable dies with the task; the
    registry entry is removed by `call_over` when they hang up, so a later
    call cannot inherit an earlier one's id from either.
    """
    call_id = call_id or ""
    if call_id:
        with _LINE_LOCK:
            _ON_THE_LINE[call_id] = True
    return CALL.set(call_id)


def call_over(call_id: str) -> None:
    """They hung up. Take the call off the register."""
    with _LINE_LOCK:
        _ON_THE_LINE.pop(call_id or "", None)


def here() -> str:
    """The call we are part of, readable from any thread.

    The context variable first, because it is exact. The register second, and
    only when exactly one call is on the line: with two calls in progress
    there is no way to tell which one a worker thread belongs to, and guessing
    would attach one caller's decisions to another caller's record.
    """
    mine = CALL.get()
    if mine:
        return mine
    with _LINE_LOCK:
        live = [k for k in _ON_THE_LINE]
    return live[0] if len(live) == 1 else ""


def _guarded(fn):
    """Nothing in this file may reach a caller, including its own formatting.

    The first version guarded only the publish, on the reasoning that events
    is where the risk was. It was wrong, and a test found it immediately: a
    None probability raises inside the f-string that builds the line, which is
    BEFORE any publish happens. That exception came straight back out through
    what_to_load and would have ended a phone call over a dashboard.

    So the guard is the whole function, not the last statement in it.
    """
    @functools.wraps(fn)
    def go(dealer_id, *a, **kw):
        token = _KIND.set(fn.__name__)
        try:
            return fn(dealer_id, *a, **kw)
        except Exception as e:
            print(f"[trace] dropped a {fn.__name__} line: "
                  f"{type(e).__name__}: {e}", flush=True)
        finally:
            _KIND.reset(token)
    return go


def _say(dealer_id: str, text: str, **extra) -> None:
    """One line onto the live feed, and one row into the record.

    Both, from one place, so the dashboard and the database can never tell
    different stories about the same decision.

    The feed answers "what is happening now", which is a demo question. The
    row answers "why did you put a defrost heater in that van three weeks
    ago", which is the question a dealer actually has, and until this existed
    the honest answer was that nobody knew any more.
    """
    dealer_id = dealer_id or "D-REF"
    events.publish(dealer_id, "reasoning", text=text, **extra)
    _keep(dealer_id, text, extra)


def _keep(dealer_id: str, line: str, extra: dict) -> None:
    """Write the decision down. Never raises, never blocks the reasoning.

    A failed write must not change what the desk does, so this is guarded
    separately from the feed: losing the record is bad, and losing the
    decision because the record failed is worse.
    """
    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO decisions
                   (dealer_id,call_id,kind,subject,verdict,line,numbers,at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (dealer_id, CALL.get() or None,
                 extra.get("kind") or _KIND.get(),
                 extra.get("sku") or extra.get("subject"),
                 extra.get("verdict"), line.strip(),
                 json.dumps({k: v for k, v in extra.items()
                             if k not in ("kind", "subject")}) or None,
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        print(f"[trace] decision not recorded: {type(e).__name__}: {e}",
              flush=True)


# Which kind of decision is being made, set by the decorator so each publisher
# does not have to repeat it on every line it writes.
_KIND = contextvars.ContextVar("praevisum_decision_kind", default="reasoning")


def _money(x) -> str:
    try:
        return f"${float(x):,.2f}".replace(".00", "")
    except (TypeError, ValueError):
        return str(x)


@_guarded
def fault_distribution(dealer_id: str, symptom: str, rows: list) -> None:
    """What the corpus says this probably is, before anything is decided.

    Published with the evidence tier attached, because "44% the fan motor" off
    machines of the same defrost design is a different claim from 44% off the
    same model, and the caller is entitled to the difference.
    """
    if not rows:
        _say(dealer_id, f'nothing in our own history matches "{symptom[:60]}", '
                        "so there is no basis to pick parts")
        return

    _say(dealer_id, f'what "{symptom[:56]}" turned out to be, on our own jobs:')
    for r in rows[:4]:
        p = r.get("probability")
        tiers = r.get("evidence_from") or []
        where = f"  [{', '.join(tiers[:2])}]" if tiers else ""
        _say(dealer_id,
             f"    {p:>5.0%}  {str(r.get('cause'))[:58]}{where}",
             probability=p, subject=str(r.get("cause"))[:80],
             evidence_from=tiers)


@_guarded
def van_load(dealer_id: str, carry: list, skip: list) -> None:
    """Both sides of the inequality for every part, carried or not.

    The parts left behind matter as much as the ones taken. A system that only
    shows what it chose is showing a conclusion; showing what it rejected and
    why is showing a decision.
    """
    for p in carry[:6]:
        _say(dealer_id,
             f"    CARRY {p.get('sku'):<14} {p.get('probability', 0):.0%} needed  "
             f"saves {_money(p.get('expected_saving'))} in return trips  "
             f"against {_money(p.get('carrying_cost'))} to hold it",
             sku=p.get("sku"), verdict="carry",
             # The figures themselves, not only the sentence. Without these
             # "how often did we carry a part we did not need" would mean
             # parsing English back into floats, which is how a record stops
             # being evidence.
             probability=p.get("probability"),
             expected_saving=p.get("expected_saving"),
             carrying_cost=p.get("carrying_cost"))

    for p in skip[:4]:
        _say(dealer_id,
             f"    SKIP  {p.get('sku'):<14} {p.get('probability', 0):.0%} needed  "
             f"saves {_money(p.get('expected_saving'))}  "
             f"against {_money(p.get('carrying_cost'))} to hold it",
             sku=p.get("sku"), verdict="skip",
             probability=p.get("probability"),
             expected_saving=p.get("expected_saving"),
             carrying_cost=p.get("carrying_cost"))


@_guarded
def send_decision(dealer_id: str, decision: dict) -> None:
    """Whether a van moves at all, and on what grounds.

    The most consequential thing this desk decides, and the one where the two
    errors are not symmetric: a wasted visit costs money, talking somebody out
    of a visit they needed costs the relationship.
    """
    send = decision.get("send")
    conf = decision.get("confidence_in_cause")

    if send is True:
        _say(dealer_id,
             f"    SEND  somebody goes: {decision.get('why', '')[:70]}"
             + (f"  (best cause {conf:.0%})" if conf else ""),
             verdict="send", confidence=conf,
             cost_if_wrong=decision.get("cost_if_we_are_wrong"))
        return

    fix = decision.get("remote_fix") or {}
    _say(dealer_id,
         f"    OFFER FIRST  a {fix.get('source', '?')} procedure matches, "
         f"worked {fix.get('worked_before', '?')}  "
         f"avoids {_money(decision.get('cost_avoided_if_it_works'))} if it holds",
         verdict="offer_first", source=fix.get("source"), confidence=conf,
         avoided=decision.get("cost_avoided_if_it_works"))


@_guarded
def outside_opinion(dealer_id: str, make: str, result: dict) -> None:
    """A second opinion, and at which level it was actually found."""
    if not result.get("available"):
        _say(dealer_id, f"    market  nothing quotable on {make}: "
                        f"{result.get('why', '')[:60]}")
        return
    _say(dealer_id,
         f"    market  {make} {result.get('rating')} from "
         f"{result.get('reviews')} reviews [{result.get('level')} level, "
         f"{result.get('source')}], kept separate from our own record")


@_guarded
def settled(dealer_id: str, outcome: dict) -> None:
    """What the call turned out to be, once it has ended."""
    won = "resolved" if outcome.get("resolved") else "unresolved"
    extra = "  (no van needed)" if outcome.get("avoided_visit") else ""
    _say(dealer_id,
         f"    call {won}: {outcome.get('outcome')}{extra}",
         outcome=outcome.get("outcome"))


@_guarded
def quote(dealer_id: str, q: dict) -> None:
    """Every line of the price, and which side of the warranty it fell on.

    A total is a conclusion. What makes a quote arguable, and therefore
    trustworthy, is the line that says the compressor is covered and the four
    hours to fit it are not, next to the line that says where the hourly rate
    came from.
    """
    _say(dealer_id,
         f"  QUOTE {q.get('quote_id')}  {q.get('machine')}",
         subject=q.get("quote_id"), verdict=_money(q.get("total")))

    for line in q.get("lines", []):
        mark = "CHARGE " if line.get("charged") else "COVERED"
        _say(dealer_id,
             f"    {mark} {_money(line.get('amount')):>9}  "
             f"{str(line.get('what'))[:34]:<35} {str(line.get('why'))[:52]}",
             subject=str(line.get("what"))[:60],
             amount=line.get("amount"), charged=bool(line.get("charged")))

    if q.get("covered_by_warranty"):
        _say(dealer_id,
             f"    warranty absorbs {_money(q['covered_by_warranty'])}, "
             f"customer pays {_money(q.get('total'))}",
             covered=q.get("covered_by_warranty"), verdict=_money(q.get("total")))

    if q.get("range"):
        lo, hi = q["range"]
        _say(dealer_id, f"    could run {_money(lo)} to {_money(hi)}: "
                        f"{q.get('range_why')}")

    _say(dealer_id, f"    rate from {q.get('rate_from')}")
