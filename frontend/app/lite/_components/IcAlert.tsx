"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Volume2, VolumeX } from "lucide-react";
import { useApi } from "@/lib/api";

/* A sound when a starred caller shows up.

   Only the starred ones. The whole point of Important Caller is that those are
   the groups worth interrupting for, and a chime on all hundred-odd premium
   groups is a chime you would turn off within the hour.

   Two of these on the page, one per panel, each with its own switch — because
   they answer different questions. The calls one fires when a starred caller
   names a token; the tracker one fires on anything they post. Wanting the
   first without the second is the common case, which is why they are not one
   setting.

   Both are deliberately independent of their panel's filters. This is an
   alert: if the table happens to be showing one chain, an IC call on another
   still matters, and an alert you only hear when you were already looking at
   the right tab is not an alert. */

const SOUND = "/alarm.mp3";

// One element for both toggles, and one guard across them. A starred caller
// naming a token is a new call *and* a new message — with both switched on
// that is two triggers a few milliseconds apart, and two overlapping copies of
// the same alarm sound like a fault.
let shared: HTMLAudioElement | null = null;
let lastPlayed = 0;
const GAP_MS = 1500;

function ring(): Promise<void> {
  const now = Date.now();
  if (now - lastPlayed < GAP_MS) return Promise.resolve();
  lastPlayed = now;
  if (!shared) {
    shared = new Audio(SOUND);
    shared.preload = "auto";
  }
  shared.currentTime = 0;
  return shared.play();
}

const SOURCES = {
  calls: {
    path: "/api/calls?chain=all&limit=20",
    key: "lite_sound_calls",
    what: "names a token",
  },
  messages: {
    path: "/api/calls/tracker?chain=all&limit=20",
    key: "lite_sound_messages",
    what: "posts anything",
  },
} as const;

export function IcAlert({ kind }: { kind: keyof typeof SOURCES }) {
  const src = SOURCES[kind];
  const [on, setOn] = useState(false);
  const [blocked, setBlocked] = useState(false);
  // The newest row we have already accounted for. null until the first load
  // has been seen, which is what stops a page refresh replaying the backlog.
  const seen = useRef<number | null>(null);

  const { data } = useApi<any>(src.path);
  const { data: meta } = useApi<any>("/api/forwarder/group-chips");

  const starred = useMemo(
    () => new Set<number>((meta?.ic ?? []).map(Number)),
    [meta],
  );

  useEffect(() => {
    try { setOn(localStorage.getItem(src.key) === "1"); } catch {}
  }, [src.key]);

  useEffect(() => {
    const items: any[] = data?.items ?? [];
    if (!items.length) return;

    const newest = Math.max(...items.map((r) => r.ts || 0));
    if (seen.current === null) {
      // First sight of the feed. Everything here is history as far as this
      // session is concerned.
      seen.current = newest;
      return;
    }
    if (newest <= seen.current) return;

    const fresh = items.filter((r) => (r.ts || 0) > seen.current!);
    seen.current = newest;
    if (!on || !starred.size) return;

    // One sound per batch, not per row: a token live on three chains is three
    // rows of the same call.
    if (fresh.some((r) => starred.has(Number(r.chat_id)))) {
      ring().then(() => setBlocked(false)).catch(() => setBlocked(true));
    }
  }, [data, on, starred]);

  function toggle() {
    const next = !on;
    setOn(next);
    try { localStorage.setItem(src.key, next ? "1" : "0"); } catch {}
    if (next) {
      // Played inside the click, which is what a browser needs before it will
      // ever let a page make noise on its own. It doubles as a preview: you
      // hear what you have just switched on.
      lastPlayed = 0;
      ring().then(() => setBlocked(false)).catch(() => setBlocked(true));
    }
  }

  const label = on
    ? (blocked
        ? "Sound is on but the browser blocked it — click to try again"
        : `Sound on — when a starred caller ${src.what} (${starred.size} starred)`)
    : `Sound off — play a sound when a starred caller ${src.what}`;

  return (
    <button
      onClick={toggle}
      title={label}
      aria-label={label}
      aria-pressed={on}
      className={`flex h-7 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-[11px] transition-colors ${
        on && !blocked
          ? "border-accent-amber/40 bg-accent-amber/15 text-accent-amber"
          : blocked
            ? "border-accent-red/40 bg-accent-red/10 text-accent-red"
            : "border-border text-text-dim hover:border-accent-amber/40 hover:text-accent-amber"
      }`}
    >
      {on && !blocked ? <Volume2 size={12} /> : <VolumeX size={12} />}
      <span className="hidden sm:inline">IC alert</span>
    </button>
  );
}
