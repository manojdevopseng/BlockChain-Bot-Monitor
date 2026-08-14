"use client";

import { useEffect, useState } from "react";

import { useApi } from "./api";

/**
 * Who is logged in, and the pages their account cannot use.
 *
 * `admin` gets everything. `user` sees the whole dashboard and changes nothing:
 * Forwarder, Commands and Settings are closed to it. This drives what the nav
 * greys out and what a page renders instead of itself — presentation only. The
 * same rule is enforced on every request server-side, so nothing here is load
 * bearing, and a user who edits it in the console gets 403s rather than access.
 */
// Operator surfaces. A customer never sees these in the nav and gets a 403
// from the server if they type the path — the hiding is a courtesy, the rule
// is in main.py. Commands is not here: it is readable by everyone and writable
// by nobody but an admin, which the server enforces per method.
export const ADMIN_ONLY_PATHS = ["/forwarder", "/settings", "/users", "/rpc",
                                 "/system", "/logs"] as const;

export function useRole() {
  // Cached hard: it changes at login, not while the page is open, and every
  // nav item asks. Undefined while it is in flight — the nav renders enabled
  // rather than flashing everything to disabled and back.
  const { data } = useApi<any>("/api/auth/me", {
    refreshInterval: 0,
    revalidateOnFocus: false,
    revalidateIfStale: false,
  });
  const role: string | undefined = data?.role;
  return {
    role,
    username: data?.username,
    // Unknown reads as admin so nothing flickers or blocks before the answer
    // arrives; the server is the one that actually decides.
    isAdmin: role === undefined || role === "admin",
    blocks: (path: string) =>
      role !== undefined && role !== "admin"
      && ADMIN_ONLY_PATHS.some((p) => path === p || path.startsWith(`${p}/`)),
  };
}

/**
 * A trailing debounce, for values that feed an SWR key.
 *
 * Every search box on this dashboard drives a request: typing straight into the
 * key fires one per character, and the list empties and refills each time. The
 * same 250ms wait had been written four times — twice as this hook and twice
 * inline — which is three copies too many for something every page needs to
 * agree on.
 */
export function useDebounced(value: string, ms = 250): string {
  const [out, setOut] = useState(value.trim());
  useEffect(() => {
    const t = setTimeout(() => setOut(value.trim()), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}
