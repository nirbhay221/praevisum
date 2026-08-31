"""Who pays, who is allowed to do the work, and when the customer can be there.

THREE THINGS NOTHING EVER ASKED

WARRANTY
    There was no warranty anywhere in this system. Not a table, not a column,
    not a tool. So the desk would quote four hundred dollars for a control
    board on an eleven month old machine that was covered, confidently, and be
    wrong in the direction that costs the customer money and costs us them.

    It is also the fact a customer is most likely to know that we did not,
    which is the worst possible split: they hang up knowing more about their
    own machine than the company that sold it to them.

CERTIFICATION IS NOT SKILL
    `technician_skills` records that somebody works on reach-in freezers. EPA
    Section 608 is what legally permits opening a refrigerant circuit at all,
    and its types are not interchangeable: Type I is small appliances, Type II
    high pressure, Type III low pressure.

    The briefing already tells a technician that R-290 is flammable and
    charge-limited. It could not tell anybody whether the person being sent
    was licensed to touch it. That is a legal exposure, not a preference, and
    sending an uncertified technician to a sealed system is an offence rather
    than an inefficiency.

WHEN THE CUSTOMER CAN BE THERE
    The diary knew when a technician was free and nothing ever asked when the
    restaurant could take them. A window across a lunch service is a window
    that gets refused, or worse, accepted and then missed, which spends the
    truck roll and the relationship at once.

    Their availability is stored rather than kept in the conversation, because
    a slot gets re-negotiated when a part slips and a window they already
    ruled out must not be offered back to them an hour later.
"""

from __future__ import annotations

from datetime import date, datetime

from . import db
from .tenancy import the_desk

# Which EPA 608 certification permits work on which kind of machine.
#
# Type II is high pressure, which covers most commercial refrigeration.
# Type III is low pressure, which is chillers. Universal is all of them. A
# Type I certification covers small appliances only and does not permit work
# on a walk-in, however experienced the person holding it is.
NEEDS_CERT = {
    "walk-in cooler": ("EPA608-II", "EPA608-UNIVERSAL"),
    "walk-in freezer": ("EPA608-II", "EPA608-UNIVERSAL"),
    "reach-in cooler": ("EPA608-I", "EPA608-II", "EPA608-UNIVERSAL"),
    "reach-in freezer": ("EPA608-I", "EPA608-II", "EPA608-UNIVERSAL"),
    "display cooler": ("EPA608-I", "EPA608-II", "EPA608-UNIVERSAL"),
    "ice machine": ("EPA608-I", "EPA608-II", "EPA608-UNIVERSAL"),
    "blast chiller": ("EPA608-II", "EPA608-III", "EPA608-UNIVERSAL"),
}

# Refrigerants that are flammable and charge-limited. Already surfaced in the
# briefing; repeated here because whether a job touches one of these changes
# who may be sent, not merely what they should expect.
FLAMMABLE = {"R-290", "R290", "R-600A", "R600A", "R-441A", "R-1234YF"}


def warranty_status(asset_id: str) -> dict:
    """Is this machine still covered, and by whom.

    Called before any price is quoted on a service call. A customer told the
    board is four hundred dollars, who then finds it was covered, has been
    given a number that was wrong in the expensive direction.

    Args:
        asset_id: the machine.
    """
    with db.connect() as c:
        a = c.execute(
            """SELECT id, manufacturer, model_number, family, installed_on,
                      warranty_until, warranty_terms, warranty_provider
               FROM assets WHERE id = ?""", (asset_id,)).fetchone()

    if a is None:
        return {"known": False, "why": "unknown machine"}

    if not a["warranty_until"]:
        return {
            "known": False,
            "machine": f"{a['manufacturer']} {a['model_number']}",
            "why": "we hold no warranty date for this machine",
            "say": "Say we do not have a warranty date on file and ask whether "
                   "they still have the paperwork. Do NOT tell them it is out "
                   "of warranty: not knowing and knowing it has expired are "
                   "different things and only one of them is true.",
        }

    today = date.today().isoformat()
    covered = a["warranty_until"] >= today
    return {
        "known": True,
        "covered": covered,
        "machine": f"{a['manufacturer']} {a['model_number']}",
        "until": a["warranty_until"],
        "provider": a["warranty_provider"],
        "terms": a["warranty_terms"],
        "say": (f"This is under warranty with {a['warranty_provider'] or 'the manufacturer'} "
                f"until {a['warranty_until']}. Say so BEFORE quoting anything, "
                "and do not quote a part price as though they were paying it."
                if covered else
                f"The warranty ran out on {a['warranty_until']}, so this is "
                "chargeable. Say that plainly rather than letting them assume "
                "either way."),
    }


