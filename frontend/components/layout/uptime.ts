"use client";

import { useEffect, useState } from "react";

/* Bot uptime, ticking every second.
 *
 * The backend sends it on the WebSocket heartbeat, which is every few seconds
 * — so the counter used to sit still and then jump by 5 or 10. It now keeps
 * the last reading with the moment it arrived and counts forward from there,
 * so the seconds move while the heartbeats stay exactly as frequent as they
 * were. A heartbeat re-anchors it, so drift cannot accumulate.
 *
 * A tiny store rather than context: the value changes every second and only
 * one card shows it, so nothing else should re-render for it.
 */

let anchor: { base: number; at: number } | null = null;
const listeners = new Set<() => void>();

/** Called from the WebSocket heartbeat with the server's own figure. */
export function setUptime(seconds: number): void {
  anchor = { base: seconds, at: Date.now() };
  listeners.forEach((fn) => fn());
}

function current(): number | null {
  if (!anchor) return null;
  return anchor.base + (Date.now() - anchor.at) / 1000;
}

export function useUptime(): number | null {
  const [, bump] = useState(0);
  useEffect(() => {
    const rerender = () => bump((n) => n + 1);
    listeners.add(rerender);
    const id = setInterval(rerender, 1000);
    return () => {
      listeners.delete(rerender);
      clearInterval(id);
    };
  }, []);
  return current();
}

/** `12d 04h 07m 33s`, or an em dash before the first heartbeat lands. */
export function uptimeLabel(seconds: number | null): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const pad = (n: number) => String(n).padStart(2, "0");
  const rest = `${pad(Math.floor((s % 86400) / 3600))}h ${pad(Math.floor((s % 3600) / 60))}m ${pad(s % 60)}s`;
  return d ? `${d}d ${rest}` : rest;
}
