"""Domains for every project-file field, quoted from ``docs/pipeline-schema.md`` §4.

**This is one of exactly two modules exempt from the numeric-literal scan** in
``tests/unit/test_config_no_literals.py`` (the other is
:mod:`terrafolio.domain.mandate_bounds`). The numbers here are not calibration:
they bound what a well-formed file may say, they never move a result, and every
one of them is quoted from the normative schema document. Everything that *does*
move a result lives in the assumption set.

Both exempt modules are pure constant declarations with no logic, which is what
makes the exemption safe — there is nowhere here for a weight to hide.
"""

from __future__ import annotations

from typing import Final

# --- Identity (§4) -----------------------------------------------------------
SCHEMA_VERSION: Final = "1.0"
ID_PATTERN: Final = r"^[A-Z][A-Za-z0-9_-]{1,31}$"
NAME_MAX_LEN: Final = 120
TEXT_MAX_LEN: Final = 200
NOTE_MAX_LEN: Final = 500

# --- Location (§4.1) ---------------------------------------------------------
COUNTRY_NAME_MAX_LEN: Final = 60
COUNTRY_CODE_PATTERN: Final = r"^[A-Z]{2}$"
ISO3_PATTERN: Final = r"^[A-Z]{3}$"
LATITUDE_ABS_MAX: Final = 90.0
LONGITUDE_ABS_MAX: Final = 180.0

# --- Asset (§4.2) ------------------------------------------------------------
CAPACITY_MW_MAX: Final = 2000.0
COD_YEARS_BEFORE_BASE: Final = 10
NET_CAPACITY_FACTOR_MAX: Final = 1.0
OPEX_PER_KW_YEAR_MAX: Final = 300.0

# --- Revenue (§4.3) ----------------------------------------------------------
SHARE_MAX: Final = 1.0
PRICE_EUR_PER_MWH_MAX: Final = 500.0
PPA_TENOR_YEARS_MAX: Final = 30
CAPTURE_FACTOR_MAX: Final = 2.0

# --- Execution (§4.4) --------------------------------------------------------
DEVELOPMENT_RISK_SCORE_MIN: Final = 1.0
DEVELOPMENT_RISK_SCORE_MAX: Final = 5.0
DEVELOPMENT_RISK_SCORE_DECIMALS: Final = 1

# --- Capital structure (§4.5) ------------------------------------------------
GEARING_MAX: Final = 1.0

# --- Declared assumptions (§4.6) ---------------------------------------------
BASE_YEAR_MIN: Final = 2000
BASE_YEAR_MAX: Final = 2100
TAX_RATE_MAX: Final = 0.6
DEPRECIATION_YEARS_MIN: Final = 1
DEPRECIATION_YEARS_MAX: Final = 40
DEBT_RATE_MAX: Final = 0.25
DEBT_TENOR_YEARS_MAX: Final = 30

# Added by 1A (decisions C-2, C-3, C-4). Declared inputs that participate in no
# tie-out; the dispersion report and the house model's variance report need them.
DEGRADATION_RATE_MAX: Final = 0.1
ESCALATION_MIN: Final = -0.1
ESCALATION_MAX: Final = 0.25
TARGET_DSCR_MIN: Final = 1.0
TARGET_DSCR_MAX: Final = 3.0
