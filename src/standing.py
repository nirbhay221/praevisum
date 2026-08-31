"""What the customer is to us, and what a warranty claim has to prove.

TWO QUESTIONS THE DESK COULD NOT ASK

DID THEY BUY FROM US
    Every caller was priced identically. Somebody who has been on account for
    nine years and somebody who found the number this morning got the same
    figure, which is not how any service business in this trade actually
    works. A first visit to a stranger carries no credit terms, no service
    agreement, no knowledge of the site and no relationship to amortise the
    wasted trips against, and it is collected on the day.

IS THAT DATE OURS OR THEIRS
    This is the important one, and it was a real hole.

    `register_asset` takes the install date from whatever the caller says on
    the phone, and `covers()` then treated it as though we had written it down
    ourselves. So anybody could ring, say the machine went in last year, and
    be quoted zero. There was no way to tell a date we recorded when we sold a
    machine from a date somebody offered ninety seconds ago.

    That is not a warranty. It is an honour system with a database attached.

WHAT A DATE IS WORTH DEPENDS ON WHERE IT CAME FROM

    sold_by_us       ours. Cover is ours to grant and nobody proves anything.
    customer_stated  a CLAIM. Might be exactly right. Needs evidence before it
                     becomes a discount.
    plate            read off a rating plate in a photo. Better than a memory,
                     still not a purchase record.
    unknown          we have nothing, and we say so rather than implying it
                     has expired.

CHARGE, THEN CREDIT. NOT THE OTHER WAY ROUND

    Quoting zero on an unproven claim and invoicing later when it falls
    through is precisely how a customer stops believing anything we say.
    Quoting the real number and taking it off when they produce the paperwork
    costs them nothing and surprises nobody.

    And a claim is settled by a person: the technician who sees the paperwork
    on the doorstep, or somebody reading the photograph they sent. Never by
    the desk, and never on the call.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import db
from .tenancy import the_desk

# What the install date is worth.
OURS = "sold_by_us"
THEIRS = "customer_stated"
PLATE = "plate"

# Only one of these lets us grant cover without asking for anything.
PROVEN = (OURS,)

# What a first visit costs somebody with no account, as a multiple of the
# ordinary rate. Not a punishment, and deliberately modest: there are no
# credit terms, no service agreement, nothing known about the site, and the
# money is collected on the day. A dealer who disagrees sets their own on the
# dealers row.
NEW_CUSTOMER_RATE = 1.25

# How long a claim stays open before the job is simply chargeable. Long enough
# to find a folder in an office, short enough that it does not sit forever.
CLAIM_DAYS = 30

# Where paperwork can be sent. Channels the desk already answers, because
# telling somebody to post a letter is the same as telling them not to bother.
_FALLBACK_CHANNELS = {
    "whatsapp": "the same number you are speaking to us on",
    "telegram": "our Telegram desk",
    "email": "",
}


def standing(account_id: str, dealer_id: str = "") -> dict:
    """What this customer is to us, and what that does to the rate.

    Three tiers, decided from what the database already knows rather than from
    anything anybody says on the phone.
    """
    dealer_id = the_desk(dealer_id)
    if not account_id:
        return {"tier": "new", "multiplier": _new_rate(dealer_id),
                "why": "we do not know who this is yet"}

    with db.connect() as c:
        acct = c.execute("SELECT id, name, trade_terms, opened_on FROM accounts "
                         "WHERE id = ?", (account_id,)).fetchone()
        if acct is None:
            return {"tier": "new", "multiplier": _new_rate(dealer_id),
                    "why": "no account on file"}

        # Work orders AND closed repairs. A customer with four repairs on the
        # book is obviously not a stranger, and counting only open work orders
        # would have priced a nine year customer as a first visit the moment
        # their last job closed.
        jobs = c.execute(
            """SELECT (SELECT COUNT(*) FROM work_orders wo
                       JOIN assets a ON a.id = wo.asset_id
                       JOIN sites s ON s.id = a.site_id
                       WHERE s.account_id = :acct)
                    + (SELECT COUNT(*) FROM repairs r
                       JOIN assets a ON a.id = r.asset_id
                       JOIN sites s ON s.id = a.site_id
                       WHERE s.account_id = :acct) n""",
            {"acct": account_id}).fetchone()["n"]

        sold = c.execute(
            """SELECT COUNT(*) n FROM assets a
               JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.installed_source = ?""",
            (account_id, OURS)).fetchone()["n"]

    if acct["trade_terms"]:
        return {"tier": "on_account", "multiplier": 1.0, "jobs": jobs,
                "machines_we_sold": sold,
                "why": f"on account with us on {acct['trade_terms']} terms"}

    if jobs or sold:
        return {"tier": "known", "multiplier": 1.0, "jobs": jobs,
                "machines_we_sold": sold,
                "why": (f"we have done {jobs} job(s) for them"
                        if jobs else f"we sold them {sold} machine(s)")}

    return {
        "tier": "new", "multiplier": _new_rate(dealer_id), "jobs": 0,
        "machines_we_sold": 0,
        "why": "we have never done a job for them and did not sell them the "
               "machine, so this is a first visit with no account behind it",
        "say": "Do not explain the rate unless they ask. If they do ask, say "
               "it plainly: there is no account, no service agreement, and it "
               "is settled on the day. Do not apologise for it and do not "
               "imply they are being penalised.",
    }


def _new_rate(dealer_id: str) -> float:
    try:
        with db.connect() as c:
            row = c.execute("SELECT new_customer_rate FROM dealers WHERE id=?",
                            (dealer_id,)).fetchone()
            if row and row["new_customer_rate"]:
                return float(row["new_customer_rate"])
    except Exception:
        pass
    return NEW_CUSTOMER_RATE


def where_to_send_proof(dealer_id: str = "") -> dict:
    """The channels a customer can send their paperwork to.

    Real ones. A channel we do not actually answer is worse than none,
    because they will send it there and wait.
    """
    dealer_id = the_desk(dealer_id)
    out = {}
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT phone_e164, proof_email, proof_whatsapp, proof_telegram
                   FROM dealers WHERE id = ?""", (dealer_id,)).fetchone()
    except Exception:
        row = None

    if row is not None:
        if row["proof_whatsapp"] or row["phone_e164"]:
            out["whatsapp"] = row["proof_whatsapp"] or row["phone_e164"]
        if row["proof_telegram"]:
            out["telegram"] = row["proof_telegram"]
        if row["proof_email"]:
            out["email"] = row["proof_email"]

    out["on_site"] = "show it to the technician when they arrive"
    return out


