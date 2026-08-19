"use client";

import { useEffect, useState } from "react";
import { ImageOff } from "lucide-react";
import { apiBlobUrl } from "@/lib/api";
import { Lightbox } from "@/components/Lightbox";

/* An image that lives behind the login.

   A plain <img src="/api/…"> cannot work here: the browser sends that request
   without the Authorization header — the token is in localStorage, not a
   cookie — so the API answers 401 and the tag renders as a broken image with
   no clue why. So the bytes are fetched the same way every other request is,
   and handed to the tag as an object URL.

   The URL is revoked on unmount, otherwise a long scroll through a feed leaks
   every picture it passed. */

export function AuthImage(
  { path, alt = "", className, zoomable = false, caption }:
  { path: string; alt?: string; className?: string; zoomable?: boolean; caption?: string },
) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let dead = false;
    let made: string | null = null;
    setFailed(false);
    setUrl(null);

    apiBlobUrl(path)
      .then((u) => {
        if (dead) { URL.revokeObjectURL(u); return; }
        made = u;
        setUrl(u);
      })
      .catch(() => { if (!dead) setFailed(true); });

    return () => {
      dead = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path]);

  if (failed) {
    return (
      <span className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-border
                       bg-bg-soft px-2 py-1 text-[11px] text-text-dim">
        <ImageOff size={11} /> Image unavailable
      </span>
    );
  }

  if (!url) {
    // Reserve the space rather than letting the row jump when the picture
    // lands — a feed that reflows as you read it is unreadable.
    return (
      <span className="mt-2 block h-24 w-40 shrink-0 animate-pulse rounded-lg bg-bg-soft" />
    );
  }

  if (!zoomable) {
    return <img src={url} alt={alt} loading="lazy" className={className} />;
  }

  // The overlay reuses this same object URL, so opening it costs no request.
  return (
    <>
      <img
        src={url}
        alt={alt}
        loading="lazy"
        className={`${className ?? ""} cursor-zoom-in`}
        onClick={() => setOpen(true)}
      />
      {open && <Lightbox url={url} caption={caption} onClose={() => setOpen(false)} />}
    </>
  );
}
