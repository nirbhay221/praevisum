"""Which vendor, when nobody said.
from .tenancy import the_desk

WHY THIS EXISTS

Twelve tools reachable from a live call took the vendor as a default argument
reading `dealer_id="D-REF"`. On a call the guard now fills the routed vendor
before the tool runs, so that path is correct. But the default is still a
hazard, for two reasons:

  IT NAMES A TENANT. In a system with four businesses behind one number, one
  of them is written into the signature of twelve functions. When routing
  works, that default is silently wrong rather than obviously wrong, which is
  the worst kind. It produced "we don't have that in stock" about a printer we
  had thirteen of, and a freezer escalated to a human because three tools in a
  row asked refrigeration whether anybody was qualified.

  IT SPREADS. The same mistake appeared in four separate places in one day,
  each written by somebody reasonably copying the shape of the function next
  to it. A default that names a tenant is a pattern, and patterns propagate.

Console pages, cron sweeps and scripts genuinely have no caller to read a
vendor from, and they are entitled to a sensible one. So the default stays,
and stops being a hard-coded name.
"""

from __future__ import annotations

import os
import threading

# WHICH VENDOR WHEN NOTHING ROUTED AND NOBODY ASKED.
#
# A console page, a cron sweep or a script has no caller to read a vendor
# from, and picking one alphabetically is not better than picking one by
# name: the first attempt at this returned whichever dealer sorted first,
# which silently moved every unrouted query from refrigeration to audio
# visual and broke twelve tests that were right to complain.
#
# So the fallback is stated once, here, and it is configurable. The gain over
# twelve hard-coded defaults is not that the name disappeared. It is that
# there is now ONE place to change it, and a test that fails if a thirteenth
# function tries to name a tenant itself.
FALLBACK = os.getenv("PRAEVISUM_FALLBACK_DEALER", "D-REF")


# THE VENDOR THIS CALL HAS BEEN ROUTED TO, carried where session state cannot
# reach.
#
# OBSERVED ON A LIVE CALL. The caller asked for a laptop. `route_to_vendor`
# fired and wrote dealer_id=D-IT into session state. `options_under` read it
# and correctly offered a Lenovo IdeaPad at $364.99. The caller said yes, and
# the `supply` SUB-AGENT then answered "we do not carry or sell the Lenovo
# IdeaPad" three times, because a sub-agent is invoked with its own context
# and never saw the write. Twelve were in stock.
#
# That is the exact failure this module was created to end, quoted in its own
# docstring: "it produced 'we don't have that in stock' about a printer we had
# thirteen of."
#
# A ContextVar crosses the boundary that session state does not, because the
# sub-agent runs inside the same async task. guards.py already carries the
# caller's LANGUAGE this way for the same reason.
from contextvars import ContextVar

VENDOR: ContextVar[str] = ContextVar("praevisum_vendor", default="")


def routed_to(dealer_id: str, call_id: str = "") -> None:
    """Remember the vendor for the rest of this call, across sub-agents.

    Written to two places on purpose. The context variable is exact and is
    what a tool on this same task reads. The registry is what a tool running
    on a WORKER THREAD reads, because a context variable does not cross a
    thread and some tool calls are dispatched onto one.
    """
    dealer_id = (dealer_id or "").strip()
    try:
        VENDOR.set(dealer_id)
    except Exception:
        pass

    # THE REGISTRY HAS TO MOVE TOO, AND IT NEEDS NO HELP TO FIND THE CALL.
    #
    # A call opens on the company that was DIALLED and is re-routed the moment
    # the caller says what they want. This updated the context variable and
    # left the registry holding the original company whenever the caller did
    # not pass a call id -- and the registry is what a worker thread reads.
    #
    # Seen live: a laptop enquiry re-routed to the IT company, the session
    # dropped and reconnected, and the next lookup ran as refrigeration, who
    # sell no laptops. The desk truthfully reported that it had none above
    # $2,000 and offered the customer another retailer's, forty seconds after
    # quoting them our own two.
    if not call_id:
        try:
            from .trace import here

            call_id = here()
        except Exception:
            call_id = ""
    if call_id:
        call_started(call_id, dealer_id)


def routed() -> str:
    """The vendor this call was routed to, if anything routed it."""
    try:
        return VENDOR.get() or ""
    except Exception:
        return ""


