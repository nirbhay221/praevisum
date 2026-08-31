"""What the visit costs, and which lines the warranty pays for.

WHY THIS DID NOT EXIST AND SHOULD HAVE

The desk could price a PART. It could not price a JOB. A visit recorded
`labor_hours` after the fact and nothing anywhere held a labour rate, a
call-out charge or an out-of-hours premium: grep found zero references to any
of the three.

So on a real call the first question anybody asks, what will this cost me, hit
the standing rule that there are no prices beyond what a tool returned, and
became "I will have to confirm and follow up". Every time. That is worse than
a wrong number: a wrong number gets corrected, and a desk that cannot answer
the money question is a desk they stop trusting to answer any question.

COVERAGE IS PER LINE, NOT PER MACHINE

This is the whole shape of the module, and it comes from what the published
warranties actually say rather than from what would be convenient as a flag on
a row:

  Wear items are excluded from every one of them. A door gasket is chargeable
  on a machine that is otherwise fully covered, and the door gasket is one of
  the commonest calls we take.

  Compressor cover outlasts parts and labour cover nearly everywhere, so a six
  and a half year old Traulsen has a covered compressor and nothing else.

  Traulsen ship the replacement compressor and bill the owner for fitting it.
  The part is free and the four hours are not.

A single covered or not-covered boolean gets all three wrong, and gets them
wrong in the direction where somebody is surprised by an invoice.

WHERE THE NUMBERS COME FROM

The hourly rate: BLS Occupational Employment and Wage Statistics for
occupation 49-9021, refrigeration mechanics, in this dealer's own metro rather
than a national average. Federal data, the same category as the equipment
catalogue and the recalls, with the series ID recorded so it can be pulled
again by anyone who doubts it.

The hours: `repairs.labor_hours`, which is what jobs on that family actually
took on our own book. The same evidence the van loading uses, asked a
different way. A median rather than a mean, because one four hour disaster
should not raise the quote on every straightforward call.

NOTHING HERE IS AN INVOICE

Every line says what it is based on, and the range is reported next to the
total, because a job that has taken between one and four hours before will not
take exactly 1.9 this time. Quoting one number as though it were firm is how a
quote turns into an argument.
"""

from __future__ import annotations

import statistics as st
import uuid
from datetime import datetime

from . import db, trace
from .trace import here
from .tenancy import the_desk
from .cover import covers

# Hourly wage for occupation 49-9021, Heating, Air Conditioning and
# Refrigeration Mechanics and Installers, in the Davenport-Moline-Rock Island
# metro. BLS Occupational Employment and Wage Statistics, 2025:
#
#   mean    $33.65   series OEUM001934000000049902103
#   median  $31.34   series OEUM001934000000049902108
#
# The median, because a mean wage is pulled up by supervisors and estimators
# and this figure stands in for the person who actually drives out. A fallback
# rather than the primary source: `dealers.labour_rate` wins wherever a dealer
# has set their own.
BLS_HOURLY_WAGE = 31.34
BLS_SERIES = "OEUM001934000000049902108"
BLS_YEAR = 2025

# What the shop charges for an hour, as a multiple of what the hour costs.
#
# The wage is the technician. The rate carries the van, the fuel, the
# insurance, the EPA certification, the parts counter, the phone and the time
# between jobs. Named rather than folded into a formula, so a dealer who
# thinks it is wrong has something to point at.
SHOP_MULTIPLIER = 2.6

# Turning up costs money before anybody picks up a spanner.
CALL_OUT = 95.0

# Outside working hours. The premium is charged even on a covered machine:
# manufacturer labour cover is straight time and the overtime is the owner's.
# That is trade convention rather than a line we can quote from a document, so
# the quote says so on the line itself instead of burying it.
AFTER_HOURS_MULTIPLIER = 1.5
WORKING_FROM, WORKING_TO = 8, 18

# Below this many comparable jobs there is no basis for an estimate, and
# saying so beats dividing one anecdote by itself.
ENOUGH_JOBS = 3

# What a service call is assumed to take when the corpus has nothing. Stated
# rather than hidden, and reported as an assumption wherever it is used.
ASSUMED_HOURS = 1.5


