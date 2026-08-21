"use client";

import Link from "next/link";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* Privacy. The honest version: this service collects very little, because it
 * was built as one person's tool and never grew a tracking habit. */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
      <h2 className="text-sm font-semibold text-text">{title}</h2>
      <div className="mt-2 space-y-2 text-xs leading-relaxed text-text-muted">{children}</div>
    </section>
  );
}

export default function PrivacyPage() {
  return (
    <SiteChrome>
      <SiteHeading eyebrow="Legal" title="Privacy"
                   lead="What we keep, why, and for how long." />

      <div className="space-y-4">
        <Section title="What we hold">
          <p>
            Your username, email address, a hash of your password (never the
            password itself), the plan you are on and when it ends, and the
            tokens and targets you added. If you connect Telegram, the chat id
            the bot answers in — that is all Telegram gives us.
          </p>
          <p>
            Orders keep the plan, the amount quoted and the receiving address.
            Support requests keep what you wrote and a snapshot of what the
            system was doing at that moment.
          </p>
        </Section>

        <Section title="What we do not hold">
          <p>
            No card details — there is no card. No private keys, no seed
            phrases, no wallet connection. We never ask for any of these, and
            anybody who does is not us.
          </p>
          <p>
            No advertising trackers, no third-party analytics scripts, no
            selling anything to anybody.
          </p>
        </Section>

        <Section title="Who else sees it">
          <p>
            Email goes out through a mail provider so it can reach you.
            Blockchain reads go to RPC providers — those requests carry token
            addresses, never anything about you. Telegram delivers the messages
            you asked for. Nobody else.
          </p>
        </Section>

        <Section title="How long">
          <p>
            Account data for as long as the account exists. Detections, alerts
            and readings age out automatically — most within 15 to 30 days,
            because the product only ever looks at what is recent.
          </p>
          <p>
            Ask us to delete your account and we delete it, along with your
            lists and your support history. Orders are kept as a record of
            payment.
          </p>
        </Section>

        <Section title="Your choices">
          <p>
            Change your email or password on Profile, disconnect Telegram there
            too, or ask for a copy or a deletion by{" "}
            <Link href="/contact" className="text-brand-soft hover:underline">contacting us</Link>.
          </p>
        </Section>
      </div>
    </SiteChrome>
  );
}
