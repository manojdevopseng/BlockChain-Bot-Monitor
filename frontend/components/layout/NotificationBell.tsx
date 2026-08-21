"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell, CheckCheck, CreditCard, LifeBuoy, Megaphone, Target } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

/* The bell: what happened to THIS account.
 *
 * It used to show the scanner's own alert feed against a timestamp in
 * localStorage, which meant every account saw the same rows and "unread" was a
 * guess made in the browser. Now it shows the account's own notices — its
 * market cap hits, its orders, replies on its support requests — and unread is
 * counted by the server, because that is the only place that knows.
 *
 * Opening the panel marks everything up to that moment read. A bell that needs
 * each line ticked off is a bell people stop opening.
 */

const BADGE_CAP = 50;

const ICONS: Record<string, any> = {
  alert: Target, billing: CreditCard, support: LifeBuoy, system: Megaphone,
};

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const { data } = useApi<any>("/api/notifications", { refreshInterval: 30000 });
  const items: any[] = data?.items ?? [];
  const unread: number = data?.unread ?? 0;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function openPanel() {
    setOpen((v) => !v);
    if (!open && unread > 0) {
      // Marked up to now, not "all": a notice that lands while the panel is
      // open should still arrive unread.
      await apiSend("/api/notifications/read", "POST", { before: Date.now() / 1000 });
      mutate("/api/notifications");
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button onClick={openPanel} title="Notifications"
              className="relative grid h-9 w-9 place-items-center rounded-lg text-text-dim hover:bg-bg-hover hover:text-text">
        <Bell size={17} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 grid min-w-[16px] place-items-center rounded-full bg-accent-red px-1 text-[9px] font-semibold text-white">
            {unread > BADGE_CAP ? `${BADGE_CAP}+` : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 overflow-hidden rounded-xl border border-border bg-bg-card shadow-xl">
          <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
            <span className="text-xs font-medium text-text">Notifications</span>
            {items.length > 0 && (
              <span className="flex items-center gap-1 text-[10px] text-text-dim">
                <CheckCheck size={11} /> up to date
              </span>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 ? (
              <p className="px-3 py-8 text-center text-xs text-text-dim">
                Nothing yet. Alerts you set up, payments and support replies
                appear here.
              </p>
            ) : (
              items.map((n: any, i: number) => {
                const Icon = ICONS[n.kind] ?? Bell;
                const row = (
                  <div className={cn(
                    "flex gap-2.5 border-b border-border-soft px-3 py-2.5 last:border-0",
                    !n.read && "bg-brand/5")}>
                    <Icon size={14} className="mt-0.5 shrink-0 text-text-dim" />
                    <div className="min-w-0">
                      <p className="text-xs text-text">{n.title}</p>
                      {n.body && (
                        <p className="mt-0.5 line-clamp-2 text-[11px] text-text-muted">
                          {n.body}
                        </p>
                      )}
                      <p className="mt-0.5 text-[10px] text-text-dim">{timeAgo(n.at)}</p>
                    </div>
                  </div>
                );
                return n.link
                  ? <Link key={i} href={n.link} onClick={() => setOpen(false)}
                          className="block hover:bg-bg-hover/50">{row}</Link>
                  : <div key={i}>{row}</div>;
              })
            )}
          </div>

          <Link href="/notifications" onClick={() => setOpen(false)}
                className="block border-t border-border-soft px-3 py-2 text-center text-[11px] text-brand-soft hover:bg-bg-hover/50">
            See all
          </Link>
        </div>
      )}
    </div>
  );
}
