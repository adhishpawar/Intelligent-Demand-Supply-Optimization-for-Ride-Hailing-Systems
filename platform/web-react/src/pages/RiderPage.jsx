import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import MapView, { PUNE_CENTER } from "../components/MapView";
import StatusPill from "../components/StatusPill";
import { api, ApiError, loadSession, wsUrl } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { useReconnectingSocket } from "../hooks/useReconnectingSocket";
import NotificationBell from "../components/NotificationBell";
import { haversineKm } from "../utils/geo";

const TERMINAL_STATUSES = new Set([
  "NO_DRIVER_FOUND", "EXPIRED", "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM", "RATED",
]);
const ACTIVE_STATUSES = new Set(["DRIVER_ASSIGNED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS"]);
const TRIP_STORAGE_KEY = "ridehail_rider_current_trip";

export default function RiderPage() {
  const { session, logout, userId } = useAuth();
  const toast = useToast();

  const mapRef = useRef(null);
  const pickupMarkerRef = useRef(null);
  const dropMarkerRef = useRef(null);
  const driverMarkerRef = useRef(null);
  const pickModeRef = useRef("pickup");
  const pollTimerRef = useRef(null);
  const prevStatusRef = useRef(null);

  const [pickup, setPickup] = useState(null);
  const [drop, setDrop] = useState(null);
  const [estimate, setEstimate] = useState(null);
  // Round 7 stakeholder council: the vehicle_type taxonomy (SEDAN/HATCHBACK/SUV/
  // AUTO/BIKE) has existed since V001 and the seeded drivers were already spread
  // across all five -- the rider just never had a way to ask for one.
  const [vehicleType, setVehicleType] = useState("SEDAN");
  const [tripId, setTripId] = useState(() => sessionStorage.getItem(TRIP_STORAGE_KEY));
  const [trip, setTrip] = useState(null);
  const [stars, setStars] = useState(0);
  const [comment, setComment] = useState("");
  const [driverInfo, setDriverInfo] = useState(null);
  const [driverDistanceKm, setDriverDistanceKm] = useState(null);
  const [cancellationFee, setCancellationFee] = useState(null);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const fetchEstimate = useCallback(async (p, d, vType) => {
    if (!p || !d) return setEstimate(null);
    try {
      setEstimate(await api.post("/v1/pricing/estimate", { pickup: p, drop: d, vehicle_type: vType }));
    } catch (e) {
      toast(e.message);
    }
  }, [toast]);

  // Re-quote whenever the rider changes vehicle type after pins are already set,
  // so the shown estimate never silently goes stale relative to what Request ride
  // will actually charge.
  useEffect(() => {
    if (pickup && drop) fetchEstimate(pickup, drop, vehicleType);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleType]);

  const handleMapClick = useCallback((e) => {
    if (tripId) return; // no re-picking pins once a ride is in flight
    const latlng = { lat: e.latlng.lat, lng: e.latlng.lng };
    const map = mapRef.current;
    if (pickModeRef.current === "pickup") {
      setPickup(latlng);
      if (pickupMarkerRef.current) pickupMarkerRef.current.setLatLng(e.latlng);
      else pickupMarkerRef.current = L.marker(e.latlng, { title: "Pickup" }).addTo(map);
      pickModeRef.current = "drop";
      toast("Pickup set — click again for drop-off");
    } else {
      setDrop(latlng);
      if (dropMarkerRef.current) dropMarkerRef.current.setLatLng(e.latlng);
      else dropMarkerRef.current = L.marker(e.latlng, { title: "Drop" }).addTo(map);
      pickModeRef.current = "pickup";
      setPickup((p) => {
        fetchEstimate(p, latlng, vehicleType);
        return p;
      });
    }
  }, [tripId, toast, fetchEstimate, vehicleType]);

  function resetPins() {
    setPickup(null);
    setDrop(null);
    setEstimate(null);
    pickModeRef.current = "pickup";
    if (pickupMarkerRef.current) { mapRef.current.removeLayer(pickupMarkerRef.current); pickupMarkerRef.current = null; }
    if (dropMarkerRef.current) { mapRef.current.removeLayer(dropMarkerRef.current); dropMarkerRef.current = null; }
  }

  async function requestRide() {
    try {
      const created = await api.post("/v1/trips", { pickup, drop, vehicle_type: vehicleType });
      setTripId(created.trip_id);
      sessionStorage.setItem(TRIP_STORAGE_KEY, created.trip_id);
      setTrip(created);
    } catch (e) {
      toast(e.message);
    }
  }

  async function cancelTrip() {
    try {
      await api.post(`/v1/trips/${tripId}/cancel`, { reason: "rider requested cancellation" });
    } catch (e) {
      toast(e.message);
    }
  }

  function bookAnotherRide() {
    setTripId(null);
    sessionStorage.removeItem(TRIP_STORAGE_KEY);
    setTrip(null);
    setStars(0);
    setComment("");
    setVehicleType("SEDAN");
    setShowCancelConfirm(false);
    if (driverMarkerRef.current) { mapRef.current.removeLayer(driverMarkerRef.current); driverMarkerRef.current = null; }
    resetPins();
  }

  async function submitRating() {
    try {
      await api.post(`/v1/trips/${tripId}/rate`, { stars, comment: comment || null });
      toast("Thanks for your rating!");
      pollTrip();
    } catch (e) {
      toast("Could not submit rating: " + e.message);
    }
  }

  const pollTrip = useCallback(async () => {
    if (!tripId) return;
    try {
      const t = await api.get(`/v1/trips/${tripId}`);
      setTrip(t);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) bookAnotherRide();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tripId]);

  // Background poll — the backstop. Always running while a trip is active,
  // independent of the WebSocket's health, so a status change is never missed
  // purely because a socket happened to be reconnecting at that moment.
  useEffect(() => {
    if (!tripId) return;
    pollTrip();
    pollTimerRef.current = setInterval(pollTrip, 1500);
    return () => clearInterval(pollTimerRef.current);
  }, [tripId, pollTrip]);

  // Round 1 stakeholder council, rider pain #1: fetch the driver's public info
  // (name/rating/vehicle) the instant a driver is assigned — the single most
  // reassuring screen in any ride-hailing app was missing entirely before this.
  useEffect(() => {
    if (!trip?.driver_id) {
      setDriverInfo(null);
      return;
    }
    let cancelled = false;
    api.get(`/v1/drivers/${trip.driver_id}/public`).then((info) => {
      if (!cancelled) setDriverInfo(info);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [trip?.driver_id]);

  // Round 13 stakeholder council: closes a Round 1 deferred item -- the cancel
  // confirmation dialog showed a generic "a fee may apply" warning rather than the
  // actual number, because showing it would have needed a new rider-facing
  // endpoint at the time. Round 1's own admin city-config panel already exposes
  // GET /v1/pricing/config/{city_id} (ALL_AUTH, not admin-only) to every
  // authenticated role -- no new endpoint needed after all, just reading one that
  // already existed for a different purpose.
  useEffect(() => {
    if (!trip?.city_id || !trip?.driver_id) {
      setCancellationFee(null);
      return;
    }
    let cancelled = false;
    api.get(`/v1/pricing/config/${trip.city_id}`).then((cfg) => {
      if (!cancelled) setCancellationFee(cfg.cancellation_fee);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [trip?.city_id, trip?.driver_id]);

  // Live position push, over a self-reconnecting socket (PLAN §5.1 never-cut tier:
  // "WebSocket reconnect/backfill"). `onOpen` re-polls immediately on every
  // (re)connect — the equivalent of the driver-side offer catch-up, applied here to
  // "did I miss a status change while the socket was down."
  const trackWsUrl = tripId ? wsUrl("trip", `/v1/ws/trips/${tripId}/track`, loadSession()?.access_token) : null;
  const { connected: trackerConnected } = useReconnectingSocket(trackWsUrl, {
    onOpen: pollTrip,
    onMessage: (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === "DRIVER_POSITION") {
        const latlng = [data.lat, data.lng];
        if (driverMarkerRef.current) driverMarkerRef.current.setLatLng(latlng);
        else if (mapRef.current) driverMarkerRef.current = L.marker(latlng, { title: "Driver" }).addTo(mapRef.current);
        // Round 4 stakeholder council, rider pain: once a driver was assigned, the
        // only feedback was "watch the map" -- no numeric sense of how far away,
        // unlike the ETA every real ride-hailing app shows. The driver's live
        // position was already streaming in via this same message; this was purely
        // an unused-data gap, not a missing capability.
        if (trip) {
          const target = trip.status === "IN_PROGRESS" ? [trip.drop_lat, trip.drop_lng] : [trip.pickup_lat, trip.pickup_lng];
          setDriverDistanceKm(haversineKm(data.lat, data.lng, target[0], target[1]));
        }
      } else if (data.status) {
        pollTrip();
      }
    },
  });

  const status = trip?.status;

  // Round 2 stakeholder council, rider pain: status changes (driver arrived, trip
  // started, trip finished) were only ever visible by noticing the status pill had
  // silently changed text -- no active, attention-grabbing signal, unlike the
  // "your driver has arrived" push notification every real ride-hailing app sends.
  // Resets per tripId (via the tripId-keyed cleanup below) so a page refresh loading
  // an in-flight trip doesn't retroactively announce the status it loads at.
  useEffect(() => {
    prevStatusRef.current = null;
    setDriverDistanceKm(null);
  }, [tripId]);

  useEffect(() => {
    const prev = prevStatusRef.current;
    if (status && prev && prev !== status) {
      const messages = {
        DRIVER_ASSIGNED: "A driver has been assigned to your ride!",
        DRIVER_ARRIVED: "Your driver has arrived at the pickup point!",
        IN_PROGRESS: "Trip started — enjoy your ride!",
        COMPLETED: "You've arrived. Calculating your fare…",
        NO_DRIVER_FOUND: "No drivers were available nearby right now.",
        CANCELLED_BY_DRIVER: "Your driver cancelled this ride.",
        CANCELLED_BY_SYSTEM: "This ride was cancelled by the system.",
      };
      if (messages[status]) toast(messages[status]);
    }
    if (status) prevStatusRef.current = status;
  }, [status, toast]);

  return (
    <div id="app">
      <div className="topbar">
        <div className="brand"><span className="dot" /> RideOps <span className="role-badge rider">Rider</span></div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <button className="link" onClick={() => setShowHistory(true)}>My rides</button>
          <NotificationBell />
          <span className="muted">{userId?.slice(0, 8)}</span>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </div>
      {showHistory && <RideHistoryModal onClose={() => setShowHistory(false)} />}
      <div className="main">
        <MapView onReady={(m) => (mapRef.current = m)} onClick={handleMapClick} />
        <div className="panel">
          {!tripId ? (
            <BookingPanel
              pickup={pickup} drop={drop} estimate={estimate}
              onReset={resetPins} onRequest={requestRide}
              vehicleType={vehicleType} setVehicleType={setVehicleType}
            />
          ) : (
            <TripPanel
              trip={trip} status={status} onCancel={cancelTrip} onBookAnother={bookAnotherRide}
              stars={stars} setStars={setStars} comment={comment} setComment={setComment}
              onSubmitRating={submitRating} liveConnected={trackerConnected} driverInfo={driverInfo}
              showCancelConfirm={showCancelConfirm} setShowCancelConfirm={setShowCancelConfirm}
              driverDistanceKm={driverDistanceKm} cancellationFee={cancellationFee}
            />
          )}
        </div>
      </div>
    </div>
  );
}

const VEHICLE_TYPES = [
  { value: "BIKE", label: "🏍️ Bike" },
  { value: "AUTO", label: "🛺 Auto" },
  { value: "HATCHBACK", label: "🚕 Hatchback" },
  { value: "SEDAN", label: "🚗 Sedan" },
  { value: "SUV", label: "🚙 SUV" },
];

function BookingPanel({ pickup, drop, estimate, onReset, onRequest, vehicleType, setVehicleType }) {
  return (
    <div className="card">
      <h3>Where to?</h3>
      <div className="muted">Click the map to set pickup, click again for drop-off.</div>
      <div style={{ marginTop: 10 }} className="muted">
        Pickup: {pickup ? `${pickup.lat.toFixed(4)}, ${pickup.lng.toFixed(4)}` : "— not set —"}<br />
        Drop: {drop ? `${drop.lat.toFixed(4)}, ${drop.lng.toFixed(4)}` : "— not set —"}
      </div>
      {/* Round 7 stakeholder council: five vehicle types have existed in the schema
          (and the seeded drivers) since the start of the night with no way for a
          rider to ask for one -- every ride cost and matched identically regardless
          of type. */}
      <label style={{ marginTop: 14 }}>Ride type</label>
      <div className="tab-row" style={{ flexWrap: "wrap" }}>
        {VEHICLE_TYPES.map((vt) => (
          <button
            key={vt.value}
            className={`tab-btn ${vehicleType === vt.value ? "active" : ""}`}
            style={{ flex: "1 1 30%", whiteSpace: "nowrap" }}
            onClick={() => setVehicleType(vt.value)}
          >
            {vt.label}
          </button>
        ))}
      </div>
      {pickup && drop && <button className="btn ghost" style={{ marginTop: 8 }} onClick={onReset}>Reset pins</button>}
      {estimate && (
        <div style={{ marginTop: 14 }}>
          <div className="fare-line"><span>Base + distance + time</span><span>₹{(estimate.fare_estimate / estimate.surge_multiplier).toFixed(2)}</span></div>
          <div className="fare-line"><span>Surge</span><span>{estimate.surge_multiplier.toFixed(2)}×</span></div>
          <div className="fare-line total"><span>Total estimate</span><span>₹{estimate.fare_estimate.toFixed(2)}</span></div>
          <div className="muted" style={{ marginTop: 6 }}>{(estimate.distance_m / 1000).toFixed(1)} km · ~{Math.round(estimate.duration_s / 60)} min</div>
          <button className="btn primary" onClick={onRequest}>Request ride</button>
        </div>
      )}
    </div>
  );
}

function TripPanel({
  trip, status, onCancel, onBookAnother, stars, setStars, comment, setComment, onSubmitRating, liveConnected,
  driverInfo, showCancelConfirm, setShowCancelConfirm, driverDistanceKm, cancellationFee,
}) {
  if (!trip) return <div className="card"><div className="muted">Loading trip…</div></div>;

  const showLiveIndicator = ["DRIVER_ASSIGNED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS"].includes(status);
  const driverAssigned = !!trip.driver_id;

  return (
    <div className="card">
      <StatusPill status={status} />
      {showLiveIndicator && (
        <span className={`live-indicator ${liveConnected ? "live" : "reconnecting"}`} style={{ marginLeft: 8 }}>
          {liveConnected ? "● live" : "○ reconnecting…"}
        </span>
      )}
      <div style={{ marginTop: 12 }} className="muted">
        Trip {trip.trip_id.slice(0, 8)}… · {VEHICLE_TYPES.find((v) => v.value === trip.vehicle_type_requested)?.label || trip.vehicle_type_requested}
      </div>
      {trip.fare_final != null && (
        <div className="fare-line total"><span>Fare</span><span>₹{trip.fare_final.toFixed(2)}</span></div>
      )}

      {/* Round 1 stakeholder council, rider pain #1: "here is who is coming for
          you" -- the single most reassuring screen in any ride-hailing app. */}
      {driverAssigned && driverInfo && ACTIVE_STATUSES.has(status) && (
        <div className="driver-card">
          <div className="driver-card-name">{driverInfo.name} <span className="muted">★ {driverInfo.rating_avg.toFixed(1)}</span></div>
          {driverInfo.vehicle_make && (
            <div className="muted">{driverInfo.vehicle_make} {driverInfo.vehicle_model} · {driverInfo.vehicle_plate}</div>
          )}
          {/* Round 4 stakeholder council, rider pain #2: "watch the map" was the only
              feedback on how far away the driver actually was -- no numeric ETA/
              distance, unlike every real ride-hailing app. Computed client-side from
              the same DRIVER_POSITION stream the map marker already consumes. */}
          {driverDistanceKm != null && (
            <div className="muted" style={{ marginTop: 4 }}>
              {status === "IN_PROGRESS" ? "Drop-off" : "Driver"} ~{driverDistanceKm < 1 ? `${Math.round(driverDistanceKm * 1000)} m` : `${driverDistanceKm.toFixed(1)} km`} away
            </div>
          )}
        </div>
      )}

      {status === "MATCHING" && (
        <>
          <div className="muted" style={{ marginTop: 10 }}>
            Checking nearby drivers — attempt {(trip.dispatch_attempts || 0) + 1} of 3.
            This never spins forever: if no driver is found, you'll see that clearly.
          </div>
          <button className="btn danger" onClick={() => setShowCancelConfirm(true)}>Cancel request</button>
        </>
      )}
      {ACTIVE_STATUSES.has(status) && (
        <>
          <div className="muted" style={{ marginTop: 10 }}>Watch the map for live position.</div>
          {status !== "IN_PROGRESS" && <button className="btn danger" onClick={() => setShowCancelConfirm(true)}>Cancel ride</button>}
        </>
      )}
      {showCancelConfirm && (
        <div className="confirm-inline">
          <div>
            Cancel this ride?
            {driverAssigned && (
              cancellationFee != null
                ? ` A cancellation fee of ₹${cancellationFee.toFixed(2)} will apply since a driver is already on the way.`
                : " A cancellation fee may apply since a driver is already on the way."
            )}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn ghost" onClick={() => setShowCancelConfirm(false)}>Keep ride</button>
            <button className="btn danger" onClick={() => { setShowCancelConfirm(false); onCancel(); }}>Yes, cancel</button>
          </div>
        </div>
      )}
      {(status === "COMPLETED" || status === "PAID_PENDING") && (
        <div className="muted" style={{ marginTop: 10 }}>Finalizing your trip and payment…</div>
      )}
      {status === "PAID" && (
        <div style={{ marginTop: 14 }}>
          <div className="muted">Rate your driver</div>
          <div className="stars">
            {[1, 2, 3, 4, 5].map((n) => (
              <span key={n} className={`star ${n <= stars ? "on" : ""}`} onClick={() => setStars(n)}>★</span>
            ))}
          </div>
          <textarea placeholder="Optional comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          <button className="btn success" disabled={stars === 0} onClick={onSubmitRating}>Submit rating</button>
        </div>
      )}
      {status === "RATED" && (
        <>
          <div className="muted" style={{ marginTop: 10 }}>Thanks for riding with RideOps!</div>
          <button className="btn primary" onClick={onBookAnother}>Book another ride</button>
        </>
      )}
      {TERMINAL_STATUSES.has(status) && status !== "RATED" && (
        <>
          {trip.cancellation_fee_applied > 0 && (
            <div className="muted" style={{ marginTop: 10 }}>A cancellation fee of ₹{trip.cancellation_fee_applied} applies.</div>
          )}
          <button className="btn primary" onClick={onBookAnother}>Book another ride</button>
        </>
      )}
    </div>
  );
}

// Round 1 stakeholder council, rider pain #2: "no trip history anywhere" -- a
// genuinely expected feature in any ride-hailing app, entirely missing before this.
function RideHistoryModal({ onClose }) {
  const [trips, setTrips] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.get("/v1/trips/mine").then(setTrips).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="offer-modal" onClick={onClose}>
      <div className="offer-card" style={{ width: 460, textAlign: "left" }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>My rides</h3>
        {error && <div className="muted">{error}</div>}
        {!trips && !error && <div className="muted">Loading…</div>}
        {trips && trips.length === 0 && <div className="muted">No rides yet — go request one!</div>}
        {trips && trips.length > 0 && (
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="data-table">
              <thead><tr><th>When</th><th>Status</th><th>Fare</th></tr></thead>
              <tbody>
                {trips.map((t) => (
                  <tr key={t.trip_id}>
                    <td>{new Date(t.requested_at).toLocaleString()}</td>
                    <td><StatusPill status={t.status} /></td>
                    <td>{t.fare_final != null ? `₹${t.fare_final.toFixed(2)}` : t.fare_estimate ? `~₹${t.fare_estimate.toFixed(2)}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <button className="btn ghost" style={{ marginTop: 14 }} onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
