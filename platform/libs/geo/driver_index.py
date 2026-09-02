"""The Redis driver geo-index (PLAN §3.2 Repository pattern; PLAN A6/B4 amendments).
Shared by the Location service (which owns writes: ingest pings, status toggles) and
the Matching service (which owns reads: candidate retrieval) — both need direct Redis
access on their hot paths, so this lives in `libs`, not behind an HTTP hop (PLAN A1:
the matching hot path must never perform network I/O beyond its own Redis call).

Two atomic Lua scripts are the entire correctness story here:

`_INGEST_SCRIPT` (fixes A6 out-of-order pings AND B4-3 zombie resurrection in one
mechanism): a ping only ever touches the geo index if the driver's CURRENT status in
the hash is ONLINE — checked and acted on atomically, so a ping that arrives after an
offline toggle can never re-add the driver, no matter how late it lands. A ping older
than the last-accepted one (out-of-order delivery) is rejected before it can move the
driver backwards on the map.

`_SET_STATUS_SCRIPT` (fixes B4-2 torn state): status change and geo-set membership
change happen in the same EVAL, so a crash between them is impossible by construction.
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

_INGEST_SCRIPT = """
local status = redis.call('HGET', KEYS[1], 'status')
if status ~= 'ONLINE' then
    return 0
end
local last_ping_ts = tonumber(redis.call('HGET', KEYS[1], 'last_ping_ts')) or 0
local ping_ts = tonumber(ARGV[5])
if ping_ts < last_ping_ts then
    return -1
end
redis.call('HSET', KEYS[1], 'lat', ARGV[1], 'lng', ARGV[2], 'heading', ARGV[3],
           'speed_kmh', ARGV[4], 'last_ping_ts', ARGV[5])
redis.call('GEOADD', KEYS[2], ARGV[2], ARGV[1], ARGV[6])
return 1
"""

_SET_STATUS_SCRIPT = """
local seq = redis.call('HINCRBY', KEYS[1], 'state_seq', 1)
redis.call('HSET', KEYS[1], 'status', ARGV[1], 'city_id', ARGV[3])
if ARGV[4] ~= '' then
    redis.call('HSET', KEYS[1], 'current_trip_id', ARGV[4])
else
    redis.call('HDEL', KEYS[1], 'current_trip_id')
end
if ARGV[1] ~= 'ONLINE' then
    redis.call('ZREM', KEYS[2], ARGV[2])
    redis.call('SREM', KEYS[3], ARGV[2])
else
    redis.call('SADD', KEYS[3], ARGV[2])
end
return seq
"""

ONLINE_DRIVERS_SET = "drivers:online"  # bounded candidate set for the staleness sweeper — never a full KEYS scan

INGEST_ACCEPTED = 1
INGEST_REJECTED_NOT_ONLINE = 0
INGEST_REJECTED_OUT_OF_ORDER = -1


@dataclass(frozen=True)
class Candidate:
    driver_id: str
    distance_km: float
    lat: float
    lng: float


class DriverIndexRepository:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @property
    def redis(self) -> Redis:
        return self._redis

    @staticmethod
    def _hash_key(driver_id: str) -> str:
        return f"driver:{driver_id}"

    @staticmethod
    def _geo_key(city_id: str) -> str:
        return f"drivers:city:{city_id}"

    async def set_status(self, driver_id: str, status: str, city_id: str, trip_id: str | None = None) -> int:
        return await self._redis.eval(
            _SET_STATUS_SCRIPT,
            3,
            self._hash_key(driver_id),
            self._geo_key(city_id),
            ONLINE_DRIVERS_SET,
            status,
            driver_id,
            city_id,
            trip_id or "",
        )

    async def list_online_driver_ids(self) -> set[str]:
        return await self._redis.smembers(ONLINE_DRIVERS_SET)

    async def ingest_ping(
        self, driver_id: str, city_id: str, lat: float, lng: float, heading: float, speed_kmh: float, ts_epoch: float
    ) -> int:
        return await self._redis.eval(
            _INGEST_SCRIPT,
            2,
            self._hash_key(driver_id),
            self._geo_key(city_id),
            lat,
            lng,
            heading,
            speed_kmh,
            ts_epoch,
            driver_id,
        )

    async def get_driver_state(self, driver_id: str) -> dict | None:
        data = await self._redis.hgetall(self._hash_key(driver_id))
        return data or None

    async def search_one_city(
        self, city_id: str, lat: float, lng: float, radius_km: float, count: int, max_ping_age_s: float = 45.0
    ) -> list[Candidate]:
        """One GEORADIUS call (PLAN test-suite note: this Redis build predates
        GEOSEARCH; GEORADIUS is the same underlying feature — see
        tests/integration/test_infra_up.py docstring), then a staleness filter: a
        driver who stopped pinging more than `max_ping_age_s` ago is excluded from
        candidates even if a crashed client never called set_status(OFFLINE) — this
        is the "staleness eviction" the location service's sweeper otherwise performs
        on a slower cadence; filtering at read time means candidate retrieval is never
        stale even between sweeps.
        """
        raw = await self._redis.execute_command(
            "GEORADIUS", self._geo_key(city_id), lng, lat, radius_km, "km", "ASC", "COUNT", count, "WITHDIST", "WITHCOORD"
        )
        import time

        now = time.time()
        out: list[Candidate] = []
        for entry in raw:
            driver_id, dist, coord = entry[0], float(entry[1]), entry[2]
            d_lng, d_lat = float(coord[0]), float(coord[1])
            last_ping_ts = await self._redis.hget(self._hash_key(driver_id), "last_ping_ts")
            if last_ping_ts is not None and (now - float(last_ping_ts)) > max_ping_age_s:
                continue
            out.append(Candidate(driver_id=driver_id, distance_km=dist, lat=d_lat, lng=d_lng))
        return out

    async def search_with_neighbors(
        self, city_id: str, neighbor_city_ids: tuple[str, ...], lat: float, lng: float, radius_km: float, count: int
    ) -> list[Candidate]:
        """PLAN amendment AM-09 (finding B1, border supply): fan the radius search out
        across the rider's own city set AND its declared neighbours, merge, re-sort,
        and re-bound by COUNT — so a driver just across an administrative boundary is
        never silently invisible to a rider standing near it.
        """
        all_results: list[Candidate] = []
        for cid in (city_id, *neighbor_city_ids):
            all_results.extend(await self.search_one_city(cid, lat, lng, radius_km, count))
        all_results.sort(key=lambda c: c.distance_km)
        return all_results[:count]
