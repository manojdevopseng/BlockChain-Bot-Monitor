"use client";

import useSWR, { SWRConfiguration } from "swr";

// Same-origin: next.config.js rewrites /api/* to the FastAPI backend in dev,
// nginx does it in production. No CORS juggling on the client.
//
// BASE_PATH is normally empty. It is set when this build is served under a
// prefix — a preview of the next version living beside the live one on the
// same host, where /beta/api has to reach the preview's backend rather than
// the live one two directories up.
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH || "").replace(/\/$/, "");
const BASE = BASE_PATH;

let token: string | null = null;
if (typeof window !== "undefined") {
  token = localStorage.getItem("token");
}

export function setToken(t: string | null) {
  token = t;
  if (typeof window !== "undefined") {
    if (t) localStorage.setItem("token", t);
    else {
      // Signing out drops what was remembered about who that was: the next
      // person to use this browser must not start on the last one's nav.
      localStorage.removeItem("token");
      localStorage.removeItem("role");
      localStorage.removeItem("account");
    }
  }
}

export function getToken(): string | null {
  return token;
}

// A 401 means the token is gone or expired. Drop it and send the user to the
// login page — without this every panel would just show its empty state and
// look like the bot had stopped producing data.
function onUnauthorized() {
  setToken(null);
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.href = `${BASE_PATH}/login`;
  }
}

// Pages that must stay reachable with an ended subscription: paying is how you
// get back in, and being bounced off the page that takes the payment is how an
// account stays ended.
const PAYWALL_SAFE = ["/plan", "/profile", "/login", "/register", "/verify",
                      "/forgot", "/reset"];

// 402 is the server saying "you may, but not until you pay". It is a different
// answer from 401 (nobody is logged in) and from 403 (not yours), so it gets
// its own destination rather than a red toast on an empty page.
function onPaymentRequired() {
  if (typeof window === "undefined") return;
  const here = window.location.pathname;
  if (!PAYWALL_SAFE.some((p) => here === p || here.startsWith(`${p}/`))) {
    window.location.href = `${BASE_PATH}/plan`;
  }
}

function headers(): HeadersInit {
  const h: HeadersInit = { "Content-Type": "application/json" };
  if (token) (h as any).Authorization = `Bearer ${token}`;
  return h;
}

export async function apiGet<T = any>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: headers() });
  if (res.status === 401) {
    onUnauthorized();
    throw new Error("Not authenticated");
  }
  if (res.status === 402) {
    onPaymentRequired();
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch {}
    throw new Error(detail || "Your access has ended");
  }
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

export async function apiSend<T = any>(
  path: string,
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  body?: any
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: headers(),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    onUnauthorized();
    throw new Error("Not authenticated");
  }
  // A write refused for want of a subscription reads as a message on the page
  // it was attempted from — "you have used 3 of 3" belongs beside the button,
  // not on a redirect.
  if (!res.ok) {
    // Routers answer with {"detail": "..."} — surfacing that is far more use
    // than "PUT /x -> 400".
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch {}
    throw new Error(detail || `${method} ${path} -> ${res.status}`);
  }
  return res.json();
}

export async function login(username: string, password: string) {
  const form = new URLSearchParams({ username, password });
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  if (!res.ok) throw new Error("Invalid credentials");
  const data = await res.json();
  setToken(data.access_token);
  return data;
}

const fetcher = (path: string) => apiGet(path);

// Polling SWR hook — realtime WS updates layer on top for instant pushes.
export function useApi<T = any>(path: string | null, cfg?: SWRConfiguration) {
  return useSWR<T>(path, fetcher, {
    // The WebSocket already pushes the moment anything happens, so the poll is
    // only a safety net for what no event covers. At 5s it was mostly redundant
    // work that repainted the page for nothing.
    refreshInterval: 12000,
    // Back on. It was off to stop the page repainting on every tab switch, but
    // the cause of that was the cache being wiped on revalidation, which is
    // fixed — and with it off, coming back to a tab showed whatever was on
    // screen when you left it. SWR also pauses polling while a tab is hidden,
    // so focus is exactly when a refresh is most wanted.
    revalidateOnFocus: true,
    // Keep showing the data we have while a new key loads. Without this a
    // filter or search change blanks the table for one frame before refilling.
    keepPreviousData: true,
    ...cfg,
  });
}


/** A call made by somebody who is not signed in — register, verify, reset. */
export async function apiPublic<T = any>(path: string, body: any): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch {}
    throw new Error(detail || `That did not work (${res.status})`);
  }
  return res.json();
}
