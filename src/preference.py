"""Who a customer wants sent, and who they would rather not see again.

WHY THIS IS NOT A SOFT PREFERENCE

`find_technician` picks on skills and drive time, which is the right default
and misses the single most common request a service desk actually receives:

    "can you send the same chap as last time, he knew the machine"
    "please not him again"

The first is worth money. An engineer who has been to a site knows where the
isolator is, which door sticks, and what was done last time, and `visits`
already proves who that was. The second is worth more, and there was no way to
record it at all: a customer who has asked not to see somebody and sees them
anyway has been told their complaint went nowhere.

WHY IT IS A REQUEST AND NOT A RULE

An exclusion is honoured absolutely. A preference is not, because the
alternative is worse: holding a job for three days waiting for one person while
a freezer is warm serves nobody, and a preference that silently outranks
availability is how a desk starts making promises it cannot keep.

So a preference RANKS and an exclusion REMOVES, and the difference is stated
in the result rather than buried.

CERTIFICATION STILL WINS

Neither can put somebody in front of a machine they may not legally open.
cover.py holds that rule and this never touches it: a preferred engineer
without the right EPA 608 type is simply not among the candidates by the time
this runs.
"""

from __future__ import annotations

from datetime import datetime

from . import db

PREFER = "prefer"
EXCLUDE = "exclude"


def remember(account_id: str, technician_id: str, kind: str,
             because: str = "", from_call: str = "") -> dict:
    """Record that a customer wants, or does not want, a particular engineer.

    Args:
        account_id: whose preference.
        technician_id: who it is about.
        kind: "prefer" or "exclude".
        because: what they said, in their words. Never required.
        from_call: the conversation it was said on.
    """
    if kind not in (PREFER, EXCLUDE):
        return {"ok": False, "why": "a preference is prefer or exclude"}

    with db.connect() as c:
        who = c.execute("SELECT name FROM technicians WHERE id = ?",
                        (technician_id,)).fetchone()
    if who is None:
        return {"ok": False, "why": "no such engineer"}

    with db.txn() as c:
        c.execute(
            """INSERT INTO crew_preference
                 (account_id,technician_id,kind,because,from_call,noted_on)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(account_id,technician_id) DO UPDATE SET
                 kind=excluded.kind, because=excluded.because,
                 from_call=excluded.from_call, noted_on=excluded.noted_on""",
            (account_id, technician_id, kind, because.strip() or None,
             from_call or None, datetime.now().date().isoformat()))

    if kind == EXCLUDE:
        return {"ok": True, "kind": kind, "technician": who["name"],
                "say": (f"Noted, and it is absolute: {who['name']} will not be "
                        "sent to them again. Do not explain the process or "
                        "ask them to justify it, and do not promise to "
                        "investigate unless they asked for that.")}

    return {"ok": True, "kind": kind, "technician": who["name"],
            "say": (f"Noted. We will send {who['name']} where we can, but do "
                    "NOT promise it: if they are booked and the machine is "
                    "down, the right answer is still the soonest qualified "
                    "person. Say it that way rather than committing.")}


def for_account(account_id: str) -> dict:
    """What this customer has asked for, split by how binding it is."""
    if not account_id:
        return {"prefer": [], "exclude": []}

    try:
        with db.connect() as c:
            rows = c.execute(
                """SELECT p.technician_id, p.kind, p.because, t.name
                   FROM crew_preference p
                   LEFT JOIN technicians t ON t.id = p.technician_id
                   WHERE p.account_id = ?""", (account_id,)).fetchall()
    except Exception as e:
        print(f"[preference] could not read preferences: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"prefer": [], "exclude": []}

    out = {"prefer": [], "exclude": []}
    for r in rows:
        out[r["kind"]].append({"id": r["technician_id"], "name": r["name"],
                               "because": r["because"]})
    return out


def apply_to(candidates: list[dict], account_id: str) -> dict:
    """Filter and reorder a candidate list by what the customer asked for.

    Args:
        candidates: what find_technician produced, each carrying an "id".
        account_id: whose job it is.

    Returns the usable list, who was removed, and why, because a shortlist
    that silently shrank is one nobody can question.
    """
    wants = for_account(account_id)
    excluded = {p["id"] for p in wants["exclude"]}
    preferred = [p["id"] for p in wants["prefer"]]

    kept, dropped = [], []
    for cand in candidates or []:
        if cand.get("id") in excluded:
            name = next((p["name"] for p in wants["exclude"]
                         if p["id"] == cand.get("id")), cand.get("id"))
            dropped.append({"id": cand.get("id"), "name": name,
                            "why": "the customer asked us not to send them"})
            continue
        kept.append(cand)

    # Preference reorders what is left. It never resurrects an exclusion and
    # never overrides certification, because neither is in this list by now.
    kept.sort(key=lambda cand: preferred.index(cand["id"])
              if cand.get("id") in preferred else len(preferred))

    said = ""
    if dropped:
        said = ("Somebody was removed because this customer asked us not to "
                "send them. Do not mention it to the customer and do not "
                "explain the rota.")
    elif preferred and kept and kept[0].get("id") in preferred:
        said = ("Their preferred engineer is available and is first. Say who "
                "is coming, since that is the whole reason they asked.")

    return {"ok": True, "candidates": kept, "removed": dropped,
            "preferred": preferred, "say": said}
