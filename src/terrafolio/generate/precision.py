"""How precisely a generated figure is quoted.

These are not tolerances, bands or rates — nothing here changes a result. They
are the number of decimal places a field is *written* at, so that a generated
pipeline reads like one an analyst would produce: a nameplate in tenths of a
megawatt, a site at centroid precision, a contracted share to the percent.

They live together, in one place, because they are one kind of thing and because
the numeric-literal guard costs an escape per line. Spending four on four
one-line modules would say the same thing four times.
"""

from __future__ import annotations

__all__ = ["CAPACITY_PLACES", "COORDINATE_PLACES", "RISK_PLACES", "SHARE_PLACES"]

# Coordinates to a site centroid (§8 says that is sufficient for the map),
# nameplate to a tenth of a megawatt, contracted share to the percent, and the
# development risk score to the one decimal §4.4 states it at.
COORDINATE_PLACES, CAPACITY_PLACES, SHARE_PLACES, RISK_PLACES = 2, 1, 2, 1  # structural: quoting