def can_work_on(technician_id: str, family: str, refrigerant: str = "",
                on: str = "") -> dict:
    """Is this technician legally permitted to do this job.

    Skill and certification are different questions and only one of them was
    ever asked. Somebody can be the best refrigeration engineer in the state
    and still not hold the certificate that permits opening a sealed system.

    Args:
        technician_id: who.
        family: the equipment family.
        refrigerant: what the machine runs on, if known.
        on: the date of the visit, so a certificate expiring next week is not
            treated as valid for a job the week after.
    """
    when = on or date.today().isoformat()
    family = (family or "").strip().lower()

    with db.connect() as c:
        tech = c.execute("SELECT id, name FROM technicians WHERE id = ?",
                         (technician_id,)).fetchone()
        if tech is None:
            return {"allowed": False, "why": "no such technician"}

        held = {r["cert"]: r["expires_on"] for r in c.execute(
            "SELECT cert, expires_on FROM technician_certs WHERE technician_id = ?",
            (technician_id,))}

    acceptable = NEEDS_CERT.get(family)
    if acceptable is None:
        # Nothing on this family opens a refrigerant circuit, so certification
        # is not the question. A laptop does not need EPA 608.
        return {"allowed": True, "technician": tech["name"],
                "why": "no refrigerant certification is required for this family"}

    valid = [c for c in acceptable
             if c in held and (held[c] is None or held[c] >= when)]
    expired = [c for c in acceptable
               if c in held and held[c] is not None and held[c] < when]

    if valid:
        return {
            "allowed": True, "technician": tech["name"], "holds": valid,
            "flammable": (refrigerant or "").upper() in FLAMMABLE,
            "say": (f"{tech['name']} holds {valid[0]}. This machine runs on "
                    f"{refrigerant}, which is flammable and charge-limited, so "
                    "say that in the briefing."
                    if (refrigerant or "").upper() in FLAMMABLE else ""),
        }

    return {
        "allowed": False,
        "technician": tech["name"],
        "needs": list(acceptable),
        "expired": expired,
        "why": (f"{tech['name']}'s certification expired on {held[expired[0]]}"
                if expired else
                f"{tech['name']} does not hold a certification that covers a "
                f"{family}"),
        "say": "Do not offer this technician for this job. Sending somebody "
               "uncertified to a sealed system is an offence, not an "
               "inefficiency. Find somebody else or say nobody is available.",
    }


def record_availability(site_id: str = "", from_min: int = 0,
                        to_min: int = 0,
                        weekday: int | None = None, note: str = "") -> dict:
    """When the customer says they can take somebody.

    Args:
        site_id: which site.
        from_min: minutes from midnight, so 9am is 540.
        to_min: minutes from midnight, so 11am is 660.
        weekday: 0 is Monday. Omit for every day.
        note: their reason, in their words, so a later slot can explain itself.
    """
    if to_min <= from_min:
        return {"ok": False, "why": "that window ends before it starts"}

    with db.txn() as c:
        c.execute(
            """INSERT INTO site_availability
               (site_id,weekday,from_min,to_min,note,recorded_at)
               VALUES (?,?,?,?,?,?)""",
            (site_id, weekday, from_min, to_min, note or None,
             datetime.now().isoformat(timespec="seconds")))

    return {"ok": True, "site": site_id,
            "window": f"{from_min // 60:02d}:{from_min % 60:02d} to "
                      f"{to_min // 60:02d}:{to_min % 60:02d}",
            "say": "Recorded. Offer only windows inside this, and if nothing "
                   "fits say so rather than offering something they already "
                   "told you does not work."}


def windows_for(site_id: str) -> list[dict]:
    """What this site has told us about when they can take somebody."""
    with db.connect() as c:
        rows = c.execute(
            """SELECT weekday, from_min, to_min, note FROM site_availability
               WHERE site_id = ? ORDER BY weekday, from_min""",
            (site_id,)).fetchall()
    return [dict(r) for r in rows]


