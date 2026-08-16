"use client";

import { useState } from "react";
import { ExternalLink, Eye, RefreshCw, Trash2, Twitter, UserMinus } from "lucide-react";
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
import { HandleList } from "@/components/HandleList";
import { AdminOnly } from "@/components/AdminOnly";
import { fmtDateTime, fmtNum, shortAddr, rowKey } from "@/lib/utils";

/* Robinhood — X — Token Monitor.
 *
 * Same columns and the same Age/Timestamp pair as AI Narrative's X Links, on
 * purpose: it answers the same question on a different chain, and two panels
 * that read differently for the same thing is a tax on whoever reads both. */

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
        <AdminOnly>
          <Button size="sm" variant={lists ? "primary" : "outline"}
                  onClick={() => setLists((v) => !v)} title="Skip and watch lists">
            <Eye size={13} /> Lists
          </Button>
        </AdminOnly>
      </>}
    >
      <p className="mb-3 text-xs text-text-dim">
        A Robinhood launchpad token carries its socials in the contract's own
        metadata. Only <b>@username</b> links are kept — a link to one post says
        nothing about who is behind the launch. Kept {stats?.retention_days ?? 15} days.
        {stats?.dev_buy_max_eth ? (
          <> {" "}A launch whose own deployer buys more than{" "}
            <b>{stats.dev_buy_max_eth} Ξ</b> of it inside {stats.dev_buy_window}s is
            dropped — the Dev Buy column fills in over that window.</>
        ) : null}
        {stats && !stats.own_endpoints && (
          <> {" "}<span className="text-accent-amber">
            Running on the Robinhood Chain endpoints — set its own in RPC Monitor.
          </span></>
        )}
      </p>

      {lists && (
        <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <HandleList base="/api/rbhx" kind="skip" title="Skip list" icon={<UserMinus size={12} />}
            hint="New tokens from these accounts are not recorded. Rows already on the page stay." />
          <HandleList base="/api/rbhx" kind="watch" title="Watch list" icon={<Eye size={12} />}
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
              <th className="px-3 py-2.5 font-medium">Dev Buy</th>
              <th className="px-3 py-2.5 font-medium">Text</th>
              <th className="px-3 py-2.5 font-medium">Timestamp</th>
              <th className="px-3 py-2.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-10 text-center text-text-dim">
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
                {/* Fills in over the watch window. A launch whose deployer
                    goes past the limit is taken off the page entirely, so
                    anything shown here was under it. */}
                <td className="px-3 py-3">
                  {r.dev_buy_eth == null ? (
                    <span className="text-xs text-text-dim" title="No signed proof on this launch, so its deployer is unknown">—</span>
                  ) : (
                    <span className={`font-mono text-xs ${r.dev_buy_eth > 0 ? "text-accent-amber" : "text-text-dim"}`}
                          title={r.dev_wallet || ""}>
                      {r.dev_buy_eth.toFixed(3)} Ξ
                    </span>
                  )}
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
                  <AdminOnly>
                    <button
                      title="Remove this row"
                      onClick={async () => {
                        await apiSend(`/api/rbhx/tokens/${r.address}`, "DELETE");
                        mutate(key);
                      }}
                      className="grid h-6 w-6 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-accent-red">
                      <Trash2 size={12} />
                    </button>
                  </AdminOnly>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </CollapsibleSection>
  );
}
