import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtNum(n: number | undefined | null, opts?: { compact?: boolean }): string {
  if (n === undefined || n === null || isNaN(n as number)) return "—";
  if (opts?.compact && Math.abs(n) >= 1000) {
    return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n);
  }
  return Intl.NumberFormat("en").format(n);
}

export function fmtUsd(n: number | undefined | null): string {
  if (n === undefined || n === null) return "—";
  return "$" + fmtNum(n, { compact: true });
}

export function fmtEth(n: number | undefined | null): string {
  if (!n) return "0 ETH";
  return `${n.toFixed(5)} ETH`;
}

export function timeAgo(ts: number): string {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// Everything in this project is keyed on IST — retention days, the digest hour,
// the per-day counters — so timestamps are shown in IST rather than in UTC or
// in whatever zone the viewer's machine happens to be set to. fmtDateTime used
// toISOString(), which meant every displayed time was 5h30m behind what the
// backend had recorded for that day.
const IST = "Asia/Kolkata";
// sv-SE gives YYYY-MM-DD HH:mm:ss, the shape these columns already had.
const _istDateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: IST, year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const _istClock = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

export function fmtClock(ts: number): string {
  return _istClock.format(new Date(ts * 1000));
}

export function fmtDateTime(ts: number): string {
  // The suffix is not decoration: these values just moved by five and a half
  // hours, and a bare timestamp gives no way to tell which zone it is in.
  return `${_istDateTime.format(new Date(ts * 1000))} IST`;
}

export function shortAddr(addr: string | undefined | null, head = 6, tail = 4): string {
  if (!addr) return "—";
  if (addr.length <= head + tail + 1) return addr;
  return `${addr.slice(0, head)}…${addr.slice(-tail)}`;
}

export function fmtUptime(sec: number): string {
  if (!sec) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${d}d ${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m`;
}

// A row's own identity, for use as a React key in a table that refreshes.
// Keying by array index means a single new row arriving at the top shifts every
// row's content down one slot, so React rewrites every cell and the whole table
// visibly repaints. Keying by the row itself makes that one insertion.
export function rowKey(row: any, i: number): string {
  if (!row || typeof row !== "object") return String(i);
  const id = row.id ?? row._id ?? row.tx_hash ?? row.pair
    ?? row.token_address ?? row.address ?? row.sol_address;
  const at = row.created_at ?? row.alert_timestamp ?? row.detected_at
    ?? row.open_timestamp ?? row.dt ?? row.ts;
  if (id != null) return at != null ? `${id}:${at}` : String(id);
  return at != null ? `${at}:${i}` : String(i);
}

/** Badge colour for a component/endpoint status string.
 *
 * The backend emits a small fixed vocabulary — connected, running, configured,
 * ok, stopped, disabled, not configured, quiet, unknown — and three pages were
 * each mapping it to colours with their own ternary chain. They disagreed:
 * chains/page.tsx sent "configured" to red (its fall-through) while
 * rpc/page.tsx sent it to blue, for the same word from the same API.
 *
 * `quiet` is deliberately not handled here — on the System page it means one
 * thing for a heartbeat tick (red: something that should be beating is not)
 * and another for an event feed (grey: nothing happened, which is normal), so
 * the caller has to decide.
 */
export type StatusTone = "green" | "blue" | "amber" | "gray" | "red";

export function statusTone(status: string | undefined | null): StatusTone {
  switch ((status ?? "").toLowerCase()) {
    case "connected":
    case "running":
    case "ok":
      return "green";
    case "configured":
      return "blue";
    case "not configured":
      return "amber";
    case "disabled":
    case "unknown":
      return "gray";
    default:
      return "red";
  }
}
