"""The lock, not the note on the door.

The four intents are decided by a model listening to a stressed person on a
narrowband phone line. Sometimes it will get that wrong. So the guard is
deliberately narrow:

  - Looking things up is ALWAYS allowed, whatever the intent is believed to be.
    A misheard sentence must never stop somebody finding out about their unit.
  - Only actions with consequences are gated: reserving stock, promising a
    technician's time, logging a commercial offer.
  - A blocked call is not a silent failure. It returns an explanation, which
    the model reads and can act on by re-routing. Being wrong is recoverable;
    being wrong AND stuck is not.

This exists because everywhere else in this system deterministic code decides
whether something may happen. Routing was the one place that was still only a
prompt asking nicely.
"""

from __future__ import annotations

import re
import threading

from typing import Any


def _record(kind: str, outcome: str, tool: str, detail: str = "",
            args: dict | None = None) -> None:
    """Keep what a guard just did, instead of printing it into a void.

    THIS FILE USED TO CONTAIN NO INSERT AT ALL

    Every interception here was printed and discarded, which made the central
    claim about this product unfalsifiable. "It refuses rather than inventing"
    was true in the code and uncountable everywhere else: no way to say how
    often, whether it was improving, or which of these guards had ever fired
    on a real call rather than only in a test.

    NOTHING HERE MAY BREAK A CALL

    A guard that throws while recording that it worked is worse than no guard.
    Every failure is swallowed on purpose, because the recording is evidence
    and the interception is the job, and losing the evidence is the cheaper of
    the two.

    Argument VALUES are never stored. A tool call carries names, addresses and
    phone numbers, and this is a count of interventions, not a second copy of
    the call.
    """
    try:
        from datetime import datetime

        from . import db
        from .trace import CALL, here

        call_id = None
        try:
            call_id = here()
        except Exception:
            pass

        dealer = None
        if args:
            got = args.get("dealer_id")
            dealer = got if isinstance(got, str) else None

        with db.txn() as c:
            c.execute(
                """INSERT INTO interventions
                     (at,call_id,dealer_id,tool,kind,outcome,detail,args_seen)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(timespec="seconds"), call_id, dealer,
                 tool or "", kind, outcome, detail[:400] if detail else None,
                 ",".join(sorted(args)) if args else None))
    except Exception as e:
        # The console shows every refusal with its reason. Losing one quietly
        # makes that screen a claim rather than a record, so say it happened
        # even though the block itself still stands.
        print(f"[guards] a refusal was NOT recorded ({kind}/{outcome} on "
              f"{tool}): {type(e).__name__}: {e}", flush=True)

# Tools that change the world, and which intent legitimately owns them.
# Tools that CHANGE something. Never deduplicated: a second write is either a
# real second thing the customer wants or a bug that belongs where the write
# happens, and quietly swallowing one here would hide both.
WRITES = {
    "open_work_order", "promise_slot", "register_asset", "register_complaint",
    "register_return", "confirm_purchase_order", "place_supply_order",
    "record_proof", "record_attempt", "record_availability", "raise_it",
    "log_supplier_offer", "note_wishlist", "set_intent", "set_language",
    "route_to_vendor", "confirm_details", "build_briefing", "open_claim",
    "settle_claim", "where_to_send_proof",
}

GATED: dict[str, set[str]] = {
    "promise_slot": {"service"},
    "build_briefing": {"service"},
    "open_work_order": {"service", "order"},
    "log_supplier_offer": {"supplier"},

    # The service machinery. All of it used to be reachable from any call, and
    # on a live PRODUCT call somebody ringing to BUY a freezer was taken all
    # the way through it: the desk registered the machine they were buying as
    # an asset they already owned, sent the delivery to the engineer diary,
    # found nobody certified to service a machine that does not exist yet, and
    # escalated it to the branch manager.
    #
    # Every step followed from the one before. The gate is where it should
    # have stopped.
    "scheduling": {"service"},
    "assessment": {"service"},
    "register_asset": {"service"},
    "can_we_serve": {"service"},
    "quote_visit": {"service"},
    "should_send_someone": {"service"},
    "record_attempt": {"service"},
    "raise_it": {"service"},
    "record_availability": {"service"},
    "warranty_status": {"service"},

    # And the buying machinery, which a breakdown call has no business in.
    "product_availability": {"order", "product"},
}

_HUMAN = {
    "service": "a broken machine",
    "order": "buying a part",
    "product": "a question about products",
    "supplier": "a vendor selling to us",
}


# Every tool that takes one of these is talking about a specific machine, and
# a machine belongs to exactly one customer.
_ASSET_ARGS = ("asset_id", "machine_id")


def _owns_the_machine(asset_id: str) -> tuple[bool, str]:
    """Does the caller on this call actually own that machine.

    THIS IS NOT HYPOTHETICAL. On the first real call a new customer ever made
    to this desk, the model called should_send_someone with AST-7EA68C, which
    is a True Refrigeration reach-in belonging to Rockvale Convenience: a
    different account, a different site, a different town. It had picked the
    id out of a load_memory result, which returns past repairs from the whole
    corpus and quite reasonably carries their asset ids.

    So the first-line advice offered to that caller was computed against a
    stranger's freezer, and nothing anywhere noticed.

    The model is not the right place to enforce this. It is holding an
    identifier it can read off any tool result, and asking it to be careful is
    the same class of mistake as asking it to carry a contact id. The check
    belongs here, where every tool call already passes through.
    """
    from . import db
    from .trace import CALL, here

    call_id = here()
    if not call_id:
        return True, ""          # not on a call: tests, sweeps, the console

    try:
        with db.connect() as c:
            # Through the contact's account, not through their site. A
            # contact registered mid-call has an account from the first
            # moment and may not have a site for another minute, and joining
            # on the site made the check silently pass for exactly the
            # first-time caller it was written for.
            mine = c.execute(
                """SELECT ct.account_id FROM calls cl
                   JOIN contacts ct ON ct.id = cl.contact_id
                   WHERE cl.id = ?""", (call_id,)).fetchone()

            # THE NUMBER THEY RANG FROM, when the call row has no contact on
            # it yet.
            #
            # HEARD LIVE. A customer rang about a Lenovo IdeaPad they had
            # bought that afternoon. The desk produced AST-3F9DE1, which is a
            # real Lenovo -- model 21SX, on somebody else's account. This
            # check read `mine` as None because the call had no contact_id
            # linked, took that as "we do not know who is calling", and
            # ALLOWED a stranger's machine through. The tool then failed on
            # it and the desk asked the customer to read out a model number,
            # which is the one thing its instructions forbid.
            #
            # We always know the number. Not knowing which contact it maps to
            # is a gap in our records, not a reason to stop checking.
            if mine is None:
                mine = c.execute(
                    """SELECT ct.account_id FROM calls cl
                       JOIN phones p ON p.e164 = cl.from_e164
                       JOIN contacts ct ON ct.id = p.contact_id
                       WHERE cl.id = ? LIMIT 1""", (call_id,)).fetchone()
            theirs = c.execute(
                """SELECT s.account_id, ac.name FROM assets a
                   JOIN sites s ON s.id = a.site_id
                   JOIN accounts ac ON ac.id = s.account_id
                   WHERE a.id = ?""", (asset_id,)).fetchone()
    except Exception as e:
        # A guard that cannot read must not become a guard that allows.
        print(f"[guard] could not check ownership: {type(e).__name__}: {e}",
              flush=True)
        return False, "we could not confirm this machine belongs to this caller"

    if theirs is None:
        return False, "there is no such machine on file"
    if mine is None:
        return True, ""          # we do not yet know who is calling
    if mine["account_id"] != theirs["account_id"]:
        return False, "that machine is on another customer's account"
    return True, ""




# What our identifiers look like: a short prefix, a dash, then hex. Never a
# space, because every id this system mints is machine-made.
_ID = re.compile(r"^[A-Za-z]{1,4}-[A-Za-z0-9]{3,12}$")


def _looks_like_an_id(value: str) -> bool:
    return bool(_ID.match(value.strip()))



def _rename_near_misses(args: dict, tool: Any) -> None:
    """Move a value under a nearly-right key onto the real parameter.

    Derived from the function being called, never from a table of names. A key
    is only moved when it differs from exactly one real parameter by a prefix
    or suffix, so `purchase_order` finds `purchase_order_id` and an outright
    invention finds nothing and is left alone for the tool to complain about.
    """
    import inspect

    fn = getattr(tool, "func", None) or getattr(tool, "_func", None)
    if fn is None or not args:
        return
    try:
        real = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return

    for given in [k for k in args if k not in real]:
        near = [r for r in real
                if r not in args
                and (r.startswith(given) or given.startswith(r))
                and abs(len(r) - len(given)) <= 4]
        if len(near) == 1 and args.get(given) not in (None, ""):
            args[near[0]] = args.pop(given)
            print(f"[guard] {given!r} is {near[0]!r} on "
                  f"{getattr(fn, '__name__', 'this tool')}; renamed rather "
                  "than dropped", flush=True)


def _fill_in_ids(args: dict, tool: Any = None) -> None:
    """Supply the identifiers this call already knows, so nobody is asked.

    Only fills what is MISSING. An id the model supplied is left alone and
    checked by the ownership guard below, because silently replacing one would
    hide the case where it reached for somebody else's.
    """
    from . import db
    from .trace import CALL, here

    # THE SAME THING UNDER A NEARLY RIGHT NAME.
    #
    # ADK's FunctionTool filters arguments against the function signature and
    # DROPS anything that does not match, silently. So when the desk called
    #
    #     confirm_purchase_order(purchase_order="PO-FFB24C", agreed_by="...")
    #
    # the order number never reached the function at all. It refused, and a
    # customer who had just agreed to $2,548.50 was told we could not confirm
    # their order. The model had the right value and the wrong key by one
    # suffix.
    #
    # Read from the tool's OWN signature rather than a list of spellings
    # somebody guessed at: a name is only corrected when it is unambiguously
    # the same word as exactly one real parameter. A list would go stale the
    # moment a tool is renamed, and would say nothing about tools nobody
    # thought of.
    _rename_near_misses(args, tool)
    # An id that belongs to nothing is worth discarding whether or not a
    # call is in progress, so this runs before the live-call gate below.
    _throw_away_what_is_not_ours(args, {})

    call_id = here()
    if not call_id:
        return

    # A value that is not an id is the same as no id.
    #
    # On a live call the model passed asset_id="Traulsen RHT126WUT-FHS", a
    # model NAME in an id field. The ownership check rejected it as "no such
    # machine", correctly and uselessly: the desk then asked the customer for
    # the model number they had already given, which is the exact loop the
    # morning was spent removing.
    #
    # Ours look like AST-1A2B3C, WO-4D5E6F, A-7G8H9I. Anything with a space in
    # it, or without a dash, is the model talking rather than an identifier.
    said = {}
    # technician_id belongs here for the same reason as the rest: the model
    # invented "14" for an engineer it had just named, and the raw foreign key
    # failure that followed killed the turn and left a promise behind.
    for k in ("account_id", "site_id", "contact_id", "asset_id",
              "work_order_id", "technician_id"):
        v = (args or {}).get(k)
        if isinstance(v, str) and v and not _looks_like_an_id(v):
            print(f"[guard] {k}={v!r} is not an id, treating it as missing",
                  flush=True)
            said[k] = v          # NOT thrown away: see _the_machine_they_mean
            args[k] = ""

    # AN ID CAN LOOK PERFECT AND NOT EXIST.
    #
    # Heard on a live call. The desk invented `asset_id="AST-037"`, which
    # matches the shape of ours exactly, so this guard waved it through, the
    # lookup found nothing, and the agent went back to the customer and asked
    # for a model number -- for a chair we had sold them an hour earlier.
    #
    # Shape is not existence. A machine id is only real if it is a machine,
    # and on this desk it is only usable if it is THEIR machine.
    # WHAT THE TOOL TAKES, NOT WHAT THE MODEL REMEMBERED TO TYPE.
    #
    # This used to fill only keys already present in args, so the guard could
    # help when the model half-remembered an id and not at all when it forgot
    # one. On a live call that meant register_asset was invoked with a make, a
    # family, a location and a date, and NO site_id, because the model simply
    # did not mention it. Nothing filled it, the insert failed on a foreign
    # key, and the desk asked the customer:
    #
    #     "Is there a specific site ID you have for that location?"
    #
    # A restaurant owner does not have our site id. That question is the exact
    # thing this function exists to make impossible, and it got asked because
    # the fill was conditional on the model's own omission.
    #
    # So the tool's signature decides. If it accepts an identifier, and one is
    # knowable from this call, it gets one.
    KNOWN = ("account_id", "site_id", "contact_id", "asset_id",
             "work_order_id")

    accepts = set()
    if tool is not None:
        target = getattr(tool, "func", None) or getattr(tool, "_func", None) or tool
        try:
            import inspect

            accepts = set(inspect.signature(target).parameters)
        except (TypeError, ValueError):
            accepts = set()

    wants = [k for k in KNOWN
             if (k in args or k in accepts) and not args.get(k)]
    if not wants:
        return

    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT ct.id contact_id, ct.account_id, ct.site_id
                   FROM calls cl JOIN contacts ct ON ct.id = cl.contact_id
                   WHERE cl.id = ?""", (call_id,)).fetchone()
            if row is None:
                return

            for key in ("contact_id", "account_id", "site_id"):
                if key in wants and row[key]:
                    args[key] = row[key]

            # A contact is not always pinned to a site: plenty are recorded
            # against the account and nothing else. Falling back to the
            # account's site is what a person would do, and without it a tool
            # that needs one goes and asks the customer instead.
            if "site_id" in wants and not args.get("site_id") and row["account_id"]:
                site = c.execute(
                    "SELECT id FROM sites WHERE account_id = ? ORDER BY rowid LIMIT 1",
                    (row["account_id"],)).fetchone()
                if site is not None:
                    args["site_id"] = site["id"]

            # The machine and the job being discussed on THIS call, which is
            # the most recent one opened against it.
            if "work_order_id" in wants or "asset_id" in wants:
                job = c.execute(
                    """SELECT id, asset_id FROM work_orders
                       WHERE opened_from_call = ?
                       ORDER BY rowid DESC LIMIT 1""", (call_id,)).fetchone()
                if job is not None:
                    if "work_order_id" in wants:
                        args["work_order_id"] = job["id"]
                    # THE STANDING JOB, UNLESS THEY HAVE JUST NAMED SOMETHING
                    # ELSE.
                    #
                    # A customer who opened a job about a chair and then says
                    # "the laptop will not charge" has changed the subject.
                    # Filing that against the chair is how one visit gets
                    # booked for the wrong machine and the engineer arrives
                    # with the wrong parts.
                    #
                    # So an explicit kind of machine in what they just said
                    # beats the job. If it names a kind they own more than one
                    # of, nothing is filled and the desk asks which -- still
                    # better than confidently handing back the chair.
                    if "asset_id" in wants and job["asset_id"]:
                        if _they_named_something_else(c, job["asset_id"], args):
                            print("[guard] they named a different kind of "
                                  "machine than the open job; not assuming "
                                  "the job's one", flush=True)
                        else:
                            args["asset_id"] = job["asset_id"]

            # AND WHEN THERE IS NO JOB YET, WHICH IS MOST OF A SERVICE CALL.
            #
            # The lookup above only worked once a work order existed, and a
            # work order needs an asset, so it could never get started. On a
            # live call that circle ran the whole conversation:
            #
            #     should_send_someone(asset_id='178Z1RGHC')  -> blanked
            #     open_work_order(asset_id='178Z1RGHC')      -> blanked
            #     quote_visit(asset_id='178Z1RGHC')          -> blanked
            #     warranty_status(asset_id='178Z1RGHC')      -> blanked
            #
            # and nothing was ever written. The caller owned exactly one ice
            # machine and its model number is 178Z1RGHC, which is the value
            # being thrown away four times over. The model was not confused;
            # it was answering with the only identifier it had.
            if "asset_id" in wants and not args.get("asset_id"):
                found = _the_machine_they_mean(
                    c, row["account_id"], said.get("asset_id", ""), args)
                if not found:
                    # THE MACHINE THIS CALL IS ALREADY ABOUT.
                    #
                    # Some tools carry no words to match on. `can_we_serve`
                    # takes an asset and a vendor and nothing else, so when
                    # the model passed "None" there was nothing to work from
                    # and this correctly refused to choose between the two
                    # machines on the account.
                    #
                    # Correct and unhelpful: the tool one step earlier had
                    # already established it was the chair. A conversation
                    # does not restart between tool calls, and neither should
                    # this.
                    found = _the_one_we_settled_on(call_id)
                    # ...but not if they have just named a different kind of
                    # machine. Remembering the chair is the right default for
                    # "it is still broken" and the wrong one for "the laptop
                    # will not charge", and the second must never quietly
                    # inherit the first.
                    if found and _they_named_something_else(c, found, args):
                        print("[guard] they named a different kind of machine "
                              "than the one settled earlier; asking rather "
                              "than assuming", flush=True)
                        found = ""
                    elif found:
                        print(f"[guard] asset_id taken from earlier in this "
                              f"call: {found}", flush=True)

                if found:
                    print(f"[guard] asset_id resolved to {found} from what "
                          "the caller owns", flush=True)
                    args["asset_id"] = found
                    _remember_the_machine(call_id, found)
                    _record("resolved_asset", "corrected", "",
                            f"matched {said.get('asset_id', '')!r} to a "
                            "machine on their own account", args)
    except Exception as e:
        # Never block a call because the convenience lookup failed. The tool
        # will report what it is missing in its own words.
        print(f"[guard] could not fill in ids: {type(e).__name__}: {e}",
              flush=True)



