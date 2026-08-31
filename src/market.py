"""What a machine actually sells for, from real listings.

WHY THIS EXISTS AND WHY THAT IS AN ADMISSION

The price list this desk quoted from was invented. `seed_product_stock` held a
dictionary of trade costs I chose, scaled by capacity and marked up by a
constant I also chose. Every number a customer heard was made up, presented
with the same confidence as the EnergyStar efficiency figures and the federal
recall data sitting next to it.

That is worse than having no prices. A desk that says "I will have to confirm"
is honest. A desk that says "five thousand five hundred and forty-four
dollars" in a firm voice, off a number nobody can source, is not.

And the capability was already here. `reviews.py` has been querying Google
Shopping through Serper since the reviews work, on the same key, to get
ratings. The listings it reads carry PRICES. So a real market price was one
field away the whole time.

WHAT A REAL PRICE LOOKS LIKE

A spread, not a number. The same True TUC-27F comes back at $3,037 from one
restaurant supplier and $3,978 from another for the SPEC3 variant. Quoting one
of those as "the price" is a choice dressed up as a fact, so this returns the
median with the range and the count behind it, and the desk says it as a
range.

THE NOISE IS THE HARD PART

That search also returns a $859 "USR Brands Coldline UC-27F" and a $1,190
"Webcoolers UC-27F". Neither is a True. A shopping search answers with
whatever is adjacent, and a confident price for the wrong freezer is the same
class of error as a confident rating for one, which is why `reviews.py`
already insists the make appears in the title. That discipline is reused here
rather than reinvented, because it was learned the hard way: "Continental"
once returned 64,376 reviews of car tyres.
"""

from __future__ import annotations

import json
import re
import statistics as st
from datetime import datetime, timedelta

from . import db
from .reviews import _fetch_shopping, _is_the_make, _searchable_model

# How long a price is worth keeping. Equipment pricing moves, but not hourly,
# and a call must not wait on a search we did this morning.
CACHE_DAYS = 7

# A miss expires sooner. A machine nobody listed today may well be listed
# next week, and a cached nothing that lasts a week is a machine we
# permanently cannot price.
MISS_DAYS = 2

# Below this many matching listings there is no market, only an anecdote. One
# supplier's number is a quote from that supplier, not what the thing costs.
ENOUGH_LISTINGS = 3

# Listings outside this band of the median are dropped before it is taken
# again. A commercial freezer listed at $89 is an accessory, a filter, or a
# scraped mistake, and it drags a median a long way.
SANE_LOW, SANE_HIGH = 0.35, 2.8

_PRICE = re.compile(r"[\d,]+\.?\d*")


def _money(text: str) -> float | None:
    hit = _PRICE.search(str(text or ""))
    if not hit:
        return None
    try:
        return float(hit.group().replace(",", ""))
    except ValueError:
        return None


