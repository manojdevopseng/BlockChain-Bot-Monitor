"use client";

import { Server, CheckCircle, Settings2, XCircle, Fuel } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { CredentialsManager } from "@/components/CredentialsManager";
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
    { key: "kind", header: "Type", render: (r) => <Badge variant="blue">{r.kind}</Badge> },
    { key: "status", header: "Status", render: (r) => (
      <Badge variant={
        r.status === "connected" ? "green"
        : r.status === "configured" ? "blue"
        : r.status === "disabled" ? "gray"
        : r.status === "not configured" ? "amber" : "red"
      }>{r.status}</Badge>
    )},
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="RPC Monitor" subtitle="RPC endpoints configured in .env and their live connection state" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Endpoints" value={stats?.total ?? 0} icon={Server} tone="blue" />
        <StatCard label="Configured" value={stats?.configured ?? 0} icon={Settings2} tone="purple" />
        <StatCard label="Connected" value={stats?.connected ?? 0} icon={CheckCircle} tone="green" />
        <StatCard label="Not Set" value={stats?.unconfigured ?? 0} icon={XCircle} tone="amber" />
        <StatCard label="High-Gas Buys" value={gas?.count ?? 0} icon={Fuel} tone="cyan" muted={gas && !gas.enabled} />
      </div>
      <Card>
        <CardHeader><CardTitle>Endpoint Status</CardTitle></CardHeader>
        <CardContent><DataTable columns={cols} rows={data?.items ?? []} empty="No RPC endpoints configured in .env" /></CardContent>
      </Card>
      {/* The URL fields, next to the connection state they actually drive,
          rather than three cards down the Settings page. One group ("RPC
          Endpoints") renders as one card, so no multi-column grid here. */}
      <CredentialsManager only="RPC Endpoints" />
    </div>
  );
}
