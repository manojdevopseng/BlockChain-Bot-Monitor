"use client";

import { createContext, useContext, useEffect, useState } from "react";

/** A row's age, ticking once a second.
 *
 * Ages were rendered once per fetch, so a row sat on "1m" for a whole minute
 * and the section looked frozen between refreshes. A tick fixes that, but
 * ticking the section itself re-renders every row every second — fine for
 * forty rows, not for hundreds. So the clock lives in a context and only the
 * age cells subscribe to it: one timer, and a second's work is a few dozen
 * text nodes.
 *
 * Shared because the AI Narrative and Detections pages both show it, and an
 * age that ticks on one page and not the other is the kind of difference that
 * reads as a difference in the data.
 */
const TickContext = createContext(0);

export function TickProvider({ children }: { children: React.ReactNode }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return <TickContext.Provider value={n}>{children}</TickContext.Provider>;
}

// Seconds only while they mean something. A launch is worth watching by the
// second in its first minute; after that the seconds are just noise ticking in
// the corner of the eye, so the age rounds to minutes and then to hours.
export function ageLabel(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  const h = Math.floor(s / 3600);
  return `${h}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export function Age({ ts }: { ts?: number }) {
  useContext(TickContext);
  return <>{ts ? ageLabel(ts) : "—"}</>;
}
