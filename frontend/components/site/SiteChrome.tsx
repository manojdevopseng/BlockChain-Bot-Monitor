"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Cpu } from "lucide-react";
import { getToken } from "@/lib/api";
import { cn } from "@/lib/utils";

/* The frame around every page a stranger can see.
 *
 * Deliberately not the dashboard shell: no sidebar, no status bar, no socket.
 * Somebody who has not signed in should not be paying for a WebSocket or
 * looking at a nav full of things they cannot open. */

const LINKS = [
  { href: "/", label: "Home" },
  { href: "/how-to-use", label: "How to use" },
  { href: "/pricing", label: "Pricing" },
  { href: "/docs", label: "Docs" },
  { href: "/faq", label: "FAQ" },
  { href: "/contact", label: "Contact" },
];

export function SiteChrome({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  // Read after mount, never during render: the server has no localStorage, and
  // a header that differs between the two flickers on every load.
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => setSignedIn(!!getToken()), []);

  return (
    <div className="min-h-screen bg-bg">
      <header className="sticky top-0 z-40 border-b border-border bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <Link href="/" className="flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand/20 text-brand">
              <Cpu size={17} />
            </span>
            <span className="text-sm font-semibold text-text">Sightline</span>
            <span className="hidden text-[11px] text-text-dim sm:inline">
              four chains, six ways
            </span>
          </Link>

          <nav className="ml-4 hidden gap-4 md:flex">
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href}
                    className={cn("text-xs transition-colors",
                                  path === l.href ? "text-text"
                                                  : "text-text-dim hover:text-text-muted")}>
                {l.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {signedIn ? (
              <Link href="/dashboard"
                    className="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
                Open dashboard
              </Link>
            ) : (
              <>
                <Link href="/login"
                      className="rounded-lg px-3 py-1.5 text-xs text-text-muted hover:text-text">
                  Sign in
                </Link>
                <Link href="/register"
                      className="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
                  Start free
                </Link>
              </>
            )}
          </div>
        </div>

        {/* The same links on a phone, where the row above has no room. */}
        <nav className="flex gap-4 overflow-x-auto border-t border-border-soft px-4 py-2 md:hidden">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href}
                  className={cn("whitespace-nowrap text-[11px]",
                                path === l.href ? "text-text" : "text-text-dim")}>
              {l.label}
            </Link>
          ))}
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-10">{children}</main>

      <footer className="border-t border-border">
        <div className="mx-auto max-w-6xl px-4 py-8">
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px] text-text-dim">
            <Link href="/legal/terms" className="hover:text-text-muted">Terms</Link>
            <Link href="/legal/privacy" className="hover:text-text-muted">Privacy</Link>
            <Link href="/legal/refund" className="hover:text-text-muted">Refunds</Link>
            <Link href="/contact" className="hover:text-text-muted">Contact</Link>
            <Link href="/changelog" className="hover:text-text-muted">Changelog</Link>
            <Link href="/status" className="hover:text-text-muted">Status</Link>
          </div>
          <p className="mt-4 max-w-3xl text-[11px] leading-relaxed text-text-dim">
            This is a monitoring tool, not advice. It reports what is happening
            on chain and on X; what to do about it is your decision and your
            risk. Most tokens go to zero. Never put in money you need.
          </p>
        </div>
      </footer>
    </div>
  );
}

/** One section heading, used on every page here so they read alike. */
export function SiteHeading({ eyebrow, title, lead }: {
  eyebrow?: string; title: string; lead?: string;
}) {
  return (
    <div className="mb-8 max-w-3xl">
      {eyebrow && <p className="mb-2 text-[11px] uppercase tracking-wide text-brand-soft">{eyebrow}</p>}
      <h1 className="text-2xl font-semibold text-text sm:text-3xl">{title}</h1>
      {lead && <p className="mt-3 text-sm leading-relaxed text-text-muted">{lead}</p>}
    </div>
  );
}
