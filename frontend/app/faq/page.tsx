"use client";

import Link from "next/link";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* FAQ — the questions that actually get asked, answered without hedging.
 *
 * Including the uncomfortable ones. A page that only answers the flattering
 * questions is one nobody believes. */

const QA: { q: string; a: React.ReactNode }[] = [
  {
    q: "Will this make me money?",
    a: <>Nobody can tell you that, and anybody who does is selling something worse
       than this. It tells you what launched, who is behind it and what it is
       worth right now, sooner than you would find out by hand. What you do with
       that is yours, and most tokens go to zero.</>,
  },
  {
    q: "What is actually different about it?",
    a: <>Six things in one place, on four chains: gas fees that show somebody
       paying to get in first, calls from the groups worth reading with what
       those calls actually did afterwards, every Robinhood launch with the X
       account read off the token&rsquo;s own metadata, the narrative it belongs
       to, what it is worth on V2, V3 and V4 pools, and RSI on what you hold.
       Any one of those exists somewhere; having them argue with each other on
       one screen is the point.</>,
  },
  {
    q: "Do I get alerts before other subscribers?",
    a: <>No. Everybody on the tool is told at the same moment. That is also why
       seats are limited: a signal that goes to an unlimited number of people
       stops being a signal.</>,
  },
  {
    q: "How do I pay, and does it renew?",
    a: <>USDT or USDC to an address we show you, with an exact amount that
       identifies your order. Nothing renews by itself and there is no card on
       file — when the days run out, the account stops until you buy more. Days
       are added to what you have, so paying early loses nothing.</>,
  },
  {
    q: "What happens to my tokens and settings if I let it lapse?",
    a: <>They stay exactly where they are. An expired account can still sign in,
       see its Profile, pay, and open a support request — it just cannot use the
       panels. Buying again brings everything back as it was.</>,
  },
  {
    q: "Where do the alerts go?",
    a: <>To your own private chat with our Telegram bot, on a paid plan. Never to
       a shared group — nobody else&rsquo;s alerts reach you, and yours reach
       nobody else. On the trial, alerts show on the dashboard only.</>,
  },
  {
    q: "Which chains?",
    a: <>Robinhood Chain, Ethereum, BNB Chain and Solana. On the three EVM chains
       V2, V3 and V4 pools are all priced — which matters on Robinhood, where the
       launchpads mint straight into hooked V4 pools that most tools cannot read.</>,
  },
  {
    q: "Why are there limits on how many tokens I can track?",
    a: <>Because each one is a real request every few seconds, for as long as you
       track it, on endpoints we pay for. The limits are the shape of that bill.
       If you need more than the yearly plan allows, ask.</>,
  },
  {
    q: "Do you take custody of anything?",
    a: <>No. We never hold funds, never ask for a private key or seed phrase, and
       never will — if anybody claiming to be us does, it is not us.</>,
  },
  {
    q: "Can I get a refund?",
    a: <>If the product could not do what it says — a fault at our end — yes, for
       the unused part. Not for what the market did. See{" "}
       <Link href="/legal/refund" className="text-brand-soft hover:underline">refunds</Link>.</>,
  },
];

export default function FaqPage() {
  return (
    <SiteChrome>
      <SiteHeading eyebrow="FAQ" title="Questions people actually ask"
                   lead="Including the awkward ones." />
      <div className="space-y-3">
        {QA.map(({ q, a }) => (
          <details key={q} className="group rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <summary className="cursor-pointer list-none text-sm font-medium text-text marker:hidden">
              <span className="mr-2 text-brand-soft group-open:hidden">+</span>
              <span className="mr-2 hidden text-brand-soft group-open:inline">−</span>
              {q}
            </summary>
            <p className="mt-2.5 pl-5 text-xs leading-relaxed text-text-muted">{a}</p>
          </details>
        ))}
      </div>
      <p className="mt-8 text-xs text-text-dim">
        Something not here? <Link href="/contact" className="text-brand-soft hover:underline">Ask us</Link>.
      </p>
    </SiteChrome>
  );
}
