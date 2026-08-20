"use client";

import { useEffect, useRef, useState } from "react";
import { Film, Loader2, Play, VideoOff } from "lucide-react";
import { apiBlobUrl } from "@/lib/api";

/* A GIF or clip from a caller, behind the login and behind a click.

   Two problems, one answer. The endpoint needs an Authorization header and a
   <video src> cannot carry one, so the bytes have to be fetched the way every
   other request is and handed over as an object URL — the same trick AuthImage
   uses for pictures.

   But a picture is tens of kilobytes and a clip is megabytes, and a feed of
   eighty messages that fetched every one of them on sight would pull down more
   in a minute than the rest of the dashboard does in an hour. So nothing is
   fetched until it is asked for: until then this is a button, and the server
   is not touched at all. */

export function AuthMedia(
  { path, kind, className }:
  { path: string; kind: "gif" | "video"; className?: string },
) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const made = useRef<string | null>(null);

  useEffect(() => () => {
    // A long scroll past a played clip would otherwise hold every one of them
    // in memory for as long as the tab is open.
    if (made.current) URL.revokeObjectURL(made.current);
  }, []);

  async function load() {
    if (url || busy) return;
    setBusy(true);
    try {
      const u = await apiBlobUrl(path);
      made.current = u;
      setUrl(u);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  if (failed) {
    return (
      <span className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-border
                       bg-bg-soft px-2 py-1 text-[11px] text-text-dim">
        <VideoOff size={11} /> {kind === "gif" ? "GIF" : "Video"} unavailable
      </span>
    );
  }

  if (!url) {
    return (
      <button
        onClick={load}
        disabled={busy}
        title={`Load this ${kind === "gif" ? "GIF" : "video"}`}
        className="mt-2 flex items-center gap-2 rounded-lg border border-border bg-bg-soft
                   px-3 py-2 text-[11px] text-text-muted transition-colors
                   hover:border-brand/40 hover:text-brand-soft disabled:opacity-60"
      >
        {busy ? <Loader2 size={13} className="animate-spin" />
              : kind === "gif" ? <Film size={13} /> : <Play size={13} />}
        {busy ? "Loading…" : kind === "gif" ? "Play GIF" : "Play video"}
      </button>
    );
  }

  // Already clicked, so autoplay is what was asked for. Muted and inline
  // because a feed that starts making noise on its own is a feed you close.
  return (
    <video
      src={url}
      className={className}
      autoPlay
      muted
      playsInline
      loop={kind === "gif"}
      controls={kind === "video"}
      preload="none"
    />
  );
}