def suits_customer(site_id: str, when: datetime) -> bool:
    """Would this slot land inside a window the customer said they could take.

    A site that has told us nothing is treated as available, because assuming
    a restaurant is closed on the strength of no evidence is worse than
    offering a window they can decline.
    """
    windows = windows_for(site_id)
    if not windows:
        return True

    minute = when.hour * 60 + when.minute
    for w in windows:
        if w["weekday"] is not None and w["weekday"] != when.weekday():
            continue
        if w["from_min"] <= minute < w["to_min"]:
            return True
    return False


# ===================================================================
# WHAT THE PUBLISHED TERMS ACTUALLY SAY
# ===================================================================
#
# `warranty_until` above is one date on the asset, entered by hand, and it is
# blank on nearly every machine we hold. Underneath it sit the manufacturers'
# own published terms, loaded by scripts/load_warranties.py with the URL they
# came from, and those terms say three things a single date cannot:
#
#   Wear items are excluded from all of them. A door gasket is chargeable on a
#   machine that is otherwise fully covered, and the door gasket is one of the
#   commonest calls we take.
#
#   Compressor cover outlasts parts and labour cover nearly everywhere. A six
#   and a half year old Traulsen has a covered compressor and nothing else.
#
#   Traulsen ships the replacement compressor and bills the owner for fitting
#   it, so the part is free and the four hours are not.
#
# Coverage is therefore per line, not per machine, and a covered/not-covered
# boolean gets it wrong in both directions.

DAYS_IN_YEAR = 365.25

# Words that mean the fault is in the compressor, the component with its own
# longer clock in every published term we hold.
COMPRESSOR_WORDS = ("compressor", "condensing unit")


def published_terms(manufacturer: str, model_number: str = "") -> dict | None:
    """The manufacturer's own terms for this machine, most specific first.

    Series patterns beat the brand default, because the terms genuinely split
    that way: Beverage-Air's CF and CT lines carry one year where everything
    else carries three. Longest pattern wins, so 'ZPT%' beats 'Z%' beats '%'.
    """
    model = (model_number or "").strip()

    # Some of our model numbers carry the distributor's item number in front
    # of the manufacturer's own: Avantco's Z-series reach-ins are held here as
    # "178Z1RGHC", where 178 is the catalogue prefix and Z1RGHC is the model.
    # Without stripping it, every Z-series machine falls through to the one
    # year brand default and gets told its three year cover has run out.
    stripped = model.lstrip("0123456789")

    with db.connect() as c:
        rows = c.execute(
            """SELECT * FROM warranty_terms
               WHERE manufacturer = ? AND (? LIKE series OR ? LIKE series)""",
            (manufacturer, model, stripped)).fetchall()

        if not rows:
            # A brand default still applies when the model number is unknown.
            rows = c.execute(
                "SELECT * FROM warranty_terms WHERE manufacturer=? AND series='%'",
                (manufacturer,)).fetchall()

    if not rows:
        return None
    return dict(max(rows, key=lambda r: len(r["series"])))


def is_wear_item(name: str) -> dict | None:
    """Is this the kind of part no warranty has ever covered.

    Asked against a part name rather than a SKU, because the answer has to
    hold for a component a technician named in a sentence and we have never
    stocked.
    """
    low = (name or "").strip().lower()
    if not low:
        return None
    try:
        with db.connect() as c:
            for r in c.execute("SELECT pattern, why, source_url FROM wear_items"):
                if r["pattern"] in low:
                    return dict(r)
    except Exception as e:
        # A missing table must not silently promote a gasket to covered.
        print(f"[cover] wear item list unreadable: {type(e).__name__}: {e}",
              flush=True)
    return None


