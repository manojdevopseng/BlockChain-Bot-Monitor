"use client";

import Link from "next/link";
import { ArrowRight, Bell, Crosshair, Search, Target, Twitter } from "lucide-react";
import { useApi } from "@/lib/api";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* The front page.
 *
 * It says the one thing that is actually different — you find out who is behind
 * a Robinhood launch in the first seconds, not after the chart has moved — and
 * it says the honest parts too. A landing page that promises returns is one
 * that gets refund requests for a market it does not control. */

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-xl border border-border-soft bg-bg-card/40 px-4 py-3">
      <p className="text-lg font-semibold text-text">{value}</p>
      <p className="text-[11px] text-text-dim">{label}</p>
    </div>
  );
}

export default function HomePage() {
  // The public endpoint: counts of what the scanners actually recorded, never
  // anything about a person.
  const { data } = useApi<any>("/api/public/stats", { refreshInterval: 0 });
  const pads: string[] = data?.launchpads ?? [];

  return (
    <SiteChrome>
      <section className="max-w-3xl">
        <p className="mb-3 text-[11px] uppercase tracking-wide text-brand-soft">
          Robinhood Chain · Ethereum · BNB Chain · Solana
        </p>
        <h1 className="text-3xl font-semibold leading-tight text-text sm:text-4xl">
          Know who is behind a launch
          <span className="block text-brand-soft">in the first seconds</span>
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-text-muted">
          Every launch on the Robinhood launchpads, read straight off the
          contract: the X account named in its own metadata, that account&rsquo;s
          bio and follower count, whether the bio carries the token address, and
          whether the deployer bought their own supply. Not a feed of tickers —
          the part that tells you whether there is anybody behind it.
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

      {(data?.launches_24h != null) && (
        <section className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat value={String(data.launches_24h)} label="launches read in 24h" />
          <Stat value={String(data.accounts_named_24h ?? 0)} label="named an X account" />
          <Stat value={String(pads.length)} label="launchpads watched" />
          <Stat value="4" label="chains" />
        </section>
      )}

      <section className="mt-14 grid grid-cols-1 gap-5 md:grid-cols-2">
        {[
          { icon: Twitter, title: "Who launched it",
            body: "The X account out of the token's own metadata — a profile link, a signed proof, or the launchpad's own record. Then the bio, the follower count, and whether that bio quotes the contract address." },
          { icon: Crosshair, title: "Every Robinhood launchpad",
            body: `${pads.join(", ") || "Pons, Pons V2, Flap, Pools.trade, Virtuals, LetsCash"} — one panel, one filter per launchpad, V2, V3 and V4 pools all priced.` },
          { icon: Bell, title: "Watch and skip lists",
            body: "Accounts you care about, flagged the moment they launch again. Accounts you are tired of, gone. Keywords matched against the bio, whole words only." },
          { icon: Target, title: "Market cap alerts",
            body: "Set a number on any token on four chains. One message when it gets there — not one every fifteen seconds while it sits above it." },
          { icon: Search, title: "Market cap, on demand",
            body: "Paste any address, pick the chain, read supply × price straight off the chain. Nothing stored, nothing watched." },
          { icon: Bell, title: "RSI on your own terms",
            body: "Your tokens, your timeframe, your candle count. Alerts on a crossing, not while it sits in the zone." },
        ].map((f) => (
          <div key={f.title} className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand/15 text-brand-soft">
              <f.icon size={17} />
            </span>
            <h3 className="mt-3 text-sm font-semibold text-text">{f.title}</h3>
            <p className="mt-1.5 text-xs leading-relaxed text-text-muted">{f.body}</p>
          </div>
        ))}
      </section>

      <section className="mt-14 rounded-xl border border-border bg-bg-card/40 p-6">
        <SiteHeading title="What this is not"
                     lead="Worth saying before you pay for it." />
        <ul className="space-y-2 text-xs leading-relaxed text-text-muted">
          <li>• <b className="text-text">Not advice.</b> It reports; you decide. Nobody here knows what a token will do.</li>
          <li>• <b className="text-text">Not a guarantee of being first.</b> You get the same second everyone else on the tool does — the edge is knowing who is behind it, not a private queue.</li>
          <li>• <b className="text-text">Not unlimited seats.</b> A signal shared with everybody stops being one, so the number of accounts is capped on purpose.</li>
          <li>• <b className="text-text">Not custody.</b> We never hold your funds or ask for a key. Payment is USDT or USDC to our address, once, for a plan.</li>
        </ul>
      </section>

      <section className="mt-14 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-brand/30 bg-brand/5 p-6">
        <div>
          <h3 className="text-sm font-semibold text-text">Try it for a week</h3>
          <p className="mt-1 text-xs text-text-muted">
            Everything readable, three tokens of your own, alerts on the
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
