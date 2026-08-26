"""RoutingProvider port (PLAN §3.2 Adapter pattern) - used only off the matching hot path:
rider-facing ETA display, route polyline, final-fare distance. Tonight's adapter,
`HeuristicRoutingProvider`, is real math (haversine + winding + speed profile), not a
network call - a genuine OSRM/Google adapter is a second class implementing the same
Protocol, a drop-in with no call-site changes (DEBT-03).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from libs.geo.cities import SpeedProfile
from libs.geo.eta import FastEtaEstimator
from libs.geo.haversine import bearing_deg, haversine_m


@dataclass(frozen=True)
class Route:
    distance_m: float
    duration_s: float
    polyline: list[tuple[float, float]]  # [(lat, lng), ...] - straight-line tonight


class RoutingProvider(Protocol):
    async def route(
        self, origin: tuple[float, float], dest: tuple[float, float], at: datetime
    ) -> Route: ...


class HeuristicRoutingProvider:
    """Real math, no external call. Interpolates a straight-line polyline between origin
    and dest so the map UI has something to draw even without a road-network provider."""

    def __init__(self, speed_profile: SpeedProfile, polyline_points: int = 12) -> None:
        self._eta = FastEtaEstimator(speed_profile)
        self._points = max(2, polyline_points)

    async def route(
        self, origin: tuple[float, float], dest: tuple[float, float], at: datetime
    ) -> Route:
        lat1, lng1 = origin
        lat2, lng2 = dest
        distance_m = self._eta.distance_m(lat1, lng1, lat2, lng2)
        duration_s = self._eta.eta_seconds(lat1, lng1, lat2, lng2, at)
        polyline = [
            (
                lat1 + (lat2 - lat1) * i / (self._points - 1),
                lng1 + (lng2 - lng1) * i / (self._points - 1),
            )
            for i in range(self._points)
        ]
        return Route(distance_m=distance_m, duration_s=duration_s, polyline=polyline)


def initial_bearing(origin: tuple[float, float], dest: tuple[float, float]) -> float:
    return bearing_deg(origin[0], origin[1], dest[0], dest[1])


def straight_line_m(origin: tuple[float, float], dest: tuple[float, float]) -> float:
    return haversine_m(origin[0], origin[1], dest[0], dest[1])
