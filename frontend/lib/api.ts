"use client";

import useSWR, { SWRConfiguration } from "swr";

// Same-origin: next.config.js rewrites /api/* to the FastAPI backend in dev,
// nginx does it in production. No CORS juggling on the client.
const BASE = "";

let token: string | null = null;
if (typeof window !== "undefined") {
  token = localStorage.getItem("token");
}

export function setToken(t: string | null) {
  token = t;
  if (typeof window !== "undefined") {
    if (t) localStorage.setItem("token", t);
    else localStorage.removeItem("token");
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
    window.location.href = "/login";
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
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

/** Fetch a protected binary — an image, say — as an object URL.
 *
 * An <img src> cannot carry an Authorization header, and the token lives in
 * localStorage rather than a cookie, so anything behind the login has to be
 * fetched and handed to the tag as a blob. The caller owns the URL and must
 * revoke it.
 */
export async function apiBlobUrl(path: string): Promise<string> {
  const res = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) {
    onUnauthorized();
    throw new Error("Not authenticated");
  }
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return URL.createObjectURL(await res.blob());
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