def _cached(manufacturer: str, model: str) -> dict | None:
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT * FROM market_prices
                   WHERE manufacturer = ? AND model_number = ?""",
                (manufacturer, model)).fetchone()
    except Exception:
        return None
    if row is None:
        return None

    days = CACHE_DAYS if row["median_price"] else MISS_DAYS
    try:
        seen = datetime.fromisoformat(row["fetched_at"])
    except (TypeError, ValueError):
        return None
    if datetime.now() - seen > timedelta(days=days):
        return None
    return dict(row)


def _keep(manufacturer: str, model: str, priced: list[dict],
          median: float | None) -> None:
    try:
        with db.txn() as c:
            c.execute(
                """INSERT OR REPLACE INTO market_prices
                   (manufacturer, model_number, median_price, low_price,
                    high_price, listings, sources, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (manufacturer, model, median,
                 min((p["price"] for p in priced), default=None),
                 max((p["price"] for p in priced), default=None),
                 len(priced),
                 json.dumps([{"source": p["source"], "price": p["price"],
                              "title": p["title"][:120],
                              "image": (p.get("image") or "")[:400]}
                             for p in priced[:8]]),
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        print(f"[market] could not cache {manufacturer} {model}: "
              f"{type(e).__name__}: {e}", flush=True)


def _our_own_price(manufacturer: str, model: str) -> dict | None:
    """Our shelf price, if this is a thing we actually sell.

    THE MARKET IS NOT OUR PRICE LIST, AND IT GOT READ OUT AS ONE.

    On a live call somebody asked what an HP LaserJet costs. The desk called
    price_for, which searched the open market, swept in enterprise machines,
    and said:

        "Prices for an HP LaserJet can range from about 960 to over 5000."

    Two minutes later, from our own catalogue, the same call said:

        "an HP LaserJet M209dw for about $199"

    Twenty five times apart, in one conversation, about a printer sitting on
    our shelf. Whichever tool the model happened to reach for decided what the
    customer was quoted.

    A market range is the honest answer for something we do NOT hold, where
    the question is really "what would this cost to source". For something we
    stock, our price is not an estimate, it is the price, and there is nothing
    to research.
    """
    if not manufacturer:
        return None
    try:
        from . import db

        with db.connect() as c:
            row = c.execute(
                """SELECT dealer_id, manufacturer, model_number, family,
                          list_price, on_hand, on_order
                   FROM product_stock
                   WHERE list_price IS NOT NULL
                     AND LOWER(manufacturer) = LOWER(?)
                     AND (LOWER(model_number) = LOWER(?)
                          OR (? <> '' AND LOWER(model_number) LIKE
                              '%' || LOWER(?) || '%')
                          OR (? <> '' AND LOWER(?) LIKE
                              '%' || LOWER(model_number) || '%'))
                   ORDER BY on_hand DESC, list_price
                   LIMIT 1""",
                (manufacturer, model, model, model, model, model)).fetchone()
    except Exception as e:
        print(f"[market] could not check our own price list: "
              f"{type(e).__name__}: {e}", flush=True)
        return None

    if row is None:
        return None

    return {
        "ok": True,
        "ours": True,
        "manufacturer": row["manufacturer"],
        "model_number": row["model_number"],
        "family": row["family"],
        "price": row["list_price"],
        "on_hand": row["on_hand"],
        "on_order": row["on_order"],
        "say": (f"This is ours and the price is ours: "
                f"{row['manufacturer']} {row['model_number']} at "
                f"${row['list_price']:,.2f}. Quote that figure. Do NOT give a "
                "range and do NOT go looking at what other shops charge: for "
                "something on our own shelf there is nothing to research and "
                "a range invites them to haggle against a number we made up."),
    }


def _keep_the_quote(manufacturer: str, model: str, price, where_from: str) -> None:
    """Remember a figure we are about to say out loud, against this call.

    THE PRICE WAS SPOKEN AND THEN THROWN AWAY. `_price_the_line` re-derives it
    from whatever words the customer used to order the thing, and its last
    resort splits that phrase on spaces -- so "Razer Blade 18 laptop" was
    looked up as model "Blade 18 laptop" and came back with nothing, and the
    order was written at $0.00 half a minute after the desk had read the real
    price out. Keeping the make and model we were actually given means the
    order can use the number the customer heard.
    """
    try:
        from .quoted import we_said

        we_said(manufacturer, model, float(price or 0), where_from)
    except Exception:
        pass


def price_for(manufacturer: str, model_number: str = "") -> dict:
    """What this machine actually sells for.

    OUR price if we sell it, which is a fact. A range from real listings if we
    do not, which is research, and is returned as a range with the number of
    listings behind it rather than one figure dressed up as the price.

    Args:
        manufacturer: the make.
        model_number: the model, which is what makes the answer specific.
    """
    manufacturer = (manufacturer or "").strip()
    model = (model_number or "").strip()
    if not manufacturer:
        return {"ok": False, "why": "no make given"}

    mine = _our_own_price(manufacturer, model)
    if mine is not None:
        _keep_the_quote(manufacturer, model, mine.get("price"), "our price list")
        return mine

    hit = _cached(manufacturer, model)
    if hit is not None:
        if not hit["median_price"]:
            return {"ok": False, "from": "cache",
                    "why": "we looked and found no real listings for this one"}
        _keep_the_quote(manufacturer, model, hit["median_price"], "market median")
        return _answer(manufacturer, model, hit["median_price"],
                       hit["low_price"], hit["high_price"], hit["listings"],
                       json.loads(hit["sources"] or "[]"), "cache")

    # Our catalogue holds "TUC-27F-LP-HC~SPEC3"; the shops list it as
    # "TUC-27F-HC". Requiring the whole string found nothing at all for a
    # machine with thirty listings, so the model is tried at decreasing
    # precision and the answer says which one matched.
    #
    # It stops well short of the make alone. "True" on its own would price a
    # customer's undercounter freezer off a walk-in, which is the same failure
    # as quoting a rating for the wrong machine.
    priced, matched_on = [], ""
    raw = None
    for candidate in _model_attempts(model):
        query = f"{manufacturer} {candidate} commercial".strip()
        raw = _fetch_shopping(query)
        if raw is None:
            # Not cached. A search provider having a bad minute is not a fact
            # about the machine, and caching it would make this unpriceable.
            return {"ok": False, "why": "the price search did not answer"}

        priced = []
        for item in raw.get("shopping", []):
            title = item.get("title", "")
            amount = _money(item.get("price"))
            if amount is None or amount <= 0:
                continue
            if not _is_the_make(title, manufacturer):
                continue
            if candidate and _flat(candidate) not in _flat(title):
                continue
            # THE PICTURE WAS THERE AND WAS BEING THROWN AWAY. Serper returns
            # an imageUrl on every shopping result and this kept the price,
            # the title and the source and dropped it, so the console could
            # list 923 machines and show none of them.
            priced.append({"price": amount, "title": title,
                           "source": item.get("source", ""),
                           "image": item.get("imageUrl", "")})

        if len(priced) >= ENOUGH_LISTINGS:
            matched_on = candidate
            break

    if len(priced) < ENOUGH_LISTINGS:
        _keep(manufacturer, model, priced, None)
        return {
            "ok": False,
            "listings": len(priced),
            "why": (f"only {len(priced)} real listing(s) for this machine, "
                    "which is one supplier's quote rather than a market price"),
            "say": "Say we would have to price it up and come back, rather "
                   "than reading out a number from a single listing.",
        }

    # Drop the obvious nonsense, then take the median again. A commercial
    # freezer listed at eighty-nine dollars is an accessory or a scrape error.
    rough = st.median(p["price"] for p in priced)
    sane = [p for p in priced
            if SANE_LOW * rough <= p["price"] <= SANE_HIGH * rough]
    if len(sane) < ENOUGH_LISTINGS:
        sane = priced

    median = round(st.median(p["price"] for p in sane), 2)
    _keep(manufacturer, model, sane, median)
    _keep_the_quote(manufacturer, model, median, "market median")
    out = _answer(manufacturer, model, median,
                  min(p["price"] for p in sane),
                  max(p["price"] for p in sane), len(sane), sane, "live")
    if matched_on and _flat(matched_on) != _flat(_searchable_model(model)):
        out["matched_on"] = matched_on
        out["say"] += (f" These listings are for the {matched_on} rather than "
                       f"the exact variant on their machine, so say it is the "
                       "closest we can see and that trim can move it.")
    return out


def _model_attempts(model: str) -> list[str]:
    """The model at decreasing precision, most specific first.

    "TUC-27F-LP-HC~SPEC3" becomes TUC-27F-LP-HC, then TUC-27F-LP, then
    TUC-27F. It stops before the model gets short enough to match a different
    machine, which is why there is a length floor rather than a segment count.
    """
    base = _searchable_model(model)
    if not base:
        return [""]

    parts = [p for p in base.replace("_", "-").split("-") if p]
    out = []
    for keep in range(len(parts), 0, -1):
        candidate = "-".join(parts[:keep])
        if len(_flat(candidate)) >= 5 and candidate not in out:
            out.append(candidate)
    return out or [base]


def _flat(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _answer(make, model, median, low, high, n, sources, where) -> dict:
    return {
        "ok": True,
        "machine": f"{make} {model}".strip(),
        "median": round(float(median), 2),
        "low": round(float(low), 2),
        "high": round(float(high), 2),
        "listings": n,
        "from": where,
        "sellers": [s.get("source") for s in sources][:5],
        "say": (f"Give it as a range: {low:,.0f} to {high:,.0f}, typically "
                f"around {median:,.0f}. Say it is what the machine is selling "
                "for elsewhere right now across "
                f"{n} listings, not our own quote. If they want a firm number "
                "from us, say we will price it up and come back."),
    }


# What a customer would call the thing, and the words the shops use for it.
# Searched as a phrase because "freezer" alone returns domestic chest freezers
# and a restaurant is not buying one of those.
TRADE_TERMS = {
    "reach-in freezer": "commercial reach-in freezer",
    "reach-in cooler": "commercial reach-in refrigerator",
    "display cooler": "commercial glass door merchandiser refrigerator",
    "walk-in cooler": "walk-in cooler",
    "walk-in freezer": "walk-in freezer",
    "ice machine": "commercial ice machine",
    "blast chiller": "commercial blast chiller",
    "undercounter freezer": "commercial undercounter freezer",
}

# A listing this far under the rest is not the machine. Searching for a
# commercial freezer returns door gaskets, shelf kits and thermometers, all
# of them cheap and all of them matching the words.
NOT_A_MACHINE_UNDER = 400.0

# Words that mean the listing is a DOMESTIC appliance, not trade equipment.
#
# On a live call a restaurant with a six hundred dollar budget was offered a
# Danby 8.5 cubic foot upright and a Summit compact, which are household
# freezers. They matched "freezer" and they were under the money, and neither
# belongs in a commercial kitchen: no NSF certification, domestic duty cycle,
# and a warranty that is void the moment it is used in a business.
#
# Offering one is worse than saying there is nothing at that price, because
# the customer might buy it.
DOMESTIC = ("compact", "mini fridge", "mini-fridge", "dorm", "chest freezer",
            "upright freezer", "apartment", "household", "residential",
            "garage ready", "beverage fridge", "wine cooler", "countertop")

# And the words that mean it IS trade equipment. A listing has to look like
# one of these, not merely avoid looking domestic, because most listings say
# nothing either way.
COMMERCIAL = ("commercial", "reach-in", "reach in", "undercounter",
              "merchandiser", "nsf", "restaurant", "foodservice",
              "food service", "prep table", "walk-in", "walk in",
              "back bar", "worktop")


# THE SCREEN ABOVE IS REFRIGERATION'S, AND IT WAS THE ONLY ONE THERE WAS.
#
# Every family went through it. So a laptop was searched for as "commercial
# laptop", and then every listing that came back was thrown away for not
# containing one of "reach-in", "nsf", "prep table" or "walk-in". No laptop
# listing on earth contains those words, so the market search could not
# return a laptop even in principle, and the desk told a customer with two
# thousand dollars that nothing existed.
#
# The screen itself is right, and it is right for a reason that does not
# generalise: a household freezer in a restaurant has no NSF rating, a
# domestic duty cycle, and a warranty void on business use, so offering one is
# worse than offering nothing. The equivalent distinction in IT is real but
# different, and it is already in this database: warranty_terms holds three
# years for a ThinkPad and one for an IdeaPad, which is the same consumer
# versus business split by another name.
#
# So each trade brings its own words, and refrigeration's behaviour is
# unchanged.

TRADE_SCREEN = {
    "refrigeration": {
        "prefix": "commercial",
        "floor": NOT_A_MACHINE_UNDER,
        "reject": DOMESTIC,
        "require": COMMERCIAL,
    },
    "furniture": {
        "prefix": "commercial office",
        # Below this it is a cushion, a caster pack or a set of glides.
        "floor": 120.0,
        "reject": ("caster", "casters", "cushion", "cover", "mat", "glide",
                   "armrest pad", "cylinder", "replacement parts", "set of 4",
                   "pack of", "for parts"),
        # A contract furnishings dealer sells the commercial line. The words
        # below are what the trade's own listings say.
        "require": ("office", "desk", "chair", "task", "ergonomic",
                    "conference", "boardroom", "filing", "cabinet",
                    "bookcase", "shelving", "workstation", "executive",
                    "commercial", "contract"),
    },
    "av": {
        "prefix": "commercial",
        "floor": 150.0,
        # A lamp, a mount or a cable is not a display. The lamp exclusion
        # matters twice over: it is also the trade's wear item.
        "reject": ("mount", "bracket", "cable", "remote", "lamp", "bulb",
                   "stand", "wall plate", "adapter", "for parts",
                   "replacement lamp"),
        "require": ("display", "monitor", "television", "tv", "projector",
                    "signage", "screen", "soundbar", "speaker", "audio",
                    "commercial", "professional", "4k", "uhd"),
    },
    "it": {
        "prefix": "business",
        # Accessories, not machines: sleeves, docks, chargers, memory.
        "floor": 150.0,
        "reject": ("case", "sleeve", "bag", "charger", "adapter", "dock",
                   "docking", "stand", "skin", "cover", "screen protector",
                   "keyboard", "mouse", "cable", "ram only", "ssd only",
                   "refurbished parts", "for parts", "battery"),
        # Business lines, by the names the trade actually uses. A listing has
        # to look like one of these rather than merely avoid looking domestic,
        # for the same reason it does in refrigeration: most say nothing.
        "require": ("thinkpad", "latitude", "elitebook", "probook", "vostro",
                    "expertbook", "travelmate", "toughbook", "precision",
                    "zbook", "thinkcentre", "optiplex", "business", "pro",
                    "workstation", "laptop", "notebook", "desktop", "server",
                    "printer", "ups", "monitor"),
    },
}


def _trade_for(family: str) -> str:
    """Which trade a family belongs to, from the only place that records it."""
    fam = (family or "").strip().lower()
    if not fam:
        return "refrigeration"
    try:
        from . import db

        with db.connect() as c:
            rows = c.execute("SELECT trade, families FROM dealers "
                             "WHERE families IS NOT NULL").fetchall()
        for r in rows:
            for f in (r["families"] or "").split(","):
                if f.strip().lower() == fam:
                    return (r["trade"] or "refrigeration").lower()
    except Exception as e:
        print(f"[market] could not tell which trade {family!r} is: "
              f"{type(e).__name__}: {e}", flush=True)
    return "refrigeration"


def alternatives(family: str, budget: float, limit: int = 5,
                 segment: str = "") -> dict:
    """What can actually be bought for that money, from real listings.

    THE QUESTION THIS ANSWERS. A customer said five and a half thousand was
    too much and asked for something cheaper. Everything the desk could reach
    was our own price list, and our own price list had nothing under it, so
    the honest answer was "no" four times over.

    A real dealer does not stop there. They know what is on the market at that
    money and either source it or tell you it does not exist. This looks at
    what is genuinely listed, which is the difference between "we do not have
    one" and "there is not one".

    These are OTHER people's listings and must be said as such. We are not
    quoting them, we are telling somebody what the market looks like and
    offering to source it.

    Args:
        family: reach-in freezer, ice machine, display cooler.
        budget: the most they want to spend.
        limit: how many to bring back.
        segment: optional override for the words prepended to the search, so
            a catalogue build can sweep "budget", "business" and "gaming"
            rather than seeing one slice of a trade. Left empty on a live
            call, where the trade's own default is what a caller means.
    """
    if budget <= 0:
        return {"ok": False, "why": "no budget given"}

    trade = _trade_for(family)
    screen = TRADE_SCREEN.get(trade, TRADE_SCREEN["refrigeration"])

    if segment:
        term = f"{segment} {family}".strip()
    else:
        term = TRADE_TERMS.get((family or "").strip().lower()) or (
            f"{screen['prefix']} {family}".strip() or "commercial refrigeration")

    raw = _fetch_shopping(f"{term} under ${int(budget)}")
    if raw is None:
        return {"ok": False, "why": "the search did not answer"}

    seen, found, domestic = set(), [], 0
    for item in raw.get("shopping", []):
        amount = _money(item.get("price"))
        title = (item.get("title") or "").strip()
        if amount is None or amount > budget or amount < screen["floor"]:
            continue

        low = title.lower()
        if (any(w in low for w in screen["reject"])
                or not any(w in low for w in screen["require"])):
            # A household freezer in a commercial kitchen has no NSF rating,
            # a domestic duty cycle, and a warranty void on business use.
            domestic += 1
            continue

        key = _flat(title)[:40]
        if key in seen:
            continue
        seen.add(key)
        found.append({"title": title[:110], "price": amount,
                      "source": item.get("source", "")})

    found.sort(key=lambda x: -x["price"])     # best they can get for the money

    if not found:
        return {
            "ok": True, "budget": budget, "found": [],
            "domestic_skipped": domestic,
            "say": (f"No COMMERCIAL {family} on the open market at "
                    f"${budget:,.0f} either, not just nothing of ours. Say "
                    "that plainly: it tells them the budget is the problem "
                    "rather than the supplier."
                    + (f" There are {domestic} household machines at that "
                       "money and they are not an option for a kitchen: no "
                       "NSF rating, a domestic duty cycle, and the warranty "
                       "is void the moment it is used in a business. Say that "
                       "if they push, but do not offer one."
                       if domestic else "")),
        }

    return {
        "ok": True,
        "budget": budget,
        "found": found[:limit],
        "say": ("These are OTHER suppliers' listings, not our stock and not "
                "our quote. Say so in those words. Give two of them with the "
                "price and who is listing them, then offer to source one: "
                "that is a real answer to somebody with a budget, and it is "
                "what a person on this desk would do. Never imply we have one "
                "on the floor."),
    }
