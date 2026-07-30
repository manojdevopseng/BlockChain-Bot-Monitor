"use client";

/** Pieces the AI page's sections share.
 *
 * Private to app/ai — the underscore keeps Next.js from treating this folder as
 * a route. These were all inline in page.tsx when it reached 797 lines, which
 * made the actual page layout hard to find among them.
 */

import { createContext, useContext, useEffect, useState } from "react";
import { Twitter } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { CopyButton } from "@/components/CopyButton";
import { shortAddr } from "@/lib/utils";


// Verdicts, in the order they matter:
//   pending   — cleared every gate; this is the list the model will be given
//   matched   — the model found one of the narratives in the post
//   launching — the link points at an account, not a post. These stay here
//               while the account is watched for a contract address; the ones
//               that have published this token's address are marked inside the
//               tab rather than moved out of it.
//   rejected  — the model read it and found no narrative
//   skipped   — never reached the model: a gate stopped it first
// The last two are deliberately browsable. Rejected and skipped answer
// different questions — is the model too strict, or are the gates? — and a
// filter you cannot audit is a filter you cannot trust.
export const VERDICTS = [
  { id: "", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "matched", label: "Matched" },
  { id: "launching", label: "Launching" },
  { id: "rejected", label: "Rejected" },
  { id: "skipped", label: "Skipped" },
  // Not a verdict but a flag: a launch from a link's burst of five that also
  // crossed the market cap bar in its first minute. It keeps whatever verdict
  // the model gave it, so it shows up here as well as in its own tab.
  { id: "telegram", label: "Telegram" },
] as const;

export const TONE: Record<string, "green" | "purple" | "amber" | "gray" | "blue" | "red"> = {
  matched: "green", launching: "purple", rejected: "amber",
  skipped: "gray", pending: "blue", error: "red",
};

// Rows fetched at a time. The sections hold thousands, and rendering all of
// them at once would be a slow page for a list nobody reads past the top of —
// so they arrive a page at a time, until the table matches the count.
export const PAGE = 200;

// Both launch sections are fed by the one PumpPortal socket, so they are on the
// one Settings switch — "X Links Feed". Neither has its own. The state is shown
// in both headers because a stopped feed otherwise reads as a quiet market.
export function useFeedEnabled(): boolean | undefined {
  const { data } = useApi<any>("/api/settings/services", { refreshInterval: 30000 });
  const svc = (data?.bot ?? []).find((x: any) => x.id === "x_feed");
  return svc ? Boolean(svc.enabled) : undefined;
}

export function FeedState({ enabled }: { enabled: boolean | undefined }) {
  if (enabled !== false) return null;
  return <Badge variant="amber">feed off — Settings → Bots → X Links Feed</Badge>;
}

// Ages were rendered once per fetch, so a row sat on "1m ago" for a whole minute
// and the section looked frozen between refreshes. A tick fixes that, but
// ticking the section itself re-renders every row every second — fine for forty
// rows, not for hundreds. So the clock lives in a context and only the age cells
// subscribe to it: one timer, and a second's work is a few dozen text nodes.
const TickContext = createContext(0);

export function TickProvider({ children }: { children: React.ReactNode }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return <TickContext.Provider value={n}>{children}</TickContext.Provider>;
}

export function Age({ ts }: { ts?: number }) {
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
export function useKeywordRegex(): RegExp | null {
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

// The token, the way both sections show it: the ticker opens GMGN, the address
// is there to copy, and the short form sits underneath. Written once because
// Decisions and the live check are read together and a difference between them
// reads as a difference in the data.
export function TokenCell({ address, symbol, name }: {
  address: string; symbol?: string; name?: string;
}) {
  return (
    <td className="px-3 py-3">
      <span className="flex items-center gap-1.5">
        <a href={`https://gmgn.ai/sol/token/${address}`}
           target="_blank" rel="noopener noreferrer"
           title="View on GMGN"
           className="font-semibold text-brand-soft hover:underline">
          {symbol || "?"}
        </a>
        <CopyButton value={address} />
      </span>
      {name ? <div className="text-xs text-text-dim">{name}</div> : null}
      <span className="font-mono text-[11px] text-text-dim">
        {shortAddr(address)}
      </span>
    </td>
  );
}

// An X link, shown as what it points at rather than as a URL. A post and a
// profile are different things to click on, and the label says which.
export function XLink({ link, handle, kind }: {
  link?: string; handle?: string; kind?: string;
}) {
  if (!link) return <span className="text-text-dim">—</span>;
  const label = handle ? `@${handle}` : "open";
  return (
    <a href={link} target="_blank" rel="noopener noreferrer" title={link}
       className="inline-flex items-center gap-1 text-accent-blue hover:underline">
      <Twitter size={11} className="shrink-0" />
      <span className="truncate">{label}</span>
      {kind ? <span className="text-[10px] text-text-dim">{kind}</span> : null}
    </a>
  );
}

export function Highlighted({ text, rx }: { text: string; rx: RegExp | null }) {
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

export function Stat({ label, value, sub, strong }: {
  label: string; value?: number | null; sub?: string; strong?: boolean;
}) {
  return (
    <div>
      <div className="text-xs text-text-dim">{label}</div>
      <div className={`font-mono ${strong ? "text-lg text-accent-green" : "text-base text-text"}`}>
        {value ? `$${Math.round(Number(value)).toLocaleString()}` : "—"}
      </div>
      {sub ? <div className="text-[11px] text-text-dim">{sub}</div> : null}
    </div>
  );
}
