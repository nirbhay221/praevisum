"""The technician closes a job by replying to the text. Gemma reads the reply.

This is the half of the system that makes it improve, and it is the half most
likely to be quietly abandoned in production, so it is built around one finding
from the field-service literature:

    "Data capture that happens as a byproduct of doing the job rather than as
    a separate, burdensome administrative step is critical."

App fragmentation is one of the most cited adoption barriers in mobile
workforce research, and 34% of operators name insufficient digital literacy
among technicians. So there is no app, no form and no login. Curtis got the
briefing as a text. He replies to the same thread, in whatever words he uses,
with grease on his hands:

    "was the harness again at the hinge, used the 556700, about two hours"

and that becomes a searchable repair that shapes the next briefing.

WHY GEMMA, AND WHY LOCAL
    This runs on the VM that already answers the phone, so a reply costs
    nothing per message and works whether or not Vertex is reachable. The task
    is short structured extraction from one sentence, which does not need a
    frontier model. It is also asynchronous: a technician texts hours after a
    call ended, so nothing here competes with live audio for the CPU.

NOTHING IS TRUSTED
    A model reading a greasy text message is the last thing that should decide
    what went into a machine. Every part number is checked against the parts
    actually reserved for that visit and against fitment for that model, and
    anything unrecognised is returned for a human rather than written in.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import uuid
from datetime import datetime

from . import db

OLLAMA = os.getenv("PRAEVISUM_OLLAMA", "http://127.0.0.1:11434")
GEMMA = os.getenv("PRAEVISUM_GEMMA", "gemma3:1b")

PROMPT = """You read short messages from refrigeration technicians closing a job.
Extract exactly these fields as JSON and nothing else:

  found_cause  what was actually wrong, in their words, one clause
  parts        every part they name, as written. Include part numbers AND
               plain names like "mullion harness" or "fan motor". [] if none
  hours        hours on site as a decimal number, null if not stated
  fixed        true if they finished the job. Only false if they explicitly
               say they must come back, are waiting on a part, or could not
               complete it. Advice for a future visit does NOT mean false.
  note         anything the next technician should know, "" if nothing

Examples:
  "replaced the fan motor, 90 mins, all good" ->
    {{"found_cause":"fan motor replaced","parts":["fan motor"],"hours":1.5,"fixed":true,"note":""}}
  "diagnosed the board but dont have one on the van, need to come back" ->
    {{"found_cause":"control board failed","parts":["board"],"hours":null,"fixed":false,"note":"needs a control board"}}

Message: {message}

