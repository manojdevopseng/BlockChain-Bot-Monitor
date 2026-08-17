"use client";

import { useState } from "react";
import { ExternalLink, Fuel, Rocket, Target } from "lucide-react";
import { useApi } from "@/lib/api";
import { CopyButton } from "@/components/CopyButton";
import { Badge, Variant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FilterTabs } from "@/components/SectionFilters";
import { cn, fmtNum, timeAgo } from "@/lib/utils";

/* The three detection feeds in one list.
 *
 * Premium calls, launchpad launches and high-gas early buys, newest first, so
 * the interesting rows can be read without scrolling three panels. Nothing is
 * stored for this: it reads the same collections Detections writes.
 *
 * The quotas are the point. Measured over 24 hours: 2,865 launches, 36 gas
 * alerts, 0 calls. Merged straight by time, launches are 98.8% of the feed and
 * a premium call — a handful a day, and the most valuable row here — is off the
 * screen within minutes. With a quota each, a call from 17:53 is still there at
 * 18:30, because only a newer call can push it out. */

const SOURCES: Record<string, { label: string; icon: any; tone: Variant }> = {
  calls: { label: "Premium call", icon: Target, tone: "purple" },
  launches: { label: "Launch", icon: Rocket, tone: "blue" },
  gas: { label: "High gas", icon: Fuel, tone: "amber" },
};

const TABS = [
  { id: "all", label: "All" },
  { id: "calls", label: "Calls" },
  { id: "launches", label: "Launches" },
  { id: "gas", label: "Gas" },
];

export function LiveFeed() {
  const [source, setSource] = useState("all");
  const { data } = useApi<any>(`/api/dashboard/feed?source=${source}`);
  const items: any[] = data?.items ?? [];
  const strongAt: number = data?.strong_dev_buy_eth ?? 0.199;

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>Live Activity</CardTitle>
        <FilterTabs value={source} onChange={setSource} options={TABS} />
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-dim">
          Premium calls, Robinhood launches and high-gas early buys together.
          Each source keeps its own place in the list, so a call is not pushed
          off by the launch feed. {data?.note}
        </p>

        {items.length === 0 ? (
          <p className="py-8 text-center text-xs text-text-dim">
            Nothing yet on this filter
          </p>
        ) : (
          <div className="divide-y divide-border-soft">
            {items.map((r, i) => (
              <Row key={`${r.source}-${r.address}-${r.at}-${i}`}
                   row={r} strongAt={strongAt} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ row, strongAt }: { row: any; strongAt: number }) {
  const meta = SOURCES[row.source] ?? SOURCES.launches;
  const Icon = meta.icon;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5">
      <span className="flex w-[104px] shrink-0 items-center gap-1.5">
        <Icon size={13} className="shrink-0 text-text-dim" />
        <Badge variant={meta.tone}>{row.source === "calls" ? "call"
          : row.source === "gas" ? "gas" : row.launchpad || "launch"}</Badge>
      </span>

      {/* Symbol, then the address and the two things anybody does with it. */}
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="font-semibold text-text">${row.symbol || "?"}</span>
        <CopyButton value={row.address} />
        {row.gmgn_url && (
          <a href={row.gmgn_url} target="_blank" rel="noopener noreferrer"
             title="View on GMGN"
             className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
            <ExternalLink size={12} />
          </a>
        )}
      </span>

      <span className="min-w-0 flex-1 text-xs text-text-muted">
        <Detail row={row} strongAt={strongAt} />
      </span>

      <span className="shrink-0 font-mono text-[11px] text-text-dim">
        {row.at ? timeAgo(row.at) : "—"}
      </span>
    </div>
  );
}

/* The one line worth reading about this row, and it differs per source —
 * a launch is about who is behind it, a gas alert is about the fee, a call is
 * about which group said it. */
function Detail({ row, strongAt }: { row: any; strongAt: number }) {
  if (row.source === "gas") {
    return (
      <>
        <b className="text-text">{Number(row.fee_eth ?? 0).toFixed(6)} ETH</b> gas
        {row.age_seconds != null ? ` · token ${row.age_seconds}s old` : ""}
        {row.dex ? ` · ${row.dex}` : ""}
      </>
    );
  }

  if (row.source === "calls") {
    return (
      <span className="flex flex-wrap items-center gap-1.5">
        {(row.groups ?? []).slice(0, 3).map((g: any, i: number) => (
          // The chips and their colours come from Forwarder → Premium Groups,
          // the same as on the Detections table.
          <span key={`${g.name}-${i}`}
                className="rounded px-1.5 py-0.5 text-[10px] font-medium"
                style={g.color ? { background: `${g.color}22`, color: g.color }
                               : undefined}>
            {g.name}
          </span>
        ))}
        {row.calls ? <span className="text-text-dim">call {row.calls}</span> : null}
        {row.keyword ? (
          <span className="text-accent-amber">kw: {row.keyword}</span>
        ) : null}
      </span>
    );
  }

  const strong = strongAt > 0 && Number(row.dev_buy_eth ?? 0) > strongAt;
  return (
    <span className="flex flex-wrap items-center gap-x-2">
      {row.watched && <span title="Watched account">👁</span>}
      {row.handle ? (
        <a href={row.link || `https://x.com/${row.handle}`}
           target="_blank" rel="noopener noreferrer"
           className="text-accent-blue hover:underline">@{row.handle}</a>
      ) : (
        <span className="text-text-dim">no X account</span>
      )}
      {row.followers ? <span>{fmtNum(row.followers)} followers</span> : null}
      {row.handle_seq > 1 ? (
        <span className="text-text-dim" title="Launches from this account">
          ×{row.handle_seq}
        </span>
      ) : null}
      {row.dev_buy_eth ? (
        <span className={cn("font-mono",
                            strong ? "font-semibold text-accent-green"
                                   : "text-accent-amber")}>
          {strong ? "🟢 " : ""}{Number(row.dev_buy_eth).toFixed(3)} Ξ
        </span>
      ) : null}
      {row.matched_keywords ? (
        <span className="text-accent-amber">kw: {row.matched_keywords}</span>
      ) : null}
    </span>
  );
}
