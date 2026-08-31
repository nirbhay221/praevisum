"""Who this business can actually send, and what each of them may touch.

WHY THIS WAS MISSING AND WHY THAT MATTERED

The console showed customers, jobs, parts, offers and machines, and not one
engineer. Eight people, with names, phones, home towns, skills, federal
certifications and a hundred visits each, and the owner could not see any of
them.

That is a strange gap in a dispatch system, because the crew is the constraint.
Stock can be ordered and a price can be changed; the number of people who hold
an EPA 608 Type II on a Tuesday cannot.

WHAT MAKES THIS MORE THAN A STAFF LIST

Section 608 of the Clean Air Act is what legally permits opening a refrigerant
circuit, and its types are not interchangeable: Type I is small appliances,
Type II is high-pressure, and only Universal covers everything. `cover.py`
already refuses to promise a visit from somebody who is not certified for that
machine, which is the enforcement. This is the same fact made visible BEFORE a
customer asks, so an owner can see that one qualification is held by two people
and plan around it rather than discovering it on a Friday.

FIRST-VISIT FIX IS PER PERSON, AND IS NOT A SCORE

It is shown because a fix that did not hold is the one piece of feedback this
business cannot produce for itself, and it belongs next to the person who did
the job. It is NOT a ranking and must never be read as one: an engineer who
takes the awkward jobs will sit lower than one who takes the easy ones, and
punishing that is how a rota stops telling the truth.
"""

from __future__ import annotations

from . import db

# What each EPA 608 type actually permits, in the words a person would use.
# Straight from the rule rather than paraphrased, because a wrong summary here
# is somebody sent to a machine they may not legally open.
WHAT_IT_COVERS = {
    "EPA608-I": "small appliances only, under 5 lb of refrigerant",
    "EPA608-II": "high-pressure systems, which is most commercial refrigeration",
    "EPA608-III": "low-pressure chillers",
    "EPA608-UNIVERSAL": "everything: Types I, II and III",
}


def the_crew(dealer_id: str = "D-REF") -> dict:
    """Every engineer on this desk, with what they hold and what they have done.

    Args:
        dealer_id: whose crew.
    """
    with db.connect() as c:
        people = c.execute(
            """SELECT id, name, phone, email, home_base, van_location, active
               FROM technicians WHERE dealer_id = ?
               ORDER BY active DESC, name""", (dealer_id,)).fetchall()

        skills: dict[str, list[str]] = {}
        for r in c.execute("SELECT technician_id, family FROM technician_skills"):
            skills.setdefault(r["technician_id"], []).append(r["family"])

        certs: dict[str, list[dict]] = {}
        try:
            for r in c.execute(
                    "SELECT technician_id, cert, number, expires_on "
                    "FROM technician_certs"):
                certs.setdefault(r["technician_id"], []).append({
                    "cert": r["cert"],
                    "covers": WHAT_IT_COVERS.get(r["cert"], ""),
                    "number": r["number"],
                    "expires_on": r["expires_on"],
                })
        except Exception as e:
            print(f"[crew] could not read certifications: "
                  f"{type(e).__name__}: {e}", flush=True)

        done = {r["technician_id"]: r["n"] for r in c.execute(
            "SELECT technician_id, COUNT(*) n FROM visits "
            "WHERE technician_id IS NOT NULL GROUP BY technician_id")}

        held = {}
        for r in c.execute(
                """SELECT v.technician_id t, COUNT(*) n,
                          SUM(f.fixed_first_time) ok
                   FROM first_visit_fix f
                   JOIN visits v ON v.work_order_id = f.work_order_id
                   WHERE v.technician_id IS NOT NULL
                   GROUP BY v.technician_id"""):
            if (r["n"] or 0) >= 5:      # below this it is noise, not a rate
                held[r["t"]] = round(100.0 * (r["ok"] or 0) / r["n"], 1)

    out = []
    for p in people:
        item = {
            "id": p["id"],
            "name": p["name"],
            "phone": p["phone"],
            "email": p["email"],
            "based": p["home_base"],
            "van": p["van_location"],
            "active": bool(p["active"]),
            "works_on": sorted(skills.get(p["id"], [])),
            "certified": certs.get(p["id"], []),
            "visits": done.get(p["id"], 0),
        }
        if p["id"] in held:
            item["first_visit_fix"] = held[p["id"]]

        # WHERE THE ROTA AND THE LAW DISAGREE.
        #
        # `technician_skills` says what somebody works on. EPA 608 says what
        # they may legally open. Nothing had ever compared the two, and the
        # first run of this found Dale Hutchins listed for walk-in coolers
        # while holding only Type I, which covers small appliances under five
        # pounds of refrigerant and does not cover a walk-in.
        #
        # cover.py already refuses to PROMISE that visit, so a customer was
        # never at risk. But the rota says he does that work, and a scheduler
        # reading the rota would keep trying, so the contradiction is worth
        # naming rather than being silently absorbed at dispatch.
        item["cannot_legally_touch"] = _mismatches(item["works_on"],
                                                   item["certified"])
        out.append(item)

    # What the crew can cover between them, which is the question an owner is
    # really asking when they look at this.
    covered: dict[str, int] = {}
    for person in out:
        if not person["active"]:
            continue
        for fam in person["works_on"]:
            covered[fam] = covered.get(fam, 0) + 1

    thin = sorted(f for f, n in covered.items() if n <= 1)

    return {
        "ok": True,
        "crew": out,
        "cover": [{"family": f, "people": n}
                  for f, n in sorted(covered.items(), key=lambda kv: kv[1])],
        "only_one_person_covers": thin,
        "say": ("Certification is the constraint, not headcount: EPA 608 types "
                "are not interchangeable and cover.py already refuses a visit "
                "from somebody who does not hold the right one. Anything only "
                "one person covers is a holiday away from being uncovered."),
    }


def _mismatches(works_on: list[str], certified: list[dict]) -> list[dict]:
    """Families somebody is rostered for but not licensed to open.

    Read from cover.NEEDS_CERT so there is one definition of the rule. A
    family that needs no certification at all, an oven or a chair, never
    appears here: this is about refrigerant circuits, not competence.
    """
    from .cover import NEEDS_CERT

    held = {c["cert"] for c in certified}
    out = []
    for family in works_on:
        need = NEEDS_CERT.get(family)
        if not need:
            continue                    # no refrigerant, no certificate
        if held & set(need):
            continue
        out.append({
            "family": family,
            "needs": " or ".join(n.replace("EPA608-", "Type ")
                                 for n in need),
            "holds": (", ".join(c["cert"].replace("EPA608-", "Type ")
                                for c in certified) or "nothing on file"),
        })
    return out
