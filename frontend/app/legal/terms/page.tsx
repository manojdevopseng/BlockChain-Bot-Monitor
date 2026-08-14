"use client";

import Link from "next/link";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* Terms. Written to be read, not to be survived.
 *
 * Every clause here is one we can actually honour with the system as built —
 * a promise about uptime or profits would be a promise we do not control. */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
      <h2 className="text-sm font-semibold text-text">{title}</h2>
      <div className="mt-2 space-y-2 text-xs leading-relaxed text-text-muted">{children}</div>
    </section>
  );
}

export default function TermsPage() {
  return (
    <SiteChrome>
      <SiteHeading eyebrow="Legal" title="Terms of use"
                   lead="Short, and in plain words. Using the service means these
                         apply." />

      <div className="space-y-4">
        <Section title="1. What the service is">
          <p>
            A monitoring tool. It reads public blockchain data and public X
            profiles, and shows you what it found. It does not trade, hold funds,
            or act on your behalf in any way.
          </p>
        </Section>

        <Section title="2. It is not advice">
          <p>
            Nothing here is financial, investment, tax or legal advice, and
            nothing here is a recommendation to buy or sell anything. We are not
            licensed advisers. Decisions you make with this information are
            yours, and so are their outcomes.
          </p>
          <p>
            Trading tokens of this kind carries a high risk of losing everything
            you put in. Most new tokens end at or near zero.
          </p>
        </Section>

        <Section title="3. What we promise, and what we do not">
          <p>
            We try to keep the service running, accurate and quick, and we fix
            what breaks. We do not promise it will be available without
            interruption, that every launch will be detected, or that any figure
            is free of error — the data comes from chains, third-party endpoints
            and X, and any of them can be slow, wrong or unavailable.
          </p>
          <p>
            We do not promise you will be first to anything. Every account is
            told at the same moment.
          </p>
        </Section>

        <Section title="4. Your account">
          <p>
            One account is for one person. Sharing a login, reselling access, or
            re-publishing the alerts as your own service ends the account without
            a refund.
          </p>
          <p>
            Keep your password to yourself. Anything done with your login is
            treated as done by you.
          </p>
        </Section>

        <Section title="5. Fair use">
          <p>
            Plan limits exist because each tracked token costs real requests on
            endpoints we pay for. Working around them — multiple accounts for one
            person, scripted requests, scraping the API — is a breach of these
            terms, and we may suspend an account that does it.
          </p>
        </Section>

        <Section title="6. Payment">
          <p>
            Plans are paid in advance in USDT or USDC. Nothing renews
            automatically and no payment details are stored, because there are
            none to store. When the days run out, access stops until you buy
            more. See <Link href="/legal/refund" className="text-brand-soft hover:underline">refunds</Link>.
          </p>
        </Section>

        <Section title="7. Ending it">
          <p>
            You can stop at any time by not buying more days; nothing needs
            cancelling. We may suspend or end an account that breaches these
            terms, and will say why. If we end the service entirely, unused paid
            time is refunded.
          </p>
        </Section>

        <Section title="8. Liability">
          <p>
            To the extent the law allows, our liability for any claim connected
            to the service is limited to what you paid us in the three months
            before it arose. We are not liable for trading losses, missed
            opportunities, or decisions made using this information.
          </p>
        </Section>

        <Section title="9. Changes">
          <p>
            If these terms change in a way that matters, accounts are told by
            email before it takes effect. Continuing to use the service after
            that means the new version applies.
          </p>
        </Section>
      </div>

      <p className="mt-8 text-[11px] text-text-dim">
        Questions about any of this: <Link href="/contact" className="text-brand-soft hover:underline">contact us</Link>.
      </p>
    </SiteChrome>
  );
}
