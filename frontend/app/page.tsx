"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { LayoutDashboard, Radar, ArrowRight, ShieldCheck, Moon, Sun } from "lucide-react";
import { getToken } from "@/lib/api";
import { useTheme } from "@/lib/theme";

/* The front door. Two dashboards read the same data and the same scanners —
   this only decides which one you land in. Deliberately unauthenticated: it
   shows nothing but its own two links, and each of them goes through the login
   that already guards everything behind it. */

type Choice = {
  href: string;
  title: string;
  blurb: string;
  points: string[];
  icon: typeof LayoutDashboard;
};

const CHOICES: Choice[] = [
  {
    href: "/dashboard",
    title: "Main Dashboard",
    blurb: "Everything the monitor does, on one shell.",
    points: ["Detections, RSI and launchpads", "Forwarder, alerts and commands",
             "Settings, RPCs and system"],
    icon: LayoutDashboard,
  },
  {
    href: "/lite",
    title: "2nd Dashboard",
    blurb: "Premium calls, one row per call, and what the caller actually said.",
    points: ["Every call listed separately, newest first",
             "TG Tracker — messages, replies and images",
             "Add a caller group without leaving the page"],
    icon: Radar,
  },
];

// The chooser sits outside the Shell, so it carries its own theme control —
// the same one the header uses, minus the header.
function ThemeButton() {
  const { theme, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-text-muted
                 transition-colors hover:bg-bg-hover hover:text-text"
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={theme === "dark" ? "Light mode" : "Dark mode"}
    >
      {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}

export default function Chooser() {
  // Only to change the wording on the buttons — the pages guard themselves.
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  useEffect(() => setSignedIn(!!getToken()), []);

  return (
    <div className="min-h-screen bg-bg px-5 py-10">
      <div className="mx-auto flex max-w-4xl flex-col gap-10">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-text">BlockChain-Bot Monitor</h1>
            <p className="mt-1 text-sm text-text-muted">
              Pick a dashboard. Both run off the same scanners and the same
              database — nothing is duplicated behind them.
            </p>
          </div>
          <ThemeButton />
        </header>

        <div className="grid gap-4 sm:grid-cols-2">
          {CHOICES.map((c) => (
            <Link
              key={c.href}
              href={signedIn ? c.href : `/login?next=${encodeURIComponent(c.href)}`}
              className="group flex flex-col gap-4 rounded-xl border border-border bg-bg-card/60 p-6
                         transition hover:border-brand/50 hover:bg-bg-hover/40
                         focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
            >
              <span className="grid h-10 w-10 place-items-center rounded-lg bg-brand/15 text-brand-soft">
                <c.icon size={19} />
              </span>
              <div>
                <h2 className="text-base font-semibold text-text">{c.title}</h2>
                <p className="mt-1 text-sm text-text-muted">{c.blurb}</p>
              </div>
              <ul className="flex flex-col gap-1.5 text-xs text-text-dim">
                {c.points.map((p) => (
                  <li key={p} className="flex gap-2">
                    <span className="text-brand-soft">·</span>{p}
                  </li>
                ))}
              </ul>
              <span className="mt-auto flex items-center gap-1.5 pt-2 text-xs font-medium text-brand-soft">
                {signedIn === false ? "Sign in and open" : "Open"}
                <ArrowRight size={13} className="transition group-hover:translate-x-0.5" />
              </span>
            </Link>
          ))}
        </div>

        <p className="flex items-center gap-2 text-xs text-text-dim">
          <ShieldCheck size={13} />
          Both dashboards need the same login. Signing out of one signs you out
          of both.
        </p>
      </div>
    </div>
  );
}
