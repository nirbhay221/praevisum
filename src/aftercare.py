"""Do they want it with cover, and should they.

WHY THIS EXISTS

Nothing on this desk ever offered extended cover. A machine was sold, a date
was promised, and the conversation ended. Attach rate on service plans is one
of the largest margin lines a dealer has, and more to the point the customer
was never told what the standard term actually gives them, which is the part
they are entitled to know before they decide.

WHY IT IS NOT SIMPLY AN UPSELL

Because for a good deal of what this desk sells, the honest answer is no.

Herman Miller covers a chair for twelve years including labour. Humanscale
covers fifteen. Selling somebody three more years on top of that is selling
them nothing, and a desk that does it has told them something untrue in
exchange for money, which is the thing this entire project is built to avoid.

So this answers two questions and refuses to collapse them into one:

    what does the standard term already give you
    is buying more than that worth the money

and where the answer to the second is no, it says no, and says why.

THE NUMBERS ARE REAL

Extended cover on a refrigerator averages 11.3% of the appliance price, and
retail consumer electronics run 20 to 30%. The consumer advice on when to
decline is consistent and specific: skip anything priced above 15 to 25% of
retail, because the premium outruns the expected repair.

The argument FOR is equally real and is about downtime rather than the repair
bill. A Michigan restaurant chain put one refrigeration failure in summer peak
at $18,000 once lost trade and spoiled stock were counted, which is more than
five years of premiums. That is a true sentence and it is the only honest
reason to sell one of these on a commercial machine.

Sources are on each figure below and go out with the quote.
"""

from __future__ import annotations

WARRANTY_WEEK = "https://www.warrantyweek.com/archive/ww20260305.html"
ANGI = ("https://www.angi.com/articles/"
        "are-extended-warranties-appliances-worth-it.htm")
TRINITY = ("https://www.trinitywarranty.com/"
           "extended-service-agreements-esas/commercial-refrigeration")

# What the trade actually charges, as a share of the price, per year of extra
# cover. Derived from the published averages rather than chosen.
RATE_PER_YEAR = {
    "refrigeration": 0.038,   # 11.3% over a typical 3 extra years
    "av": 0.075,              # consumer electronics run 20-30%, mid of range
    "it": 0.060,
    "furniture": 0.030,
}

# Above this share of the purchase price, the published consumer advice is to
# decline, and so do we.
NOT_WORTH_IT_ABOVE = 0.20

# Cover at or beyond this many years is already long enough that adding to it
# buys almost nothing.
ALREADY_GENEROUS = 7.0

WHY_COMMERCIAL = (
    "One refrigeration failure in summer peak was measured at $18,000 by a "
    "Michigan restaurant chain once lost trade and spoiled stock were counted, "
    "which is more than five years of premiums. On a machine a kitchen cannot "
    "trade without, cover is about the downtime rather than the repair bill."
)



# Calls on which cover has actually been quoted out loud.
#
# The instruction says to offer it before the order and the model obeys about
# two times in three. Nothing enforced it, so the third customer was sold a
# machine and asked about cover afterwards -- which is the moment it gets
# refused, because they have finished buying.
#
# Recording it makes the order tool able to say "you have not offered cover
# yet", which is a fact rather than a reminder.
_QUOTED: dict[str, bool] = {}


def cover_was_quoted(call_id: str = "") -> bool:
    """Has extended cover been priced out loud on this call."""
    if not call_id:
        try:
            from .trace import here

            call_id = here()
        except Exception:
            return False
    return bool(_QUOTED.get(call_id or ""))


def remember_we_asked(call_id: str) -> None:
    """Mark that the desk has been told to offer cover on this call."""
    from .buying import _NUDGED

    if call_id:
        _NUDGED.add(call_id)


def forget_cover_quotes(call_id: str) -> None:
    _QUOTED.pop(call_id or "", None)
    try:
        from .buying import _NUDGED

        _NUDGED.discard(call_id or "")
    except Exception:
        pass


