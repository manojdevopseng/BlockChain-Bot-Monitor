"use client";

import Link from "next/link";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* Refunds. Written before anybody asks, because a policy invented during an
 * argument is not a policy. */

export default function RefundPage() {
  return (
    <SiteChrome>
      <SiteHeading eyebrow="Legal" title="Refunds"
                   lead="What we refund, what we do not, and why — decided in
                         advance rather than during an argument." />

      <div className="space-y-4">
        <section className="rounded-xl border border-accent-green/30 bg-accent-green/5 p-5">
          <h2 className="text-sm font-semibold text-text">We refund</h2>
          <ul className="mt-2 space-y-1.5 text-xs leading-relaxed text-text-muted">
            <li>• <b className="text-text">Our fault.</b> If the service was
              unusable for a stretch because of something at our end, we refund
              that time, pro rata, without being asked twice.</li>
            <li>• <b className="text-text">Paid twice.</b> A duplicate payment
              comes straight back, or becomes extra days — your choice.</li>
            <li>• <b className="text-text">Paid and never got in.</b> If a
              payment landed and the plan did not start and we cannot fix it,
              you get it back.</li>
            <li>• <b className="text-text">We close the service.</b> Unused paid
              time is returned.</li>
          </ul>
        </section>

        <section className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
          <h2 className="text-sm font-semibold text-text">We do not refund</h2>
          <ul className="mt-2 space-y-1.5 text-xs leading-relaxed text-text-muted">
            <li>• <b className="text-text">What the market did.</b> A trade that
              lost money is not a fault in a monitoring tool, however it was
              found.</li>
            <li>• <b className="text-text">A change of mind after use.</b> The
              trial is seven days and costs nothing — that is the time to
              decide.</li>
            <li>• <b className="text-text">Sent to the wrong chain.</b> A
              transfer on a network we do not watch never reaches us and cannot
              be recovered by us. Check the network before you send.</li>
            <li>• <b className="text-text">An account ended for breaking the
              terms.</b> Sharing a login or reselling access ends it without a
              refund.</li>
          </ul>
        </section>

        <section className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
          <h2 className="text-sm font-semibold text-text">Sent the wrong amount?</h2>
          <p className="mt-2 text-xs leading-relaxed text-text-muted">
            That is not a refund case, it is a matching case. Every order is
            quoted an exact figure; if you sent a rounded number instead, the
            payment still arrived — open a support request with the order id and
            it gets matched by hand, usually the same day.
          </p>
        </section>

        <section className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
          <h2 className="text-sm font-semibold text-text">How to ask</h2>
          <p className="mt-2 text-xs leading-relaxed text-text-muted">
            From inside the app: Support → &ldquo;I paid and my plan did not
            start&rdquo; or &ldquo;I want to change or cancel my plan&rdquo;.
            Without an account:{" "}
            <Link href="/contact" className="text-brand-soft hover:underline">contact us</Link>{" "}
            with the order id and the address you paid from. Refunds go back to
            the address they came from, in the same coin.
          </p>
        </section>
      </div>
    </SiteChrome>
  );
}
