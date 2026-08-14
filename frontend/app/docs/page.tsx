"use client";

import Link from "next/link";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* Docs: one block per panel, all built the same way.
 *
 * "How to use" is the first ten minutes and is read once. This is the thing you
 * come back to, so every entry answers the same four questions in the same
 * order — what it is, when to look at it, how to set it, and the mistake
 * everybody makes once. */

type Entry = {
  id: string; title: string; what: string; when: string;
  how: string[]; gotcha: string;
};

const SECTIONS: { group: string; entries: Entry[] }[] = [
  {
    group: "Detections",
    entries: [
      {
        id: "launchpad", title: "RBH Launchpad Monitor",
        what: "Every launch on the six Robinhood launchpads, read off the token's own contract within seconds of the mint.",
        when: "Constantly, if you trade Robinhood launches. It is the panel this product exists for.",
        how: ["Filter by launchpad with the tabs — All, Pons, Pons V2, Flap, Pools.trade, Virtuals, LetsCash.",
              "Read the Account column: a profile link is a claim anyone could make; a 🔒 means the deployer proved they own it.",
              "The Text column is the account's bio. Green is the token address quoted in it; amber is one of your keywords.",
              "Lists → add an account to Watch (told whenever they launch again) or Skip (never shown)."],
        gotcha: "An empty Account column means the launch named nobody — not that we failed to look. On some launchpads that is most of them.",
      },
      {
        id: "xmonitor", title: "Robinhood — X — Token Monitor",
        what: "The same chain, filtered to launches that carry an X account, with follower counts and verification.",
        when: "When you want signal over volume — this one is quiet by design.",
        how: ["Set Min Followers to cut the long tail.",
              "Verified accounts only is off by default; turn it on if blue ticks are all you trade."],
        gotcha: "It shares one socket with the Launchpad Monitor, so switching that off does not switch this off, and the reverse.",
      },
    ],
  },
  {
    group: "Your own tokens",
    entries: [
      {
        id: "mcap", title: "Market Cap Alert",
        what: "Watches tokens you add on RBH, ETH, BSC and SOL, and says once when one reaches a number you set.",
        when: "Anything you hold or are stalking. It is the cheapest way to stop watching a chart.",
        how: ["Add: chain, address, target — targets take 250k, 1.5m or 40000.",
              "A target above where it is now fires on the way up; below, on the way down.",
              "The check interval is per account; a paid plan can sit on 15 seconds."],
        gotcha: "Market cap here is total supply × price. A token whose team holds half will read higher than a site guessing at circulating supply.",
      },
      {
        id: "mcheck", title: "Market Cap Check",
        what: "One reading, on demand. Paste an address, pick the chain, get supply × price.",
        when: "Somebody sends you a contract and you want the number before you decide anything.",
        how: ["Nothing is stored and nothing is watched — for that, add it to the alert list above."],
        gotcha: "Daily allowance per plan, because each check is a real request on endpoints we pay for.",
      },
      {
        id: "rsi", title: "RSI Tracker",
        what: "Wilder's RSI on the tokens you add, on the timeframe and candle count you choose.",
        when: "For positions, not for launches — a token needs a history before RSI means anything.",
        how: ["Add a token, then set its own timeframe and candle count on the row.",
              "Bounds are 30/70 by default; alerts fire on a crossing, not while it sits in the zone."],
        gotcha: "Timeframe (how long one candle is) and check interval (how often it is recomputed) are different things. Only the first changes the number.",
      },
    ],
  },
  {
    group: "Account",
    entries: [
      {
        id: "telegram", title: "Telegram alerts",
        what: "Your alerts, in your own private chat with the bot.",
        when: "As soon as you are on a paid plan.",
        how: ["Profile → Connect Telegram → open the link, press Start.",
              "Disconnect from the same place; the link is one-shot and lasts fifteen minutes."],
        gotcha: "Nobody else's alerts reach your chat, and yours reach nobody else's — there is no shared group.",
      },
      {
        id: "billing", title: "Plans and orders",
        what: "Prepaid time. Nothing renews by itself and no card is stored, because there is none.",
        when: "Before the days run out — the bell warns you at three days and again at one.",
        how: ["Plan → pick a length → pick a coin and chain → send the EXACT amount shown.",
              "Orders shows every attempt and what came of it."],
        gotcha: "Send the exact figure, not the round one. Those last digits are what identify your payment.",
      },
    ],
  },
];

export default function DocsPage() {
  return (
    <SiteChrome>
      <SiteHeading eyebrow="Docs" title="Every panel, and what it is for"
                   lead="The reference. If you are here for the first ten minutes,
                         read How to use instead." />

      <div className="space-y-10">
        {SECTIONS.map((s) => (
          <section key={s.group}>
            <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-brand-soft">
              {s.group}
            </h2>
            <div className="space-y-4">
              {s.entries.map((e) => (
                <div key={e.id} id={e.id}
                     className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
                  <h3 className="text-sm font-semibold text-text">{e.title}</h3>
                  <p className="mt-2 text-xs leading-relaxed text-text-muted">{e.what}</p>
                  <p className="mt-2 text-xs leading-relaxed text-text-dim">
                    <b className="text-text-muted">When:</b> {e.when}
                  </p>
                  <ul className="mt-2 space-y-1">
                    {e.how.map((h) => (
                      <li key={h} className="text-xs leading-relaxed text-text-muted">• {h}</li>
                    ))}
                  </ul>
                  <p className="mt-3 rounded-lg border border-accent-amber/25 bg-accent-amber/5 px-3 py-2 text-[11px] leading-relaxed text-text-muted">
                    <b className="text-text">Once:</b> {e.gotcha}
                  </p>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>

      <p className="mt-10 text-xs text-text-dim">
        Missing something? <Link href="/contact" className="text-brand-soft hover:underline">Tell us</Link> and it goes in.
      </p>
    </SiteChrome>
  );
}
