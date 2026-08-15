"use client";

import { useState } from "react";
import { ExternalLink, Eye, Globe, Rocket, RefreshCw, Trash2, Twitter, UserMinus } from "lucide-react";
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
import { HandleList } from "@/components/HandleList";
import { NoteButton, SectionNote, useSectionNote } from "@/components/SectionNote";
import { cn, fmtDateTime, fmtNum, shortAddr, rowKey } from "@/lib/utils";

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
  // Pons and Pons V2 are two deployments of one launchpad, so they are two
  // shades of the same idea rather than two unrelated colours.
  pons: "purple", pons_v2: "blue", flap: "cyan", pools: "green",
  virtuals: "amber", letscash: "red",
};

type Pad = { id: string; label: string; factories: number; enabled: boolean };

/* The account's bio, with two things picked out of it.
 *
 * The token's own address, when the account has it in their bio — an account
 * carrying the contract has said the token is theirs. And any of the keywords
 * from Settings, whole-word, which is the same rule and the same list the
 * Telegram alert uses.
 *
 * Drawn rather than stored: it costs nothing here, it applies to every row
 * already on the page, and a keyword added in Settings lights up the rows that
 * were caught before it existed.
 *
 * The address is matched case-insensitively with or without the 0x, because
 * bios carry both. A shortened "0x5fb2…95fa5" is deliberately not matched — it
 * is not the address, and guessing at one is how the wrong token lights up. */
function escapeRe(v: string) {
  return v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function BioText({ text, address, keywords }: {
  text: string; address: string; keywords: string[];
}) {
  const bare = (address || "").toLowerCase().replace(/^0x/, "");
  const alts: string[] = [];
  if (/^[0-9a-f]{40}$/.test(bare)) alts.push(`(?:0x)?${bare}(?![0-9a-f])`);
  // Longest first, so "Buybacks" wins over "Buyback" on the same word.
  const words = [...keywords].filter(Boolean).sort((a, b) => b.length - a.length);
  if (words.length) alts.push(`\\b(?:${words.map(escapeRe).join("|")})\\b`);
  if (!alts.length) return <>{text}</>;

  const re = new RegExp(alts.join("|"), "gi");
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    const isAddress = m[0].toLowerCase().replace(/^0x/, "") === bare;
    parts.push(
      <span key={at}
            title={isAddress ? "This account's bio names this token's address"
                             : "Matches a keyword from Settings"}
            className={isAddress
              ? "rounded bg-accent-green/15 px-0.5 font-medium text-accent-green"
              : "rounded bg-accent-amber/15 px-0.5 font-medium text-accent-amber"}>
        {m[0]}
      </span>
    );
    last = at + m[0].length;
  }
  if (!parts.length) return <>{text}</>;
  parts.push(text.slice(last));
  return <>{parts}</>;
}

