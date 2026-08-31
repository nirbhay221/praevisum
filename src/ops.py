"""The operations surface, now assembled from five focused modules.

This file was 1,346 lines holding scheduling, buying advice, complaints,
restocking and returns. Five unrelated jobs in one place named "ops", which
is the name a file gets when nobody decides what it is for. It became the
file you search rather than the file you read.

Nothing moved out of reach: every name that used to live here still imports
from here, so agents.py, tools.py and the tests did not have to change. The
split is for the person reading it, not for the machine.
"""

from __future__ import annotations

from .scheduling import next_available_slot, hold_slot  # noqa: F401
from .buying import (  # noqa: F401
    recommend_equipment, what_we_know_about, quote_delivery,
    create_purchase_order, confirm_purchase_order, cancel_purchase_order,
    supplier_options,
    note_wishlist, MIN_SAMPLE, _recall_for, _recall_kind,
)
from .feedback import (  # noqa: F401
    register_complaint, complaints_about,
)
from .restock import (  # noqa: F401
    restock_advice, _complaint_demand, REVIEW_DAYS, SERVICE_Z,
    WARNING_WINDOW_DAYS, PREDICTION_ACCURACY,
)
from .returns import (  # noqa: F401
    register_return, returns_about, RETURN_WEIGHT,
)
