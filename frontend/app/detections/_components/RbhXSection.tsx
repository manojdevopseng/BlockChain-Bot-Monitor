"use client";

import { useState } from "react";
import { ExternalLink, Eye, RefreshCw, Trash2, Twitter, UserMinus, X } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { HistorySelect, SearchBox } from "@/components/SectionFilters";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Age } from "@/components/Age";
import { fmtDateTime, fmtNum, shortAddr, rowKey } from "@/lib/utils";

/* Robinhood — X — Token Monitor.
 *
 * Same columns and the same Age/Timestamp pair as AI Narrative's X Links, on
 * purpose: it answers the same question on a different chain, and two panels
 * that read differently for the same thing is a tax on whoever reads both. */

type Entry = { handle: string; note: string; added_at: number; expires_in_days: number };

/** Skip and Watch are the same widget twice — one list of @usernames each,
 *  add and remove, with what each list does written on it. */
function HandleList({ kind, title, hint, icon }: {
  kind: "skip" | "watch";
  title: string;
  hint: string;
  icon: React.ReactNode;
}) {
  const path = `/api/rbhx/${kind}`;
  const { data } = useApi<any>(path);
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const items: Entry[] = data?.items ?? [];

  async function add() {
    const handle = val.trim();
    if (!handle) return;
    setBusy(true); setErr("");
    try {
      await apiSend(path, "POST", { handle });
      setVal("");
      mutate(path);
    } catch (e: any) {
      setErr(e?.message || "could not add that username");
    } finally {
      setBusy(false);
    }
  }

  async function remove(handle: string) {
    await apiSend(`${path}/${handle}`, "DELETE");
    mutate(path);
  }

  return (
    <div className="rounded-lg border border-border-soft p-3">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-text">
        {icon} {title}
        <Badge variant="gray">{items.length}</Badge>
      </div>
      <p className="mb-2 text-[11px] text-text-dim">{hint}</p>
      <div className="mb-2 flex gap-1.5">
        <Input value={val} onChange={(e) => setVal(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && add()}
               placeholder="@username" className="h-7 text-xs" />
        <Button size="sm" onClick={add} disabled={busy}>Add</Button>
      </div>
      {err && <div className="mb-2 text-[11px] text-accent-red">{err}</div>}
      <div className="flex flex-wrap gap-1">
        {items.map((e) => (
          <span key={e.handle}
                title={`expires in ${e.expires_in_days} days unless removed sooner`}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-white/5 px-2 py-0.5 text-[11px] text-text-muted">
            @{e.handle}
            <span className="text-text-dim">{e.expires_in_days}d</span>
            <button onClick={() => remove(e.handle)} className="hover:text-accent-red">
              <X size={10} />
            </button>
          </span>
        ))}
        {items.length === 0 && <span className="text-[11px] text-text-dim">empty</span>}
      </div>
    </div>
  );
}

export function RbhXSection() {
  const [q, setQ] = useState("");
  const [minF, setMinF] = useState("");
  const [date, setDate] = useState("");
  const [lists, setLists] = useState(false);
  const query = useDebounced(q);

  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (date) params.set("date", date);
  // Blank means no floor, not zero — the two read the same to the API, but
  // typing "0" should not look like an active filter either.
  const floor = parseInt(minF, 10);
  if (Number.isFinite(floor) && floor > 0) params.set("min_followers", String(floor));
  const qs = params.toString();

  const key = `/api/rbhx/tokens${qs ? `?${qs}` : ""}`;
  const { data } = useApi<any>(key);
  const { data: datesData } = useApi<any>("/api/rbhx/dates");
  const { data: stats } = useApi<any>("/api/rbhx/stats");
  const items = data?.items ?? [];

  return (
    <CollapsibleSection
      id="rbhx"
      title="Robinhood — X — Token Monitor"
      icon={<Twitter size={14} />}
      count={data?.total ?? 0}
      controls={<>
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <Input value={minF} onChange={(e) => setMinF(e.target.value.replace(/\D/g, ""))}
               placeholder="min followers" className="h-8 w-32 text-xs" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
        <Button size="sm" variant="outline" onClick={() => mutate(key)} title="Refresh now">
          <RefreshCw size={13} />
        </Button>
        <Button size="sm" variant={lists ? "primary" : "outline"}
                onClick={() => setLists((v) => !v)} title="Skip and watch lists">
          <Eye size={13} /> Lists
        </Button>
      </>}
    >
      <p className="mb-3 text-xs text-text-dim">
        A Robinhood launchpad token carries its socials in the contract's own
        metadata. Only <b>@username</b> links are kept — a link to one post says
        nothing about who is behind the launch. Kept {stats?.retention_days ?? 15} days.
        {stats && !stats.own_endpoints && (
          <> {" "}<span className="text-accent-amber">
            Running on the Robinhood Chain endpoints — set its own in RPC Monitor.
          </span></>
        )}
      </p>

      {lists && (
        <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <HandleList kind="skip" title="Skip list" icon={<UserMinus size={12} />}
            hint="New tokens from these accounts are not recorded. Rows already on the page stay." />
          <HandleList kind="watch" title="Watch list" icon={<Eye size={12} />}
            hint="Tokens from these accounts are flagged 👁 here and in the Telegram alert." />
        </div>
      )}

      <TableScroll>
        <table className="w-full min-w-[900px] text-sm">
          <thead>
            <tr className={`${STICKY_HEAD} border-b border-border`}>
              <th className="px-3 py-2.5 font-medium">Token</th>
              <th className="px-3 py-2.5 font-medium">Age</th>
              <th className="px-3 py-2.5 font-medium">Account</th>
              <th className="px-3 py-2.5 font-medium">Followers</th>
              <th className="px-3 py-2.5 font-medium">Text</th>
              <th className="px-3 py-2.5 font-medium">Timestamp</th>
              <th className="px-3 py-2.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={7} className="px-3 py-10 text-center text-text-dim">
                {query ? "Nothing matches this search"
                  : date ? `No tokens on ${date}`
                  : floor > 0 ? `No account above ${fmtNum(floor)} followers`
                  : "No tokens caught yet"}
              </td></tr>
            ) : items.map((r: any, i: number) => (
              <tr key={rowKey(r, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    {r.watched && <span title="Watched account">👁</span>}
                    <span className="font-semibold text-text">{r.symbol || "?"}</span>
                    <CopyButton value={r.address} />
                    {r.gmgn_url && (
                      <a href={r.gmgn_url} target="_blank" rel="noopener noreferrer" title="View on GMGN"
                         className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                  <span className="font-mono text-[10px] text-text-dim">{shortAddr(r.address)}</span>
                </td>
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted"><Age ts={r.open_timestamp} /></span>
                </td>
                <td className="px-3 py-3">
                  <a href={r.link} target="_blank" rel="noopener noreferrer"
                     className="text-xs text-accent-blue hover:underline">@{r.handle}</a>
                  {r.verified && <span title={r.verified_type || "verified"}> ✅</span>}
                  {/* Not the same claim as the tick: this is the launchpad
                      having watched the deployer sign in to that account. */}
                  {r.proved && <span title="Ownership proved to the launchpad at launch"> 🔒</span>}
                </td>
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text">{fmtNum(r.followers)}</span>
                </td>
                <td className="px-3 py-3">
                  <span className="block max-w-[320px] truncate text-xs text-text-muted"
                        title={r.excerpt}>{r.excerpt || "—"}</span>
                </td>
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted">
                    {r.open_timestamp ? fmtDateTime(r.open_timestamp) : "—"}
                  </span>
                </td>
                <td className="px-3 py-3">
                  <button
                    title="Remove this row"
                    onClick={async () => {
                      await apiSend(`/api/rbhx/tokens/${r.address}`, "DELETE");
                      mutate(key);
                    }}
                    className="grid h-6 w-6 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-accent-red">
                    <Trash2 size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </CollapsibleSection>
  );
}
