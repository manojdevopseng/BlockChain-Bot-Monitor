"use client";

import { useEffect, useState } from "react";
import { Brain, CheckCircle2, Crown, Rocket, XCircle, ExternalLink, Eye, RefreshCw, Twitter } from "lucide-react";
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
import { CopyButton } from "@/components/CopyButton";
import { fmtDateTime, rowKey, shortAddr, timeAgo } from "@/lib/utils";

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

// Ages were rendered once per fetch, so a row sat on "1m ago" for a whole
// minute and the section looked frozen between refreshes. This re-renders every
// second, which is what makes a live counter live.
function useTick(ms = 1000): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((v) => v + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
  return n;
}

// Seconds only while they mean something. A launch is worth watching by the
// second in its first minute; after that the seconds are just noise ticking in
// the corner of the eye, so the age rounds to minutes and then to hours.
function ageLabel(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  const h = Math.floor(s / 3600);
  return `${h}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

// Highlighting the Settings keywords where they appear. Whole-word and
// case-insensitive, matching app/keywords.py exactly — "ai" lights up in "AI
// token" and "ai-agent" but not in "main road" — so what the page marks is what
// the forwarder would have matched. Keywords are fetched rather than baked in,
// so one added in Settings shows up here without a deploy.
function useKeywordRegex(): RegExp | null {
  const { data } = useApi<any>("/api/settings/keywords", { refreshInterval: 120000 });
  const words: string[] = data?.items ?? [];
  if (!words.length) return null;
  // Longest first, so "New Token Launchpad" wins over "Token Launchpad".
  const alts = [...words]
    .sort((a, b) => b.length - a.length)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  try {
    return new RegExp(`\\b(${alts})\\b`, "gi");
  } catch {
    return null;
  }
}

function Highlighted({ text, rx }: { text: string; rx: RegExp | null }) {
  if (!text) return <>—</>;
  if (!rx) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let last = 0;
  // A global regex carries state between calls, so it is reset per render.
  rx.lastIndex = 0;
  for (let m = rx.exec(text); m !== null; m = rx.exec(text)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(
      // The green Badge treatment the Verified column uses, minus the badge's
      // own padding so a hit sits inside a sentence without breaking its rhythm.
      <mark key={`${m.index}-${m[0]}`}
        className="rounded-md border border-accent-green/30 bg-accent-green/15
                   px-1 py-0.5 text-[11px] font-medium text-accent-green">
        {m[0]}
      </mark>,
    );
    last = m.index + m[0].length;
  }
  if (!parts.length) return <>{text}</>;
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

// The launch table, shared by the live section and the OG one. Same nine
// columns, same row rendering — two copies would have drifted the moment one
// of them gained a column.
function LaunchTable({ items, rx, empty }: {
  items: any[];
  rx: RegExp | null;
  empty: string;
}) {
  return (
        <TableScroll>
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Age</th>
                <th className="px-3 py-2.5 font-medium">Link type</th>
                <th className="px-3 py-2.5 font-medium">Account</th>
                <th className="px-3 py-2.5 font-medium">Verified</th>
                <th className="px-3 py-2.5 font-medium">Followers</th>
                <th className="px-3 py-2.5 font-medium">Post</th>
                <th className="px-3 py-2.5 font-medium">Text</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={9} className="px-3 py-10 text-center text-text-dim">
                  {empty}
                </td></tr>
              ) : items.map((r: any, i: number) => (
                <tr key={rowKey(r, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <span className="flex items-center gap-1.5">
                      <a href={`https://gmgn.ai/sol/token/${r.address}`}
                         target="_blank" rel="noopener noreferrer"
                         title="View on GMGN"
                         className="font-semibold text-brand-soft hover:underline">
                        {r.symbol}
                      </a>
                      <CopyButton value={r.address} />
                    </span>
                    <span className="font-mono text-[11px] text-text-dim">
                      {shortAddr(r.address)}
                    </span>
                  </td>
                  <td className="px-3 py-3 font-mono text-xs text-text-muted">
                    {r.open_timestamp ? ageLabel(r.open_timestamp) : "—"}
                  </td>
                  <td className="px-3 py-3">
                    <Badge variant={r.kind === "tweet" ? "purple" : "blue"}>
                      {r.kind}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">
                    {r.handle ? (
                      <a href={`https://x.com/${r.handle}`} target="_blank" rel="noopener noreferrer"
                         className="text-accent-blue hover:underline">@{r.handle}</a>
                    ) : <span className="font-mono text-xs text-text-dim">{r.link?.slice(0, 24)}</span>}
                    {!r.resolved && r.handle && (
                      <span className="ml-2 text-xs text-accent-amber">not found</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <Badge variant="green">{r.verified_type || "verified"}</Badge>
                  </td>
                  <td className="px-3 py-3 text-text-muted">
                    {r.followers ? Number(r.followers).toLocaleString() : "—"}
                  </td>
                  <td className="px-3 py-3">
                    {r.post_found
                      ? <span className="text-accent-green">{r.post_source}
                          {r.post_age_minutes != null && (
                            <span className="text-text-dim"> · {r.post_age_minutes}m</span>
                          )}
                        </span>
                      : <span className="text-text-dim">—</span>}
                  </td>
                  <td className="max-w-[300px] px-3 py-3 text-text-muted">
                    <Highlighted text={r.excerpt || ""} rx={rx} />
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-text-muted">
                      {r.open_timestamp ? fmtDateTime(r.open_timestamp) : "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
  );
}

// A name relaunched to the daily cap is somebody working at it, and the first
// of those launches is the one worth keeping — the rest are copies of it.
function OGSection() {
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const rx = useKeywordRegex();
  useTick(1000);

  const params = new URLSearchParams({ limit: "40" });
  if (query) params.set("q", query);
  if (date) params.set("date", date);

  const { data } = useApi<any>(`/api/ai/og?${params.toString()}`, { refreshInterval: 60000 });
  const { data: datesData } = useApi<any>("/api/ai/xdates?og=true", { refreshInterval: 300000 });
  const items: any[] = data?.items ?? [];

  return (
    <CollapsibleSection
      id="ai-og"
      title="OG Pump.Fun Tokens"
      icon={<Crown size={14} />}
      count={items.length}
      controls={<>
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
      </>}
    >
      <p className="mb-3 text-xs text-text-dim">
        The first launch of a name and ticker that then came back five times in
        the same IST day. Five is not a coincidence — it is somebody working at
        it — and the original is the one that ran before the copies.
      </p>
      <LaunchTable
        items={items}
        rx={rx}
        empty={query ? "Nothing matches this search"
          : date ? `No originals on ${date}`
          : "No name has hit five launches yet today"}
      />
    </CollapsibleSection>
  );
}

// Proves the X side works on its own, with the model out of the picture. The
// two halves fail for completely different reasons — no credits versus a dead
// Nitter instance — and a decisions table cannot tell them apart.
function XCheck() {
  // Rows arrive over the WebSocket, one per token, as the feed finds them —
  // Shell revalidates this key on an `x_link` event. The interval is only a
  // safety net for a dropped socket, so it can be slow.
  const [q, setQ] = useState("");
  const [followers, setFollowers] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const minFollowers = useDebounced(followers);

  const params = new URLSearchParams({ limit: "40" });
  if (query) params.set("q", query);
  // Only when a number is actually typed — an empty box is no filter, not zero.
  if (/^\d+$/.test(minFollowers)) params.set("min_followers", minFollowers);
  if (date) params.set("date", date);

  const { data, mutate: refetch, isValidating } =
    useApi<any>(`/api/ai/xcheck?${params.toString()}`, { refreshInterval: 60000 });
  const { data: datesData } = useApi<any>("/api/ai/xdates", { refreshInterval: 300000 });
  const rx = useKeywordRegex();
  useTick(1000);
  const items: any[] = data?.items ?? [];
  const emptyText = query || minFollowers ? "Nothing matches this filter"
    : date ? `No launches recorded on ${date}`
    : "Nothing recorded yet";

  return (
    <CollapsibleSection
      id="ai-xcheck"
      title="X Links — live check"
      icon={<Twitter size={14} />}
      count={items.length}
      controls={<>
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <Input
          value={followers}
          onChange={(e) => setFollowers(e.target.value.replace(/\D/g, ""))}
          placeholder="min followers"
          inputMode="numeric"
          className="h-8 w-32 text-xs"
        />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
        {/* A visible way to force a read. Polling covers the normal case, but a
            tab left open across a deploy runs the previous build's timers. */}
        <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isValidating}>
          <RefreshCw size={13} className={isValidating ? "animate-spin" : ""} /> Refresh
        </Button>
      </>}
    >
      {data?.error ? (
        <p className="text-xs text-accent-amber">{data.error}</p>
      ) : (
        <>
          <p className="mb-3 text-xs text-text-dim">
            New pump.fun launches whose X account is verified, pushed by
            PumpPortal as they happen. The link comes from the token's own
            metadata, so it arrives with the launch — a row is typically a second
            or two old. Any kind of tick counts; unverified accounts are not
            listed. The same name and ticker is listed at most five times an IST
            day, so a relaunch loop cannot fill the table.
            {data && (
              <> 
                <span className="text-text">{data.resolved}</span> accounts
                resolved, <span className="text-text">{data.verified}</span>{" "}
                verified, <span className="text-text">{data.posts}</span> with
                post text.</>
            )}
          </p>
          <LaunchTable items={items} rx={rx} empty={emptyText} />
        </>
      )}
    </CollapsibleSection>
  );
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
        subtitle="pump.fun tokens judged by the X account behind them"
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

      {/* Decisions first: it is the answer, and the two sections under it
          are the working that produced it — the burst originals, then the
          raw feed they came from. */}
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

      <OGSection />

      <XCheck />
    </div>
  );
}
