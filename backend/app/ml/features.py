"""
Feature construction for the FR-01 demand model.

ONE BUILDER, TWO CALLERS
------------------------
Training builds features over a whole history at once (vectorised, grouped by
zone). Serving builds a single row for one (zone, window). The pre-production
code implemented these twice, in two files, and they disagreed -- see
`contract.py` for what that cost.

Here both paths end at `assemble_row`/`assemble_frame`, which are the only
functions permitted to decide column order, and they take that order from
`contract.FEATURE_COLUMNS`. The calendar features are computed by the same
`calendar_features()` in both paths, so a change to (say) the definition of
`is_peak_hour` cannot land in training without also landing in serving.

WHAT IS *NOT* HERE
------------------
Lag/rolling values. At training time they come from the historical series; at
serving time they must come from a feature store lookup (recent demand for that
zone), because the caller cannot be trusted to supply their own model input.
`assemble_row` therefore takes them as an explicit argument -- it does not
invent them, and it does not read the request body.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.contract import (
    FEATURE_COLUMNS,
    FEATURE_DEFAULTS,
    LAG_WINDOWS,
    PEAK_HOURS,
    ROLL_WINDOWS,
    TARGET_COLUMN,
    WINDOW_FREQ,
)


# ---------------------------------------------------------------------------
# Calendar features -- pure function of a timestamp, identical in both paths
# ---------------------------------------------------------------------------
def calendar_features(ts: pd.Timestamp) -> dict[str, float]:
    """Derive every time-only feature from a single window-start timestamp."""
    hour = int(ts.hour)
    dow = int(ts.dayofweek)  # 0=Mon .. 6=Sun
    return {
        "hour": hour,
        "day_of_week": dow,
        "month": int(ts.month),
        "quarter": int(ts.quarter),
        "week_of_year": int(ts.isocalendar().week),
        "is_weekend": int(dow >= 5),
        "is_peak_hour": int(hour in PEAK_HOURS),
        "window_of_day": hour * 4 + int(ts.minute) // 15,
        "sin_hour": float(np.sin(2 * np.pi * hour / 24)),
        "cos_hour": float(np.cos(2 * np.pi * hour / 24)),
        "sin_dow": float(np.sin(2 * np.pi * dow / 7)),
        "cos_dow": float(np.cos(2 * np.pi * dow / 7)),
    }


def add_calendar_features(df: pd.DataFrame, ts_col: str = "window_start") -> pd.DataFrame:
    """Vectorised equivalent of `calendar_features` for the training path.

    Kept deliberately adjacent to the scalar version: if you change one, the
    unit test `test_calendar_features_scalar_matches_vectorised` fails until you
    change the other.
    """
    ts = df[ts_col]
    out = df.copy()
    out["hour"] = ts.dt.hour.astype(int)
    out["day_of_week"] = ts.dt.dayofweek.astype(int)
    out["month"] = ts.dt.month.astype(int)
    out["quarter"] = ts.dt.quarter.astype(int)
    out["week_of_year"] = ts.dt.isocalendar().week.astype(int)
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(int)
    out["is_peak_hour"] = out["hour"].isin(PEAK_HOURS).astype(int)
    out["window_of_day"] = ts.dt.hour * 4 + ts.dt.minute // 15
    out["sin_hour"] = np.sin(2 * np.pi * out["hour"] / 24)
    out["cos_hour"] = np.cos(2 * np.pi * out["hour"] / 24)
    out["sin_dow"] = np.sin(2 * np.pi * out["day_of_week"] / 7)
    out["cos_dow"] = np.cos(2 * np.pi * out["day_of_week"] / 7)
    return out


# ---------------------------------------------------------------------------
# Autoregressive features -- training path (whole series available)
# ---------------------------------------------------------------------------
def add_autoregressive_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag / rolling / volatility features, grouped by zone.

    LEAKAGE RULES, ENFORCED HERE RATHER THAN DOCUMENTED ELSEWHERE
    -------------------------------------------------------------
    * every lag is `groupby(zone_id).shift(k)` -- never a bare shift, which
      would bleed the tail of zone 3 into the head of zone 4;
    * every rolling statistic is computed on `.shift(1)` first, so the window
      being predicted is never part of its own input;
    * `zone_mean_demand` is an expanding mean over *past* windows only, for the
      same reason. The pre-production version used a whole-series
      `transform("mean")`, which is a full-history leak: the training row for
      08:00 on day 1 knew the mean demand of day 90.
    """
    out = df.sort_values(["zone_id", "window_start"]).copy()
    grouped = out.groupby("zone_id")[TARGET_COLUMN]

    for k in LAG_WINDOWS:
        out[f"demand_lag{k}"] = grouped.shift(k)

    for w in ROLL_WINDOWS:
        out[f"demand_roll{w}"] = grouped.transform(
            lambda s, _w=w: s.shift(1).rolling(_w, min_periods=1).mean()
        )

    out["demand_std3"] = grouped.transform(
        lambda s: s.shift(1).rolling(3, min_periods=2).std()
    )

    # Expanding (past-only) zone baseline. min_periods=1 so the first window of
    # a zone gets its own value rather than NaN-ing the whole row away.
    out["zone_mean_demand"] = grouped.transform(
        lambda s: s.shift(1).expanding(min_periods=1).mean()
    )

    return out


