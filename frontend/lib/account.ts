"use client";

import { useEffect } from "react";

import { useApi } from "./api";

/**
 * The signed-in account: who they are, what they pay for, what that allows.
 *
 * One request answers all of it (`/api/account/me`) because the paywall, the
 * Profile page and every "you have used 3 of 3" line want the same facts, and
 * three hooks asking three times would disagree with each other for a second
 * at a time.
 *
 * Nothing here is load bearing. The server decides on every request; this only
 * decides what to draw, so an account that edits it in the console gets 402s
 * and 403s rather than access.
 */

export type Plan = {
  id: string;
  label: string;
  price_usd: number;
  days: number;
  note?: string;
  rsi_tokens: number;
  mcap_tokens: number;
  mcap_checks_per_day: number;
  min_cadence: number;
  min_interval: number;
  telegram_alerts: boolean;
  current?: boolean;
};

export type Account = {
  username: string;
  email: string;
  role: string;
  email_verified: boolean;
  plan: string;
  plan_label: string;
  status: "trialing" | "active" | "expired" | "blocked" | "unverified";
  days_left: number;
  expires_at: number;
  /** Kept working on the house — its expiry is a placeholder, not a date. */
  comped: boolean;
  telegram_linked: boolean;
  usable: boolean;
  reason: string;
  limits: Record<string, number | boolean>;
  usage: { rsi_tokens: number; mcap_tokens: number; mcap_checks_today: number;
           ai_checks_today: number };
  plans: Plan[];
};

const CACHE_KEY = "account";

function remembered(): Account | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as Account) : undefined;
  } catch {
    return undefined;
  }
}

export function useAccount() {
  // Seeded from the last answer this browser had, so Profile, Plan and the
  // paywall paint immediately instead of showing a spinner on every visit and
  // a fresh one after every reload. It is still revalidated on mount — the
  // remembered copy decides what is drawn first, never what is true.
  const { data, error, isLoading, mutate } = useApi<Account>("/api/account/me", {
    refreshInterval: 0,
    revalidateOnFocus: true,
    fallbackData: remembered(),
  });

  useEffect(() => {
    if (data && typeof window !== "undefined") {
      try { localStorage.setItem(CACHE_KEY, JSON.stringify(data)); } catch {}
    }
  }, [data]);
  return {
    account: data,
    loading: isLoading && !data,
    error,
    reload: mutate,
    isAdmin: data?.role === "admin",
    // Undefined while it loads: a page that guesses "expired" for a frame
    // shows a paywall to somebody who has paid.
    usable: data ? data.usable : undefined,
  };
}

/** "3 days left", "expired", "trial" — the one line a header needs. */
export function statusLine(a?: Account): string {
  if (!a) return "";
  if (a.role === "admin") return "admin";
  // "27,148 days left" is a true sentence and a useless one. An account kept
  // on the house has a placeholder expiry decades out; say what it means.
  if (a.comped) return `${a.plan_label} — on the house, no expiry`;
  if (a.status === "trialing") return `Trial — ${a.days_left} day${a.days_left === 1 ? "" : "s"} left`;
  if (a.status === "active") return `${a.plan_label} — ${a.days_left} day${a.days_left === 1 ? "" : "s"} left`;
  if (a.status === "unverified") return "Email not confirmed";
  if (a.status === "blocked") return "Suspended";
  return "Expired";
}

export function statusTone(a?: Account): "green" | "amber" | "red" | "gray" {
  if (!a) return "gray";
  if (a.status === "active") return "green";
  if (a.status === "trialing") return a.days_left <= 2 ? "amber" : "green";
  if (a.status === "unverified") return "amber";
  return "red";
}
