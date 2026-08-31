"""Does one company ever see another company's rows.

WHY THIS IS A SCRIPT AND NOT A GLANCE AT THE CODE

A static scan found 133 queries on company-owned tables with no dealer filter
in the SQL. Almost all are scoped by a key that was already resolved for the
right company, so the count says nothing on its own and reads as alarming.

The question that matters is empirical: call the read paths as one company and
see whether anything belonging to another comes back.

    python -m scripts.tenant_leak_check
"""

from __future__ import annotations

from src import db


def _owned(table: str, col: str, dealer: str) -> set:
    with db.connect() as c:
        return {r[0] for r in c.execute(
            f"SELECT {col} FROM {table} WHERE dealer_id = ?", (dealer,))}


def main() -> None:
    with db.connect() as c:
        dealers = [r[0] for r in c.execute("SELECT id FROM dealers ORDER BY id")]
        print("WHAT EACH COMPANY HOLDS")
        for t in ("product_stock", "parts", "accounts", "technicians",
                  "promotions"):
            try:
                rows = c.execute(
                    f"SELECT dealer_id, COUNT(*) FROM {t} GROUP BY dealer_id"
                ).fetchall()
                print(f"  {t:14}",
                      {(r[0] or "NULL"): r[1] for r in rows})
            except Exception as e:
                print(f"  {t:14} {e}")

    from src.book import the_book
    from src.crew import the_crew
    from src.shopfloor import whats_on_the_floor

    print()
    print("READING AS ONE COMPANY, DOES ANOTHER COMPANY APPEAR?")
    total_leaks = 0

    for d in dealers:
        print(f"  --- as {d} ---")

        # The floor returns the column as `model`, not `model_number`. The
        # first version of this compared against a key that does not exist,
        # so every row came back None and every row read as a leak: 923 of
        # them. A check that cries wolf is worse than no check, because the
        # next real one gets ignored.
        floor = whats_on_the_floor(d, "", 500).get("products", [])
        mine = _owned("product_stock", "model_number", d)
        bad = [p for p in floor if (p.get("model") or p.get("model_number"))
               not in mine]
        total_leaks += len(bad)
        print(f"    shop floor  {len(floor):>4} shown  {len(bad)} not ours")
        for p in bad[:2]:
            print(f"        LEAK {p.get('manufacturer')} {p.get('model') or p.get('model_number')}")

        crew = the_crew(d)
        people = crew.get("technicians") or crew.get("crew") or []
        mine = _owned("technicians", "name", d)
        bad = [p for p in people if p.get("name") not in mine]
        total_leaks += len(bad)
        print(f"    crew        {len(people):>4} shown  {len(bad)} not ours")
        for p in bad[:2]:
            print(f"        LEAK {p.get('name')}")

        book = the_book(d, 500)
        mine = _owned("accounts", "name", d)
        bad = [x for x in book["customers"] if x["name"] not in mine]
        total_leaks += len(bad)
        print(f"    customers   {len(book['customers']):>4} shown  "
              f"{len(bad)} not ours")
        for p in bad[:2]:
            print(f"        LEAK {p.get('name')}")

        mine = _owned("technicians", "name", d)
        bad = [x for x in book["crew"] if x["name"] not in mine]
        total_leaks += len(bad)
        print(f"    book crew   {len(book['crew']):>4} shown  "
              f"{len(bad)} not ours")

    print()
    print(f"  TOTAL ROWS CROSSING A COMPANY BOUNDARY: {total_leaks}")


if __name__ == "__main__":
    main()
