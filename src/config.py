"""Settings. Everything env-driven so nothing is hardcoded per deployment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "praevisum"


@dataclass(frozen=True)
class Settings:
    live_model: str = os.getenv(
        "PRAEVISUM_LIVE_MODEL", "gemini-live-2.5-flash-preview-native-audio"
    )
    # Gemini 3.x is served from the `global` Vertex endpoint, not regional.
    worker_model: str = os.getenv("PRAEVISUM_WORKER_MODEL", "gemini-3.5-flash")
    # judgment rather than lookup: what to buy, weighing cost against our own
    # failure record
    advisor_model: str = os.getenv("PRAEVISUM_ADVISOR_MODEL", "gemini-3.6-flash")
    # structured form filling, cheapest thing that can do it
    simple_model: str = os.getenv("PRAEVISUM_SIMPLE_MODEL", "gemini-3.5-flash-lite")
    dealer_name: str = os.getenv("PRAEVISUM_DEALER_NAME", "the service desk")

    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    public_ws_base: str = os.getenv("PUBLIC_WS_BASE", "")


settings = Settings()

# Twilio media streams are 8 kHz mu-law. Gemini Live wants 16 kHz PCM in and
# emits 24 kHz PCM out. Those three numbers drive telephony/audio.py.
TWILIO_RATE = 8000
GEMINI_IN_RATE = 16000
GEMINI_OUT_RATE = 24000
