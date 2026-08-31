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

    # WHAT THE CALLER HEARS. One number, one desk, several vendors behind it.
    #
    # This was built the other way round for most of its life: two numbers,
    # two companies, and a customer who had to know which one to ring. That
    # produced a desk saying "we do not sell laptops" to its own customer, and
    # then a hand-off, and then a call transfer, all of which are elaborate
    # answers to a problem that only existed because the front was split.
    #
    # The vendors are still separate underneath. Their stock, technicians,
    # rates, warranties and repair corpora never mix. The caller simply does
    # not have to care which one they need.
    front_name: str = os.getenv("PRAEVISUM_FRONT_NAME", "Riverbend Appliance")

    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    public_ws_base: str = os.getenv("PUBLIC_WS_BASE", "")

    # The number outbound calls and messages come FROM. Inbound never needed
    # one, because Twilio dials us; the moment anything goes out, it does.
    twilio_from: str = os.getenv("TWILIO_FROM", "")

    # WHATSAPP DOES NOT SEND FROM OUR NUMBER, AND ASSUMING IT DID WAS A BUG.
    #
    # whatsapp.py built its sender as f"whatsapp:{twilio_from}". On the Twilio
    # sandbox that is wrong: the sandbox sends from Twilio's own shared number
    # and our number is not a WhatsApp sender at all, so every reply would have
    # been rejected. Inbound would have worked, which is the worst shape of
    # failure: the desk reads the message, does the work, and the answer never
    # arrives.
    #
    # An approved WhatsApp Business sender later WOULD be our own number, so
    # this falls back to it rather than hardcoding the sandbox.
    twilio_whatsapp_from: str = (os.getenv("TWILIO_WHATSAPP_FROM", "")
                                 or os.getenv("TWILIO_FROM", ""))

    # MAIL. A2P 10DLC blocks US business SMS from this number, so every SMS
    # reply the desk sent came back error 30034 undelivered, and a technician
    # cannot share a phone number with a customer because desk.py routes on
    # exactly that. Email gives the crew an identity of their own.
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")

    # Required on any COMMERCIAL email under CAN-SPAM, which a review request
    # is. email_out refuses to send one without both rather than trusting the
    # copy to remember.
    postal_address: str = os.getenv("PRAEVISUM_POSTAL_ADDRESS", "")
    unsubscribe_to: str = os.getenv("PRAEVISUM_UNSUBSCRIBE", "")


settings = Settings()

# Twilio media streams are 8 kHz mu-law. Gemini Live wants 16 kHz PCM in and
# emits 24 kHz PCM out. Those three numbers drive telephony/audio.py.
TWILIO_RATE = 8000
GEMINI_IN_RATE = 16000
GEMINI_OUT_RATE = 24000
