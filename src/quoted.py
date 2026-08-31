"""The prices this desk has been given on the call it is on.

WHY THE ORDER DID NOT CARRY THE PRICE IT HAD JUST QUOTED

On a live call the desk priced a machine, said the number to the customer,
took the yes, and wrote the order at nothing:

    [tool] price_for({'manufacturer': 'Continental', 'model_number': 'UC24N'})
    [agent] That model is priced at $3100.23.
    [caller] Yes.
    ...
    Razer Blade 18 laptop    confirmed    $0.00

The price existed, was correct, was spoken, and then was thrown away. What
happens next is `_price_the_line` trying to find it AGAIN from the words the
conversation happened to use, and its last resort is to split that phrase on
spaces and call the first word the manufacturer:

    "Razer Blade 18 laptop"  ->  make "Razer", model "Blade 18 laptop"

No listing anywhere is filed under a model number with the word "laptop" on
the end, so the lookup found nothing and the line was written unpriced. The
desk knew the answer thirty seconds earlier and asked the question a second
time, worse.

WHAT THIS KEEPS

The make, the model and the figure, exactly as they were looked up, against
the call they were looked up on. Not parsed out of a sentence afterwards: the
make and model here are the ones the pricing tool was actually given, which is
why they can be matched with confidence later.

WHY IT IS SCOPED TO ONE CALL

A price is a thing said to a person in a conversation. Yesterday's median for
somebody else is not a quote, and carrying one across calls is how a customer
gets held to a number nobody ever said to them. Everything here dies when they
hang up.

A PLAIN DICT, NOT A CONTEXTVAR, and the reason is the same one written up in
tenancy.py and trace.py: the agent runs tool calls on worker threads, a
ContextVar set on the request thread is invisible there, and every guard built
on one was silently switched off in exactly the situation it existed for.
"""

from __future__ import annotations

import re
import threading

# call id -> what was priced on it, oldest first.
_SAID: dict[str, list[dict]] = {}
_LOCK = threading.Lock()


def _here() -> str:
    try:
        from .trace import here

        return here() or ""
    except Exception:
        return ""


def _bits(text: str) -> set[str]:
    """Words and part numbers, with the punctuation taken off.

    Model numbers arrive as "EVC1.5 R6" and get said as "EVC1.5-R6", so
    splitting on anything that is not a letter or a digit is what makes the
    two the same thing.
    """
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w}


def we_said(manufacturer: str, model_number: str, price: float,
            where_from: str = "", call_id: str = "") -> None:
    """Record a price the desk has been given, to quote or to read out.

    Never raises. A price that could not be remembered must not take down the
    call that was pricing it.
    """
    try:
        if not price or price <= 0:
            return
        make = (manufacturer or "").strip()
        model = (model_number or "").strip()
        # A make with no model is not enough to match on later. "True" would
        # price a customer's undercounter freezer off a walk-in.
        if not make or not model:
            return

        call_id = call_id or _here()
        if not call_id:
            return

        entry = {"manufacturer": make, "model_number": model,
                 "price": round(float(price), 2),
                 "where_from": where_from or "",
                 "words": _bits(make) | _bits(model)}
        with _LOCK:
            said = _SAID.setdefault(call_id, [])
            # The same machine priced twice is one quote, the later one.
            said[:] = [s for s in said
                       if not (s["manufacturer"].lower() == make.lower()
                               and s["model_number"].lower() == model.lower())]
            said.append(entry)
            # A call does not quote a hundred machines. This is a bound
            # against a loop, not a policy.
            del said[:-40]
    except Exception:
        pass


def the_price_we_said(text: str, call_id: str = "") -> dict | None:
    """The quote behind this phrase, if the desk gave one on this call.

    Matched on the make AND the model both appearing in what was asked for,
    which is deliberately strict. A model number is distinctive and a make on
    its own is not: "order the Dell" must not be priced off whichever Dell was
    mentioned last.

    The most recent match wins, because a conversation that priced two
    machines and then said "order that one" means the one it just talked
    about.
    """
    try:
        call_id = call_id or _here()
        if not call_id:
            return None
        with _LOCK:
            said = list(_SAID.get(call_id, ()))
        if not said:
            return None

        asked = _bits(text)
        if not asked:
            return None

        for entry in reversed(said):
            if entry["words"] <= asked:
                return {k: v for k, v in entry.items() if k != "words"}
    except Exception:
        pass
    return None


def what_was_quoted(call_id: str = "") -> list[dict]:
    """Everything priced on this call, for anybody who needs to see it."""
    call_id = call_id or _here()
    with _LOCK:
        return [{k: v for k, v in e.items() if k != "words"}
                for e in _SAID.get(call_id, ())]


def forget_quotes(call_id: str) -> None:
    """They hung up. Nothing said on that call is a live quote any more."""
    with _LOCK:
        _SAID.pop(call_id or "", None)
