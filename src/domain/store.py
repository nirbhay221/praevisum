"""Storage behind an interface.

Day one this is in-memory so the audio loop can be built without cloud
dependencies. Firestore drops in behind the same methods later - that swap is
the only thing that changes, and no agent or tool touches storage directly.
"""

from __future__ import annotations

from .models import Customer, Part, Repair, Technician, Unit, WorkOrder


class Store:
    def __init__(self) -> None:
        self.customers: dict[str, Customer] = {}
        self.units: dict[str, Unit] = {}
        self.parts: dict[str, Part] = {}
        self.technicians: dict[str, Technician] = {}
        self.repairs: list[Repair] = []
        self.work_orders: dict[str, WorkOrder] = {}
        # SKU -> work order that has claimed it. The commitment keeper reads this.
        self.reservations: dict[str, str] = {}

    # ---- lookups -------------------------------------------------------

    def customer_by_phone(self, phone: str) -> Customer | None:
        return next((c for c in self.customers.values() if c.phone == phone), None)

    def units_for_customer(self, customer_id: str) -> list[Unit]:
        return [u for u in self.units.values() if u.customer_id == customer_id]

    def repairs_for_model(self, manufacturer: str, model: str) -> list[Repair]:
        return [
            r for r in self.repairs
            if r.manufacturer.lower() == manufacturer.lower()
            and r.model.lower() == model.lower()
        ]

    def repairs_for_unit(self, serial: str) -> list[Repair]:
        return sorted(
            (r for r in self.repairs if r.serial == serial),
            key=lambda r: r.closed_on,
            reverse=True,
        )

    def parts_fitting(self, model: str) -> list[Part]:
        return [p for p in self.parts.values() if any(model.startswith(f) for f in p.fits)]

    def available(self, sku: str) -> int:
        """On-hand minus anything already claimed by another work order."""
        part = self.parts.get(sku)
        if part is None:
            return 0
        claimed = sum(1 for s in self.reservations if s == sku)
        return max(0, part.on_hand - claimed)

    # ---- mutations -----------------------------------------------------

    def reserve(self, sku: str, work_order_id: str) -> bool:
        if self.available(sku) <= 0:
            return False
        self.reservations[sku] = work_order_id
        return True

    def release(self, sku: str) -> None:
        self.reservations.pop(sku, None)


STORE = Store()
