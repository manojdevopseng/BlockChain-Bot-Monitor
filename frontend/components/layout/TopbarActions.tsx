"use client";

import { useEffect, useState } from "react";
import { LogOut, Moon, Sun } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { NotificationBell } from "@/components/layout/NotificationBell";
import { useTheme } from "@/lib/theme";
import { setToken } from "@/lib/api";

/* The right-hand cluster of the header — clock, theme, notifications, sign
   out, and whether the backend is answering. Both dashboards carry exactly the
   same set, so it lives here rather than being written twice and drifting. */

export function TopbarActions({ connected }: { connected: boolean }) {
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
    <div className="flex shrink-0 items-center gap-2 sm:gap-3">
      <div className="hidden text-right text-xs leading-tight text-text-muted tabular sm:block">
        <div suppressHydrationWarning>{now ? now.toLocaleTimeString("en-GB") : "--:--:--"}</div>
        <div className="text-text-dim" suppressHydrationWarning>
          {now ? now.toLocaleDateString("en-GB") : ""}
        </div>
      </div>

      <button
        onClick={toggle}
        className="grid h-8 w-8 place-items-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text"
        aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        title={theme === "dark" ? "Light mode" : "Dark mode"}
      >
        {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
      </button>

      <NotificationBell />
      <button
        onClick={() => {
          setToken(null);
          // Full reload, not a router push: it drops every SWR cache, so no
          // signed-in data is left on screen behind the login form.
          window.location.href = "/login";
        }}
        title="Sign out"
        aria-label="Sign out"
        className="grid h-8 w-8 place-items-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-accent-red"
      >
        <LogOut size={17} />
      </button>

      <Badge variant={connected ? "green" : "red"}>
        <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-accent-green animate-pulse-soft" : "bg-accent-red"}`} />
        <span className="hidden sm:inline">{connected ? "Running" : "Offline"}</span>
      </Badge>
    </div>
  );
}
