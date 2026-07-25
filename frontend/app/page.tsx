"use client";

import { Bell, Coins, Fuel, Eye, Activity } from "lucide-react";
import { useApi } from "@/lib/api";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { Donut } from "@/components/charts/Charts";
import { fmtEth, fmtUsd, timeAgo } from "@/lib/utils";

const ICONS: Record<string, any> = { total_alerts: Bell, total_tokens: Coins, eth_gas: Fuel, watchlist: Eye };
const TONE: Record<string, any> = { total_alerts: "red", total_tokens: "amber", eth_gas: "blue", watchlist: "cyan" };

export default function Dashboard() {
  const { data: stats } = useApi<any>("/api/dashboard/stats");
  const { data: overview } = useApi<any>("/api/dashboard/overview");
  const { data: activity } = useApi<any>("/api/dashboard/activity");
  const { data: tokens } = useApi<any>("/api/tokens?limit=6");

  const cards = stats?.cards ?? [];
  const components = overview?.components ?? [];

  const alertCols: Column<any>[] = [
    { key: "message", header: "Event", render: (r) => <span className="text-text">{r.message}</span> },
    { key: "chain", header: "Chain", render: (r) => <Badge variant="purple">{r.chain}</Badge> },
    { key: "severity", header: "Severity", render: (r) => (
      <Badge variant={r.severity === "high" ? "red" : r.severity === "medium" ? "amber" : "blue"}>{r.severity}</Badge>
    )},
    { key: "created_at", header: "When", render: (r) => <span className="text-text-muted">{timeAgo(r.created_at)}</span> },
  ];

  return (
    <div className="space-y-5">
      {/* stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {cards.map((c: any) => (
          <StatCard key={c.key} label={c.label}
            value={c.key === "eth_gas" ? fmtEth(c.value) : c.value}
            delta={c.delta} icon={ICONS[c.key] || Activity} tone={TONE[c.key] || "purple"} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* recent alerts */}
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Recent Alerts</CardTitle></CardHeader>
          <CardContent>
            <DataTable columns={alertCols} rows={activity ?? []} empty="No alerts yet" />
          </CardContent>
        </Card>

        {/* system overview */}
        <Card>
          <CardHeader>
            <CardTitle>System Overview</CardTitle>
            <span className="text-lg font-bold text-accent-green">{overview?.overall_health ?? 0}%</span>
          </CardHeader>
          <CardContent className="space-y-2">
            {components.map((c: any) => (
              <div key={c.name} className="flex items-center justify-between text-sm">
                <span className="text-text-muted">{c.name}</span>
                <Badge variant={c.status === "connected" ? "green" : "gray"}>{c.status}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* latest tokens */}
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Latest Tokens</CardTitle></CardHeader>
          <CardContent>
            <DataTable
              columns={[
                { key: "symbol", header: "Token", render: (r) => <span className="font-medium">{r.symbol}</span> },
                { key: "chain", header: "Chain", render: (r) => <Badge variant="purple">{r.chain}</Badge> },
                { key: "mcap_usd", header: "MCap", render: (r) => fmtUsd(r.mcap_usd) },
                { key: "fee_eth", header: "Gas Fee", render: (r) => <span className="font-mono text-xs text-accent-blue">{fmtEth(r.fee_eth)}</span> },
              ]}
              rows={tokens?.items ?? []}
            />
          </CardContent>
        </Card>

        {/* health donut */}
        <Card>
          <CardHeader><CardTitle>Health Distribution</CardTitle></CardHeader>
          <CardContent>
            <Donut data={[
              { name: "Connected", value: components.filter((c: any) => c.status === "connected").length, color: "#22c55e" },
              { name: "Disabled", value: components.filter((c: any) => c.status !== "connected").length, color: "#334155" },
            ]} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
