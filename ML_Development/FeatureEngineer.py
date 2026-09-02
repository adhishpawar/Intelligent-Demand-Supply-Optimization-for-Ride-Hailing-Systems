"""
==============================================================================
FeatureEngineer.py  —  FR-01: Demand Prediction  |  MLDLC Phase 5
==============================================================================

PURPOSE
-------
Transform the cleaned trip-level DataFrame into a zone-level, time-windowed
feature matrix that the LightGBM model will consume.

INPUT  : trips_cleaned.csv  (one row = one trip)
OUTPUT : features.csv        (one row = one zone × 15-min window)

CORE CONCEPT: The Feature Matrix
---------------------------------
The demand model needs to answer:
  "How many rides will be requested in zone Z during window W?"

To answer that, we aggregate individual trips into counts per (zone, window)
and enrich each row with:
  - What time is it?       → temporal features
  - What happened before?  → lag features (autocorrelation)
  - Any trends?            → rolling mean features
  - External signals?      → weather / event scores

FEATURE CATEGORIES
------------------
1. TEMPORAL FEATURES
   - hour, day_of_week, month, is_weekend, is_peak_hour
   - WHY: Demand is strongly periodic. Rush-hour Monday != 3am Sunday.
   - ENCODING: Direct integers — LightGBM handles ordinal values natively.

2. LAG FEATURES  (autocorrelation exploitation)
   - demand_lag1  = demand 15 min ago  (t-1)
   - demand_lag2  = demand 30 min ago  (t-2)
   - demand_lag4  = demand 60 min ago  (t-4)
   - demand_lag8  = demand 120 min ago (t-8)
   - WHY: "Past demand is the strongest predictor of future demand."
          demand_lag1 typically has r > 0.85 with target.
   - CRITICAL: Always grouped by zone_id before shifting — otherwise
               you leak zone 5's history into zone 3's lag features!

3. ROLLING MEAN FEATURES  (trend smoothing)
   - demand_roll3  = mean of last 3 windows  (45 min trend)
   - demand_roll8  = mean of last 8 windows  (2 hr trend)
   - WHY: Reduces noise from single-window spikes. "Is demand going
          UP or DOWN in this zone right now?"
   - Always computed on shifted data (shift(1)) to prevent leakage.

4. CYCLIC ENCODING
   - sin/cos transform of hour and day_of_week
   - WHY: hour=23 and hour=0 are adjacent, but naive encoding makes
          them look 23 apart. sin/cos maps them to neighbors on a circle.
   - Formula: sin_hour = sin(2π × hour / 24)

5. EXTERNAL FEATURES
   - weather_score : 0.0–1.0 (1.0 = heavy rain)
   - event_score   : 0.0–1.0 (1.0 = major stadium event nearby)
   - WHY: Rain → +30% demand spike. Events → predictable surges.
   - If API data unavailable → filled with 0.0 (neutral/no effect).

LEAKAGE PREVENTION (CRITICAL)
------------------------------
Never let information from the future flow into training features.
  ✓ CORRECT : lag features use .shift(1) (one window behind target)
  ✗ WRONG   : computing rolling mean without shift includes current window

TIME-BASED SPLIT (not random split)
-------------------------------------
Data: Jan 2015 – Mar 2016 → Train: Jan–Dec 2015, Test: Jan–Mar 2016
WHY: With time series, random split allows the model to see Feb data
     and predict Jan — which is impossible in production.
==============================================================================
"""

import logging
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
WINDOW_FREQ   = "15min"     # demand aggregation granularity
LAG_WINDOWS   = [1, 2, 4, 8]       # 15, 30, 60, 120 min lags
ROLL_WINDOWS  = [3, 8]              # 45 min, 2 hr rolling means
PEAK_HOURS    = [7, 8, 9, 17, 18, 19]
TEST_DAYS     = 90                  # last 90 days held out for test


