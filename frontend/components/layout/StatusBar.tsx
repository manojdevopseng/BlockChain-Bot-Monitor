"use client";

import { Cpu, HardDrive, MemoryStick, Wifi } from "lucide-react";

export function StatusBar({ backend }: { backend?: string }) {
  return (
    <footer className="flex h-8 shrink-0 items-center justify-between gap-3 border-t border-border px-3 text-[11px] text-text-muted sm:px-6">
      <div className="flex items-center gap-3 sm:gap-5">
        <span className="flex items-center gap-1.5"><Cpu size={13} className="text-accent-green" /> CPU 22%</span>
        <span className="hidden items-center gap-1.5 sm:flex"><MemoryStick size={13} className="text-accent-blue" /> RAM 1.1 / 3.8 GB</span>
        <span className="hidden items-center gap-1.5 md:flex"><HardDrive size={13} className="text-accent-purple" /> Disk 18%</span>
        <span className="hidden items-center gap-1.5 md:flex"><Wifi size={13} className="text-accent-cyan" /> Network</span>
      </div>
      <div className="flex items-center gap-3 sm:gap-4">
        <span>DB: <span className="text-text">{backend || "—"}</span></span>
        <span className="hidden sm:inline">BlockChain-Bot Monitor v0.1.0</span>
      </div>
    </footer>
  );
}
