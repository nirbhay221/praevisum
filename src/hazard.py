"""Reading complaints for danger, and ringing people before the recall does.

THE IDEA

A dealer holds something no manufacturer and no regulator has: every complaint
its own customers made, about every machine it sold them, in their own words.
Read one at a time those are grumbles. Read together, per model, they are the
earliest possible evidence that something is wrong with a product.

The federal recall notices this desk already carries make the point. The
Bosch dishwasher recall is dated 2017; the Alto-Shaam oven recall is dated
1998. Every one of those was preceded by people ringing their dealer to say
the thing smelled hot. Nobody was reading those calls together.

So: classify each complaint for danger, weigh it by what the machine actually
is, aggregate per model, and when the same model draws dangerous complaints
from more than one customer, ring everybody who owns one. Not to sell them
anything. To tell them to stop using it.

THE VOCABULARY IS NOT INVENTED

The words below are the ones the CPSC actually uses, counted across the 324
real recall records in this database:

    fire 221, overheat 116, burn 113, electrical 32, shock 28,
    short circuit 25, melt 22, ignite 19

and the remedies are theirs too: "unplug and stop using immediately".

WHY THE MACHINE CHANGES THE READING

"Smells odd" is a nuisance on most equipment. On a machine charged with
R-290 it is propane, and this catalogue holds 5,280 machines that run it.
The desk already refuses to send a technician without the certification to
open that circuit. It has never once used the same fact to protect the person
standing next to it.

That asymmetry is the whole reason this file exists.
"""

from __future__ import annotations

from . import db

# Four levels, in the order a desk would escalate them.
NUISANCE = "nuisance"        # irritating, nothing at stake
DEGRADED = "degraded"        # it works badly, and it will fail
UNSAFE = "unsafe"            # somebody could be hurt
DANGEROUS = "dangerous"      # stop using it now

ORDER = (NUISANCE, DEGRADED, UNSAFE, DANGEROUS)

# Straight from the recall corpus. Fire, heat and electricity, in the words
# people actually use on the phone rather than the words a regulator writes.
DANGER_WORDS = (
    "fire", "smoke", "smoking", "burning", "burnt", "flame", "spark",
    "sparks", "sparking", "ignite", "melted", "melting", "shock",
    "shocked", "electrocut", "arcing", "smells hot", "smell of burning",
    "hot to touch", "scorch",
)

UNSAFE_WORDS = (
    "overheat", "overheating", "too hot", "short circuit", "shorting",
    "tripping the breaker", "trips the breaker", "breaker", "exposed wire",
    "bare wire", "water on the floor", "leaking water", "door fell",
    "door detached", "fell off", "gas", "hissing", "leak", "leaking",
    "fumes", "ammonia",
)

DEGRADED_WORDS = (
    "not holding", "warm", "icing", "ice build", "defrost", "noisy",
    "loud", "rattling", "banging", "cycling", "running constantly",
    "running longer", "not cooling", "temperature", "keeps failing",
    "third time", "second time", "again",
)

# A complaint that names heat or electricity on a machine carrying a
# flammable charge is read one level worse than the same words elsewhere.
FLAMMABLE_LIFTS = ("gas", "hissing", "leak", "leaking", "fumes", "smell",
                   "hot", "burning", "spark")

# How many separate CUSTOMERS must report danger on one model before the
# desk treats it as a pattern rather than an incident. Two is deliberate: one
# is an accident, two on the same model is the thing a regulator would want
# to have been told about.
PATTERN_AT = 2


def _worse(a: str, b: str) -> str:
    return a if ORDER.index(a) >= ORDER.index(b) else b


