"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { Brain, CheckCircle2, Crown, Rocket, XCircle, ExternalLink, RefreshCw, Twitter } from "lucide-react";
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

// Verdicts, in the order they matter:
//   pending   — cleared every gate; this is the list the model will be given
//   matched   — a narrative, and the model could stand behind it being real
//   launching — the narrative fits but nothing confirms it yet
//   rejected  — the model read it and found no narrative
//   skipped   — never reached the model: a gate stopped it first
// The last two are deliberately browsable. Rejected and skipped answer
// different questions — is the model too strict, or are the gates? — and a
// filter you cannot audit is a filter you cannot trust.
const VERDICTS = [
  { id: "", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "matched", label: "Matched" },
  { id: "launching", label: "Launching" },
  { id: "rejected", label: "Rejected" },
  { id: "skipped", label: "Skipped" },
  // Not a verdict but a flag: a launch that was part of a link's burst AND
  // crossed the market cap bar in its first minute. It keeps whatever verdict
  // the model gave it, so it shows up here as well as in its own tab.
  { id: "telegram", label: "Telegram" },
] as const;

const TONE: Record<string, "green" | "purple" | "amber" | "gray" | "blue" | "red"> = {
  matched: "green", launching: "purple", rejected: "amber",
  skipped: "gray", pending: "blue", error: "red",
};

// Rows fetched at a time. The sections hold thousands, and rendering all of
// them at once would be a slow page for a list nobody reads past the top of —
// so they arrive a page at a time, until the table matches the count.
const PAGE = 200;

// Both launch sections are fed by the one PumpPortal socket, so they are on the
// one Settings switch — "X Links Feed". Neither has its own. The state is shown
// in both headers because a stopped feed otherwise reads as a quiet market.
function useFeedEnabled(): boolean | undefined {
  const { data } = useApi<any>("/api/settings/services", { refreshInterval: 30000 });
  const svc = (data?.bot ?? []).find((x: any) => x.id === "x_feed");
  return svc ? Boolean(svc.enabled) : undefined;
}

function FeedState({ enabled }: { enabled: boolean | undefined }) {
  if (enabled !== false) return null;
  return <Badge variant="amber">feed off — Settings → Bots → X Links Feed</Badge>;
}

// Ages were rendered once per fetch, so a row sat on "1m ago" for a whole minute
// and the section looked frozen between refreshes. A tick fixes that, but
// ticking the section itself re-renders every row every second — fine for forty
// rows, not for hundreds. So the clock lives in a context and only the age cells
// subscribe to it: one timer, and a second's work is a few dozen text nodes.
const TickContext = createContext(0);

function TickProvider({ children }: { children: React.ReactNode }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return <TickContext.Provider value={n}>{children}</TickContext.Provider>;
}

