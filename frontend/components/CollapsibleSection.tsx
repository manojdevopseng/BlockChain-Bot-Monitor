"use client";

import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const KEY = "collapsed_sections";

function readCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

// A section card whose body folds away. Collapsing is remembered per `id`, so a
// section you closed stays closed across page loads and navigation — otherwise
// re-collapsing the same six sections on every visit is worse than scrolling.
//
// The title bar is the toggle, but the controls inside it (search box, history
// dropdown, filter buttons) are not: clicks there stop at the control.
export function CollapsibleSection({
  id, title, icon, count, controls, children, bodyClass, defaultCollapsed = false,
}: {
  id: string;
  title: string;
  icon?: React.ReactNode;
  count?: number;
  controls?: React.ReactNode;
  children: React.ReactNode;
  bodyClass?: string;
  defaultCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [ready, setReady] = useState(false);

  // Read after mount: localStorage on the server would break hydration.
  useEffect(() => {
    const stored = readCollapsed()[id];
    if (stored !== undefined) setCollapsed(stored);
    setReady(true);
  }, [id]);

  function toggle() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(KEY, JSON.stringify({ ...readCollapsed(), [id]: next }));
    } catch {}
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-3">
        <button
          onClick={toggle}
          aria-expanded={!collapsed}
          className="flex min-w-0 items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text-muted transition-colors hover:text-text"
        >
          <ChevronDown
            size={14}
            className={cn("shrink-0 transition-transform", collapsed && "-rotate-90")}
          />
          {icon}
          <span className="truncate">{title}</span>
        </button>
        <div className="flex flex-wrap items-center gap-2">
          {controls}
          {count !== undefined && <Badge variant="purple">{count}</Badge>}
        </div>
      </div>
      {/* Hidden with CSS rather than unmounted: the section keeps its search
          term and history selection, and re-expanding shows data immediately
          instead of refetching. `ready` avoids a flash of the wrong state. */}
      <div className={cn("px-5 pb-5 pt-0", bodyClass, ready && collapsed && "hidden")}>
        {children}
      </div>
    </Card>
  );
}
