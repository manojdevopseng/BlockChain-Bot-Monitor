"use client";

import { useEffect, useRef, useState } from "react";
import { GripVertical } from "lucide-react";
import { cn } from "@/lib/utils";

/* Sections you can drag into the order you want.
 *
 * Which section matters is personal and changes by the day — someone watching
 * Robinhood launches wants that at the top, someone watching gas wants gas. The
 * order is remembered per `storageKey`, in the browser, like the collapsed
 * state each section already keeps: it is a view preference, not data, and it
 * should not follow one person's layout onto everyone else's screen.
 *
 * Pointer events rather than HTML5 drag-and-drop. That was the first attempt
 * and it did not work: `draggable` has to be set before the gesture starts, so
 * turning it on from the handle's mousedown was a race the browser usually
 * lost, and when it did fire you got a translucent photocopy of the whole card
 * floating over the page instead of the list reordering under the cursor.
 * Pointer events start on the handle, reorder live as you pass each section,
 * and work with touch as well as a mouse.
 *
 * Saved order and current sections are merged rather than trusted: a section
 * added after someone last dragged theirs still appears, at the end.
 */

export type Section = { id: string; node: React.ReactNode };

function readOrder(key: string): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(raw) ? raw.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export function SortableSections({ storageKey, sections }: {
  storageKey: string;
  sections: Section[];
}) {
  const ids = sections.map((s) => s.id);
  const [order, setOrder] = useState<string[]>(ids);
  const [dragging, setDragging] = useState<string | null>(null);

  // The pointer handlers live on the window, so they read the current order
  // through a ref rather than a closure captured when the drag began.
  const orderRef = useRef<string[]>(ids);
  const draggingRef = useRef<string | null>(null);
  const boxes = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    orderRef.current = order;
  }, [order]);

  // After mount: localStorage on the server would break hydration.
  useEffect(() => {
    const saved = readOrder(storageKey).filter((id) => ids.includes(id));
    const merged = [...saved, ...ids.filter((id) => !saved.includes(id))];
    setOrder(merged);
    orderRef.current = merged;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, ids.join(",")]);

  function move(from: string, to: string) {
    const current = orderRef.current;
    if (from === to) return;
    const fromIdx = current.indexOf(from);
    const toIdx = current.indexOf(to);
    const next = current.filter((id) => id !== from);
    // Dropping below the target has to land *after* it, or a section could
    // never be dragged into last place: inserting before the target always
    // left it one from the bottom.
    next.splice(next.indexOf(to) + (fromIdx < toIdx ? 1 : 0), 0, from);
    orderRef.current = next;
    setOrder(next);
  }

  useEffect(() => {
    if (!dragging) return;

    function onMove(e: PointerEvent) {
      const held = draggingRef.current;
      if (!held) return;
      // Which section is under the pointer. Comparing against each box's
      // midpoint is what makes a section swap as you pass its middle rather
      // than only when you reach its far edge.
      for (const id of orderRef.current) {
        if (id === held) continue;
        const el = boxes.current[id];
        if (!el) continue;
        const box = el.getBoundingClientRect();
        if (e.clientY >= box.top && e.clientY <= box.bottom) {
          const past = e.clientY > box.top + box.height / 2;
          const heldIdx = orderRef.current.indexOf(held);
          const overIdx = orderRef.current.indexOf(id);
          if ((past && overIdx > heldIdx) || (!past && overIdx < heldIdx)) {
            move(held, id);
          }
          break;
        }
      }
    }

    function stop() {
      try {
        localStorage.setItem(storageKey, JSON.stringify(orderRef.current));
      } catch {}
      draggingRef.current = null;
      setDragging(null);
      document.body.style.userSelect = "";
    }

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragging, storageKey]);

  const byId = new Map(sections.map((s) => [s.id, s.node]));

  return (
    <div className="space-y-5">
      {order.map((id) => (
        <div
          key={id}
          ref={(el) => { boxes.current[id] = el; }}
          className={cn(
            "group/drag relative rounded-xl transition-shadow",
            dragging === id && "opacity-80 ring-2 ring-brand/50"
          )}
        >
          {/* Its own small target, in the card's own padding to the left of the
              collapse chevron. Dragging by the whole card would mean every
              mis-click on a search box or a filter tab started a drag. */}
          <button
            aria-label="Drag to reorder this section"
            title="Drag to reorder"
            onPointerDown={(e) => {
              e.preventDefault();
              draggingRef.current = id;
              setDragging(id);
              document.body.style.userSelect = "none";
            }}
            className={cn(
              "absolute left-0 top-3.5 z-10 hidden h-6 w-5 place-items-center rounded",
              "text-text-dim opacity-0 transition-opacity hover:text-text md:grid",
              "group-hover/drag:opacity-100 touch-none",
              dragging === id ? "cursor-grabbing opacity-100" : "cursor-grab"
            )}
          >
            <GripVertical size={14} />
          </button>
          {byId.get(id)}
        </div>
      ))}
    </div>
  );
}