export function LaunchpadSection() {
  const [pad, setPad] = useState("all");
  const [q, setQ] = useState("");
  const [minF, setMinF] = useState("");
  const [withX, setWithX] = useState(false);
  const [date, setDate] = useState("");
  const [lists, setLists] = useState(false);
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
  // The same list the worker matches on, so the column and the alert agree.
  const { data: kwData } = useApi<any>("/api/launchpad/keywords");
  const keywords: string[] = kwData?.items ?? [];
  const items = data?.items ?? [];
  // The line above which a deployer is putting real money into their own
  // launch. Comes from the backend rather than being typed here, so the
  // highlighted rows and the 🟢 Telegram alerts are the same set — 0 while the
  // figure is still loading, which marks nothing.
  const strongAt: number = stats?.dev_buy_strong_eth ?? 0;
  const note = useSectionNote("launchpad");

  return (
    <CollapsibleSection
      id="launchpad"
      // Shortened from "Robinhood Launchpad Monitor": with six filter tabs, a
      // search box, min followers, With X, History, refresh and Lists on the
      // same row, the full name pushed the controls off the end.
      title="RBH Launchpad Monitor"
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
        <Button size="sm" variant={lists ? "primary" : "outline"}
                onClick={() => setLists((v) => !v)} title="Skip and watch lists">
          <Eye size={13} /> Lists
        </Button>
        <NoteButton {...note} />
      </>}
    >
      {/* Stays out of the note: a launchpad being off is not a description of
          the section, it is the reason rows have stopped arriving from one —
          and it only appears when something actually is off. */}
      {offPads.length > 0 && (
        <p className="mb-3 text-xs text-accent-amber">
          {offPads.map((p) => p.label).join(" and ")} switched off in Settings —
          no new launches from {offPads.length > 1 ? "them" : "it"}.
        </p>
      )}

      <SectionNote open={note.open}>
        Every launch from a watched launchpad, caught on its own mint event —
        seconds after the token is created, not when it graduates to a pool.
        Each launchpad is read its own way, because each keeps its socials
        somewhere different. Kept {stats?.retention_days ?? 15} days.
        {stats?.dev_buy_max_eth ? (
          <> {" "}A launch whose deployer buys more than{" "}
            <b>{stats.dev_buy_max_eth} Ξ</b> of it is not recorded at all.</>
        ) : null}
        {strongAt > 0 ? (
          <> {" "}<span className="text-accent-green">
            A dev buy over <b>{strongAt} Ξ</b> is a Strong Signal — the row is
            marked here and the alert says so.
          </span></>
        ) : null}
        {stats?.with_x != null && stats?.total ? (
          <> {" "}<span className="text-text-muted">
            {fmtNum(stats.with_x)} of {fmtNum(stats.total)} carry an X account.
          </span></>
        ) : null}
      </SectionNote>

      {/* This panel's own two lists — the same widget the X Monitor uses, over
          its own entries. Adding an account here does not touch that one. */}
      {lists && (
        <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <HandleList base="/api/launchpad" kind="skip" title="Skip list"
            icon={<UserMinus size={12} />}
            hint="New launches from these accounts are not recorded. Rows already on the page stay." />
          <HandleList base="/api/launchpad" kind="watch" title="Watch list"
            icon={<Eye size={12} />}
            hint="Launches from these accounts are flagged 👁 here and in the Telegram alert." />
        </div>
      )}

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
            ) : items.map((r: any, i: number) => {
              const strong = strongAt > 0 && r.dev_buy_eth > strongAt;
              return (
              <tr key={rowKey(r, i)}
                  className={cn(
                    "border-b border-border-soft align-top hover:bg-bg-hover/40",
                    // A tint plus a rail down the left edge: the tint alone is
                    // easy to miss when every other row is striped by hover,
                    // and the rail survives at a glance on a long page.
                    strong && "bg-accent-green/10 shadow-[inset_3px_0_0_0_rgb(var(--accent-green))] hover:bg-accent-green/[0.15]",
                  )}>
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
                    <span className={cn("font-mono text-xs",
                                        strong ? "font-semibold text-accent-green"
                                          : r.dev_buy_eth > 0 ? "text-accent-amber"
                                          : "text-text-dim")}
                          title={strong
                            ? `Strong Signal — over ${strongAt} Ξ${r.dev_wallet ? `\n${r.dev_wallet}` : ""}`
                            : r.dev_wallet || ""}>
                      {strong && "🟢 "}{r.dev_buy_eth.toFixed(3)} Ξ
                    </span>
                  )}
                </td>
                {/* Wrapped rather than cut off at one line: the bio is stored
                    capped at 200 characters, so it costs about four lines at
                    this width and nothing is hidden. No title either — a
                    tooltip that repeats what is already on screen is just a
                    box in the way. break-words is for the links bios carry,
                    which would otherwise widen the column on their own. */}
                <td className="px-3 py-3">
                  <span className="block max-w-[300px] whitespace-normal break-words text-xs text-text-muted">
                    {r.excerpt
                      ? <BioText text={r.excerpt} address={r.address} keywords={keywords} />
                      : "—"}
                  </span>
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
              );
            })}
          </tbody>
        </table>
      </TableScroll>
    </CollapsibleSection>
  );
}
