import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import MapView, { PUNE_CENTER } from "../components/MapView";
import StatusPill from "../components/StatusPill";
import { api, loadSession, wsUrl } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { useReconnectingSocket } from "../hooks/useReconnectingSocket";

const PING_INTERVAL_MS = 3000;
const TRIP_STORAGE_KEY = "ridehail_driver_current_trip";

function stepToward(from, to, fraction) {
  return { lat: from.lat + (to.lat - from.lat) * fraction, lng: from.lng + (to.lng - from.lng) * fraction };
}

function haversineKm(lat1, lng1, lat2, lng2) {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export default function DriverPage() {
  const { userId, logout } = useAuth();
  const toast = useToast();

  const mapRef = useRef(null);
  const selfMarkerRef = useRef(null);
  const pingTimerRef = useRef(null);
  const pollTimerRef = useRef(null);
  const posRef = useRef({ lat: PUNE_CENTER[0] + (Math.random() - 0.5) * 0.02, lng: PUNE_CENTER[1] + (Math.random() - 0.5) * 0.02 });

  const [online, setOnline] = useState(false);
  const [offer, setOffer] = useState(null); // {trip_id, offer_id, expires_at, pickup_lat, pickup_lng, eta_seconds}
  const [countdown, setCountdown] = useState(0);
  const [tripId, setTripId] = useState(() => sessionStorage.getItem(TRIP_STORAGE_KEY));
  const [trip, setTrip] = useState(null);
  const [earnings, setEarnings] = useState(0);
  const [myStats, setMyStats] = useState(null); // {rating_avg, acceptance_rate, rides_completed}

  // ---------- Location simulation ----------
  // A driver sitting at a desk has no real GPS movement to report. Rather than fake
  // a static ping forever, this steps the reported position toward the pickup (while
  // en route) or drop (once IN_PROGRESS) each tick — a real, deterministic algorithm
  // producing a genuinely moving marker on the rider's live map, not a random jitter.
  const sendPing = useCallback(async () => {
    let target = null;
    if (trip && ["DRIVER_ASSIGNED", "DRIVER_ARRIVING"].includes(trip.status)) target = { lat: trip.pickup_lat, lng: trip.pickup_lng };
    else if (trip && trip.status === "IN_PROGRESS") target = { lat: trip.drop_lat, lng: trip.drop_lng };

    if (target) {
      posRef.current = stepToward(posRef.current, target, 0.25);
    } else {
      posRef.current = { lat: posRef.current.lat + (Math.random() - 0.5) * 0.0008, lng: posRef.current.lng + (Math.random() - 0.5) * 0.0008 };
    }
    const { lat, lng } = posRef.current;
    if (selfMarkerRef.current) selfMarkerRef.current.setLatLng([lat, lng]);
    else if (mapRef.current) selfMarkerRef.current = L.marker([lat, lng], { title: "You" }).addTo(mapRef.current);

    try {
      await api.patch(`/v1/drivers/${userId}/location`, { lat, lng, heading: 0, speed_kmh: target ? 22 : 0 });
    } catch (e) {
      // A rejected ping (e.g. briefly OFFLINE mid-toggle) is not demo-fatal -- next tick recovers.
    }
  }, [trip, userId]);

  async function goOnline() {
    try {
      await api.patch(`/v1/drivers/${userId}/status`, { status: "ONLINE" });
      setOnline(true);
      toast("You're online");
    } catch (e) {
      toast(e.message);
    }
  }

  async function goOffline() {
    try {
      await api.patch(`/v1/drivers/${userId}/status`, { status: "OFFLINE" });
      setOnline(false);
      toast("You're offline");
    } catch (e) {
      toast(e.message);
    }
  }

  useEffect(() => {
    if (!online) return;
    sendPing();
    pingTimerRef.current = setInterval(sendPing, PING_INTERVAL_MS);
    return () => clearInterval(pingTimerRef.current);
  }, [online, sendPing]);

  // ---------- Own stats (rating, acceptance rate, rides completed) ----------
  // Round 2 stakeholder council, driver pain: a driver's rating and acceptance rate
  // materially affect their standing on a real platform (deactivation risk, priority
  // matching) yet were completely invisible in this console -- the driver had no way
  // to know where they stood short of asking a rider directly. Both fields already
  // existed server-side (rating_avg on /v1/users/me, acceptance_rate + rides_completed
  // on /v1/drivers/{id}) and were already self-readable under existing RBAC -- purely
  // a display gap, not a new endpoint.
  const fetchMyStats = useCallback(async () => {
    try {
      const [profile, driverInfo] = await Promise.all([
        api.get("/v1/users/me"),
        api.get(`/v1/drivers/${userId}`),
      ]);
      setMyStats({
        rating_avg: profile.rating_avg,
        acceptance_rate: driverInfo.acceptance_rate,
        rides_completed: driverInfo.rides_completed,
      });
    } catch {
      /* non-critical -- stats panel just stays hidden */
    }
  }, [userId]);

  useEffect(() => {
    fetchMyStats();
  }, [fetchMyStats]);

  // Refresh stats right after a trip completes, since rides_completed / rating_avg
  // may have just changed.
  useEffect(() => {
    if (trip?.status === "COMPLETED") fetchMyStats();
  }, [trip?.status, fetchMyStats]);

  // ---------- Offer WS (self-reconnecting) + catch-up ----------
  // Redis pub/sub (the live push) has no replay: if this WS connects AFTER an offer
  // was already published (a reload, a network blip, or a reconnect after a drop),
  // the push is gone forever and the driver would silently never see an offer the
  // backend still holds active for them. A REST catch-up call on every (re)connect
  // — via `onOpen`, so it fires after the FIRST connect and every automatic
  // reconnect alike — closes that gap. Same "never trust a single path" principle
  // applied elsewhere tonight (the outbox relay, the payment reconciler).
  const offerWsUrl = online ? wsUrl("trip", `/v1/ws/drivers/${userId}/offers`, loadSession()?.access_token) : null;
  useReconnectingSocket(offerWsUrl, {
    onOpen: () => {
      api.get(`/v1/drivers/${userId}/current-offer`).then((resp) => {
        if (resp.offer) setOffer({ ...resp.offer, eta_seconds: 0 });
      }).catch(() => {});
    },
    onMessage: (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === "OFFER") setOffer(data);
    },
  });

  // Offer countdown (never an indefinite spinner -- PLAN §6.4 D2's rule applies
  // symmetrically on the driver side: the offer clearly expires visibly).
  //
  // Round 2 stakeholder council, driver pain: when the countdown hit zero the modal
  // just vanished with no explanation -- felt like the app glitched, not like a
  // deliberate timeout. (A future "someone else took it" server push for the
  // supersede/redispatch case is a separate, larger change -- deferred, see
  // PROGRESS.md Round 2 -- this covers the dominant case: not responding in time.)
  useEffect(() => {
    if (!offer) return;
    const tick = () => {
      const remaining = Math.max(0, Math.round((new Date(offer.expires_at).getTime() - Date.now()) / 1000));
      setCountdown(remaining);
      if (remaining <= 0) {
        setOffer(null);
        toast("Offer expired — you didn't respond in time.");
      }
    };
    tick();
    const id = setInterval(tick, 500);
    return () => clearInterval(id);
  }, [offer, toast]);

  async function respondToOffer(action) {
    const offerTripId = offer.trip_id;
    setOffer(null);
    try {
      await api.post(`/v1/trips/${offerTripId}/respond`, { action });
      if (action === "ACCEPT") {
        setTripId(offerTripId);
        sessionStorage.setItem(TRIP_STORAGE_KEY, offerTripId);
      }
    } catch (e) {
      toast(e.message);
    }
  }

  // ---------- Active trip polling + lifecycle ----------
  const pollTrip = useCallback(async () => {
    if (!tripId) return;
    try {
      setTrip(await api.get(`/v1/trips/${tripId}`));
    } catch {
      /* transient -- next tick recovers */
    }
  }, [tripId]);

  useEffect(() => {
    if (!tripId) return;
    pollTrip();
    pollTimerRef.current = setInterval(pollTrip, 1500);
    return () => clearInterval(pollTimerRef.current);
  }, [tripId, pollTrip]);

  async function advance(step) {
    try {
      const updated = await api.post(`/v1/trips/${tripId}/${step}`);
      setTrip(updated);
      if (updated.status === "PAID" || updated.status === "COMPLETED") {
        if (updated.fare_final) setEarnings((e) => e + updated.fare_final * 0.8); // driver payout share, matches commission split
      }
      if (["PAID", "RATED", "NO_DRIVER_FOUND", "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM"].includes(updated.status)) {
        setTimeout(() => {
          setTripId(null);
          sessionStorage.removeItem(TRIP_STORAGE_KEY);
          setTrip(null);
        }, 2500);
      }
    } catch (e) {
      toast(e.message);
    }
  }

  async function driverCancel() {
    try {
      await api.post(`/v1/trips/${tripId}/driver-cancel`);
      setTripId(null);
      sessionStorage.removeItem(TRIP_STORAGE_KEY);
      setTrip(null);
    } catch (e) {
      toast(e.message);
    }
  }

  return (
    <div id="app">
      <div className="topbar">
        <div className="brand"><span className="dot" /> Glovatrix <span className="role-badge driver">Driver</span></div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span className="muted">{userId?.slice(0, 8)}</span>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </div>
      <div className="main">
        <MapView center={[posRef.current.lat, posRef.current.lng]} onReady={(m) => (mapRef.current = m)} />
        <div className="panel">
          <div className="card">
            <h3>Status</h3>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn success" disabled={online} onClick={goOnline}>Go online</button>
              <button className="btn ghost" disabled={!online} onClick={goOffline}>Go offline</button>
            </div>
            <div className="muted" style={{ marginTop: 10 }}>{online ? "Online — pinging location every 3s" : "Offline — not matchable"}</div>
          </div>

          <div className="grid-stats" style={{ marginBottom: 14 }}>
            <div className="stat-tile">
              <div className="value">₹{earnings.toFixed(2)}</div>
              <div className="label">Session earnings</div>
            </div>
            {myStats && (
              <>
                <div className="stat-tile">
                  <div className="value">★ {myStats.rating_avg.toFixed(2)}</div>
                  <div className="label">Your rating</div>
                </div>
                <div className="stat-tile">
                  <div className="value">{Math.round(myStats.acceptance_rate * 100)}%</div>
                  <div className="label">Acceptance rate</div>
                </div>
                <div className="stat-tile">
                  <div className="value">{myStats.rides_completed}</div>
                  <div className="label">Rides completed</div>
                </div>
              </>
            )}
          </div>

          {trip && <ActiveTripCard trip={trip} onAdvance={advance} onCancel={driverCancel} />}
        </div>
      </div>

      {offer && (
        <div className="offer-modal">
          <div className="offer-card">
            <h3>🚗 New ride request</h3>
            <div className="countdown">{countdown}s</div>
            {/* Round 1 stakeholder council, driver pain #1: show what the ride
                actually pays and where it goes -- a driver cannot make a real
                accept/reject decision on a countdown and an ETA number alone. */}
            {offer.fare_estimate != null && (
              <div className="fare-line total" style={{ justifyContent: "center", gap: 8 }}>
                <span>Estimated fare</span><span>₹{offer.fare_estimate.toFixed(2)}</span>
              </div>
            )}
            {offer.drop_lat != null && (
              <div className="muted">
                Trip distance ~{haversineKm(offer.pickup_lat, offer.pickup_lng, offer.drop_lat, offer.drop_lng).toFixed(1)} km
              </div>
            )}
            <div className="muted">Pickup ETA ~{Math.round(offer.eta_seconds)}s away</div>
            <div className="row" style={{ marginTop: 16 }}>
              <button className="btn danger" onClick={() => respondToOffer("REJECT")}>Reject</button>
              <button className="btn success" onClick={() => respondToOffer("ACCEPT")}>Accept</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const NEXT_STEP = {
  DRIVER_ASSIGNED: { step: "start-navigation", label: "Start navigation" },
  DRIVER_ARRIVING: { step: "confirm-arrival", label: "Confirm arrival" },
  DRIVER_ARRIVED: { step: "start", label: "Start trip" },
  IN_PROGRESS: { step: "complete", label: "Complete trip" },
};

function ActiveTripCard({ trip, onAdvance, onCancel }) {
  const next = NEXT_STEP[trip.status];
  return (
    <div className="card">
      <StatusPill status={trip.status} />
      <div className="muted" style={{ marginTop: 10 }}>Trip {trip.trip_id.slice(0, 8)}…</div>
      {trip.fare_final != null && <div className="fare-line total"><span>Fare</span><span>₹{trip.fare_final.toFixed(2)}</span></div>}
      {next && <button className="btn primary" onClick={() => onAdvance(next.step)}>{next.label}</button>}
      {["DRIVER_ASSIGNED", "DRIVER_ARRIVING"].includes(trip.status) && (
        <button className="btn danger" onClick={onCancel}>Cancel (return to pool)</button>
      )}
      {["PAID", "RATED", "NO_DRIVER_FOUND", "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM"].includes(trip.status) && (
        <div className="muted" style={{ marginTop: 10 }}>Trip closed — returning to online pool…</div>
      )}
    </div>
  );
}
