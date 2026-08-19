"use client";

import { MessageSquare, Palette } from "lucide-react";
import { ChipStyle, GroupChip } from "@/components/GroupChip";
import { StylePopover } from "@/components/StylePopover";

/* Two editors, one panel. Both pick three colours for one caller; they differ
 * only in what they are colouring, which is what the preview shows.
 *
 * Nothing is written until Save, so dragging through forty shades costs no
 * requests — but the preview follows every pixel of the drag, which is the
 * point of picking a colour by eye instead of typing a hex. */

// What a caller starts from when it has never been styled. Deliberately the
// dashboard's own brand violet rather than something loud, so the first drag
// starts somewhere usable.
const CHIP_SEED: ChipStyle = { bg: "#2a2344", text: "#c4b5fd", border: "#7c5cff" };

// The tracker box is a large surface behind several lines of someone else's
// text, so it starts far quieter than the chip does — a pill's background at
// box size is a wall of colour.
const BOX_SEED: ChipStyle = { bg: "#161326", text: "#cbc3f0", border: "#4c3f7a" };

export function ChipStyleEditor({ name, value, onSave }: {
  name: string;
  value?: ChipStyle | null;
  onSave: (chip: ChipStyle | null) => Promise<void> | void;
}) {
  return (
    <StylePopover
      name={name}
      value={value}
      seed={CHIP_SEED}
      onSave={onSave}
      title={value ? "Change this caller's chip colours"
                   : "Give this caller its own chip colours"}
      trigger={(style) => (
        <button
          className="grid h-6 w-6 place-items-center rounded border border-border text-text-dim transition-colors hover:border-brand/40 hover:text-brand-soft"
          style={style ? { background: style.bg, borderColor: style.border, color: style.text } : undefined}
        >
          <Palette size={12} />
        </button>
      )}
      preview={(draft) => (
        <div className="text-center">
          <GroupChip label={name} style={draft} className="max-w-full" />
        </div>
      )}
    />
  );
}

/* The TG Tracker's message box on the Lite dashboard — the whole card behind a
 * caller's post, not a pill in a table cell. Its own style for that reason: a
 * colour that reads as an 11px chip is rarely the one you want behind four
 * lines of text. */
export function TrackerStyleEditor({ name, value, onSave }: {
  name: string;
  value?: ChipStyle | null;
  onSave: (style: ChipStyle | null) => Promise<void> | void;
}) {
  return (
    <StylePopover
      name={name}
      value={value}
      seed={BOX_SEED}
      onSave={onSave}
      title={value ? "Change this caller's TG Tracker box"
                   : "Give this caller its own TG Tracker box"}
      trigger={(style) => (
        <button
          className="grid h-6 w-6 place-items-center rounded border border-border text-text-dim transition-colors hover:border-brand/40 hover:text-brand-soft"
          style={style ? { background: style.bg, borderColor: style.border, color: style.text } : undefined}
        >
          <MessageSquare size={12} />
        </button>
      )}
      preview={(draft) => (
        // A miniature of the real card: name line, a line of message, so the
        // colour is judged against text rather than against an empty swatch.
        <div className="rounded-md border px-1.5 py-1 text-left"
             style={{ background: draft.bg, borderColor: draft.border, color: draft.text }}>
          <div className="truncate text-[9px] font-semibold">{name}</div>
          <div className="truncate text-[9px] opacity-75">Sample call text…</div>
        </div>
      )}
    />
  );
}
