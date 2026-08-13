"use client";

import { useEffect, useState } from "react";
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
 * The drag handle is deliberately its own small target rather than the whole
 * card. A section header holds a search box, filter tabs and a history
 * dropdown, and making all of that draggable turns every mis-click into a
 * drag.
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
  const [over, setOver] = useState<string | null>(null);

  // After mount: localStorage on the server would break hydration.
  useEffect(() => {
    const saved = readOrder(storageKey).filter((id) => ids.includes(id));
    const merged = [...saved, ...ids.filter((id) => !saved.includes(id))];
    setOrder(merged);
    // ids is derived from props and stable in practice; joining it keeps the
    // effect from re-running on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, ids.join(",")]);

  function persist(next: string[]) {
    setOrder(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {}
  }

  function move(from: string, to: string) {
    if (from === to) return;
    const fromIdx = order.indexOf(from);
    const toIdx = order.indexOf(to);
    const next = order.filter((id) => id !== from);
    // Dropping below the target has to land *after* it, or a section could
    // never be dragged into last place: inserting before the target always
    // left it one from the bottom.
    const at = next.indexOf(to) + (fromIdx < toIdx ? 1 : 0);
    next.splice(at, 0, from);
    setOrder(next);
  }

  const byId = new Map(sections.map((s) => [s.id, s.node]));

  return (
    <div className="space-y-5">
      {order.map((id) => (
        <div
          key={id}
          // Only draggable once the handle is held: without this, selecting
          // text inside a section would start a drag.
          draggable={dragging === id}
          onDragStart={(e) => {
            setDragging(id);
            e.dataTransfer.effectAllowed = "move";
            // Firefox ignores a drag with no payload.
            e.dataTransfer.setData("text/plain", id);
          }}
          onDragEnter={() => dragging && setOver(id)}
          onDragOver={(e) => {
            if (!dragging) return;
            e.preventDefault();          // without this, no drop is allowed
            if (over !== id) move(dragging, id);
            setOver(id);
          }}
          onDragEnd={() => {
            persist(order);
            setDragging(null);
            setOver(null);
          }}
          className={cn(
            "group/drag relative transition-opacity",
            dragging === id && "opacity-60",
            over === id && dragging !== id && "ring-2 ring-brand/40 rounded-xl"
          )}
        >
          {/* Sits in the card's own padding, left of the collapse chevron, and
              only shows on hover so it is out of the way until wanted. */}
          <button
            aria-label="Drag to reorder this section"
            title="Drag to reorder"
            onMouseDown={() => setDragging(id)}
            onMouseUp={() => dragging === id && !over && setDragging(null)}
            className="absolute left-0 top-3.5 z-10 hidden h-6 w-5 cursor-grab place-items-center
                       rounded text-text-dim opacity-0 transition-opacity active:cursor-grabbing
                       group-hover/drag:opacity-100 hover:text-text md:grid"
          >
            <GripVertical size={14} />
          </button>
          {byId.get(id)}
        </div>
      ))}
    </div>
  );
}