def labour_rate(dealer_id: str = "") -> dict:
    """What an hour costs here, and where the number came from.

    Args:
        dealer_id: whose rates.
    """
    dealer_id = the_desk(dealer_id)
    rate = None
    trade = None
    try:
        with db.connect() as c:
            row = c.execute("SELECT labour_rate, trade FROM dealers WHERE id = ?",
                            (dealer_id,)).fetchone()
            if row is not None:
                rate, trade = row["labour_rate"], row["trade"]
    except Exception as e:
        print(f"[pricing] no dealer rate on file: {type(e).__name__}: {e}",
              flush=True)

    if rate:
        return {"rate": round(float(rate), 2),
                "source": "this dealer's own posted rate"}

    # THE DEALER'S OWN TRADE, not refrigeration for everybody.
    #
    # This used to be a constant naming occupation 49-9021, refrigeration
    # mechanics. One service answers two businesses' phones, and an IT job was
    # being quoted at a refrigeration mechanic's wage with the same confidence
    # as everything else. The database has known there were two trades from
    # the beginning; the pricing did not.
    if trade:
        try:
            with db.connect() as c:
                t = c.execute("SELECT * FROM trade_rates WHERE trade = ?",
                              (trade,)).fetchone()
            if t is not None:
                return {
                    "rate": round(t["hourly_wage"] * t["multiplier"], 2),
                    "trade": trade,
                    "source": (f"BLS OEWS {t['year']} median wage of "
                               f"${t['hourly_wage']} an hour for occupation "
                               f"{t['occupation']}, {t['occupation_name']}, "
                               f"{t['geography']}, series {t['series_id']}, "
                               f"times {t['multiplier']} to carry the "
                               "overheads of that trade"),
                }
        except Exception as e:
            print(f"[pricing] no trade rate for {trade}: "
                  f"{type(e).__name__}: {e}", flush=True)

    return {
        "rate": round(BLS_HOURLY_WAGE * SHOP_MULTIPLIER, 2),
        "source": (f"BLS OEWS {BLS_YEAR} median wage of ${BLS_HOURLY_WAGE} an "
                   f"hour for occupation 49-9021 in this metro, series "
                   f"{BLS_SERIES}, times {SHOP_MULTIPLIER} to carry the van, "
                   "the insurance and the certification"),
    }


def call_out_fee(dealer_id: str = "") -> float:
    """What it costs to turn up, before anybody picks up a spanner."""
    dealer_id = the_desk(dealer_id)
    try:
        with db.connect() as c:
            row = c.execute("SELECT call_out_fee, trade FROM dealers WHERE id = ?",
                            (dealer_id,)).fetchone()
            if row and row["call_out_fee"]:
                return round(float(row["call_out_fee"]), 2)
            # Per trade, for the same reason the hourly rate is. Sending a
            # refrigeration van is not the same cost as sending somebody with
            # a toolkit.
            if row and row["trade"]:
                t = c.execute("SELECT call_out FROM trade_rates WHERE trade=?",
                              (row["trade"],)).fetchone()
                if t and t["call_out"]:
                    return round(float(t["call_out"]), 2)
    except Exception:
        pass
    return CALL_OUT


