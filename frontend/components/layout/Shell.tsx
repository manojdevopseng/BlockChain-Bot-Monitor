"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { mutate } from "swr";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { StatusBar } from "./StatusBar";
import { useWebSocket } from "@/lib/ws";
import { getToken } from "@/lib/api";
import { ThemeProvider } from "@/lib/theme";

// Map a realtime WS event to the API path-prefixes whose SWR caches should
// revalidate immediately — so the dashboard reflects scanner activity and
// toggle changes the moment they happen, not on the next poll.
const EVENT_KEYS: Record<string, string[]> = {
  alert: ["/api/alerts", "/api/dashboard", "/api/rpc", "/api/tokens"],
  log: ["/api/logs"],
  service_changed: ["/api/settings/services", "/api/chains", "/api/rpc", "/api/system", "/api/dashboard"],
  premium_detection: ["/api/forwarder"],
};

const COLLAPSE_KEY = "sidebar_collapsed";

function revalidate(prefixes: string[]) {
  mutate(
    (key) => typeof key === "string" && prefixes.some((p) => key.startsWith(p)),
    undefined,
    { revalidate: true }
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const [backend, setBackend] = useState<string>();
  const router = useRouter();
  // null until the token has been read on the client — rendering the dashboard
  // before that would fire a screenful of requests that all 401.
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  // Mobile drawer starts closed on every load; desktop collapse is remembered.
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const path = usePathname();

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {}
  }, []);

  const isLogin = path === "/login";

  useEffect(() => {
    const ok = !!getToken();
    setSignedIn(ok);
    if (!ok && !isLogin) router.replace("/login");
  }, [isLogin, path, router]);

  // Route change always closes the mobile drawer.
  useEffect(() => setMobileOpen(false), [path]);

  // Prevent background scroll while the mobile drawer is open.
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [mobileOpen]);

  function toggleCollapse() {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  }

  const { connected } = useWebSocket((e) => {
    if (e.type === "hello" || e.type === "heartbeat") {
      if (e.data?.backend) setBackend(e.data.backend);
      if (e.data?.db_backend) setBackend(e.data.db_backend);
      const up = document.getElementById("sidebar-uptime");
      if (up && e.data?.uptime_seconds != null) {
        const s = e.data.uptime_seconds;
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
        up.textContent = `${h}h ${String(m).padStart(2, "0")}m ${String(sec).padStart(2, "0")}s`;
      }
      return;
    }
    const keys = EVENT_KEYS[e.type];
    if (keys) revalidate(keys);
  });

  // The login page gets the theme but none of the chrome — no sidebar to
  // navigate with and no status bar to poll while signed out.
  if (isLogin) {
    return <ThemeProvider>{children}</ThemeProvider>;
  }

  // Signed out, or still checking: render nothing rather than a dashboard
  // frame that flashes and then redirects.
  if (!signedIn) {
    return <ThemeProvider><div className="min-h-screen bg-bg" /></ThemeProvider>;
  }

  return (
    <ThemeProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={toggleCollapse}
          mobileOpen={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
        />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Topbar connected={connected} onOpenMobile={() => setMobileOpen(true)} />
          <main className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
            <div className="animate-fade-in">{children}</div>
          </main>
          <StatusBar backend={backend} />
        </div>
      </div>
    </ThemeProvider>
  );
}
