"""
Regression test for ML-04: `/predict/batch` built `pd.DataFrame([[dict]])` --
a 1x1 frame holding a dict object -- and could never score correctly. This
proves the replacement builds one correctly-shaped frame per zone and returns
one result per input zone, using a fake registry so it exercises the service's
batching logic without needing a real trained model.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.config import Settings
from app.ml.contract import FEATURE_COLUMNS
from app.ml.service import DemandForecastService


class _FakeModel:
    """Returns each row's zone_id as its prediction -- deterministic and
    lets the test assert a 1:1, correctly-ordered mapping input->output.
    """

    def predict(self, frame: pd.DataFrame):
        assert list(frame.columns) == list(FEATURE_COLUMNS)
        return frame["zone_id"].to_numpy(dtype=float)


class _FakeRegistry:
    def __init__(self) -> None:
        self._model = _FakeModel()

    def get_model(self):
        return self._model

    @property
    def metadata(self):
        class _M:
            model_version = "fake-v1"

        return _M()


def test_predict_many_scores_every_zone_with_one_call_and_correct_shape():
    service = DemandForecastService(_FakeRegistry(), Settings())
    zone_ids = [3, 7, 11, 19]
    window = pd.Timestamp("2024-05-01 09:00:00")

    results = service.predict_many(zone_ids=zone_ids, window_start=window)

    assert [r["zone_id"] for r in results] == zone_ids
    # The fake model echoes zone_id -- proves each row really carries its own
    # zone's features, not a shared/garbled 1x1 frame.
    assert [r["predicted_demand"] for r in results] == [float(z) for z in zone_ids]


def test_predict_many_caps_at_max_batch_zones():
    settings = Settings(max_batch_zones=2)
    service = DemandForecastService(_FakeRegistry(), settings)
    results = service.predict_many(zone_ids=[1, 2, 3, 4, 5], window_start=pd.Timestamp("2024-01-01"))
    assert len(results) == 2


def test_predict_many_empty_input_returns_empty_list():
    service = DemandForecastService(_FakeRegistry(), Settings())
    assert service.predict_many(zone_ids=[], window_start=pd.Timestamp("2024-01-01")) == []


def test_predict_one_clamps_negative_predictions_to_zero():
    class _NegativeModel:
        def predict(self, frame):
            return [-5.0] * len(frame)

    class _NegRegistry(_FakeRegistry):
        def get_model(self):
            return _NegativeModel()

    service = DemandForecastService(_NegRegistry(), Settings())
    result = service.predict_one(zone_id=1, window_start=pd.Timestamp("2024-01-01"))
    assert result["predicted_demand"] == 0.0
