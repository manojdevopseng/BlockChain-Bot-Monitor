"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, RotateCcw, X } from "lucide-react";
import { ColorPicker } from "@/components/ColorPicker";
import { ChipStyle } from "@/components/GroupChip";

/* The shell both style editors sit in: a trigger button, a portalled panel,
 * three colour targets and one picker.
 *
 * Extracted when the tracker gained its own colours. Everything here is about
 * where the panel goes and when it closes, and none of it knows what is being
 * styled — the caller supplies the trigger's look and the preview, which is
 * the only part that differs between a chip and a message box.
 *
 * Portalled to <body> and positioned from the button's own rect. An absolutely
 * positioned panel was clipped to a sliver: the group list lives inside a
 * scroll container, and no z-index escapes an ancestor's overflow. */

type Target = keyof ChipStyle;

const TARGETS: { key: Target; label: string }[] = [
  { key: "bg", label: "Background" },
  { key: "text", label: "Text" },
  { key: "border", label: "Border" },
];

// Fixed size, so the panel can be placed before it has ever been measured.
const PANEL_W = 268;
const PANEL_H = 430;

export function StylePopover({
  name, value, seed, onSave, trigger, preview, title,
}: {
  name: string;
  value?: ChipStyle | null;
  seed: ChipStyle;
  onSave: (style: ChipStyle | null) => Promise<void> | void;
  /** The button, given the stored style so it can show what is set. */
  trigger: (style: ChipStyle | null | undefined) => React.ReactNode;
  /** Drawn once per theme swatch, with the live draft. */
  preview: (style: ChipStyle, pageBg: string) => React.ReactNode;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<Target>("bg");
  const [draft, setDraft] = useState<ChipStyle>(value || seed);
  const [busy, setBusy] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  // Right-aligned under the button, flipped above when the bottom of the
  // window is closer than the panel is tall, and nudged back inside if either
  // edge would cut it off.
  const place = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    setAt({
      top: below < PANEL_H + 12 && r.top > PANEL_H ? r.top - PANEL_H - 6 : r.bottom + 6,
      left: Math.min(Math.max(8, r.right - PANEL_W), window.innerWidth - PANEL_W - 8),
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    place();
    // Follow the button: the list it sits in scrolls, and so does the page.
    // `true` catches scrolls on those inner containers too, not just window.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  // Reopening after a save elsewhere should show what is actually stored, not
  // the draft from last time.
  useEffect(() => { if (open) setDraft(value || seed); }, [open, value, seed]);

  // Click-away closes without saving — the draft is discarded, which is the
  // safe reading of "I clicked somewhere else".
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!box.current?.contains(t) && !panel.current?.contains(t)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  async function commit(style: ChipStyle | null) {
    setBusy(true);
    try {
      await onSave(style);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative" ref={box}>
      <span onClick={() => setOpen((o) => !o)} title={title}>{trigger(value)}</span>

      {open && at && createPortal(
        <div ref={panel} style={{ top: at.top, left: at.left, width: PANEL_W }}
             className="fixed z-[100] rounded-lg border border-border bg-bg-card p-3 shadow-xl">
          <div className="mb-2 flex items-center justify-between">
            <span className="truncate text-xs font-medium text-text" title={name}>{name}</span>
            <button onClick={() => setOpen(false)}
                    className="grid h-5 w-5 place-items-center rounded text-text-dim hover:text-text">
              <X size={12} />
            </button>
          </div>

          {/* Both page backgrounds. One hex is stored for both themes, so a
              colour that only works in the one you happen to be in is a trap
              worth seeing before saving. */}
          <div className="mb-2 grid grid-cols-2 gap-1.5">
            {[["#0b0d14", "Dark"], ["#f8fafc", "Light"]].map(([bg, label]) => (
              <div key={label}
                   className="overflow-hidden rounded-md border border-border-soft p-2"
                   style={{ background: bg }}>
                {preview(draft, bg)}
                <div className="mt-1 text-center text-[9px] uppercase tracking-wide"
                     style={{ color: label === "Dark" ? "#64748b" : "#94a3b8" }}>
                  {label}
                </div>
              </div>
            ))}
          </div>

          <div className="mb-2 flex gap-1">
            {TARGETS.map((t) => (
              <button key={t.key} onClick={() => setTarget(t.key)}
                className={`flex-1 rounded-md border px-1.5 py-1 text-[10px] font-medium transition-colors ${
                  target === t.key
                    ? "border-brand/40 bg-brand/15 text-brand-soft"
                    : "border-border text-text-dim hover:text-text-muted"
                }`}>
                <span className="mr-1 inline-block h-2 w-2 rounded-full align-middle"
                      style={{ background: draft[t.key] }} />
                {t.label}
              </button>
            ))}
          </div>

          <ColorPicker
            value={draft[target]}
            onChange={(hex) => setDraft((d) => ({ ...d, [target]: hex }))}
          />

          <div className="mt-3 flex gap-1.5">
            <button disabled={busy} onClick={() => commit(draft)}
              className="flex flex-1 items-center justify-center gap-1 rounded-md bg-brand px-2 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50">
              <Check size={12} /> Save
            </button>
            {value && (
              <button disabled={busy} onClick={() => commit(null)}
                title="Back to the default"
                className="flex items-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs text-text-dim transition-colors hover:text-accent-red disabled:opacity-50">
                <RotateCcw size={12} /> Reset
              </button>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