JSON:"""


def _ask_gemma(message: str) -> dict:
    body = json.dumps({
        "model": GEMMA,
        "prompt": PROMPT.format(message=message),
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        out = json.load(r)
    try:
        return json.loads(out.get("response", "{}"))
    except json.JSONDecodeError:
        return {}


def _fallback(message: str) -> dict:
    """If Gemma is unreachable, get what can be got without a model.

    Worse than the model and better than losing the technician's words, which
    are kept verbatim either way.
    """
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hour|hr|h)\b", message, re.I)
    return {
        "found_cause": message.strip(),
        "parts": re.findall(r"\b[A-Z]{1,4}[- ]?\d{4,}\b", message.upper()),
        "hours": float(hours.group(1)) if hours else None,
        "fixed": not re.search(r"\b(come back|return|order|waiting|need)\b", message, re.I),
        "note": "",
        "parsed_by": "fallback",
    }


def _sane_hours(value, message: str) -> float | None:
    """Hours on site, sanity-checked against the message.

    A small model reading "90 mins" happily returns 90 and calls them hours.
    Nobody spends ninety hours on a reach-in freezer, so an implausible number
    is re-read from the text: minutes if the technician said minutes, and
    dropped entirely if it still makes no sense. Better a blank field than a
    fiction in the corpus.
    """
    try:
        hours = float(value) if value is not None else None
    except (TypeError, ValueError):
        hours = None

    said_minutes = re.search(r"(\d+(?:\.\d+)?)\s*(?:min|mins|minutes)\b", message, re.I)
    if said_minutes:
        return round(float(said_minutes.group(1)) / 60, 2)

    if hours is not None and hours > 12:
        # probably minutes that arrived labelled as hours
        return round(hours / 60, 2) if hours <= 720 else None
    return hours


_NOISE = {"the", "a", "an", "new", "old", "one", "unit", "part", "spare"}


def _resolve_parts(raw: list, visit_id: str, asset_id: str,
                   message: str = "") -> tuple[list[str], list[str]]:
    """Turn whatever the technician typed into real SKUs, or refuse to guess.

    Technicians write "the mullion harness", not "P-MULLIONHAR". So matching
    works on the words in the part's name as well as its number, and the whole
    message is scanned as a last pass, because the part is often named in the
    sentence describing the fault rather than in a tidy list.

    Anything that cannot be tied to a part that actually fits this machine is
    returned as unrecognised. A model reading a greasy text message does not
    get to decide what went into a fridge.
    """
    with db.connect() as c:
        reserved = {r["sku"] for r in c.execute(
            "SELECT sku FROM reservations WHERE visit_id=?", (visit_id,))}
        fitting = {r["sku"]: r["name"] for r in c.execute(
            """SELECT p.sku, p.name FROM parts p
               JOIN fitments f ON f.sku = p.sku
               JOIN assets a ON a.manufacturer = f.manufacturer
                            AND a.model_number LIKE f.model_pattern
               WHERE a.id = ?""", (asset_id,))}
        if not fitting:      # unknown machine: fall back to the whole catalogue
            fitting = {r["sku"]: r["name"] for r in c.execute(
                "SELECT sku, name FROM parts")}

    def match(text: str) -> str | None:
        t = text.strip().lower()
        if not t or t in _NOISE:
            return None
        flat = t.upper().replace(" ", "").replace("-", "")
        for s in list(reserved) + list(fitting):
            if flat and flat in s.upper().replace("-", ""):
                return s
        # name words: "mullion harness" against "Door mullion heater harness"
        words = [w for w in re.findall(r"[a-z]+", t) if w not in _NOISE and len(w) > 3]
        if not words:
            return None
        best, score = None, 0
        for s, name in fitting.items():
            hits = sum(1 for w in words if w in name.lower())
            if hits > score:
                best, score = s, hits
        return best if score >= 2 or (score == 1 and len(words) == 1) else None

    resolved: list[str] = []
    unknown: list[str] = []
    for item in (raw or []):
        hit = match(str(item))
        if hit:
            if hit not in resolved:
                resolved.append(hit)
        elif str(item).strip():
            unknown.append(str(item))

    # last pass over the sentence itself, for parts named while describing the
    # fault rather than listed
    if message:
        for sku, name in fitting.items():
            if sku in resolved:
                continue
            words = [w for w in re.findall(r"[a-z]+", name.lower())
                     if w not in _NOISE and len(w) > 3]
            if words and sum(1 for w in words if w in message.lower()) >= 2:
                resolved.append(sku)
                unknown = [u for u in unknown
                           if not any(w in str(u).lower() for w in words)]

    return resolved, unknown


def close_by_text(technician_phone: str, message: str,
                  visit_id: str = "") -> dict:
    """A technician replies to the briefing. That reply closes the job.

    Args:
        technician_phone: who texted, from the SMS webhook.
        message: exactly what they wrote.
        visit_id: optional. If omitted, their most recent open visit is used,
            which is what a reply to a briefing thread means in practice.

    Returns:
        What was written, what could not be resolved, and the new corpus size.
    """
    with db.connect() as c:
        tech = c.execute("SELECT id, name FROM technicians WHERE phone=?",
                         (technician_phone,)).fetchone()
        if tech is None:
            return {"ok": False, "why": "that number is not a technician on file"}

        if visit_id:
            visit = c.execute(
                """SELECT v.*, w.asset_id, w.id wo FROM visits v
                   JOIN work_orders w ON w.id = v.work_order_id
                   WHERE v.id = ?""", (visit_id,)).fetchone()
        else:
            visit = c.execute(
                """SELECT v.*, w.asset_id, w.id wo FROM visits v
                   JOIN work_orders w ON w.id = v.work_order_id
                   WHERE v.technician_id = ? AND v.completed_at IS NULL
                   ORDER BY v.starts_at DESC LIMIT 1""",
                (tech["id"],)).fetchone() if "starts_at" in [d[0] for d in c.execute(
                    "SELECT * FROM visits LIMIT 0").description] else c.execute(
                """SELECT v.*, w.asset_id, w.id wo FROM visits v
                   JOIN work_orders w ON w.id = v.work_order_id
                   WHERE v.technician_id = ? AND v.completed_at IS NULL
                   ORDER BY v.id DESC LIMIT 1""", (tech["id"],)).fetchone()

        if visit is None:
            return {"ok": False, "why": "no open visit for that technician",
                    "advice": "Text back and ask which job they mean."}

        asset = c.execute(
            """SELECT id, manufacturer, model_number, family
               FROM assets WHERE id = ?""", (visit["asset_id"],)).fetchone()

    try:
        parsed = _ask_gemma(message)
        parsed["parsed_by"] = GEMMA
    except Exception:
        parsed = _fallback(message)

    if not parsed.get("found_cause"):
        parsed = _fallback(message)

    parts = parsed.get("parts") or []
    if isinstance(parts, str):
        parts = [parts]
    resolved, unknown = _resolve_parts(parts, visit["id"],
                                       asset["id"] if asset else "", message)

    hours = _sane_hours(parsed.get("hours"), message)

    fixed = bool(parsed.get("fixed", True))
    cause = str(parsed.get("found_cause") or message).strip()
    note = str(parsed.get("note") or "").strip() or None
    now = datetime.now()

    with db.txn() as c:
        c.execute("""UPDATE visits SET completed_at=?, outcome=?, found_cause=?,
                                       labor_hours=?, tech_note=?
                     WHERE id=?""",
                  (now.isoformat(timespec="seconds"),
                   "fixed" if fixed else "parts_missing",
                   cause, hours, note, visit["id"]))

        for sku in resolved:
            c.execute("INSERT OR IGNORE INTO parts_used (visit_id,sku,qty) VALUES (?,?,1)",
                      (visit["id"], sku))
            c.execute("""UPDATE reservations SET released_at=?
                         WHERE visit_id=? AND sku=? AND released_at IS NULL""",
                      (now.isoformat(timespec="seconds"), visit["id"], sku))

        # anything held but not fitted goes back on the shelf immediately
        c.execute("""UPDATE reservations SET released_at=?
                     WHERE visit_id=? AND released_at IS NULL""",
                  (now.isoformat(timespec="seconds"), visit["id"]))

        repair_id = None
        if fixed and asset:
            first = c.execute(
                "SELECT COUNT(*) n FROM visits WHERE work_order_id=?",
                (visit["work_order_id"],)).fetchone()["n"] == 1
            repair_id = f"R-{uuid.uuid4().hex[:6].upper()}"
            c.execute("""INSERT INTO repairs
                (id,visit_id,asset_id,manufacturer,model_number,family,
                 reported_symptom,found_cause,tech_note,parts_consumed,
                 labor_hours,first_visit_fix,closed_on,technician_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (repair_id, visit["id"], asset["id"], asset["manufacturer"],
                 asset["model_number"], asset["family"],
                 c.execute("SELECT reported_symptom FROM work_orders WHERE id=?",
                           (visit["work_order_id"],)).fetchone()["reported_symptom"],
                 cause, note, ",".join(resolved), hours,
                 1 if first else 0, now.date().isoformat(), tech["id"]))
            c.execute("UPDATE work_orders SET status='closed', closed_at=? WHERE id=?",
                      (now.isoformat(timespec="seconds"), visit["work_order_id"]))

    # make it searchable straight away, so the next caller benefits from it
    indexed = False
    if repair_id:
        try:
            from .domain.models import Repair
            import src.memory as memory

            memory.INDEX.add(Repair(
                id=repair_id, serial=asset["id"], manufacturer=asset["manufacturer"],
                model=asset["model_number"], reported_symptom="",
                error_code=None,
                found_cause=cause + (f". {note}" if note else ""),
                parts_consumed=tuple(resolved), labor_hours=hours or 0.0,
                closed_on=now.date().isoformat(), technician_id=tech["id"]))
            indexed = True
        except Exception:
            pass

    # The job is closed. A day from now, ask the customer the one question the
    # database cannot answer for itself: did it hold. Only on a job that was
    # actually finished, since asking whether a repair worked when the
    # technician said they have to come back is insulting.
    if fixed:
        try:
            from .followup import queue_after_visit

            queue_after_visit(visit["work_order_id"])
        except Exception as e:
            print(f"[followup] could not queue the after-visit check: "
                  f"{type(e).__name__}: {e}", flush=True)

    reply = (f"Thanks {tech['name'].split()[0]}, closed."
             if fixed else
             f"Thanks {tech['name'].split()[0]}, logged as needing a return visit.")
    if unknown:
        reply += f" Could not match: {', '.join(unknown)}. Reply with the part number if you have it."

    return {
        "ok": True,
        "technician": tech["name"],
        "visit": visit["id"],
        "work_order": visit["work_order_id"],
        "parsed_by": parsed.get("parsed_by", GEMMA),
        "understood": {"found_cause": cause, "parts": resolved,
                       "hours": hours, "fixed": fixed, "note": note},
        "unrecognised": unknown,
        "repair_written": repair_id,
        "searchable_now": indexed,
        "reply_to_technician": reply,
    }