def covers(asset_id: str, part_name: str = "") -> dict:
    """Does the warranty pay for this line, and which half of it.

    Returns the parts and labour answers separately, because they genuinely
    come apart: on a Traulsen compressor the part is covered and the labour to
    fit it is the owner's.

    Args:
        asset_id: the machine.
        part_name: the component, if the fault points at one. Left blank this
            answers for ordinary parts and labour cover.
    """
    with db.connect() as c:
        a = c.execute(
            """SELECT id, manufacturer, model_number, installed_on,
                      warranty_until, installed_source
               FROM assets WHERE id = ?""", (asset_id,)).fetchone()

    if a is None:
        return {"parts": False, "labour": False, "known": False,
                "why": "unknown machine"}

    wear = is_wear_item(part_name)
    if wear is not None:
        return {
            "parts": False, "labour": False, "known": True,
            "wear_item": True,
            "why": f"a {part_name.lower()} is a {wear['why']}",
            "say": "Say this before the number, not after it. A customer who "
                   "believes their machine is under warranty and then gets an "
                   "invoice will not believe the next thing we tell them.",
            "source": wear["source_url"],
        }

    terms = published_terms(a["manufacturer"], a["model_number"])
    if terms is None or not a["installed_on"]:
        # WE SOLD IT, SO WE DO NOT ASK THEM FOR THE RECEIPT.
        #
        # The fallback below tells the desk to ask whether they have the
        # paperwork. That is right for a machine that arrived with them from
        # somewhere else and WRONG for one we sold: the order, the price and
        # the delivery date are all on our own account, and asking a customer
        # to prove a purchase we made is how a warranty becomes an argument.
        #
        # Found on a chair we sold and delivered ourselves, where the desk was
        # about to ask the customer for proof of purchase because we happen to
        # hold no published Serta terms.
        ours = (a["installed_source"] or "") == "sold_by_us"
        if ours:
            return {"parts": False, "labour": False, "known": False,
                    "sold_by_us": True,
                    "since": a["installed_on"] or "",
                    "why": f"we sold this one ourselves"
                           + (f", delivered {a['installed_on']}"
                              if a["installed_on"] else "")
                           + f", and we hold no published terms for "
                             f"{a['manufacturer']}",
                    "say": "Do NOT ask them for paperwork: we sold it, so the "
                           "purchase is on our own books. Say we can see we "
                           "supplied it and on what date, that we are checking "
                           "what the cover runs to, and book the visit anyway. "
                           "Never say it is out of warranty."}

        return {"parts": False, "labour": False, "known": False,
                "why": ("we hold no published warranty terms for "
                        f"{a['manufacturer']}" if terms is None else
                        "we do not know when this machine was installed"),
                "say": "Do NOT say it is out of warranty. Say we cannot see the "
                       "cover from here and ask whether they have the paperwork."}

    try:
        age = (date.today() - date.fromisoformat(a["installed_on"])).days / DAYS_IN_YEAR
    except ValueError:
        return {"parts": False, "labour": False, "known": False,
                "why": "the installation date on file is not readable"}

    compressor = any(w in (part_name or "").lower() for w in COMPRESSOR_WORDS)

    if compressor:
        years = terms["compressor_years"] or 0
        parts_ok = age <= years
        labour_ok = parts_ok and bool(terms["compressor_labour_covered"])
        clock = f"the {years} year compressor term"
    else:
        parts_ok = age <= (terms["parts_years"] or 0)
        labour_ok = age <= (terms["labour_years"] or 0)
        clock = f"the {terms['parts_years']} year parts and labour term"

    # WHOSE DATE IS THIS. A date we wrote down when we sold the machine is a
    # record; a date somebody gave us on the phone ninety seconds ago is a
    # claim. Treating the second as the first meant anybody could ring, say it
    # went in last year, and be quoted zero.
    from .standing import date_provenance

    prov = date_provenance(asset_id)
    proven = prov.get("proven", False)

    out = {
        "parts": parts_ok and proven,
        "labour": labour_ok and proven,
        "claimed_parts": parts_ok,
        "claimed_labour": labour_ok,
        "needs_proof": (parts_ok or labour_ok) and not proven,
        "date_from": prov.get("source"),
        "known": True,
        "age_years": round(age, 1),
        "terms": clock,
        "manufacturer": a["manufacturer"],
        "source": terms["source_url"],
        "read_on": terms["read_on"],
        "condition": terms["condition_note"],
    }

    if parts_ok and not labour_ok and compressor:
        out["why"] = (f"{a['manufacturer']} ship the replacement compressor "
                      "under warranty and bill the owner for fitting it, so "
                      "the part is covered and the labour is not")
        out["say"] = ("Say both halves out loud. Somebody told the compressor "
                      "is covered will not expect an invoice for the four "
                      "hours it takes to fit one.")
    elif parts_ok:
        out["why"] = (f"this machine is {round(age, 1)} years old and {clock} "
                      "has not run out")
    else:
        out["why"] = (f"this machine is {round(age, 1)} years old and {clock} "
                      "has run out")

    if terms["condition_note"] and parts_ok:
        out["say"] = (out.get("say", "") + " " + terms["condition_note"]).strip()

    if out["needs_proof"]:
        out["why"] = (
            f"on the date we were given this would be inside {clock}, but that "
            "date came from the customer rather than from our own paperwork, "
            "so it is a claim rather than something we can grant")
        out["say"] = (
            "Do NOT say it is covered and do NOT quote zero. Say we did not "
            "sell them this machine so we have no warranty paperwork for it, "
            "quote the visit as chargeable, and tell them how to get it "
            "credited: show the invoice to the technician on the day, or send "
            "a photograph of it to us before then.")

    # EXTENDED COVER WE SOLD THEM, which the manufacturer term knows nothing
    # about. Applied after the published term rather than instead of it, and
    # only ever to WIDEN cover: an extension cannot take away something the
    # manufacturer already covers.
    #
    # It does not need `proven`. The provenance rule exists because an install
    # date somebody says on the phone is a claim, and we have no way to check
    # it. An extension is a row we wrote when we sold it, so the date is ours
    # by definition.
    try:
        from .extended import cover_on

        extra = cover_on(asset_id)
    except Exception:
        extra = {"has_cover": False}

    if extra.get("has_cover") and extra.get("live"):
        if extra.get("parts") and not out["parts"]:
            out["parts"] = True
            out["extended_parts"] = True
        if extra.get("labour") and not out["labour"]:
            out["labour"] = True
            out["extended_labour"] = True
        out["extended_to"] = extra["ends_on"]
        out["extended_cover"] = extra["cover"]
        if out.get("extended_parts") or out.get("extended_labour"):
            out["needs_proof"] = False
            out["why"] = (f"the manufacturer term has run out, and they bought "
                          f"{extra['extra_years']:g} extra years from us to "
                          f"{extra['ends_on']}")
            out["say"] = ("Lead with the fact that they are covered. They paid "
                          "for this, so it is not a favour."
                          + ("" if extra.get("labour") else
                             " Parts only, so the labour is chargeable and "
                             "they should hear that before the engineer "
                             "arrives, not after."))

    return out


