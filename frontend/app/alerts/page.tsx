"use client";

import { useState } from "react";
import { Bell, AlertTriangle, ShieldCheck, Eye } from "lucide-react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { HistorySelect, SearchBox } from "@/components/SectionFilters";
import { DownloadCsv } from "@/components/features/Performance";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtClock } from "@/lib/utils";

export default function AlertsPage() {
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);

  const params = new URLSearchParams({ limit: "50" });
  if (query) params.set("q", query);
  if (date) params.set("date", date);

  const { data: stats } = useApi<any>("/api/alerts/stats");
  const { data: datesData } = useApi<any>("/api/alerts/dates");
  // keepPreviousData holds the rows while a new query resolves, instead of
  // blanking to "No alerts" and back.
  const { data } = useApi<any>(`/api/alerts?${params.toString()}`, { keepPreviousData: true });

  const cols: Column<any>[] = [
    { key: "created_at", header: "Time", render: (r) => <span className="font-mono text-xs text-text-muted">{fmtClock(r.created_at)}</span> },
    { key: "severity", header: "Severity", render: (r) => (
      <Badge variant={r.severity === "high" ? "red" : r.severity === "medium" ? "amber" : "blue"}>{r.severity}</Badge>
    )},
    { key: "type", header: "Type" },
    { key: "chain", header: "Chain", render: (r) => <Badge variant="purple">{r.chain}</Badge> },
    { key: "message", header: "Message", render: (r) => <span className="text-text-muted">{r.message}</span> },
    { key: "status", header: "Status", render: (r) => <Badge variant="green">{r.status}</Badge> },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="Alerts" subtitle="Real-time alerts and important events from all chains" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Alerts" value={stats?.total ?? 0} icon={Bell} tone="red" />
        <StatCard label="High Priority" value={stats?.high ?? 0} icon={AlertTriangle} tone="red" />
        <StatCard label="Medium" value={stats?.medium ?? 0} icon={Eye} tone="amber" />
        <StatCard label="Low" value={stats?.low ?? 0} icon={ShieldCheck} tone="blue" />
      </div>
      <Card>
        <CardHeader className="flex-wrap gap-2">
          <CardTitle>Recent Alerts</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <SearchBox value={q} onChange={setQ} placeholder="token / address / message" />
            <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
            <DownloadCsv path="/api/alerts/export.csv" filename="alerts.csv" />
            <Badge variant="purple">{data?.total ?? 0}</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={cols}
            rows={data?.items ?? []}
            empty={query || date ? "No alerts match this filter" : "No alerts"}
          />
        </CardContent>
      </Card>
    </div>
  );
}
