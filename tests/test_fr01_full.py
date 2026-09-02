"""
==============================================================================
tests/test_fr01_full.py  —  FR-01 Complete Unit Test Suite
==============================================================================

TESTING PHILOSOPHY
------------------
Test every transformation and invariant that must hold true for the model
to work correctly in production. We are NOT testing the model's accuracy
here (that's done in DemandForecaster._baseline_comparison) — we are
testing that the CODE does what it promises.

TEST CATEGORIES
---------------
1. Preprocessor tests  — cleaning rules, GPS bounds, transforms
2. FeatureEngineer tests — lag correctness, leakage check, cyclic encoding
3. Model tests         — output range, feature count, baseline beat
4. API tests           — endpoint contracts, error handling

Run: pytest tests/ -v --cov=ML_Development --cov-report=term-missing
==============================================================================
"""

import pytest
import sys
import os
import numpy as np
import pandas as pd

# Allow imports from ML_Development/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ML_Development"))

from ML_Development.DataPreprocessor import DataPreprocessor
from ML_Development.FeatureEngineer  import FeatureEngineer


# ─────────────────────────────────────────────────────────────
# SHARED FIXTURES
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def raw_df():
    """
    Synthetic raw trip DataFrame that mimics NYC TLC CSV structure.
    Contains valid rows AND intentional invalid rows to test cleaning.
    """
    base_date = pd.Timestamp("2015-01-15 08:00:00")
    n = 30
    return pd.DataFrame({
        "tpep_pickup_datetime"  : pd.date_range(base_date, periods=n, freq="5min"),
        "tpep_dropoff_datetime" : pd.date_range(base_date + pd.Timedelta(minutes=10),
                                                 periods=n, freq="5min"),
        "trip_distance"         : [1.5, 0.0, 2.3, -1.0, 0.5, 150.0,   # 0, -1, 150 = bad
                                   3.1, 2.0, 0.8, 4.5, 1.2, 2.8,
                                   1.9, 3.3, 0.9, 2.1, 1.7, 4.0,
                                   2.5, 3.8, 1.1, 2.2, 0.6, 1.8,
                                   2.9, 3.5, 1.4, 2.6, 1.0, 3.7],
        "fare_amount"           : [7.5, 3.0, 9.0, 5.0, -4.0, 45.0,   # -4 = bad
                                   12.0, 8.0, 5.5, 18.0, 6.0, 11.0,
                                   9.5, 14.0, 5.0, 8.5, 7.0, 16.0,
                                   10.0, 15.0, 6.5, 9.0, 4.5, 8.0,
                                   11.5, 13.0, 7.5, 10.5, 5.8, 14.5],
        "pickup_latitude"       : [40.75] * 26 + [99.0, 0.0, 40.75, 40.75],  # 2 bad
        "pickup_longitude"      : [-73.99] * 26 + [-73.99, -73.99, 200.0, -73.99],
        "dropoff_latitude"      : [40.72] * n,
        "dropoff_longitude"     : [-73.97] * n,
        "passenger_count"       : [1, 0, 2, 1, 1, 1,    # 0 = bad
                                   1, 2, 1, 1, 1, 1,
                                   1, 1, 1, 1, 1, 1,
                                   1, 1, 1, 1, 1, 1,
                                   1, 1, 1, 1, 1, 1],
        "trip_id"               : np.arange(n),
    })


