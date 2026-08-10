"use client";

import { useState } from "react";
import { ExternalLink, Globe, Rocket, RefreshCw, Trash2, Twitter } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { FilterTabs, HistorySelect, SearchBox } from "@/components/SectionFilters";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge, Variant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Age } from "@/components/Age";
import { fmtDateTime, fmtNum, shortAddr, rowKey } from "@/lib/utils";

/* Robinhood Launchpad Monitor.
 *
 * Every launch from a watched launchpad, where the X monitor above shows only
 * the ones with an account behind them. Same columns as that panel on purpose
 * — it is the same underlying row, written by the same worker in the same pass
 * — plus a Launchpad column, and the account columns simply run empty where a
 * launch carries no profile. */

// One colour per launchpad, so the merged view reads at a glance. Theme tokens,
// not hex: both themes stay legible without a second palette.
const PAD_TONE: Record<string, Variant> = {
  pons: "purple", flap: "cyan", pools: "green",
};

type Pad = { id: string; label: string; factories: number; enabled: boolean };

export function LaunchpadSection() {
  const [pad, setPad] = useState("all");
  const [q, setQ] = useState("");
  const [minF, setMinF] = useState("");
  const [withX, setWithX] = useState(false);
  const [date, setDate] = useState("");
  const query = useDebounced(q);

  // The tabs come from the backend, not a list typed in here: a launchpad
  // whose address is set in .env appears on its own, and one that is not
  // configured does not offer a tab that can only ever be empty.
  const { data: padData } = useApi<any>("/api/launchpad/pads");
  const pads: Pad[] = padData?.items ?? [];
  // A launchpad switched off in Settings keeps its tab and its history — it
  // just takes no new launches, and the tab says so rather than looking broken.
  const tabs = [{ id: "all", label: "All" },
                ...pads.map((p) => ({ id: p.id,
                                      label: p.enabled ? p.label : `${p.label} (off)` }))];
  const offPads = pads.filter((p) => !p.enabled);

  const params = new URLSearchParams();
  if (pad !== "all") params.set("pad", pad);
  if (query) params.set("q", query);
  if (date) params.set("date", date);
  if (withX) params.set("with_x", "true");
  // Blank means no floor, not zero — typing "0" should not look like a filter.
  const floor = parseInt(minF, 10);
  if (Number.isFinite(floor) && floor > 0) params.set("min_followers", String(floor));
  const qs = params.toString();

  const key = `/api/launchpad/tokens${qs ? `?${qs}` : ""}`;
  const { data } = useApi<any>(key);
  const { data: datesData } = useApi<any>(`/api/launchpad/dates?pad=${pad}`);
  const { data: stats } = useApi<any>("/api/launchpad/stats");
  const items = data?.items ?? [];

  return (
    <CollapsibleSection
      id="launchpad"
      title="Robinhood Launchpad Monitor"
      icon={<Rocket size={14} />}
      count={data?.total ?? 0}
      controls={<>
        <FilterTabs value={pad} onChange={setPad} options={tabs} />
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <Input value={minF} onChange={(e) => setMinF(e.target.value.replace(/\D/g, ""))}
               placeholder="min followers" className="h-8 w-32 text-xs" />
        <Button size="sm" variant={withX ? "primary" : "outline"}
                onClick={() => setWithX((v) => !v)}
                title="Only launches that carry an X account">
          <Twitter size={13} /> With X
        </Button>
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
        <Button size="sm" variant="outline" onClick={() => mutate(key)} title="Refresh now">
          <RefreshCw size={13} />
        </Button>
      </>}
    >
      <p className="mb-3 text-xs text-text-dim">
        Every launch from a watched launchpad, caught on its own mint event —
        seconds after the token is created, not when it graduates to a pool.
        Each launchpad is read its own way, because each keeps its socials
        somewhere different. Kept {stats?.retention_days ?? 15} days.
        {stats?.dev_buy_max_eth ? (
          <> {" "}A launch whose deployer buys more than{" "}
            <b>{stats.dev_buy_max_eth} Ξ</b> of it is not recorded at all.</>
        ) : null}
        {offPads.length > 0 && (
          <> {" "}<span className="text-accent-amber">
            {offPads.map((p) => p.label).join(" and ")} switched off in Settings —
            no new launches from {offPads.length > 1 ? "them" : "it"}.
          </span></>
        )}
        {stats?.with_x != null && stats?.total ? (
          <> {" "}<span className="text-text-muted">
            {fmtNum(stats.with_x)} of {fmtNum(stats.total)} carry an X account.
          </span></>
        ) : null}
      </p>

      <TableScroll>
        <table className="w-full min-w-[1000px] text-sm">
          <thead>
            <tr className={`${STICKY_HEAD} border-b border-border`}>
              <th className="px-3 py-2.5 font-medium">Launchpad</th>
              <th className="px-3 py-2.5 font-medium">Token</th>
              <th className="px-3 py-2.5 font-medium">Age</th>
              <th className="px-3 py-2.5 font-medium">Account</th>
              <th className="px-3 py-2.5 font-medium">Followers</th>
              <th className="px-3 py-2.5 font-medium">Dev Buy</th>
              <th className="px-3 py-2.5 font-medium" title="The X account's bio">Text</th>
              <th className="px-3 py-2.5 font-medium">Timestamp</th>
              <th className="px-3 py-2.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={9} className="px-3 py-10 text-center text-text-dim">
                {query ? "Nothing matches this search"
                  : date ? `No launches on ${date}`
                  : withX ? "No launch here carries an X account"
                  : floor > 0 ? `No account above ${fmtNum(floor)} followers`
                  : pads.length === 0 ? "No launchpad configured — set PONS_FACTORIES / FLAP_PORTALS"
                  : "No launches caught yet"}
              </td></tr>
            ) : items.map((r: any, i: number) => (
              <tr key={rowKey(r, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                <td className="px-3 py-3">
                  <Badge variant={PAD_TONE[r.launchpad] || "gray"}>
                    {r.launchpad_label || r.launchpad || "?"}
                  </Badge>
                </td>
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
                    {r.website && (
                      <a href={r.website} target="_blank" rel="noopener noreferrer" title={r.website}
                         className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                        <Globe size={12} />
                      </a>
                    )}
                  </div>
                  <span className="font-mono text-[10px] text-text-dim">{shortAddr(r.address)}</span>
                </td>
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted"><Age ts={r.open_timestamp} /></span>
                </td>
                {/* Empty for a launch that carries no account — most of them do
                    not, and that is information rather than a gap. */}
                <td className="px-3 py-3">
                  {r.handle ? (
                    <>
                      <a href={r.link} target="_blank" rel="noopener noreferrer"
                         className="text-xs text-accent-blue hover:underline">@{r.handle}</a>
                      {r.verified && <span title={r.verified_type || "verified"}> ✅</span>}
                      {r.proved && <span title="Ownership proved to the launchpad at launch"> 🔒</span>}
                      {/* The handle came out of a link to one of that account's
                          posts, not a link to the account. Weaker, so it is
                          marked here and kept out of the X Monitor entirely. */}
                      {r.handle_source === "post" && (
                        <span className="ml-1 text-[9px] text-text-dim"
                              title="From a link to one of this account's posts, not to the account itself">
                          post
                        </span>
                      )}
                    </>
                  ) : <span className="text-xs text-text-dim">—</span>}
                </td>
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text">
                    {r.handle ? fmtNum(r.followers) : "—"}
                  </span>
                </td>
                <td className="px-3 py-3">
                  {r.dev_buy_eth == null ? (
                    <span className="text-xs text-text-dim">—</span>
                  ) : (
                    <span className={`font-mono text-xs ${r.dev_buy_eth > 0 ? "text-accent-amber" : "text-text-dim"}`}
                          title={r.dev_wallet || ""}>
                      {r.dev_buy_eth.toFixed(3)} Ξ
                    </span>
                  )}
                </td>
                <td className="px-3 py-3">
                  <span className="block max-w-[300px] truncate text-xs text-text-muted"
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
                      await apiSend(`/api/launchpad/tokens/${r.address}`, "DELETE");
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
