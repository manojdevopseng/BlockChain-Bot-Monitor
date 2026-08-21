"use client";

/** The AI Narrative page: live launches, the model's decisions, and the
 *  read-only checks that sit beside them.
 *
 *  The sections themselves are in ./_components — this file is the layout and
 *  the Decisions table, which is the only part specific to the page itself.
 */

import { useEffect, useState } from "react";
import { Brain, CheckCircle2, Rocket, XCircle } from "lucide-react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { HistorySelect, SearchBox } from "@/components/SectionFilters";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { fmtDateTime, rowKey, timeAgo } from "@/lib/utils";
import { Age, PAGE, TickProvider, TokenCell, TONE, VERDICTS, XLink } from "./_components/shared";
import { McapCheck } from "./_components/McapCheck";
import { FactCheck } from "./_components/FactCheck";
import { XCheck } from "./_components/XCheck";

export default function AiPage() {
  const [limit, setLimit] = useState(PAGE);
  const [q, setQ] = useState("");
  const [followers, setFollowers] = useState("");
  const [date, setDate] = useState("");
  const [verdict, setVerdict] = useState<string>("");
  const query = useDebounced(q);
  const minFollowers = useDebounced(followers);

  const params = new URLSearchParams({ limit: String(limit) });
  if (verdict) params.set("verdict", verdict);
  if (query) params.set("q", query);
  // Only when a number is actually typed — an empty box is no filter, not zero.
  if (/^\d+$/.test(minFollowers)) params.set("min_followers", minFollowers);
  if (date) params.set("date", date);

  const { data: stats } = useApi<any>("/api/ai/stats");
  const { data } = useApi<any>(`/api/ai/decisions?${params.toString()}`);
  const { data: datesData } = useApi<any>("/api/ai/decision-dates",
    { refreshInterval: 300000 });
  // A filter change starts again at the first page.
  useEffect(() => setLimit(PAGE), [query, minFollowers, date, verdict]);

  const items: any[] = data?.items ?? [];

  return (
    <TickProvider>
    <div className="space-y-5">
      <PageHeader
        title="AI Narrative"
        subtitle="pump.fun tokens judged by the X account behind them"
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Matched" value={stats?.matched ?? 0} icon={CheckCircle2} tone="green" />
        <StatCard label="Launching" value={stats?.launching ?? 0} icon={Rocket} tone="purple" />
        <StatCard label="Rejected" value={stats?.rejected ?? 0} icon={XCircle} tone="amber" />
        <StatCard label="Pending" value={stats?.pending ?? 0} icon={Brain} tone="blue" />
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
      </div>

      {/* The manual check sits at the top: it is the one thing here you come to
          the page to ask rather than to read. Under it, Decisions — the answer —
          then the working: the raw feed of verified launches, and the originals
          picked out of it. */}
      <McapCheck />

      <CollapsibleSection
        id="ai-decisions"
        title="Decisions"
        icon={<Brain size={14} />}
        count={data?.total ?? items.length}
        controls={<>
          <SearchBox value={q} onChange={setQ} placeholder="symbol / address / handle / narrative" />
          <Input
            value={followers}
            onChange={(e) => setFollowers(e.target.value.replace(/\D/g, ""))}
            placeholder="min followers"
            inputMode="numeric"
            className="h-8 w-32 text-xs"
          />
          <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
          {VERDICTS.map((v) => (
            <Button key={v.id} size="sm" variant={verdict === v.id ? "primary" : "outline"}
              onClick={() => setVerdict(v.id)}>{v.label}</Button>
          ))}
        </>}
      >
        <p className="mb-3 text-xs text-text-dim">
          Every launch the agent looked at, including the ones it threw away and
          why. Before the model: the account must be verified and have
          followers, and a link is asked about once — as is a name and ticker,
          once per IST day. Everything else goes to the model.
          <b>Telegram</b> is a separate rule: one X link must have carried five
          launches inside five minutes — the names may differ, the link may not
          — and the launch must also have crossed the market cap bar in its
          first minute. Those are the ones sent to the chat.
        </p>
        <TableScroll>
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Verdict</th>
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Age</th>
                <th className="px-3 py-2.5 font-medium">Peak MC</th>
                <th className="px-3 py-2.5 font-medium">Account</th>
                <th className="px-3 py-2.5 font-medium">Narrative</th>
                <th className="px-3 py-2.5 font-medium">Reason</th>
                <th className="px-3 py-2.5 font-medium">X link</th>
                <th className="px-3 py-2.5 font-medium">When</th>
                <th className="px-3 py-2.5 font-medium">Fact check</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={10} className="px-3 py-10 text-center text-text-dim">
                  {query || verdict || minFollowers ? "Nothing matches this filter"
                    : date ? `No decisions on ${date}`
                    : "No decisions yet — the agent is off, or has no key"}
                </td></tr>
              ) : items.map((d: any, i: number) => (
                <tr key={rowKey(d, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <Badge variant={TONE[d.verdict] ?? "gray"}>{d.verdict}</Badge>
                    {d.telegram ? (
                      <div className="mt-1"><Badge variant="green">telegram</Badge></div>
                    ) : null}
                    {/* Inside Launching, the ones that have named their own
                        contract — the thing the whole branch is waiting for. */}
                    {d.ca_matched ? (
                      <div className="mt-1"><Badge variant="green">CA matched</Badge></div>
                    ) : null}
                  </td>
                  <TokenCell address={d.address} symbol={d.symbol} name={d.name} />
                  {/* Age from the launch, not from the moment we judged it, so
                      it reads the same as the age in the live section above. */}
                  <td className="px-3 py-3 font-mono text-xs text-text-muted">
                    <Age ts={d.open_timestamp || d.at} />
                  </td>
                  {/* The highest it reached in its first minute. Green once it
                      is past the bar, which is the whole reason it is here. */}
                  <td className="px-3 py-3 font-mono text-xs">
                    {d.peak_mcap_usd ? (
                      <span className={d.telegram ? "text-accent-green" : "text-text-muted"}>
                        ${Number(d.peak_mcap_usd).toLocaleString()}
                      </span>
                    ) : <span className="text-text-dim">—</span>}
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
                  <td className="max-w-[170px] px-3 py-3">
                    <XLink link={d.link} handle={d.handle} kind={d.kind} />
                  </td>
                  {/* The clock time, the way the live section above shows it —
                      "3m ago" answers how long, not when, and the two sections
                      are read side by side. The age stays underneath, since it
                      is the quicker read of the two. */}
                  <td className="px-3 py-3">
                    <span className="whitespace-nowrap font-mono text-xs text-text-muted">
                      {d.at ? fmtDateTime(d.at) : "—"}
                    </span>
                    {d.at && (
                      <div className="text-[11px] text-text-dim">{timeAgo(d.at)}</div>
                    )}
                  </td>
                  <td className="px-3 py-3"><FactCheck row={d} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
        {typeof data?.total === "number" && items.length < data.total && (
          <div className="mt-3 flex items-center justify-center gap-3 text-xs">
            <span className="text-text-dim">
              showing {items.length.toLocaleString()} of {data.total.toLocaleString()}
            </span>
            <Button size="sm" variant="outline" onClick={() => setLimit((n) => n + PAGE)}>
              Load {Math.min(PAGE, data.total - items.length).toLocaleString()} more
            </Button>
          </div>
        )}
      </CollapsibleSection>

      <XCheck />

    </div>
    </TickProvider>
  );
}