class FeatureEngineer:
    """
    Builds the final feature matrix for FR-01 demand model.

    Usage
    -----
    >>> fe  = FeatureEngineer()
    >>> df  = fe.run("Data_Processing/trips_cleaned.csv")
    >>> df.to_csv("Data_Processing/features.csv", index=False)
    """

    def run(
        self,
        trips_csv: str,
        weather_csv: str = None,
        event_csv: str   = None,
    ) -> pd.DataFrame:
        """
        Full feature engineering pipeline.
        Returns time-windowed zone-level feature matrix.
        """
        logger.info("Step 1/5 : Aggregating demand into 15-min windows …")
        df = self._aggregate_demand(trips_csv)

        logger.info("Step 2/5 : Adding temporal features …")
        df = self._add_temporal(df)

        logger.info("Step 3/5 : Adding lag & rolling features …")
        df = self._add_lag_features(df)

        logger.info("Step 4/5 : Adding cyclic encoding …")
        df = self._add_cyclic(df)

        logger.info("Step 5/5 : Adding external features …")
        df = self._add_external(df, weather_csv, event_csv)

        df = df.dropna().reset_index(drop=True)
        logger.info(f"Feature matrix shape: {df.shape}")
        logger.info(f"Columns: {list(df.columns)}")
        return df

    def time_based_split(self, df: pd.DataFrame, test_days: int = TEST_DAYS):
        """
        Temporal train/test split.

        LOGIC
        -----
        split_point = max_date − test_days
        train = all rows BEFORE split_point
        test  = all rows ON/AFTER split_point

        WHY NOT RANDOM SPLIT?
        - Random split allows training on Feb 2016, testing on Jan 2015.
        - In production the model only sees past data → simulate that.
        - Time-based split gives realistic generalization estimate.
        """
        split_point = df["window_start"].max() - pd.Timedelta(days=test_days)
        train = df[df["window_start"] <= split_point].copy()
        test  = df[df["window_start"] >  split_point].copy()
        logger.info(
            f"Train: {len(train):,} rows  |  Test: {len(test):,} rows  "
            f"|  Split at: {split_point.date()}"
        )
        return train, test

    # ──────────────────────────────────────────────────────────
    # PRIVATE STEPS
    # ──────────────────────────────────────────────────────────

    def _aggregate_demand(self, trips_csv: str) -> pd.DataFrame:
        """
        Group trips into (zone_id × 15-min window) demand counts.

        LOGIC
        -----
        trips_cleaned has one row per trip. We need:
          demand = number of ride requests per zone per 15-min window.

        Steps:
        1. Set request_time as index
        2. Group by zone_id
        3. Resample at 15T → count trip_ids within each window
        4. Rename count → 'demand'
        5. Fill gaps (windows with 0 rides) with 0

        RESAMPLE vs GROUPBY+FLOOR
        -------------------------
        pd.Grouper(freq='15T') is cleaner and handles DST transitions.
        """
        df = pd.read_csv(trips_csv, parse_dates=["request_time"], low_memory=False)

        demand = (
            df.set_index("request_time")
            .groupby("zone_id")
            .resample(WINDOW_FREQ)["trip_id"]
            .count()
            .reset_index()
            .rename(columns={"trip_id": "demand", "request_time": "window_start"})
        )

        # Fill truly empty windows (no trips at all) with 0
        demand["demand"] = demand["demand"].fillna(0).astype(int)
        demand = demand.sort_values(["zone_id", "window_start"]).reset_index(drop=True)

        logger.info(
            f"Aggregated → {len(demand):,} (zone, window) rows  "
            f"| Zones: {demand['zone_id'].nunique()}  "
            f"| Date range: {demand['window_start'].min().date()} "
            f"→ {demand['window_start'].max().date()}"
        )
        return demand

    def _add_temporal(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extract time-based features from window_start timestamp.

        FEATURES & RATIONALE
        --------------------
        hour         : 0-23. Demand peaks at rush hours 7-9am, 5-7pm.
        day_of_week  : 0=Mon … 6=Sun. Weekdays vs weekends differ massively.
        month        : 1-12. Seasonal effects (summer vs winter).
        is_weekend   : Binary flag. Simpler than day_of_week for tree splits.
        is_peak_hour : Binary flag for morning/evening rush hours.
                       Explicitly telling the model about known high-demand
                       periods speeds up learning.
        quarter      : 1-4. Coarser seasonal signal.
        week_of_year : 1-52. Even finer seasonality.
        """
        ts = df["window_start"]
        df = df.copy()

        df["hour"]          = ts.dt.hour
        df["day_of_week"]   = ts.dt.dayofweek          # 0=Mon, 6=Sun
        df["month"]         = ts.dt.month
        df["quarter"]       = ts.dt.quarter
        df["week_of_year"]  = ts.dt.isocalendar().week.astype(int)
        df["is_weekend"]    = (df["day_of_week"] >= 5).astype(int)
        df["is_peak_hour"]  = df["hour"].isin(PEAK_HOURS).astype(int)

        # Window index within day (0-95 for 15-min windows)
        df["window_of_day"] = ts.dt.hour * 4 + ts.dt.minute // 15

        return df

    def _add_lag_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Create lag and rolling-mean features.

        ── LAG FEATURES ──────────────────────────────────────────
        lag_k means: the demand k windows ago in THIS zone.

        Formula: demand_lag{k} = demand shifted by k within zone_id group.

        CRITICAL: .groupby("zone_id") before .shift(k)
          Without this:
            zone=0 window[t] sees zone=1 window[t-1] — WRONG!
          With this:
            zone=0 window[t] sees zone=0 window[t-k] — CORRECT!

        ── ROLLING MEAN FEATURES ─────────────────────────────────
        demand_roll{k} = mean of last k lag-1 values (shifted first!)

        Formula: grouped_demand.shift(1).rolling(k).mean()
          shift(1) prevents leakage of current window into the mean.

        ── WHY THESE SPECIFIC LAGS? ─────────────────────────────
        lag1  (15 min) : Immediate past — strongest predictor (r~0.85)
        lag2  (30 min) : Short-term momentum
        lag4  (60 min) : 1-hour pattern (recurring commuter behavior)
        lag8  (2 hrs)  : Captures slow patterns (stadium event winding up)
        roll3 (45 min) : Smoothed short-term trend
        roll8 (2 hrs)  : Medium-term trend direction
        """
        df = df.sort_values(["zone_id", "window_start"]).copy()
        grouped = df.groupby("zone_id")["demand"]

        # Lag features
        for k in LAG_WINDOWS:
            df[f"demand_lag{k}"] = grouped.shift(k)

        # Rolling mean features (shift(1) prevents current-window leakage)
        for w in ROLL_WINDOWS:
            df[f"demand_roll{w}"] = (
                grouped.transform(lambda x: x.shift(1).rolling(w, min_periods=1).mean())
            )

        # Rolling std (volatility signal)
        df["demand_std3"] = (
            grouped.transform(lambda x: x.shift(1).rolling(3, min_periods=1).std())
        )

        # Zone-level mean demand (static feature — historical baseline)
        zone_mean = df.groupby("zone_id")["demand"].transform("mean")
        df["zone_mean_demand"] = zone_mean

        return df

    def _add_cyclic(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Cyclic encoding for hour and day_of_week.

        PROBLEM: Standard encoding treats hour=23 and hour=0 as far apart.
                 But 11 PM and midnight are adjacent — the model should know this.

        SOLUTION: Map to a circle using sin and cos.
          sin_hour = sin(2π × hour / 24)
          cos_hour = cos(2π × hour / 24)

          Now hour=0 and hour=23 are close together on the unit circle.

        WHY BOTH sin AND cos? One function alone is ambiguous:
          sin(30°) = sin(150°) but cos(30°) ≠ cos(150°).
          Together they uniquely identify every point on the circle.
        """
        df = df.copy()

        # Hour cycle (24 hours)
        df["sin_hour"] = np.sin(2 * np.pi * df["hour"] / 24)
        df["cos_hour"] = np.cos(2 * np.pi * df["hour"] / 24)

        # Day-of-week cycle (7 days)
        df["sin_dow"]  = np.sin(2 * np.pi * df["day_of_week"] / 7)
        df["cos_dow"]  = np.cos(2 * np.pi * df["day_of_week"] / 7)

        return df

    def _add_external(
        self, df: pd.DataFrame,
        weather_csv: str = None,
        event_csv: str   = None
    ) -> pd.DataFrame:
        """
        Merge weather and event scores.

        WEATHER SCORE LOGIC
        -------------------
        If weather API data available (weather_csv):
          - rain_1h > 5mm  → score = 0.8  (heavy rain boosts demand ~25%)
          - rain_1h > 1mm  → score = 0.5  (light rain)
          - temp < 5°C     → score += 0.2 (cold → people avoid walking)
          - temp > 35°C    → score += 0.15 (heat → people avoid walking)
          - Clear sky      → score = 0.0

        If not available → fill with 0.0 (neutral, no weather signal).

        EVENT SCORE LOGIC
        -----------------
        If event data available:
          major event (concert, sports) within 2 km → score = 0.8
          small event → 0.3
        Else → 0.0
        """
        df = df.copy()
        df["weather_score"] = 0.0
        df["event_score"]   = 0.0

        if weather_csv and __import__("os").path.exists(weather_csv):
            weather = pd.read_csv(weather_csv, parse_dates=["window_start"])
            df = df.merge(
                weather[["zone_id", "window_start", "weather_score"]],
                on=["zone_id", "window_start"],
                how="left",
                suffixes=("", "_ext")
            )
            if "weather_score_ext" in df.columns:
                df["weather_score"] = df["weather_score_ext"].fillna(0.0)
                df.drop(columns=["weather_score_ext"], inplace=True)
            else:
                df["weather_score"] = df["weather_score"].fillna(0.0)

        if event_csv and __import__("os").path.exists(event_csv):
            events = pd.read_csv(event_csv, parse_dates=["window_start"])
            df = df.merge(
                events[["zone_id", "window_start", "event_score"]],
                on=["zone_id", "window_start"],
                how="left",
                suffixes=("", "_ext")
            )
            if "event_score_ext" in df.columns:
                df["event_score"] = df["event_score_ext"].fillna(0.0)
                df.drop(columns=["event_score_ext"], inplace=True)
            else:
                df["event_score"] = df["event_score"].fillna(0.0)

        return df

    @staticmethod
    def get_feature_columns() -> list:
        """
        Returns the canonical list of feature columns used by the model.
        Centralised here so train.py, app.py, and tests all use the same list.
        """
        return [
            "zone_id",
            "hour", "day_of_week", "month", "quarter", "week_of_year",
            "is_weekend", "is_peak_hour", "window_of_day",
            "demand_lag1", "demand_lag2", "demand_lag4", "demand_lag8",
            "demand_roll3", "demand_roll8", "demand_std3",
            "zone_mean_demand",
            "sin_hour", "cos_hour", "sin_dow", "cos_dow",
            "weather_score", "event_score",
        ]


# ──────────────────────────────────────────────────────────────────────────────
# STANDALONE RUN
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    fe = FeatureEngineer()
    df = fe.run(
        trips_csv   = "Data_Processing/trips_cleaned.csv",
        weather_csv = "Data_Processing/weather_scores.csv",
        event_csv   = "Data_Processing/event_scores.csv",
    )
    df.to_csv("Data_Processing/features.csv", index=False)
    logger.info("FeatureEngineer complete. Output → Data_Processing/features.csv")

    train, test = fe.time_based_split(df)
    train.to_csv("Data_Processing/train.csv", index=False)
    test.to_csv("Data_Processing/test.csv",  index=False)
    logger.info("Train/test split saved.")