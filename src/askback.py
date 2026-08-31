"""The technician asks a question, instead of only ever closing a job.

THE GAP

`textback.py` reads a technician's reply, writes a repair record and grows the
corpus. It is the best mechanism in this system and it runs one way only.
`desk.py` routes EVERY message from a known technician straight into
close_by_text, so an engineer standing in front of an open machine who texts:

    any idea why this one keeps tripping the breaker?

has that sentence parsed as a job closure. The desk tries to extract a cause
and labour hours from a question.

Meanwhile the company holds 851 repairs it has actually done and a set of
first-line procedures, and the only path to either was the CUSTOMER-facing
"should we send anybody" check. The person with their hands on the machine, who
is the one qualified to act on it, could not reach any of it.

WHAT THIS ANSWERS WITH, AND IN WHAT ORDER

Our own record first. "We saw this on the same model in March and it was the
door heater" is worth more than anything general, because it happened here, to
this company, on this equipment.

Then the wider trade knowledge, clearly labelled as such and never blended
with it. The distinction is the same one reviews.py draws between what we know
and what the world says: a technician deciding what to do next is entitled to
know which is which.

WHAT IT WILL NOT DO

It does not talk anybody through a sealed system. Refrigerant circuits are
pressurised and some of them are propane, and a text message is the wrong
medium for that whoever is reading it. It also does not guess: a question with
nothing behind it gets told so, because an engineer who drives back for a part
we invented has lost an afternoon.
"""

from __future__ import annotations


from . import db

# A question, as opposed to a job being closed out. A closure reads
# "replaced the defrost thermostat, two hours"; a question reads "any idea
# why". Deliberately generous: mistaking a closure for a question costs one
# clarifying reply, while mistaking a question for a closure writes a false
# repair record into the corpus every technician then reads.
_OPENERS = (
    "what", "why", "how", "which", "where", "when", "who", "is", "are",
    "does", "do", "did", "can", "could", "should", "any", "anyone", "anybody",
    "have you", "has anyone", "seen this", "ideas", "idea", "thoughts",
    "help", "stuck",
)

# Anything this touches is behind a panel on a sealed, pressurised system.
_SEALED = ("refrigerant", "recharge", "regas", "charge", "compressor terminal",
           "brazing", "braze", "vacuum pump", "gauges", "high side",
           "low side", "capacitor")

HOW_MANY = 3


