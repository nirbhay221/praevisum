"""Does the outside review source actually answer, and about the right machine?

    .venv/Scripts/python.exe scripts/check_reviews.py

Runs the real makes from this dealer's own book past the live provider and
prints what came back. Deliberately includes the makes expected to FAIL:
commercial refrigeration is not reviewed by consumers anywhere, and a run
where everything succeeds would mean the false-attribution guard is broken
rather than that the world has changed.

One credit per answerable model, two when it falls back to the make.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db, reviews  # noqa: E402

def sample(n: int = 5) -> list[tuple]:
    """Real machines out of the book, half each way on purpose.

    The IT side is reviewed heavily and the refrigeration side is not reviewed
    at all, so a run where everything answers means a guard has broken rather
    than that the world has changed. Read from `assets` rather than written
    here so the check cannot drift away from what the dealer actually holds.
    """
    with db.connect() as c:
        it = c.execute(
            """SELECT DISTINCT manufacturer, model_number FROM assets
               WHERE family IN ('laptop','printer','desktop')
                 AND model_number <> '' ORDER BY manufacturer LIMIT ?""",
            (n,)).fetchall()
        cold = c.execute(
            """SELECT DISTINCT manufacturer, model_number FROM assets
               WHERE (family LIKE '%cooler%' OR family LIKE '%freezer%')
                 AND model_number <> '' ORDER BY manufacturer LIMIT ?""",
            (n,)).fetchall()
    return [(r[0], r[1]) for r in list(it) + list(cold)]


def main() -> None:
    SAMPLE = sample()
    src = reviews.provider()
    if not src:
        print("  no provider configured")
        print("  put SERPER_API_KEY in .env (free tier, no card, any email)")
        return

    print(f"  provider: {src}\n")
    hits = 0
    for make, model in SAMPLE:
        r = reviews.outside_opinion(make, model)
        label = f"{make} {model}".strip()
        if r["available"]:
            hits += 1
            print(f"  {label:<34} {r['rating']} from {r['reviews']:>6} "
                  f"[{r['level']}]  {r.get('matched') or ''}")
        else:
            print(f"  {label:<34} nothing: {r['why']}")

    print(f"\n  {hits} of {len(SAMPLE)} answered")
    print("  Expect the refrigeration makes to come back empty. Nobody")
    print("  reviews a reach-in, and saying so is the correct answer.")

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM outside_reviews").fetchone()[0]
    print(f"  {n} rows cached, so a second run costs no credits")


if __name__ == "__main__":
    main()
