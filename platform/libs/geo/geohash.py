"""Geohash encoding — used to stamp every geo-bearing row/event with a portable cell id
(PLAN §6.2 amendment AM-10: raw lat/lng + geohash7 stored everywhere, not just derived
city_id, so re-sharding/re-zoning later is a batch UPDATE, not archaeology).

This is a standalone base32 geohash implementation (no third-party dependency) — the
operational Redis GEO index uses its own internal encoding via GEOADD/GEOSEARCH; this
module is for the *stored, portable* cell id on rows and Kafka events, matching
`ride_hailing_HLD_LLD.md` section 3.4's `geohash7` field.
"""
from __future__ import annotations

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lng: float, precision: int = 7) -> str:
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    geohash = []
    bit = 0
    ch = 0
    even = True
    while len(geohash) < precision:
        if even:
            mid = (lng_range[0] + lng_range[1]) / 2
            if lng > mid:
                ch |= 1 << (4 - bit)
                lng_range[0] = mid
            else:
                lng_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat > mid:
                ch |= 1 << (4 - bit)
                lat_range[0] = mid
            else:
                lat_range[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            geohash.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(geohash)
