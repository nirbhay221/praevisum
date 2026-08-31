"""Remove a test caller and everything one of their calls created.

WHY

Testing the first-time-caller path needs the caller to actually be a first
time caller. Ring twice from the same handset and the second call is a
returning customer: the contact already exists, the account has a work order
against it, and `standing()` quite correctly prices them as known rather than
new. The second test silently measures a different thing from the first.

Four calls from one number left one contact, one account renamed twice, three
duplicate Traulsen assets and a work order that had already changed the answer.

WHY THE DELETE ORDER IS DERIVED AND NOT WRITTEN DOWN

The first version of this listed the child tables by hand. It missed nine of
them: call_outcomes, counter_bookings, remote_attempts, returns, wishlist,
purchase_orders, supplier_offers, outreach_queue and appointments all carry a
foreign key into calls, contacts, sites or accounts. The run half-succeeded,
left the contact behind, and reported success for rows it had not removed.

A hand-written list of foreign keys is a copy of the schema that starts
rotting the moment somebody adds a table, and this project has now been bitten
by hand-maintained lists three times: SLOW_TOOLS, the remote source list, and
this. So the schema files are parsed and the order comes from the actual
references. Add a table tomorrow and this keeps working.

WHAT IT WILL NOT DO

It will not touch seeded data. An account with closed repairs against it is
part of the demo corpus and something has gone wrong if a test phone reaches
it, so this refuses rather than tidying it away.

It also refuses to run without a number. There is no "clean everything" mode.

    python -m scripts.forget_caller +18573187009 --dry-run
    python -m scripts.forget_caller +18573187009
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from src import db

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "src"

# How deep to follow a chain of dependents. A grandchild row (a quote_line
# under a quote under an asset) is three hops; ten is far more than this
# schema needs and stops a cycle spinning forever.
MAX_DEPTH = 10


def _foreign_keys() -> dict[str, list[tuple[str, str]]]:
    """Which tables point at which, read out of the schema files.

    Returns {referenced_table: [(child_table, child_column), ...]}.
    """
    sql = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted(SCHEMA_DIR.glob("schema*.sql")))

    refs: dict[str, list[tuple[str, str]]] = {}
    for m in re.finditer(
            r"CREATE TABLE(?: IF NOT EXISTS)? ([a-z_]+)\s*\((.*?)\n\s*\);",
            sql, re.S | re.I):
        child, body = m.group(1), m.group(2)
        for line in body.splitlines():
            line = line.split("--")[0]
            hit = re.match(r"\s*([a-z_]+)\b.*REFERENCES\s+([a-z_]+)\s*\(",
                           line, re.I)
            if hit:
                refs.setdefault(hit.group(2), []).append((child, hit.group(1)))

    # ALTER TABLE ... ADD COLUMN x TEXT REFERENCES y(id)
    for m in re.finditer(
            r"ALTER TABLE\s+([a-z_]+)\s+ADD COLUMN\s+([a-z_]+)\b[^;]*?"
            r"REFERENCES\s+([a-z_]+)\s*\(", sql, re.I):
        refs.setdefault(m.group(3), []).append((m.group(1), m.group(2)))

    return refs


def _cascade(c, table: str, ids: list[str], refs, seen, depth=0) -> list[tuple]:
    """Rows to delete, children before parents.

    Depth-first: everything pointing at these rows goes first, recursively,
    then the rows themselves.
    """
    if not ids or depth > MAX_DEPTH:
        return []
    key = (table, tuple(sorted(ids)))
    if key in seen:
        return []
    seen.add(key)

    marks = ",".join("?" * len(ids))
    out: list[tuple] = []

    for child, column in refs.get(table, []):
        try:
            rows = c.execute(
                f"SELECT * FROM {child} WHERE {column} IN ({marks})",
                tuple(ids)).fetchall()
        except Exception:
            continue          # table not in this deployment
        if not rows:
            continue

        # If the child has its own id, follow what points at IT before
        # removing it. A quote has quote_lines; an asset has work_orders which
        # have visits which have reservations.
        child_ids = [r["id"] for r in rows if "id" in r.keys() and r["id"]]
        if child_ids:
            out += _cascade(c, child, child_ids, refs, seen, depth + 1)

        out.append((child, f"{column} IN ({marks})", tuple(ids), len(rows)))

    out.append((table, f"id IN ({marks})", tuple(ids), len(ids)))
    return out


def forget(phone: str, dry_run: bool = False) -> dict:
    if not phone or not phone.startswith("+"):
        return {"ok": False, "why": "give an E.164 number, like +13095550101"}

    refs = _foreign_keys()

    with db.connect() as c:
        contacts = [r["id"] for r in c.execute(
            """SELECT id FROM contacts
               WHERE id IN (SELECT contact_id FROM phones WHERE e164 = ?)
                  OR id IN (SELECT contact_id FROM calls WHERE from_e164 = ?)""",
            (phone, phone)) if r["id"]]
        calls = [r["id"] for r in c.execute(
            "SELECT id FROM calls WHERE from_e164 = ?", (phone,))]

        if not contacts and not calls:
            return {"ok": True, "nothing": True,
                    "why": f"nothing on file for {phone}"}

        accounts, sites, assets = [], [], []
        if contacts:
            marks = ",".join("?" * len(contacts))
            accounts = [r["account_id"] for r in c.execute(
                f"SELECT DISTINCT account_id FROM contacts WHERE id IN ({marks})",
                tuple(contacts)) if r["account_id"]]
        if accounts:
            marks = ",".join("?" * len(accounts))
            sites = [r["id"] for r in c.execute(
                f"SELECT id FROM sites WHERE account_id IN ({marks})",
                tuple(accounts))]
        if sites:
            marks = ",".join("?" * len(sites))
            assets = [r["id"] for r in c.execute(
                f"SELECT id FROM assets WHERE site_id IN ({marks})",
                tuple(sites))]

        # Seeded history is not test noise. An account carrying closed repairs
        # is part of the corpus the product runs on, and a test handset
        # reaching it means something is wrong that deleting rows would hide.
        if assets:
            marks = ",".join("?" * len(assets))
            n = c.execute(
                f"SELECT COUNT(*) n FROM repairs WHERE asset_id IN ({marks})",
                tuple(assets)).fetchone()["n"]
            if n:
                return {"ok": False,
                        "why": f"{phone} is attached to an account with {n} "
                               "closed repairs against it. That is seeded "
                               "history, not test noise, and something is "
                               "wrong if a test handset reached it."}

        seen: set = set()
        plan: list[tuple] = []
        # Accounts last, because sites and contacts hang off them.
        for table, ids in (("calls", calls), ("assets", assets),
                           ("contacts", contacts), ("sites", sites),
                           ("accounts", accounts)):
            plan += _cascade(c, table, ids, refs, seen)

        # phones has no id column of its own, so it is keyed on the number.
        got = c.execute("SELECT COUNT(*) n FROM phones WHERE e164 = ?",
                        (phone,)).fetchone()["n"]
        if got:
            plan.insert(0, ("phones", "e164 = ?", (phone,), got))

    counted: dict[str, int] = {}
    for table, _, _, n in plan:
        counted[table] = counted.get(table, 0) + n

    if dry_run:
        return {"ok": True, "dry_run": True, "phone": phone,
                "would_remove": counted, "steps": len(plan)}

    failed = []
    with db.txn() as c:
        for table, where, params, _ in plan:
            try:
                c.execute(f"DELETE FROM {table} WHERE {where}", params)
            except Exception as e:
                failed.append(f"{table}: {type(e).__name__}: {e}")

    return {"ok": not failed, "phone": phone, "removed": counted,
            "failed": failed}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit("give a phone number in E.164")

    out = forget(args[0], dry_run="--dry-run" in sys.argv)
    if out.get("nothing"):
        print(out["why"])
        raise SystemExit(0)

    what = out.get("would_remove") or out.get("removed") or {}
    verb = "would remove" if out.get("dry_run") else "removed"
    print(f"{verb} for {out['phone']}:")
    for table, n in sorted(what.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {table}")

    if out.get("failed"):
        print("\nFAILED, nothing was fully removed:")
        for f in out["failed"]:
            print(f"  {f}")
        raise SystemExit(1)
    if not out.get("ok"):
        raise SystemExit(f"refused: {out.get('why')}")
