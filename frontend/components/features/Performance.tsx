"use client";

import { Download, TrendingUp, Trophy } from "lucide-react";
import { useApi, getToken } from "@/lib/api";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { TableScroll, STICKY_HEAD } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SOURCE_LABEL: Record<string, string> = {
  xchain_eth: "SOL→ETH matches",
  xchain_rbh: "SOL→RBH matches",
  gas: "High-gas early buys",
  premium: "Premium caller calls",
};

const CHECKPOINTS = ["15m", "1h", "6h", "24h"];

function pct(v: number | null | undefined) {
  if (v == null) return <span className="text-text-dim">—</span>;
  return (
    <span className={v > 0 ? "text-accent-green" : v < 0 ? "text-accent-red" : "text-text-muted"}>
      {v > 0 ? "+" : ""}{v.toFixed(1)}%
    </span>
  );
}

// The API is behind a bearer token, so a plain <a href> download would 401.
// Fetch it, then hand the blob to a temporary link.
async function download(path: string, filename: string) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) return;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function DownloadCsv({ path, filename, label = "CSV" }: {
  path: string; filename: string; label?: string;
}) {
  return (
    <Button size="sm" variant="outline" onClick={() => download(path, filename)}>
      <Download size={13} /> {label}
    </Button>
  );
}

/** Per-source performance: does each feature actually produce winners? */
export function OutcomeSummary() {
  const { data } = useApi<any>("/api/outcomes/summary?days=7", { refreshInterval: 60000 });
  const bySource: Record<string, any> = data?.by_source ?? {};
  const rows = Object.entries(bySource);

  return (
    <CollapsibleSection
      id="perf-sources"
      title="Alert Performance — last 7 days"
      icon={<TrendingUp size={14} />}
      count={data?.overall?.tracked ?? 0}
      controls={<DownloadCsv path="/api/outcomes/export.csv"
                             filename="outcomes.csv" label="Export" />}
    >
      <p className="mb-3 text-xs text-text-dim">
        Each alert is followed forward and priced at 15m, 1h, 6h and 24h. “Up” is
        the share that were above the entry price — the number that says whether
        a threshold is earning its place. Prices come from DexScreener, so this
        adds no load to GMGN.
      </p>
      <TableScroll>
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className={cn(STICKY_HEAD, "border-b border-border")}>
              <th className="px-3 py-2.5 font-medium">Source</th>
              <th className="px-3 py-2.5 font-medium">Tracked</th>
              <th className="px-3 py-2.5 font-medium">No price</th>
              {CHECKPOINTS.map((c) => (
                <th key={c} className="px-3 py-2.5 font-medium">{c} avg</th>
              ))}
              <th className="px-3 py-2.5 font-medium">1h up</th>
              <th className="px-3 py-2.5 font-medium">Best</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-3 py-10 text-center text-text-dim">
                  Nothing tracked yet — this fills in as alerts fire and reach
                  their first checkpoint.
                </td>
              </tr>
            ) : rows.map(([src, s]: [string, any]) => (
              <tr key={src} className="border-b border-border-soft hover:bg-bg-hover/40">
                <td className="px-3 py-3 text-text">{SOURCE_LABEL[src] ?? src}</td>
                <td className="px-3 py-3 text-text-muted">{s.tracked}</td>
                {/* Robinhood Chain is on neither DexScreener nor GMGN's token
                    endpoint, so those alerts cannot be priced. Shown rather
                    than folded into the averages as 0%. */}
                <td className="px-3 py-3">
                  {s.unpriceable ? (
                    <span className="text-text-dim" title="chain has no public price source">
                      {s.unpriceable}
                    </span>
                  ) : <span className="text-text-dim">—</span>}
                </td>
                {CHECKPOINTS.map((c) => (
                  <td key={c} className="px-3 py-3">{pct(s[c]?.avg_pct)}</td>
                ))}
                <td className="px-3 py-3">
                  {s["1h"] ? <Badge variant={s["1h"].hit_rate >= 50 ? "green" : "gray"}>
                    {s["1h"].hit_rate}%
                  </Badge> : <span className="text-text-dim">—</span>}
                </td>
                <td className="px-3 py-3">{pct(s["1h"]?.best_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </CollapsibleSection>
  );
}

/** Which premium groups are worth listening to. */
export function GroupLeaderboard() {
  const { data } = useApi<any>("/api/outcomes/groups?days=30&min_calls=1",
                               { refreshInterval: 60000 });
  const items: any[] = data?.items ?? [];

  return (
    <CollapsibleSection
      id="perf-groups"
      title="Premium Groups — ranked by outcome"
      icon={<Trophy size={14} />}
      count={items.length}
    >
      <p className="mb-3 text-xs text-text-dim">
        Ranked by how often a group’s calls went up, then by how far. Only groups
        whose calls have a price reading appear — the switch on the Forwarder
        page is what to do with a group at the bottom.
      </p>
      <TableScroll>
        <table className="w-full min-w-[680px] text-sm">
          <thead>
            <tr className={cn(STICKY_HEAD, "border-b border-border")}>
              <th className="px-3 py-2.5 font-medium">#</th>
              <th className="px-3 py-2.5 font-medium">Group</th>
              <th className="px-3 py-2.5 font-medium">Calls</th>
              <th className="px-3 py-2.5 font-medium">Up</th>
              <th className="px-3 py-2.5 font-medium">Avg best</th>
              <th className="px-3 py-2.5 font-medium">Avg 1h</th>
              <th className="px-3 py-2.5 font-medium">Top call</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-10 text-center text-text-dim">
                  No group has a priced call yet
                </td>
              </tr>
            ) : items.map((g, i) => (
              <tr key={g.group} className="border-b border-border-soft hover:bg-bg-hover/40">
                <td className="px-3 py-3 text-text-dim">{i + 1}</td>
                <td className="px-3 py-3 text-text">{g.group}</td>
                <td className="px-3 py-3 text-text-muted">{g.calls}</td>
                <td className="px-3 py-3">
                  <Badge variant={g.hit_rate >= 50 ? "green" : "gray"}>{g.hit_rate}%</Badge>
                </td>
                <td className="px-3 py-3">{pct(g.avg_best_pct)}</td>
                <td className="px-3 py-3">{pct(g.avg_1h_pct)}</td>
                <td className="px-3 py-3 text-xs">
                  {g.top_call ? (
                    <span className="text-text-muted">
                      {/* The ticker is the one thing in this table worth
                          opening, so it opens. Falls back to plain text for
                          rows recorded before the address travelled with it. */}
                      {g.top_call.gmgn_url ? (
                        <a href={g.top_call.gmgn_url} target="_blank" rel="noopener noreferrer"
                           title="View on GMGN"
                           className="font-medium text-brand-soft hover:underline">
                          {g.top_call.symbol}
                        </a>
                      ) : g.top_call.symbol}{" "}
                      {pct(g.top_call.pct)}
                    </span>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </CollapsibleSection>
  );
}
