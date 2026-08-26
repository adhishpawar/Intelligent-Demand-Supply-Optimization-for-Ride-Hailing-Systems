// Round 4 stakeholder council: extracted from DriverPage.jsx, where it was a local
// helper only that page used for the offer card's trip-distance display, so
// RiderPage.jsx can reuse it for the live driver-distance/ETA indicator (see
// RiderPage's `driverDistanceKm` computation) without duplicating the formula.
export function haversineKm(lat1, lng1, lat2, lng2) {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
