"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { mutate } from "swr";
import { Sidebar } from "./Sidebar";
import { setUptime } from "@/components/layout/uptime";
import { Topbar } from "./Topbar";
import { StatusBar } from "./StatusBar";
import { useWebSocket } from "@/lib/ws";
import { getToken, useApi } from "@/lib/api";
import { useRole } from "@/lib/hooks";
import { ThemeProvider } from "@/lib/theme";
import { Lock } from "lucide-react";

// Map a realtime WS event to the API path-prefixes whose SWR caches should
// revalidate immediately — so the dashboard reflects scanner activity and
// toggle changes the moment they happen, not on the next poll.
const EVENT_KEYS: Record<string, string[]> = {
  alert: ["/api/alerts", "/api/dashboard", "/api/rpc", "/api/tokens"],
  log: ["/api/logs"],
  service_changed: ["/api/settings/services", "/api/chains", "/api/rpc", "/api/system", "/api/dashboard"],
  premium_detection: ["/api/forwarder"],
  // One token, pushed the moment the X feed finds it. Revalidating the section
  // on the event is what makes rows arrive one at a time instead of a poll at a
  // time.
  x_link: ["/api/ai/xcheck", "/api/ai/og"],
};

const COLLAPSE_KEY = "sidebar_collapsed";

// Scanner events arrive in bursts — logs alone run at roughly 40 a minute — and
// revalidating on each one repainted the page that often. Prefixes are pooled
// and flushed once per window, so a burst costs one refetch.
const REVALIDATE_WINDOW_MS = 500;
let pendingPrefixes = new Set<string>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function revalidate(prefixes: string[]) {
  prefixes.forEach((p) => pendingPrefixes.add(p));
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    const due = [...pendingPrefixes];
    pendingPrefixes = new Set();
    flushTimer = null;
    // No data argument. Passing `undefined` as the second argument told SWR to
    // *clear* the cache and only then refetch, so every component rendered its
    // empty state in the gap and filled back in a moment later — that was the
    // flashing. Without it the current data stays on screen and is replaced
    // once, when the new response lands.
    mutate((key) => typeof key === "string" && due.some((p) => key.startsWith(p)));
  }, REVALIDATE_WINDOW_MS);
}

// A tab left open across a deploy keeps running the old build — old polling
// intervals, old columns — and looks merely broken. This notices and offers the
// reload rather than leaving it to be guessed at.
function BuildWatcher() {
  const { data } = useApi<any>("/api/system/version", { refreshInterval: 60000 });
  const seen = useRef<string | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    const build = data?.build;
    if (!build || build === "unknown") return;
    if (seen.current === null) {
      seen.current = build;
    } else if (seen.current !== build) {
      setStale(true);
    }
  }, [data?.build]);

  if (!stale) return null;
  return (
    <button
      onClick={() => window.location.reload()}
      className="flex w-full items-center justify-center gap-2 bg-brand/15 px-3 py-1.5 text-xs
                 text-brand-soft hover:bg-brand/25"
    >
      A new build is deployed — click to reload
    </button>
  );
}

// The nav greys these pages out, but the URL is still typeable and a bookmark
// still resolves — so the page itself has to say no as well. This is still only
// presentation: every request the page would make is refused server-side.
function RoleGate({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { blocks } = useRole();
  if (!blocks(path)) return <>{children}</>;
  return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-border bg-bg-card/60 p-8 text-center">
      <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full bg-bg-soft text-text-dim">
        <Lock size={20} />
      </div>
      <h2 className="text-base font-semibold text-text">Admin only</h2>
      <p className="mt-1.5 text-sm text-text-muted">
        This account can see the dashboard but not change it. Sign in as an
        admin to open this page.
      </p>
    </div>
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

  // Pages that exist for people who are not signed in yet — or cannot be,
  // because they are here to fix exactly that. None of them may bounce to
  // /login, and none of them get the dashboard chrome.
  const PUBLIC_PATHS = ["/login", "/register", "/verify", "/forgot", "/reset"];
  const isPublic = PUBLIC_PATHS.some((p) => path === p || path.startsWith(`${p}/`));
  const isLogin = isPublic;

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
      // Hands the figure to the uptime store, which ticks it forward every
       // second instead of holding it still between heartbeats.
      if (e.data?.uptime_seconds != null) setUptime(e.data.uptime_seconds);
      return;
    }
    const keys = EVENT_KEYS[e.type];
    if (keys) revalidate(keys);
  });

  // The signed-out pages get the theme but none of the chrome — no sidebar to
  // navigate with and no status bar to poll while signed out.
  if (isPublic) {
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
          <BuildWatcher />
          <Topbar connected={connected} onOpenMobile={() => setMobileOpen(true)} />
          <main className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
            <div className="animate-fade-in">
              <RoleGate>{children}</RoleGate>
            </div>
          </main>
          <StatusBar backend={backend} />
        </div>
      </div>
    </ThemeProvider>
  );
}