# The machine each live call turned out to be about. Cleared when the call
# ends, so the next caller cannot inherit it.
_SETTLED: dict[str, str] = {}
_SETTLED_LOCK = threading.Lock()




# EVERY ID, CHECKED AGAINST ITS OWN TABLE.
#
# The model invents identifiers. Not occasionally: AST-037 for a chair,
# technician_id "14" for an engineer it had just named, STK-412 for a freezer,
# PO-1234 for an order it had itself drafted seconds earlier. Each one had the
# right shape and belonged to nothing.
#
# This is a known failure class and the recognised answer is token
# abstraction: the model should not be the thing carrying an identifier. It
# cannot be removed from the loop entirely here -- the tools take ids -- so
# the next best thing is that a value which does not EXIST never reaches a
# tool, and the real one is put back from the live call.
#
# Written once, for every id, rather than the five separate patches this
# started as. A table nobody remembers to extend is how the sixth one gets
# through.
_WHERE_AN_ID_LIVES = {
    "asset_id": "assets",
    "account_id": "accounts",
    "site_id": "sites",
    "contact_id": "contacts",
    "work_order_id": "work_orders",
    "technician_id": "technicians",
    "purchase_order_id": "purchase_orders",
}

def _throw_away_what_is_not_ours(args: dict, said: dict) -> None:
    """Blank any id that is not a row in the table it names."""
    from . import db
    for key, table in _WHERE_AN_ID_LIVES.items():
        v = (args or {}).get(key)
        if not isinstance(v, str) or not v or not _looks_like_an_id(v):
            continue

        # A STOCK HANDLE IS NOT AN INVENTED ASSET ID. STK-366 is a row on the
        # price list, and cover is sold against one at the moment of purchase,
        # before any asset exists. Checking it against `assets` read a
        # correct value as invented.
        if re.match(r"^STK-\d+$", v.strip().upper()):
            continue

        try:
            with db.connect() as c:
                real = c.execute(
                    f"SELECT 1 FROM {table} WHERE id = ?", (v,)).fetchone()
        except Exception:
            continue             # never block a call on a lookup failing

        if not real:
            print(f"[guard] {key}={v!r} has the shape of one of ours and is "
                  f"not in {table}; treating it as missing", flush=True)
            said.setdefault(key, "")
            args[key] = ""


