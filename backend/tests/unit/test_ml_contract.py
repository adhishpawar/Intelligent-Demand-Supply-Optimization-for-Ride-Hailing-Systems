"""
Tests for the defects the audit found (ML-02, ML-03, ML-08) and the guard
rails added to prevent them recurring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml.contract import CONTRACT_VERSION, FEATURE_COLUMNS, verify_contract, ContractMismatchError
from app.ml.features import (
    add_autoregressive_features,
    add_calendar_features,
    assemble_frame,
    assemble_row,
    calendar_features,
    time_based_split,
)


# ---------------------------------------------------------------------------
# ML-02: training/serving skew
# ---------------------------------------------------------------------------
def test_contract_has_exactly_23_columns_in_a_fixed_order():
    """Regression pin for the original defect: 23 trained vs 14 served. If
    this number changes, CONTRACT_VERSION must change with it (see docstring
    in contract.py) -- this test forces that to be a deliberate act.
    """
    assert len(FEATURE_COLUMNS) == 23
    assert FEATURE_COLUMNS[0] == "zone_id"
    assert "is_peak_hour" in FEATURE_COLUMNS
    assert "is_peak" not in FEATURE_COLUMNS  # the original misspelling


def test_verify_contract_accepts_matching_version_and_columns():
    verify_contract(CONTRACT_VERSION, list(FEATURE_COLUMNS))  # must not raise


def test_verify_contract_rejects_wrong_version():
    with pytest.raises(ContractMismatchError):
        verify_contract("some-other-version", list(FEATURE_COLUMNS))


def test_verify_contract_rejects_reordered_columns():
    reordered = list(reversed(FEATURE_COLUMNS))
    with pytest.raises(ContractMismatchError):
        verify_contract(CONTRACT_VERSION, reordered)


def test_assemble_frame_orders_columns_per_contract_regardless_of_input_order():
    scrambled = {c: [1] for c in reversed(FEATURE_COLUMNS)}
    df = pd.DataFrame(scrambled)
    result = assemble_frame(df)
    assert list(result.columns) == list(FEATURE_COLUMNS)


def test_assemble_frame_raises_on_missing_column():
    df = pd.DataFrame({c: [1] for c in FEATURE_COLUMNS if c != "zone_id"})
    with pytest.raises(ValueError, match="missing contract columns"):
        assemble_frame(df)


def test_assemble_row_uses_neutral_defaults_when_no_autoregressive_data():
    """Serving-time equivalent of the original '/predict/demand always got 0
    for every lag feature' behaviour -- except now it's an explicit, tested
    default rather than an accident of what the caller happened to send.
    """
    row = assemble_row(zone_id=5, window_start=pd.Timestamp("2024-06-01 08:00:00"))
    assert list(row.columns) == list(FEATURE_COLUMNS)
    assert row["zone_id"].iloc[0] == 5
    assert row["demand_lag1"].iloc[0] == 0.0
    assert row["weather_score"].iloc[0] == 0.0


def test_calendar_features_scalar_matches_vectorised():
    """If someone edits one of calendar_features/add_calendar_features without
    the other, this catches the divergence immediately -- the exact class of
    bug that produced ML-02 in the first place.
    """
    ts = pd.Timestamp("2024-03-15 18:30:00")  # Friday evening rush
    scalar = calendar_features(ts)

    df = pd.DataFrame({"window_start": [ts]})
    vectorised = add_calendar_features(df).iloc[0].to_dict()

    for key, value in scalar.items():
        assert vectorised[key] == pytest.approx(value), f"mismatch on {key}"


# ---------------------------------------------------------------------------
# ML-03: target leakage in zone_mean_demand
# ---------------------------------------------------------------------------
def test_zone_mean_demand_excludes_future_and_current_window():
    """The original bug: `groupby('zone_id').transform('mean')` let every row
    see the WHOLE series including the future. This proves the fixed version
    (`shift(1).expanding().mean()`) for the first window of a zone has no
    peek: its mean must equal exactly the previous window's value, not an
    average that includes itself.
    """
    windows = pd.date_range("2024-01-01", periods=5, freq="15min")
    df = pd.DataFrame({"zone_id": [1] * 5, "window_start": windows, "demand": [10, 20, 30, 999, 999]})

    result = add_autoregressive_features(df)
    row2 = result[result["window_start"] == windows[2]].iloc[0]  # demand=30, sees [10,20] before it

    # Mean of PRIOR windows only (10, 20) = 15 -- must not include 30 (itself)
    # or the 999s that come after it.
    assert row2["zone_mean_demand"] == pytest.approx(15.0)


def test_lag_features_are_zone_scoped_not_leaked_across_zones():
    """`groupby('zone_id').shift(k)` must never let zone B's history flow into
    zone A's lag feature -- the original code did do this correctly, but it's
    load-bearing enough (a genuine cross-zone data leak) to pin explicitly.
    """
    windows = pd.date_range("2024-01-01", periods=3, freq="15min")
    df = pd.DataFrame(
        {
            "zone_id": [0, 0, 0, 1, 1, 1],
            "window_start": list(windows) * 2,
            "demand": [100, 200, 300, 1, 2, 3],
        }
    )
    result = add_autoregressive_features(df).sort_values(["zone_id", "window_start"])

    zone0_last = result[(result.zone_id == 0) & (result.window_start == windows[2])].iloc[0]
    zone1_last = result[(result.zone_id == 1) & (result.window_start == windows[2])].iloc[0]

    assert zone0_last["demand_lag1"] == 200  # zone 0's own history
    assert zone1_last["demand_lag1"] == 2  # zone 1's own history, not 200


# ---------------------------------------------------------------------------
# ML-08: silent empty-training-set split
# ---------------------------------------------------------------------------
def test_time_based_split_rejects_horizon_larger_than_data_span():
    windows = pd.date_range("2024-01-01", periods=10, freq="D")
    df = pd.DataFrame({"window_start": windows, "demand": range(10)})
    with pytest.raises(ValueError, match="test_days=30"):
        time_based_split(df, test_days=30)


def test_time_based_split_produces_both_nonempty_sides():
    windows = pd.date_range("2024-01-01", periods=30, freq="D")
    df = pd.DataFrame({"window_start": windows, "demand": range(30)})
    train, test = time_based_split(df, test_days=5)
    assert len(train) > 0
    assert len(test) > 0
    assert train["window_start"].max() <= test["window_start"].min()


def test_time_based_split_rejects_empty_frame():
    with pytest.raises(ValueError, match="empty"):
        time_based_split(pd.DataFrame(columns=["window_start", "demand"]), test_days=1)
