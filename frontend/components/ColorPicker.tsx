"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/* A drag-to-pick colour picker: saturation/brightness square + hue strip.
 *
 * Built here rather than pulled in, for the same reason the rest of
 * components/ui is hand-rolled — one small file beats a dependency, and it can
 * speak the dashboard's own tokens. It reports on every pointer move, so
 * whatever is previewing the colour updates while the handle is still moving.
 *
 * Pointer events, not mouse events: the same code then works under a finger on
 * the phone, and setPointerCapture keeps the drag alive when the pointer
 * leaves the square — without it, dragging past the edge drops the handle. */

type HSV = { h: number; s: number; v: number };

function hsvToRgb({ h, s, v }: HSV) {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
      : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [r + m, g + m, b + m].map((n) => Math.round(n * 255));
}

export function hsvToHex(hsv: HSV): string {
  return "#" + hsvToRgb(hsv).map((n) => n.toString(16).padStart(2, "0")).join("");
}

export function hexToHsv(hex: string): HSV {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return { h: 260, s: 0.6, v: 0.9 };
  let s = m[1];
  if (s.length === 3) s = s.split("").map((c) => c + c).join("");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
  }
  return { h, s: max ? d / max : 0, v: max };
}

/** True when white text on this colour is easier to read than black. */
export function isDark(hex: string): boolean {
  const { h, s, v } = hexToHsv(hex);
  const [r, g, b] = hsvToRgb({ h, s, v });
  // Rec. 601 luma — close enough for "which text colour wins", and it does not
  // need the gamma work a full WCAG contrast ratio would.
  return (0.299 * r + 0.587 * g + 0.114 * b) < 150;
}

const clamp = (n: number) => Math.min(1, Math.max(0, n));

/** Track a drag over an element, reporting 0..1 positions on every move. */
function useDrag(onMove: (x: number, y: number) => void) {
  const ref = useRef<HTMLDivElement>(null);
  const handler = useCallback((e: React.PointerEvent) => {
    const el = ref.current;
    if (!el) return;
    el.setPointerCapture(e.pointerId);
    const box = el.getBoundingClientRect();
    const report = (ev: { clientX: number; clientY: number }) =>
      onMove(clamp((ev.clientX - box.left) / box.width),
             clamp((ev.clientY - box.top) / box.height));
    report(e);
    const move = (ev: PointerEvent) => report(ev);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [onMove]);
  return { ref, onPointerDown: handler };
}

export function ColorPicker({ value, onChange }: {
  value: string;
  onChange: (hex: string) => void;
}) {
  const [hsv, setHsv] = useState<HSV>(() => hexToHsv(value));
  const [text, setText] = useState(value);

  // Follow the value when the parent switches target (background → text →
  // border). Keyed on the hex so typing in the box does not fight the drag.
  useEffect(() => {
    if (value.toLowerCase() !== hsvToHex(hsv).toLowerCase()) {
      setHsv(hexToHsv(value));
      setText(value);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const emit = (next: HSV) => {
    setHsv(next);
    const hex = hsvToHex(next);
    setText(hex);
    onChange(hex);
  };

  const sv = useDrag((x, y) => emit({ ...hsv, s: x, v: 1 - y }));
  const hue = useDrag((x) => emit({ ...hsv, h: x * 360 }));
  const pure = hsvToHex({ h: hsv.h, s: 1, v: 1 });

  return (
    <div className="space-y-2">
      <div
        {...sv}
        className="relative h-32 w-full cursor-crosshair rounded-md"
        style={{
          background:
            `linear-gradient(to top, #000, transparent), ` +
            `linear-gradient(to right, #fff, ${pure})`,
        }}
      >
        <div
          className="pointer-events-none absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow"
          style={{ left: `${hsv.s * 100}%`, top: `${(1 - hsv.v) * 100}%` }}
        />
      </div>

      <div
        {...hue}
        className="relative h-3 w-full cursor-ew-resize rounded-full"
        style={{
          background:
            "linear-gradient(to right, #f00 0%, #ff0 17%, #0f0 33%, #0ff 50%, #00f 67%, #f0f 83%, #f00 100%)",
        }}
      >
        <div
          className="pointer-events-none absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow"
          style={{ left: `${(hsv.h / 360) * 100}%` }}
        />
      </div>

      <div className="flex items-center gap-2">
        <span className="h-6 w-6 shrink-0 rounded border border-border"
              style={{ background: value }} />
        <input
          value={text}
          onChange={(e) => {
            const v = e.target.value;
            setText(v);
            // Only commit once it is a real colour, so half-typed "#7c5" does
            // not repaint the preview with something the user did not mean.
            if (/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(v.trim())) {
              setHsv(hexToHsv(v));
              onChange(v.trim().toLowerCase());
            }
          }}
          spellCheck={false}
          className="w-full rounded-md border border-border bg-bg-soft px-2 py-1 font-mono text-xs text-text outline-none focus:border-brand/50"
        />
      </div>
    </div>
  );
}