def _remember_the_machine(call_id: str, asset_id: str) -> None:
    if not call_id or not asset_id:
        return
    with _SETTLED_LOCK:
        _SETTLED[call_id] = asset_id


def _the_one_we_settled_on(call_id: str) -> str:
    with _SETTLED_LOCK:
        return _SETTLED.get(call_id or "", "")


def forget_the_machine(call_id: str) -> None:
    """They hung up. The next caller starts from nothing."""
    with _SETTLED_LOCK:
        _SETTLED.pop(call_id or "", None)



def _they_named_something_else(c, job_asset: str, args: dict) -> bool:
    """Did the caller just name a different kind of machine to the open job.

    Only when the words are unambiguous about the KIND. "it is still broken"
    names nothing and leaves the standing job alone, which is what a person
    means.
    """
    spoken = " ".join(str(args.get(k) or "") for k in
                      ("reported_symptom", "symptom", "what", "description",
                       "request", "note")).lower()
    if not spoken:
        return False

    words = set(re.split(r"[^a-z0-9]+", spoken)) - {""}
    try:
        fam = (c.execute("SELECT family FROM assets WHERE id = ?",
                         (job_asset,)).fetchone() or {})["family"] or ""
    except Exception:
        return False
    if not fam:
        return False

    # The job's own family being spoken of means they are still on it.
    if any(w in words for w in fam.lower().split() if len(w) > 3):
        return False

    # Some OTHER family they own being named means they are not.
    try:
        theirs = [r[0] for r in c.execute(
            """SELECT DISTINCT a.family FROM assets a
               JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = (SELECT s2.account_id FROM assets a2
                                     JOIN sites s2 ON s2.id = a2.site_id
                                     WHERE a2.id = ?)
                 AND a.family IS NOT NULL AND a.family != ''""",
            (job_asset,))]
    except Exception:
        return False

    for other in theirs:
        if other.lower() == fam.lower():
            continue
        if any(w in words for w in other.lower().split() if len(w) > 3):
            return True
    return False


