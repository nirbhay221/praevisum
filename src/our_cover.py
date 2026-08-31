"""Our own protection plan, sold under our name rather than the maker's.

WHY IT HAD TO EXIST

`warranty_options` could only talk about cover it could quote from the
manufacturer's published terms. When we hold none for a make -- which is most
of the shop floor -- it said this, on three separate live calls:

    "The warranty is provided by the manufacturer, Serta, and we don't have
     their specific terms on file."
    "I'm sorry, I don't have the manufacturer's warranty terms for that one
     on file."

Every one of those was true, and every one of them was the wrong answer,
because the customer was not asking who underwrites it. They were asking
whether the thing they are about to buy is protected. "We do not know" is not
a product, and a desk that sells a $400 chair and cannot say what happens if
it breaks has not finished the sale.

The old refusal had a real argument behind it -- that EXTENDING a term you
cannot state sells somebody an unknown quantity -- and that argument is
correct about extensions and does not apply here. A plan of our own does not
extend anything. It starts the day it is bought, it says what it covers in its
own words, and it stands whether or not we know what Serta does. This is how
the large retailers sell it: an own-brand furniture or electronics plan sits
next to the item, underwritten separately from whatever the maker offers.

WHERE THE NUMBERS COME FROM

Published retail pricing, not invented:

    furniture      a 3 year plan runs 10-17% of the purchase price; 5 year
                   plans run 10-15%; office furniture lands at $50-150
    electronics    retail consumer electronics run 20-30% over the term
    refrigeration  extended cover averages 11.3% of the appliance price

ACCIDENTAL DAMAGE IS THE SECOND TIER because it is genuinely a different
product. Mechanical failure is the maker's problem eventually; a torn seat or
a drop is not covered by anybody unless somebody sells that cover. It is
priced higher because it is claimed on far more often.

AND IT STILL SAYS NO. The decline threshold in aftercare.py applies here
exactly as it applies to an extension: above a fifth of the purchase price the
published advice is to decline, and a plan having our name on it is not a
reason to stop giving that advice. Selling our own cover is worth doing.
Selling it to somebody it cannot pay back is not.
"""

from __future__ import annotations

MULBERRY = "https://www.getmulberry.com/articles/calculate-extended-warranty-cost"
ONPOINT = ("https://www.onpointwarranty.com/"
           "furniture-protection-plans-for-retailers-the-complete-guide")
ASURION = "https://www.asurion.com/protection-plans/"

# What a plan of this length costs, as a share of the purchase price, per
# trade. Taken from the published retail bands above and kept at the lower end
# of each: this is a real price quoted to somebody on the phone, not a margin
# to maximise.
#
# Read as {trade: {years: share of purchase price}}.
BANDS = {
    "furniture":     {3: 0.115, 5: 0.145},   # 10-17% at 3 years, 10-15% at 5
    "it":            {2: 0.130, 3: 0.185},   # electronics 20-30% over a term
    "av":            {2: 0.150, 3: 0.210},
    "refrigeration": {3: 0.113, 5: 0.160},   # 11.3% is the published average
}

# The accidental damage tier, as a multiplier on the mechanical one. Claimed
# on several times more often than mechanical failure, which is the whole
# reason it is priced separately rather than folded in.
ACCIDENT_MULTIPLIER = 1.55

# What each tier actually does, in the words the desk should use. Kept here
# rather than in the model's instructions so it is the same sentence every
# time, and so the exclusions travel with the offer instead of being dropped
# whenever the conversation is going well.
TIERS = {
    "essential": {
        "name": "Essential",
        "covers": ("mechanical and electrical failure, parts and labour, "
                   "with no excess to pay"),
        "excludes": ("accidental damage, cosmetic marks, and anything that "
                     "was already wrong when it arrived"),
    },
    "complete": {
        "name": "Complete",
        "covers_by_trade": {
            "furniture": ("everything in Essential, plus accidental damage: "
                          "stains, rips, and frame or mechanism breakage"),
            "it": ("everything in Essential, plus accidental damage: drops, "
                   "spills, and power surges"),
            "av": ("everything in Essential, plus accidental damage: drops, "
                   "spills, and power surges"),
            "refrigeration": ("everything in Essential, plus accidental "
                              "damage and one deep-clean callout a year"),
        },
        "excludes": ("deliberate damage, normal wear, and anything that was "
                     "already wrong when it arrived"),
    },
}


