"""The caller speaks whatever they speak. The corpus is in English.

WHY THIS MATTERS HERE SPECIFICALLY

One in five US restaurant workers speaks English as a second language, a
quarter of the workforce is Hispanic or Latino, and close to a quarter is
foreign born. Those are not front-of-house numbers.

And the person who finds the walk-in at twelve degrees at six in the morning
is not the owner. It is whoever opened up. So the single most valuable call
this desk can take is disproportionately likely to come from somebody who
would rather not conduct it in English.

THE PROBLEM IS NOT THE SPEAKING

Gemini Live speaks Spanish natively; that part is configuration. The problem
is that the evidence is English. Six hundred and seventy repairs, written by
technicians in their own words, and the retrieval that makes this desk worth
more than a person with a clipboard searches those words.

A caller who says "el congelador no enfría bien desde anoche" retrieves
nothing. The desk then falls back to having no history, which is exactly the
condition it exists to avoid, and it happens silently.

SO THE SYMPTOM IS NORMALISED, NOT THE EVIDENCE

The caller's words are turned into English at the one boundary where they meet
the corpus, and nowhere else. Their actual words are still what gets recorded,
quoted back and kept, for the same reason the repair corpus keeps a
technician's phrasing rather than a summary.

The model translates a question. It never translates an answer into a claim,
and it never touches a model number, a part number, a price or a time, because
those are not words and a helpful translation of one is a defect.

WHAT IT DOES WHEN IT CANNOT

Returns the original and says so. A symptom that could not be normalised
retrieves badly, which is bad; a symptom silently mistranslated retrieves
confidently against the wrong repairs, which is worse.
"""

from __future__ import annotations

import os
import re

# Loads .env, so GOOGLE_GENAI_USE_VERTEXAI is set before the client is built.
# Without it the client falls through to the public API path and asks for an
# API key that does not exist, which the fallback then handles gracefully and
# silently: the desk keeps working and every non-English caller quietly gets
# no history.
from .config import settings  # noqa: F401

import contextvars

# What the caller is speaking, readable from anywhere in the stack.
#
# A context variable rather than an argument threaded through nine
# functions, for the same reason trace.CALL is one: `_fault_distribution`
# has no business taking a language parameter to satisfy a retrieval
# detail, and every caller of it would have to learn about one.
#
# Set by guard_tool, which already runs before EVERY tool call and already
# holds the session state this lives in. That is also what bounds its
# lifetime: the value is overwritten at the start of every tool, so a caller
# who hangs up cannot leave their language behind for the next one.
#
# Nothing else may set it and walk away. `set_language` writes it for the rest
# of the current turn, and guard_tool re-establishes it from state on the next
# one, which is the only place the truth lives.
SPEAKING = contextvars.ContextVar("praevisum_language", default="")


def forget() -> None:
    """Drop the current language. For tests and for the end of a conversation.

    A context variable outlives the call that set it. In production that is
    harmless because guard_tool overwrites it before every tool, but nothing
    else should be able to set it and leave.
    """
    SPEAKING.set("")

# Codes for anything the retrieval can already read. English needs no work,
# and pretending otherwise would put a model call in front of every symptom
# on every call for nothing.
NATIVE = {"en", "en-us", "en-gb", ""}

# Things that must survive a translation untouched. A model number is not a
# word, and a model that helpfully renders HRP2HC as something more Spanish
# has destroyed the only identifier on the call.
KEEP = re.compile(
    r"\b([A-Z0-9]{2,}[-/][A-Z0-9-]{1,}|[A-Z]{2,}\d[A-Z0-9*#~\[\]-]*|"
    r"P-[A-Z0-9]+|R-\d{3}[A-Za-z]?|\d+\s?(?:degrees?|°[CF]|[CF]\b))")

_client = None


def _model():
    global _client
    if _client is None:
        from google import genai

        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in {"1", "TRUE"}:
            _client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                # Gemini 3.x serves from the global endpoint, not a region.
                location=os.getenv("PRAEVISUM_VISION_LOCATION", "global"))
        else:
            _client = genai.Client()
    return _client


def _protect(text: str) -> tuple[str, dict]:
    """Hide model numbers, part numbers and temperatures behind placeholders."""
    kept: dict[str, str] = {}

    def swap(m):
        token = f"__K{len(kept)}__"
        kept[token] = m.group(0)
        return token

    return KEEP.sub(swap, text), kept


def _restore(text: str, kept: dict) -> str:
    for token, original in kept.items():
        text = text.replace(token, original)
    return text


