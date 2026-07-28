"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Bell, Brain, Coins, Cpu, Crosshair, LayoutDashboard, Link2, PanelLeftClose, PanelLeftOpen, Radio, ScrollText, Send, Server, Settings, Terminal, X } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/tokens", label: "Tokens", icon: Coins },
  { href: "/detections", label: "Detections", icon: Crosshair },
  { href: "/chains", label: "Chains", icon: Link2 },
  { href: "/forwarder", label: "Forwarder", icon: Send },
  { href: "/commands", label: "Commands", icon: Terminal },
  { href: "/ai", label: "AI Narrative", icon: Brain },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/logs", label: "Logs", icon: ScrollText },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/rpc", label: "RPC Monitor", icon: Radio },
  { href: "/system", label: "System", icon: Server },
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
            <div className="truncate text-sm font-bold leading-tight text-accent-green">BlockChain</div>
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
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
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

        {/* Uptime card — hidden when the desktop rail is collapsed */}
        <div className={cn("m-3 rounded-lg border border-border bg-bg-card/50 p-3 text-xs", isCollapsed && "lg:hidden")}>
          <div className="flex items-center gap-2 text-text-muted">
            <span className="h-2 w-2 animate-pulse-soft rounded-full bg-accent-green" />
            Bot Uptime
          </div>
          <div className="mt-1 font-mono text-sm text-accent-green" id="sidebar-uptime">—</div>
          <div className="mt-2 flex justify-between text-[11px] text-text-dim">
            <span>Version</span><span>0.1.0</span>
          </div>
        </div>

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
