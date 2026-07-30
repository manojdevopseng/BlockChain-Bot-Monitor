"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, History as HistoryIcon, Search } from "lucide-react";
import { Input } from "@/components/ui/input";

// The search box and History dropdown used in section headers. Shared so the
// Alerts page and the Detections sections stay the same control rather than
// two that drift apart.

export function SearchBox({
  value, onChange, placeholder, className = "h-8 w-60 pl-8 text-xs",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  className?: string;
}) {
  return (
    <div className="relative">
      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-dim" />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={className}
      />
    </div>
  );
}

// "Live" = no date filter; picking a day pins the section to it.
export function HistorySelect({
  value, onChange, dates,
}: {
  value: string;
  onChange: (v: string) => void;
  dates: string[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape. Without this the panel stays open
  // while you interact with the table behind it.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const options = [{ v: "", label: "Live" }, ...dates.map((d) => ({ v: d, label: d }))];
  const current = value || "Live";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={
          "flex h-8 w-[132px] items-center gap-1.5 rounded-lg border bg-bg-soft px-2 text-xs " +
          (open ? "border-brand/60 text-text" : "border-border text-text hover:border-brand/40")
        }
      >
        <HistoryIcon size={13} className="shrink-0 text-text-dim" />
        <span className="flex-1 truncate text-left">{current}</span>
        <ChevronDown
          size={13}
          className={"shrink-0 text-text-dim transition-transform " + (open ? "rotate-180" : "")}
        />
      </button>

      {/* Our own panel rather than the browser's. A native <select> draws its
          open list in the OS's colours, so on a dark page it appeared as a
          pale box sitting over the table — the one control in the app that did
          not match the theme. Anchored right so it opens inward from a control
          that usually sits near the edge of a section header. */}
      {open && (
        <div className="absolute right-0 z-50 mt-1 max-h-64 w-[168px] overflow-y-auto rounded-lg border border-border bg-bg-card p-1 shadow-2xl">
          {options.map((o) => {
            const active = o.v === value;
            return (
              <button
                key={o.v || "live"}
                type="button"
                onClick={() => { onChange(o.v); setOpen(false); }}
                className={
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors " +
                  (active ? "bg-brand text-white" : "text-text-muted hover:bg-bg-hover hover:text-text")
                }
              >
                {o.v === "" && <span className={"h-1.5 w-1.5 shrink-0 rounded-full " + (active ? "bg-white" : "bg-accent-green")} />}
                <span className={o.v === "" ? "" : "font-mono"}>{o.label}</span>
              </button>
            );
          })}
          {dates.length === 0 && (
            <div className="px-2 py-1.5 text-[11px] text-text-dim">No archived days yet</div>
          )}
        </div>
      )}
    </div>
  );
}

/** A row of mutually-exclusive filter buttons for a section header.
 *
 * Both Detections sections merged three (and two) chain-specific panels into
 * one, and this is how the chain is chosen. Deliberately buttons rather than a
 * dropdown: there are only three or four options and the current one should be
 * readable without opening anything.
 */
export function FilterTabs<T extends string>({
  value, onChange, options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: readonly { id: T; label: string }[];
}) {
  return (
    <div className="flex items-center gap-1 rounded-lg border border-border bg-bg-soft p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          className={
            "rounded-md px-2.5 py-1 text-xs transition-colors " +
            (value === o.id
              ? "bg-brand text-white"
              : "text-text-muted hover:bg-bg-hover hover:text-text")
          }
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
