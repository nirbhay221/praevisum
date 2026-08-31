"""Post everything already recorded onto the loss ledger.

WHY A BACKFILL AND NOT A FRESH START

The losses have been happening all along; only the place to put them is new.
A service visit costed last month cost the same as one costed today, and a
ledger that starts empty tells a dealer their worst product has never cost
them anything.

WHAT IT READS

    visit_cost      parts and labour on a service visit, at cost
    returns         a machine sent back, which is the most expensive kind of
                    unhappy customer there is

Idempotent by construction: the ledger is keyed on (source_table, source_id),
so running this twice posts nothing the second time. That is the property that
makes a backfill safe to re-run when somebody is not sure whether it worked.

    python scripts/build_the_ledger.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db, ledger  # noqa: E402


def main() -> None:
    posted = skipped = 0

    with db.connect() as c:
        visits = [r["visit_id"] for r in c.execute(
            "SELECT visit_id FROM visit_cost")]
    for vid in visits:
        out = ledger.post_a_visit(vid)
        posted += 1 if out.get("posted") else 0
        skipped += 0 if out.get("posted") else 1
    print(f"  service visits: {posted} posted, {skipped} already there or nil")

    # Returns. A machine coming back is a loss even when nothing was repaired:
    # it is stock we bought, shipped and got back, and the customer is gone.
    back = 0
    try:
        with db.connect() as c:
            rows = [dict(r) for r in c.execute(
                """SELECT r.id, r.dealer_id, r.opened_at, r.account_id,
                          r.manufacturer, r.model_number, r.reason,
                          r.condition, r.amount,
                          ps.list_price, ps.family
                   FROM returns r
                   LEFT JOIN product_stock ps
                          ON LOWER(ps.manufacturer) = LOWER(r.manufacturer)
                         AND LOWER(ps.model_number) = LOWER(r.model_number)
                   WHERE r.kind = 'machine'""")]
    except Exception as e:
        print(f"  returns: could not read them ({type(e).__name__}: {e})")
        rows = []

    # WHOSE FAULT IT WAS DECIDES WHAT IT IS EVIDENCE OF.
    #
    # The returns schema makes this distinction itself and says why: "faulty"
    # and "not_as_described" are the model's fault, "changed_mind" and
    # "ordered_wrong" are ours or theirs, and counting them together would
    # make a model look bad because a customer miscounted.
    #
    # Both are real money and both go on the ledger. Only the first kind is
    # attributed to a make and model, because `worth_restocking` reads that
    # attribution as an argument for dropping a product, and a customer
    # changing their mind is not an argument against the freezer.
    THE_MODELS_FAULT = ("faulty", "not_as_described")

    for r in rows:
        price = r.get("list_price")
        # What a return actually costs is handling and remarketing, not the
        # machine: it comes back and most of its value goes out again. Where
        # the refund is recorded, that figure is used instead of an estimate.
        loss = r.get("amount")
        if not loss:
            if not price:
                continue
            loss = round(float(price) * 0.15, 2)

        blames_the_model = (r.get("reason") or "") in THE_MODELS_FAULT
        out = ledger.post(
            "return", float(loss), source_table="returns", source_id=r["id"],
            manufacturer=r.get("manufacturer", "") if blames_the_model else "",
            model_number=r.get("model_number", "") if blames_the_model else "",
            family=r.get("family") or "",
            dealer_id=r.get("dealer_id") or "",
            happened_on=(r.get("opened_at") or "")[:10],
            account_id=r.get("account_id") or "",
            note=f"machine returned: {r.get('reason')}"
                 + ("" if blames_the_model else
                    ", not attributed to the model"))
        back += 1 if out.get("posted") else 0
    print(f"  returned machines: {back} posted")

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM losses").fetchone()[0]
        total = c.execute("SELECT ROUND(SUM(amount),2) FROM losses").fetchone()[0]
    print(f"  ledger now holds {n} entries, ${total or 0:,.2f} at cost")


if __name__ == "__main__":
    main()
