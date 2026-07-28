"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollText, Info, AlertTriangle, XCircle } from "lucide-react";
import { cn, fmtClock } from "@/lib/utils";

const LEVEL: Record<string, string> = {
  INFO: "text-accent-blue", DEBUG: "text-text-dim",
  WARN: "text-accent-amber", ERROR: "text-accent-red",
};

export default function LogsPage() {
  const [q, setQ] = useState("");
  const query = useDebounced(q);

  const { data: stats } = useApi<any>("/api/logs/stats", { refreshInterval: 15000 });
  const { data, isLoading } = useApi<any>(
    `/api/logs?limit=100${query ? `&q=${encodeURIComponent(query)}` : ""}`,
    // keepPreviousData holds the old lines on screen while the new query
    // resolves, instead of blanking to "No logs" and back.
    // 15s is a safety net, not the update path: the WS `log` event revalidates
    // this the moment a line is written. Polling every 3s on top of that was
    // five wasted repaints per new line.
    { refreshInterval: 15000, keepPreviousData: true }
  );

  const items: any[] = data?.items ?? [];

  return (
    <div className="space-y-5">
      <PageHeader title="Logs" subtitle="Real-time logs from all services and components" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Logs" value={stats?.total ?? 0} icon={ScrollText} tone="purple" />
        <StatCard label="Info" value={stats?.info ?? 0} icon={Info} tone="blue" />
        <StatCard label="Warnings" value={stats?.warn ?? 0} icon={AlertTriangle} tone="amber" />
        <StatCard label="Errors" value={stats?.error ?? 0} icon={XCircle} tone="red" />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Live Stream</CardTitle>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search logs…" className="w-56" />
        </CardHeader>
        <CardContent>
          <div className="max-h-[520px] overflow-y-auto font-mono text-xs">
            {items.map((l: any, i: number) => (
              // Keyed by the log's own id: a new line at the top then inserts
              // one row instead of shifting every row's content down by one
              // index and repainting the whole list every 3 seconds.
              <div key={l.id ?? `${l.ts}-${i}`} className="flex gap-3 border-b border-border-soft py-1.5">
                <span className="shrink-0 text-text-dim">{fmtClock(l.ts)}</span>
                <span className={cn("w-12 shrink-0 font-semibold", LEVEL[l.level] || "text-text")}>{l.level}</span>
                <span className="w-32 shrink-0 text-brand-soft">{l.service}</span>
                <span className="text-text-muted">{l.message}</span>
              </div>
            ))}
            {items.length === 0 && !isLoading && (
              <div className="py-8 text-center text-text-dim">
                {query ? "No logs match this search" : "No logs"}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
