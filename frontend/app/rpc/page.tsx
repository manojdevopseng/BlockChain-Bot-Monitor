"use client";

import { Server, CheckCircle, AlertTriangle, XCircle, Clock, Fuel } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { cn, fmtNum, fmtEth } from "@/lib/utils";

export default function RpcPage() {
  const { data: stats } = useApi<any>("/api/rpc/stats");
  const { data } = useApi<any>("/api/rpc/endpoints");
  const { data: gas } = useApi<any>("/api/rpc/gas");

  const cols: Column<any>[] = [
    { key: "name", header: "Name", render: (r) => (
      <span className={cn("font-medium", r.enabled ? "text-text" : "text-text-dim")}>{r.name}</span>
    )},
    { key: "chain", header: "Chain", render: (r) => <Badge variant="purple">{r.chain}</Badge> },
    { key: "url", header: "Endpoint", render: (r) => <span className="font-mono text-[11px] text-text-muted">{r.url}</span> },
    { key: "status", header: "Status", render: (r) => (
      <Badge variant={r.status === "healthy" ? "green" : r.status === "degraded" ? "amber" : r.status === "disabled" ? "gray" : "red"}>{r.status}</Badge>
    )},
    { key: "latency_ms", header: "Latency", render: (r) => `${r.latency_ms} ms` },
    { key: "uptime", header: "Uptime", render: (r) => <span className="text-accent-green">{r.uptime}%</span> },
    { key: "requests_1h", header: "Req 1h", render: (r) => fmtNum(r.requests_1h, { compact: true }) },
    { key: "error_rate", header: "Errors", render: (r) => `${r.error_rate}%` },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="RPC Monitor" subtitle="Real-time monitoring of all RPC endpoints" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        <StatCard label="Total" value={stats?.total ?? 0} icon={Server} tone="blue" />
        <StatCard label="Healthy" value={stats?.healthy ?? 0} icon={CheckCircle} tone="green" />
        <StatCard label="Degraded" value={stats?.degraded ?? 0} icon={AlertTriangle} tone="amber" />
        <StatCard label="Down" value={stats?.down ?? 0} icon={XCircle} tone="red" />
        <StatCard label="Avg Latency" value={`${stats?.avg_latency_ms ?? 0}ms`} icon={Clock} tone="purple" />
        <StatCard label="ETH Gas (avg/tx)" value={fmtEth(gas?.avg_eth)} icon={Fuel} tone="cyan" muted={gas && !gas.enabled} />
      </div>
      <Card>
        <CardHeader><CardTitle>Endpoint Status</CardTitle></CardHeader>
        <CardContent><DataTable columns={cols} rows={data?.items ?? []} /></CardContent>
      </Card>
    </div>
  );
}
