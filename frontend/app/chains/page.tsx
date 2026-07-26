"use client";

import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { cn, fmtNum } from "@/lib/utils";

function statusVariant(s: string) {
  if (s === "connected") return "green";
  if (s === "disabled") return "gray";
  if (s === "not configured") return "amber";
  return "red";
}

export default function ChainsPage() {
  const { data } = useApi<any>("/api/chains");
  const { data: stats } = useApi<any>("/api/chains/stats");
  const chains = data?.items ?? [];

  const cols: Column<any>[] = [
    { key: "name", header: "Chain", render: (r) => (
      <span className={cn("font-medium", r.enabled ? "text-text" : "text-text-dim")}>{r.name}</span>
    )},
    { key: "symbol", header: "Symbol", render: (r) => <Badge variant="purple">{r.symbol}</Badge> },
    { key: "status", header: "Status", render: (r) => (
      <Badge variant={statusVariant(r.status) as any}>{r.status}</Badge>
    )},
    { key: "ws_configured", header: "WebSocket", render: (r) => (
      <Badge variant={r.ws_configured ? "green" : "gray"}>{r.ws_configured ? "configured" : "not set"}</Badge>
    )},
    { key: "rpc_configured", header: "RPC", render: (r) => (
      <Badge variant={r.rpc_configured ? "green" : "gray"}>{r.rpc_configured ? "configured" : "not set"}</Badge>
    )},
    { key: "tokens", header: "Tokens Found", render: (r) => fmtNum(r.tokens) },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="Chains" subtitle="Chains this bot monitors, and their live connection state" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {chains.map((c: any) => (
          <Card key={c.id} className={cn(!c.enabled && "opacity-40")}>
            <CardContent className="pt-4">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-text">{c.name}</span>
                <Badge variant={statusVariant(c.status) as any}>{c.status}</Badge>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-center text-xs">
                <div>
                  <div className="text-text-dim">Tokens found</div>
                  <div className="mt-0.5 font-semibold text-text">{fmtNum(c.tokens)}</div>
                </div>
                <div>
                  <div className="text-text-dim">WebSocket</div>
                  <div className={cn("mt-0.5 font-semibold", c.ws_configured ? "text-accent-green" : "text-text-dim")}>
                    {c.ws_configured ? "configured" : "not set"}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Chain Overview</CardTitle>
          <span className="text-[11px] text-text-dim">
            {stats?.connected ?? 0}/{stats?.total ?? 0} connected
          </span>
        </CardHeader>
        <CardContent>
          <DataTable columns={cols} rows={chains} empty="No chains configured" />
        </CardContent>
      </Card>
    </div>
  );
}