def _is_flammable(manufacturer: str, model_number: str) -> bool:
    """Does this machine carry a flammable charge, per the certified catalogue."""
    if not manufacturer:
        return False
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT refrigerant FROM equipment
                   WHERE brand LIKE ? AND (? = '' OR model_norm LIKE ?)
                   LIMIT 1""",
                (f"%{manufacturer}%", model_number or "",
                 f"%{(model_number or '').upper()[:10]}%")).fetchone()
        if row is None:
            return False
        r = (row["refrigerant"] or "").upper()
        return "290" in r or "600A" in r or "R290" in r
    except Exception:
        return False


def classify(what: str, manufacturer: str = "", model_number: str = "") -> dict:
    """How dangerous is what this customer described.

    Args:
        what: the complaint, in their words.
        manufacturer: the make, used to look up the refrigerant.
        model_number: the model, same.
    """
    low = (what or "").lower()
    level = NUISANCE
    hit = []

    for w in DEGRADED_WORDS:
        if w in low:
            level = _worse(level, DEGRADED)
            hit.append(w)
            break
    for w in UNSAFE_WORDS:
        if w in low:
            level = _worse(level, UNSAFE)
            hit.append(w)
            break
    for w in DANGER_WORDS:
        if w in low:
            level = _worse(level, DANGEROUS)
            hit.append(w)
            break

    flammable = _is_flammable(manufacturer, model_number)
    lifted = False
    if flammable and level != NUISANCE and any(w in low for w in FLAMMABLE_LIFTS):
        # One level worse, because the same sentence means something else on
        # a machine holding propane.
        i = min(ORDER.index(level) + 1, len(ORDER) - 1)
        if ORDER[i] != level:
            level = ORDER[i]
            lifted = True

    return {
        "level": level,
        "matched": hit,
        "flammable_charge": flammable,
        "raised_for_refrigerant": lifted,
        "why": (f"read as {level}"
                + (f", and raised a level because this machine carries a "
                   "flammable charge" if lifted else "")),
    }


def sweep_hazards(dealer_id: str = "") -> dict:
    """Models drawing dangerous complaints from more than one customer.

    The whole point is the aggregate. One person saying their freezer smells
    hot is an incident. Two, on the same model, from different accounts, is a
    pattern, and the dealer is the only party in a position to see it.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)

    with db.connect() as c:
        rows = c.execute(
            """SELECT id, account_id, asset_id, manufacturer, model_number,
                      family, what, raised_at
               FROM complaints
               WHERE dealer_id = ? AND status = 'open'
               ORDER BY raised_at DESC""", (dealer_id,)).fetchall()

    by_model: dict[tuple, dict] = {}
    for r in rows:
        verdict = classify(r["what"], r["manufacturer"], r["model_number"])
        if verdict["level"] not in (UNSAFE, DANGEROUS):
            continue

        key = (r["manufacturer"], r["model_number"])
        entry = by_model.setdefault(key, {
            "manufacturer": r["manufacturer"],
            "model_number": r["model_number"],
            "family": r["family"],
            "flammable_charge": verdict["flammable_charge"],
            "accounts": set(),
            "reports": [],
        })
        entry["accounts"].add(r["account_id"])
        entry["reports"].append({
            "complaint_id": r["id"], "account_id": r["account_id"],
            "asset_id": r["asset_id"], "said": r["what"],
            "level": verdict["level"], "matched": verdict["matched"],
            "raised_at": r["raised_at"],
        })

    patterns = []
    for entry in by_model.values():
        dangerous = [x for x in entry["reports"] if x["level"] == DANGEROUS]
        households = len(entry["accounts"])
        if households < PATTERN_AT or not dangerous:
            continue

        entry["accounts"] = sorted(entry["accounts"])
        entry["dangerous_reports"] = len(dangerous)
        entry["households"] = households
        entry["owners"] = _who_else_owns(dealer_id, entry["manufacturer"],
                                         entry["model_number"])
        patterns.append(entry)

    patterns.sort(key=lambda e: (-e["dangerous_reports"], -e["households"]))
    return {"dealer_id": dealer_id, "patterns": patterns,
            "considered": len(rows)}


