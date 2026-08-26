import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import MapView, { PUNE_CENTER } from "../components/MapView";
import StatusPill from "../components/StatusPill";
import { api, ApiError, loadSession, wsUrl } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { useReconnectingSocket } from "../hooks/useReconnectingSocket";

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

  const [pickup, setPickup] = useState(null);
  const [drop, setDrop] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [tripId, setTripId] = useState(() => sessionStorage.getItem(TRIP_STORAGE_KEY));
  const [trip, setTrip] = useState(null);
  const [stars, setStars] = useState(0);
  const [comment, setComment] = useState("");

  const fetchEstimate = useCallback(async (p, d) => {
    if (!p || !d) return setEstimate(null);
    try {
      setEstimate(await api.post("/v1/pricing/estimate", { pickup: p, drop: d }));
    } catch (e) {
      toast(e.message);
    }
  }, [toast]);

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
        fetchEstimate(p, latlng);
        return p;
      });
    }
  }, [tripId, toast, fetchEstimate]);

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
      const created = await api.post("/v1/trips", { pickup, drop });
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
      } else if (data.status) {
        pollTrip();
      }
    },
  });

  const status = trip?.status;

  return (
    <div id="app">
      <div className="topbar">
        <div className="brand"><span className="dot" /> Glovatrix <span className="role-badge rider">Rider</span></div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span className="muted">{userId?.slice(0, 8)}</span>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </div>
      <div className="main">
        <MapView onReady={(m) => (mapRef.current = m)} onClick={handleMapClick} />
        <div className="panel">
          {!tripId ? (
            <BookingPanel
              pickup={pickup} drop={drop} estimate={estimate}
              onReset={resetPins} onRequest={requestRide}
            />
          ) : (
            <TripPanel
              trip={trip} status={status} onCancel={cancelTrip} onBookAnother={bookAnotherRide}
              stars={stars} setStars={setStars} comment={comment} setComment={setComment}
              onSubmitRating={submitRating} liveConnected={trackerConnected}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function BookingPanel({ pickup, drop, estimate, onReset, onRequest }) {
  return (
    <div className="card">
      <h3>Where to?</h3>
      <div className="muted">Click the map to set pickup, click again for drop-off.</div>
      <div style={{ marginTop: 10 }} className="muted">
        Pickup: {pickup ? `${pickup.lat.toFixed(4)}, ${pickup.lng.toFixed(4)}` : "— not set —"}<br />
        Drop: {drop ? `${drop.lat.toFixed(4)}, ${drop.lng.toFixed(4)}` : "— not set —"}
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

function TripPanel({ trip, status, onCancel, onBookAnother, stars, setStars, comment, setComment, onSubmitRating, liveConnected }) {
  if (!trip) return <div className="card"><div className="muted">Loading trip…</div></div>;

  const showLiveIndicator = ["DRIVER_ASSIGNED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS"].includes(status);

  return (
    <div className="card">
      <StatusPill status={status} />
      {showLiveIndicator && (
        <span className={`live-indicator ${liveConnected ? "live" : "reconnecting"}`} style={{ marginLeft: 8 }}>
          {liveConnected ? "● live" : "○ reconnecting…"}
        </span>
      )}
      <div style={{ marginTop: 12 }} className="muted">Trip {trip.trip_id.slice(0, 8)}…</div>
      {trip.fare_final != null && (
        <div className="fare-line total"><span>Fare</span><span>₹{trip.fare_final.toFixed(2)}</span></div>
      )}

      {status === "MATCHING" && (
        <>
          <div className="muted" style={{ marginTop: 10 }}>
            Checking nearby drivers — attempt {(trip.dispatch_attempts || 0) + 1} of 3.
            This never spins forever: if no driver is found, you'll see that clearly.
          </div>
          <button className="btn danger" onClick={onCancel}>Cancel request</button>
        </>
      )}
      {ACTIVE_STATUSES.has(status) && (
        <>
          <div className="muted" style={{ marginTop: 10 }}>Your driver is on it. Watch the map for live position.</div>
          {status !== "IN_PROGRESS" && <button className="btn danger" onClick={onCancel}>Cancel ride</button>}
        </>
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
          <div className="muted" style={{ marginTop: 10 }}>Thanks for riding with Glovatrix!</div>
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