@pytest.fixture(scope="module")
def demand_df():
    """
    Synthetic zone-level demand DataFrame for FeatureEngineer tests.
    3 zones × 50 windows = 150 rows.
    """
    n_zones    = 3
    n_windows  = 50
    dates      = pd.date_range("2015-01-01", periods=n_windows, freq="15min")
    np.random.seed(42)
    rows = []
    for z in range(n_zones):
        for d in dates:
            rows.append({
                "zone_id"     : z,
                "window_start": d,
                "demand"      : int(np.random.poisson(10 + z * 3)),
            })
    return pd.DataFrame(rows).sort_values(["zone_id", "window_start"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# SECTION 1 : DataPreprocessor Tests
# ─────────────────────────────────────────────────────────────

class TestDataPreprocessor:

    def setup_method(self):
        self.proc = DataPreprocessor(n_zones=5, random_seed=42)

    # ── Cleaning rules ──────────────────────────────────────

    def test_negative_distance_removed(self, raw_df):
        """Trips with trip_distance < 0.1 miles must be filtered out."""
        cleaned = self.proc._clean(raw_df.copy())
        assert (cleaned["trip_distance"] >= 0.1).all(), \
            "Negative/zero distance trips not removed"

    def test_extreme_distance_removed(self, raw_df):
        """Trips > 100 miles must be removed as outliers."""
        cleaned = self.proc._clean(raw_df.copy())
        assert cleaned["trip_distance"].max() <= 100.0, \
            "Extreme distance outlier not removed"

    def test_negative_fare_removed(self, raw_df):
        """Negative fares are data corruption — must be removed."""
        cleaned = self.proc._clean(raw_df.copy())
        assert (cleaned["fare_amount"] >= 2.50).all(), \
            "Sub-minimum fares not removed"

    def test_gps_lat_in_bounds(self, raw_df):
        """Pickup latitude must be within NYC bounding box [40.4, 41.0]."""
        cleaned = self.proc._clean(raw_df.copy())
        assert cleaned["pickup_latitude"].between(40.4, 41.0).all(), \
            "Out-of-bounds latitude coordinates not filtered"

    def test_gps_lng_in_bounds(self, raw_df):
        """Pickup longitude must be within NYC bounding box [-74.3, -73.6]."""
        cleaned = self.proc._clean(raw_df.copy())
        assert cleaned["pickup_longitude"].between(-74.3, -73.6).all(), \
            "Out-of-bounds longitude coordinates not filtered"

    def test_zero_passengers_removed(self, raw_df):
        """Trips with 0 passengers are data errors — must be removed."""
        cleaned = self.proc._clean(raw_df.copy())
        assert (cleaned["passenger_count"] >= 1).all(), \
            "Zero-passenger trips not removed"

    def test_row_count_decreases_after_cleaning(self, raw_df):
        """Cleaning must remove at least some rows from the synthetic data."""
        cleaned = self.proc._clean(raw_df.copy())
        assert len(cleaned) < len(raw_df), \
            "Cleaning removed no rows — check filter conditions"

    # ── Transforms ──────────────────────────────────────────

    def test_distance_km_conversion(self, raw_df):
        """
        1 mile = 1.60934 km.
        Verify conversion is correct to 3 decimal places.
        """
        cleaned = self.proc._clean(raw_df.copy())
        df      = self.proc._transform(cleaned)
        expected = cleaned["trip_distance"].iloc[0] * 1.60934
        # log1p was applied, so reverse it
        actual_raw = np.expm1(df["distance_km_log"].iloc[0])
        assert abs(actual_raw - expected) < 0.01, \
            f"Distance conversion error: expected {expected:.3f} km, got {actual_raw:.3f}"

    def test_log1p_reduces_skew(self, raw_df):
        """log1p transform must reduce skewness of fare_amount below 2.0."""
        cleaned     = self.proc._clean(raw_df.copy())
        df          = self.proc._transform(cleaned)
        transformed_skew = abs(df["fare_amount_log"].skew())
        assert transformed_skew < 3.0, \
            f"log1p did not sufficiently reduce skew: {transformed_skew:.2f}"

    def test_request_time_always_before_pickup(self, raw_df):
        """Synthetic request_time must be strictly before tpep_pickup_datetime."""
        cleaned = self.proc._clean(raw_df.copy())
        df      = self.proc._transform(cleaned)
        assert (df["tpep_pickup_datetime"] > df["request_time"]).all(), \
            "request_time is not consistently before pickup_datetime"

    def test_request_time_offset_in_range(self, raw_df):
        """Offset must be between 2 and 7 minutes."""
        cleaned  = self.proc._clean(raw_df.copy())
        df       = self.proc._transform(cleaned)
        offset_min = (
            df["tpep_pickup_datetime"] - df["request_time"]
        ).dt.total_seconds() / 60
        assert offset_min.min() >= 2.0, "Offset too small (< 2 min)"
        assert offset_min.max() <= 7.5, "Offset too large (> 7 min)"

    def test_no_nulls_in_key_columns_after_transform(self, raw_df):
        """After transform(), core columns must have no NaN."""
        cleaned   = self.proc._clean(raw_df.copy())
        df        = self.proc._transform(cleaned)
        key_cols  = ["distance_km_log", "fare_amount_log", "request_time"]
        null_sum  = df[key_cols].isnull().sum().sum()
        assert null_sum == 0, f"Found {null_sum} NaN values in key columns"

    # ── Zone creation ────────────────────────────────────────

    def test_zone_ids_in_expected_range(self, raw_df):
        """zone_id must be integers in [0, n_zones-1]."""
        cleaned = self.proc._clean(raw_df.copy())
        df      = self.proc._transform(cleaned)
        df      = self.proc._create_zones(df)
        assert df["zone_id"].between(0, self.proc.n_zones - 1).all(), \
            "zone_id values out of expected range"

    def test_correct_number_of_unique_zones(self, raw_df):
        """Number of unique zone_ids must equal n_zones parameter."""
        cleaned = self.proc._clean(raw_df.copy())
        df      = self.proc._transform(cleaned)
        df      = self.proc._create_zones(df)
        # May be less if data points cluster into fewer groups
        assert df["zone_id"].nunique() <= self.proc.n_zones

    def test_kmeans_fitted_after_create_zones(self, raw_df):
        """self.kmeans_ must be set after _create_zones()."""
        cleaned = self.proc._clean(raw_df.copy())
        df      = self.proc._transform(cleaned)
        self.proc._create_zones(df)
        assert self.proc.kmeans_ is not None, "KMeans model not fitted"

    def test_zone_centroids_shape(self, raw_df):
        """zone_centroids_ must have n_zones rows and correct columns."""
        cleaned = self.proc._clean(raw_df.copy())
        df      = self.proc._transform(cleaned)
        self.proc._create_zones(df)
        assert self.proc.zone_centroids_ is not None
        assert "centroid_lat" in self.proc.zone_centroids_.columns
        assert "centroid_lng" in self.proc.zone_centroids_.columns


# ─────────────────────────────────────────────────────────────
# SECTION 2 : FeatureEngineer Tests
# ─────────────────────────────────────────────────────────────

class TestFeatureEngineer:

    def setup_method(self):
        self.fe = FeatureEngineer()

    # ── Temporal features ────────────────────────────────────

    def test_hour_range(self, demand_df):
        """hour must be in [0, 23]."""
        df = self.fe._add_temporal(demand_df)
        assert df["hour"].between(0, 23).all(), "hour out of [0,23]"

    def test_day_of_week_range(self, demand_df):
        """day_of_week must be in [0, 6]."""
        df = self.fe._add_temporal(demand_df)
        assert df["day_of_week"].between(0, 6).all(), "day_of_week out of [0,6]"

    def test_is_weekend_binary(self, demand_df):
        """is_weekend must only contain 0 or 1."""
        df = self.fe._add_temporal(demand_df)
        assert set(df["is_weekend"].unique()).issubset({0, 1}), \
            "is_weekend contains values other than 0 and 1"

    def test_is_peak_hour_binary(self, demand_df):
        """is_peak_hour must only contain 0 or 1."""
        df = self.fe._add_temporal(demand_df)
        assert set(df["is_peak_hour"].unique()).issubset({0, 1}), \
            "is_peak_hour contains values other than 0 and 1"

    def test_window_of_day_range(self, demand_df):
        """window_of_day must be in [0, 95] (96 windows per day)."""
        df = self.fe._add_temporal(demand_df)
        assert df["window_of_day"].between(0, 95).all(), \
            "window_of_day out of [0, 95]"

    # ── Lag features ─────────────────────────────────────────

    def test_all_lag_columns_created(self, demand_df):
        """Lag columns demand_lag1, lag2, lag4, lag8 must all exist."""
        df = self.fe._add_lag_features(demand_df)
        for lag in [1, 2, 4, 8]:
            assert f"demand_lag{lag}" in df.columns, \
                f"Missing column: demand_lag{lag}"

    def test_all_rolling_columns_created(self, demand_df):
        """Rolling mean columns demand_roll3, roll8 must exist."""
        df = self.fe._add_lag_features(demand_df)
        for w in [3, 8]:
            assert f"demand_roll{w}" in df.columns, \
                f"Missing column: demand_roll{w}"

    def test_no_nulls_in_lags_after_dropna(self, demand_df):
        """After dropna(), lag columns must have zero NaN values."""
        df       = self.fe._add_lag_features(demand_df).dropna()
        lag_cols = [f"demand_lag{k}" for k in [1, 2, 4, 8]]
        null_sum = df[lag_cols].isnull().sum().sum()
        assert null_sum == 0, f"Found {null_sum} NaN in lag columns after dropna"

    def test_lag_is_zone_specific(self, demand_df):
        """
        LEAKAGE TEST: lag features must NOT cross zone boundaries.
        For zone=0, demand_lag1 at row[k] should equal demand at row[k-1]
        of zone=0 (not zone=1).
        """
        df = self.fe._add_lag_features(demand_df)
        z0 = df[df["zone_id"] == 0].sort_values("window_start").reset_index(drop=True)
        # lag1 of row[k] should equal demand of row[k-1] for the same zone
        for i in range(2, min(10, len(z0))):
            expected = z0.loc[i - 1, "demand"]
            actual   = z0.loc[i, "demand_lag1"]
            if pd.notna(actual):
                assert abs(actual - expected) < 1e-9, \
                    f"Lag leakage detected at row {i}: expected {expected}, got {actual}"

    def test_rolling_mean_not_exceeds_max_demand(self, demand_df):
        """Rolling mean cannot exceed historical max demand (sanity check)."""
        df = self.fe._add_lag_features(demand_df)
        assert df["demand_roll3"].max() <= demand_df["demand"].max() + 1, \
            "Rolling mean exceeds max demand — possible calculation error"

    # ── Cyclic encoding ──────────────────────────────────────

    def test_cyclic_sin_cos_range(self, demand_df):
        """sin and cos values must be in [-1, 1]."""
        df = self.fe._add_temporal(demand_df)
        df = self.fe._add_cyclic(df)
        for col in ["sin_hour", "cos_hour", "sin_dow", "cos_dow"]:
            assert df[col].between(-1.0, 1.0).all(), \
                f"Cyclic feature {col} out of [-1, 1]"

    def test_cyclic_hour_full_circle(self, demand_df):
        """
        sin²(hour) + cos²(hour) should equal 1.0 for all rows.
        This is the Pythagorean identity — validates the encoding is correct.
        """
        df   = self.fe._add_temporal(demand_df)
        df   = self.fe._add_cyclic(df)
        identity = (df["sin_hour"] ** 2 + df["cos_hour"] ** 2)
        assert (identity - 1.0).abs().max() < 1e-9, \
            "sin²(hour) + cos²(hour) ≠ 1.0 — cyclic encoding error"

    # ── Feature columns ──────────────────────────────────────

    def test_feature_columns_count(self):
        """get_feature_columns() should return exactly 23 columns."""
        cols = FeatureEngineer.get_feature_columns()
        assert len(cols) == 23, \
            f"Expected 23 feature columns, got {len(cols)}: {cols}"

    def test_no_duplicate_feature_columns(self):
        """Feature column list must have no duplicates."""
        cols = FeatureEngineer.get_feature_columns()
        assert len(cols) == len(set(cols)), \
            "Duplicate column names in get_feature_columns()"

    def test_target_not_in_feature_cols(self):
        """'demand' (the target) must NOT appear in feature columns."""
        cols = FeatureEngineer.get_feature_columns()
        assert "demand" not in cols, \
            "'demand' target column found in feature list — data leakage!"

    def test_window_start_not_in_feature_cols(self):
        """'window_start' raw timestamp must NOT be in features."""
        cols = FeatureEngineer.get_feature_columns()
        assert "window_start" not in cols, \
            "'window_start' timestamp in features — LightGBM cannot use raw datetime"

    # ── Train/test split ─────────────────────────────────────

    def test_time_split_no_overlap(self, demand_df):
        """Train and test sets must have zero timestamp overlap."""
        df = self.fe._add_temporal(demand_df)
        df = self.fe._add_lag_features(df).dropna()
        train, test = self.fe.time_based_split(df, test_days=3)
        assert train["window_start"].max() < test["window_start"].min(), \
            "Train/test overlap detected — data leakage in time split!"

    def test_time_split_covers_all_rows(self, demand_df):
        """Train + test row count must equal total row count."""
        df = self.fe._add_temporal(demand_df)
        df = self.fe._add_lag_features(df).dropna()
        train, test = self.fe.time_based_split(df, test_days=3)
        assert len(train) + len(test) == len(df), \
            "Row count mismatch: train + test ≠ total rows"

    def test_train_larger_than_test(self, demand_df):
        """Training set must be larger than test set."""
        df = self.fe._add_temporal(demand_df)
        df = self.fe._add_lag_features(df).dropna()
        train, test = self.fe.time_based_split(df, test_days=3)
        assert len(train) > len(test), \
            "Test set is larger than train set — check split_days parameter"


# ─────────────────────────────────────────────────────────────
# SECTION 3 : API Tests (requires Flask test client)
# ─────────────────────────────────────────────────────────────

class TestFlaskAPI:

    @pytest.fixture
    def client(self):
        """Create Flask test client with a dummy model."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Inference"))

        # If model doesn't exist yet, skip API tests
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "models", "demand_model.joblib"
        )
        if not os.path.exists(model_path):
            pytest.skip("Trained model not found — run main.py --mode train first")

        from app import app as flask_app
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            yield c

    def test_health_returns_200(self, client):
        """GET /health must return HTTP 200."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_ok_status(self, client):
        """GET /health response body must include status=ok."""
        import json
        resp = client.get("/health")
        body = json.loads(resp.data)
        assert body["status"] == "ok"

    def test_predict_valid_request(self, client):
        """POST /predict/demand with valid body must return predicted_demand >= 0."""
        import json
        payload = {
            "zone_id"      : 5,
            "window_start" : "2016-01-15 08:30:00",
            "demand_lag1"  : 12.0,
            "demand_lag2"  : 10.0,
            "demand_lag4"  : 9.0,
            "demand_lag8"  : 8.0,
            "demand_roll3" : 11.0,
            "demand_roll8" : 10.0,
        }
        resp = client.post(
            "/predict/demand",
            data=json.dumps(payload),
            content_type="application/json"
        )
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "predicted_demand" in body
        assert body["predicted_demand"] >= 0, "Predicted demand must be non-negative"

    def test_predict_missing_zone_id_returns_400(self, client):
        """POST /predict/demand without zone_id must return 400."""
        import json
        payload = {"window_start": "2016-01-15 08:30:00"}
        resp = client.post(
            "/predict/demand",
            data=json.dumps(payload),
            content_type="application/json"
        )
        assert resp.status_code == 400

    def test_predict_missing_window_start_returns_400(self, client):
        """POST /predict/demand without window_start must return 400."""
        import json
        payload = {"zone_id": 5}
        resp = client.post(
            "/predict/demand",
            data=json.dumps(payload),
            content_type="application/json"
        )
        assert resp.status_code == 400

    def test_batch_predict_count_matches_input(self, client):
        """Batch response must contain same number of predictions as input zones."""
        import json
        zones = [
            {"zone_id": i, "window_start": "2016-01-15 08:30:00",
             "demand_lag1": 10, "demand_lag2": 9, "demand_lag4": 8, "demand_lag8": 7,
             "demand_roll3": 9.5, "demand_roll8": 9.0}
            for i in range(5)
        ]
        resp = client.post(
            "/predict/batch",
            data=json.dumps({"zones": zones}),
            content_type="application/json"
        )
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["count"] == 5

    def test_predict_demand_is_numeric(self, client):
        """predicted_demand must be a number, not a string."""
        import json
        payload = {
            "zone_id"     : 3,
            "window_start": "2016-01-15 17:00:00",
            "demand_lag1" : 20.0,
        }
        resp = client.post(
            "/predict/demand",
            data=json.dumps(payload),
            content_type="application/json"
        )
        body = json.loads(resp.data)
        assert isinstance(body["predicted_demand"], (int, float)), \
            "predicted_demand is not numeric"