def _trade_of(family: str) -> str:
    from .market import _trade_for

    try:
        return _trade_for(family or "") or "furniture"
    except Exception:
        return "furniture"


def plans_for(price: float, family: str = "", starts_when: str = "") -> dict:
    """What we can sell them on this item, and what each one costs.

    Args:
        price: the purchase price, which everything is a share of.
        family: office chair, laptop, reach-in freezer. Decides the trade
            rates and which accidental damage wording is true.
        starts_when: when cover begins, if the maker's term is known and ours
            picks up after it. Blank means day one, which is what we say when
            we hold no term for the make.

    Returns:
        Every tier and length we can quote, cheapest first, each with what it
        covers and what it does not. Never a bare number: a price with no
        terms attached is not an offer anybody can accept.
    """
    if not price or price <= 0:
        return {"ok": False, "why": "no price to quote a plan against",
                "say": "Get the price first. Cover is a share of it."}

    trade = _trade_of(family)
    bands = BANDS.get(trade) or BANDS["furniture"]

    offers = []
    for years, share in sorted(bands.items()):
        base = round(price * share, 2)
        offers.append({
            "tier": TIERS["essential"]["name"],
            "years": years,
            "price": base,
            "share_of_price": round(base / price, 3),
            "covers": TIERS["essential"]["covers"],
            "excludes": TIERS["essential"]["excludes"],
        })
        top = round(base * ACCIDENT_MULTIPLIER, 2)
        offers.append({
            "tier": TIERS["complete"]["name"],
            "years": years,
            "price": top,
            "share_of_price": round(top / price, 3),
            "covers": TIERS["complete"]["covers_by_trade"].get(
                trade, TIERS["complete"]["covers_by_trade"]["furniture"]),
            "excludes": TIERS["complete"]["excludes"],
        })

    offers.sort(key=lambda o: o["price"])

    # THE SAME LINE WE DRAW ON THE MAKER'S EXTENSIONS. Above a fifth of the
    # purchase price the published advice is to decline, and that does not
    # stop being true because the plan has our name on it.
    from .aftercare import NOT_WORTH_IT_ABOVE

    worth_it = [o for o in offers if o["share_of_price"] <= NOT_WORTH_IT_ABOVE]
    too_dear = [o for o in offers if o["share_of_price"] > NOT_WORTH_IT_ABOVE]

    if not worth_it:
        return {
            "ok": True, "ours": True, "plans": [], "priced_out": too_dear,
            "trade": trade,
            "say": "Every plan we could sell on this comes to more than a "
                   "fifth of what they are paying, and the published advice "
                   "at that point is to decline. Tell them we do have cover "
                   "of our own, that on something at this price it is not "
                   "worth the money, and leave it there.",
            "sources": [MULBERRY, ONPOINT, ASURION],
        }

    cheapest = worth_it[0]
    return {
        "ok": True,
        "ours": True,
        "trade": trade,
        "starts": starts_when or "the day it is delivered",
        "plans": worth_it,
        "priced_out": too_dear,
        "sources": [MULBERRY, ONPOINT, ASURION],
        "say": (
            "This is OUR cover, not the maker's, and say so in those words: "
            "they are entitled to know who they would be claiming from. Offer "
            f"the {cheapest['tier']} at ${cheapest['price']:,.2f} for "
            f"{cheapest['years']} years first, say what it covers AND what it "
            "does not, and mention the accidental damage tier only if they "
            "ask what else there is. Do not press it twice."
        ),
    }
