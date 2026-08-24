"""Outside opinion, kept strictly apart from what we know.

The temptation this file exists to resist is averaging a star rating into our
own fault rate to produce one number. That would destroy the only defensible
thing here: every figure this desk gives can be justified out loud, and a
blended score cannot.

They answer different questions anyway. Our record says whether it will break.
Reviews say whether it is pleasant to own. The most valuable output is when
they disagree, and that only exists if they stay separate.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """A key in the developer's own environment must not steer a test.

    Both providers read straight from os.environ, so without this a machine
    with SERPER_API_KEY exported would silently run the Best Buy tests down
    the Google Shopping path and pass for the wrong reason.
    """
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)


def test_no_key_means_no_reviews_and_no_guessing(dbfile, monkeypatch):
    from src import reviews

    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    r = reviews.outside_opinion("Lenovo", "21SX")

    assert r["available"] is False
    assert "Do not mention reviews" in r["say"]


def test_a_network_failure_never_raises(dbfile, monkeypatch):
    """A phone call must not fail because somebody else's API is slow."""
    from src import reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        r = reviews.outside_opinion("Lenovo", "21SX")
    except Exception:
        pytest.fail("an outside API took the call down with it")
    assert r["available"] is False


def test_a_real_rating_is_reported_with_its_sample(dbfile, monkeypatch):
    from src import reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch", lambda *a: {"products": [
        {"name": "Lenovo ThinkPad", "customerReviewAverage": 4.6,
         "customerReviewCount": 812}]})

    r = reviews.outside_opinion("Lenovo", "21SX")
    assert r["available"] is True
    assert r["rating"] == 4.6
    assert r["reviews"] == 812
    assert r["source"] == "Best Buy"


def test_a_thin_sample_is_refused(dbfile, monkeypatch):
    """Three reviews is as thin as a two-unit service record."""
    from src import reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch", lambda *a: {"products": [
        {"name": "x", "customerReviewAverage": 5.0, "customerReviewCount": 3}]})

    r = reviews.outside_opinion("Lenovo", "21SX")
    assert r["available"] is False
    assert "too few" in r["why"]


def test_a_machine_nobody_reviews_says_so(dbfile, monkeypatch):
    """Commercial refrigeration. Nothing free covers it, and that is honest."""
    from src import reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch", lambda *a: {"products": []})

    r = reviews.outside_opinion("Beverage-Air", "HRP2HC")
    assert r["available"] is False
    assert "not a machine consumers review" in r["say"]


def test_the_result_is_cached(dbfile, monkeypatch):
    """Ratings move slowly and a call must not wait on an API twice."""
    from src import reviews

    calls = []
    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch", lambda *a: (calls.append(a), {
        "products": [{"name": "x", "customerReviewAverage": 4.4,
                      "customerReviewCount": 200}]})[1])

    reviews.outside_opinion("Lenovo", "21SX")
    reviews.outside_opinion("Lenovo", "21SX")
    assert len(calls) == 1


def test_reviews_are_never_merged_into_our_own_numbers(dbfile, monkeypatch):
    """The whole point. Two separate facts, never one score."""
    from src import ops, reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch", lambda *a: {"products": [
        {"name": "x", "customerReviewAverage": 4.9, "customerReviewCount": 900}]})
    reviews.outside_opinion("Traulsen", "G12010")

    ours = ops.what_we_know_about("Traulsen", "G12010")
    for field in ("rating", "stars", "review", "score"):
        assert not any(field in str(k).lower() for k in ours), \
            f"an outside rating leaked into our own record as {field}"


def test_the_guidance_forbids_averaging(dbfile, monkeypatch):
    from src import reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch", lambda *a: {"products": [
        {"name": "x", "customerReviewAverage": 4.6, "customerReviewCount": 812}]})

    say = reviews.outside_opinion("Lenovo", "21SX")["say"]
    assert "Never average" in say
    assert "disagree" in say


def test_only_the_advice_agent_can_reach_outside(dbfile):
    """The phone agent answers from our own record, not the internet."""
    from src import agents

    phone = {getattr(t, "__name__", getattr(t, "name", ""))
             for t in agents.front_agent.tools}
    advice = {getattr(t, "__name__", getattr(t, "name", ""))
              for t in agents.advice_agent.tools}

    assert "outside_opinion" in advice
    assert "outside_opinion" not in phone