def warranty_options(manufacturer: str, model_number: str = "",
                     price: float = 0.0, family: str = "",
                     extra_years: int = 3) -> dict:
    """What cover this machine already carries, and what more would cost.

    Call it when somebody is about to buy something, BEFORE they commit, so
    they hear what the standard term gives them rather than finding out at the
    first fault.

    Args:
        manufacturer: the make.
        model_number: the model, which decides the line and so the term.
        price: what they are paying, which is what extended cover is priced off.
        family: reach-in freezer, laptop, office chair.
        extra_years: how many additional years to quote for.
    """
    from .cover import published_terms
    from .market import _trade_for

    # Whatever the answer turns out to be, the question has now been asked on
    # this call, which is what the order tool needs to know.
    try:
        from .trace import here

        _QUOTED[here()] = True
    except Exception:
        pass

    # THE FAMILY DECIDES THE RATE, AND NOBODY WAS PASSING ONE.
    #
    # HEARD LIVE. This quoted three years on a $199.99 projector at $22.60 and
    # the order line came out at $45.00. Both were computed honestly: with no
    # family given, this fell back to the refrigeration rate of 11.3%, while
    # the order knew the machine was a projector and used the audio-visual
    # rate of 21%. The customer agreed to one number and was invoiced the
    # other.
    #
    # Looked up rather than defaulted. We know what we sell; the make and
    # model are already in front of us, and guessing the trade from nothing is
    # how the two halves of one quote disagreed.
    if not family and manufacturer:
        try:
            from . import db

            with db.connect() as c:
                row = c.execute(
                    """SELECT family FROM product_stock
                       WHERE LOWER(manufacturer) = LOWER(?)
                         AND (? = '' OR LOWER(model_number) = LOWER(?)
                              OR LOWER(?) LIKE '%' || LOWER(model_number) || '%')
                       LIMIT 1""",
                    (manufacturer, model_number, model_number,
                     model_number)).fetchone()
            if row and row["family"]:
                family = row["family"]
        except Exception as e:
            print(f"[aftercare] could not look up the family for "
                  f"{manufacturer} {model_number}: {type(e).__name__}: {e}",
                  flush=True)

    if not price or price <= 0:
        # WE ALREADY KNOW WHAT IT COSTS. WE SAID SO A MINUTE AGO.
        #
        # HEARD LIVE. The desk read out an Xming projector at $159.00, the
        # customer asked "do you have any warranty on this", and this was
        # called with the make and no price. Cover is a share of the price, so
        # it refused -- and the desk fell back to "I can check on the coverage
        # for you", which is the answer this whole feature was built to stop
        # giving.
        #
        # The figure is in the register of what was read out on this call.
        # Asking the model to pass a number it has already said out loud is
        # the same mistake as asking it to carry an order id.
        try:
            from .shortlist import the_one_they_picked, what_we_offered

            want = f"{manufacturer} {model_number}".lower().strip()
            for row in ([the_one_they_picked()] + what_we_offered()):
                if not row:
                    continue
                mine = (f"{row.get('manufacturer', '')} "
                        f"{row.get('model_number', '')}").lower().strip()
                if not mine:
                    continue
                if (manufacturer or "").lower() in mine or mine in want:
                    price = float(row.get("list_price") or 0)
                    family = family or row.get("family") or ""
                    if price > 0:
                        print(f"[aftercare] no price passed for "
                              f"{manufacturer} {model_number}; using the "
                              f"${price:,.2f} read out on this call",
                              flush=True)
                        break
        except Exception as e:
            print(f"[aftercare] could not recover the price for "
                  f"{manufacturer}: {type(e).__name__}: {e}", flush=True)

    trade = _trade_for(family) if family else ""
    terms = None
    try:
        terms = published_terms(manufacturer, model_number)
    except Exception as e:
        print(f"[aftercare] could not read published terms: "
              f"{type(e).__name__}: {e}", flush=True)

    standard = float((terms or {}).get("parts_years") or 0)
    labour = float((terms or {}).get("labour_years") or 0)
    note = (terms or {}).get("condition_note")

    out = {
        "manufacturer": manufacturer,
        "model_number": model_number,
        "standard_years": standard or None,
        "standard_labour_years": labour or None,
        "condition": note,
        "source": (terms or {}).get("source_url"),
    }

    if not terms:
        # WE DO NOT KNOW WHAT SERTA GIVES YOU. WE DO KNOW WHAT WE GIVE YOU.
        #
        # This used to stop here and tell the customer we hold no terms for
        # the make, which is true and is not an answer to what they asked.
        # Somebody buying a chair wants to know whether the chair is
        # protected, not who underwrites it, and "we do not know" is not a
        # product. It went out on three separate live calls.
        #
        # Our own plan does not depend on knowing the maker's term: it starts
        # the day it is delivered and states its own cover. The old refusal
        # was right that EXTENDING an unknown term sells an unknown quantity,
        # and that is a different thing from selling a plan of our own.
        ours = {}
        try:
            from .our_cover import plans_for

            ours = plans_for(price, family)
        except Exception as e:
            print(f"[aftercare] could not price our own cover: "
                  f"{type(e).__name__}: {e}", flush=True)

        if ours.get("plans"):
            # WHAT WE SAID, KEPT, SO THE ORDER CHARGES IT.
            #
            # HEARD ON A LIVE CALL. This quoted $22.60 for three years on a
            # $199.99 projector and the order line came out at $45.00 -- twice
            # the price the customer had agreed to. Both figures were computed
            # honestly and from different families: this had none passed in,
            # so it priced at the refrigeration rate, and the order knew the
            # machine was a projector and priced at the audio-visual one.
            #
            # There is no version of this where recomputing is right. They
            # were told a number and said yes to that number.
            try:
                from .quoted import we_said

                for plan in ours["plans"]:
                    we_said("OurCover",
                            f"{plan['tier']} {plan['years']}yr",
                            plan["price"], "our own plan, quoted on this call")
            except Exception:
                pass

            out["ok"] = True
            out["standard_terms_on_file"] = False
            out["our_own_cover"] = ours
            out["say"] = (
                "Do NOT say we have nothing. Say the honest version: we do "
                "not hold this maker's published terms, so you will not "
                "quote a term you cannot stand behind, AND we sell cover of "
                "our own that starts the day it is delivered. "
                + ours["say"])
            return out

        out["ok"] = False
        out["why"] = "we hold no published terms for this make"
        # WRONG DIRECTION, HEARD ON A LIVE SALES CALL. This used to end "ask
        # whether they have the paperwork", which is the right thing to say
        # about a machine they ALREADY OWN and we did not sell. Here they are
        # BUYING, from us, brand new. Asking the customer for the warranty
        # paperwork on something we are about to hand them reads as though we
        # do not know what we sell, and there is no paperwork for them to have
        # yet.
        #
        # What is true: we hold no terms for this make on file. That is our
        # gap to close before they commit, not a question for them.
        out["say"] = ("Say plainly that we do not hold this maker's published "
                      "terms on file, and that you will confirm the cover "
                      "before they commit rather than guess at it. Do NOT ask "
                      "THEM for paperwork: they are buying it new from us and "
                      "there is none for them to have. Do NOT offer extended "
                      "cover on top of a term you cannot state, because they "
                      "would be buying an unknown quantity.")
        return out

    if not price or price <= 0:
        out["ok"] = False
        out["why"] = "no price to quote cover against"
        out["say"] = ("Get the price first, then offer cover as a share of "
                      "it. Call price_for or look it up on the floor: do NOT "
                      "tell them you will check the cover later, because we "
                      "sell cover of our own and can quote it the moment we "
                      "know the price.")
        return out

    rate = RATE_PER_YEAR.get(trade, 0.05)
    cost = round(price * rate * max(1, extra_years), 2)
    share = cost / price

    out.update({
        "ok": True,
        "extra_years": extra_years,
        "covered_until_years": standard + extra_years,
        "price": round(cost, 2),
        "share_of_price": round(share, 3),
        "priced_from": (f"{rate * 100:.1f}% of the purchase price per year, "
                        f"from published averages: extended cover on a "
                        f"refrigerator averages 11.3% of price and retail "
                        f"electronics run 20 to 30%"),
        "sources": [WARRANTY_WEEK, ANGI],
    })

    # THE HONEST NO, IN THE TWO CASES WHERE IT IS THE RIGHT ANSWER.
    if standard >= ALREADY_GENEROUS:
        out["recommend"] = False
        out["say"] = (
            f"Tell them plainly NOT to buy it. {manufacturer} already covers "
            f"this for {standard:.0f} years"
            + (f" including labour" if labour >= standard else "")
            + ". Adding to a term that long buys them almost nothing, and "
              "saying so is worth more than the sale. Read them the standard "
              "term instead, and mention what it excludes."
        )
        return out

    if share > NOT_WORTH_IT_ABOVE:
        out["recommend"] = False
        out["say"] = (
            f"At ${cost:,.2f} that is {share * 100:.0f}% of the purchase "
            "price, and the published advice is to decline anything above "
            "twenty per cent because the premium outruns the expected repair. "
            "Quote it if they ask, and say plainly you would not take it."
        )
        return out

    out["recommend"] = True
    out["say"] = (
        f"Offer it, once, and let them decide: {extra_years} more years takes "
        f"the cover to {standard + extra_years:.0f}, at ${cost:,.2f}, which is "
        f"{share * 100:.0f}% of the price. "
        + (WHY_COMMERCIAL if trade == "refrigeration" else
           "Say what it covers and what it does not, and do not press it a "
           "second time if they say no.")
        + " Never imply the standard term is worse than it is to make the "
          "extra look necessary."
    )
    return out
