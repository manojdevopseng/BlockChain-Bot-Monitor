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

export function fmtClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB");
}

export function fmtDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toISOString().slice(0, 19).replace("T", " ");
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