# Google Shopping. Reached through a SERP provider because every retailer API
# that returns a star rating is gated behind a paid plan, a custom domain, or
# an affiliate account this dealer does not have.


def _shopping(monkeypatch, listings):
    from src import reviews
    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping",
                        lambda q: {"shopping": listings})
    return reviews


def test_a_rating_for_the_wrong_machine_is_thrown_away(dbfile, monkeypatch):
    """The worst failure available here, so it is guarded hardest.

    A shopping search returns adjacent products cheerfully. Quoting a Turbo
    Air rating for a Beverage-Air is a confident number about a machine the
    caller did not ask about.
    """
    reviews = _shopping(monkeypatch, [
        {"title": "Turbo Air M3R24-1-N Reach-In Refrigerator",
         "rating": 4.8, "ratingCount": 300}])

    r = reviews.outside_opinion("Beverage-Air", "HRP2HC")
    assert r["available"] is False
    assert "4.8" not in str(r)


def test_an_outage_is_not_reported_as_nobody_reviews_this(dbfile, monkeypatch):
    """The two answers are opposite and the live run confused them.

    A timeout came back as "this is not a machine consumers review" about
    ASUS, which has twelve thousand of them. Failing to reach the provider is
    a fact about our network. Nobody reviewing a machine is a claim about the
    world, and it must not be made on a timeout.
    """
    from src import reviews

    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping", lambda q: None)

    r = reviews.outside_opinion("ASUS", "M1505Y")
    assert r["available"] is False
    assert "could not be reached" in r["why"]
    assert "not a machine consumers review" not in r["say"]


def test_an_outage_is_not_cached_as_an_answer(dbfile, monkeypatch):
    """Otherwise one timeout silences a make for a month."""
    from src import db, reviews

    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping", lambda q: None)
    reviews.outside_opinion("ASUS", "M1505Y")

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM outside_reviews").fetchone()[0]
    assert n == 0, "a failed lookup was stored as though it were an answer"


def test_the_same_product_in_nine_shops_is_not_nine_samples(dbfile, monkeypatch):
    """Google lists one machine per retailer. The reviews are the same reviews.

    Summing them would claim a sample nine times larger than it is, which is
    the exact dressing-up of a thin number this module exists to prevent.
    """
    reviews = _shopping(monkeypatch, [
        {"title": f"Lenovo ThinkPad 21SX Laptop ({shop})",
         "rating": 4.6, "ratingCount": 812}
        for shop in ("Newegg", "B&H", "CDW", "Insight")])

    r = reviews.outside_opinion("Lenovo", "21SX")
    assert r["available"] is True
    assert r["reviews"] == 812, "duplicate listings were counted as new reviews"
    assert r["level"] == "model"


def test_a_masked_model_number_falls_back_to_the_make_and_says_so(dbfile, monkeypatch):
    """HRP2HC***S******** is unsearchable past the wildcard.

    The make is still answerable, but a brand average is a different fact from
    a rating for their machine and has to be labelled as one.

    The review counts here are synthetic, to isolate the fallback mechanic.
    The real Beverage-Air is refused on sample size by the test below.
    """
    reviews = _shopping(monkeypatch, [
        {"title": "Beverage-Air MMR23HC-1-B Cooler", "rating": 4.0,
         "ratingCount": 100},
        {"title": "Beverage Air UCR27AHC Undercounter", "rating": 4.4,
         "ratingCount": 300},
        {"title": "Beverage-Air WTR27A Worktop", "rating": 4.8,
         "ratingCount": 100}])

    r = reviews.outside_opinion("Beverage-Air", "***S********",
                                family="reach-in cooler")
    assert r["available"] is True
    assert r["level"] == "brand"
    assert r["rating"] == 4.4, "brand level should be weighted by review count"
    assert r["reviews"] == 500
    assert "ACROSS ITS RANGE" in r["say"]
    assert "Never average" in r["say"]


