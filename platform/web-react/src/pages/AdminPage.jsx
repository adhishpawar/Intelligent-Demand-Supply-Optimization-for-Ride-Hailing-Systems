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

const TERMINAL_STATUSES = ["PAID", "RATED", "NO_DRIVER_FOUND", "EXPIRED", "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM"];
const ALL_STATUSES = [
  "REQUESTED", "MATCHING", "DRIVER_ASSIGNED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS",
  "COMPLETED", "PAID_PENDING", "PAID", "RATED", "NO_DRIVER_FOUND",
  "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM", "EXPIRED",
];

export default function AdminPage() {
  const { userId, logout } = useAuth();
  const toast = useToast();
  const mapRef = useRef(null);
  const markersRef = useRef(new Map());

  const [cityId, setCityId] = useState("pune");
  const [view, setView] = useState("trips"); // "trips" | "settings" | "drivers"
  const [heatmap, setHeatmap] = useState({ drivers: [], online_count: 0 });
  const [trips, setTrips] = useState([]);
  const [tripFilter, setTripFilter] = useState("ALL"); // "ALL" | "ACTIVE" | "TERMINAL" | one exact status
  const [selectedTrip, setSelectedTrip] = useState(null);
  const [audit, setAudit] = useState([]);
  const [ledger, setLedger] = useState(null);
  const [revenue, setRevenue] = useState(null); // {commission_total, gross_fares_total, paid_trip_count}

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

  // Round 3 stakeholder council (admin/tech-lead pain): the ledger was always real
  // and balanced per-trip, but there was no "how is the platform doing today"
  // rollup anywhere -- only the one-trip-at-a-time ledger view.
  const refreshRevenue = useCallback(async () => {
    try {
      setRevenue(await api.get("/v1/payments/analytics/summary"));
    } catch (e) {
      /* transient */
    }
  }, []);

  useEffect(() => {
    refreshHeatmap();
    refreshTrips();
    refreshRevenue();
    const id = setInterval(() => { refreshHeatmap(); refreshTrips(); refreshRevenue(); }, 3000);
    return () => clearInterval(id);
  }, [refreshHeatmap, refreshTrips, refreshRevenue]);

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

  const activeTrips = trips.filter((t) => !TERMINAL_STATUSES.includes(t.status)).length;

  // Round 2 stakeholder council, admin/tech-lead pain: the trip feed was an
  // unfilterable flat list -- fine for a demo's handful of trips, but a real ops
  // team hunting for e.g. one stuck trip among hundreds needs to narrow by status.
  // Client-side filter over the already-fetched feed -- no new endpoint needed, the
  // full recent-trips list is already being polled every 3s.
  const filteredTrips = trips.filter((t) => {
    if (tripFilter === "ALL") return true;
    if (tripFilter === "ACTIVE") return !TERMINAL_STATUSES.includes(t.status);
    if (tripFilter === "TERMINAL") return TERMINAL_STATUSES.includes(t.status);
    return t.status === tripFilter;
  });

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
            {revenue && (
              <>
                <div className="stat-tile"><div className="value">₹{revenue.commission_total.toFixed(2)}</div><div className="label">Commission today</div></div>
                <div className="stat-tile"><div className="value">₹{revenue.gross_fares_total.toFixed(2)}</div><div className="label">Gross fares today</div></div>
                <div className="stat-tile"><div className="value">{revenue.paid_trip_count}</div><div className="label">Paid trips today</div></div>
              </>
            )}
          </div>

          <div className="tab-row">
            <button className={`tab-btn ${view === "trips" ? "active" : ""}`} onClick={() => { setView("trips"); setSelectedTrip(null); }}>Trips</button>
            <button className={`tab-btn ${view === "settings" ? "active" : ""}`} onClick={() => setView("settings")}>City settings</button>
            <button className={`tab-btn ${view === "drivers" ? "active" : ""}`} onClick={() => setView("drivers")}>Drivers / KYC</button>
          </div>

          {view === "trips" && (
            selectedTrip ? (
              <TripDetail trip={selectedTrip} audit={audit} ledger={ledger} onBack={() => setSelectedTrip(null)} />
            ) : (
              <div className="card">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <h3 style={{ margin: 0 }}>Recent trips (live)</h3>
                  <select value={tripFilter} onChange={(e) => setTripFilter(e.target.value)} style={{ width: "auto", padding: "6px 8px" }}>
                    <option value="ALL">All ({trips.length})</option>
                    <option value="ACTIVE">Active</option>
                    <option value="TERMINAL">Terminal</option>
                    {ALL_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                {filteredTrips.length === 0 && <div className="muted">No trips match this filter.</div>}
                <div style={{ maxHeight: 480, overflowY: "auto" }}>
                  <table className="data-table">
                    <thead><tr><th>Trip</th><th>Status</th><th>Fare</th></tr></thead>
                    <tbody>
                      {filteredTrips.map((t) => (
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
            )
          )}

          {view === "settings" && <CityConfigPanel cityId={cityId} />}
          {view === "drivers" && <DriversPanel />}
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

const CONFIG_FIELDS = [
  { key: "base_fare", label: "Base fare (₹)" },
  { key: "per_km_rate", label: "Per km rate (₹)" },
  { key: "per_min_rate", label: "Per min rate (₹)" },
  { key: "booking_fee", label: "Booking fee (₹)" },
  { key: "surge_cap", label: "Surge cap (×)" },
  { key: "commission_pct", label: "Platform commission (0-1)" },
  { key: "cancellation_fee", label: "Cancellation fee (₹)" },
];

// Round 1 stakeholder council: ops + tech-lead independently flagged the same gap —
// every business rule GAP AS-06 called ambiguous was a hardcoded Python constant,
// changeable only by a deploy. This is the admin-facing, audited, DB-backed fix.
function CityConfigPanel({ cityId }) {
  const toast = useToast();
  const [config, setConfig] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const c = await api.get(`/v1/pricing/config/${cityId}`);
      setConfig(c);
      setDraft(c);
    } catch (e) {
      toast(e.message);
    }
  }, [cityId, toast]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    setSaving(true);
    try {
      const changed = {};
      for (const f of CONFIG_FIELDS) {
        if (draft[f.key] !== config[f.key]) changed[f.key] = Number(draft[f.key]);
      }
      if (Object.keys(changed).length === 0) return toast("Nothing changed");
      const updated = await api.patch(`/v1/pricing/config/${cityId}`, changed);
      setConfig(updated);
      setDraft(updated);
      toast("Saved — audit-logged, no deploy needed");
    } catch (e) {
      toast(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!config) return <div className="card"><div className="muted">Loading…</div></div>;

  return (
    <div className="card">
      <h3>City settings — {cityId}</h3>
      <div className="muted">Every business rule here is live, admin-editable, and audit-logged (see `city_config_events`). Changing it takes effect on the next fare calculation — no deploy.</div>
      {CONFIG_FIELDS.map((f) => (
        <div key={f.key}>
          <label>{f.label}</label>
          <input
            type="number" step="0.01" value={draft[f.key] ?? ""}
            onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
          />
        </div>
      ))}
      <button className="btn primary" disabled={saving} onClick={save}>Save changes</button>
    </div>
  );
}

// Round 1 stakeholder council, ops pain #2: "KYC verification has an API endpoint and
// zero UI" -- an admin could not actually use the feature that already existed.
function DriversPanel() {
  const toast = useToast();
  const [drivers, setDrivers] = useState(null);

  const load = useCallback(async () => {
    try {
      setDrivers(await api.get("/v1/admin/drivers"));
    } catch (e) {
      toast(e.message);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  async function toggleKyc(driverId, current) {
    try {
      await api.patch(`/v1/drivers/${driverId}/kyc`, { verified: !current });
      load();
    } catch (e) {
      toast(e.message);
    }
  }

  if (!drivers) return <div className="card"><div className="muted">Loading…</div></div>;

  return (
    <div className="card">
      <h3>Drivers ({drivers.length})</h3>
      <div style={{ maxHeight: 480, overflowY: "auto" }}>
        <table className="data-table">
          <thead><tr><th>Name</th><th>City</th><th>Status</th><th>Rating</th><th>KYC</th><th></th></tr></thead>
          <tbody>
            {drivers.map((d) => (
              <tr key={d.driver_id}>
                <td>{d.name}</td>
                <td>{d.city_id}</td>
                <td>{d.status}</td>
                <td>★ {d.rating_avg.toFixed(1)}</td>
                <td>{d.kyc_verified ? "✅" : "—"}</td>
                <td>
                  <button className="link" onClick={() => toggleKyc(d.driver_id, d.kyc_verified)}>
                    {d.kyc_verified ? "Revoke" : "Verify"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
