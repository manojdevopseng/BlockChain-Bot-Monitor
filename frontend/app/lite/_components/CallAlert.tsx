"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Volume2, VolumeX } from "lucide-react";
import { useApi } from "@/lib/api";

/* A sound when a starred caller calls.

   Only the starred ones. The whole point of Important Caller is that those are
   the groups worth interrupting for, and a chime on all hundred-odd premium
   groups is a chime you would turn off within the hour.

   Deliberately independent of the section's filters. This is an alert: if the
   table happens to be filtered to one chain, an IC call on another still
   matters, and an alert you only hear when you were already looking at the
   right tab is not an alert. So it watches its own small unfiltered query,
   which revalidates on the same socket event the table does and costs one
   twenty-row request. */

const KEY = "lite_call_sound";
const SOUND = "/alarm.mp3";

export function CallAlert() {
  const [on, setOn] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  // The newest call we have already accounted for. null until the first load
  // has been seen, which is what stops a page refresh playing the backlog.
  const seen = useRef<number | null>(null);

  const { data } = useApi<any>("/api/calls?chain=all&limit=20");
  const { data: meta } = useApi<any>("/api/forwarder/group-chips");

  const starred = useMemo(
    () => new Set<number>((meta?.ic ?? []).map(Number)),
    [meta],
  );

  useEffect(() => {
    try { setOn(localStorage.getItem(KEY) === "1"); } catch {}
    audio.current = new Audio(SOUND);
    audio.current.preload = "auto";
  }, []);

  useEffect(() => {
    const items: any[] = data?.items ?? [];
    if (!items.length) return;

    const newest = Math.max(...items.map((c) => c.ts || 0));
    if (seen.current === null) {
      // First sight of the feed. Everything here is history as far as this
      // session is concerned.
      seen.current = newest;
      return;
    }
    if (newest <= seen.current) return;

    const fresh = items.filter((c) => (c.ts || 0) > seen.current!);
    seen.current = newest;
    if (!on || !starred.size) return;

    // One sound per batch, not per row: a token live on three chains is three
    // rows of the same call, and one caller posting twice in a second should
    // not overlap the chime with itself.
    if (fresh.some((c) => starred.has(Number(c.chat_id)))) {
      const el = audio.current;
      if (!el) return;
      el.currentTime = 0;
      el.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    }
  }, [data, on, starred]);

  function toggle() {
    const next = !on;
    setOn(next);
    try { localStorage.setItem(KEY, next ? "1" : "0"); } catch {}
    if (next && audio.current) {
      // Played inside the click, which is what a browser needs before it will
      // ever let a page make noise on its own. It doubles as a preview: you
      // hear what you have just switched on.
      audio.current.currentTime = 0;
      audio.current.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    }
  }

  const label = on
    ? (blocked
        ? "Sound is on but the browser blocked it — click to try again"
        : `Sound on — ${starred.size} starred caller${starred.size === 1 ? "" : "s"}`)
    : "Sound off — play a sound when a starred (IC) caller calls";

  return (
    <button
      onClick={toggle}
      title={label}
      aria-label={label}
      aria-pressed={on}
      className={`flex h-8 items-center gap-1.5 rounded-lg border px-2 text-xs transition-colors ${
        on && !blocked
          ? "border-accent-amber/40 bg-accent-amber/15 text-accent-amber"
          : blocked
            ? "border-accent-red/40 bg-accent-red/10 text-accent-red"
            : "border-border text-text-dim hover:border-accent-amber/40 hover:text-accent-amber"
      }`}
    >
      {on && !blocked ? <Volume2 size={13} /> : <VolumeX size={13} />}
      <span className="hidden sm:inline">IC alert</span>
    </button>
  );
}