def _who_else_owns(dealer_id: str, manufacturer: str, model_number: str) -> list[dict]:
    """Everybody with one of these on their site, complaint or not.

    The people who have not rung yet are the reason to make the call.
    """
    try:
        with db.connect() as c:
            return [dict(r) for r in c.execute(
                """SELECT a.id asset_id, a.location_note, s.id site_id,
                          s.label site, ac.id account_id, ac.name account
                   FROM assets a
                   JOIN sites s ON s.id = a.site_id
                   JOIN accounts ac ON ac.id = s.account_id
                   WHERE a.retired_on IS NULL
                     AND a.manufacturer = ? AND a.model_number = ?
                   ORDER BY ac.name""",
                (manufacturer, model_number))]
    except Exception:
        return []


def _what_happens_next(engineer: str) -> str:
    """The next-step sentence, matched to what was actually arranged.

    Two versions, because there are two truths and only one of them was ever
    told. Somebody who has just switched their only freezer off is entitled
    to know whether a van is coming or whether we are still finding one.
    """
    if engineer.strip():
        return (f"{engineer.strip()} is coming out to take it away and fit a "
                "replacement. There is no charge for any of that and you do "
                "not need to find any paperwork.")
    return ("We are arranging for it to be taken away and replaced, at no "
            "charge to you. I do not have an engineer's name yet and I am not "
            "going to invent one: somebody will ring you back today with a "
            "time.")



def stop_using_it(pattern: dict, engineer: str = "") -> dict:
    """What to say to somebody who owns one, and what to do about it.

    Modelled on the CPSC remedy language this database already carries:
    "Consumers should unplug and stop using the recalled units immediately."
    That is the shape of a safety call. It is not a sales call and it must
    never be dressed as one.

    THE PROMISE HAS TO MATCH WHAT WAS ARRANGED. This said "We are sending an
    engineer to take it out" in every case, including the ones where nothing
    had been booked and nobody had been asked. Saying it anyway is the same
    failure as quoting a price no tool produced, and worse here, because
    somebody who has just switched their only freezer off is relying on it.

    Args:
        pattern: one entry from sweep_hazards.
        engineer: who was actually assigned. Empty means nobody was, and
            the wording changes to match.
    """
    machine = f"{pattern['manufacturer']} {pattern['model_number']}"
    n = pattern["dangerous_reports"]
    homes = pattern["households"]

    unplug = ("Switch it off at the wall and stop using it now, before we "
              "hang up.")
    if pattern.get("flammable_charge"):
        unplug = ("Switch it off at the wall, do not unplug it and do not "
                  "switch anything else on or off near it, open a door or a "
                  "window if you can, and keep people away from it. This "
                  "machine holds a flammable refrigerant, so a spark is the "
                  "thing to avoid.")

    return {
        "machine": machine,
        "level": DANGEROUS,
        "say": (
            f"This is not a sales call and it will take a minute. "
            f"{n} of our customers have reported the same fault on the "
            f"{machine}, across {homes} different sites, and every one of "
            "them described something we treat as dangerous.\n"
            f"{unplug}\n"
            f"{_what_happens_next(engineer)}\n"
            "Do NOT diagnose it for them, do NOT ask them to check anything "
            "themselves, and do NOT ask them to describe the fault first. "
            "The instruction comes before the conversation."
        ),
        "then": "Book the swap, and raise it so somebody follows up today.",
        "evidence": [r["said"] for r in pattern["reports"][:3]],
    }


# --------------------------------------------------------------------------
# acting on it, which is the half that did not exist
# --------------------------------------------------------------------------
#
# Everything above this line DETECTS. It classified complaints, aggregated
# them per model, found every owner, and wrote a script telling somebody to
# switch the machine off at the wall.
#
# Nothing called it. `sweep_hazards` was reachable from one seed script and
# from no part of the running system, so on the live book a Beverage-Air
# HR1HC with three dangerous reports across three sites had twenty-six other
# owners, and not one of them was ever going to hear about it.
#
# Worse than nothing happening: `stop_using_it` already said "We are sending
# an engineer to take it out and put a replacement in." That is a promise, in
# the customer's ear, that no code anywhere kept.

# Nobody is dispatched further than this to a machine that should be switched
# off rather than driven to. Beyond it the honest answer is that somebody has
# to arrange it, not that a van is already on the way.
MAX_DISPATCH_MILES = 60.0