def _the_machine_they_mean(c, account_id: str, said: str, args: dict) -> str:
    """Which of THEIR machines this is, from whatever the model called it.

    Matched against the caller's own book, never the wider catalogue, so this
    cannot reach another customer's equipment. Returns nothing unless exactly
    one machine fits: guessing between two of somebody's freezers is worse
    than asking which.

    Tried in order of how specific the evidence is:

      the model number they gave      178Z1RGHC
      the make                        Avantco
      the kind of machine             ice machine
      and if they own only one        that one
    """
    if not account_id:
        return ""

    try:
        rows = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.family
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.retired_on IS NULL""",
            (account_id,)).fetchall()
    except Exception:
        return ""

    if not rows:
        return ""

    hint = (said or "").strip().lower()
    if hint in ("none", "null", "unknown", ""):
        hint = ""
    # An invented id is not a hint. "AST-037" tells us nothing about which
    # machine they mean, and letting it fall through to the last resort below
    # is how a chair complaint came back holding a laptop.
    if _looks_like_an_id(hint):
        hint = ""

    # WHAT THEY ACTUALLY SAID IS WRONG WITH IT.
    #
    # On a live call the customer said "one of the office chairs is playing
    # up". The desk then called a tool with asset_id="None", so the hint was
    # empty, and this picked their LAPTOP -- the other machine on the account.
    # Nothing in the id told it which; everything in the sentence did.
    #
    # The symptom is on the same call, in the same arguments, and naming the
    # kind of machine is exactly what a customer does instead of giving an id.
    spoken = " ".join(str(args.get(k) or "") for k in
                      ("reported_symptom", "symptom", "what", "description",
                       "request", "note")).lower()

    family = (args.get("family") or "").strip().lower()
    make = (args.get("manufacturer") or "").strip().lower()
    model = (args.get("model_number") or "").strip().lower()

    def only(matches):
        return matches[0]["id"] if len(matches) == 1 else ""

    for needle in (hint, model):
        if not needle:
            continue
        hit = only([r for r in rows
                    if needle == (r["model_number"] or "").lower()])
        if hit:
            return hit
        hit = only([r for r in rows
                    if needle in (r["model_number"] or "").lower()
                    or (r["model_number"] or "").lower() in needle])
        if hit:
            return hit

    for needle in (hint, make):
        if not needle:
            continue
        hit = only([r for r in rows
                    if (r["manufacturer"] or "").lower() in needle
                    or needle in (r["manufacturer"] or "").lower()])
        if hit:
            return hit

    # The family named in the sentence, before falling back to owning one.
    #
    # Matched WORD BY WORD, because a family is "office chair" and a customer
    # says "the chair is tilted". Requiring the whole family name to appear
    # never matched anything anybody actually says.
    if spoken:
        words = set(re.split(r"[^a-z0-9]+", spoken)) - {""}

        def names_it(r) -> bool:
            fam = (r["family"] or "").lower()
            return bool(fam) and any(w in words for w in fam.split() if len(w) > 3)

        by_family = [r for r in rows if names_it(r)]
        if len(by_family) == 1:
            return by_family[0]["id"]

        by_make = [r for r in rows
                   if (r["manufacturer"] or "")
                   and (r["manufacturer"] or "").split()[0].lower() in words]
        if len(by_make) == 1:
            return by_make[0]["id"]

        # THE MODEL NAME IN THE SENTENCE, for a machine whose family we never
        # recorded. The ThinkPad has no family on it at all, so the two checks
        # above can never see it, and "the laptop will not charge" has to land
        # somewhere.
        by_model = [r for r in rows
                    if any(w in words
                           for w in (r["model_number"] or "").lower().split()
                           if len(w) > 3)]
        if len(by_model) == 1:
            return by_model[0]["id"]

    for needle in (family, hint):
        if not needle:
            continue
        hit = only([r for r in rows
                    if (r["family"] or "").lower() in needle
                    or needle in (r["family"] or "").lower()])
        if hit:
            return hit

    # They own one machine. It is that one.
    return rows[0]["id"] if len(rows) == 1 else ""


def _not_over_a_disproved_fact(name: str, args: dict) -> dict | None:
    """Refuse an escalation whose stated reason we can check and disprove."""
    if name != "raise_it":
        return None
    if (args.get("reason") or "") != "no_qualified_technician":
        return None

    asset_id = args.get("asset_id") or ""
    if not asset_id:
        return None

    try:
        from .cover import can_we_serve

        out = can_we_serve(asset_id)
    except Exception:
        return None

    if not out.get("ok") or not out.get("qualified"):
        return None

    return {
        "blocked": True,
        "qualified": out["qualified"],
        "say": (f"Do NOT escalate this. {out['qualified']} of our technicians "
                f"are certified for a {out.get('family') or 'machine'}, which "
                "is what can_we_serve just told you. Book the visit through "
                "scheduling instead. Sending a routine job to a branch manager "
                "over a shortage that does not exist costs the customer days."),
    }


def _already_answered(name: str, args: dict, tool_context: Any) -> dict | None:
    """Has this exact call already been made on this call?

    Keyed on the tool and its arguments AFTER ids have been filled in, so two
    calls that differ only in an identifier the model invented and the guard
    corrected count as the same call, which they are.

    Lookups only. A tool that WRITES something is never treated as a repeat:
    booking the same visit twice is a different kind of mistake and is already
    handled where it belongs, and short-circuiting a write here could quietly
    swallow a second order somebody genuinely wanted.
    """
    if name in WRITES:
        return None

    try:
        key = name + "|" + repr(sorted((args or {}).items()))
    except Exception:
        return None

    try:
        seen = tool_context.state.get("_answered") or {}
        if not isinstance(seen, dict):
            seen = {}
        n = int(seen.get(key, 0))
        seen[key] = n + 1
        tool_context.state["_answered"] = seen
    except Exception:
        return None

    if n == 0:
        return None

    return {
        "already_answered": True,
        "times": n + 1,
        "say": ("You have already run this exact lookup on this call and "
                "already told them the answer. Nothing has changed since, so "
                "there is nothing new to report.\n"
                "If they ASKED you to repeat it, or said they did not catch "
                "it, say it again plainly. Otherwise do NOT read it out a "
                "second time: move the conversation forward, or ask them what "
                "they would like to do next. Repeating yourself unprompted is "
                "the single thing callers complain about most."),
    }


def _fill_in_dealer(tool: Any, args: dict, tool_context: Any) -> None:
    """Put the routed vendor into any tool that takes one and was not given one.

    Only fills what is absent. A tool call that names a vendor explicitly is
    left alone, because something meant it.
    """
    if args.get("dealer_id") or args.get("dealer"):
        return

    try:
        routed = (tool_context.state.get("dealer_id") or "").strip()
    except Exception:
        routed = ""
    if not routed:
        # Same reason as tools._dealer: a sub-agent carries its own state and
        # never saw the routing write.
        from .tenancy import routed as routed_vendor

        routed = routed_vendor()
    if not routed:
        return

    target = getattr(tool, "func", None) or getattr(tool, "_func", None) or tool
    try:
        import inspect

        params = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return

    for name in ("dealer_id", "dealer"):
        if name in params:
            args[name] = routed
            _record("filled_dealer", "corrected",
                    getattr(tool, "name", "") or getattr(tool, "__name__", ""),
                    f"tool would have asked the default vendor instead of "
                    f"{routed}", args)
            return


def guard_tool(tool: Any, args: dict, tool_context: Any) -> dict | None:
    """ADK before_tool_callback. Return None to allow, a dict to block.

    Returning a dict short-circuits the tool: the model receives that dict as
    the tool result, so the refusal is legible to it rather than mysterious.
    """
    # Carry the caller's language from session state into somewhere the
    # retrieval can read it. This callback already runs before every tool and
    # already holds the state, so nothing else has to learn about languages.
    try:
        from .language import SPEAKING

        SPEAKING.set((tool_context.state.get("language") or "").strip().lower())
    except Exception as e:
        # Retrieval silently falls back to English. The caller keeps being
        # answered, in the wrong language, with no history behind it.
        print(f"[guards] could not carry the caller's language into "
              f"retrieval: {type(e).__name__}: {e}", flush=True)

    # WHAT THIS CALL IS ABOUT, PUT WHERE A SUB-AGENT WILL ACTUALLY SEE IT.
    #
    # `supply` is reached through AgentTool, and AgentTool gives a sub-agent
    # the parent's STATE while giving it none of the conversation. That is the
    # documented behaviour and it is the root of most of what has gone wrong
    # on this desk: every call to `supply` is a fresh conversation that has to
    # work out, from the words alone, which machine and which order it is on.
    #
    # The registers beside this file already hold those facts and are readable
    # from any thread. Copying them into state is what makes them cross the
    # AgentTool boundary as well, using ADK's own mechanism rather than
    # hoping the model repeats an identifier back correctly.
    #
    # Never raises. A missing hint means the sub-agent works it out the way it
    # always did, which is worse and is not broken.
    try:
        from .shortlist import the_one_they_picked, what_we_offered

        chosen = the_one_they_picked()
        if chosen:
            tool_context.state["they_chose"] = {
                "ref": chosen.get("ref"),
                "what": f"{chosen.get('manufacturer', '')} "
                        f"{chosen.get('model_number', '')}".strip(),
                "price": chosen.get("list_price"),
                "family": chosen.get("family"),
            }
        offered = what_we_offered()
        if offered:
            tool_context.state["we_offered"] = [
                {"number": o.get("number"), "ref": o.get("ref"),
                 "what": f"{o.get('manufacturer', '')} "
                         f"{o.get('model_number', '')}".strip(),
                 "price": o.get("list_price")}
                for o in offered]
    except Exception as e:
        print(f"[guards] could not carry the working set into state: "
              f"{type(e).__name__}: {e}", flush=True)

    name = getattr(tool, "name", "") or getattr(tool, "__name__", "")

    # REMEMBER THAT THIS TOOL RAN, so saying.py can tell a quoted price from
    # an invented one. Written here because every tool call already passes
    # through this function, and nothing else has to learn that the output
    # guard exists.
    #
    # Accumulated for the session rather than reset each turn. Turn-scoped
    # would be stricter and is genuinely harder to get right in ADK, where a
    # single turn is several model calls with tool calls between them, and
    # clearing at the wrong moment would block honest answers. The failure
    # this exists to catch had NO tool calls at all, which either scope
    # catches; a false block on a real price is the worse error.
    try:
        seen = list(tool_context.state.get("tools_this_turn") or [])
        if name and name not in seen:
            seen.append(name)
            tool_context.state["tools_this_turn"] = seen
    except Exception as e:
        # saying.py reads this to decide whether a figure came from a pricing
        # tool. Losing it fails CLOSED, so a real price gets blocked rather
        # than an invented one let through, which is the right direction and
        # still worth seeing.
        print(f"[guards] lost the record of which tools ran this turn: "
              f"{type(e).__name__}: {e}", flush=True)

    # NOBODY IS EVER ASKED FOR AN ID.
    #
    # On a live call the desk asked a restaurant owner for an Asset ID, then
    # an Account ID, then a Work Order ID. They do not have them. They are
    # ours, they are in the call row, and asking tells somebody three minutes
    # into a conversation that we have lost track of it.
    #
    # A sub-agent that needs one gets it filled in here rather than going and
    # asking the customer, which is the same reasoning as _on_this_call in
    # caller.py: an identifier the model has to carry is one it can invent or
    # mislay, so it should never have to carry one.
    _fill_in_ids(args or {}, tool)

    # AND THE VENDOR, WHICH TWELVE TOOLS QUIETLY GOT WRONG.
    #
    # route_to_vendor decides whose stock, technicians, rates and history
    # apply, and writes it into session state. Twelve tools reachable from a
    # call never read it. They took the vendor as a DEFAULT ARGUMENT reading
    # dealer_id="D-REF", so unless the model happened to pass one they asked
    # the refrigeration business, whatever the desk had just routed to.
    #
    # It was invisible while one vendor wrongly held every product. It stopped
    # being invisible the moment routing worked. On a live call, in sequence:
    #
    #     should_send_someone -> can_we_serve -> raise_it
    #
    # all three asking refrigeration about a machine, finding nobody
    # qualified, and escalating to a branch manager a job eight technicians
    # could have taken. Then the same in French, for the same reason.
    #
    # Fixing twelve call sites one at a time invites a thirteenth. This runs
    # before every tool and already holds the state, so the vendor is filled
    # here for anything that takes one and was not given one.
    _fill_in_dealer(tool, args or {}, tool_context)


    # SAYING THE SAME THING TWICE.
    #
    # On a live call the desk listed the reach-in freezers, called
    # find_equipment again with the identical arguments, and listed them a
    # second time in slightly different words. The caller said, out loud:
    #
    #     "Okay, don't repeat."
    #
    # Repeating yourself is the complaint customers name more often than
    # almost anything else about a phone desk, and it is the one that makes an
    # automated one sound broken rather than merely slow.
    #
    # The same tool with the same arguments in the same call has the same
    # answer. Running it again cannot produce new information; it only
    # produces a second chance to read the old information out.
    #
    # UNLESS THEY ASKED. "Sorry, say that again" is a real request and the
    # answer is genuinely wanted twice. So this does not hide the result: it
    # hands it back with the fact that it has already been said, and lets the
    # model tell those two situations apart, which is a judgement it is
    # actually good at.
    # DO NOT ESCALATE OVER A FACT WE JUST DISPROVED.
    #
    # On a live call can_we_serve answered, in as many words:
    #
    #     {'ok': True, 'qualified': 8,
    #      'why': '8 of our technicians are certified for a ice machine'}
    #
    # and the desk immediately called raise_it with reason
    # 'no_qualified_technician' and sent a routine job to a branch manager.
    #
    # Every other invention on that call filled a silence where no tool had
    # been asked. This one contradicted a successful answer holding an
    # explicit count. An instruction cannot fix that, because the instruction
    # was already followed and then talked over. So it is checked here.
    # THE QUESTION THE TRADE NOTE ONLY ASKED FOR NICELY.
    #
    # Each vendor carries its own trade knowledge in its instruction, and the
    # furniture one says the single question deciding whether a recommendation
    # is honest is how many hours a day a chair gets sat in. It says it well
    # and it enforces nothing, so the desk could quote a task chair to a 24
    # hour dispatch office, never ask, and nothing would notice until the
    # chair failed with the warranty void for exceeding its duty rating.
    #
    # Same for a consumer television, whose warranty EXCLUDES commercial and
    # public display use. Mounted in a dining room it is uncovered from the
    # day it goes up.
    #
    # This is the argument this file already makes about routing: a rule a
    # model can talk past is not a rule. The policy itself lives in
    # suitability.py so a buyer can read and correct it without touching the
    # callback that enforces it.
    unasked = _must_ask_first(name, args or {}, tool_context)
    if unasked is not None:
        return unasked

    blocked = _not_over_a_disproved_fact(name, args or {})
    if blocked is not None:
        _record("disproved", "blocked", name, blocked.get("why", ""),
                args or {})
        return blocked

    repeat = _already_answered(name, args or {}, tool_context)
    if repeat is not None:
        _record("repeat", "blocked", name, repeat.get("why", ""), args or {})
        return repeat


    # A machine belongs to one customer, and the model is holding ids it can
    # read off any tool result. Checked before the intent gate, because
    # reaching into another customer's account is worse than reaching for the
    # wrong kind of tool.
    for key in _ASSET_ARGS:
        asset_id = (args or {}).get(key)
        if not asset_id:
            continue
        ok, why = _owns_the_machine(str(asset_id))

        # NOT THEIRS, BUT THEY OWN ONE LIKE IT. SWAP, DO NOT ASK.
        #
        # HEARD LIVE. A customer rang about a Lenovo IdeaPad bought that
        # afternoon. The desk produced AST-3F9DE1 -- a real Lenovo, model
        # 21SX, on another customer's account. This refused it, correctly,
        # and the refusal says "ask the customer which machine it is". So the
        # desk asked them to read out a model number, for a laptop we had
        # sold them three hours earlier and which sits on their account with
        # our own cover on it.
        #
        # Refusing the stranger's machine is right. Asking the customer to
        # identify their own is not: we know what they own, and the resolver
        # below already matches it from the words they used. Only ask when
        # there is genuinely nothing of theirs that fits.
        if not ok:
            try:
                from . import db as _db

                with _db.connect() as _c:
                    _who = _c.execute(
                        """SELECT ct.account_id FROM calls cl
                           JOIN phones p ON p.e164 = cl.from_e164
                           JOIN contacts ct ON ct.id = p.contact_id
                           WHERE cl.id = ? LIMIT 1""", (here(),)).fetchone()
                    if _who and _who["account_id"]:
                        _mine = _the_machine_they_mean(
                            _c, _who["account_id"],
                            str(said.get("asset_id", "")), args)
                        if _mine:
                            print(f"[guard] {asset_id} is another customer's; "
                                  f"the caller owns {_mine} and that is what "
                                  f"is used", flush=True)
                            args["asset_id"] = _mine
                            ok, why = True, ""
            except Exception as e:
                print(f"[guard] could not find the caller's own machine: "
                      f"{type(e).__name__}: {e}", flush=True)
        if not ok:
            _record("not_theirs", "blocked", name, why, args or {})
            return {
                "blocked": True,
                "why": f"{asset_id} was refused: {why}.",
                "do_this": "Do NOT try another id. Ask the customer which "
                           "machine it is and use register_asset if we have "
                           "never seen it. An id you read off a past repair "
                           "belongs to whoever had that repair, not to the "
                           "person on the phone.",
            }

    allowed = GATED.get(name)
    if allowed is None:
        return None  # lookups and everything else: always permitted

    intent = (tool_context.state.get("intent") or "").strip().lower()

    if not intent:
        _record("no_intent", "blocked", name,
                "a write before the call was classified", args or {})
        return {
            "blocked": True,
            "why": f"{name} changes something, and this call has not been "
                   "classified yet.",
            "do_this": "Ask them one short question to establish whether this "
                       "is a breakdown, an order, a product question, or a "
                       "vendor call. Then call set_intent and try again.",
        }

    if intent not in allowed:
        _record("wrong_intent", "blocked", name,
                f"{name} belongs to {'/'.join(sorted(allowed))}, call is "
                f"{intent}", args or {})
        return {
            "blocked": True,
            "why": f"{name} belongs to a {'/'.join(sorted(allowed))} call, "
                   f"but this call is currently marked as {intent} "
                   f"({_HUMAN.get(intent, intent)}).",
            "do_this": "If you have misread what they want, call set_intent "
                       "with the correct one and try again. If you have not, "
                       "do not attempt this action.",
        }

    return None


# What each kind of interception meant, in words an owner would use. The table
# stores the code's vocabulary; this is what it is FOR.
_MEANS = {
    "filled_id": "supplied an identifier so the customer was never asked for one",
    "filled_dealer": "sent a tool to the right business instead of the default one",
    "resolved_asset": "worked out which of their own machines they meant",
    "not_theirs": "refused to touch a machine belonging to another customer",
    "disproved": "stopped the desk contradicting an answer it had just been given",
    "repeat": "stopped the same answer being read out twice",
    "no_intent": "stopped a change before the call had been understood",
    "wrong_intent": "stopped an action belonging to a different kind of call",
    "unasked_fitness": ("held a quote back until we had asked how the thing "
                        "would actually be used"),
}


def what_the_guards_did(dealer_id: str = "", days: int = 30) -> dict:
    """How often the enforcement layer actually intervened, and at what.

    The point of keeping these is that a guard nobody can count is
    indistinguishable from a guard that does not work, and the ones that matter
    most here fire on the rarest calls: reaching into another customer's
    account, or escalating over a fact a tool had just disproved.

    Args:
        dealer_id: one business, or empty for all of them.
        days: how far back to look.
    """
    from datetime import datetime, timedelta

    from . import db

    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    where = ["at >= ?"]
    params: list = [since]
    if dealer_id:
        where.append("(dealer_id = ? OR dealer_id IS NULL)")
        params.append(dealer_id)

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT kind, outcome, COUNT(*) n,
                       COUNT(DISTINCT call_id) calls
                FROM interventions WHERE {' AND '.join(where)}
                GROUP BY kind, outcome ORDER BY n DESC""", params).fetchall()

        tools = c.execute(
            f"""SELECT tool, COUNT(*) n FROM interventions
                WHERE {' AND '.join(where)} AND tool != ''
                GROUP BY tool ORDER BY n DESC LIMIT 8""", params).fetchall()

        total_calls = c.execute(
            f"""SELECT COUNT(DISTINCT call_id) n FROM interventions
                WHERE {' AND '.join(where)} AND call_id IS NOT NULL""",
            params).fetchone()["n"]

    blocked = sum(r["n"] for r in rows if r["outcome"] == "blocked")
    corrected = sum(r["n"] for r in rows if r["outcome"] == "corrected")

    return {
        "since": since, "days": days,
        "blocked": blocked,
        "corrected": corrected,
        "calls_touched": total_calls,
        "by_kind": [{"kind": r["kind"], "outcome": r["outcome"],
                     "times": r["n"], "calls": r["calls"],
                     "means": _MEANS.get(r["kind"], r["kind"])}
                    for r in rows],
        "tools": [{"tool": r["tool"], "times": r["n"]} for r in tools],
        "say": (f"{corrected} thing(s) put right without the customer "
                f"noticing, {blocked} refused outright. The corrections are "
                "the interesting number: those are calls that would have gone "
                "wrong quietly."),
    }


