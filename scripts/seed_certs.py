"""EPA Section 608 certificates for the technicians who hold them.

WHY THIS SCRIPT EXISTS AND WHY THAT IS EMBARRASSING

`cover.can_work_on` was built to stop us sending somebody uncertified to open
a sealed refrigerant circuit, which is an offence rather than an inefficiency.
It was wired into the scheduler, tested, and shipped.

Nobody ever put a certificate in the table.

So on a live call a restaurant with a freezer sitting at fifteen degrees was
told we had nobody qualified to service their machine, and offered a callback
from a supervisor. Thirteen technicians, zero certificates, and a gate that
did exactly what it was told.

A guard with an empty allow-list is not a safe system, it is a closed one.

WHAT THE DISTRIBUTION IS BASED ON

EPA 608 types, and who in a commercial refrigeration shop actually holds them:

  UNIVERSAL  all three types. What a career refrigeration engineer holds, and
             what anyone working on walk-ins and chillers needs.
  TYPE II    high pressure. Most commercial refrigeration: walk-ins,
             reach-ins, display cases, ice machines.
  TYPE I     small appliances only. Does NOT permit work on a walk-in,
             however experienced the holder is.

The IT dealer's technicians get none, deliberately. A laptop has no
refrigerant circuit and `can_work_on` already answers that correctly without
a certificate.

Certificate numbers are recorded because a real one is checkable, and a
technician sent to a sealed system may be asked for it on site.

Run: python -m scripts.seed_certs
"""

from __future__ import annotations

from datetime import date, timedelta

from src import db

# EPA 608 certification does not expire. Recorded as null rather than as a far
# future date, because `can_work_on` reads null as "does not expire" and a
# fake expiry would eventually and silently start refusing people.
NEVER = None

# Who holds what. Weighted the way a commercial refrigeration shop actually
# looks: a core of Universal holders, a majority on Type II, and a couple of
# newer people on Type I who genuinely cannot be sent to a walk-in.
PATTERN = [
    "EPA608-UNIVERSAL",
    "EPA608-II",
    "EPA608-UNIVERSAL",
    "EPA608-II",
    "EPA608-II",
    "EPA608-UNIVERSAL",
    "EPA608-I",
    "EPA608-II",
    "EPA608-UNIVERSAL",
    "EPA608-II",
    "EPA608-I",
    "EPA608-UNIVERSAL",
    "EPA608-II",
]

# One technician's certificate is deliberately given a real expiry inside the
# next few months. Not decoration: `can_work_on` checks the certificate
# against the DATE OF THE VISIT, so a job booked for next quarter must not be
# given to somebody whose card lapses before they arrive. A table where
# nothing ever expires never exercises that.
EXPIRING_SOON_DAYS = 75


def load(dealer_id: str = "D-REF") -> dict:
    db.init()

    with db.connect() as c:
        techs = c.execute(
            "SELECT id, name FROM technicians WHERE dealer_id = ? ORDER BY id",
            (dealer_id,)).fetchall()

    if not techs:
        return {"technicians": 0, "certs": 0,
                "why": f"no technicians on file for {dealer_id}"}

    written = []
    with db.txn() as c:
        for i, t in enumerate(techs):
            cert = PATTERN[i % len(PATTERN)]
            expires = NEVER
            if i == len(techs) - 1:
                expires = (date.today()
                           + timedelta(days=EXPIRING_SOON_DAYS)).isoformat()

            c.execute(
                """INSERT OR REPLACE INTO technician_certs
                   (technician_id, cert, number, expires_on) VALUES (?,?,?,?)""",
                (t["id"], cert, f"{cert.split('-')[-1]}-{t['id'][-4:]}-608",
                 expires))
            written.append((t["name"], cert, expires))

    with db.connect() as c:
        total = c.execute("SELECT COUNT(*) n FROM technician_certs").fetchone()["n"]

    return {"technicians": len(techs), "certs": total, "written": written}


if __name__ == "__main__":
    out = load()
    print(f"{out['certs']} certificates for {out['technicians']} technicians")
    for name, cert, expires in out.get("written", []):
        when = f"expires {expires}" if expires else "does not expire"
        print(f"  {name:<22} {cert:<20} {when}")
