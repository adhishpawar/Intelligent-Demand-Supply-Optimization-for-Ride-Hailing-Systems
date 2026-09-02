"""
Canonical feature contract for the FR-01 demand model.

WHY THIS MODULE EXISTS
----------------------
The pre-production code had two independent definitions of "what features the
demand model consumes":

  * ML_Development/FeatureEngineer.get_feature_columns()  -> 23 columns
  * Inference/app.py, building its dict by hand           -> 14 columns,
                                                             and it spelled
                                                             `is_peak_hour` as
                                                             `is_peak`

That is textbook training/serving skew. Training would fit on 23 ordered
columns; serving would hand LightGBM 14 differently-named ones. The model
either raises on shape mismatch or -- worse, if the count happened to line up --
silently scores garbage, because column i at serving time is not column i at
training time.

The fix is not "remember to keep them in sync". The fix is that there is only
one definition, both paths import it, and a test asserts the artifact's stored
contract matches this module. Everything else in the ML package builds on
FEATURE_COLUMNS below rather than restating it.

ORDERING IS PART OF THE CONTRACT
--------------------------------
LightGBM binds features positionally once fitted. Re-ordering FEATURE_COLUMNS
is therefore a breaking model change, not a cosmetic edit: a model trained
before the change will mis-score after it. CONTRACT_VERSION exists to make that
break loud -- bump it on any change to the tuple, and `verify_contract` will
refuse to serve an artifact trained against a different version.
"""

from __future__ import annotations

from typing import Final

# Bump on ANY change to FEATURE_COLUMNS (add/remove/rename/reorder).
# Artifacts store the version they were trained with; serving refuses a mismatch.
CONTRACT_VERSION: Final[str] = "fr01-v1"

TARGET_COLUMN: Final[str] = "demand"

# The spatial/temporal key of a row. Not features -- they identify the row.
KEY_COLUMNS: Final[tuple[str, ...]] = ("zone_id", "window_start")

# Aggregation granularity. Lag/rolling window semantics below are all expressed
# in multiples of this, so changing it invalidates every lag feature's meaning.
WINDOW_FREQ: Final[str] = "15min"
WINDOW_MINUTES: Final[int] = 15

PEAK_HOURS: Final[frozenset[int]] = frozenset({7, 8, 9, 17, 18, 19})

# Lags are in units of WINDOW_MINUTES: lag1=15min ago, lag8=2h ago.
LAG_WINDOWS: Final[tuple[int, ...]] = (1, 2, 4, 8)
ROLL_WINDOWS: Final[tuple[int, ...]] = (3, 8)

# ---------------------------------------------------------------------------
# THE contract. Order is significant (see module docstring).
# ---------------------------------------------------------------------------
FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    # identity / spatial
    "zone_id",
    # calendar
    "hour",
    "day_of_week",
    "month",
    "quarter",
    "week_of_year",
    "is_weekend",
    "is_peak_hour",
    "window_of_day",
    # autoregressive
    "demand_lag1",
    "demand_lag2",
    "demand_lag4",
    "demand_lag8",
    "demand_roll3",
    "demand_roll8",
    "demand_std3",
    "zone_mean_demand",
    # cyclic encodings
    "sin_hour",
    "cos_hour",
    "sin_dow",
    "cos_dow",
    # external signals
    "weather_score",
    "event_score",
)

N_FEATURES: Final[int] = len(FEATURE_COLUMNS)

# Features a caller may supply at request time. Everything else is derived from
# the timestamp or looked up from the feature store, so accepting them from an
# untrusted caller would let a client fabricate model input.
CLIENT_SUPPLIABLE: Final[frozenset[str]] = frozenset(
    {
        "demand_lag1",
        "demand_lag2",
        "demand_lag4",
        "demand_lag8",
        "demand_roll3",
        "demand_roll8",
        "demand_std3",
        "zone_mean_demand",
        "weather_score",
        "event_score",
    }
)

# Neutral values used when a signal genuinely is not available. These are the
# documented degraded-mode defaults from the design doc's NFR table: a missing
# weather feed means "no weather effect", never "skip the prediction".
FEATURE_DEFAULTS: Final[dict[str, float]] = {
    "demand_lag1": 0.0,
    "demand_lag2": 0.0,
    "demand_lag4": 0.0,
    "demand_lag8": 0.0,
    "demand_roll3": 0.0,
    "demand_roll8": 0.0,
    "demand_std3": 0.0,
    "zone_mean_demand": 0.0,
    "weather_score": 0.0,
    "event_score": 0.0,
}


class ContractMismatchError(RuntimeError):
    """Raised when a model artifact was trained against a different contract."""


def verify_contract(artifact_version: str, artifact_columns: list[str] | None = None) -> None:
    """Fail loudly if an artifact does not match the contract this build serves.

    Called at model load time rather than at first prediction, so a bad deploy
    fails at startup/readiness instead of silently scoring wrong for hours.
    """
    if artifact_version != CONTRACT_VERSION:
        raise ContractMismatchError(
            f"model artifact was trained against contract {artifact_version!r}, "
            f"but this build serves {CONTRACT_VERSION!r}. Retrain or deploy the "
            f"matching build -- serving across a contract change scores garbage."
        )
    if artifact_columns is not None and tuple(artifact_columns) != FEATURE_COLUMNS:
        expected = list(FEATURE_COLUMNS)
        raise ContractMismatchError(
            "model artifact feature columns do not match the contract.\n"
            f"  artifact: {artifact_columns}\n"
            f"  expected: {expected}"
        )