def looks_like_a_question(text: str) -> bool:
    """Whether a technician is asking rather than closing.

    THE OPENER HAS TO BE A WHOLE WORD.

    A plain prefix test reads "door heater open circuit, swapped the harness,
    1.5" as a question, because it begins with "do". That is a completed job
    written up exactly as an engineer writes one, and treating it as a question
    means the repair never reaches the corpus at all.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    if "?" in t:
        return True
    return any(t == w or t.startswith(w + " ") or t.startswith(w + ",")
               for w in _OPENERS)


def _their_current_job(phone: str) -> dict | None:
    """The machine this technician is most likely standing in front of."""
    with db.connect() as c:
        row = c.execute(
            """SELECT a.id asset_id, a.manufacturer, a.model_number, a.family,
                      wo.reported_symptom, wo.dealer_id, t.name, t.id tech_id
               FROM technicians t
               JOIN visits v ON v.technician_id = t.id
               JOIN work_orders wo ON wo.id = v.work_order_id
               LEFT JOIN assets a ON a.id = wo.asset_id
               WHERE t.phone = ?
               ORDER BY COALESCE(v.arrived_at, v.promised_at) DESC
               LIMIT 1""", (phone,)).fetchone()
    return dict(row) if row else None


def _what_we_found_before(job: dict, question: str) -> list[dict]:
    """Our own repairs on this model, by meaning rather than by keyword."""
    try:
        from .memory import index_for
    except Exception:
        return []

    query = f"{question} {job.get('reported_symptom') or ''}".strip()
    try:
        hits = index_for(job.get("dealer_id")).search(
            query, model=job.get("model_number") or None, limit=HOW_MANY)
    except Exception:
        return []

    # THE SAME CAUSE THREE TIMES IS ONE FACT, NOT THREE.
    #
    # Searching the corpus for a recurring fault returns the same sentence
    # repeatedly, because it genuinely did happen repeatedly. Reading it out
    # three times wastes a text message and buries anything else that matched.
    #
    # How OFTEN it has been the answer is the useful part: a technician who
    # hears "we have found this three times on this model, most recently in
    # July" knows where to look first in a way that one anonymous instance
    # does not tell them.
    seen: dict[str, dict] = {}
    for h in hits:
        r = getattr(h, "repair", h)
        cause = (getattr(r, "found_cause", "") or "").strip()
        if not cause:
            continue
        when = getattr(r, "closed_on", "") or ""
        if cause in seen:
            seen[cause]["times"] += 1
            seen[cause]["closed_on"] = max(seen[cause]["closed_on"], when)
        else:
            seen[cause] = {"found_cause": cause, "closed_on": when,
                           "times": 1, "model": getattr(r, "model", "")}

    return sorted(seen.values(), key=lambda r: r["times"], reverse=True)


def _what_the_trade_says(job: dict, question: str) -> list[dict]:
    """First-line procedures on file for this kind of machine."""
    from .remote import _overlap

    with db.connect() as c:
        rows = c.execute(
            """SELECT symptom, check_first, instruction, source_ref, safety_note
               FROM remote_fixes
               WHERE (dealer_id IS NULL OR dealer_id = ?)
                 AND (family IS NULL OR family = ?)""",
            (job.get("dealer_id"), job.get("family"))).fetchall()

    asked = f"{question} {job.get('reported_symptom') or ''}"
    scored = []
    for r in rows:
        score = _overlap(asked, r["symptom"] or "")
        if score > 0:
            scored.append((score, dict(r)))

    scored.sort(key=lambda s: s[0], reverse=True)
    return [r for _, r in scored[:HOW_MANY]]


def answer_for_technician(phone: str, question: str) -> dict:
    """Answer an engineer's question from what this company knows.

    Args:
        phone: the technician's number, already matched to a technician.
        question: what they texted.
    """
    job = _their_current_job(phone)
    if job is None:
        return {"ok": False,
                "reply": "I cannot see a job against you at the moment, so I "
                         "do not know which machine you mean. Send the work "
                         "order number and ask again."}

    first = (job.get("name") or "").split(" ")[0] or "there"
    machine = " ".join(x for x in (job.get("manufacturer"),
                                   job.get("model_number")) if x)

    ours = _what_we_found_before(job, question)
    theirs = _what_the_trade_says(job, question)

    if not ours and not theirs:
        return {
            "ok": True, "found": False,
            "reply": (f"{first}, nothing on file for that on the {machine}. "
                      "I am not going to guess at it. If you work it out, "
                      "text the cause when you close and the next person "
                      "gets it."),
        }

    lines = []
    if ours:
        lines.append("What we found before on this model:")
        for r in ours:
            when = (r["closed_on"] or "")[:7]
            times = r.get("times", 1)
            how_often = (f"{times} times, latest {when}" if times > 1
                         else when)
            lines.append(f"- {r['found_cause']}"
                         + (f" ({how_often})" if how_often else ""))

    if theirs:
        lines.append("General trade check, not from our own jobs:")
        for r in theirs:
            bit = r["check_first"] or r["instruction"] or ""
            lines.append(f"- {bit}")

    # A sealed system is not a text-message conversation, whoever is asking.
    asked = (question or "").lower()
    if any(w in asked for w in _SEALED):
        lines.append("Not over text for anything behind the panel: it is "
                     "sealed and pressurised, and some of these run propane. "
                     "Ring the branch.")

    return {
        "ok": True, "found": True,
        "asset_id": job.get("asset_id"),
        "ours": ours, "trade": theirs,
        "reply": f"{first}, on the {machine}:\n" + "\n".join(lines),
    }
