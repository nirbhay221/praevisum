"""Real repair knowledge for the one place this system tells a customer what to do.

    .venv/Scripts/python.exe scripts/load_ifixit.py

WHY THIS EXISTS

`remote_fixes` is the table behind the decision not to send a van. It is the
most safety-sensitive thing here: an unattended agent telling somebody to go
behind a live appliance, at six in the morning, on their own.

Seven of its ten rows were written by hand for the seed and labelled `general`
rather than `manual`, because calling them `manual` would have been a citation
to a document that does not exist. That labelling was honest and the rows were
still somebody's guess at trade knowledge.

iFixit publishes community-maintained troubleshooting pages under a Creative
Commons licence, through an open API with no key. They are real, they are
citable, and each one carries a URL a customer or a technician can go and read.

WHAT MAKES THEM USABLE HERE

The pages are ordered from simplest cause to hardest, which is diagnostic
knowledge rather than formatting:

    No Power, External Temperatures, Overloaded, Thermostat Setting,
    Door Seals, Dirty Condenser Coils, Out of Level          <- a customer can
    Fan Motor, Thermistor, Control Board, Capacitor,
    Relays, Compressor, Refrigerant Leak                     <- a technician must

Only the first kind is loaded. The boundary is the one remote.py already
enforces: nothing that needs a tool, a panel off, a meter, or a refrigerant
circuit opened.

WHY THE CLASSIFIER IS A WORD LIST AND NOT A MODEL

Asking a model which of these a customer may safely attempt would put a
generated judgment in front of somebody standing at a live appliance. A word
list is cruder and it fails in the safe direction: an unfamiliar term is
treated as a technician job and simply not offered.

LICENCE

iFixit wiki content is Creative Commons BY-NC-SA. Each row keeps its source
URL, and `source` is recorded as `ifixit` so nothing here can be mistaken for
a manufacturer's own manual.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

API = "https://www.ifixit.com/api/2.0"

# Pages worth reading, chosen because they match faults this book actually
# sees. Consumer refrigeration rather than commercial, which is a real limit
# and is why the first-line checks transfer and the component work does not.
# One page can serve several families, because a door seal fails the same way
# on a reach-in and a display cooler. Loading it once per family is what lets
# the fitment filter in find_remote_fix do its job.
PAGES = [
    ("Refrigerator Not Cooling",
     ("reach-in cooler", "display cooler", "walk-in cooler")),
    ("Freezer Not Freezing",
     ("reach-in freezer", "walk-in freezer")),
    ("Refrigerator Leaking Water",
     ("reach-in cooler", "display cooler", "ice machine")),
]

# Anything mentioning these is a technician's job. Deliberately generous: a
# term not on the list is treated as unsafe rather than assumed safe, because
# the cost of being wrong is somebody's hand inside a live machine.
TECHNICIAN_ONLY = (
    "capacitor", "relay", "compressor", "refrigerant", "control board",
    "motor", "thermistor", "multimeter", "continuity", "wiring", "solder",
    "evaporator", "condenser fan", "sealed system", "recharge", "voltage",
    "ohm", "terminal", "panel", "disassemb", "screw", "remove the back",
    "defrost heater", "inverter", "compressor start", "electrical",
)

# And these are the shape of thing that IS a customer's job: something they
# can look at, move, clean, or set, without opening anything.
CUSTOMER_SAFE = (
    "door seal", "gasket", "thermostat setting", "overload", "loaded",
    "level", "temperature", "vent", "airflow", "clean", "coil", "power",
    "plug", "breaker", "spacing", "clearance", "food",
)


def _get(path: str) -> dict | list | None:
    try:
        with urllib.request.urlopen(f"{API}/{path}", timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  could not fetch {path}: {type(e).__name__}: {e}")
        return None


def _clean(text: str) -> str:
    """Wiki markup out, plain sentences in."""
    text = re.sub(r"\[\[([^|\]]+)\|?([^\]]*)\]\]", r"\2" if False else r"\1", text)
    text = re.sub(r"\[(?:quote|/quote|br)\]", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"^\*+\s*", "", text, flags=re.M)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    return " ".join(text.split())


def _customer_can_do(heading: str, body: str) -> bool:
    """Would we let somebody try this on their own machine?

    Fails safe. A cause is offered only when nothing in it looks like opening
    the machine AND something in it looks like a check a person can make.
    """
    blob = f"{heading} {body}".lower()
    if any(w in blob for w in TECHNICIAN_ONLY):
        return False
    return any(w in blob for w in CUSTOMER_SAFE)


def main() -> None:
    db.init()

    with db.connect() as c:
        dealers = {r["id"]: (r["families"] or "")
                   for r in c.execute("SELECT id, families FROM dealers")}
        already = {r["source_ref"] for r in c.execute(
            "SELECT source_ref FROM remote_fixes WHERE source = 'ifixit'")}

    rows, skipped = [], 0
    for title, family_list in PAGES:
        page = _get(f"wikis/WIKI/{urllib.parse.quote(title)}")
        if not isinstance(page, dict):
            continue
        raw = page.get("contents_raw") or ""
        url = f"https://www.ifixit.com/Wiki/{title.replace(' ', '_')}"

        causes = re.findall(r"^==\s*(.+?)\s*==\s*\n(.*?)(?=^==\s|\Z)",
                            raw, re.M | re.S)
        kept = 0
        for heading, body in causes:
            if heading.lower() in ("first steps", "related pages",
                                   "additional resources"):
                continue
            text = _clean(body)
            if not text or len(text) < 60:
                continue
            if not _customer_can_do(heading, text):
                skipped += 1
                continue

            ref = f"{url}#{heading.replace(' ', '_')}"
            if ref in already:
                continue

            # The symptom a caller would actually describe, not the page
            # title. "not cooling" is two words and could never clear the
            # match threshold; the cause heading is what carries the words
            # somebody says, so "door seal" finds the door seal page.
            symptom = " ".join([
                title.lower().replace("refrigerator ", "").replace("freezer ", ""),
                heading.lower(),
            ])

            for dealer, families in dealers.items():
                for family in family_list:
                    if family not in families:
                        continue
                    rows.append((
                        f"RF-{uuid.uuid4().hex[:6].upper()}", dealer, family,
                        None, None, None, symptom,
                        f"Is this it: {heading.lower()}?",
                        text[:600], "ifixit", ref, 0,
                        "Community repair documentation, not a manufacturer "
                        "manual. Read it as written and stop if anything needs "
                        "a panel off."))
            kept += 1

        print(f"  {title:<40} {len(causes):>2} causes, {kept} a customer can try")

    if not rows:
        print("\n  nothing new to add")
        return

    with db.txn() as c:
        c.executemany(
            """INSERT INTO remote_fixes
               (id,dealer_id,family,product_type,defrost_type,manufacturer,
                symptom,check_first,instruction,source,source_ref,
                requires_tools,safety_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

    print(f"\n  {len(rows)} procedures loaded, {skipped} left out as technician work")
    with db.connect() as c:
        for r in c.execute("""SELECT source, COUNT(*) n FROM remote_fixes
                              GROUP BY source ORDER BY n DESC"""):
            print(f"    {r['n']:>3} from {r['source']}")


if __name__ == "__main__":
    main()
