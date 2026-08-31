"""What an engineer photographed, read rather than thanked for.

WHAT WAS HAPPENING

`desk.py` routes a known technician's message to close_by_text, and when that
message carried a photograph and no words it answered:

    "Thanks Curtis, got the photo. Send a line about what you found
     and I will close the job."

The image was acknowledged and discarded. An engineer standing in front of an
open machine, photographing a burnt board or a scorched terminal or the rating
plate they cannot read out over a compressor, got a thank-you and nothing else.

Meanwhile the CUSTOMER path has read plates through a vision model since the
beginning. The person who cannot describe what they are looking at had the
better tool, and the person who could act on it had none.

WHY THIS IS NOT plate.py

`read_plate` answers one question: which machine is this. It is aimed at a
customer holding a phone up to a sticker, and everything about it, including
refusing to report a model the federal catalogue does not recognise, is built
for that.

An engineer's photograph is a different question. They know what the machine
is. They are showing a CONDITION: a component that has failed, a part number
on something they need ordering, damage that explains a callback. So this asks
what is in the picture and what it means for the job, and deliberately does
not try to identify the appliance.

WHAT IT REFUSES TO DO

It does not diagnose. A model looking at a photograph of a control board
cannot know whether the board is the fault, and saying so to the one person
qualified to judge would be worse than silence. It describes what is visible,
names any part number or marking it can read, and stops.
"""

from __future__ import annotations

import json

from .config import settings

# What a photograph from an engineer can usefully be. Video is deliberately
# absent: a clip of a running machine is a real diagnostic artefact and it is
# also a minute of audio and motion this cannot handle honestly, so it is
# refused with a reason rather than half-read.
CAN_READ = ("image/jpeg", "image/png", "image/webp", "image/heic")

PROMPT = (
    "You are looking at a photograph taken by a refrigeration engineer on a "
    "job. Describe ONLY what is visible.\n"
    "Return JSON with these keys:\n"
    "  what_is_shown: one sentence, plainly. A component, a rating plate, "
    "damage, a wiring detail.\n"
    "  markings: any part number, model number, serial or printed text you "
    "can read, exactly as printed. An empty list if none.\n"
    "  condition: anything visibly wrong: burning, corrosion, ice, a leak, a "
    "loose or broken connection. Empty string if nothing is visibly wrong.\n"
    "  legible: true only if the important text is genuinely readable.\n"
    "Do NOT diagnose. Do NOT guess a cause. Do NOT say what should be "
    "replaced. The engineer is standing in front of it and you are not."
)


def looks_readable(mime: str) -> bool:
    return any((mime or "").lower().startswith(t) for t in CAN_READ)


def why_not(mime: str) -> str:
    """What to say about a file this cannot read."""
    m = (mime or "").lower()
    if m.startswith("video/"):
        return ("I cannot watch video yet. A still of the part, or of the "
                "plate, and I can read that.")
    if m.startswith("audio/"):
        return ("I cannot listen to a recording. Tell me what you are hearing "
                "and where it is coming from.")
    if m.startswith("application/pdf"):
        return "Send it as a photo rather than a PDF and I can read it."
    return "I can read photographs. Send it as a picture and I will look."


def read_for_the_job(image: bytes, mime: str = "image/jpeg") -> dict:
    """What an engineer's photograph shows, without diagnosing it.

    Args:
        image: the photograph.
        mime: its content type.
    """
    if not image:
        return {"ok": False, "why": "no image"}
    if not looks_readable(mime):
        return {"ok": False, "why": why_not(mime)}

    try:
        from google import genai
        from google.genai import types as gt

        client = genai.Client(vertexai=True,
                              project=settings.project,
                              location=settings.location)
        out = client.models.generate_content(
            model=settings.simple_model,
            contents=[gt.Part.from_bytes(data=image, mime_type=mime), PROMPT],
            config=gt.GenerateContentConfig(response_mime_type="application/json"),
        )
        seen = json.loads(out.text or "{}")
    except Exception as e:
        print(f"[seeing] could not read the photo: {type(e).__name__}: {e}",
              flush=True)
        return {"ok": False,
                "why": "I could not read that one. Send another if it "
                       "matters, otherwise tell me what you are seeing."}

    return {
        "ok": True,
        "what_is_shown": (seen.get("what_is_shown") or "").strip(),
        "markings": [m for m in (seen.get("markings") or []) if m],
        "condition": (seen.get("condition") or "").strip(),
        "legible": bool(seen.get("legible")),
    }


def reply_to_the_engineer(seen: dict) -> str:
    """What to say back, built from what was read rather than narrated.

    Ends by asking for the sentence that closes the job, because a photograph
    is evidence and not a closure: nothing here can say what was wrong or what
    fixed it, and only the engineer can.
    """
    if not seen.get("ok"):
        return seen.get("why", "I could not read that.")

    bits = []
    if seen.get("what_is_shown"):
        # The model returns a sentence without a full stop about half the
        # time, and joining on a space then runs it into the next clause.
        said = seen["what_is_shown"].rstrip()
        bits.append(said if said.endswith((".", "!", "?")) else said + ".")
    if seen.get("condition"):
        bits.append(f"I can see {seen['condition']}.")
    if seen.get("markings"):
        bits.append("I can read: " + ", ".join(seen["markings"][:4]) + ".")

    if not bits:
        return ("Got the photo but I cannot make much out. Tell me what you "
                "are looking at and I will get it on the job.")

    return (" ".join(bits) + " Noted against the job. Send a line about what "
            "you found and what you fitted, and that closes it.")
