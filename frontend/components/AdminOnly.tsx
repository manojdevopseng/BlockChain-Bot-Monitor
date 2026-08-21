"use client";

import { useRole } from "@/lib/hooks";

/* An operator control on a page a customer can also see.
 *
 * The shared panels — Detections, Commands, Chains — are one set of data
 * produced for everybody: a customer reads them, only an admin changes them.
 * The server has always enforced that (every non-GET on those routers is
 * admin-only), but the buttons were drawn for everyone, so a customer got a
 * switch that answered 403 and a delete that did nothing. Hiding them is a
 * courtesy on top of the rule, never the rule itself.
 *
 * Renders `fallback` instead where the column still needs something in it —
 * a badge showing the state, say, in place of the switch that sets it.
 *
 * Unknown role counts as not-admin, same as the nav: `useRole` fails closed. */
export function AdminOnly({ children, fallback = null }: {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { isAdmin } = useRole();
  return <>{isAdmin ? children : fallback}</>;
}