def hours_for(family: str, dealer_id: str = "") -> dict:
    """How long jobs on this kind of machine have actually taken.

    The median, with the tenth and ninetieth alongside it, because the honest
    answer to how long this will take is a range and not a number.
    """
    dealer_id = the_desk(dealer_id)
    # An unknown family must not silently become an invented number. Falling
    # back to every job on the book is a wider answer, but it is still an
    # answer made of jobs we actually did, and the basis line says which one
    # was used. On a real call a null family sent this to the 1.5 hour
    # assumption while 114 comparable jobs sat in the table.
    where = ("WHERE dealer_id = ? AND family = ? AND labor_hours IS NOT NULL"
             if family else
             "WHERE dealer_id = ? AND labor_hours IS NOT NULL")
    params = (dealer_id, family) if family else (dealer_id,)

    try:
        with db.connect() as c:
            hours = sorted(
                r["labor_hours"] for r in c.execute(
                    f"SELECT labor_hours FROM repairs {where}", params)
                if r["labor_hours"])
    except Exception as e:
        print(f"[pricing] could not read past hours: {type(e).__name__}: {e}",
              flush=True)
        hours = []

    if len(hours) < ENOUGH_JOBS:
        return {"hours": ASSUMED_HOURS, "jobs": len(hours),
                "basis": ("we have not done enough of these to say, so this "
                          f"assumes {ASSUMED_HOURS} hours")}

    return {
        "hours": round(st.median(hours), 2),
        "jobs": len(hours),
        "low": hours[len(hours) // 10],
        "high": hours[-max(1, len(hours) // 10)],
        "basis": ((f"the median of {len(hours)} jobs on machines like this on "
                   "our own book") if family else
                  (f"the median of all {len(hours)} jobs on our book, because "
                   "we do not know what kind of machine this is")),
    }


def _after_hours(when: datetime | None) -> bool:
    when = when or datetime.now()
    return not (WORKING_FROM <= when.hour < WORKING_TO and when.weekday() < 5)


def _part_rows(skus: list[str], dealer_id: str) -> list[dict]:
    if not skus:
        return []
    with db.connect() as c:
        out = []
        for sku in skus:
            r = c.execute(
                "SELECT sku, name, unit_cost FROM parts WHERE sku=? AND dealer_id=?",
                (sku, dealer_id)).fetchone()
            if r is not None:
                out.append(dict(r))
        return out


def _apply_offers(priced: list[dict], dealer_id: str, tier: str) -> None:
    """Discount any priced part a live offer touches. Never raises.

    THE SHAPE IS CHECKED, NOT ASSUMED. The first version of this read a dict
    keyed by sku and looked for `offer_price`. `offers_on_many` returns
    {"offers": [ ... ]}, a LIST, and the discounted figure is called `now`. It
    ran clean, logged nothing, and quoted every part at full price: a guess
    about a return shape fails silently and looks like a feature that is
    simply off.

    Mutates in place. An offers lookup that fails leaves the quote whole at
    full price, because charging the undiscounted amount is a conversation and
    losing the quote is a dropped call.
    """
    if not priced:
        return
    try:
        from .offers import offers_on_many

        got = offers_on_many([p["sku"] for p in priced], dealer_id,
                             tier or "unknown")
    except Exception as e:
        print(f"[pricing] could not check offers on a quote: "
              f"{type(e).__name__}: {e}", flush=True)
        return

    by_sku = {d.get("sku"): d for d in (got or {}).get("offers", [])
              if isinstance(d, dict) and d.get("applies")}

    for row in priced:
        deal = by_sku.get(row["sku"])
        if not deal:
            continue
        now = deal.get("now")
        if now is None or float(now) >= row["unit_cost"]:
            continue
        row["was"] = row["unit_cost"]
        row["unit_cost"] = round(float(now), 2)
        row["offer"] = deal.get("promotion") or "a live offer"
        row["saving"] = round(row["was"] - row["unit_cost"], 2)


def _who_pays(cover: dict) -> str:
    """Which party is actually carrying this, in the customer's hearing.

    WHY THIS IS NOT COSMETIC. The line used to read "covered by Avantco under
    the 3 year parts and labour term" whenever labour came back covered, even
    when the manufacturer term had run out years ago and the customer was
    covered because they BOUGHT an extension from us.

    That is a factual error with a consequence: somebody reading it files a
    warranty claim against a manufacturer who owes nothing, the claim is
    refused, and the customer is chased for a bill they already paid to avoid.
    """
    if cover.get("extended_labour") or cover.get("extended_parts"):
        return (f"covered by the extended cover they bought from us, to "
                f"{cover.get('extended_to')}. This is ours, not the "
                "manufacturer's: do not file it as a warranty claim")
    return (f"covered by {cover.get('manufacturer') or 'the manufacturer'} "
            f"under {cover.get('terms') or 'the warranty'}")


def quote_visit(asset_id: str, parts: list[str] | None = None,
                when: str = "", dealer_id: str = "") -> dict:
    """What this visit will cost, line by line, and who pays each line.

    Always quotes. A covered machine gets a quote that says zero, which is a
    far better sentence than silence and is the moment a customer finds out
    that the cover they have been paying for did something.

    Coverage is worked out per line, because that is how the published
    warranties read: a compressor can be covered while the labour to fit it is
    not, and a door gasket is chargeable on a machine that is otherwise
    entirely covered.

    Args:
        asset_id: the machine.
        parts: SKUs the fault points at, from the assessment or what_to_load.
        when: ISO datetime of the visit, for the out of hours question.
        dealer_id: whose rates.
    """
    dealer_id = the_desk(dealer_id)
    with db.connect() as c:
        asset = c.execute(
            "SELECT id, manufacturer, model_number, family FROM assets WHERE id=?",
            (asset_id,)).fetchone()
    if asset is None:
        return {"ok": False, "why": "unknown machine"}

    priced = _part_rows(parts or [], dealer_id)

    # Labour follows the part the fault most likely sits in, because that is
    # the clock the manufacturer applies to the repair. With no part named it
    # falls to the ordinary parts and labour term.
    driver = priced[0]["name"] if priced else ""
    main = covers(asset_id, driver)

    rate = labour_rate(dealer_id)
    est = hours_for(asset["family"], dealer_id)

    # WHAT THIS CUSTOMER IS TO US. Everyone used to be priced identically:
    # somebody on account for nine years and somebody who found the number
    # this morning got the same figure. A first visit to a stranger carries no
    # credit terms, no service agreement, nothing known about the site, and it
    # is settled on the day.
    from . import standing as st_mod

    with db.connect() as c:
        acct = c.execute(
            """SELECT s.account_id FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE a.id = ?""", (asset_id,)).fetchone()
    who = st_mod.standing(acct["account_id"] if acct else "", dealer_id)

    # LIVE OFFERS, APPLIED HERE RATHER THAN WHEN SOMEBODY REMEMBERS TO ASK.
    #
    # `offers_on_many` was written for exactly this ("every live offer
    # touching a list of parts, for a whole quote") and nothing called it, so
    # a quote containing a part under a running offer was priced at full cost.
    # The customer only got it if the agent separately thought to look it up,
    # which is the behaviour offers.py exists to end.
    #
    # After the tier, not before: two of the offers on this book are trade
    # accounts only, and applying one to a stranger is the same failure as
    # reading a trade price out to whoever rings.
    _apply_offers(priced, dealer_id, who.get("tier", "unknown"))
    rate = dict(rate)
    rate["rate"] = round(rate["rate"] * who["multiplier"], 2)
    if who["multiplier"] != 1.0:
        rate["source"] += (f", at {who['multiplier']} times because {who['why']}")

    at = None
    if when:
        try:
            at = datetime.fromisoformat(when)
        except ValueError:
            at = None
    out_of_hours = _after_hours(at)

    day_rate = rate["rate"]
    labour = round(day_rate * est["hours"], 2)
    premium = round(labour * (AFTER_HOURS_MULTIPLIER - 1), 2) if out_of_hours else 0.0
    fee = call_out_fee(dealer_id)

    lines: list[dict] = [
        {"what": "Call-out", "amount": fee, "charged": not main["labour"],
         "why": ("covered: this is a warranty repair inside the labour term"
                 if main["labour"] else
                 "turning up, before anybody picks up a spanner")},
        {"what": f"Labour, about {est['hours']} hours", "amount": labour,
         "charged": not main["labour"],
         "why": (_who_pays(main) if main["labour"] else est["basis"])},
    ]

    if premium:
        lines.append({
            "what": "Out of hours premium", "amount": premium, "charged": True,
            "why": (f"{AFTER_HOURS_MULTIPLIER} times the day rate outside "
                    f"{WORKING_FROM}:00 to {WORKING_TO}:00. Charged even on a "
                    "covered machine, because manufacturer labour cover is "
                    "straight time and the overtime is the owner's. That is "
                    "trade convention rather than a line from their warranty "
                    "document, so say it as ours")})

    for p in priced:
        line = covers(asset_id, p["name"])
        lines.append({
            "what": p["name"], "amount": round(p["unit_cost"] or 0, 2),
            "charged": not line["parts"],
            "conditional": True,
            "why": (("covered, " + (line.get("why") or "")) if line["parts"]
                    else (line.get("why") or "not covered")),
        })
        # SAY THE DISCOUNT. A price that is quietly lower is a price the
        # customer cannot check, and the offer's own guidance is to mention it
        # before they agree the full amount rather than after.
        if p.get("offer"):
            lines[-1]["offer"] = p["offer"]
            lines[-1]["was"] = p["was"]
            lines[-1]["why"] = (f"{p['offer']}, saving {p['saving']:.2f}. "
                                + lines[-1]["why"])

    charged = round(sum(x["amount"] for x in lines if x["charged"]), 2)
    absorbed = round(sum(x["amount"] for x in lines if not x["charged"]), 2)

    # A CLAIM IS NOT A DISCOUNT. Where the cover rests on a date the customer
    # gave us rather than on our own paperwork, everything is charged and the
    # difference is recorded as what they get back when somebody has seen the
    # invoice. Quoting zero on paperwork nobody has read, and then billing
    # when it does not turn up, is how a customer stops believing us.
    would_credit = 0.0
    if main.get("needs_proof"):
        would_credit = round(
            sum(x["amount"] for x in lines
                if x["charged"] and "Out of hours" not in x["what"]), 2)

    quote_id = "Q-" + uuid.uuid4().hex[:6].upper()

    out = {
        "ok": True,
        "quote_id": quote_id,
        "machine": f"{asset['manufacturer']} {asset['model_number']}",
        "lines": lines,
        "total": charged,
        "covered_by_warranty": absorbed,
        "warranty": main.get("why"),
        "warranty_source": main.get("source"),
        "after_hours": out_of_hours,
        "hours": est["hours"],
        "hours_from": est["basis"],
        "rate_from": rate["source"],
        "standing": who["tier"],
        "standing_why": who["why"],
    }

    if would_credit:
        claim = st_mod.open_claim(asset_id, would_credit,
                                  claimed_terms=main.get("terms") or "",
                                  quote_id=quote_id, dealer_id=dealer_id)
        out["claim"] = claim
        out["would_credit"] = would_credit
        out["say"] = (
            f"Quote the ${charged:.2f} and say plainly why it is chargeable: "
            "we did not sell them this machine, so we hold no warranty "
            "paperwork for it. Then tell them how to get "
            f"${would_credit:.2f} of it back. They can show the invoice or the "
            "warranty certificate to the technician on the day, or send a "
            "photograph of it to us before then, and give them the claim "
            "number. Do NOT tell them it is covered.")
        if claim.get("ok"):
            out["send_proof_to"] = claim["channels"]
        return _finish(out, est, main, day_rate, out_of_hours, charged,
                       quote_id, dealer_id, asset_id, rate, absorbed, lines)

    # The uncertainty is all in the hours, so the total only swings where the
    # labour is being charged for.
    if "low" in est and not main["labour"]:
        hourly = day_rate * (AFTER_HOURS_MULTIPLIER if out_of_hours else 1)
        out["range"] = [round(charged - hourly * (est["hours"] - est["low"]), 2),
                        round(charged + hourly * (est["high"] - est["hours"]), 2)]
        out["range_why"] = (f"jobs like this have run from {est['low']} to "
                            f"{est['high']} hours")

    if absorbed and not charged:
        out["say"] = ("Say there is nothing to pay, and say why: the machine is "
                      "inside its warranty. This is the moment the cover they "
                      "have been paying for does something, so do not bury it "
                      "in the middle of a sentence.")
    elif absorbed:
        out["say"] = (f"Lead with the ${absorbed:.2f} the warranty covers, then "
                      f"the ${charged:.2f} it does not, and say plainly which "
                      "is which. Somebody who hears the word covered and then "
                      "gets an invoice will not believe the next thing we tell "
                      "them.")
    else:
        out["say"] = ("Give the total and what it is made of. The parts lines "
                      "are conditional and must be said as conditional: they "
                      "are only charged if that is what it turns out to be. "
                      "This is an estimate and not an invoice, and the "
                      "technician confirms it on site.")

    if main.get("condition"):
        out["say"] += " " + main["condition"]

    return _finish(out, est, main, day_rate, out_of_hours, charged,
                   quote_id, dealer_id, asset_id, rate, absorbed, lines)


def _finish(out, est, main, day_rate, out_of_hours, charged, quote_id,
            dealer_id, asset_id, rate, absorbed, lines):
    """Record it and publish it. One place, so both paths behave the same."""
    _record(quote_id, dealer_id, asset_id, est, rate, charged, absorbed,
            out_of_hours, lines)
    trace.quote(dealer_id, out)
    return out


def _record(quote_id, dealer_id, asset_id, est, rate, charged, absorbed,
            out_of_hours, lines) -> None:
    """Keep what we told them.

    A quote given on a call is the thing most likely to be argued about later,
    and it is the only way the review pass can compare what was quoted against
    what the visit actually billed.
    """
    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO quotes (id, dealer_id, call_id, asset_id, hours,
                                       hourly_rate, rate_source, total,
                                       covered_total, after_hours, quoted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (quote_id, dealer_id, trace.here() or None, asset_id,
                 est["hours"], rate["rate"], rate["source"], charged, absorbed,
                 int(out_of_hours),
                 datetime.now().isoformat(timespec="seconds")))
            for i, line in enumerate(lines):
                c.execute(
                    """INSERT INTO quote_lines (quote_id, seq, what, amount,
                                                charged, why)
                       VALUES (?,?,?,?,?,?)""",
                    (quote_id, i, line["what"], line["amount"],
                     int(line["charged"]), line.get("why")))
    except Exception as e:
        # A quote that cannot be filed is still a quote worth giving.
        print(f"[pricing] could not record {quote_id}: {type(e).__name__}: {e}",
              flush=True)