def test_a_brand_average_built_from_thin_listings_is_refused(dbfile, monkeypatch):
    """The defect the first live run exposed.

    Fifteen real Beverage-Air listings carrying one to seven reviews each
    summed past a total-only threshold and came back as a confident 4.42.
    Checking the total is not enough: a brand average has to rest on several
    products that are each individually worth quoting.
    """
    reviews = _shopping(monkeypatch, [
        {"title": "Beverage-Air 48in Dual Tap Kegerator", "rating": 4.8,
         "ratingCount": 7},
        {"title": "Beverage Air WTR48A Worktop", "rating": 4.0,
         "ratingCount": 4},
        {"title": "Beverage-Air UCR48A Commercial", "rating": 5.0,
         "ratingCount": 3},
        {"title": "Beverage Air WTR27A Worktop", "rating": 4.2,
         "ratingCount": 5},
        {"title": "Beverage-Air 27in Prep Table", "rating": 3.0,
         "ratingCount": 1}])

    r = reviews.outside_opinion("Beverage-Air", "***S********",
                                family="reach-in cooler")
    assert r["available"] is False
    assert "not a machine consumers review" in r["say"]


def test_a_brand_average_on_eighteen_reviews_is_refused(dbfile, monkeypatch):
    """Three products clearing the bar is not enough on its own.

    The live run flickered on exactly this: the same Beverage-Air query
    refused twice and returned 4.67 from eighteen reviews once, depending on
    which listings Google happened to send. A brand average is a weaker claim
    than a rating for the machine, so it has to rest on a stronger sample.
    """
    reviews = _shopping(monkeypatch, [
        {"title": "Beverage-Air Back Bar Refrigerator", "rating": 5.0,
         "ratingCount": 8},
        {"title": "Beverage Air UCR48A Commercial", "rating": 4.5,
         "ratingCount": 5},
        {"title": "Beverage-Air WTR27A Worktop", "rating": 4.0,
         "ratingCount": 5}])

    r = reviews.outside_opinion("Beverage-Air", "HRS1WHC***G********",
                                family="reach-in freezer")
    assert r["available"] is False
    assert "not a machine consumers review" in r["say"]


def test_a_make_that_shares_a_word_with_a_bigger_industry(dbfile, monkeypatch):
    """The worst thing this module ever did, caught on a live run.

    Searched on its own, "Continental" returned 64,376 reviews of car and
    bicycle tyres and offered them as the rating for a commercial freezer
    maker. The make guard could not help: those really are Continental
    products. Only the category separates them.
    """
    from src import reviews

    asked = []
    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping",
                        lambda q: (asked.append(q), {"shopping": []})[1])

    reviews.outside_opinion("Continental", "", family="reach-in freezer")
    assert asked, "no search was made at all"
    assert all("reach-in freezer" in q for q in asked), \
        f"a brand search went out without its category: {asked}"


def test_no_category_means_no_brand_answer(dbfile, monkeypatch):
    """Rather than a confident rating for the wrong industry."""
    from src import reviews

    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping", lambda q: {"shopping": [
        {"title": "Continental ExtremeContact DWS06 Tyre", "rating": 4.7,
         "ratingCount": 3200}]})
    monkeypatch.setattr(reviews, "_family_of", lambda *a: "")

    r = reviews.outside_opinion("Continental", "")
    assert r["available"] is False
    assert "4.7" not in str(r)


def test_the_family_is_read_from_our_own_book(dbfile, monkeypatch):
    """The system knows what it sells. It should not need to be told."""
    from src import reviews

    asked = []
    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping",
                        lambda q: (asked.append(q), {"shopping": []})[1])
    monkeypatch.setattr(reviews, "_family_of", lambda *a: "walk-in cooler")

    reviews.outside_opinion("Continental", "")
    assert any("walk-in cooler" in q for q in asked), \
        "the family we hold on record was not used"


def test_the_catalogues_legal_name_still_matches_the_markets_name(dbfile):
    """The federal data and the shop do not call a make the same thing.

    The catalogue says "True Refrigeration". Every listing on the market says
    "True T-23-HC" or "True Mfg. GDM-47-HC-LD". Matching the full name threw
    away all 206 reviews the make actually has and reported silence instead.
    """
    from src.reviews import _is_the_make

    assert _is_the_make("True T-23-HC 27in Reach-In Refrigerator",
                        "True Refrigeration")
    assert _is_the_make("True Mfg. GDM-47-HC-LD Glass Door Merchandiser",
                        "True Refrigeration")
    assert _is_the_make("Avantco A-19R-HC Reach In", "Avantco Refrigeration")
    assert _is_the_make("Baxter OV310G Rack Oven", "Baxter Mfg.")

    # Punctuation drift, which is why containment stays as the other way in.
    assert _is_the_make("Beverage Air WTR27A Worktop", "Beverage-Air")
    assert _is_the_make("BEVERAGEAIR MT34-1-B", "Beverage-Air")


