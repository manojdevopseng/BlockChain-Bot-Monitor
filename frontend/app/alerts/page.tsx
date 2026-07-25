"use client";

import { Bell, AlertTriangle, ShieldCheck, Eye } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtClock } from "@/lib/utils";

export default function AlertsPage() {
  const { data: stats } = useApi<any>("/api/alerts/stats");
  const { data } = useApi<any>("/api/alerts?limit=50");

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
        <StatCard label="Total Alerts" value={stats?.total ?? 0} icon={Bell} tone="red" delta={23.5} />
        <StatCard label="High Priority" value={stats?.high ?? 0} icon={AlertTriangle} tone="red" delta={12.7} />
        <StatCard label="Medium" value={stats?.medium ?? 0} icon={Eye} tone="amber" delta={18.2} />
        <StatCard label="Low" value={stats?.low ?? 0} icon={ShieldCheck} tone="blue" delta={-4.1} />
      </div>
      <Card>
        <CardHeader><CardTitle>Recent Alerts</CardTitle></CardHeader>
        <CardContent><DataTable columns={cols} rows={data?.items ?? []} empty="No alerts" /></CardContent>
      </Card>
    </div>
  );
}
