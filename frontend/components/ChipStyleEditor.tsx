"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Palette, RotateCcw, X } from "lucide-react";
import { ColorPicker, isDark } from "@/components/ColorPicker";
import { ChipStyle, GroupChip } from "@/components/GroupChip";

/* The per-caller chip editor: three colours, one picker, live preview.
 *
 * Nothing is written until Save, so dragging through forty shades costs no
 * requests — but the preview follows every pixel of the drag, which is the
 * point of picking a colour by eye instead of typing a hex. */

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

export function ChipStyleEditor({ name, value, onSave }: {
  name: string;
  value?: ChipStyle | null;
  onSave: (chip: ChipStyle | null) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<Target>("bg");
  const [draft, setDraft] = useState<ChipStyle>(value || SEED);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Reopening after a save elsewhere should show what is actually stored, not
  // the draft from last time.
  useEffect(() => { if (open) setDraft(value || SEED); }, [open, value]);

  // Click-away closes without saving — the draft is discarded, which is the
  // safe reading of "I clicked somewhere else".
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
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

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-64 rounded-lg border border-border bg-bg-card p-3 shadow-xl">
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
              <div key={label} className="rounded-md border border-border-soft p-2 text-center"
                   style={{ background: bg }}>
                <GroupChip label={name} style={draft} />
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
        </div>
      )}
    </div>
  );
}
