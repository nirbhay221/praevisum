"""What the rest of the world says, kept strictly apart from what we know.

WHY THIS IS NEEDED AT ALL

The buying advice is built on this dealer's own service record, which is real
evidence nobody else has. It also covers 37 models out of a catalogue of
32,767. Ask about almost any machine and the honest answer is "we have never
supplied one", which is true and useless on most calls.

WHY IT IS NEVER BLENDED

The temptation is to average a star rating into our own fault rate and produce
one number. That would destroy the only defensible thing here. Every figure
this desk gives can be justified out loud: nine problems across six machines in
service, one of them unusable. A customer can argue with that. A blended 7.4
out of 10 is unarguable and therefore worthless.

They also answer different questions:

    our record   will it break, and what happens to you when it does
    reviews      is it pleasant to own

For a restaurant buying a freezer the first matters more. For an office buying
laptops the second does. Averaging them destroys that difference.

So they stay separate, and the most valuable output is when they DISAGREE:

    "It reviews well, 4.6 stars. I'll be straight with you though, we have
     replaced the control board on four of the nine we installed."

No review site and no manufacturer can produce that sentence.

WHY GOOGLE SHOPPING, AND WHY NOT THE OBVIOUS ONES

Every retailer API that returns a star rating is gated:

    Best Buy       will not issue a key to a free email address
    Trustpilot     API is bundled with Premium and Enterprise only
    Amazon PA-API  requires three qualifying affiliate sales first
    eBay           requires an account this dealer does not have
    Rainforest     $66/month floor

Google Shopping is reached through a SERP provider on a free tier that needs
only an email address. It is also the better source on the merits, for one
reason that matters here: it aggregates across retailers rather than reporting
one shop's own shoppers. That is the difference between a rating for a machine
and a rating for a storefront, and it is the only free source that covers the
restaurant suppliers where commercial equipment is actually listed.

THE FALSE ATTRIBUTION GUARD

A shopping search will happily return something adjacent. Quoting a rating for
a machine that is not the one they asked about is exactly the false precision
this project refuses everywhere else, so a listing is discarded unless the make
and, at model level, the model itself appear in the returned title. The guard
throws away a lot. That is correct: an honest "nobody reviews this" beats a
confident number about the wrong freezer.

BRAND LEVEL IS LABELLED AS BRAND LEVEL

Federal model numbers arrive masked (HRP2HC***S********), and the masked part
is unsearchable. When only the prefix survives, or nothing matches at model
level, the answer falls back to the make and says so. "Beverage-Air rates 4.1
across eleven products" is a real fact and a different fact from a rating for
their machine, so it is returned with `level` set and a different instruction
to the agent.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import db
from .config import settings  # noqa: F401  (loads .env, so a key there is seen)

BESTBUY_API = "https://api.bestbuy.com/v1/products"
SERPER_API = "https://google.serper.dev/shopping"

# Ratings move slowly and a phone call should never wait on somebody else's
# API. A month-old rating is as good as a fresh one and costs nothing.
CACHE_DAYS = 30

# A "nothing found" expires far sooner than a rating does.
#
# Google Shopping does not return a stable result set: consecutive identical
# queries for Comfee came back with 382 reviews across sixteen products, then
# with nothing at all. Caching a positive for a month is safe because ratings
# move slowly. Caching a MISS for a month lets one unlucky call silence a make
# that is reviewed perfectly well, and the desk would keep saying nobody
# reviews it for four weeks.
NEGATIVE_CACHE_DAYS = 3

# Below this, a star rating is as thin as a two-unit service record, and this
# module exists partly to stop us dressing up small samples.
MIN_REVIEWS = 5

# How many separately reviewed products a brand average has to rest on.
#
# Checking the total was not enough and the first live run proved it: fifteen
# Beverage-Air listings carrying one to seven reviews each summed past the bar
# and came back as a confident 4.42. Commercial refrigeration genuinely is not
# reviewed, and the correct answer there is nothing at all.
MIN_BRAND_PRODUCTS = 3

# And how many reviews the brand average rests on in total.
#
# A brand average is a WEAKER claim than a rating for the machine itself, so
# it has to earn a stronger sample to be worth making at all. Without this the
# answer flickered: the same Beverage-Air query refused twice and returned
# 4.67 from eighteen reviews once, depending on which listings came back.
#
# The gap it separates is not marginal. The makes that are genuinely reviewed
# come back with thousands (ASUS 12,098, AORUS 1,626, Comfee 371). The ones
# that are not come back with eighteen.
MIN_BRAND_REVIEWS = 100

# A masked federal model number is unsearchable past the wildcard, and what
# survives has to be long enough to identify something. "HR" would match half
# the catalogue.
MIN_MODEL_CHARS = 3

# Positions the federal catalogue masks. Not one marker but three.
WILDCARDS = "*~[#"

# A model level rating below this is worth saying, because it is about THEIR
# machine, but it must never be said without its sample size attached. Six
# reviews averaging 3.7 is a real observation and is not a finding.
THIN_SAMPLE = 25


class _Unreachable(Exception):
    """The provider did not answer.

    Kept distinct from the provider answering with nothing, because the two
    produce opposite sentences. "Nobody reviews this machine" is a claim about
    the world and saying it after a timeout is simply false. A live run said
    exactly that about ASUS, which has twelve thousand reviews.
    """


def _norm(s: str) -> str:
    """Lowercase, alphanumerics only, so punctuation cannot break a match.

    Beverage-Air, Beverage Air and BEVERAGEAIR are one make, and a listing
    title written by a restaurant supplier will use whichever it likes.
    """
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


# Words a catalogue puts in a legal entity name and a shop never puts in a
# listing title. The federal data says "True Refrigeration" and every listing
# on the market says "True T-23-HC" or "True Mfg.", so an exact match on the
# full name threw away all 206 reviews the make actually has.
LEGAL_SUFFIXES = {
    "refrigeration", "mfg", "manufacturing", "inc", "corp", "corporation",
    "company", "co", "spa", "group", "ltd", "llc", "industries", "systems",
    "equipment", "products", "international",
}


def _make_tokens(manufacturer: str) -> list[str]:
    """The words of a make that a listing title would actually carry."""
    words = [w for w in "".join(
        ch if ch.isalnum() else " " for ch in (manufacturer or "").lower()
    ).split() if w]
    kept = [w for w in words if w not in LEGAL_SUFFIXES]
    return kept or words


def _is_the_make(title: str, manufacturer: str) -> bool:
    """Does this listing belong to the make, without matching a lookalike?

    Two ways in, because the two failure modes pull in opposite directions.

    Whole-name containment on the normalised strings handles punctuation drift:
    Beverage-Air, Beverage Air and BEVERAGEAIR are one make and a restaurant
    supplier will use whichever it likes.

    Token matching handles the legal-suffix problem, and is deliberately done
    on WORD BOUNDARIES rather than by containment. "True" as a substring would
    match TrueTone; "true" as a word in "True T-23-HC" matches only the make.
    """
    if not manufacturer:
        return True
    if _norm(manufacturer) and _norm(manufacturer) in _norm(title):
        return True

    words = set("".join(
        ch if ch.isalnum() else " " for ch in (title or "").lower()).split())
    return all(w in words for w in _make_tokens(manufacturer))


def _searchable_model(model: str) -> str:
    """The part of a model number that can actually be looked up.

    The federal catalogue masks variant positions, so HRP2HC***S********
    carries six usable characters and thirteen that match nothing anywhere.
    Everything from the first wildcard on is dropped.

    It does not use one marker consistently: the asterisk dominates, but `~`
    and `[#]` both appear too, and MT34-1[#] only matched by luck of the
    normaliser stripping brackets. Hyphens, spaces and dots stay, because
    those are real characters in a real model number.
    """
    head = model or ""
    for mark in WILDCARDS:
        head = head.split(mark)[0]
    head = head.strip(" -_.")
    return head if len(_norm(head)) >= MIN_MODEL_CHARS else ""


def configured() -> bool:
    return bool(os.getenv("SERPER_API_KEY") or os.getenv("BESTBUY_API_KEY"))


def provider() -> str:
    """Which source is live. Google Shopping wins when both are configured.

    Not a preference for the vendor: it aggregates across retailers, and it is
    the only one of the two that covers commercial equipment at all.
    """
    if os.getenv("SERPER_API_KEY"):
        return "google_shopping"
    if os.getenv("BESTBUY_API_KEY"):
        return "bestbuy"
    return ""


def _cached(manufacturer: str, model: str, source: str):
    """The stored answer, if it is still worth trusting.

    Freshness depends on what was stored. A rating holds for a month because
    ratings move slowly. A miss holds for days, because the provider's result
    set is not stable and a miss is as likely to be an unlucky page of results
    as a fact about the machine.
    """
    with db.connect() as c:
        row = c.execute(
            """SELECT rating, review_count, matched_name, fetched_at, raw
               FROM outside_reviews
               WHERE manufacturer=? AND model_number=? AND source=?""",
            (manufacturer, model, source)).fetchone()
    if row is None:
        return None

    days = CACHE_DAYS if row["rating"] else NEGATIVE_CACHE_DAYS
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return row if row["fetched_at"] >= cutoff else None


def _store(manufacturer: str, model: str, source: str, rating, count, name, raw):
    with db.txn() as c:
        c.execute(
            """INSERT INTO outside_reviews
               (manufacturer,model_number,source,rating,review_count,
                matched_name,fetched_at,raw)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(manufacturer,model_number,source) DO UPDATE SET
                 rating=excluded.rating, review_count=excluded.review_count,
                 matched_name=excluded.matched_name,
                 fetched_at=excluded.fetched_at, raw=excluded.raw""",
            (manufacturer, model, source, rating, count, name,
             datetime.now().isoformat(timespec="seconds"),
             json.dumps(raw)[:4000] if raw else None))


def _fetch(manufacturer: str, model: str) -> dict | None:
    """One search against Best Buy. Returns None rather than raising.

    A phone call must not fail because somebody else's API is slow, so this
    has a short timeout and swallows everything.
    """
    terms = [f"manufacturer={manufacturer}"]
    if model:
        # Their search is fussy about punctuation in model numbers, and a
        # commercial model like "HRP2HC***S********" matches nothing at all.
        clean = "".join(ch for ch in model if ch.isalnum() or ch in " -")[:40]
        if clean.strip():
            terms.append(f"model={clean.strip()}*")

    url = (f"{BESTBUY_API}(({'&'.join(terms)}))?format=json"
           f"&show=sku,name,manufacturer,modelNumber,"
           f"customerReviewAverage,customerReviewCount"
           f"&pageSize=5&apiKey={urllib.parse.quote(os.environ['BESTBUY_API_KEY'])}")
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_shopping(query: str) -> dict | None:
    """One Google Shopping search. Returns None rather than raising.

    Same contract as the Best Buy fetch: short timeout, swallows everything,
    because a customer's call must never end because a search provider did.
    """
    body = json.dumps({"q": query, "num": 20}).encode("utf-8")
    req = urllib.request.Request(
        SERPER_API, data=body,
        headers={"X-API-KEY": os.environ["SERPER_API_KEY"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _rated(results: list, manufacturer: str, model: str) -> list[dict]:
    """Listings that carry a rating AND are demonstrably the right machine.

    The make must appear in the title, and when a model was asked for it must
    appear too. A shopping search returns adjacent products cheerfully and
    there is no worse failure here than a confident rating for the wrong
    freezer.
    """
    want = _norm(model)
    out = []
    for p in results or []:
        title = p.get("title") or ""
        if not _is_the_make(title, manufacturer):
            continue
        if want and want not in _norm(title):
            continue
        rating, count = p.get("rating"), p.get("ratingCount")
        if not rating or not count:
            continue
        out.append({"title": p.get("title"), "rating": float(rating),
                    "count": int(count), "source": p.get("source")})
    return out


def _consolidate(listings: list[dict], level: str) -> tuple:
    """One rating out of many listings, without inventing a sample size.

    At MODEL level the listings are the same product resold by different
    shops, so their review counts are the same reviews counted repeatedly.
    Summing them would claim a sample nine times bigger than it is. The
    largest single listing is taken instead.

    At BRAND level the listings are genuinely different products, so a
    count-weighted mean across them is the real figure and the counts do add.
    """
    seen, distinct = set(), []
    for x in listings:
        key = (round(x["rating"], 2), x["count"])
        if key in seen:
            continue
        seen.add(key)
        distinct.append(x)

    if not distinct:
        return None, 0, None, 0

    if level == "model":
        best = max(distinct, key=lambda x: x["count"])
        return best["rating"], best["count"], best["title"], len(listings)

    # Brand level aggregates DIFFERENT products, so a thin listing is not
    # diluted by a fat one the way duplicates of the same product are. It just
    # contributes noise and an unearned decimal place. Only products that
    # individually clear the sample bar count, and a brand average has to rest
    # on several of them or it is one product wearing a make's name.
    solid = [x for x in distinct if x["count"] >= MIN_REVIEWS]
    if len(solid) < MIN_BRAND_PRODUCTS:
        return None, 0, None, len(solid)

    total = sum(x["count"] for x in solid)
    mean = sum(x["rating"] * x["count"] for x in solid) / total
    return round(mean, 2), total, None, len(solid)


def _family_of(manufacturer: str, model: str) -> str:
    """What kind of machine this make sells us, out of our own book.

    Asked of our own data rather than of the caller, because the system
    already knows and a tool argument the model fills in is a tool argument
    the model can leave out.
    """
    with db.connect() as c:
        row = c.execute(
            """SELECT family FROM assets
               WHERE manufacturer = ? AND (? = '' OR model_number = ?)
               ORDER BY CASE WHEN model_number = ? THEN 0 ELSE 1 END
               LIMIT 1""",
            (manufacturer, model, model, model)).fetchone()
        if row is None:
            row = c.execute(
                "SELECT family FROM assets WHERE manufacturer = ? LIMIT 1",
                (manufacturer,)).fetchone()
    return (row["family"] if row else "") or ""


def _lookup_shopping(manufacturer: str, model: str, family: str) -> tuple:
    """Model level first, then the make within its category.

    Two searches at most, and the second only runs when the first found
    nothing, so an answerable question costs one credit.
    """
    searchable = _searchable_model(model)

    if searchable:
        data = _fetch_shopping(f"{manufacturer} {searchable}")
        if data is None:
            raise _Unreachable()
        hits = _rated(data.get("shopping") or [], manufacturer, searchable)
        rating, count, name, n = _consolidate(hits, "model")
        if rating and count >= MIN_REVIEWS:
            return rating, count, name, "model", n

    # A brand average without the category is not a brand average. Searched on
    # its own, "Continental" returned 64,376 reviews of car and bicycle tyres
    # and offered them as the rating for a commercial freezer maker. Plenty of
    # makes share a word with a bigger industry, so the category is required
    # rather than merely helpful, and no category means no brand answer.
    if not family:
        return None, 0, None, "", 0

    data = _fetch_shopping(f"{manufacturer} {family}")
    if data is None:
        raise _Unreachable()
    hits = _rated(data.get("shopping") or [], manufacturer, "")
    rating, count, _, n = _consolidate(hits, "brand")
    if rating and count >= MIN_BRAND_REVIEWS:
        return rating, count, f"{n} products", "brand", n

    return None, 0, None, "", 0


def outside_opinion(manufacturer: str, model_number: str = "",
                    family: str = "") -> dict:
    """What the wider market says about a machine, reported on its own.

    Deliberately returns nothing useful rather than something vague. No key, no
    match, or too few reviews all produce an honest empty answer, because a
    made-up second opinion is worse than one signal stated clearly.

    Args:
        manufacturer: the make.
        model_number: the model, if they have it.
        family: what kind of machine it is, such as reach-in freezer. Leave it
            blank and it is read from our own book, which is usually better
            than guessing at it.
    """
    manufacturer = (manufacturer or "").strip()
    model_number = (model_number or "").strip()
    family = (family or "").strip()
    if not manufacturer:
        return {"available": False, "why": "no manufacturer given"}
    if not family:
        family = _family_of(manufacturer, model_number)

    src = provider()
    if not src:
        return {"available": False,
                "why": "no outside review source is configured",
                "say": "Do not mention reviews. Answer from our own record."}

    hit = _cached(manufacturer, model_number, src)
    level = "model" if _searchable_model(model_number) else "brand"

    if hit is None:
        # Guarded here as well as inside each fetch. Two layers because this
        # is the only code path in the project that depends on somebody else's
        # server, and a customer's call must not end because their API did.
        try:
            if src == "google_shopping":
                rating, count, name, level, n = _lookup_shopping(
                    manufacturer, model_number, family)
                _store(manufacturer, model_number, src, rating, count, name,
                       {"level": level, "listings": n})
            else:
                data = _fetch(manufacturer, model_number)
                products = (data or {}).get("products") or []
                rated = [p for p in products
                         if p.get("customerReviewCount")
                         and p.get("customerReviewAverage")]
                if rated:
                    best = max(rated, key=lambda p: p["customerReviewCount"])
                    _store(manufacturer, model_number, src,
                           best["customerReviewAverage"],
                           best["customerReviewCount"], best.get("name"), best)
                else:
                    _store(manufacturer, model_number, src, None, 0, None, None)
        except Exception:
            return {"available": False,
                    "why": "the review source could not be reached",
                    "say": "Do not mention reviews. Answer from our own record."}
        hit = _cached(manufacturer, model_number, src)

    if hit is None or not hit["rating"]:
        return {"available": False,
                "manufacturer": manufacturer, "model": model_number,
                "why": "nothing found for that machine",
                "say": "Say plainly that this is not a machine consumers review, "
                       "and that our own service record is the only evidence "
                       "there is on it."}

    if (hit["review_count"] or 0) < MIN_REVIEWS:
        return {"available": False,
                "why": f"only {hit['review_count']} reviews, too few to quote",
                "say": "Too thin to be worth repeating. Use our own record."}

    try:
        level = (json.loads(hit["raw"] or "{}") or {}).get("level") or level
    except Exception:
        pass

    never_blend = ("This is what the market says, and it is a SEPARATE fact "
                   "from what we have seen. Never average the two into one "
                   "score. If they disagree, say both out loud: that sentence "
                   "is worth more than either number on its own.")

    if level == "model" and (hit["review_count"] or 0) < THIN_SAMPLE:
        never_blend = (f"Only {hit['review_count']} people reviewed this "
                       "machine. Say the number and the sample size in the "
                       "same breath, never the rating alone. " + never_blend)

    return {
        "available": True,
        "source": "Google Shopping" if src == "google_shopping" else "Best Buy",
        "level": level,
        "manufacturer": manufacturer, "model": model_number,
        "matched": hit["matched_name"],
        "rating": hit["rating"],
        "reviews": hit["review_count"],
        "as_of": hit["fetched_at"][:10],
        "say": never_blend if level == "model" else (
            "This is the make's rating ACROSS ITS RANGE, not this machine. Say "
            "so in those words: a good brand average is not evidence about the "
            "unit in front of them. " + never_blend),
    }
