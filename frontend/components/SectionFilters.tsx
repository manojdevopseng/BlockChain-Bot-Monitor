"use client";

import { History as HistoryIcon, Search } from "lucide-react";
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
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-lg border border-border bg-bg-soft pl-7 pr-2 text-xs text-text focus:border-brand/60 focus:outline-none"
      >
        <option value="">Live</option>
        {dates.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
      <HistoryIcon size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
    </div>
  );
}
