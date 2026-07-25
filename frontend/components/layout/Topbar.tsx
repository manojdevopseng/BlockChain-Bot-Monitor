"use client";

import { useEffect, useState } from "react";
import { Menu, Moon, Sun } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { NotificationBell } from "@/components/NotificationBell";
import { useTheme } from "@/lib/theme";

export function Topbar({
  connected, onOpenMobile,
}: {
  connected: boolean;
  onOpenMobile: () => void;
}) {
  // Rendered only after mount: the server and client would otherwise stamp
  // different times and React would report a hydration mismatch.
  const [now, setNow] = useState<Date | null>(null);
  const { theme, toggle } = useTheme();

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

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

      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        <div className="hidden text-right text-xs leading-tight text-text-muted tabular sm:block">
          <div suppressHydrationWarning>{now ? now.toLocaleTimeString("en-GB") : "--:--:--"}</div>
          <div className="text-text-dim" suppressHydrationWarning>
            {now ? now.toLocaleDateString("en-GB") : ""}
          </div>
        </div>

        {/* Light / dark toggle */}
        <button
          onClick={toggle}
          className="grid h-8 w-8 place-items-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text"
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          title={theme === "dark" ? "Light mode" : "Dark mode"}
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </button>

        <NotificationBell />

        <Badge variant={connected ? "green" : "red"}>
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-accent-green animate-pulse-soft" : "bg-accent-red"}`} />
          <span className="hidden sm:inline">{connected ? "Running" : "Offline"}</span>
        </Badge>
      </div>
    </header>
  );
}
