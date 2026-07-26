"use client";

import { useEffect, useRef, useState } from "react";

export type WSEvent = { type: string; data: any };

// Connects to the FastAPI /ws hub and invokes onEvent for each message.
// Auto-reconnects with backoff. Backend pushes heartbeat + service_changed etc.
export function useWebSocket(onEvent?: (e: WSEvent) => void) {
  const [connected, setConnected] = useState(false);
  const ref = useRef<WebSocket | null>(null);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    let closed = false;
    let retry = 0;
    let timer: any;

    const connect = () => {
      if (closed) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      // In production the page is served by nginx, which proxies /ws to the
      // API — so use the same origin. Hard-coding :8000 broke this on the
      // server, where that port is deliberately not exposed. Only local dev
      // (Next on :3000, API on :8000) needs the explicit port.
      const base =
        process.env.NEXT_PUBLIC_WS_BASE ||
        (window.location.port === "3000"
          ? `${proto}://${window.location.hostname}:8000`
          : `${proto}://${window.location.host}`);
      const ws = new WebSocket(`${base}/ws`);
      ref.current = ws;
      ws.onopen = () => {
        setConnected(true);
        retry = 0;
      };
      ws.onmessage = (ev) => {
        try {
          cb.current?.(JSON.parse(ev.data));
        } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          retry = Math.min(retry + 1, 6);
          timer = setTimeout(connect, retry * 1000);
        }
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ref.current?.close();
    };
  }, []);

  return { connected };
}
