"use client";

import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { cn, fmtNum } from "@/lib/utils";

export default function ChainsPage() {
  const { data } = useApi<any>("/api/chains");
  const { data: stats } = useApi<any>("/api/chains/stats");
  const chains = data?.items ?? [];

  const cols: Column<any>[] = [
    { key: "name", header: "Chain", render: (r) => (
      <span className={cn("font-medium", r.enabled ? "text-text" : "text-text-dim")}>{r.name}</span>
    )},
    { key: "status", header: "Status", render: (r) => (
      <Badge variant={r.status === "connected" ? "green" : r.status === "disabled" ? "gray" : "amber"}>{r.status}</Badge>
    )},
    { key: "latency_ms", header: "Latency", render: (r) => `${r.latency_ms} ms` },
    { key: "tps", header: "TPS", render: (r) => fmtNum(r.tps) },
    { key: "block_height", header: "Block", render: (r) => fmtNum(r.block_height, { compact: true }) },
    { key: "uptime", header: "Uptime", render: (r) => <span className="text-accent-green">{r.uptime}%</span> },
    { key: "errors_24h", header: "Errors" },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="Chains" subtitle="Monitor all connected blockchains and their real-time status" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {chains.slice(0, 4).map((c: any) => (
          <Card key={c.id} className={cn(!c.enabled && "opacity-40")}>
            <CardContent className="pt-4">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-text">{c.name}</span>
                <Badge variant={c.status === "connected" ? "green" : "gray"}>{c.status}</Badge>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                <div><div className="text-text-dim">Latency</div><div className="mt-0.5 font-semibold text-text">{c.latency_ms}ms</div></div>
                <div><div className="text-text-dim">TPS</div><div className="mt-0.5 font-semibold text-text">{fmtNum(c.tps)}</div></div>
                <div><div className="text-text-dim">Health</div><div className="mt-0.5 font-semibold text-accent-green">{c.uptime}%</div></div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Chain Performance Overview</CardTitle>
          <span className="text-[11px] text-text-dim">{stats?.healthy ?? 0}/{stats?.total ?? 0} healthy</span>
        </CardHeader>
        <CardContent><DataTable columns={cols} rows={chains} /></CardContent>
      </Card>
    </div>
  );
}
