import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import MapView, { PUNE_CENTER } from "../components/MapView";
import StatusPill from "../components/StatusPill";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

// The admin console — PLAN §6.4 D5: "the demo is inspectable, not merely watchable."
// Everything here reads live off the real Redis geo index and the real Postgres
// trip/ledger tables — no separate analytics pipeline, no mocked numbers.

export default function AdminPage() {
  const { userId, logout } = useAuth();
  const toast = useToast();
  const mapRef = useRef(null);
  const markersRef = useRef(new Map());

  const [cityId, setCityId] = useState("pune");
  const [heatmap, setHeatmap] = useState({ drivers: [], online_count: 0 });
  const [trips, setTrips] = useState([]);
  const [selectedTrip, setSelectedTrip] = useState(null);
  const [audit, setAudit] = useState([]);
  const [ledger, setLedger] = useState(null);

  const refreshHeatmap = useCallback(async () => {
    try {
      setHeatmap(await api.get(`/v1/admin/heatmap/${cityId}`));
    } catch (e) {
      /* transient */
    }
  }, [cityId]);

  const refreshTrips = useCallback(async () => {
    try {
      setTrips(await api.get("/v1/trips"));
    } catch (e) {
      toast(e.message);
    }
  }, [toast]);

  useEffect(() => {
    refreshHeatmap();
    refreshTrips();
    const id = setInterval(() => { refreshHeatmap(); refreshTrips(); }, 3000);
    return () => clearInterval(id);
  }, [refreshHeatmap, refreshTrips]);

  // Render driver dots on the map, diffed against the previous frame.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const seen = new Set();
    for (const d of heatmap.drivers) {
      seen.add(d.driver_id);
      const color = d.current_trip_id ? "#4f8cff" : "#2fbf71";
      let marker = markersRef.current.get(d.driver_id);
      if (marker) {
        marker.setLatLng([d.lat, d.lng]);
        marker.setStyle({ color, fillColor: color });
      } else {
        marker = L.circleMarker([d.lat, d.lng], { radius: 7, color, fillColor: color, fillOpacity: 0.85, weight: 1 })
          .addTo(map)
          .bindTooltip(d.driver_id.slice(0, 8));
        markersRef.current.set(d.driver_id, marker);
      }
    }
    for (const [id, marker] of markersRef.current.entries()) {
      if (!seen.has(id)) { map.removeLayer(marker); markersRef.current.delete(id); }
    }
  }, [heatmap]);

  async function selectTrip(trip) {
    setSelectedTrip(trip);
    setLedger(null);
    try {
      setAudit(await api.get(`/v1/trips/${trip.trip_id}/audit`));
    } catch (e) {
      setAudit([]);
    }
    try {
      setLedger(await api.get(`/v1/payments/${trip.trip_id}/ledger`));
    } catch (e) {
      setLedger(null);
    }
  }

  const activeTrips = trips.filter((t) => !["PAID", "RATED", "NO_DRIVER_FOUND", "EXPIRED", "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM"].includes(t.status)).length;

  return (
    <div id="app">
      <div className="topbar">
        <div className="brand"><span className="dot" /> Glovatrix <span className="role-badge admin">Admin</span></div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <select value={cityId} onChange={(e) => setCityId(e.target.value)} style={{ width: "auto" }}>
            <option value="pune">Pune</option>
            <option value="mumbai">Mumbai</option>
          </select>
          <span className="muted">{userId?.slice(0, 8)}</span>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </div>
      <div className="main">
        <MapView center={PUNE_CENTER} zoom={12} onReady={(m) => (mapRef.current = m)} />
        <div className="panel" style={{ width: 460 }}>
          <div className="grid-stats" style={{ marginBottom: 14 }}>
            <div className="stat-tile"><div className="value">{heatmap.online_count}</div><div className="label">Drivers online ({cityId})</div></div>
            <div className="stat-tile"><div className="value">{activeTrips}</div><div className="label">Active trips</div></div>
            <div className="stat-tile"><div className="value">{trips.length}</div><div className="label">Recent trips (feed)</div></div>
          </div>

          {selectedTrip ? (
            <TripDetail trip={selectedTrip} audit={audit} ledger={ledger} onBack={() => setSelectedTrip(null)} />
          ) : (
            <div className="card">
              <h3>Recent trips (live)</h3>
              <div style={{ maxHeight: 480, overflowY: "auto" }}>
                <table className="data-table">
                  <thead><tr><th>Trip</th><th>Status</th><th>Fare</th></tr></thead>
                  <tbody>
                    {trips.map((t) => (
                      <tr key={t.trip_id} style={{ cursor: "pointer" }} onClick={() => selectTrip(t)}>
                        <td>{t.trip_id.slice(0, 8)}…</td>
                        <td><StatusPill status={t.status} /></td>
                        <td>{t.fare_final != null ? `₹${t.fare_final.toFixed(2)}` : t.fare_estimate ? `~₹${t.fare_estimate.toFixed(2)}` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TripDetail({ trip, audit, ledger, onBack }) {
  return (
    <div className="card">
      <button className="link" onClick={onBack}>&larr; Back to feed</button>
      <h3 style={{ marginTop: 10 }}>Trip {trip.trip_id.slice(0, 8)}…</h3>
      <StatusPill status={trip.status} />
      <div className="muted" style={{ marginTop: 8 }}>
        Rider {trip.rider_id?.slice(0, 8)} · Driver {trip.driver_id ? trip.driver_id.slice(0, 8) : "—"}<br />
        Dispatch attempts: {trip.dispatch_attempts}
      </div>

      <h3 style={{ marginTop: 16 }}>Audit trail</h3>
      <table className="data-table">
        <thead><tr><th>Event</th><th>From → To</th><th>At</th></tr></thead>
        <tbody>
          {audit.map((e) => (
            <tr key={e.event_id}>
              <td>{e.event_type}</td>
              <td>{e.from_status || "—"} → {e.to_status}</td>
              <td>{new Date(e.created_at).toLocaleTimeString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {ledger && (
        <>
          <h3 style={{ marginTop: 16 }}>Ledger {Math.abs(ledger.balance) < 0.01 ? "✅ balanced" : "⚠️ imbalanced"}</h3>
          <table className="data-table">
            <thead><tr><th>Entry</th><th>Account</th><th>Amount</th></tr></thead>
            <tbody>
              {ledger.entries.map((e, i) => (
                <tr key={i}>
                  <td>{e.entry_type}</td>
                  <td>{e.account_type}</td>
                  <td style={{ color: e.amount < 0 ? "var(--bad)" : "var(--good)" }}>₹{e.amount.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
