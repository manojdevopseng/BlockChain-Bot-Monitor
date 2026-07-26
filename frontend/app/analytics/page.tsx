"use client";

import { Coins, Bell, Fuel, Crosshair, ArrowRightLeft } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LineSeries, Donut } from "@/components/charts/Charts";
import { OutcomeSummary, GroupLeaderboard } from "@/components/Performance";
import { fmtNum } from "@/lib/utils";

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
    alerts: activity?.alerts_triggered?.[i]?.value ?? 0,
    gas: activity?.gas_hits?.[i]?.value ?? 0,
    premium: activity?.premium_detections?.[i]?.value ?? 0,
  }));

  const donut = (byChain ?? []).map((c: any) => ({
    name: c.chain, value: c.count, color: CHAIN_COLORS[c.chain] || "#64748b",
  }));

  return (
    <div className="space-y-5">
      <PageHeader title="Analytics" subtitle="Counted from what the scanners actually recorded" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Tokens Found" value={fmtNum(summary?.tokens_detected)} icon={Coins} tone="green" />
        <StatCard label="Tokens (24h)" value={fmtNum(summary?.tokens_24h)} icon={Coins} tone="blue" />
        <StatCard label="Cross-Chain Matches" value={fmtNum(summary?.cross_chain_matches)} icon={ArrowRightLeft} tone="purple" />
        <StatCard label="High-Gas Buys" value={fmtNum(summary?.gas_hits)} icon={Fuel} tone="amber" />
        <StatCard label="Premium Detections" value={fmtNum(summary?.premium_detections)} icon={Crosshair} tone="cyan" />
      </div>
      <OutcomeSummary />
      <GroupLeaderboard />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Activity — last 24h (per hour)</CardTitle></CardHeader>
          <CardContent>
            <LineSeries data={series} keys={[
              { key: "tokens", color: "#22c55e", label: "Tokens" },
              { key: "alerts", color: "#8b5cf6", label: "Alerts" },
              { key: "gas", color: "#f59e0b", label: "High-Gas Buys" },
              { key: "premium", color: "#06b6d4", label: "Premium" },
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