def date_provenance(asset_id: str) -> dict:
    """Where this machine's install date came from, and what it is worth."""
    with db.connect() as c:
        a = c.execute(
            "SELECT installed_on, installed_source FROM assets WHERE id=?",
            (asset_id,)).fetchone()

    if a is None:
        return {"known": False, "proven": False, "why": "unknown machine"}
    if not a["installed_on"]:
        return {"known": False, "proven": False,
                "source": a["installed_source"],
                "why": "we hold no install date for this machine"}

    source = a["installed_source"] or "unknown"
    proven = source in PROVEN
    return {
        "known": True,
        "proven": proven,
        "source": source,
        "installed_on": a["installed_on"],
        "why": ("we sold and installed this machine, so the date is ours"
                if proven else
                "the install date came from the customer rather than from our "
                "own paperwork, so cover on it is a claim and not a record"),
    }


def open_claim(asset_id: str, would_credit: float = 0.0,
               claimed_terms: str = "", quote_id: str = "",
               dealer_id: str = "") -> dict:
    """Record that a customer says their machine is covered, and say how to prove it.

    The visit is still quoted and booked as chargeable. This is the credit
    waiting to happen, not a discount already given.

    Args:
        asset_id: the machine.
        would_credit: what the claim is worth to them if it stands.
        claimed_terms: what they say the cover is, in their words.
        quote_id: the quote this would come off.
        dealer_id: whose books.
    """
    dealer_id = the_desk(dealer_id)
    from .trace import CALL, here

    with db.connect() as c:
        row = c.execute(
            """SELECT a.installed_on, s.account_id FROM assets a
               JOIN sites s ON s.id = a.site_id WHERE a.id = ?""",
            (asset_id,)).fetchone()
    if row is None:
        return {"ok": False, "why": "unknown machine"}

    claim_id = "WC-" + uuid.uuid4().hex[:6].upper()
    expires = (date.today() + timedelta(days=CLAIM_DAYS)).isoformat()

    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO warranty_claims
                   (id,dealer_id,account_id,asset_id,call_id,quote_id,
                    claimed_installed_on,claimed_terms,would_credit,
                    state,opened_at,expires_on)
                   VALUES (?,?,?,?,?,?,?,?,?,'awaiting_proof',?,?)""",
                (claim_id, dealer_id, row["account_id"], asset_id,
                 here() or None, quote_id or None, row["installed_on"],
                 claimed_terms or None, round(would_credit, 2),
                 datetime.now().isoformat(timespec="seconds"), expires))
    except Exception as e:
        print(f"[standing] could not open {claim_id}: {type(e).__name__}: {e}",
              flush=True)
        return {"ok": False, "why": "we could not record the claim"}

    channels = where_to_send_proof(dealer_id)
    return {
        "ok": True,
        "claim_id": claim_id,
        "would_credit": round(would_credit, 2),
        "expires_on": expires,
        "channels": channels,
        "say": (
            "Tell them the visit is quoted as chargeable and exactly why: we "
            "did not sell them this machine, so we have no paperwork for it. "
            f"Then tell them how to get the ${would_credit:.2f} back. They can "
            "show the invoice or the warranty certificate to the technician on "
            "the day, or send a photograph of it to us before then. Give them "
            "the claim number. "
            "Do NOT tell them it is covered and do NOT quote zero: promising "
            "a discount on paperwork nobody has seen, and then invoicing when "
            "it does not turn up, is how a customer stops believing us."),
    }


def record_proof(claim_id: str, channel: str, reference: str = "") -> dict:
    """Note that the paperwork arrived. Does NOT decide the claim.

    Deliberately stops short of accepting it. A photograph is a photograph;
    somebody still has to read it and agree it covers this machine on this
    date. The desk's job is to make sure it does not get lost.

    Args:
        claim_id: which claim.
        channel: whatsapp, telegram, email, or on_site.
        reference: the message or file it arrived as.
    """
    with db.connect() as c:
        row = c.execute("SELECT state, would_credit FROM warranty_claims "
                        "WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        return {"ok": False, "why": "no such claim"}

    with db.txn() as c:
        c.execute(
            """UPDATE warranty_claims
               SET state='evidence_received', evidence_channel=?,
                   evidence_ref=?, evidence_at=?
               WHERE id=?""",
            (channel, reference or None,
             datetime.now().isoformat(timespec="seconds"), claim_id))

    return {
        "ok": True, "claim_id": claim_id, "state": "evidence_received",
        "say": "Say we have got it and that somebody will check it against "
               "the machine. Do NOT say it is approved. You have not read it "
               "and you are not the person who decides.",
    }


def settle_claim(claim_id: str, accepted: bool, by: str,
                 note: str = "") -> dict:
    """A person decides. Not the desk, and not on the call.

    Args:
        claim_id: which claim.
        accepted: whether the paperwork stands it up.
        by: who decided. A technician who saw it on the doorstep counts.
        note: why.
    """
    if not by:
        return {"ok": False,
                "why": "a claim has to be settled by somebody, by name"}

    with db.txn() as c:
        c.execute(
            """UPDATE warranty_claims
               SET state=?, decided_by=?, decided_at=?, decided_note=?
               WHERE id=?""",
            ("accepted" if accepted else "rejected", by,
             datetime.now().isoformat(timespec="seconds"), note or None,
             claim_id))

    with db.connect() as c:
        row = c.execute("SELECT asset_id, claimed_installed_on, would_credit "
                        "FROM warranty_claims WHERE id=?", (claim_id,)).fetchone()

    # An accepted claim is not just a credit: it means the date we were given
    # is now backed by paperwork somebody has actually seen, so the next call
    # about this machine does not start from nothing.
    if accepted and row is not None:
        with db.txn() as c:
            c.execute("UPDATE assets SET installed_source=? WHERE id=?",
                      (OURS, row["asset_id"]))

    return {"ok": True, "claim_id": claim_id,
            "state": "accepted" if accepted else "rejected",
            "credit": round(row["would_credit"], 2) if (accepted and row) else 0.0}


def open_claims(dealer_id: str = "") -> list[dict]:
    """Claims still waiting on paperwork, so nothing quietly rots."""
    dealer_id = the_desk(dealer_id)
    today = date.today().isoformat()
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, asset_id, would_credit, state, opened_at, expires_on
               FROM warranty_claims
               WHERE dealer_id = ? AND state IN ('awaiting_proof','evidence_received')
               ORDER BY opened_at""", (dealer_id,)).fetchall()
    return [dict(r) | {"overdue": bool(r["expires_on"] and r["expires_on"] < today)}
            for r in rows]