# Tools that commit to a specific product at a specific price. A lookup is
# never gated: guards.py's opening principle is that finding things out is
# always allowed, and a caller must be able to browse before they are asked
# how many hours a day they will sit in something.
_COMMITS_TO_A_PRODUCT = ("create_purchase_order",)


def _must_ask_first(name: str, args: dict, tool_context: Any) -> dict | None:
    """Refuse to quote a product whose fitness question has not been asked.

    Argument validation rather than a database lookup, because
    create_purchase_order receives the line items directly and the draft it
    would write records only a description string. Checking the arguments is
    both simpler and the documented ADK pattern.

    Fails OPEN on any internal error. This gate protects the quality of a
    recommendation; the ownership and intent gates below protect other
    people's data, and it would be wrong for the softer rule to be the one
    that can break a call.
    """
    if name not in _COMMITS_TO_A_PRODUCT:
        return None

    try:
        from . import suitability

        state = getattr(tool_context, "state", {}) or {}
        items = args.get("items") or []
        if isinstance(items, str):
            items = [items]

        missing = suitability.unanswered_for(
            items, (args.get("dealer_id") or state.get("dealer_id") or ""),
            state)
    except Exception as e:
        print(f"[guard] could not check fitness: {type(e).__name__}: {e}",
              flush=True)
        return None

    if not missing:
        return None

    first = missing[0]
    _record("unasked_fitness", "blocked", name,
            f"{first['family']}: {first['ask']}", args)

    return {
        "blocked": True,
        "why": (f"We have not established {first['ask']}, and for a "
                f"{first['family']} that is not a detail: {first['why']}."),
        "do_this": first["do_this"],
        "then": "Call note_how_it_will_be_used, then raise the order again.",
    }
