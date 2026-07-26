"use client";

import { Cpu, HardDrive, MemoryStick, Wifi } from "lucide-react";
import { useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

// Anything we can't actually measure shows a dash — never a placeholder number.
function val(v: number | null | undefined, fmt: (n: number) => string): string {
  return v === null || v === undefined ? "—" : fmt(v);
}

function tone(pct: number | null | undefined, warn: number, crit: number): string {
  if (pct === null || pct === undefined) return "text-text-dim";
  if (pct >= crit) return "text-accent-red";
  if (pct >= warn) return "text-accent-amber";
  return "text-accent-green";
}

export function StatusBar({ backend }: { backend?: string }) {
  // Measured on the server the backend runs on, refreshed slowly — this is a
  // status bar, not a monitoring tool.
  const { data: m } = useApi<any>("/api/system/metrics", { refreshInterval: 10000 });

  return (
    <footer className="flex h-8 shrink-0 items-center justify-between gap-3 border-t border-border px-3 text-[11px] text-text-muted sm:px-6">
      <div className="flex items-center gap-3 sm:gap-5">
        <span className="flex items-center gap-1.5" title="Server CPU">
          <Cpu size={13} className={tone(m?.cpu_percent, 70, 90)} />
          CPU {val(m?.cpu_percent, (n) => `${n}%`)}
        </span>
        <span className="hidden items-center gap-1.5 sm:flex" title="Server memory">
          <MemoryStick size={13} className={tone(m?.ram_percent, 75, 90)} />
          RAM {m?.ram_used_gb != null && m?.ram_total_gb != null
            ? `${m.ram_used_gb} / ${m.ram_total_gb} GB`
            : "—"}
        </span>
        <span className="hidden items-center gap-1.5 md:flex" title="Root filesystem">
          <HardDrive size={13} className={tone(m?.disk_percent, 80, 92)} />
          Disk {val(m?.disk_percent, (n) => `${n}%`)}
          {m?.disk_free_gb != null && (
            <span className="ml-1 text-text-dim">({m.disk_free_gb} GB free)</span>
          )}
        </span>
        <span className="hidden items-center gap-1.5 md:flex" title="Network traffic since boot">
          <Wifi size={13} className="text-accent-cyan" />
          Net {m?.net_recv_mb != null
            ? `↓${Math.round(m.net_recv_mb)} ↑${Math.round(m.net_sent_mb ?? 0)} MB`
            : "—"}
        </span>
      </div>
      <div className="flex items-center gap-3 sm:gap-4">
        <span>
          DB: <span className={cn(backend ? "text-text" : "text-text-dim")}>{backend || "—"}</span>
        </span>
        <span className="hidden sm:inline">BlockChain-Bot Monitor v0.1.0</span>
      </div>
    </footer>
  );
}