function Age({ ts }: { ts?: number }) {
  useContext(TickContext);
  return <>{ts ? ageLabel(ts) : "—"}</>;
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
function LaunchTable({ items, rx, empty, total, onMore }: {
  items: any[];
  rx: RegExp | null;
  empty: string;
  total?: number;
  onMore?: () => void;
}) {
  return (
    <>
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
                    <Age ts={r.open_timestamp} />
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
      {/* The count in the header is the whole filter; this is how the table
          catches up with it. */}
      {onMore && typeof total === "number" && items.length < total && (
        <div className="mt-3 flex items-center justify-center gap-3 text-xs">
          <span className="text-text-dim">
            showing {items.length.toLocaleString()} of {total.toLocaleString()}
          </span>
          <Button size="sm" variant="outline" onClick={onMore}>
            Load {Math.min(PAGE, total - items.length).toLocaleString()} more
          </Button>
        </div>
      )}
    </>
  );
}

// A name relaunched to the daily cap is somebody working at it, and the first
// of those launches is the one worth keeping — the rest are copies of it.
function OGSection() {
  const [limit, setLimit] = useState(PAGE);
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const rx = useKeywordRegex();
  const feedOn = useFeedEnabled();

  const params = new URLSearchParams({ limit: String(limit) });
  if (query) params.set("q", query);
  if (date) params.set("date", date);

  const { data } = useApi<any>(`/api/ai/og?${params.toString()}`, { refreshInterval: 60000 });
  useEffect(() => setLimit(PAGE), [query, date]);
  const { data: datesData } = useApi<any>("/api/ai/xdates?og=true", { refreshInterval: 300000 });
  const items: any[] = data?.items ?? [];

  return (
    <CollapsibleSection
      id="ai-og"
      title="OG Pump.Fun Tokens"
      icon={<Crown size={14} />}
      count={data?.total ?? items.length}
      controls={<>
        <FeedState enabled={feedOn} />
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
      </>}
    >
      <p className="mb-3 text-xs text-text-dim">
        The first launch of a name and ticker that came back five times inside
        five minutes, counting only launches that carry an X link. Five is not a
        coincidence — it is somebody working at it — and the original is the one
        that ran before the copies. Same feed and same switch as the live section
        below.
        {data && (
          <> <span className="text-text">{data.total}</span> stored, showing the
            newest <span className="text-text">{data.shown}</span>.</>
        )}
      </p>
      <LaunchTable
        items={items}
        rx={rx}
        total={data?.total}
        onMore={() => setLimit((n) => n + PAGE)}
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
  const [limit, setLimit] = useState(PAGE);
  const [q, setQ] = useState("");
  const [followers, setFollowers] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const minFollowers = useDebounced(followers);

  const params = new URLSearchParams({ limit: String(limit) });
  if (query) params.set("q", query);
  // Only when a number is actually typed — an empty box is no filter, not zero.
  if (/^\d+$/.test(minFollowers)) params.set("min_followers", minFollowers);
  if (date) params.set("date", date);

  const { data, mutate: refetch, isValidating } =
    useApi<any>(`/api/ai/xcheck?${params.toString()}`, { refreshInterval: 60000 });
  // A filter change puts us back at the first page — otherwise a narrow filter
  // would keep asking for a page size it can never fill.
  useEffect(() => setLimit(PAGE), [query, minFollowers, date]);
  const { data: datesData } = useApi<any>("/api/ai/xdates", { refreshInterval: 300000 });
  const rx = useKeywordRegex();
  const feedOn = useFeedEnabled();
  const items: any[] = data?.items ?? [];
  const emptyText = query || minFollowers ? "Nothing matches this filter"
    : date ? `No launches recorded on ${date}`
    : "Nothing recorded yet";

  return (
    <CollapsibleSection
      id="ai-xcheck"
      title="X Links — live check"
      icon={<Twitter size={14} />}
      count={data?.total ?? items.length}
      controls={<>
        <FeedState enabled={feedOn} />
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
                <span className="text-text">{data.total}</span> stored,{" "}
                showing the newest <span className="text-text">{data.shown}</span>{" "}
                — <span className="text-text">{data.verified}</span> verified,{" "}
                <span className="text-text">{data.posts}</span> with post text.</>
            )}
          </p>
          <LaunchTable items={items} rx={rx} empty={emptyText}
            total={data?.total} onMore={() => setLimit((n) => n + PAGE)} />
        </>
      )}
    </CollapsibleSection>
  );
}

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

      {/* Decisions first: it is the answer. Under it comes the working — the
          raw feed of verified launches, then the originals picked out of it. */}
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
          why. Before the model: one X link must carry five launches inside five
          minutes, and of those five only the first is asked about. The account
          must also be verified and have followers, and a name and ticker is
          asked about once per IST day. <b>Telegram</b> holds the launches from
          those bursts that crossed the market cap bar in their first minute —
          those are the ones sent to the chat.
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
                <th className="px-3 py-2.5 font-medium">CA</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={9} className="px-3 py-10 text-center text-text-dim">
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
                  </td>
                  <td className="px-3 py-3">
                    <div className="font-semibold text-text">{d.symbol || "?"}</div>
                    <div className="text-xs text-text-dim">{d.name}</div>
                  </td>
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

      <OGSection />
    </div>
    </TickProvider>
  );
}
