"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Minus, Plus, RotateCcw, X } from "lucide-react";

/* A picture, full screen, zoomable.

   Rendered through a portal onto <body>: the feed it is opened from lives
   inside a scrolling panel with its own overflow, and an overlay drawn inside
   that would be clipped by it.

   The image is handed in as an already-loaded object URL, so opening costs no
   request — which is the whole reason this is worth having rather than a link
   to a new tab. */

const MIN = 1;
const MAX = 8;

export function Lightbox(
  { url, caption, onClose }: { url: string; caption?: string; onClose: () => void },
) {
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const pinch = useRef<{ dist: number; scale: number } | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const reset = useCallback(() => { setScale(1); setPan({ x: 0, y: 0 }); }, []);

  const zoomBy = useCallback((factor: number) => {
    setScale((s) => {
      const next = Math.min(MAX, Math.max(MIN, s * factor));
      // Back at 1x there is nothing to pan to, and leaving an old offset there
      // would show the picture off-centre for no reason.
      if (next === 1) setPan({ x: 0, y: 0 });
      return next;
    });
  }, []);

  // Esc closes, +/- zoom, 0 resets — the keys anyone would try first.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "+" || e.key === "=") zoomBy(1.3);
      else if (e.key === "-" || e.key === "_") zoomBy(1 / 1.3);
      else if (e.key === "0") reset();
    }
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose, zoomBy, reset]);

  function onWheel(e: React.WheelEvent) {
    // The overlay owns the wheel while it is open, so the feed underneath does
    // not scroll away behind it.
    e.stopPropagation();
    zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }

  function onPointerDown(e: React.PointerEvent) {
    if (scale <= 1) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    setPan({ x: d.px + (e.clientX - d.x), y: d.py + (e.clientY - d.y) });
  }

  function onPointerUp() { drag.current = null; }

  // Two fingers on a phone. Distance between them maps straight onto scale.
  function onTouchStart(e: React.TouchEvent) {
    if (e.touches.length !== 2) return;
    const [a, b] = [e.touches[0], e.touches[1]];
    pinch.current = {
      dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
      scale,
    };
  }

  function onTouchMove(e: React.TouchEvent) {
    const p = pinch.current;
    if (!p || e.touches.length !== 2) return;
    const [a, b] = [e.touches[0], e.touches[1]];
    const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    setScale(Math.min(MAX, Math.max(MIN, p.scale * (dist / p.dist))));
  }

  function onTouchEnd() { pinch.current = null; }

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex flex-col bg-black/90 backdrop-blur-sm"
      onClick={onClose}
      onWheel={onWheel}
    >
      {/* Controls. stopPropagation so using them does not close the overlay. */}
      <div
        className="flex shrink-0 items-center justify-between gap-3 px-4 py-3 text-white/80"
        onClick={(e) => e.stopPropagation()}
      >
        <span className="min-w-0 truncate text-xs">{caption}</span>
        <div className="flex shrink-0 items-center gap-1">
          <span className="mr-1 w-12 text-right font-mono text-xs tabular-nums">
            {Math.round(scale * 100)}%
          </span>
          <button onClick={() => zoomBy(1 / 1.3)} title="Zoom out"
                  className="grid h-8 w-8 place-items-center rounded-lg hover:bg-white/10">
            <Minus size={16} />
          </button>
          <button onClick={() => zoomBy(1.3)} title="Zoom in"
                  className="grid h-8 w-8 place-items-center rounded-lg hover:bg-white/10">
            <Plus size={16} />
          </button>
          <button onClick={reset} title="Reset"
                  className="grid h-8 w-8 place-items-center rounded-lg hover:bg-white/10">
            <RotateCcw size={15} />
          </button>
          <button onClick={onClose} title="Close (Esc)"
                  className="grid h-8 w-8 place-items-center rounded-lg hover:bg-white/10">
            <X size={17} />
          </button>
        </div>
      </div>

      <div
        className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onDoubleClick={() => (scale > 1 ? reset() : zoomBy(2.5))}
        style={{ cursor: scale > 1 ? (drag.current ? "grabbing" : "grab") : "zoom-in" }}
      >
        <img
          src={url}
          alt={caption || ""}
          draggable={false}
          className="max-h-full max-w-full select-none object-contain"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
            transition: drag.current || pinch.current ? "none" : "transform .12s ease-out",
          }}
        />
      </div>

      <p className="shrink-0 pb-3 text-center text-[11px] text-white/40">
        Scroll or pinch to zoom · drag to move · double-click to fit · Esc to close
      </p>
    </div>,
    document.body,
  );
}
