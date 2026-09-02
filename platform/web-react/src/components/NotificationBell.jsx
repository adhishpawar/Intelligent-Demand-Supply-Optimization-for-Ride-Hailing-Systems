import { useCallback, useEffect, useRef, useState } from "react";
import { api, loadSession, wsUrl } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useReconnectingSocket } from "../hooks/useReconnectingSocket";

// Round 3 stakeholder council: services/notification's real Kafka consumer + real
// persisted inbox + real Redis-pub/sub live push (PLAN §2.1 "REAL-LITE") existed,
// fully working, on every ride.*/payment.* event all night -- and had no UI
// anywhere. A rider or driver had zero way to see "your trip has ended, fare:
// ₹X" or "your payment succeeded" except by watching the trip panel itself change.
// This surfaces the inbox that was already being filled.
export default function NotificationBell() {
  const { userId } = useAuth();
  const [notifications, setNotifications] = useState([]);
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      setNotifications(await api.get("/v1/notifications"));
    } catch {
      /* non-critical -- bell just shows stale/empty until the next successful poll */
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const wsEndpoint = userId ? wsUrl("notification", `/v1/ws/notifications/${userId}`, loadSession()?.access_token) : null;
  useReconnectingSocket(wsEndpoint, {
    onOpen: refresh, // catch-up: Redis pub/sub has no replay (same principle as the offer/trip-track sockets)
    onMessage: (evt) => {
      const data = JSON.parse(evt.data);
      setNotifications((prev) => [{ ...data, read_at: null, created_at: new Date().toISOString() }, ...prev]);
    },
  });

  // Close the dropdown on an outside click.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  async function markRead(notificationId) {
    setNotifications((prev) => prev.map((n) => (n.notification_id === notificationId ? { ...n, read_at: new Date().toISOString() } : n)));
    try {
      await api.post(`/v1/notifications/${notificationId}/read`);
    } catch {
      /* non-critical -- worst case it shows unread again after the next refresh */
    }
  }

  const unreadCount = notifications.filter((n) => !n.read_at).length;

  return (
    <div className="notif-bell-root" ref={rootRef}>
      <button className="notif-bell-btn" onClick={() => setOpen((o) => !o)} title="Notifications">
        🔔{unreadCount > 0 && <span className="notif-badge">{unreadCount > 9 ? "9+" : unreadCount}</span>}
      </button>
      {open && (
        <div className="notif-dropdown">
          <div className="notif-dropdown-header">Notifications</div>
          {notifications.length === 0 && <div className="muted" style={{ padding: "12px 14px" }}>Nothing yet.</div>}
          <div className="notif-list">
            {notifications.map((n) => (
              <div
                key={n.notification_id}
                className={`notif-item ${n.read_at ? "" : "unread"}`}
                onClick={() => !n.read_at && markRead(n.notification_id)}
              >
                <div className="notif-item-title">{n.title}</div>
                <div className="notif-item-body">{n.body}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