def can_we_serve(asset_id: str, dealer_id: str = "") -> dict:
    """Can we actually put a qualified person in front of this machine.

    ASKED BEFORE ANYTHING IS PROMISED, and that ordering is the whole point.

    On a real call the desk quoted the job, opened a work order, and only then
    asked the scheduler, which came back with nobody qualified. So a customer
    with a freezer sitting at fifteen degrees had been given a price and a
    work order for a visit that was never going to happen, and the fallback
    offered was a callback from an unnamed supervisor. That is a shrug with a
    job title on it.

    Checking first costs one query. It changes "here is a price, and now some
    bad news" into knowing what we can actually offer before we open our mouth.

    Args:
        asset_id: the machine.
        dealer_id: whose technicians.
    """
    dealer_id = the_desk(dealer_id)
    with db.connect() as c:
        a = c.execute(
            "SELECT id, family, manufacturer, model_number FROM assets WHERE id=?",
            (asset_id,)).fetchone()
        if a is None:
            return {"ok": False, "why": "unknown machine"}

        techs = c.execute("SELECT id, name FROM technicians WHERE dealer_id=?",
                          (dealer_id,)).fetchall()

    family = (a["family"] or "").strip().lower()
    if not family:
        # A null family used to reach the scheduler as "nobody is qualified on
        # None", which reads to a customer as a refusal and is really a gap in
        # our own record.
        return {"ok": False, "unknown_family": True,
                "why": "we do not know what kind of machine this is",
                "say": "Ask them what kind of machine it is, a reach-in, a "
                       "walk-in, an ice machine. Do NOT tell them nobody is "
                       "qualified: that is not what this means."}

    qualified = [t["name"] for t in techs
                 if can_work_on(t["id"], family).get("allowed")]

    if qualified:
        return {"ok": True, "qualified": len(qualified),
                "family": family,
                "why": f"{len(qualified)} of our technicians are certified for "
                       f"a {family}"}

    needs = NEEDS_CERT.get(family, ())
    return {
        "ok": False,
        "qualified": 0,
        "family": family,
        "needs": list(needs),
        "why": f"none of our {len(techs)} technicians hold a certification "
               f"that covers a {family}",
        "say": "Do NOT offer a slot and do NOT take a booking you cannot "
               "staff. Say plainly that this one needs a certification none of "
               "our people currently hold, that you are putting it straight to "
               "the branch manager to arrange cover, and give them a time by "
               "which somebody will ring back. Then call escalate. Never leave "
               "it at 'a supervisor will call you': a restaurant with a "
               "failing freezer needs to know who, and when.",
    }
