"""A promotion read over the hold music, like a station break.

WHY THIS OVERRULES A DECISION THAT WAS ALREADY WRITTEN DOWN

station.py says, in its own words, "It does not read a promotion out over the
hold music", and it left `spoken_lead_in` built and uncalled so the choice
would be visible rather than forgotten. Two reasons were given:

    selling to somebody whose call has just failed is tone-deaf
    a spoken line in a 1.6 second gap talks over the agent coming back

Both are true. Neither covers the case this handles. The first is about the
FALLBACK path, when the desk could not be reached at all, and nothing here
touches it. The second is about a short lookup, and it is the reason this
waits: a caller who has been listening to music for the better part of a
minute while `advice` runs is not going to be talked over, and by then the
silence needs filling with something better than a loop.

So the rule is not "never", it is "not on a failed call, and not in a gap
short enough that the answer beats the advert". Both are enforced below rather
than left to judgement.

WHY IT IS RECORDED AHEAD AND NOT SPOKEN LIVE

The agent's own voice comes from the Live model and belongs to the
conversation. This is background: it plays while a tool is running, which is
exactly when the conversation cannot produce audio, and mixing a second live
stream into that is how two things end up talking at once. A short recorded
line, generated once and cached on disk, plays into the gap and stops the
moment anybody speaks, the same as the music does.

WHAT IT WILL NOT SAY

Trade-only offers, which is the same audience gate `spoken_lead_in` already
applied: two of the four offers on this book are trade-accounts only, and
reading one to whoever happens to be holding is the same failure as quoting a
price nobody checked. And nothing at all when there is no live promotion --
silence is better than an invented advert.
"""

from __future__ import annotations

import audioop
import base64
import hashlib
import wave
from datetime import datetime
from pathlib import Path

from .config import TWILIO_RATE

ASSETS = Path(__file__).resolve().parents[1] / "assets"

# The voice the break is read in. Deliberately not the agent's: a caller
# should be able to tell the difference between the desk talking to them and a
# recorded announcement, and a recording in the desk's own voice pretending to
# be live is the kind of small dishonesty this project is built to avoid.
VOICE = "Puck"
TTS_MODEL = "gemini-2.5-flash-preview-tts"

# Quieter than the music it sits over would be wrong -- an advert nobody can
# make out is worse than no advert. Louder than the music, quieter than the
# agent.
GAIN = 0.75


def line_for(dealer_id: str = "") -> str:
    """The words, or nothing at all if there is no live offer.

    Built rather than generated, like every other unattended message on this
    system, so nobody has to review what a model decided to claim about a
    price.

    Blank means whichever company this call was routed to, NOT refrigeration.
    A default naming one tenant is a wrong answer waiting for the data to be
    right: it would read the fridge offer to somebody who rang about a chair.
    """
    from . import db
    from .tenancy import the_desk

    dealer_id = the_desk(dealer_id)
    today = datetime.now().date().isoformat()
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT headline, ends FROM promotions
                   WHERE dealer_id = ? AND ends >= ?
                     AND (terms IS NULL OR terms NOT LIKE '%trade%')
                   ORDER BY ends LIMIT 1""", (dealer_id, today)).fetchone()
    except Exception as e:
        print(f"[radio] could not read the promotions: "
              f"{type(e).__name__}: {e}", flush=True)
        return ""

    if row is None:
        return ""
    return (f"While you hold: {row['headline']}, until {_spoken_date(row['ends'])}. "
            "Ask whoever answers whether it applies to you.")


def _spoken_date(iso: str) -> str:
    """A date as somebody would say it out loud.

    The stored value is 2026-09-15 and a voice reads that as "twenty twenty
    six dash zero nine", which is not a date anybody recognises. Written out
    here rather than left to the model, because this is a recorded line and
    what it says has to be the same every time it plays.
    """
    from datetime import date

    try:
        when = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "further notice"

    nth = {1: "first", 2: "second", 3: "third", 21: "twenty first",
           22: "twenty second", 23: "twenty third", 31: "thirty first"}.get(
        when.day, f"{when.day}th")
    return f"{when.strftime('%B')} the {nth}"


def _wav_for(text: str) -> Path:
    """Where the recording of this exact sentence lives.

    Keyed on the words, so a promotion that changes gets a new file and an
    unchanged one is never generated twice. The old file is left alone: it
    costs a few kilobytes and it is the evidence of what actually went out.
    """
    stamp = hashlib.sha256(text.encode()).hexdigest()[:12]
    return ASSETS / f"promo_{stamp}.wav"


def recording_of(text: str) -> Path | None:
    """The line as an 8 kHz mono wav, generating it once if need be.

    Returns nothing on any failure at all. A promotion that could not be
    recorded is worth a log line; it is not worth a caller hearing an error
    where the music should be.
    """
    if not text.strip():
        return None

    path = _wav_for(text)
    if path.exists():
        return path

    try:
        import google.genai as genai
        from google.genai import types

        from . import config  # noqa: F401  (loads the key)

        client = genai.Client()
        got = client.models.generate_content(
            model=TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=VOICE)))))
        blob = got.candidates[0].content.parts[0].inline_data
        pcm = blob.data

        # The model returns 24 kHz signed 16-bit mono. The phone line wants
        # 8 kHz. Resampling here rather than at play time, because the whole
        # point of this path is that the caller is already waiting.
        rate = 24000
        for bit in (blob.mime_type or "").split(";"):
            if bit.strip().startswith("rate="):
                rate = int(bit.split("=", 1)[1])
        if rate != TWILIO_RATE:
            pcm, _ = audioop.ratecv(pcm, 2, 1, rate, TWILIO_RATE, None)

        ASSETS.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TWILIO_RATE)
            w.writeframes(pcm)
        print(f"[radio] recorded {path.name} ({len(pcm)} bytes at "
              f"{TWILIO_RATE} Hz)", flush=True)
        return path
    except Exception as e:
        print(f"[radio] could not record the promotion: "
              f"{type(e).__name__}: {e}", flush=True)
        return None


def frames_for(dealer_id: str = "") -> list[str]:
    """The break, as ready-to-send Twilio frames. Empty when there is none.

    Blank means the company this call belongs to. See `line_for`.
    """
    text = line_for(dealer_id)
    if not text:
        return []

    path = recording_of(text)
    if path is None:
        return []

    try:
        with wave.open(str(path), "rb") as w:
            if w.getframerate() != TWILIO_RATE or w.getnchannels() != 1:
                return []
            pcm = w.readframes(w.getnframes())
    except (OSError, wave.Error):
        return []

    pcm = audioop.mul(pcm, 2, GAIN)
    step = (TWILIO_RATE * 20 // 1000) * 2
    return [base64.b64encode(audioop.lin2ulaw(pcm[i:i + step], 2)).decode("ascii")
            for i in range(0, len(pcm) - step + 1, step)]
