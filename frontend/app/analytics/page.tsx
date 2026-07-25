"use client";

import { Coins, Send, Eye, DollarSign, Activity } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LineSeries, Donut } from "@/components/charts/Charts";
import { fmtNum, fmtUsd } from "@/lib/utils";

const CHAIN_COLORS: Record<string, string> = {
  solana: "#8b5cf6", ethereum: "#3b82f6", robinhood: "#22c55e",
  base: "#06b6d4", other: "#64748b",
};

export default function AnalyticsPage() {
  const { data: summary } = useApi<any>("/api/analytics/summary");
  const { data: activity } = useApi<any>("/api/analytics/activity");
  const { data: byChain } = useApi<any>("/api/analytics/by-chain");

  const series = (activity?.tokens_detected ?? []).map((p: any, i: number) => ({
    label: new Date(p.t * 1000).getHours() + ":00",
    tokens: p.value,
    forwarded: activity?.messages_forwarded?.[i]?.value ?? 0,
    alerts: activity?.alerts_triggered?.[i]?.value ?? 0,
  }));

  const donut = (byChain ?? []).map((c: any) => ({
    name: c.chain, value: c.count, color: CHAIN_COLORS[c.chain] || "#64748b",
  }));

  return (
    <div className="space-y-5">
      <PageHeader title="Analytics" subtitle="Deep insights and performance metrics across all systems" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Tokens Detected" value={fmtNum(summary?.tokens_detected)} icon={Coins} tone="green" delta={23.6} />
        <StatCard label="Messages Fwd" value={fmtNum(summary?.messages_forwarded)} icon={Send} tone="purple" delta={18.7} />
        <StatCard label="Watchlist Hits" value={fmtNum(summary?.watchlist_hits)} icon={Eye} tone="blue" delta={21.1} />
        <StatCard label="Total Volume" value={fmtUsd(summary?.total_volume_usd)} icon={DollarSign} tone="amber" delta={22.6} />
        <StatCard label="Avg Response" value={`${summary?.avg_response_ms ?? 0}ms`} icon={Activity} tone="cyan" delta={-8.2} />
      </div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Activity Over Time</CardTitle></CardHeader>
          <CardContent>
            <LineSeries data={series} keys={[
              { key: "tokens", color: "#22c55e", label: "Tokens" },
              { key: "forwarded", color: "#8b5cf6", label: "Forwarded" },
              { key: "alerts", color: "#f59e0b", label: "Alerts" },
            ]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Top Chains by Activity</CardTitle></CardHeader>
          <CardContent>
            <Donut data={donut.length ? donut : [{ name: "none", value: 1, color: "#334155" }]} />
            <div className="mt-3 space-y-1.5">
              {(byChain ?? []).map((c: any) => (
                <div key={c.chain} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-2 text-text-muted">
                    <span className="h-2 w-2 rounded-full" style={{ background: CHAIN_COLORS[c.chain] || "#64748b" }} />
                    {c.chain}
                  </span>
                  <span className="text-text">{c.count} ({c.pct}%)</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