PROMPT = (
    "Translate this description of a broken piece of commercial kitchen "
    "equipment into plain English, as a technician would write it in a job "
    "sheet.\n\n"
    "Rules:\n"
    "  Translate only. Do not diagnose, do not add a cause, do not add a "
    "part that was not mentioned.\n"
    "  Leave every __K0__ style placeholder exactly as it is.\n"
    "  Keep it to one clause, the length of the original.\n"
    "  If it is already English, return it unchanged.\n\n"
    "Description: {text}\n\nEnglish:")


def for_retrieval(text: str, language: str = "") -> dict:
    """The caller's words, in the language the corpus is written in.

    Used at the one boundary where a symptom meets the repair index, and
    nowhere else. What the customer actually said is still what gets recorded
    and quoted back to them.

    Args:
        text: the symptom in the caller's own words.
        language: their language code, if the channel knows it.

    Returns:
        `searchable` for the corpus, `said` as they put it, and `translated`
        so a caller cannot be told we understood when we did not.
    """
    said = (text or "").strip()
    if not said or (language or "").lower() in NATIVE:
        return {"searchable": said, "said": said, "translated": False,
                "language": language or "en"}

    guarded, kept = _protect(said)
    try:
        from google.genai import types as gt

        resp = _model().models.generate_content(
            model=os.getenv("PRAEVISUM_WORKER_MODEL", "gemini-3.5-flash"),
            contents=[gt.Part(text=PROMPT.format(text=guarded))],
            config=gt.GenerateContentConfig(temperature=0))
        english = _restore((resp.text or "").strip(), kept)
    except Exception as e:
        # The original, and an honest flag. A symptom that could not be
        # normalised retrieves badly. A symptom silently mistranslated
        # retrieves confidently against the wrong repairs, which is worse.
        print(f"[language] could not normalise a {language} symptom: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"searchable": said, "said": said, "translated": False,
                "language": language,
                "say": "Retrieval ran on their own words and may have found "
                       "nothing. Do not claim we have seen this before unless "
                       "a tool actually returned something."}

    if not english:
        return {"searchable": said, "said": said, "translated": False,
                "language": language}

    return {"searchable": english, "said": said, "translated": True,
            "language": language,
            "say": "The corpus was searched in English. Quote the customer "
                   "back in their own words, not the translation."}


def kept_intact(original: str, translated: str) -> bool:
    """Did every identifier survive the round trip?

    A model number, a part number or a temperature that changed is a defect,
    not a stylistic choice, and it is the failure most likely to pass a casual
    read: the sentence still looks right.
    """
    return all(tok in translated for tok in KEEP.findall(original) or [])


# Languages the desk will switch into. Not a limit on what Gemini can speak,
# a limit on what has been thought about: each one here means the retrieval
# normalisation has been considered and the identifiers survive it.
SPOKEN = {
    "es": "Spanish", "en": "English", "pt": "Portuguese", "fr": "French",
    "zh": "Chinese", "vi": "Vietnamese", "tl": "Tagalog", "ar": "Arabic",
    "pl": "Polish", "ko": "Korean",
}


def set_language(code: str, tool_context) -> dict:
    """Switch to the language the caller is actually speaking.

    Call this the moment somebody answers in something other than English, and
    then keep going in that language. The desk opens in English because it
    cannot know before they speak, not because English is the default.

    Args:
        code: two-letter code for what they are speaking, such as "es".
    """
    code = (code or "").strip().lower()[:2]
    if code not in SPOKEN:
        return {"ok": False, "why": f"{code} is not one of the languages this "
                                    "desk has been set up for",
                "say": "Carry on in English and do not pretend to switch."}

    tool_context.state["language"] = code
    SPEAKING.set(code)

    # Onto the contact as well, so the NEXT call opens in their language
    # instead of making them switch it again. Same shape as took_two_trips:
    # one fact from the database changing how a call starts.
    contact = (tool_context.state.get("caller") or {}).get("contact_id")
    if contact:
        try:
            from . import db

            with db.txn() as c:
                c.execute("UPDATE contacts SET language = ? WHERE id = ?",
                          (code, contact))
        except Exception as e:
            print(f"[language] could not remember {code} for {contact}: "
                  f"{type(e).__name__}: {e}", flush=True)

    return {
        "ok": True, "language": code, "called": SPOKEN[code],
        "say": (f"Continue in {SPOKEN[code]} from here. Read model numbers, "
                "part numbers, prices and times exactly as they are, in "
                "digits, because those are not words. Everything the tools "
                "return is in English: say what it means, do not read it out."),
    }
