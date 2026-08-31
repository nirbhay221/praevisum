"""The list of things the desk last read out, in the order it read them.

WHAT WENT WRONG WITHOUT IT

A caller asked for an office chair under $500. The desk called `advice`, which
came back with one set of chairs, then `options_under`, which came back with a
different set of five. It read three of the five out loud:

    "the HOOC-NF001 at $147.42, the Serta Works at $139.99, and the
     WorkPro Momentum at $399.99"

The caller said "I want the third one". The third one it had said was the
WorkPro at $399.99. What it ordered was a FlexiSpot C7 at $319.99 -- a real
chair, on our own floor, that had never been mentioned on the call. It came
from the earlier list, which was still sitting in the conversation.

Nothing anchored the ordinal. Two lists were in play, "the third one" is a
position and not a name, and the model resolved it against the wrong one. The
customer agreed to a chair, was charged for a different chair, and every
sentence in the transcript reads as if it went perfectly.

WHY IT IS A REGISTER AND NOT AN INSTRUCTION

Because "only offer what you last looked up" is exactly the kind of rule that
survives two turns of conversation and then loses to the flow of it. This is
the same lesson as the price register next door: the model should not be asked
to carry a position across turns, so the position is kept somewhere it cannot
be summarised away, and the tools check it.

REPLACEMENT, NOT ACCUMULATION, which is the whole point. A new search REPLACES
what the desk is offering. "The third one" means the third thing they were
last told, not the third of everything ever mentioned, and a union of both
lists would have admitted the FlexiSpot and changed nothing.

A PLAIN DICT, NOT A CONTEXTVAR, for the reason written up in tenancy.py and
trace.py: sub-agents run on worker threads and a ContextVar set on the request
thread is invisible there. `options_under` is reached through the `supply`
sub-agent, so a register that could not cross that boundary would be off in
precisely the case it exists for.
"""

from __future__ import annotations

import re
import threading

# call id -> the options last read out on it, in order.
_OFFERED: dict[str, list[dict]] = {}
_LOCK = threading.Lock()


def _here() -> str:
    try:
        from .trace import here

        return here() or ""
    except Exception:
        return ""


def we_offered(rows: list[dict], call_id: str = "") -> list[dict]:
    """These are what the desk is putting in front of them, in this order.

    Replaces anything offered earlier on the call. Returns the rows with a
    position on each, so the list the model reads and the list it is held to
    are the same object.
    """
    numbered = []
    for i, r in enumerate(rows or [], 1):
        d = dict(r)
        d["number"] = i
        numbered.append(d)

    try:
        call_id = call_id or _here()
        if call_id:
            with _LOCK:
                _OFFERED[call_id] = numbered
    except Exception:
        pass
    return numbered


def what_we_offered(call_id: str = "") -> list[dict]:
    """The current shortlist, or nothing if the desk has not offered any."""
    call_id = call_id or _here()
    with _LOCK:
        return list(_OFFERED.get(call_id, ()))


def was_offered(ref: str, call_id: str = "") -> bool:
    """Is this one of the things the desk last read out.

    True when there is no shortlist at all: a customer who names a machine
    outright, with no list in play, is not making a positional reference and
    must not be blocked.
    """
    offered = what_we_offered(call_id)
    if not offered:
        return True

    handle = re.search(r"STK-\d+", (ref or "").upper())
    if not handle:
        return True            # named in words, not by position or handle
    want = handle.group(0)
    return any((o.get("ref") or "").upper() == want for o in offered)


def on_our_own_floor(ref: str) -> bool:
    """Does this handle resolve to a real row this company actually sells.

    WHY THE REFUSAL NEEDED THIS, AND WHY IT IS NOT THE SAME QUESTION.

    The first version of the order guard refused any handle that was not on
    the last shortlist. That is too strong, and it broke a live sale: the desk
    read out a filing cabinet it had found through a different tool, the
    customer said "the first one", and the order was refused as "not one of
    the ones you read out to them" -- which was true of the register and false
    of the conversation. The desk then apologised for an item it had itself
    offered.

    The register only ever sees what `options_under` and the equipment search
    return. The desk can put a machine in front of somebody by other routes,
    and a register that does not know about those must not be the thing that
    decides a sale is fraudulent.

    So the hard refusal is kept for the case it was built for -- a handle that
    is not ours at all, which is how a $2,059 freezer got priced at $19.65 off
    another company's row -- and a handle that IS on our floor is allowed
    through with a read-back instead.
    """
    try:
        from .supply import the_row_behind

        return the_row_behind(ref) is not None
    except Exception:
        return False


def the_one_numbered(n: int, call_id: str = "") -> dict | None:
    """The nth thing the desk read out, counting from one."""
    offered = what_we_offered(call_id)
    if 1 <= n <= len(offered):
        return offered[n - 1]
    return None


def forget_shortlist(call_id: str) -> None:
    """They hung up. Nothing is on offer any more."""
    with _LOCK:
        _OFFERED.pop(call_id or "", None)


# The one they settled on, per call. Separate from the shortlist because a
# list is what we offered and this is what they chose, and the second survives
# a new search replacing the first.
_PICKED: dict[str, dict] = {}


def they_picked(ref: str, call_id: str = "") -> dict:
    """Record which item off the list they actually went for.

    WHY THIS IS NOT THE SAME AS THE SHORTLIST.

    The list is replaced every time the desk searches again. What they CHOSE
    has to outlive that, because the rest of the call is about it: the
    warranty question, the delivery question, the total. Without it every
    later tool call re-derives the machine from whatever words the
    conversation happened to reach for, which is the loop this whole file was
    written to stop.
    """
    call_id = call_id or _here()
    if not call_id or not ref:
        return {}
    for item in what_we_offered(call_id):
        if (item.get("ref") or "").upper() == (ref or "").upper():
            with _LOCK:
                _PICKED[call_id] = item
            return item
    return {}


def the_one_they_picked(call_id: str = "") -> dict:
    """What they settled on, if they have settled on anything."""
    call_id = call_id or _here()
    with _LOCK:
        return dict(_PICKED.get(call_id, {}))


def forget_the_choice(call_id: str) -> None:
    with _LOCK:
        _PICKED.pop(call_id or "", None)


# The order raised on this call. Not a shortlist and not a choice: the one
# piece of paper this conversation created.
_THE_ORDER: dict[str, str] = {}


def we_raised(po_id: str, call_id: str = "") -> None:
    """This call raised this order."""
    call_id = call_id or _here()
    if call_id and po_id:
        with _LOCK:
            _THE_ORDER[call_id] = po_id


def the_order_on_this_call(call_id: str = "") -> str:
    """The order this conversation raised, if it raised one.

    WHY THE MODEL'S ANSWER IS NOT GOOD ENOUGH.

    HEARD LIVE. The desk sold a projector, then tried to confirm PO-44EF8D --
    a real order, for a standing desk, raised on a DIFFERENT CALL half an hour
    earlier. The id existed, so every check that asks "is this one of ours"
    passed it. It just was not this one.

    An order id is exactly the kind of thing a model should never be asked to
    carry, and this is the third time that lesson has cost a sale on this
    desk. The order raised here is a fact; whatever the model produces is a
    recollection.
    """
    call_id = call_id or _here()
    with _LOCK:
        return _THE_ORDER.get(call_id, "")


def forget_the_order(call_id: str) -> None:
    with _LOCK:
        _THE_ORDER.pop(call_id or "", None)
