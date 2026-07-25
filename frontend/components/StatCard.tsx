"use client";

import { ArrowDown, ArrowUp, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function StatCard({
  label, value, unit, delta, icon: Icon, tone = "purple", muted = false,
}: {
  label: string;
  value: string | number;
  unit?: string;
  delta?: number;
  icon?: LucideIcon;
  tone?: "purple" | "green" | "red" | "amber" | "blue" | "cyan";
  muted?: boolean;
}) {
  const toneBg: Record<string, string> = {
    purple: "bg-brand/15 text-brand-soft",
    green: "bg-accent-green/15 text-accent-green",
    red: "bg-accent-red/15 text-accent-red",
    amber: "bg-accent-amber/15 text-accent-amber",
    blue: "bg-accent-blue/15 text-accent-blue",
    cyan: "bg-accent-cyan/15 text-accent-cyan",
  };
  return (
    <div className={cn(
      "rounded-xl border border-border bg-bg-card/60 p-4 transition-opacity",
      muted && "opacity-40"
    )}>
      <div className="flex items-start justify-between">
        <div className="text-[11px] font-medium uppercase tracking-wider text-text-muted">
          {label}
        </div>
        {Icon && (
          <div className={cn("grid h-8 w-8 place-items-center rounded-lg", toneBg[tone])}>
            <Icon size={16} />
          </div>
        )}
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="text-2xl font-bold text-text tabular">{value}</span>
        {unit && <span className="text-xs text-text-muted">{unit}</span>}
      </div>
      {delta !== undefined && (
        <div className="mt-1 flex items-center gap-1 text-[11px]">
          <span className={cn("flex items-center", delta >= 0 ? "text-accent-green" : "text-accent-red")}>
            {delta >= 0 ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
            {Math.abs(delta)}%
          </span>
          <span className="text-text-dim">vs last 24h</span>
        </div>
      )}
    </div>
  );
}
