"use client";

import { useEffect, useState } from "react";
import { Brain, CheckCircle2, Rocket, XCircle, ExternalLink, Eye } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { SearchBox } from "@/components/SectionFilters";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { CopyButton } from "@/components/CopyButton";
import { rowKey, shortAddr, timeAgo } from "@/lib/utils";

// Verdicts, in the order they matter. `skipped` and `rejected` are deliberately
// browsable: a filter you cannot audit is a filter you cannot trust.
const VERDICTS = [
  { id: "", label: "All" },
  { id: "matched", label: "Matched" },
  { id: "launching", label: "Launching" },
  { id: "rejected", label: "Rejected" },
  { id: "skipped", label: "Skipped" },
] as const;

const TONE: Record<string, "green" | "purple" | "amber" | "gray" | "red"> = {
  matched: "green", launching: "purple", rejected: "amber",
  skipped: "gray", error: "red",
};

function useDebounced(value: string, ms = 250): string {
  const [out, setOut] = useState(value.trim());
  useEffect(() => {
    const t = setTimeout(() => setOut(value.trim()), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}

export default function AiPage() {
  const [q, setQ] = useState("");
  const [verdict, setVerdict] = useState<string>("");
  const query = useDebounced(q);

  const params = new URLSearchParams({ limit: "150" });
  if (verdict) params.set("verdict", verdict);
  if (query) params.set("q", query);

  const { data: stats } = useApi<any>("/api/ai/stats");
  const { data } = useApi<any>(`/api/ai/decisions?${params.toString()}`);
  const { data: watch } = useApi<any>("/api/ai/watching");

  const items: any[] = data?.items ?? [];
  const watching: any[] = (watch?.items ?? []).filter((w: any) => w.status === "launching");

  return (
    <div className="space-y-5">
      <PageHeader
        title="AI Narrative"
        subtitle="Robinhood tokens judged by the X account behind them"
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Matched" value={stats?.matched ?? 0} icon={CheckCircle2} tone="green" />
        <StatCard label="Launching" value={stats?.launching ?? 0} icon={Rocket} tone="purple" />
        <StatCard label="Rejected" value={stats?.rejected ?? 0} icon={XCircle} tone="amber" />
        <StatCard label="Judged" value={stats?.total ?? 0} icon={Brain} tone="blue" />
      </div>

      {/* The agent is quiet by default and easy to forget about, so its state
          is stated rather than left to be inferred from an empty table. */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border-soft px-3 py-2.5 text-xs">
        <Badge variant={stats?.enabled ? "green" : "gray"}>
          {stats?.enabled ? "API key set" : "no XAI_API_KEY"}
        </Badge>
        {stats?.dry_run && <Badge variant="amber">dry run — nothing is sent</Badge>}
        <span className="text-text-dim">model</span>
        <span className="font-mono text-text">{stats?.model ?? "—"}</span>
        <span className="text-text-dim">· watching</span>
        <span className="text-text">{stats?.watching ?? 0}</span>
      </div>

      {watching.length > 0 && (
        <CollapsibleSection
          id="ai-watching"
          title="Launching — waiting for a contract"
          icon={<Eye size={14} />}
          count={watching.length}
        >
          <p className="mb-3 text-xs text-text-dim">
            These accounts look like a launch but have not published a contract
            address yet. Each is re-checked until one appears; if it matches the
            token, the alert becomes Matched.
          </p>
          <TableScroll>
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className={`${STICKY_HEAD} border-b border-border`}>
                  <th className="px-3 py-2.5 font-medium">Token</th>
                  <th className="px-3 py-2.5 font-medium">Account</th>
                  <th className="px-3 py-2.5 font-medium">CA</th>
                  <th className="px-3 py-2.5 font-medium">Seen</th>
                </tr>
              </thead>
              <tbody>
                {watching.map((w: any, i: number) => (
                  <tr key={rowKey(w, i)} className="border-b border-border-soft hover:bg-bg-hover/40">
                    <td className="px-3 py-3">
                      <span className="font-semibold text-text">{w.symbol || "?"}</span>
                      <span className="ml-2 text-text-muted">{w.name}</span>
                    </td>
                    <td className="px-3 py-3">
                      <a href={`https://x.com/${w.handle}`} target="_blank" rel="noopener noreferrer"
                         className="text-accent-blue hover:underline">@{w.handle}</a>
                    </td>
                    <td className="px-3 py-3">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-text-muted">{shortAddr(w.address)}</span>
                        <CopyButton value={w.address} />
                      </span>
                    </td>
                    <td className="px-3 py-3 text-text-muted">
                      {w.first_seen ? timeAgo(w.first_seen) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </CollapsibleSection>
      )}

      <CollapsibleSection
        id="ai-decisions"
        title="Decisions"
        icon={<Brain size={14} />}
        count={items.length}
        controls={<>
          <SearchBox value={q} onChange={setQ} placeholder="symbol / address / handle / narrative" />
          {VERDICTS.map((v) => (
            <Button key={v.id} size="sm" variant={verdict === v.id ? "primary" : "outline"}
              onClick={() => setVerdict(v.id)}>{v.label}</Button>
          ))}
        </>}
      >
        <p className="mb-3 text-xs text-text-dim">
          Every token the agent looked at, including the ones it threw away and
          why. A verified account is required before anything is asked of the
          model.
        </p>
        <TableScroll>
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Verdict</th>
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Account</th>
                <th className="px-3 py-2.5 font-medium">Narrative</th>
                <th className="px-3 py-2.5 font-medium">Reason</th>
                <th className="px-3 py-2.5 font-medium">CA</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-10 text-center text-text-dim">
                  {query || verdict ? "Nothing matches this filter"
                    : "No decisions yet — the agent is off, or has no key"}
                </td></tr>
              ) : items.map((d: any, i: number) => (
                <tr key={rowKey(d, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <Badge variant={TONE[d.verdict] ?? "gray"}>{d.verdict}</Badge>
                  </td>
                  <td className="px-3 py-3">
                    <div className="font-semibold text-text">{d.symbol || "?"}</div>
                    <div className="text-xs text-text-dim">{d.name}</div>
                  </td>
                  <td className="px-3 py-3">
                    {d.handle ? (
                      <>
                        <a href={`https://x.com/${d.handle}`} target="_blank" rel="noopener noreferrer"
                           className="text-accent-blue hover:underline">@{d.handle}</a>
                        <div className="text-xs text-text-dim">
                          {d.verified_type || "unverified"}
                          {d.followers ? ` · ${Number(d.followers).toLocaleString()}` : ""}
                        </div>
                      </>
                    ) : <span className="text-text-dim">—</span>}
                  </td>
                  <td className="px-3 py-3">
                    {d.narrative && d.narrative !== "none" ? (
                      <span className="text-text">
                        {d.narrative}
                        {d.confidence ? <span className="text-text-dim"> ({d.confidence}/10)</span> : null}
                      </span>
                    ) : <span className="text-text-dim">—</span>}
                  </td>
                  <td className="max-w-[280px] px-3 py-3">
                    <span className="text-text-muted">{d.reason || "—"}</span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="flex items-center gap-1.5">
                      <span className="font-mono text-xs text-text-muted">{shortAddr(d.address)}</span>
                      <CopyButton value={d.address} />
                      {d.gmgn_url && (
                        <a href={d.gmgn_url} target="_blank" rel="noopener noreferrer" title="View on GMGN"
                           className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-text-muted">{d.at ? timeAgo(d.at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </CollapsibleSection>
    </div>
  );
}