def _nearest_engineer(c, dealer_id: str, site: dict, family: str,
                      refrigerant: str = "") -> dict:
    """The closest engineer who may LEGALLY do this job, or nothing.

    Certification before distance, never the other way round. A propane
    charge is exactly the case where the nearest available person is the
    wrong answer if they cannot open that circuit, and exactly the case where
    somebody would be tempted to send them anyway.
    """
    from .cover import can_work_on
    from .roads import legs_to

    rows = c.execute(
        """SELECT t.id, t.name, t.phone, t.email, t.lat, t.lon
           FROM technicians t
           WHERE t.dealer_id = ? AND t.active = 1""", (dealer_id,)).fetchall()

    # CERTIFICATION FIRST, and only then measure. Filtering before the matrix
    # is also what keeps this inside the free tier: eight engineers of whom
    # three are certified is three elements, not eight.
    eligible = [t for t in rows
                # The key is "allowed". Reading a key that does not exist
                # returns None, which is falsey, which silently excluded EVERY
                # engineer while looking exactly like the rule working.
                if can_work_on(t["id"], family, refrigerant).get("allowed")]
    if not eligible:
        return {}

    # Road distance where a key is configured, straight-line where it is not.
    # The territory is split by the Mississippi and engineers sit on both
    # banks, so a straight line across it is a bridge somebody has to drive to.
    dest = (site.get("lat"), site.get("lon"))
    legs = legs_to(dest, [(t["lat"], t["lon"]) for t in eligible])

    ranked = [{"id": t["id"], "name": t["name"], "phone": t["phone"],
               "email": t["email"], "distance_mi": leg["miles"],
               "drive_minutes": leg["minutes"], "measured": leg["source"]}
              for t, leg in zip(eligible, legs)]
    ranked.sort(key=lambda r: r["drive_minutes"])
    best = ranked[0]
    if best["distance_mi"] is not None and best["distance_mi"] > MAX_DISPATCH_MILES:
        return {}
    return best


