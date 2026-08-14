"use client";

import Link from "next/link";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* How to use it — the five-minute version.
 *
 * Deliberately short. Nobody reads documentation before they have a problem, so
 * this is the first ten minutes and nothing else; the detail belongs beside the
 * thing it describes, inside the app. */

const STEPS = [
  {
    n: "1", title: "Create an account and confirm your email",
    body: "The 7-day trial starts when you open the link in that email, not when you sign up — so nothing is running down while you read this.",
  },
  {
    n: "2", title: "Connect Telegram (paid plans)",
    body: "Profile → Connect Telegram → open the link and press Start. Alerts then arrive in your own chat with the bot. Nobody else's alerts land there, and yours land in nobody else's.",
  },
  {
    n: "3", title: "Open Detections and watch the Launchpad Monitor",
    body: "Every launch on the Robinhood launchpads, newest first. The Account column is the one to read: a profile link is a claim, a 🔒 is a signed proof, and an empty one means nobody was named.",
  },
  {
    n: "4", title: "Add the accounts you care about to the Watch list",
    body: "When one of them launches again you are told immediately — with the token, the bio, and whether that bio quotes the contract address.",
  },
  {
    n: "5", title: "Set a market cap on anything you are holding",
    body: "RSI nav → Market Cap Alert → chain, address, target. One message when it gets there. A target above where it is now fires on the way up; below, on the way down.",
  },
  {
    n: "6", title: "If something looks wrong, say so in two taps",
    body: "Support → tick what happened. The page you were on, your plan and whether the part behind it was running are attached for you.",
  },
];

const CONFUSIONS = [
  {
    q: "Timeframe and check interval are not the same thing",
    a: "The timeframe is how long one RSI candle is — that decides the number. The check interval is only how often that number is recomputed, and it is what costs requests. Changing the second one never changes the reading.",
  },
  {
    q: "Market cap here is supply × price, read on chain",
    a: "Total supply, not circulating — no on-chain call can tell you which wallets a team controls. A token whose team holds half will read higher here than on a site that guesses at circulating supply.",
  },
  {
    q: "A brand-new token can show no price for a minute",
    a: "There is nothing to read until its pool exists. It is retried automatically; nothing needs pressing.",
  },
  {
    q: "An empty Account column is information",
    a: "It means the launch named nobody — not that we failed to look. On some launchpads that is most of them.",
  },
];

export default function HowToUsePage() {
  return (
    <SiteChrome>
      <SiteHeading
        eyebrow="How to use"
        title="The first ten minutes"
        lead="Six steps to having it working. Everything else is explained beside
              the thing it describes, inside the app."
      />

      <ol className="space-y-4">
        {STEPS.map((s) => (
          <li key={s.n} className="flex gap-4 rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand/15 text-sm font-semibold text-brand-soft">
              {s.n}
            </span>
            <div>
              <h3 className="text-sm font-semibold text-text">{s.title}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-text-muted">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>

      <section className="mt-12">
        <SiteHeading title="Four things that confuse everybody once"
                     lead="Read these now and save yourself a support request." />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {CONFUSIONS.map((c) => (
            <div key={c.q} className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
              <h3 className="text-sm font-semibold text-text">{c.q}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-text-muted">{c.a}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-12 rounded-xl border border-brand/30 bg-brand/5 p-6">
        <h3 className="text-sm font-semibold text-text">Ready?</h3>
        <p className="mt-1 text-xs text-text-muted">
          Seven days, no card. <Link href="/register" className="text-brand-soft hover:underline">
            Create an account
          </Link> — or <Link href="/contact" className="text-brand-soft hover:underline">
            ask us something
          </Link> first.
        </p>
      </section>
    </SiteChrome>
  );
}
