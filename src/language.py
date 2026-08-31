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
import threading

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

    # DID THE IDENTIFIERS SURVIVE. `kept_intact` was written for exactly this
    # and nothing called it, so a model number mangled in translation went
    # straight into retrieval and searched confidently for the wrong machine.
    # Its own docstring names it the failure most likely to pass a casual
    # read, because the sentence still looks right.
    #
    # Falling back to the original is the safe direction: searching their own
    # words finds less, and searching a corrupted model number finds the wrong
    # thing and sounds certain about it.
    if not kept_intact(said, english):
        return {"searchable": said, "said": said, "translated": False,
                "why": "the translation changed a model or part number, so "
                       "the original wording is being searched instead",
                "identifiers_lost": True}

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



# Verbs and phrasings that mean "change language", in the languages somebody
# would ask in. Short because it only has to catch an ASK, not translate one.
# Languages not written in the Latin alphabet. A caller speaking one of these
# cannot produce a transcript that is pure ASCII.
_NOT_LATIN = {"ar", "hi", "zh", "ru", "ja", "ko", "he", "fa", "ur", "th", "el"}

# How sure the detector has to be before a switch is allowed.
#
# Chosen from the cases that actually went wrong. "hola necesito ayuda con mi
# congelador", a real Spanish sentence, scores 0.89. "Hello, Lenovo machine.",
# which is English with an Italian-looking brand in it, scores 0.28 for
# Italian -- a wrong answer the detector is already unsure about. Anything
# under this is not evidence of a language, it is a guess about one.
SURE_ENOUGH = 0.60

_DETECTOR = None


def _detector():
    """Lingua, built once and only when a switch is actually attempted.

    WHY A LIBRARY AND NOT A WORD LIST.

    The first version of this guard held a set of common English words and
    refused a switch when two of them appeared. That is a heuristic somebody
    invented, it knows nothing about the other nine languages this desk
    speaks, and it would have to be extended by hand for every new one.

    Lingua is built for SHORT text -- single phrases and even single words --
    which is all a phone call ever produces. langdetect and langid are both
    documented as unreliable on fragments, which is why they are not here.

    Falls back to allowing the other checks to decide if the library is
    missing, so a deployment without it degrades rather than breaks.
    """
    global _DETECTOR
    if _DETECTOR is not None:
        return _DETECTOR
    try:
        from lingua import Language, LanguageDetectorBuilder

        from lingua import IsoCode639_1

        wanted = []
        for code in SPOKEN:
            try:
                # getattr, not subscripting: IsoCode639_1 is an enum class and
                # is not subscriptable, which fails as a TypeError for every
                # language and leaves the detector silently unbuilt.
                iso = getattr(IsoCode639_1, code.upper(), None)
                if iso is None:
                    continue
                wanted.append(Language.from_iso_code_639_1(iso))
            except Exception:
                continue          # a language lingua does not carry
        if len(wanted) < 2:
            return None
        _DETECTOR = LanguageDetectorBuilder.from_languages(*wanted).build()
    except Exception as e:
        print(f"[language] no detector available, falling back to the other "
              f"checks: {type(e).__name__}: {e}", flush=True)
        _DETECTOR = None
    return _DETECTOR


def _is_really(code: str, text: str):
    """Is this text actually in that language. None when we cannot tell."""
    det = _detector()
    if det is None:
        return None
    try:
        values = det.compute_language_confidence_values(text)
    except Exception:
        return None
    if not values:
        return None

    best = values[0]
    said = best.language.iso_code_639_1.name.lower()

    # UNSURE IS A NO, NOT A SHRUG.
    #
    # "Hello, Lenovo machine." comes back as Italian at 0.28, because a brand
    # name looks Italian and three words is not much to go on. Treating that
    # as "cannot tell" let it through, and the call switched to Italian on the
    # strength of the word Lenovo.
    #
    # A switch needs EVIDENCE. A detector that is not sure has not provided
    # any, and the cost of refusing is that somebody repeats themselves once,
    # against a call conducted in a language the customer does not speak.
    if best.value < SURE_ENOUGH:
        return False
    return said == code.lower()

_ASKING = ("speak", "talk", "say it in", "in english", "switch to",
           "habla", "hablar", "espanol", "parle", "parlez", "francais",
           "sprich", "sprechen", "deutsch", "parla", "italiano", "fala",
           "portugues", "arabi", "arabic", "hindi", "chinese", "mandarin",
           "language")