def _queue_the_warnings(candidates: list[dict], dealer_id: str) -> dict:
    """Queue, and never let a queueing failure lose the warning silently."""
    try:
        from .outreach import queue_outreach
        return queue_outreach(candidates, dealer_id)
    except Exception as e:
        print(f"[hazard] could not queue {len(candidates)} warnings: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"queued": [], "blocked": [], "error": str(e)[:200]}


def act_on_hazards(dealer_id: str = "", send: bool = True) -> dict:
    """Ring every owner, find who can swap it, and tell that engineer.

    Runs the sweep, then for each model that drew a pattern:

      QUEUE A CALL TO EVERY OWNER of that model, complaint or not. The people
      who have not rung yet are the whole reason to make the call. A hazard is
      a safety kind, so marketing consent does not gate it, the same rule a
      federal recall already followed.

      FIND THE NEAREST ENGINEER legally permitted to touch that circuit, per
      site, and only then by drive time.

      BRIEF THAT ENGINEER once, with every machine they are to pull, rather
      than once per machine.

    Never raises. This runs unattended inside the nightly sweep, and a hazard
    warning that cannot be sent must not stop the recalls queued behind it.
    """
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)
    swept = sweep_hazards(dealer_id)
    if not swept["patterns"]:
        return {"ok": True, "patterns": 0, "owners_found": 0, "queued": 0,
                "assigned": 0, "nobody_to_send": [], "engineers_briefed": [],
                "why": f"nothing dangerous across {swept['considered']} open "
                       "complaints"}

    candidates, jobs, unassigned = [], [], []

    with db.connect() as c:
        for pat in swept["patterns"]:
            machine = f"{pat['manufacturer']} {pat['model_number']}"
            reason = (f"{pat['dangerous_reports']} dangerous reports on the "
                      f"{machine} across {pat['households']} sites")
            evidence = " | ".join(r["said"] for r in pat["reports"][:3])

            for owner in pat["owners"]:
                candidates.append({
                    "kind": "hazard",
                    "account_id": owner["account_id"],
                    "asset_id": owner["asset_id"],
                    "reason": reason,
                    "evidence": evidence,
                })

                site = c.execute(
                    "SELECT id, label, address, lat, lon FROM sites "
                    "WHERE id = ?", (owner["site_id"],)).fetchone()
                # Refrigerant is on the certified catalogue, not the asset:
                # what a machine is charged with is a fact about the model,
                # and the asset row records where this one sits.
                arow = c.execute(
                    """SELECT e.refrigerant FROM assets a
                       LEFT JOIN equipment e ON e.id = a.equipment_id
                       WHERE a.id = ?""", (owner["asset_id"],)).fetchone()
                refrig = ((arow["refrigerant"] if arow else "") or "")

                eng = _nearest_engineer(c, dealer_id,
                                        dict(site) if site else {},
                                        pat["family"] or "", refrig)
                row = {"account": owner["account"],
                       "account_id": owner["account_id"],
                       "asset_id": owner["asset_id"],
                       "site": owner.get("site"), "machine": machine,
                       "flammable": pat["flammable_charge"]}
                if eng:
                    jobs.append({**row, "engineer": eng})
                else:
                    # SAID, not silently dropped. An owner nobody can be sent
                    # to still has to be rung and told to switch it off, and
                    # somebody has to know a van was never arranged.
                    unassigned.append(row)

    queued = _queue_the_warnings(candidates, dealer_id)

    briefed = []
    if send and jobs:
        briefed = _brief_the_engineers(jobs, dealer_id)

    from . import events
    events.publish(dealer_id, "hazard",
                   what=f"hazard on {len(swept['patterns'])} model(s): "
                        f"{len(candidates)} owners to ring, "
                        f"{len(jobs)} swaps assigned, "
                        f"{len(unassigned)} with nobody to send")

    return {
        "ok": True,
        "patterns": len(swept["patterns"]),
        "owners_found": len(candidates),
        "queued": len(queued.get("queued", [])),
        "already_raised": len(queued.get("blocked", [])),
        "assigned": len(jobs),
        "nobody_to_send": unassigned,
        "engineers_briefed": briefed,
        "note": "A hazard call is a safety call: queued regardless of "
                "marketing consent, the same rule a federal recall follows.",
    }


def _brief_the_engineers(jobs: list[dict], dealer_id: str) -> list[dict]:
    """One message per engineer, listing every machine they are to pull.

    Per engineer rather than per machine. Somebody with six of these on their
    round should get one list, not six separate messages, or the sixth is the
    one they stop reading.
    """
    by_engineer: dict[str, dict] = {}
    for j in jobs:
        e = j["engineer"]
        slot = by_engineer.setdefault(e["id"], {"engineer": e, "jobs": []})
        slot["jobs"].append(j)

    sent = []
    for eid, slot in by_engineer.items():
        e = slot["engineer"]
        lines = [f"SAFETY SWAP. {len(slot['jobs'])} machine(s) to pull.", ""]
        for j in slot["jobs"]:
            lines.append(f"  {j['account']} ({j['site']}): {j['machine']}")
        lines.append("")
        lines.append("The customer has already been told to switch it off at "
                     "the wall and stop using it.")
        if any(j["flammable"] for j in slot["jobs"]):
            lines.append("At least one of these holds a FLAMMABLE refrigerant. "
                         "Do not switch anything on or off near it.")
        lines.append("Remove it and fit a replacement. There is no charge to "
                     "the customer and no paperwork for them to find.")
        body = "\n".join(lines)

        ok, how = False, "no email on file for this engineer"
        if e.get("email"):
            try:
                from .email_out import send
                out = send(e["email"], "Safety swap", body,
                           kind="transactional", dealer_id=dealer_id)
                ok, how = bool(out.get("ok")), out.get("why") or "emailed"
            except Exception as ex:
                how = f"{type(ex).__name__}: {ex}"[:120]

        sent.append({"engineer": e["name"], "engineer_id": eid,
                     "machines": len(slot["jobs"]), "sent": ok, "how": how})
    return sent
