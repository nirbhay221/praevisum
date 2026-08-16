"""Distance. Dispatch is skills AND proximity, not skills alone.

A qualified technician ninety minutes away is not a better answer than a
qualified technician fifteen minutes away, and until this existed the system
could not tell the difference.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

_EARTH_MI = 3958.8


def miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return round(2 * _EARTH_MI * asin(sqrt(a)), 1)


def drive_minutes(distance_mi: float) -> int:
    """Rough road time. 32 mph average plus 6 minutes of getting in and out.

    Deliberately crude and deliberately honest about being crude: the point is
    ordering technicians correctly, not predicting arrival to the minute.
    """
    return int(round(distance_mi / 32 * 60)) + 6
