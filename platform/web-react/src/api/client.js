// Every role's app talks ONLY to the gateway (PLAN §3.1 Layer 1) — never directly to
// a backend service for REST calls — so RBAC enforcement is never something the
// frontend can accidentally bypass by knowing a service's internal port.
export const GATEWAY_BASE = "http://localhost:8000";
export const SESSION_KEY = "ridehail_session_v1";

// sessionStorage, deliberately, NOT localStorage — found live during this session's
// own testing: localStorage is shared across every tab of the same origin, so
// logging in as a driver in a second tab silently overwrote the rider session an
// already-open first tab was using, and every subsequent "rider" API call started
// authenticating as the driver instead (a 403 from the gateway's own Layer-1 role
// check was the symptom). sessionStorage is scoped per TAB, which is exactly the
// isolation a demo that opens rider + driver + admin side by side needs — each tab
// keeps its own role's session independently, at the acceptable cost of a session
// not surviving a closed tab (a real trade-off for a demo, not a hidden one).
const store = window.sessionStorage;

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function loadSession() {
  try {
    return JSON.parse(store.getItem(SESSION_KEY) || "null");
  } catch {
    return null;
  }
}

export function saveSession(session) {
  store.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession() {
  store.removeItem(SESSION_KEY);
}

async function request(method, path, body) {
  const session = loadSession();
  const headers = { "Content-Type": "application/json" };
  if (session?.access_token) headers["Authorization"] = `Bearer ${session.access_token}`;

  const resp = await fetch(`${GATEWAY_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await resp.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!resp.ok) throw new ApiError(resp.status, data);
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
};

// WebSocket endpoints (live trip tracking, driver offers, notifications) are NOT
// proxied through the gateway tonight — a documented scope simplification (see
// services/trip/router.py's WS handler docstrings) — the frontend connects directly
// to the owning service's port, with the JWT as a query param (browsers cannot set
// custom headers on a WS handshake) verified server-side like any bearer token.
const SERVICE_PORTS = { trip: 8004, notification: 8008 };

export function wsUrl(service, path, token) {
  const port = SERVICE_PORTS[service];
  const sep = path.includes("?") ? "&" : "?";
  return `ws://localhost:${port}${path}${sep}token=${encodeURIComponent(token)}`;
}