def the_desk(dealer_id: str = "") -> str:
    """The vendor to use: the one given, else the stated fallback.

    Given, then routed, then the fallback.

    THE MIDDLE ONE WAS MISSING AND IT COST A SALE. This used to read
    `dealer_id or FALLBACK`, and the docstring claimed a live call never
    reaches the fallback because the guard fills the routed vendor in first.
    That is true for a tool the front agent calls directly and FALSE for one
    inside a sub-agent, which is invoked with its own context and never sees
    the routing write.

    So `supply` asked the refrigeration company whether it sold laptops, was
    correctly told no, and said "we do not carry or sell the Lenovo IdeaPad"
    three times to somebody trying to buy one. Twelve were in stock at the IT
    company, one route_to_vendor call away.

    THE CONTEXTVAR WAS NOT ENOUGH, AND THE REASON IS WORTH WRITING DOWN.

    This used to end with a claim: "the routed vendor is a ContextVar, which
    crosses into the sub-agent because it runs in the same async task". That
    is true of an async task and FALSE of a thread, and the framework runs
    some tool calls on a worker thread. A context variable does not cross a
    thread boundary at all:

        routed_to(whoever)
        the_desk()                      -> whoever
        executor.submit(the_desk)       -> the configured fallback

    So on a live call the desk quoted an ASUS Zenbook at $1,399.99 out of the
    IT catalogue, the customer said book it, and the supply agent -- running
    on a worker thread, reading an empty ContextVar, falling back to
    the configured default -- looked for a laptop in the fridge catalogue and reported
    "the ASUS Zenbook isn't on our standard product list". Three of them were
    in stock.

    The registry below survives the hop because it is a plain dictionary. It
    is deliberately NOT a single global "current vendor": that would answer
    confidently during two simultaneous calls to two different companies,
    which is the one failure this system must never have. It answers only
    when every call in flight agrees, and otherwise says nothing and lets the
    caller fall through to the fallback.

    Nothing here widens what a company can see: it narrows what gets asked,
    from "the default one" to "the one this call is actually about".
    """
    if dealer_id:
        return dealer_id

    mine = routed() or _the_only_call_in_flight()
    if mine:
        return mine

    # FALLING BACK DURING A LIVE CALL IS THE BUG SIGNATURE.
    #
    # With nobody on the phone this is a background job or the console, and
    # the configured default is the right answer. DURING A CALL it means the
    # routing was lost, and the caller is about to be served another
    # company's catalogue: a laptop enquiry answered by the refrigeration
    # desk, which truthfully reports it sells no laptops.
    #
    # That is not a data leak -- the isolation check still reports zero rows
    # crossing a boundary -- and it is a wrong answer given confidently,
    # which is worse than an error. Said out loud so it can never happen
    # quietly again.
    try:
        from .trace import here

        if here():
            print(f"[tenancy] a live call fell back to {FALLBACK}: the "
                  "routing was lost and this caller is about to be served "
                  "the wrong company's catalogue", flush=True)
    except Exception:
        pass

    return FALLBACK


# call id -> vendor, for the calls currently connected. A dict rather than a
# context variable because this has to be readable from a worker thread.
_IN_FLIGHT: dict[str, str] = {}
_FLIGHT_LOCK = threading.Lock()


def _the_only_call_in_flight() -> str:
    """The vendor, when every call in progress belongs to the same one.

    Silence when they do not. Guessing between two companies is worse than
    the fallback, because the fallback is at least predictable and this would
    be a tenant leak that depends on timing.
    """
    with _FLIGHT_LOCK:
        vendors = {v for v in _IN_FLIGHT.values() if v}
    if len(vendors) == 1:
        return next(iter(vendors))
    if len(vendors) > 1:
        print(f"[tenancy] {len(vendors)} companies on the phone at once and no "
              f"routing on this thread; refusing to guess", flush=True)
    return ""


def call_started(call_id: str, dealer_id: str = "") -> None:
    """A call connected. Register it so off-thread tools can see its vendor."""
    if not call_id:
        return
    with _FLIGHT_LOCK:
        _IN_FLIGHT[call_id] = dealer_id or ""


def call_ended(call_id: str) -> None:
    """A call hung up. Forget it, so the next one cannot inherit its vendor."""
    with _FLIGHT_LOCK:
        _IN_FLIGHT.pop(call_id, None)
