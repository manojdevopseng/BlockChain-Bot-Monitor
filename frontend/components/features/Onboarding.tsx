"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, ChevronRight, X } from "lucide-react";
import { useAccount } from "@/lib/account";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/* The first ten minutes, on the dashboard, until they are done.
 *
 * Every step is DERIVED from what the account has actually done — a token
 * exists, Telegram is connected — rather than from a checklist somebody ticks.
 * A tick you give yourself measures nothing, and a step that completes itself
 * is one nobody has to be reminded of.
 *
 * It disappears on its own when the steps are done, and can be dismissed before
 * that; the dismissal is a browser preference rather than a fact about the
 * account, so it is stored where browser preferences go. */

const KEY = "onboarding_hidden";

export function Onboarding() {
  const { account } = useAccount();
  const [hidden, setHidden] = useState(true);   // assume hidden until we know

  useEffect(() => {
    setHidden(localStorage.getItem(KEY) === "1");
  }, []);

  if (!account || hidden) return null;

  const canTelegram = !!account.limits?.telegram_alerts;
  const steps = [
    {
      done: account.email_verified,
      title: "Confirm your email",
      body: "Your trial starts when you do.",
      href: "/profile", cta: "Profile",
    },
    canTelegram ? {
      done: account.telegram_linked,
      title: "Connect Telegram",
      body: "Alerts arrive in your own chat with the bot — nobody else's.",
      href: "/profile", cta: "Connect",
    } : {
      done: false,
      title: "Telegram alerts come with a paid plan",
      body: "On the trial, everything shows on the dashboard.",
      href: "/plan", cta: "See plans",
    },
    {
      done: (account.usage?.mcap_tokens ?? 0) > 0,
      title: "Watch your first market cap",
      body: "Pick a chain, paste an address, set a number. One message when it gets there.",
      href: "/rsi", cta: "Market Cap Alert",
    },
    {
      done: (account.usage?.rsi_tokens ?? 0) > 0,
      title: "Add a token to RSI",
      body: "Your timeframe, your candle count — alerts on a crossing, not while it sits there.",
      href: "/rsi", cta: "RSI Tracker",
    },
    {
      done: false,
      title: "Read the six-step guide",
      body: "Ten minutes, and it saves the two questions everybody asks once.",
      href: "/how-to-use", cta: "How to use",
    },
  ];

  const done = steps.filter((s) => s.done).length;
  // Everything that can complete itself has: stop showing it.
  if (done >= steps.length - 1) return null;

  function dismiss() {
    setHidden(true);
    try { localStorage.setItem(KEY, "1"); } catch {}
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Getting started</CardTitle>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-text-dim">{done} of {steps.length}</span>
          <button onClick={dismiss} title="Hide this"
                  className="grid h-6 w-6 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-text">
            <X size={13} />
          </button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {steps.map((s) => (
            <Link key={s.title} href={s.href}
                  className="flex items-start gap-2.5 rounded-lg border border-border-soft px-3 py-2.5 hover:bg-bg-hover/40">
              <span className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border ${
                s.done ? "border-accent-green bg-accent-green/20 text-accent-green"
                       : "border-border text-transparent"}`}>
                <Check size={10} />
              </span>
              <span className="min-w-0">
                <span className={`block text-xs ${s.done ? "text-text-dim line-through" : "text-text"}`}>
                  {s.title}
                </span>
                {!s.done && (
                  <>
                    <span className="mt-0.5 block text-[11px] text-text-muted">{s.body}</span>
                    <span className="mt-1 inline-flex items-center gap-0.5 text-[11px] text-brand-soft">
                      {s.cta} <ChevronRight size={11} />
                    </span>
                  </>
                )}
              </span>
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