# What the caller actually said, per call, most recent last. Fed by the
# telephony bridge as each utterance is transcribed.
_SAID: dict[str, list[str]] = {}
_SAID_LOCK = threading.Lock()


def they_said(call_id: str, text: str) -> None:
    """Record a caller utterance, so a claimed one can be checked against it."""
    if not call_id or not (text or "").strip():
        return
    with _SAID_LOCK:
        turns = _SAID.setdefault(call_id, [])
        turns.append(text.strip())
        del turns[:-6]          # the last few turns are all that can be meant


def forget_what_they_said(call_id: str) -> None:
    with _SAID_LOCK:
        _SAID.pop(call_id or "", None)


def _was_actually_said(heard: str) -> bool | None:
    """Did the caller really say this. None when we have no transcript to check.

    THE GUARD WAS GAMED, ON A LIVE CALL, WITHIN AN HOUR.

    Requiring evidence before switching language was the right shape and it
    made the evidence worth fabricating. The caller said "Okay then." and the
    desk called

        set_language(code="es", heard="O.K. donde esta el numero de modelo?")

    Nobody said that. It is not English, so the English test passed it; it is
    Latin script, so the alphabet test passed it; it is a whole sentence, so
    the length test passed it. The call switched to Spanish and stayed there.
    
    A claim about what somebody said can be checked against what they said.
    """
    want = [w for w in re.split(r"[^\w']+", (heard or "").lower()) if len(w) > 2]
    if not want:
        return False

    from .trace import here

    with _SAID_LOCK:
        turns = list(_SAID.get(here(), []))
    if not turns:
        return None             # nothing recorded: fall back to the other tests

    spoken = " ".join(turns).lower()
    hit = sum(1 for w in want if w in spoken)
    return hit >= max(1, len(want) // 2)


def _really_that_language(code: str, heard: str):
    """None to allow the switch, or a refusal.

    WHY THIS IS A GUARD AND NOT A LINE IN THE DOCSTRING.

    The docstring used to say "call this the moment somebody answers in
    something other than English". On a live call the caller had mentioned
    Dubai, then said one word the transcriber rendered as "jaye", and the desk
    switched the entire call into Arabic. The customer had to interrupt and
    ask it to speak English again.

    Nothing was broken in the switching. It did what it was told. A single
    unrecognised token is a TRANSCRIPTION FAILURE, and reading it as evidence
    of a language is how a caller loses their own call.

    Worse, the old path then wrote that language onto the contact, so the next
    call would have opened in Arabic too, off the same one bad word.

    A guard rather than better wording, because the wording was already there
    and the model still had every reason to believe it was doing right.
    """
    if not code or code == "en":
        # Going back to English is always allowed. Somebody asking for English
        # is somebody who has already been taken somewhere they did not want.
        return None

    said = (heard or "").strip()
    if not said:
        return {"ok": False,
                "why": "no caller words were given for that switch",
                "say": "Carry on in the language you are already speaking. If "
                       "you genuinely think they want another one, ask them."}

    # FIRST, DID THEY SAY IT. Everything below reasons about the words; this
    # asks whether the words are real.
    truly = _was_actually_said(said)
    if truly is False:
        return {"ok": False,
                "why": f"{said!r} is not what they said on this call",
                "say": "Stay in the language you are speaking. Switch only on "
                       "words they actually used."}

    low = said.lower()
    if any(w in low for w in _ASKING):
        return None

    # PLAIN ENGLISH IS NOT ANOTHER LANGUAGE, however long it is.
    #
    # The first version of this guard only measured length, and on the very
    # next call the desk switched to Arabic on the strength of
    #
    #     heard="How will I confirm it to you?"
    #
    # which is seven words of English. Length was never the point; the point
    # is whether the caller was speaking the language being switched to.
    really = _is_really(code, said)
    if really is False:
        return {"ok": False,
                "why": f"{said!r} does not read as {SPOKEN.get(code, code)}",
                "say": "Stay in the language you are speaking. Switch only "
                       "when they speak another one themselves or ask you to."}

    # A language written in another script cannot arrive as plain ASCII. If
    # every character is a keyboard character, whatever they said, they did
    # not say it in Arabic, Hindi, Russian or Chinese.
    if code in _NOT_LATIN and said.isascii():
        return {"ok": False,
                "why": f"{said!r} is all ASCII, which {SPOKEN.get(code, code)} "
                       "is not written in",
                "say": "Stay in the language you are speaking."}

    # A WHOLE SENTENCE, not a fragment. Three words is the line: below it a
    # transcriber that mangled one word looks identical to a second language.
    if len(said.split()) < 3:
        return {"ok": False,
                "why": f"{said!r} is one fragment, which is more likely a "
                       "mis-transcription than a language",
                "say": "Stay in the language you are speaking. If you are not "
                       "sure what they said, ask them to repeat it. Do not "
                       "change the call over a single word."}

    return None


def set_language(code: str, tool_context, heard: str = "") -> dict:
    """Switch to the language the caller is actually speaking.

    Switch when they have spoken a WHOLE SENTENCE in another language, or when
    they ask you to. The desk opens in English because it cannot know before
    they speak, not because English is the default.

    Args:
        code: two-letter code for what they are speaking, such as "es".
        heard: what the caller actually said that made you call this. Their
            words, not your summary. A switch without it is refused.
    """
    code = (code or "").strip().lower()[:2]


    # ALREADY IN THAT LANGUAGE: DO NOTHING, AND SAY ALMOST NOTHING.
    #
    # THE LEAK THIS IS AIMED AT.
    #
    # On one live call every set_language call put the function-response
    # envelope into the AUDIO. The caller heard, out loud:
    #
    #     response:set_language{status:<ctrl46>Finished<ctrl46>}<ctrl43>
    #     Is there anything else I can help you with?
    #
    # Those are Gemini's own special tokens reaching the speech path, and it
    # happened on eight consecutive switches including this one, a no-op from
    # English to English while already speaking English.
    #
    # The audio is synthesised by the Live model, so there is nothing to
    # filter downstream: by the time it is text it has already been spoken.
    # What CAN be reduced is how often the session is asked to change its own
    # output language mid-turn, and how much text comes back for the model to
    # verbalise when it does.
    #
    # A switch to the language already in use changes nothing and is the
    # cheapest one to stop making.
    try:
        current = (tool_context.state.get("language") or "").strip().lower()
    except Exception:
        current = ""
    if code and code in SPOKEN and code == current:
        return {"ok": True, "language": code, "unchanged": True}

    if code not in SPOKEN:
        # Say what we DO have. The desk was refusing correctly and then
        # inventing its own shorter list out loud: "I can speak English,
        # Spanish and French" on a call, when it is set up for ten.
        others = ", ".join(sorted(v for k, v in SPOKEN.items() if k != "en"))

        # DID THEY ASK, OR DID WE DECIDE. This is the whole difference and the
        # refusal used to assume the first.
        #
        # HEARD TWICE ON ONE CALL. The caller said "The tax" and the
        # transcriber produced Arabic script, so the desk tried Persian. They
        # said "am A Sofa" and it produced "Ähm Ach so," so the desk tried
        # German. Neither language is one this desk speaks, so both were
        # refused, correctly -- and then the model read the refusal out:
        #
        #     "I'm sorry, I don't speak that language."
        #
        # To a customer who was speaking English, had never mentioned a
        # language, and was asking about a sofa. The guard did its job and the
        # WORDING of the guard leaked onto the call, which from the caller's
        # side is indistinguishable from the bug it prevented.
        asked = any(w in (heard or "").lower() for w in _ASKING)
        if not asked:
            return {
                "ok": False,
                "why": f"{code} is not one this desk speaks, and they did "
                       "not ask about languages",
                "say": "SAY NOTHING ABOUT LANGUAGES. They are speaking to you "
                       "in English and have not asked what you speak, so "
                       "there is nothing to apologise for and nothing to "
                       "announce. That was almost certainly a "
                       "mis-transcription. Carry on with what they actually "
                       "asked you, in English, and if you did not catch it, "
                       "ask them to say it again.",
            }

        return {"ok": False,
                "why": f"{code} is not one of the languages this desk has "
                       "been set up for",
                "available": sorted(SPOKEN.values()),
                "say": f"Carry on in English and do not pretend to switch. "
                       f"They asked, so the real answer is English, {others}. "
                       "Do not shorten that list from memory: this is the "
                       "list."}

    # ONLY NOW, once we know this is a language the desk actually speaks and
    # is not the one already in use, is it worth asking whether they really
    # spoke it. Put ahead of those checks, this swallowed the refusal that
    # tells a caller which languages we DO have.
    guard = _really_that_language(code, heard)
    if guard is not None:
        return guard

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
