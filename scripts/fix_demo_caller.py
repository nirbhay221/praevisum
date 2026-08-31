"""Give the number that actually rings this desk a real customer behind it.

WHAT WAS WRONG, AND HOW LONG IT HID

The phone used for every live test resolves like this:

    +18573187009 -> CT-NUL "Arjun Raman" -> A-NUL "Legacy Caller"

A-NUL is a placeholder. No site, no assets, no history. It was almost
certainly written by caller._register the first time that number rang, before
there was anything to attach it to, and it has been the demo customer ever
since.

Every strange thing on two hours of live calls came from that one row:

  THE GREETING opened with the stranger fork, because the caller genuinely
  had no machines. Correct behaviour, and I spent a while suspecting a
  regression that was not there.

  SERVICE ESCALATED TO A HUMAN. The caller described a True freezer, nothing
  could register it against a site that did not exist, so can_we_serve found
  no asset and the desk raised it to a branch manager. Twice, in two
  languages, for a job eight technicians could take.

  ORDERS WERE ORPHANED. Four real purchase orders were written against
  "Legacy Caller" rather than a customer, so asking the desk what had been
  ordered found nothing on the account, and it answered from its own memory of
  the conversation instead.

  AND ONE FOREIGN KEY CRASH mid-call, because an order needs somewhere to
  deliver to and there was nowhere.

THE POINT IS NOT THE DEMO

It is that an account can exist with a contact and a phone and no site, and
nothing anywhere notices until a customer is on the line. The lasting fix is
that ordering for a siteless account should ask for the address and create
one; this script only repairs the row that has been poisoning every test.

    python -m scripts.fix_demo_caller
"""

from __future__ import annotations

from src import db

PHONE = "+18573187009"

SITE = ("S-NUL", "Coriander House",
        "412 Brady Street, Davenport, IA 52801",
        "kitchen entrance at the rear, ring the bell")

# Downtown Davenport. Coordinates matter because the forecast that decides
# whether a marginal machine is urgent this week has to be for where the
# machine actually is, not for the dealer's office.
COORDS = (41.5236, -90.5776)

# A believable book for a small restaurant: two refrigeration machines it
# cannot trade without, and a laptop, so the desk has something to name in the
# greeting and something to route across vendors on.
MACHINES = [
    ("AST-NUL1", "True Refrigeration", "TUC-27F-LP-HC~SPEC3",
     "reach-in freezer", "2024-03-14", "kitchen, back wall", "sold_by_us"),
    ("AST-NUL2", "Avantco Refrigeration", "178Z1RGHC",
     "ice machine", "2023-07-02", "service corridor", "sold_by_us"),
    ("AST-NUL3", "Lenovo", "ThinkPad E14 Gen 6",
     "laptop", "2025-01-20", "office", "customer_stated"),
]


def load() -> dict:
    db.init()

    with db.connect() as c:
        row = c.execute(
            """SELECT ct.id contact_id, a.id account_id, a.name
               FROM phones p
               JOIN contacts ct ON ct.id = p.contact_id
               JOIN accounts a ON a.id = ct.account_id
               WHERE p.e164 = ?""", (PHONE,)).fetchone()

    if row is None:
        return {"ok": False, "why": f"{PHONE} is not on file at all"}

    account = row["account_id"]

    with db.txn() as c:
        # A name somebody would say out loud, rather than "Legacy Caller".
        c.execute("UPDATE accounts SET name=?, kind='business' WHERE id=?",
                  ("Coriander House", account))

        have = c.execute("SELECT id FROM sites WHERE account_id=?",
                         (account,)).fetchone()
        if have is None:
            c.execute(
                """INSERT INTO sites (id,account_id,label,address,access_note)
                   VALUES (?,?,?,?,?)""",
                (SITE[0], account, SITE[1], SITE[2], SITE[3]))
            c.execute("UPDATE sites SET lat=?, lon=? WHERE id=?",
                      (COORDS[0], COORDS[1], SITE[0]))
            site_id = SITE[0]
        else:
            site_id = have["id"]

        added = []
        for aid, make, model, family, on, where, source in MACHINES:
            exists = c.execute("SELECT id FROM assets WHERE id=?",
                               (aid,)).fetchone()
            if exists:
                continue
            c.execute(
                """INSERT INTO assets
                   (id,site_id,manufacturer,model_number,family,
                    installed_on,installed_source,location_note)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (aid, site_id, make, model, family, on, source, where))
            added.append(f"{make} {model}")

    with db.connect() as c:
        n = c.execute(
            """SELECT COUNT(*) n FROM assets a JOIN sites s ON s.id=a.site_id
               WHERE s.account_id=?""", (account,)).fetchone()["n"]

    return {"ok": True, "account": account, "site": site_id,
            "added": added, "machines_now": n}


if __name__ == "__main__":
    out = load()
    if not out.get("ok"):
        print(out["why"])
        raise SystemExit(1)
    print(f"account {out['account']} now has a site and "
          f"{out['machines_now']} machines")
    for m in out["added"]:
        print(f"  added {m}")
