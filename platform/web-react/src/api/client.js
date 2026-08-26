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

// Round 5 stakeholder council (tech lead): found live during this round's own
// testing -- a browser tab left open past `jwt_access_ttl_seconds` (1 hour) had
// EVERY subsequent call fail with 401, forever, with no path back except manually
// signing out and re-verifying OTP from scratch. The access token expiring is
// completely normal; having no refresh path is the actual bug -- `refresh_token`
// (14-day TTL) was already being stored on login and never used for anything.
// `refreshPromise` collapses concurrent 401s (e.g. several in-flight requests all
// hitting the same expired token at once) into a single refresh call.
let refreshPromise = null;

async function doRefresh() {
  const session = loadSession();
  if (!session?.refresh_token) return null;
  if (!refreshPromise) {
    refreshPromise = fetch(`${GATEWAY_BASE}/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session.refresh_token }),
    })
      .then(async (resp) => {
        if (!resp.ok) return null;
        const fresh = await resp.json();
        // /v1/auth/refresh rotates both tokens (a fresh refresh_token too, per
        // services/identity/service.py) -- `fresh` wins on both.
        const merged = { ...session, ...fresh };
        saveSession(merged);
        return merged;
      })
      .catch(() => null)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function request(method, path, body, _isRetry = false) {
  const session = loadSession();
  const headers = { "Content-Type": "application/json" };
  if (session?.access_token) headers["Authorization"] = `Bearer ${session.access_token}`;

  const resp = await fetch(`${GATEWAY_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (resp.status === 401 && !_isRetry && session?.refresh_token) {
    const refreshed = await doRefresh();
    if (refreshed) return request(method, path, body, true);
    // Refresh token itself is invalid/expired -- there is genuinely no way back
    // short of signing in again. Clear the stale session and reload so the app
    // re-reads (nothing) from storage and lands cleanly on the login screen,
    // instead of leaving a role page up that will 401 on every next action too.
    clearSession();
    window.location.reload();
  }

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
