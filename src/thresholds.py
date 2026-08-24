"""The numbers this business runs on, in one place so they cannot disagree.

Every value here was argued for somewhere and each is used by more than one
module. Left scattered, the van loading and the restocking could drift apart
and the desk would refuse to stock a part it would happily send out in a van.

Splitting ops.py made this necessary rather than tidy: `MIN_SAMPLE` was needed
by both the buying advice and the complaint reporting, and `RETURN_WEIGHT` by
both returns and recommendations. Importing one from the other would have made
a cycle.
"""

from __future__ import annotations

# ---- scheduling ---------------------------------------------------------
VISIT_MINUTES = 120
BUFFER_MINUTES = 30          # travel and paperwork either side

# ---- how much evidence is enough ----------------------------------------
# Below this many units in service we do not have an opinion worth having. The
# version without it called a machine "recommended" on one install that had not
# broken yet, which is a sample of one wearing a confident sentence.
MIN_SAMPLE = 4

# A return blamed on the machine outweighs a complaint. A complaint is
# annoyance; a return is somebody deciding they would rather have nothing.
RETURN_WEIGHT = 2.0

# ---- stock ---------------------------------------------------------------
# How often the owner is expected to look. Ordering must cover the lead time
# AND this, or the shelf runs dry between reviews however good the arithmetic.
REVIEW_DAYS = 30

# 1.65 standard deviations is a 95% service level: out of stock roughly one
# time in twenty. Not 100%, because covering every spike means holding stock
# that mostly sits still, which is the cost this exists to weigh.
SERVICE_Z = 1.65

# ---- the complaint signal ------------------------------------------------
# How long a complaint stays predictive. Measured on this book, a customer
# raises the grumble about 41 days before the repair closes.
WARNING_WINDOW_DAYS = 120

# How often the corpus, given a complaint's wording, names a part the repair
# actually used. Measured against complaints that genuinely preceded a repair:
# 66%, against roughly 20% for guessing. Applied as a discount, not believed.
PREDICTION_ACCURACY = 0.66
