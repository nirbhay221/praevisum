"""Reading the data plate off a photograph.

WHY THIS IS THE RIGHT USE OF A CAMERA HERE

The tempting one is diagnosis: point a phone at a freezer and ask what is
wrong with it. That is a model inventing a fault with no image corpus behind
it, on a machine somebody is standing in front of, and this project refuses
it. There is no photograph of a warm cabinet that distinguishes a failed
defrost timer from a blocked condenser.

The honest one is transcription. `identify_equipment` exists because a model
number spoken aloud never arrives clean:

    "A model number spoken aloud never arrives clean, so matching runs against
     a normalised form with dashes, spaces and case stripped, then by prefix,
     then by containment. Exact matching would fail on almost every real call."

That is the single most error-prone step in the whole call, and it is a person
in a loud kitchen reading HRP2HC***S******** off a sticker behind a door. A
photograph deletes it.

THE MODEL READS. THE CATALOGUE DECIDES.

What comes back from the vision call is a CLAIM about what is printed on a
sticker, and it is treated as one. Nothing is accepted until the federal
catalogue recognises it, and when the catalogue does not, the answer says so
rather than passing the model's reading off as the machine. That keeps this
consistent with every other decision in the system: the model may read, and it
may not assert.

A misread plate is not a harmless error either. It picks the wrong refrigerant,
and R-290 and R-600a are flammable and charge-limited. A technician is told
what to expect before opening a panel, so a confident wrong answer here is
worse than no answer.
"""

from __future__ import annotations

import json
import os

from .config import settings
from .tools import identify_equipment

# Gemini 3.x is served from the `global` Vertex endpoint and 404s in a region.
VISION_LOCATION = os.getenv("PRAEVISUM_VISION_LOCATION", "global")

READ_PLATE = (
    "This is a photograph of the rating plate or data sticker on a piece of "
    "commercial equipment. Transcribe what is printed on it.\n\n"
    "Return JSON with exactly these keys:\n"
    '  manufacturer  the brand as printed, or "" if you cannot read it\n'
    '  model         the model or catalogue number as printed, or ""\n'
    '  serial        the serial number as printed, or ""\n'
    "  legible       true only if you can actually read the characters\n\n"
    "Transcribe, do not interpret. Copy the characters you can see, including "
    "dashes and letters that look like digits. If the plate is blurred, cut "
    "off, or out of frame, set legible to false and leave the fields empty. "
    "Never guess a plausible model number for the kind of machine you think "
    "this is: an invented model number is worse than an unreadable photograph, "
    "because the wrong one sends a technician out expecting the wrong "
    "refrigerant."
)

_client = None


def _vision():
    """A client pinned to the endpoint that actually serves the vision model."""
    global _client
    if _client is None:
        from google import genai

        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in {"1", "TRUE"}:
            _client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=VISION_LOCATION,
            )
        else:
            _client = genai.Client()
    return _client


def _transcribe(image: bytes, mime: str) -> dict:
    """What the model says is printed on the plate. A claim, not a finding."""
    from google.genai import types as gt

    resp = _vision().models.generate_content(
        model=settings.worker_model,
        contents=[gt.Part.from_bytes(data=image, mime_type=mime),
                  gt.Part(text=READ_PLATE)],
        config=gt.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0,
        ),
    )
    return json.loads(resp.text or "{}")


def read_plate(image: bytes, mime: str = "image/jpeg") -> dict:
    """Identify a machine from a photo of its data plate.

    The vision model transcribes. The federal catalogue confirms. Nothing is
    reported as the machine until the catalogue recognises it, because a
    confident wrong model number sends a technician out expecting the wrong
    refrigerant.

    Args:
        image: the photograph.
        mime: its content type.
    """
    if not image:
        return {"ok": False, "why": "no image"}

    try:
        seen = _transcribe(image, mime)
    except Exception as e:
        # Same contract as every other outside call in this project. A photo
        # that cannot be read must not take the conversation down with it.
        return {"ok": False, "why": f"could not read the photo ({type(e).__name__})",
                "say": "Tell them the photo did not come through and ask them "
                       "to read the model number out instead."}

    make = (seen.get("manufacturer") or "").strip()
    model = (seen.get("model") or "").strip()

    if not seen.get("legible") or not model:
        return {
            "ok": False,
            "why": "the plate is not readable in that photo",
            "read": {"manufacturer": make, "model": model},
            "say": "Ask for another photo straight on with the whole sticker "
                   "in frame, or ask them to read the model number out. Do "
                   "not guess at what it might be.",
        }

    # The catalogue is the authority. Everything above this line is a reading.
    found = identify_equipment(model, brand_hint=make)

    if not found.get("found"):
        return {
            "ok": False,
            "why": "read the plate but the catalogue does not have that model",
            "read": {"manufacturer": make, "model": model,
                     "serial": (seen.get("serial") or "").strip()},
            "say": (f"Say what was read off the plate, which is {make} {model}, "
                    "and say plainly that it is not in our catalogue. Do NOT "
                    "treat the reading as confirmed. Ask them to check the "
                    "characters, since a misread plate picks the wrong "
                    "refrigerant."),
        }

    return {
        "ok": True,
        "read": {"manufacturer": make, "model": model,
                 "serial": (seen.get("serial") or "").strip()},
        "confirmed_by": "certified equipment catalogue",
        "machine": found,
        "say": ("The machine is identified and confirmed against the "
                "catalogue, so the model number no longer has to be read out. "
                "Carry straight on with what they rang about."),
    }
