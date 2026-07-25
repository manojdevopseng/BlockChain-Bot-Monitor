"use client";

import { useState } from "react";
import { Coins, Plus, TrendingUp, Star, ExternalLink } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { CopyButton } from "@/components/CopyButton";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtUsd, shortAddr, timeAgo } from "@/lib/utils";

export default function TokensPage() {
  const [q, setQ] = useState("");
  const { data: stats } = useApi<any>("/api/tokens/stats");
  const { data } = useApi<any>(`/api/tokens?limit=50${q ? `&q=${q}` : ""}`);

  const cols: Column<any>[] = [
    { key: "symbol", header: "Token", render: (r) => (
      <div className="flex items-center gap-1.5">
        <span className="font-medium text-text">{r.symbol}</span>
        {r.symbol && <CopyButton value={r.symbol} />}
      </div>
    )},
    { key: "chain", header: "Chain", render: (r) => <Badge variant="purple">{r.chain}</Badge> },
    { key: "type", header: "Type", render: (r) => (
      <Badge variant={r.type === "new" ? "blue" : r.type === "migrated" ? "amber" : "green"}>{r.type}</Badge>
    )},
    { key: "address", header: "Address", render: (r) => (
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-xs text-accent-blue">{shortAddr(r.address)}</span>
        {r.address && <CopyButton value={r.address} />}
        {r.gmgn_url && (
          <a href={r.gmgn_url} target="_blank" rel="noopener noreferrer" title="View on GMGN"
             className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
            <ExternalLink size={12} />
          </a>
        )}
      </div>
    )},
    { key: "pair", header: "Pair", render: (r) => <span className="text-text-muted">{r.pair || "—"}</span> },
    { key: "mcap_usd", header: "MCap", render: (r) => fmtUsd(r.mcap_usd) },
    { key: "volume_24h", header: "Volume 24h", render: (r) => fmtUsd(r.volume_24h) },
    { key: "created_at", header: "Age", render: (r) => <span className="text-text-muted">{timeAgo(r.created_at)}</span> },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="Tokens" subtitle="Live token discovery across all enabled chains" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Tokens" value={stats?.total ?? 0} icon={Coins} tone="amber" delta={18.7} />
        <StatCard label="New (24h)" value={stats?.new_24h ?? 0} icon={Plus} tone="green" delta={24.8} />
        <StatCard label="Migrated" value={stats?.migrated ?? 0} icon={TrendingUp} tone="purple" delta={15.2} />
        <StatCard label="Watching" value={stats?.watching ?? 0} icon={Star} tone="cyan" delta={9.5} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Token Discovered</CardTitle>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search token…" className="w-56" />
        </CardHeader>
        <CardContent>
          <DataTable columns={cols} rows={data?.items ?? []} empty="No tokens found" />
        </CardContent>
      </Card>
    </div>
  );
}
