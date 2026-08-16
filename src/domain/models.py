"""The operational objects. Brand-agnostic on purpose.

Nothing here knows what a Traulsen is. The engine keys on
(manufacturer, model, symptom) and everything brand-specific lives in data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    phone: str          # E.164
    address: str
    channel_pref: str   # "sms" | "whatsapp" | "email"
    lat: float = 0.0
    lon: float = 0.0


@dataclass(frozen=True)
class Unit:
    """A specific piece of equipment at a specific customer."""

    serial: str
    customer_id: str
    manufacturer: str
    model: str
    family: str         # "walk-in cooler", "reach-in freezer", "ice machine"
    installed: str      # ISO date
    location_note: str  # "kitchen, back wall"


@dataclass(frozen=True)
class Part:
    sku: str
    name: str
    fits: tuple[str, ...]     # model prefixes this part fits
    on_hand: int
    lead_time_days: int       # if not on hand
    unit_cost: float


@dataclass(frozen=True)
class Technician:
    id: str
    name: str
    phone: str
    skills: tuple[str, ...]   # families they're qualified on
    home_base: str
    van_stock: tuple[str, ...]  # SKUs already in the van
    lat: float = 0.0
    lon: float = 0.0


@dataclass(frozen=True)
class Repair:
    """A closed visit. This is the corpus the briefing is built from.

    `parts_consumed` is deliberately distinct from parts ordered - what the
    technician actually fitted is the signal, not what someone requisitioned.
    """

    id: str
    serial: str
    manufacturer: str
    model: str
    reported_symptom: str
    error_code: str | None
    found_cause: str
    parts_consumed: tuple[str, ...]
    labor_hours: float
    closed_on: str            # ISO date
    technician_id: str


@dataclass
class WorkOrder:
    """The artifact. Created live on the call, closed by the technician.

    The close is not bookkeeping. What the technician writes when they finish
    is the only record of what the fault *actually* was, and it is the input to
    every future briefing on this model. The loop lives here.
    """

    id: str
    customer_id: str
    serial: str
    reported_symptom: str
    error_code: str | None = None
    promised_window: str | None = None
    technician_id: str | None = None
    parts_reserved: list[str] = field(default_factory=list)
    status: str = "open"      # open | promised | briefed | at_risk | closed
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    events: list[str] = field(default_factory=list)

    # --- filled in at close, by the person who actually fixed it ---
    found_cause: str | None = None
    parts_consumed: list[str] = field(default_factory=list)
    labor_hours: float | None = None
    tech_note: str | None = None
    first_visit_fix: bool | None = None
    closed_on: str | None = None

    # --- what was said, kept because the caller's words are how the next
    #     caller will describe the same fault ---
    call_transcript: str | None = None

    def log(self, msg: str) -> None:
        self.events.append(f"{datetime.now().isoformat(timespec='seconds')} {msg}")
