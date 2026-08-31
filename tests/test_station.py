"""Which hold track plays, and why it is not random.

WHAT CHANGED

There was one track, 32.8 seconds, looping. Anybody held for two minutes heard
it four times, which is how hold music stops being neutral and starts being
irritating. The music is generated rather than licensed, so making four costs
nothing but the generating.

WHY THE DATE

Per call sounds clever and is worse: somebody ringing twice in an afternoon
about the same freezer hears two different tracks and wonders whether they
reached the same company. Random has that problem AND cannot be reproduced
when somebody reports the hold music was awful on Tuesday.

A function of the date fixes both. Every line answers the same on the same
day, and a complaint is reproducible.

AND THE PART THAT IS DELIBERATELY NOT BUILT

No promotion is read over the hold music. Hold audio plays in exactly two
places: the fallback, which fires when the desk could not be reached at all,
and comfort.py, which fills a 1.6 second gap during a lookup. Selling to
somebody whose call just failed is tone-deaf, and a spoken line in a 1.6
second gap talks over the agent returning.

`spoken_lead_in` exists and nothing calls it, so the decision is visible
rather than an omission. These tests pin that, including that it would still
be audience-gated if it were ever switched on.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def test_there_is_always_something_to_play(dbfile):
    """A phone line must never go silent because a generated file is missing."""
    from src import station

    assert station.todays_track()
    assert station.path_for().exists()


def test_the_same_day_always_gives_the_same_track(dbfile):
    """Reproducible. "The hold music was awful on Tuesday" has to be a thing
    somebody can go and check."""
    from src import station

    day = date(2026, 8, 30)
    assert station.todays_track(day) == station.todays_track(day)


def test_it_turns_over_rather_than_changing_every_call(dbfile, monkeypatch):
    """Three days, not per call: somebody ringing twice in an afternoon must
    hear the same music both times."""
    from src import station

    monkeypatch.setattr(station, "tracks",
                        lambda: ["hold_1.wav", "hold_2.wav", "hold_3.wav"])

    # Aligned to a block boundary. The windows are anchored to the ordinal
    # date, not to whatever day the test happens to pick, so starting
    # mid-window would straddle two blocks and fail for the wrong reason.
    start = date(2026, 8, 30)
    while start.toordinal() % station.DAYS_PER_TRACK:
        start += timedelta(days=1)

    same_block = {station.todays_track(start + timedelta(days=n))
                  for n in range(station.DAYS_PER_TRACK)}
    assert len(same_block) == 1, "the track changed inside its own window"

    later = station.todays_track(start + timedelta(days=station.DAYS_PER_TRACK))
    assert later not in same_block, "the track never changed"


def test_every_track_gets_used(dbfile, monkeypatch):
    """A rotation that only ever reaches two of four files is not a rotation."""
    from src import station

    monkeypatch.setattr(station, "tracks",
                        lambda: ["hold_1.wav", "hold_2.wav", "hold_3.wav",
                                 "hold_4.wav"])

    start = date(2026, 8, 30)
    seen = {station.todays_track(start + timedelta(days=n))
            for n in range(station.DAYS_PER_TRACK * 4)}
    assert len(seen) == 4


def test_a_caller_cannot_ask_for_a_file_outside_the_assets_folder(dbfile):
    """The track name reaches this from a URL on a public endpoint. Without
    this, somebody could ask for ../../.env and be handed it."""
    from src import station

    for nasty in ("../../.env", "../../../etc/passwd", "..\\..\\.env"):
        got = station.path_for(nasty)
        assert got.name == station.ALWAYS_THERE


def test_a_missing_file_falls_back_rather_than_erroring(dbfile):
    from src import station

    assert station.path_for("hold_9.wav").name == station.ALWAYS_THERE


# --------------------------------------------------------------------------
# the promotion that deliberately does not play
# --------------------------------------------------------------------------

def test_nothing_reads_a_promotion_over_the_hold_music(dbfile):
    """The decision, pinned. Hold audio plays when the desk has FAILED or
    during a 1.6 second lookup gap, and an offer belongs in neither."""
    import inspect

    from src import main, station
    from src.telephony import comfort

    for module in (main, comfort):
        assert "spoken_lead_in" not in inspect.getsource(module), (
            f"{module.__name__} started reading offers over the hold music")

    # And it still exists, so the choice is visible rather than forgotten.
    assert callable(station.spoken_lead_in)


def test_if_it_were_ever_used_it_would_still_be_audience_gated(dbfile):
    """Two of the four offers on this book are trade-accounts only. Reading
    one of those to whoever happens to be holding is the same failure as
    quoting a price nobody checked."""
    from src import db, station

    with db.txn() as c:
        c.executemany(
            "INSERT INTO promotions (id,dealer_id,headline,ends,terms) "
            "VALUES (?,?,?,?,?)",
            [("PR-TRADE", "D-REF", "20% off for the trade", "2099-01-01",
              "trade accounts only"),
             ("PR-ALL", "D-REF", "Free delivery this month", "2099-01-01",
              None)])

    said = station.spoken_lead_in("D-REF")
    assert "trade" not in said.lower()
    assert "Free delivery" in said


def test_it_says_nothing_when_there_is_nothing_on(dbfile):
    """Silence beats inventing an offer, which is the rule everywhere else in
    this system."""
    from src import db, station

    with db.txn() as c:
        c.execute("DELETE FROM promotions")

    assert station.spoken_lead_in("D-REF") == ""


# --------------------------------------------------------------------------
# the station keeping itself stocked
# --------------------------------------------------------------------------

def test_it_asks_for_a_track_when_the_library_is_short(dbfile, monkeypatch,
                                                       tmp_path):
    from src import station

    monkeypatch.setattr(station, "ASSETS", tmp_path)
    (tmp_path / "hold_1.wav").write_bytes(b"x")

    want, why = station.needs_a_new_one()
    assert want is True
    assert "keeping" in why


def test_it_stops_asking_once_the_library_is_full(dbfile, monkeypatch,
                                                  tmp_path):
    """Lyria is billable and this runs unattended. A nightly job that always
    generates is a nightly bill."""
    from src import station

    monkeypatch.setattr(station, "ASSETS", tmp_path)
    for n in range(1, station.KEEP_TRACKS + 1):
        (tmp_path / f"hold_{n}.wav").write_bytes(b"x")

    want, why = station.needs_a_new_one()
    assert want is False
    assert "newest" in why


def test_a_stale_library_is_refreshed(dbfile, monkeypatch, tmp_path):
    """Full but old still turns over, so the station does not freeze on the
    six tracks it happened to generate first."""
    import os
    import time

    from src import station

    monkeypatch.setattr(station, "ASSETS", tmp_path)
    old = time.time() - (station.REFRESH_EVERY_DAYS + 1) * 86400
    for n in range(1, station.KEEP_TRACKS + 1):
        f = tmp_path / f"hold_{n}.wav"
        f.write_bytes(b"x")
        os.utime(f, (old, old))

    want, why = station.needs_a_new_one()
    assert want is True
    assert "days old" in why


def test_a_refusal_never_breaks_the_nightly_sweep(dbfile, monkeypatch,
                                                  tmp_path):
    """Observed for real: four generations back to back are refused with 403
    while the same call made singly succeeds. A station that cannot generate
    music must not stop safety recalls going out."""
    from src import station

    monkeypatch.setattr(station, "ASSETS", tmp_path)

    import scripts.make_hold_music as maker
    monkeypatch.setattr(maker, "_once", lambda prompt: None)

    out = station.refresh()
    assert out["generated"] is False
    assert "refused" in out["why"] or "tomorrow" in out["why"]


def test_the_sweep_reports_what_the_station_did(dbfile, monkeypatch):
    """Visible in the nightly output rather than silent, so somebody can see
    money being spent on music."""
    from src import outreach, station

    monkeypatch.setattr(station, "refresh",
                        lambda force=False: {"ok": True, "generated": True,
                                             "track": "hold_5.wav",
                                             "why": "short"})
    out = outreach.run_sweep("D-REF")
    assert out["music"]["generated"] is True
    assert out["music"]["track"] == "hold_5.wav"


def test_the_original_track_is_never_retired(dbfile, monkeypatch, tmp_path):
    """hold.wav is the floor. Everything else can turn over, but a phone line
    must never go silent because the library churned."""
    from src import station

    monkeypatch.setattr(station, "ASSETS", tmp_path)
    (tmp_path / station.ALWAYS_THERE).write_bytes(b"x")
    for n in range(1, 4):
        (tmp_path / f"hold_{n}.wav").write_bytes(b"x")

    assert all(p.name != station.ALWAYS_THERE
               for p in station._generated_tracks())