def add_external_features(
    df: pd.DataFrame,
    weather: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Left-join external signals, defaulting to the neutral value when absent.

    Absent external data is a normal operating state (the design doc's degraded
    mode), not an error: a missing weather feed must not stop forecasting.
    """
    out = df.copy()
    out["weather_score"] = FEATURE_DEFAULTS["weather_score"]
    out["event_score"] = FEATURE_DEFAULTS["event_score"]

    for frame, col in ((weather, "weather_score"), (events, "event_score")):
        if frame is None or frame.empty:
            continue
        if not {"zone_id", "window_start", col}.issubset(frame.columns):
            raise ValueError(f"external frame for {col} missing required columns")
        merged = out.merge(
            frame[["zone_id", "window_start", col]],
            on=["zone_id", "window_start"],
            how="left",
            suffixes=("", "_ext"),
        )
        ext = f"{col}_ext"
        if ext in merged.columns:
            merged[col] = merged[ext].fillna(FEATURE_DEFAULTS[col])
            merged = merged.drop(columns=[ext])
        out = merged

    return out


def aggregate_demand(
    trips: pd.DataFrame,
    request_time_col: str = "request_time",
    id_col: str = "trip_id",
) -> pd.DataFrame:
    """Collapse trip rows into (zone_id, window_start) demand counts.

    Gap-filling matters: a window with no rides is a real observation of zero
    demand, not a missing row. Resampling per zone and filling with 0 keeps the
    series contiguous, which is what makes `shift(k)` mean "k windows ago"
    rather than "k *rows* ago, whenever those happened to be".
    """
    if trips.empty:
        return pd.DataFrame(columns=["zone_id", "window_start", TARGET_COLUMN])

    demand = (
        trips.set_index(request_time_col)
        .groupby("zone_id")
        .resample(WINDOW_FREQ)[id_col]
        .count()
        .rename(TARGET_COLUMN)
        .reset_index()
        .rename(columns={request_time_col: "window_start"})
    )
    demand[TARGET_COLUMN] = demand[TARGET_COLUMN].fillna(0).astype(int)
    return demand.sort_values(["zone_id", "window_start"]).reset_index(drop=True)


def build_training_frame(
    trips: pd.DataFrame,
    weather: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Full training-side pipeline: trips -> model-ready feature matrix."""
    demand = aggregate_demand(trips)
    demand = add_calendar_features(demand)
    demand = add_autoregressive_features(demand)
    demand = add_external_features(demand, weather=weather, events=events)
    return demand


# ---------------------------------------------------------------------------
# Assembly -- the single place column order is decided
# ---------------------------------------------------------------------------
def assemble_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Project a feature frame onto the contract, in contract order."""
    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"feature frame is missing contract columns: {missing}")
    return df.loc[:, list(FEATURE_COLUMNS)]


def assemble_row(
    zone_id: int,
    window_start: pd.Timestamp,
    autoregressive: dict[str, float] | None = None,
    external: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build exactly one model-input row, in contract order.

    `autoregressive` is expected to come from the feature store (recent demand
    for this zone), and `external` from the weather/event providers. Anything
    not supplied falls back to its documented neutral default rather than
    raising -- a stale feature store degrades prediction quality, but the
    design doc requires it degrade rather than fail.
    """
    values: dict[str, float] = dict(FEATURE_DEFAULTS)
    values.update(calendar_features(window_start))
    values["zone_id"] = int(zone_id)
    if autoregressive:
        values.update({k: v for k, v in autoregressive.items() if k in FEATURE_DEFAULTS})
    if external:
        values.update({k: v for k, v in external.items() if k in FEATURE_DEFAULTS})

    row = pd.DataFrame([[values[c] for c in FEATURE_COLUMNS]], columns=list(FEATURE_COLUMNS))
    return row.astype("float64")


def time_based_split(
    df: pd.DataFrame, test_days: int, ts_col: str = "window_start"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological train/test split, with a guard against an empty train set.

    The pre-production version computed `max_date - test_days` and returned
    whatever fell out. When the data span was shorter than `test_days` that
    silently produced a ZERO-ROW training set and a full test set, and training
    would then fail somewhere much later with an unrelated-looking error. Since
    a caller can reasonably pass a test horizon larger than a short backfill,
    this raises with the actual numbers instead.
    """
    if df.empty:
        raise ValueError("cannot split an empty feature frame")

    span_days = (df[ts_col].max() - df[ts_col].min()).total_seconds() / 86400.0
    if test_days >= span_days:
        raise ValueError(
            f"test_days={test_days} but the data only spans {span_days:.2f} days; "
            f"the training set would be empty. Use a shorter horizon or more data."
        )

    split_point = df[ts_col].max() - pd.Timedelta(days=test_days)
    train = df[df[ts_col] <= split_point].copy()
    test = df[df[ts_col] > split_point].copy()
    if train.empty or test.empty:
        raise ValueError(
            f"time split produced an empty side (train={len(train)}, test={len(test)})"
        )
    return train, test
