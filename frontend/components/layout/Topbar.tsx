"use client";

import { Menu } from "lucide-react";
import { TopbarActions } from "@/components/layout/TopbarActions";

export function Topbar({
  connected, onOpenMobile,
}: {
  connected: boolean;
  onOpenMobile: () => void;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-3 sm:px-6">
      <div className="flex min-w-0 items-center gap-2">
        {/* Hamburger — mobile only */}
        <button
          onClick={onOpenMobile}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text lg:hidden"
          aria-label="Open menu"
        >
          <Menu size={18} />
        </button>
        <div className="hidden min-w-0 items-center gap-2 truncate text-sm text-text-muted sm:flex">
          <span className="text-text">SOL–ETH</span>
          <span className="text-text-dim">•</span>
          <span className="text-text">SOL–Robinhood</span>
          <span className="hidden text-text-dim md:inline">•</span>
          <span className="hidden text-text md:inline">Telegram Forwarder</span>
        </div>
      </div>

      <TopbarActions connected={connected} />
    </header>
  );
}
