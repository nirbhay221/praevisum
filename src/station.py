"""Which hold track plays today.

WHY THERE IS MORE THAN ONE NOW

There was exactly one, 32.8 seconds long, looping. Anybody held for two
minutes heard it four times. The music was generated rather than licensed,
which is the reason it can exist on a service line at all, so making four
costs nothing but the generating.

WHY THE DATE AND NOT RANDOM, AND NOT PER CALL

Per call sounds clever and is worse: somebody who rings twice in an afternoon
about the same freezer hears two different tracks and wonders whether they got
the same company. Random has the same problem plus it cannot be reproduced
when somebody reports that the hold music was awful on Tuesday.

So it is a function of the date. Every line of this system answers the same on
the same day, a bug is reproducible, and it turns over often enough not to
become the company jingle.

WHAT THIS DELIBERATELY DOES NOT DO

It does not read a promotion out over the hold music.

The two places hold audio actually plays are the FALLBACK path, which fires
when the desk could not be reached at all, and comfort.py, which fills a
1.6 second gap while a lookup runs. Selling to somebody whose call has just
failed is tone-deaf, and a spoken line in a 1.6 second gap talks over the
agent coming back. offers.py puts the offer at the moment of the quote
instead, which is where it earns money.

The capability is here if that judgement changes: `spoken_lead_in` builds the
line and nothing calls it. It is a decision, not an omission.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"

# How long a track stays before the next one. Long enough to feel settled,
# short enough that a weekly caller is not stuck with one loop.
DAYS_PER_TRACK = 3

# The original, kept as the fallback so this can never leave a phone line
# silent because a generated file is missing.
ALWAYS_THERE = "hold.wav"


def tracks() -> list[str]:
    """Every hold track on disk, in a stable order."""
    found = sorted(p.name for p in ASSETS.glob("hold_[0-9].wav"))
    if (ASSETS / ALWAYS_THERE).exists():
        found.append(ALWAYS_THERE)
    return found or ([ALWAYS_THERE] if (ASSETS / ALWAYS_THERE).exists() else [])


def todays_track(on: date | None = None) -> str:
    """The file that plays today.

    Args:
        on: pretend it is this date, for testing.
    """
    have = tracks()
    if not have:
        return ALWAYS_THERE

    day = (on or date.today()).toordinal()
    return have[(day // DAYS_PER_TRACK) % len(have)]


def path_for(name: str = "") -> Path:
    """Where a track lives, refusing anything outside the assets folder.

    The name reaches this from a URL on a public endpoint, so a caller could
    otherwise ask for ../../.env and be handed it.
    """
    wanted = (name or todays_track()).strip()
    target = (ASSETS / wanted).resolve()
    if not str(target).startswith(str(ASSETS.resolve())):
        return ASSETS / ALWAYS_THERE
    if not target.exists():
        return ASSETS / ALWAYS_THERE
    return target


def spoken_lead_in(dealer_id: str = "D-REF") -> str:
    """A line that could be read before the music. Nothing calls this.

    Built rather than generated, like every other unattended message, and
    audience-gated: two of the four offers on this book are trade-accounts
    only, and reading one of those to whoever happens to be holding is the
    same failure as quoting a price nobody checked.

    It exists so the decision not to use it is visible. See the module
    docstring for why hold audio is the wrong place for an offer.
    """
    from datetime import datetime

    from . import db

    today = datetime.now().date().isoformat()
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT headline, ends FROM promotions
                   WHERE dealer_id = ? AND ends >= ?
                     AND (terms IS NULL OR terms NOT LIKE '%trade%')
                   ORDER BY ends LIMIT 1""", (dealer_id, today)).fetchone()
    except Exception:
        return ""

    if row is None:
        return ""
    return (f"While you hold: {row['headline']}, until {row['ends']}. "
            "Ask whoever answers and they will tell you if it applies to you.")


# How many tracks the station keeps. Beyond this the oldest is retired, so
# the library stays fresh without growing forever on a VM disk.
KEEP_TRACKS = 6

# A new one is generated when the rotation has been all the way round, so the
# station changes at roughly the pace somebody would notice and no faster.
# At three days a track, six tracks is eighteen days of music.
REFRESH_EVERY_DAYS = DAYS_PER_TRACK * 2


def _generated_tracks() -> list[Path]:
    """Only the generated ones, oldest first. hold.wav is never retired."""
    return sorted(ASSETS.glob("hold_[0-9].wav"),
                  key=lambda p: p.stat().st_mtime)


def needs_a_new_one(on: date | None = None) -> tuple[bool, str]:
    """Whether the station should generate a track today, and why.

    Two conditions, both cheap to check and neither of them a timer:

      the library is short of KEEP_TRACKS, or
      the newest track is older than REFRESH_EVERY_DAYS

    Deliberately not "every night". Lyria is billable, this runs unattended,
    and a system that quietly spends money on music while nobody watches is
    the thing worth being careful about. Eighteen days of audio is already
    more than any caller will hear.
    """
    import time

    have = _generated_tracks()
    if len(have) < KEEP_TRACKS:
        return True, f"only {len(have)} generated tracks, keeping {KEEP_TRACKS}"

    newest = max(p.stat().st_mtime for p in have)
    age_days = (time.time() - newest) / 86400
    if age_days >= REFRESH_EVERY_DAYS:
        return True, (f"the newest track is {age_days:.0f} days old, "
                      f"refreshing every {REFRESH_EVERY_DAYS}")

    return False, f"{len(have)} tracks, newest is {age_days:.0f} days old"


def _next_name() -> str:
    used = {p.name for p in _generated_tracks()}
    for n in range(1, 10):
        if f"hold_{n}.wav" not in used:
            return f"hold_{n}.wav"
    return ""


def refresh(force: bool = False) -> dict:
    """Generate one track if the station wants one, and retire the oldest.

    ONE track, never a batch. Four generations back to back were refused with
    403 while the same call made singly succeeded, so the limit is on rate,
    and a nightly job that asks for one is both kinder and enough.

    Never raises. This is called from the nightly sweep and a station that
    cannot generate music must not stop recalls going out.
    """
    want, why = needs_a_new_one()
    if not (want or force):
        return {"ok": True, "generated": False, "why": why}

    name = _next_name()
    if not name:
        # Retire the oldest to make room, so the library turns over rather
        # than stopping once the names run out.
        oldest = _generated_tracks()[0]
        name = oldest.name
        try:
            oldest.unlink()
        except Exception as e:
            return {"ok": False, "generated": False,
                    "why": f"could not retire {oldest.name}: {e}"}

    try:
        import sys

        sys.path.insert(0, str(ASSETS.parent))
        from scripts.make_hold_music import TRACKS, _once, _to_phone

        prompt = TRACKS.get(name) or next(iter(TRACKS.values()))
        raw = _once(prompt)
        if not raw:
            return {"ok": False, "generated": False,
                    "why": "the music service refused, will try tomorrow"}

        (ASSETS / name).write_bytes(_to_phone(raw))
    except Exception as e:
        print(f"[station] could not refresh the music: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "generated": False, "why": str(e)[:120]}

    # Retire anything over the cap, oldest first.
    retired = []
    have = _generated_tracks()
    while len(have) > KEEP_TRACKS:
        gone = have.pop(0)
        try:
            gone.unlink()
            retired.append(gone.name)
        except Exception:
            break

    return {"ok": True, "generated": True, "track": name,
            "retired": retired, "why": why,
            "library": [p.name for p in _generated_tracks()]}
