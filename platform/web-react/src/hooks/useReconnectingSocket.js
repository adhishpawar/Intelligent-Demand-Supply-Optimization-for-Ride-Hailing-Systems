import { useEffect, useRef, useState } from "react";

/**
 * A WebSocket that reconnects itself with backoff on an unexpected close — PLAN
 * §5.1 explicitly keeps "WebSocket reconnect/backfill" in the never-cut tier for
 * rider live tracking, and this session's own testing found a related real gap
 * (Redis pub/sub has no replay — see `current-offer` catch-up on the driver side).
 * The reconnect here handles the connection-loss half of that story; `onOpen` is
 * the hook for a caller to run its OWN catch-up fetch every time a connection
 * (re)establishes, so a dropped-and-restored socket never leaves the UI stale.
 *
 * `url` may be null/undefined to mean "don't connect yet" (e.g. no active trip).
 */
export function useReconnectingSocket(url, { onMessage, onOpen } = {}) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const attemptRef = useRef(0);
  const closedByUsRef = useRef(false);
  const timerRef = useRef(null);
  const callbacksRef = useRef({ onMessage, onOpen });
  callbacksRef.current = { onMessage, onOpen };

  useEffect(() => {
    if (!url) {
      setConnected(false);
      return;
    }
    closedByUsRef.current = false;
    attemptRef.current = 0;

    function connect() {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setConnected(true);
        callbacksRef.current.onOpen?.();
      };
      ws.onmessage = (evt) => callbacksRef.current.onMessage?.(evt);
      ws.onclose = () => {
        setConnected(false);
        if (closedByUsRef.current) return;
        const delay = Math.min(1000 * 2 ** attemptRef.current, 8000);
        attemptRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      };
      ws.onerror = () => ws.close();
    }

    connect();

    return () => {
      closedByUsRef.current = true;
      clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [url]);

  return { connected };
}