def test_stripping_the_suffix_does_not_let_a_lookalike_through(dbfile):
    """Why the token match is on word boundaries and not containment.

    Reducing "True Refrigeration" to "True" and then asking whether "true"
    appears anywhere in the title would match TrueTone and Truelove. It has to
    appear as its own word.
    """
    from src.reviews import _is_the_make

    assert not _is_the_make("TrueTone Bluetooth Speaker", "True Refrigeration")
    assert not _is_the_make("Truelove Pet Cooler", "True Refrigeration")
    assert not _is_the_make("Turbo Air M3R24-1-N", "True Refrigeration")
    assert not _is_the_make("Continental CFA1-SS Freezer", "Beverage-Air")


def test_a_miss_expires_sooner_than_a_rating(dbfile, monkeypatch):
    """Google Shopping does not return a stable result set.

    Consecutive identical Comfee queries came back with 382 reviews across
    sixteen products, then with nothing. A month-long cache on the miss would
    have the desk saying nobody reviews Comfee for four weeks.
    """
    from datetime import datetime, timedelta

    from src import db, reviews

    monkeypatch.setenv("SERPER_API_KEY", "x")
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    with db.txn() as c:
        c.execute("""INSERT INTO outside_reviews
                     (manufacturer,model_number,source,rating,review_count,
                      matched_name,fetched_at)
                     VALUES (?,?,?,?,?,?,?)""",
                  ("Comfee", "CRR33S3ARD", "google_shopping", None, 0, None, old))
        c.execute("""INSERT INTO outside_reviews
                     (manufacturer,model_number,source,rating,review_count,
                      matched_name,fetched_at)
                     VALUES (?,?,?,?,?,?,?)""",
                  ("Lenovo", "21SX", "google_shopping", 4.6, 812, "x", old))

    assert reviews._cached("Comfee", "CRR33S3ARD", "google_shopping") is None, \
        "a ten day old miss was still being trusted"
    assert reviews._cached("Lenovo", "21SX", "google_shopping") is not None, \
        "a ten day old rating should still be good"


def test_every_wildcard_marker_the_catalogue_uses_is_stripped(dbfile):
    """The catalogue masks variant positions with three different markers.

    MT34-1[#] only matched a real listing by luck of the normaliser stripping
    brackets. Hyphens, spaces and dots survive, because those are real
    characters in a real model number.
    """
    from src.reviews import _searchable_model

    assert _searchable_model("HRP2HC***S********") == "HRP2HC"
    assert _searchable_model("MT34-1[#]") == "MT34-1"
    assert _searchable_model("G12010~") == "G12010"
    assert _searchable_model("AM240QDW Plus") == "AM240QDW Plus"
    assert _searchable_model("CP314-2H") == "CP314-2H"
    assert _searchable_model("***********") == "", "all mask, nothing to search"
    assert _searchable_model("A*") == "", "too little left to identify anything"


def test_a_thin_model_sample_must_be_quoted_with_its_size(dbfile, monkeypatch):
    """Six reviews on their exact machine beats eight thousand on the brand.

    It is still six. The rating is worth saying and is not worth saying alone,
    so the instruction carries the sample size into the same sentence.
    """
    reviews = _shopping(monkeypatch, [
        {"title": "Beverage-Air MT34-1-B Marketeer Merchandiser",
         "rating": 3.7, "ratingCount": 6}])

    r = reviews.outside_opinion("Beverage-Air", "MT34-1[#]")
    assert r["available"] is True
    assert r["level"] == "model"
    assert "6 people reviewed" in r["say"]
    assert "never the rating alone" in r["say"]


def test_google_shopping_wins_when_both_are_configured(dbfile, monkeypatch):
    """Not vendor preference. It aggregates across retailers rather than one."""
    from src import reviews

    monkeypatch.setenv("BESTBUY_API_KEY", "x")
    monkeypatch.setenv("SERPER_API_KEY", "x")
    assert reviews.provider() == "google_shopping"


def test_a_shopping_outage_never_raises(dbfile, monkeypatch):
    from src import reviews

    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(reviews, "_fetch_shopping",
                        lambda q: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        r = reviews.outside_opinion("Lenovo", "21SX")
    except Exception:
        pytest.fail("a search provider took the call down with it")
    assert r["available"] is False
