"use client";

import Link from "next/link";
import { Activity, ArrowRight, Brain, Crosshair, Fuel, Radio, Target } from "lucide-react";
import { useApi } from "@/lib/api";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* The front page.
 *
 * Six things across four chains, in the order they happen to a trader: money
 * moving before the news (gas), who is calling what and whether their calls
 * worked, the launch itself and who is behind it, what narrative it belongs to,
 * what it is worth, and when it turns.
 *
 * It says the honest parts too. A landing page that promises returns is one
 * that gets refund requests for a market it does not control. */

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-xl border border-border-soft bg-bg-card/40 px-4 py-3">
      <p className="text-lg font-semibold text-text">{value}</p>
      <p className="text-[11px] leading-snug text-text-dim">{label}</p>
    </div>
  );
}

const PILLARS = [
  {
    icon: Fuel, n: "01", title: "Gas tells you before the chart does",
    body: "Every new pair on Ethereum is watched, and every buy's gas fee is read off its own receipt. Somebody paying far over the odds to be first in is not guessing — that is the buy worth knowing about, and it happens before there is anything to see on a chart.",
    detail: "gasUsed × effectiveGasPrice, per buy, in the first minutes of a pair.",
  },
  {
    icon: Radio, n: "02", title: "Every call, on four chains, and whether it worked",
    body: "Calls from the groups worth reading — Ethereum, Robinhood, BNB Chain and Solana — collected in one place, deduplicated, and verified on chain before they count. Then the part nobody publishes: what each call actually did at 15 minutes, an hour, six hours and a day.",
    detail: "The same address called by four groups inside an hour is a different signal from one group shouting.",
  },
  {
    icon: Crosshair, n: "03", title: "Robinhood launches, read off the contract",
    body: "All six launchpads. Within seconds of the mint: the X account named in the token's own metadata, that account's bio and following, whether the bio quotes the contract address, your keywords matched against it whole-word, and how much of its own supply the deployer bought.",
    detail: "Watch and skip lists, so the accounts you care about find you rather than the other way round.",
  },
  {
    icon: Brain, n: "04", title: "What narrative it belongs to",
    body: "The X account behind a launch is read, checked for verification, and judged against the narratives you are watching — so a wave of the same idea under twenty different tickers reads as one wave rather than twenty tickers.",
    detail: "A verdict with a confidence, and the post it was based on. Not a score out of ten with no reasoning.",
  },
  {
    icon: Target, n: "05", title: "What it is worth, right now",
    body: "Supply × price, straight off the chain, on RBH, ETH, BSC and Solana — V2, V3 and V4 pools, including the hooked V4 pools Robinhood's launchpads mint into that most tools cannot read at all. Paste an address for one reading, or set a number and be told once when it gets there.",
    detail: "Total supply, not a guess at circulating. The page says so rather than flattering the figure.",
  },
  {
    icon: Activity, n: "06", title: "And when it turns",
    body: "Wilder's RSI on the tokens you hold, on your timeframe and your candle count, alerting on a crossing rather than every fifteen seconds while it sits in the zone.",
    detail: "1 Sec to 1 Day. The faster ones cost more because they are more requests, and the pricing says that out loud.",
  },
];

export default function HomePage() {
  const { data } = useApi<any>("/api/public/stats", { refreshInterval: 0 });
  const pads: string[] = data?.launchpads ?? [];
  const n = (v: any) => (v == null ? null : Number(v).toLocaleString());

  return (
    <SiteChrome>
      <section className="max-w-3xl">
        <p className="mb-3 text-[11px] uppercase tracking-wide text-brand-soft">
          Ethereum · Robinhood Chain · BNB Chain · Solana
        </p>
        <h1 className="text-3xl font-semibold leading-tight text-text sm:text-4xl">
          Six ways to see it early
          <span className="block text-brand-soft">on one screen</span>
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-text-muted">
          Who is paying real money to get in first, who is calling what and
          whether their calls ever worked, every Robinhood launch and the account
          behind it, the narrative it belongs to, what it is worth, and when it
          turns. Four chains, one place, no tab-switching.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link href="/register"
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
            Start 7 days free <ArrowRight size={14} />
          </Link>
          <Link href="/how-to-use"
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text">
            See how it works
          </Link>
        </div>
        <p className="mt-3 text-[11px] text-text-dim">
          No card. The trial starts when you confirm your email, and stops by
          itself.
        </p>
      </section>

      {data && (
        <section className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {n(data.launches_24h) != null && (
            <Stat value={n(data.launches_24h)!} label="launches read in 24h" />
          )}
          {n(data.accounts_named_24h) != null && (
            <Stat value={n(data.accounts_named_24h)!} label="of them named an X account" />
          )}
          {n(data.gas_hits_24h) != null && (
            <Stat value={n(data.gas_hits_24h)!} label="high-gas buys caught in 24h" />
          )}
          <Stat value={String(pads.length || 6)} label="Robinhood launchpads watched" />
        </section>
      )}

      <section className="mt-14 space-y-4">
        {PILLARS.map((p) => (
          <div key={p.n}
               className="flex gap-4 rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <div className="flex flex-col items-center gap-2">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand/15 text-brand-soft">
                <p.icon size={17} />
              </span>
              <span className="text-[10px] font-mono text-text-dim">{p.n}</span>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-text">{p.title}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-text-muted">{p.body}</p>
              <p className="mt-2 text-[11px] leading-relaxed text-text-dim">{p.detail}</p>
            </div>
          </div>
        ))}
      </section>

      <section className="mt-14 rounded-xl border border-border bg-bg-card/40 p-6">
        <SiteHeading title="What this is not"
                     lead="Worth saying before you pay for it." />
        <ul className="space-y-2 text-xs leading-relaxed text-text-muted">
          <li>• <b className="text-text">Not advice.</b> It reports; you decide. Nobody here knows what a token will do.</li>
          <li>• <b className="text-text">Not a guarantee of being first.</b> Everybody on the tool is told in the same second — the edge is seeing six things at once, not a private queue.</li>
          <li>• <b className="text-text">Not unlimited seats.</b> A signal shared with everybody stops being one, so accounts are capped on purpose.</li>
          <li>• <b className="text-text">Not custody.</b> We never hold funds or ask for a key. Payment is USDT or USDC, once, for a plan.</li>
        </ul>
      </section>

      <section className="mt-14 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-brand/30 bg-brand/5 p-6">
        <div>
          <h3 className="text-sm font-semibold text-text">Try it for a week</h3>
          <p className="mt-1 text-xs text-text-muted">
            Every panel readable, a few tokens of your own, alerts on the
            dashboard. No card, nothing to cancel.
          </p>
        </div>
        <Link href="/register"
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          Create an account <ArrowRight size={14} />
        </Link>
      </section>
    </SiteChrome>
  );
}
