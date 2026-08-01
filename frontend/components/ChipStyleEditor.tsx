"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Palette, RotateCcw, X } from "lucide-react";
import { ColorPicker, isDark } from "@/components/ColorPicker";
import { ChipStyle, GroupChip } from "@/components/GroupChip";

/* The per-caller chip editor: three colours, one picker, live preview.
 *
 * Nothing is written until Save, so dragging through forty shades costs no
 * requests — but the preview follows every pixel of the drag, which is the
 * point of picking a colour by eye instead of typing a hex.
 *
 * The panel is portalled to <body> and positioned from the button's own rect.
 * An absolutely-positioned panel was clipped to a sliver: the group list lives
 * inside a scroll container, and no z-index escapes an ancestor's overflow. */

type Target = keyof ChipStyle;

const TARGETS: { key: Target; label: string }[] = [
  { key: "bg", label: "Background" },
  { key: "text", label: "Text" },
  { key: "border", label: "Border" },
];

// What a group starts from when it has never been styled. Deliberately the
// dashboard's own brand violet rather than something loud, so the first drag
// starts somewhere usable.
const SEED: ChipStyle = { bg: "#2a2344", text: "#c4b5fd", border: "#7c5cff" };

// Fixed size, so the panel can be placed before it has ever been measured.
const PANEL_W = 256;
const PANEL_H = 400;

export function ChipStyleEditor({ name, value, onSave }: {
  name: string;
  value?: ChipStyle | null;
  onSave: (chip: ChipStyle | null) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<Target>("bg");
  const [draft, setDraft] = useState<ChipStyle>(value || SEED);
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
  useEffect(() => { if (open) setDraft(value || SEED); }, [open, value]);

  // Click-away closes without saving — the draft is discarded, which is the
  // safe reading of "I clicked somewhere else".
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      // The panel is no longer a child of the button, so it has to be asked
      // separately — otherwise clicking inside the picker closes it.
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

  async function commit(chip: ChipStyle | null) {
    setBusy(true);
    try {
      await onSave(chip);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative" ref={box}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={value ? "Change this caller's chip colours" : "Give this caller its own chip colours"}
        className="grid h-6 w-6 place-items-center rounded border border-border text-text-dim transition-colors hover:border-brand/40 hover:text-brand-soft"
        style={value ? { background: value.bg, borderColor: value.border, color: value.text } : undefined}
      >
        <Palette size={12} />
      </button>

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

          {/* Preview on both page backgrounds. One hex is stored for both
              themes, so a colour that only works in the one you happen to be
              in is a trap worth seeing before saving. */}
          <div className="mb-2 grid grid-cols-2 gap-1.5">
            {[["#0b0d14", "Dark"], ["#f8fafc", "Light"]].map(([bg, label]) => (
              <div key={label} className="overflow-hidden rounded-md border border-border-soft p-2 text-center"
                   style={{ background: bg }}>
                <GroupChip label={name} style={draft} className="max-w-full" />
                <div className="mt-1 text-[9px] uppercase tracking-wide"
                     style={{ color: isDark(bg) ? "#64748b" : "#94a3b8" }}>{label}</div>
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
                title="Back to the default chip"
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
