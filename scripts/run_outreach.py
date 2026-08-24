"""The sweep a scheduler runs. Nobody starts this by hand.

    .venv/Scripts/python.exe scripts/run_outreach.py
    .venv/Scripts/python.exe scripts/run_outreach.py --show

Every other path through this system begins with a human: a customer rings, or
an owner types. This one begins with a clock.

It scans each dealer's whole book for three things worth a call, ranked
absolutely: a machine under federal safety recall, a complaint that matches
what preceded a failure elsewhere, and something their kit suggests they need
and do not have. Then it queues what consent allows and refuses the rest with
a reason.

Safe to run twice. Duplicates are refused at the queue rather than prevented
by remembering when it last ran, so a scheduler that fires late, twice, or not
at all cannot produce a wrong outcome. That matters more than it sounds:
scheduled jobs are exactly where "it ran twice" bugs live, and the cost here
is ringing a customer twice about the same hazard.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.memory as memory  # noqa: E402
from src import db, outreach  # noqa: E402


def main() -> None:
    show = "--show" in sys.argv

    memory.load_from_db()
    with db.connect() as c:
        dealers = [r["id"] for r in c.execute("SELECT id FROM dealers ORDER BY id")]

    grand = {"recall": 0, "prediction": 0, "offer": 0}
    for dealer in dealers:
        r = outreach.run_sweep(dealer)
        found = r["scanned"]
        got = r["counts"]
        for k in grand:
            grand[k] += got.get(k, 0)

        print(f"\n  {dealer}")
        print(f"    found   recalls {found['recalls']:>3}  "
              f"predictions {found['predictions']:>3}  offers {found['offers']:>3}")
        print(f"    queued  recalls {got.get('recall',0):>3}  "
              f"predictions {got.get('prediction',0):>3}  "
              f"offers {got.get('offer',0):>3}")

        blocked = r["blocked"]
        if blocked:
            from collections import Counter
            why = Counter(b["blocked_because"] for b in blocked)
            print(f"    refused {len(blocked)}: "
                  + ", ".join(f"{n} {reason}" for reason, n in why.most_common()))

        if show:
            for q in r["queued"][:6]:
                print(f"      [{q['kind']}] {q['account_name'][:26]}")
                print(f"              {q['reason'][:70]}")

    print(f"\n  queued this run: {grand}")

    # What a person would actually pick up now.
    for dealer in dealers:
        d = outreach.due_now(dealer)
        if d["ready"] or d["held_for_quiet_hours"]:
            print(f"\n  {dealer} at {d['at']}: "
                  f"{len(d['ready'])} to ring now, "
                  f"{len(d['held_for_quiet_hours'])} held for quiet hours")
            for item in d["ready"][:4]:
                print(f"    {item['priority']:>3}  [{item['kind']}] "
                      f"{item['account'][:28]}")


if __name__ == "__main__":
    main()
