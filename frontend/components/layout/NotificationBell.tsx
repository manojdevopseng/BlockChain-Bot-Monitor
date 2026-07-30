"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell, CheckCheck } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn, timeAgo } from "@/lib/utils";

const SEEN_KEY = "notif_seen_ts";

// Bell backed by real alerts: unread = alerts newer than the last time the user
// opened/cleared the panel (persisted in localStorage).
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [seenTs, setSeenTs] = useState<number>(0);
  // What counted as unread when the panel was opened. Opening clears the badge
  // straight away, but the rows keep their highlight while you are looking at
  // them — otherwise the panel would tell you nothing about what was new.
  const [openedAt, setOpenedAt] = useState<number>(0);
  const [ready, setReady] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const { data } = useApi<any>("/api/alerts?limit=12");
  const items: any[] = data?.items ?? [];

  useEffect(() => {
    const raw = Number(localStorage.getItem(SEEN_KEY) || 0);
    setSeenTs(raw);
    setReady(true);
  }, []);

  // Close on outside click / Escape
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

  const unread = ready ? items.filter((a) => (a.created_at ?? 0) > seenTs).length : 0;

  function markAllRead() {
    const now = Date.now() / 1000;
    setSeenTs(now);
    try { localStorage.setItem(SEEN_KEY, String(now)); } catch {}
  }

  function toggle() {
    setOpen((wasOpen) => {
      if (!wasOpen) {
        // Reading them is what marks them read — clicking the bell used to open
        // the panel and leave the count sitting there.
        setOpenedAt(seenTs);
        markAllRead();
      }
      return !wasOpen;
    });
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={toggle}
        className="relative grid h-8 w-8 place-items-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text"
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
      >
        <Bell size={18} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 grid min-w-[16px] place-items-center rounded-full bg-accent-red px-1 text-[9px] font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {/* On phones the panel is pinned to the viewport, not to the bell.
          Anchoring it to the button with `right-0` capped its width but not its
          position: the bell sits ~320px in, so a 22rem panel began off the left
          edge and the first characters of every row were cut off. From sm: up
          there is room, so it goes back to hanging off the button. */}
      {open && (
        <div className="fixed inset-x-2 top-14 z-50 overflow-hidden rounded-xl border border-border bg-bg-card shadow-2xl animate-fade-in
                        sm:absolute sm:inset-x-auto sm:right-0 sm:top-auto sm:mt-2 sm:w-[22rem]">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <span className="text-xs font-semibold uppercase tracking-wider text-text-muted">
              Notifications
            </span>
            <button
              onClick={markAllRead}
              className="flex items-center gap-1 text-[11px] text-text-muted transition-colors hover:text-text"
            >
              <CheckCheck size={13} /> Mark all read
            </button>
          </div>

          <div className="max-h-80 overflow-y-auto">
            {items.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-text-dim">No notifications</div>
            ) : (
              items.map((a, i) => {
                const isUnread = (a.created_at ?? 0) > openedAt;
                return (
                  <div
                    // Keyed by the alert, not the array index: a new one arriving
                    // at the top otherwise rewrites every visible row.
                    key={`${a.created_at ?? i}-${a.token_address ?? a.message ?? i}`}
                    className={cn(
                      "flex gap-3 border-b border-border-soft px-4 py-2.5 last:border-0",
                      isUnread && "bg-brand/5"
                    )}
                  >
                    <span
                      className={cn(
                        "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                        a.severity === "high" ? "bg-accent-red"
                          : a.severity === "medium" ? "bg-accent-amber" : "bg-accent-blue"
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs text-text">{a.message}</div>
                      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-text-dim">
                        <Badge variant="gray">{a.chain}</Badge>
                        <span>{a.created_at ? timeAgo(a.created_at) : ""}</span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <Link
            href="/alerts"
            onClick={() => setOpen(false)}
            className="block border-t border-border px-4 py-2.5 text-center text-xs text-brand-soft transition-colors hover:bg-bg-hover"
          >
            View all alerts →
          </Link>
        </div>
      )}
    </div>
  );
}
