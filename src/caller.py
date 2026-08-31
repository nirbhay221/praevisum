"""Who is on the phone. Resolved when the line connects, before anyone speaks.

This does not belong in a tool. A tool is something the agent chooses to call,
and by then it has already said hello to a stranger it could have greeted by
name. The number arrives in the Twilio `start` event, milliseconds before the
first word, so the lookup happens there and the answer is waiting in session
state when the agent opens its mouth.

Two outcomes, and both are handled here rather than left to the model:

  KNOWN    load their account, sites, machines and last visit, so the first
           sentence can be "is this the Traulsen in the back again?"

  UNKNOWN  register them. A provisional contact and account row exist before
           the greeting finishes, so anything said during the call has
           somewhere to attach. Nobody is left as a floating phone number.
"""

from __future__ import annotations

import re

import uuid
from datetime import datetime

from . import db
from .tenancy import the_desk


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _on_this_call() -> tuple[str | None, str | None]:
    """Who is on the line right now, and where they are.

    The alternative was to hand the contact id to the model and ask it to pass
    it back on every call. A model carrying an identifier is a model that can
    invent one, and an invented contact id writes a stranger's name onto
    somebody else's account. The call row already holds the answer, so it is
    read from there.
    """
    from .trace import CALL, here

    call_id = here()
    if not call_id:
        return None, None
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT c.contact_id, ct.site_id
                   FROM calls c LEFT JOIN contacts ct ON ct.id = c.contact_id
                   WHERE c.id = ?""", (call_id,)).fetchone()
        return (row["contact_id"], row["site_id"]) if row else (None, None)
    except Exception as e:
        print(f"[caller] could not resolve the live call: "
              f"{type(e).__name__}: {e}", flush=True)
        return None, None


def resolve(e164: str, dealer_id: str = "") -> dict:
    """Look up a caller, registering them if we have never spoken before.

    Returns a dict that goes straight into session state. `known` tells the
    agent which conversation it is having: continuing a relationship, or
    starting one.

    IDENTITY IS NOT SCOPED BY VENDOR, AND FOR A WHILE IT WAS.

    When the front counter was split across two numbers, this was scoped to
    the business they rang, for a good reason at the time: an IT customer
    ringing the refrigeration line was greeted by name and had their PRINTERS
    read back to a refrigeration desk, which is worse than not knowing them.

    The scoping fixed that and bought a worse problem. There is one number
    now. A caller whose account happened to sit with the other vendor came
    back as `known: False` and was greeted as a stranger, so nine years of
    history and every machine they own went missing from a call they placed to
    the only number we publish.

    Both symptoms had the same cause, which was answering the phone as a
    vendor. A caller is a caller. They are resolved by their number, they get
    their whole account, and WHICH VENDOR SERVES THEM IS DECIDED BY WHAT THEY
    ASK FOR, in route_to_vendor, per machine and per family, not by whose
    ledger their account happens to sit in.

    Nothing merges underneath. Assets carry no vendor: they hang off the site,
    and the family on each one is what picks the vendor. So reading a
    customer's own equipment back to them cannot cross a tenancy boundary,
    because there is no boundary to cross until somebody asks for something.

    Args:
        e164: the number calling.
        dealer_id: the vendor in front of us when the call opened, used only
            to stamp an account that predates the column. It no longer
            decides whether we know somebody.
    """
    dealer_id = the_desk(dealer_id)
    e164 = (e164 or "").strip()
    if not e164 or e164 == "unknown":
        return {"known": False, "registered": False, "phone": e164,
                "why": "no caller id on this call"}

    with db.connect() as c:
        rows = c.execute(
            """SELECT ct.id contact_id, ct.name, ct.role, ct.channel_pref,
                      a.id account_id, a.name account_name, a.kind account_kind,
                      a.dealer_id, p.label phone_label
               FROM phones p
               JOIN contacts ct ON ct.id = p.contact_id
               JOIN accounts a  ON a.id  = ct.account_id
               WHERE p.e164 = ?""", (e164,)).fetchall()

        # `phones.e164` is a PRIMARY KEY, so a number reaches exactly one
        # contact and one account. Whose ledger that account sits in does not
        # decide whether we know them: they rang the only number we publish.
        row = rows[0] if rows else None

        if row is None:
            return _register(e164, dealer_id)

        # An account with no vendor on it predates that column being filled
        # in. Stamping it with whoever is in front of us keeps the ledgers
        # tidy and changes nothing the caller hears.
        if dealer_id and not row["dealer_id"]:
            try:
                with db.txn() as w:
                    w.execute("UPDATE accounts SET dealer_id=? WHERE id=? "
                              "AND dealer_id IS NULL",
                              (dealer_id, row["account_id"]))
            except Exception as e:
                print(f"[caller] could not stamp the vendor on "
                      f"{row['account_id']}: {type(e).__name__}: {e}",
                      flush=True)

        # Which vendor holds the account, for anything downstream that wants a
        # default before the caller has said what they want. It is not a
        # filter and it is never spoken.
        also = {"account_vendor": row["dealer_id"]} if row["dealer_id"] else {}

        sites = c.execute(
            """SELECT id, label, address, access_note
               FROM sites WHERE account_id = ? ORDER BY label""",
            (row["account_id"],)).fetchall()

        assets = c.execute(
            """SELECT ast.id, ast.manufacturer, ast.model_number, ast.family,
                      ast.location_note, s.label site_label, s.id site_id
               FROM assets ast JOIN sites s ON s.id = ast.site_id
               WHERE s.account_id = ? AND ast.retired_on IS NULL
               ORDER BY s.label, ast.family""",
            (row["account_id"],)).fetchall()

        # The most useful single fact on a service call: what happened last
        # time, and whether it took more than one trip.
        last = c.execute(
            """SELECT w.id, w.reported_symptom, w.opened_at, ast.id asset_id,
                      ast.manufacturer, ast.model_number, ast.family,
                      (SELECT COUNT(*) FROM visits v WHERE v.work_order_id = w.id) visits,
                      (SELECT v.found_cause FROM visits v
                        WHERE v.work_order_id = w.id AND v.found_cause IS NOT NULL
                        ORDER BY v.seq DESC LIMIT 1) found_cause
               FROM work_orders w
               LEFT JOIN assets ast ON ast.id = w.asset_id
               WHERE w.account_id = ? ORDER BY w.opened_at DESC LIMIT 1""",
            (row["account_id"],)).fetchone()

    out = {
        **also,
        "known": True,
        "registered": False,
        "phone": e164,
        "phone_label": row["phone_label"],
        "contact_id": row["contact_id"],
        "contact_name": row["name"],
        "contact_role": row["role"],
        "channel_pref": row["channel_pref"],
        "account_id": row["account_id"],
        "account_name": row["account_name"],
        "account_kind": row["account_kind"],
        "sites": [dict(s) for s in sites],
        "assets": [dict(a) for a in assets],
        "single_site": len(sites) == 1,
        "single_asset": len(assets) == 1,
    }
    # How the last few conversations with them actually went, as against what
    # they own. `took_two_trips` below already proves one fact read from the
    # database can change how a call opens; this is the same shape.
    from .knowing import about

    out["habits"] = about(e164, out)

    if last:
        out["last_job"] = {
            "work_order": last["id"],
            "when": (last["opened_at"] or "")[:10],
            "symptom": last["reported_symptom"],
            "asset_id": last["asset_id"],
            "machine": f"{last['manufacturer']} {last['model_number']}" if last["manufacturer"] else None,
            "family": last["family"],
            "visits": last["visits"],
            "found_cause": last["found_cause"],
            "took_two_trips": (last["visits"] or 0) > 1,
        }
    return out


def _register(e164: str, dealer_id: str = "") -> dict:
    """A number we have never heard from. Give it somewhere to live, now.

    Provisional on purpose: we do not know their name yet, and the agent will
    ask during the call. What matters is that a work order opened two minutes
    from now has an account to hang off, instead of being orphaned or invented.
    """
    dealer_id = the_desk(dealer_id)
    now = datetime.now().isoformat(timespec="seconds")
    account_id = _new_id("A")
    contact_id = _new_id("C")

    with db.txn() as c:
        c.execute(
            # THE DEALER GOES ON THE ACCOUNT. It did not, so every caller
            # this system ever registered automatically was dealer-less: 1 of
            # 107 accounts on the live book, and it was the one belonging to
            # the person testing it. A scoped lookup can never match them, so
            # resolve fell through to here a second time and died on
            # `UNIQUE constraint failed: phones.e164` mid-call.
            "INSERT INTO accounts (id,dealer_id,kind,name,opened_on,notes) "
            "VALUES (?,?,?,?,?,?)",
            (account_id, dealer_id or None, "person", f"New caller {e164}",
             now[:10],
             "created automatically on first call, details not yet confirmed"))
        c.execute(
            "INSERT INTO contacts (id,account_id,name,role,channel_pref) VALUES (?,?,?,?,?)",
            (contact_id, account_id, "unknown", "caller", "sms"))
        c.execute(
            "INSERT INTO phones (e164,contact_id,label,verified) VALUES (?,?,?,?)",
            (e164, contact_id, "inbound", 0))

    return {
        "known": False,
        "registered": True,
        "phone": e164,
        "contact_id": contact_id,
        "account_id": account_id,
        "sites": [],
        "assets": [],
        "collect": ["their name", "the business name if there is one",
                    "the site address", "what equipment it is"],
        "advice": "First time this number has called. A record exists already, "
                  "so open a work order normally. Get their name early and use "
                  "it. Do not ask for an account number, they do not have one.",
    }


def confirm_details(name: str = "", account_name: str = "",
                    site_label: str = "", address: str = "",
                    role: str = "", contact_id: str = "") -> dict:
    """Write down who the caller is, as soon as they say it.

    A new caller is registered as a provisional row before the greeting
    finishes, named "unknown", and until this is called that is all they ever
    become. Everything they say about themselves is lost when the line drops.

    Call this the moment they give you a name or a business, not at the end of
    the call. If the line drops after this, the details survive.

    Args:
        name: the person's name, as they said it.
        account_name: the business they are calling from, if any.
        site_label: what they call the place, "the Davenport kitchen".
        address: the address, if they give one.
        role: what they do there, "kitchen manager".
        contact_id: leave blank. Resolved from the live call.
    """
    contact_id = contact_id or _on_this_call()[0]
    if not contact_id:
        return {"ok": False, "why": "no live call to attach these details to"}

    with db.txn() as c:
        row = c.execute("SELECT account_id FROM contacts WHERE id=?",
                        (contact_id,)).fetchone()
        if row is None:
            return {"ok": False, "why": "unknown contact"}
        account_id = row["account_id"]

        if name:
            c.execute("UPDATE contacts SET name=? WHERE id=?", (name, contact_id))
        if role:
            c.execute("UPDATE contacts SET role=? WHERE id=?", (role, contact_id))
        if account_name:
            c.execute("UPDATE accounts SET name=?, kind=?, notes=NULL WHERE id=?",
                      (account_name, "business", account_id))
        elif name:
            c.execute("UPDATE accounts SET name=?, notes=NULL WHERE id=?",
                      (name, account_id))

        site_id = None
        if site_label or address:
            existing = c.execute("SELECT id FROM sites WHERE account_id=? LIMIT 1",
                                 (account_id,)).fetchone()
            if existing:
                site_id = existing["id"]
                c.execute("UPDATE sites SET label=COALESCE(NULLIF(?,''),label), "
                          "address=COALESCE(NULLIF(?,''),address) WHERE id=?",
                          (site_label, address, site_id))
            else:
                site_id = _new_id("S")
                c.execute("INSERT INTO sites (id,account_id,label,address) VALUES (?,?,?,?)",
                          (site_id, account_id, site_label or (name or "site"), address))
                c.execute("UPDATE contacts SET site_id=? WHERE id=?", (site_id, contact_id))

    # An address is text. The scheduler orders technicians by drive time and
    # refuses a site with no coordinates, so a site that has never been placed
    # on a map cannot be given an appointment at all. Every seeded site had
    # coordinates because the seed wrote them; every site a real call created
    # had none, and the first new customer who rang heard ninety seconds of
    # silence while the desk asked the scheduler six times.
    located = _place_site(site_id, address) if (site_id and address) else None

    out = {"ok": True, "contact_id": contact_id, "account_id": account_id,
           "site_id": site_id, "saved": True}

    if located and located.get("ok"):
        out["located"] = located.get("matched")
    else:
        out["cannot_book_yet"] = True
        out["do_this"] = (
            "ASK FOR THE STREET ADDRESS NOW, before offering any appointment. "
            "We cannot work out which technician is nearest without it, and "
            "the scheduler will refuse every window you ask it for. Asking one "
            "question is far better than going quiet while it says no."
            if not address else
            f"That address did not come back as a real place ({located.get('why')}). "
            "Read it back to them and check it, then call this again.")
    return out


def _place_site(site_id: str, address: str) -> dict:
    """Put a site on the map, so a technician can be sent to it.

    Never raises. A geocoder that is down must not stop us writing down who
    the customer is; it only means we have to ask for the address again later.
    """
    from . import geo

    try:
        found = geo.locate(address)
    except Exception as e:
        print(f"[caller] geocoding failed for {address!r}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": "the lookup did not answer"}

    if found.get("ok"):
        try:
            with db.txn() as c:
                c.execute("UPDATE sites SET lat=?, lon=? WHERE id=?",
                          (found["lat"], found["lon"], site_id))
        except Exception as e:
            print(f"[caller] could not store the location: "
                  f"{type(e).__name__}: {e}", flush=True)
            return {"ok": False, "why": "we could not store it"}
    return found



# The certification catalogue and this trade do not use the same words for the
# same machine, and nothing translated between them.
#
#   catalogue: "Vertical Solid Door Refrigerator"
#   trade:     "reach-in cooler"
#
# Technician skills, NEEDS_CERT and the repair corpus are all keyed on the
# trade word. Writing the catalogue's word onto an asset made every one of
# them miss.
CATALOGUE_TO_TRADE = {
    "vertical solid door freezer": "reach-in freezer",
    "vertical solid door refrigerator": "reach-in cooler",
    "vertical solid door hybrid": "reach-in cooler",
    "vertical transparent door freezer": "display cooler",
    "vertical transparent door refrigerator": "display cooler",
    "vertical transparent door hybrid": "display cooler",
    "horizontal solid door freezer": "reach-in freezer",
    "horizontal solid door refrigerator": "reach-in cooler",
    "horizontal transparent door refrigerator": "display cooler",
    "chef base freezer": "reach-in freezer",
    "chef base refrigerator": "reach-in cooler",
    "service over counter": "display cooler",
    "ice making head": "ice machine",
    "self contained unit": "ice machine",
    "remote condensing unit": "ice machine",
}


def _trade_word(product_type: str) -> str:
    """Turn the catalogue's name for a machine into the trade's.

    Returns "" rather than guessing. An unmapped type is better recorded as
    nothing, because a family nobody is skilled on reads to a customer as
    "nobody here can fix your freezer".
    """
    low = (product_type or "").strip().lower()
    if low in CATALOGUE_TO_TRADE:
        return CATALOGUE_TO_TRADE[low]

    # A word-level fallback for types not listed above. Freezer before
    # refrigerator: "Vertical Solid Door Freezer" contains neither word twice,
    # but a hybrid mentions both and the colder duty is the one that decides
    # who may work on it.
    # WORD BOUNDARIES, NOT SUBSTRINGS. "ice" appears inside dev-ice-s,
    # serv-ice and vo-ice, and all three are real product types on this
    # catalogue: "Multifunction Devices (MFD)", "Service Over Counter" and
    # "Voice over Internet Protocol (VoIP)". Two of them are IT equipment.
    #
    # Classifying a printer as an ice machine is not a label problem. Dispatch
    # then looks for somebody holding EPA 608 to go and fix it, and either
    # sends a refrigeration engineer to a photocopier or refuses the job
    # because nobody qualifies.
    words = re.findall(r"[a-z]+", low)

    if "ice" in words:
        return "ice machine"
    if any(w.startswith("freezer") for w in words):
        return "reach-in freezer"
    # A serve-over counter is a display case, not an ice maker.
    if "counter" in words and ("service" in words or "serve" in words):
        return "display cooler"
    if any(w.startswith("refrigerat") for w in words) or "cooler" in words:
        return "reach-in cooler"
    return ""


def _family_for(manufacturer: str, model_number: str) -> str:
    """What kind of machine this is, when nobody said.

    A NULL family is not a cosmetic gap. On a real call it produced:

        next_available_slot -> {'ok': False, 'why': 'nobody is qualified on None'}

    so the customer was told we had nobody who could service their freezer,
    and separately the quote fell back to an assumed 1.5 hours instead of the
    median of the jobs we have actually done on that kind of machine. One null
    column, two wrong answers, neither of them obviously about the null.

    Asked of three sources in order of authority: the certified catalogue, our
    own machines of the same model, then the repair corpus. All three are
    facts we already hold rather than a guess from the model number.
    """
    if not manufacturer or not model_number:
        return ""

    norm = model_number.upper().replace("-", "").replace(" ", "").replace("/", "")
    try:
        with db.connect() as c:
            # OUR OWN MACHINES FIRST, because they already speak the right
            # language. The catalogue calls a reach-in cooler a "Vertical
            # Solid Door Refrigerator", and technicians' skills and the EPA
            # certification table are both keyed on the trade word. Asking the
            # catalogue first put "Vertical Solid Door Refrigerator" on a real
            # customer's machine mid-call, and the scheduler answered "nobody
            # is qualified on Vertical Solid Door Refrigerator" and escalated
            # a job eight certified technicians could have taken.
            #
            # A wrong family is worse than the null it was written to replace:
            # a null is visibly missing, and this looked like an answer.
            row = c.execute(
                """SELECT family FROM assets
                   WHERE manufacturer = ? AND model_number = ?
                     AND family IS NOT NULL LIMIT 1""",
                (manufacturer, model_number)).fetchone()
            if row:
                return row["family"]

            row = c.execute(
                """SELECT product_type FROM equipment
                   WHERE brand LIKE ? AND model_norm = ? LIMIT 1""",
                (f"%{manufacturer}%", norm)).fetchone()
            if row and row["product_type"]:
                return _trade_word(row["product_type"])

            row = c.execute(
                """SELECT family, COUNT(*) n FROM repairs
                   WHERE manufacturer = ? AND family IS NOT NULL
                   GROUP BY family ORDER BY n DESC LIMIT 1""",
                (manufacturer,)).fetchone()
            if row:
                return row["family"]
    except Exception as e:
        print(f"[caller] could not infer a family: {type(e).__name__}: {e}",
              flush=True)
    return ""


def register_asset(manufacturer: str, model_number: str, family: str = "",
                   location_note: str = "", installed_on: str = "",
                   site_id: str = "") -> dict:
    """Record a machine we did not know a customer had.

    Links to the certified catalogue when the model is found there, and works
    perfectly well when it is not: plenty of real equipment was never
    submitted for certification.

    ONLY FOR A MACHINE THEY ALREADY OWN. Not one they are buying. On a live
    call the desk registered a True TUC-27F as an asset at the customer's site
    because they were asking to have one delivered, which put a machine they
    do not own on their account and then sent the job to the engineer diary.
    A machine being sold is a purchase order, not an asset, and it becomes an
    asset when it is installed.

    ASK WHEN IT WENT IN. It used to be recorded as unknown always, which meant
    a new customer's machine could never be checked against the manufacturer's
    warranty terms, so every one of them was told we could not see their cover.
    "Roughly when did you put it in?" is one question and it is the difference
    between quoting somebody for a repair that is free.

    Args:
        manufacturer: the make.
        model_number: off the rating plate.
        family: reach-in freezer, walk-in cooler, ice machine.
        location_note: "kitchen, back wall".
        installed_on: when it went in, as YYYY-MM-DD. A year alone is fine:
            pass 2023-01-01 if all they remember is the year, because a year
            is enough to answer most warranty questions and unknown answers
            none of them.
        site_id: leave blank. Resolved from the live call.

    ALREADY THEIRS IS NOT A NEW MACHINE.

    On a live call the desk could not resolve the caller's ice machine from
    the model number it kept passing, so it registered one. The customer ended
    the call owning two Avantco 178Z1RGHCs, one of which had the warranty and
    the repair history and one of which had neither.

    A second copy is worse than a failed lookup: cover attaches to the wrong
    one, prior_repairs finds nothing against the new id, and the next call has
    to choose between two identical machines.
    """
    site_id = site_id or _on_this_call()[1]
    if not site_id:
        return {"ok": False, "why": "we do not know which site this machine is at"}

    # "UNKNOWN" IS NOT A MANUFACTURER.
    #
    # Observed live, minutes after the customer bought a Koolmore freezer from
    # us. The desk could not resolve it, so it registered a NEW machine with
    # manufacturer "unknown" and model "unknown", quoted a visit against that
    # blank asset, and -- because a blank has no purchase behind it and no
    # warranty terms -- told the customer the repair would cost $240.85. On a
    # freezer we had sold them that morning.
    #
    # A machine nobody can name is a machine we have failed to look up, not a
    # machine to create. The right move is to ask what they own.
    junk = {"unknown", "unsure", "none", "n/a", "na", "not sure", "?", "-"}
    if (manufacturer or "").strip().lower() in junk or             (model_number or "").strip().lower() in junk:
        with db.connect() as c:
            theirs = [dict(r) for r in c.execute(
                """SELECT a.id, a.manufacturer, a.model_number, a.family
                   FROM assets a WHERE a.site_id = ? AND a.retired_on IS NULL""",
                (site_id,))]

        same = [t for t in theirs
                if family and (t["family"] or "").lower() == family.lower()]
        if len(same) == 1:
            return {"ok": True, "asset_id": same[0]["id"],
                    "already_theirs": True,
                    "family": same[0]["family"],
                    "machine": f"{same[0]['manufacturer']} {same[0]['model_number']}",
                    "say": "They already have exactly one of these on their "
                           "account and this is it. Nothing new was created."}

        return {"ok": False,
                "why": "a machine cannot be registered as unknown",
                "they_own": [f"{t['manufacturer']} {t['model_number']}"
                             + (f" ({t['family']})" if t["family"] else "")
                             for t in theirs],
                "say": "Do NOT create a machine you cannot name, and do not "
                       "quote a repair against one: a blank machine has no "
                       "purchase and no warranty, so the customer gets "
                       "charged for cover they have. Read them the list of "
                       "what is on their account and ask which one it is."}

    # ALREADY THEIRS: same make, same model, same ACCOUNT, still in service.
    #
    # Scoped to the account this site belongs to, and never wider. The first
    # version of this check matched on make and model alone whenever no site
    # was given, which would have handed back somebody else's machine, and it
    # ran before the site was resolved so it also swallowed the refusal above.
    # Both were worse than the duplicate it was written to prevent.
    if manufacturer and model_number:
        try:
            with db.connect() as c:
                existing = c.execute(
                    """SELECT a.id, a.family
                       FROM assets a JOIN sites s ON s.id = a.site_id
                       WHERE a.retired_on IS NULL
                         AND LOWER(a.manufacturer) = LOWER(?)
                         AND LOWER(a.model_number) = LOWER(?)
                         AND s.account_id = (SELECT account_id FROM sites
                                             WHERE id = ?)
                       ORDER BY a.rowid LIMIT 1""",
                    (manufacturer, model_number, site_id)).fetchone()
        except Exception:
            existing = None

        if existing is not None:
            return {
                "ok": True,
                "asset_id": existing["id"],
                "already_theirs": True,
                "family": existing["family"],
                "say": ("They already have this one on their account, so "
                        "nothing was added. Use it and carry on: do not tell "
                        "them it is newly registered, and do not ask again "
                        "for details we already hold."),
            }

    family = (family or "").strip() or _family_for(manufacturer, model_number)

    installed_on = (installed_on or "").strip() or None
    asset_id = _new_id("AST")
    with db.txn() as c:
        eq = c.execute(
            """SELECT id, product_type FROM equipment
               WHERE brand LIKE ? AND model_norm =
                 UPPER(REPLACE(REPLACE(REPLACE(?,'-',''),' ',''),'/',''))
               LIMIT 1""", (f"%{manufacturer}%", model_number)).fetchone()

        c.execute("""INSERT INTO assets
                     (id,site_id,manufacturer,model_number,equipment_id,family,
                      installed_on,location_note)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (asset_id, site_id, manufacturer, model_number,
                   eq["id"] if eq else None,
                   family or (eq["product_type"] if eq else None),
                   installed_on, location_note or None))

        # WHERE THE DATE CAME FROM MATTERS MORE THAN THE DATE. This machine is
        # being registered because a caller described it, so the install date
        # is theirs, not ours. Recorded as such, because covers() used to
        # treat it as though we had written it down when we sold the machine,
        # which meant anybody could say "it went in last year" and be quoted
        # zero.
        c.execute("UPDATE assets SET installed_source=? WHERE id=?",
                  ("customer_stated" if installed_on else "unknown", asset_id))

    return {"ok": True, "asset_id": asset_id,
            "matched_catalogue": eq is not None,
            "installed_source": "customer_stated" if installed_on else "unknown",
            "note": "matched a certified model" if eq else
                    "not in the certification catalogue, which is normal and fine",
            "say": ("The install date they gave us is their word, not our "
                    "paperwork, so any warranty on it is a CLAIM. Quote the "
                    "visit as chargeable and let quote_visit explain how they "
                    "get it credited." if installed_on else "")}
