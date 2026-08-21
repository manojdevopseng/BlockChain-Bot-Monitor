"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BadgeCheck, BarChart3, Bell, Brain, Coins, Cpu, Crosshair, LayoutDashboard, LifeBuoy, Link2, Lock, PanelLeftClose, PanelLeftOpen, Radio, Receipt, ScrollText, Send, Server, Settings, ShieldCheck, SlidersHorizontal, Terminal, User, Users, X, CandlestickChart } from "lucide-react";
import { useRole } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { useUptime, uptimeLabel } from "@/components/layout/uptime";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/alerts", label: "Alerts", icon: Bell },
  // Next to the alerts themselves rather than buried in Profile: it is the one
  // page that decides what a customer's own phone does, and the first thing
  // they should reach for after connecting Telegram.
  { href: "/alert-rules", label: "Alert Rules", icon: SlidersHorizontal },
  { href: "/tokens", label: "Tokens", icon: Coins },
  { href: "/detections", label: "Detections", icon: Crosshair },
  { href: "/rsi", label: "RSI", icon: Activity },
  { href: "/trading", label: "Trading", icon: CandlestickChart },
  { href: "/ai", label: "AI Narrative", icon: Brain },
  { hideFromUser: true, href: "/admin", label: "Admin", icon: ShieldCheck },
  { hideFromUser: true, href: "/forwarder", label: "Forwarder", icon: Send },
  { href: "/commands", label: "Commands", icon: Terminal },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  // The account's own two pages. Every plan sees them, including an expired
  // one — paying is how it stops being expired.
  { href: "/profile", label: "Profile", icon: User },
  { href: "/plan", label: "Plan", icon: BadgeCheck },
  { href: "/orders", label: "Orders", icon: Receipt },
  { href: "/support", label: "Support", icon: LifeBuoy },
  { href: "/notifications", label: "Notifications", icon: Bell },
  { href: "/chains", label: "Chains", icon: Link2 },
  { hideFromUser: true, href: "/logs", label: "Logs", icon: ScrollText },
  { hideFromUser: true, href: "/settings", label: "Settings", icon: Settings },
  // Hidden rather than disabled for a read-only account: the other locked
  // pages are things the dashboard does, worth knowing exist. Who can log in
  // is not — and a greyed "User Management" tells a user exactly where to go
  // looking.
  { href: "/users", label: "User Management", icon: Users, hideFromUser: true },
  { hideFromUser: true, href: "/rpc", label: "RPC Monitor", icon: Radio },
  { hideFromUser: true, href: "/system", label: "System", icon: Server },
];

export function Sidebar({
  collapsed, onToggleCollapse, mobileOpen, onCloseMobile,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}) {
  const path = usePathname();
  const uptime = useUptime();
  const { blocks, known, isAdmin } = useRole();
  // On mobile the rail is always full-width inside the drawer; only desktop collapses.
  const isCollapsed = collapsed;

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={onCloseMobile}
          aria-hidden
        />
      )}

      <aside
        className={cn(
          "flex shrink-0 flex-col border-r border-border bg-bg-soft/95 backdrop-blur transition-[width] duration-200",
          // desktop width
          isCollapsed ? "lg:w-16" : "lg:w-60",
          // mobile: off-canvas drawer
          "fixed inset-y-0 left-0 z-50 w-64 lg:static lg:z-auto lg:translate-x-0",
          mobileOpen ? "translate-x-0 animate-slide-in" : "-translate-x-full",
        )}
      >
        <div className={cn("flex items-center gap-2.5 px-4 py-5", isCollapsed && "lg:justify-center lg:px-2")}>
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand/20 text-brand">
            <Cpu size={20} />
          </div>
          <div className={cn("min-w-0", isCollapsed && "lg:hidden")}>
            <div className="truncate text-sm font-bold leading-tight text-accent-green">SightLine</div>
            <div className="truncate text-[11px] leading-tight text-text-muted">MultiChain Monitor</div>
          </div>
          {/* Close (mobile only) */}
          <button
            onClick={onCloseMobile}
            className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-text lg:hidden"
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2">
          {NAV.map(({ href, label, icon: Icon, hideFromUser }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            const locked = blocks(href);
            // Hidden until we know, then hidden for anyone but an admin. The
            // remembered role means an admin does not watch their own nav
            // build itself on every load.
            if (hideFromUser && (!known || !isAdmin)) return null;
            if (locked && hideFromUser) return null;
            // Otherwise shown either way — a nav that hides pages leaves you
            // wondering what the dashboard has. Disabled says "not for this
            // account", which is the true answer.
            if (locked) {
              return (
                <div
                  key={href}
                  aria-disabled
                  title={isCollapsed ? `${label} — admin only` : "Admin only"}
                  className={cn(
                    "flex cursor-not-allowed items-center gap-3 rounded-lg px-3 py-2 text-sm text-text-dim",
                    isCollapsed && "lg:justify-center lg:px-2",
                  )}
                >
                  <Icon size={17} className="shrink-0 opacity-50" />
                  <span className={cn("flex-1", isCollapsed && "lg:hidden")}>{label}</span>
                  <Lock size={12} className={cn("shrink-0", isCollapsed && "lg:hidden")} />
                </div>
              );
            }
            return (
              <Link
                key={href}
                href={href}
                onClick={onCloseMobile}
                title={isCollapsed ? label : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  isCollapsed && "lg:justify-center lg:px-2",
                  active
                    ? "bg-brand/15 font-medium text-text"
                    : "text-text-muted hover:bg-bg-hover hover:text-text"
                )}
              >
                <Icon size={17} className={cn("shrink-0", active && "text-brand-soft")} />
                <span className={cn(isCollapsed && "lg:hidden")}>{label}</span>
              </Link>
            );
          })}
        </nav>

        {/* Uptime card — the operator's, and hidden when the rail is collapsed.
            How long the bot has been up is a fact about the operator's server;
            a customer buys the alerts, not the box. Same rule as the nav:
            unknown reads as not-admin, so it never appears and then vanishes. */}
        {known && isAdmin && (
        <div className={cn("m-3 rounded-lg border border-border bg-bg-card/50 p-3 text-xs", isCollapsed && "lg:hidden")}>
          <div className="flex items-center gap-2 text-text-muted">
            <span className="h-2 w-2 animate-pulse-soft rounded-full bg-accent-green" />
            Bot Uptime
          </div>
          <div className="mt-1 font-mono text-sm text-accent-green">{uptimeLabel(uptime)}</div>
          <div className="mt-2 flex justify-between text-[11px] text-text-dim">
            <span>Version</span><span>0.1.0</span>
          </div>
        </div>
        )}

        {/* Desktop collapse toggle */}
        <button
          onClick={onToggleCollapse}
          className={cn(
            "mx-3 mb-3 hidden items-center gap-2 rounded-lg px-3 py-2 text-xs text-text-muted transition-colors hover:bg-bg-hover hover:text-text lg:flex",
            isCollapsed && "lg:justify-center lg:px-2"
          )}
          title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {isCollapsed ? <PanelLeftOpen size={16} /> : <><PanelLeftClose size={16} /> Collapse</>}
        </button>
      </aside>
    </>
  );
}
