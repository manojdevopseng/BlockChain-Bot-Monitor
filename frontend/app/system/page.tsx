"use client";

import { Server, Clock, Activity, Database } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { fmtUptime } from "@/lib/utils";

export default function SystemPage() {
  const { data } = useApi<any>("/api/system/overview");
  const { data: svcs } = useApi<any>("/api/system/services");
  const { data: m } = useApi<any>("/api/system/metrics", { refreshInterval: 10000 });
  const { data: act } = useApi<any>("/api/system/activity", { refreshInterval: 10000 });
  const { data: ret } = useApi<any>("/api/system/retention");

  const info = [
    ["Hostname", data?.hostname],
    ["OS", data?.os],
    ["Python", data?.python],
    ["DB Backend", data?.db_backend],
    ["DB Connected", data?.db_ok ? "yes" : "no (fallback)"],
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="System" subtitle="System information, health and configuration" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="System Status" value={data?.status ?? "—"} icon={Server} tone="green" />
        <StatCard label="Uptime" value={fmtUptime(data?.uptime_seconds ?? 0)} icon={Clock} tone="purple" />
        <StatCard label="Services" value={svcs?.items?.length ?? 0} icon={Activity} tone="blue" />
        <StatCard label="DB Backend" value={data?.db_backend ?? "—"} icon={Database} tone="cyan" />
      </div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <CollapsibleSection id="sys-info" title="System Information" bodyClass="space-y-2">
            {info.map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-border-soft py-1.5 text-sm">
                <span className="text-text-muted">{k}</span>
                <span className="font-mono text-text">{v ?? "—"}</span>
              </div>
            ))}
        </CollapsibleSection>
        <CollapsibleSection
          id="sys-activity"
          title="Last Activity"
          count={(act?.items ?? []).filter((a: any) => a.status === "quiet").length || undefined}
        >
          <p className="mb-3 text-xs text-text-dim">
            When each part of the bot last actually did something. A worker can
            be running and idle, which is not the same as working.
          </p>
          <TableScroll maxHeight={360}>
            <div className="space-y-1">
              {(act?.items ?? []).map((a: any) => (
                <div key={a.name} className="flex items-start justify-between gap-2 rounded-lg border border-border-soft px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="text-text">{a.label}</div>
                    <div className="text-[10px] text-text-dim">
                      {a.detail || (a.kind === "event"
                        ? "fires when the market does — silence is normal"
                        : "checked by the watchdog")}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-xs text-text-muted">
                      {a.age_seconds == null ? "—" : a.age_seconds < 60
                        ? `${a.age_seconds}s ago`
                        : `${Math.round(a.age_seconds / 60)}m ago`}
                    </span>
                    <Badge variant={
                      a.status === "ok" ? "green"
                        : a.status === "quiet" ? (a.kind === "tick" ? "red" : "gray")
                        : "gray"
                    }>
                      {a.status}
                    </Badge>
                  </div>
                </div>
              ))}
              {!act && <span className="text-xs text-text-dim">Loading…</span>}
            </div>
          </TableScroll>
        </CollapsibleSection>

        <CollapsibleSection id="sys-services" title="Service Status">
          <TableScroll maxHeight={360}>
          <div className="space-y-1">
            {(svcs?.items ?? []).map((s: any) => (
              <div key={s.id} className="flex items-start justify-between gap-2 rounded-lg border border-border-soft px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="text-text">{s.label}</div>
                  {/* Why it's down, so the row is actionable */}
                  {s.status === "stopped" && s.reason && (
                    <div className="text-[10px] text-accent-red">{s.reason}</div>
                  )}
                </div>
                <Badge variant={
                  s.status === "running" ? "green"
                    : s.status === "stopped" ? "red"
                    : "gray"
                }>
                  {s.status}
                </Badge>
              </div>
            ))}
          </div>
          </TableScroll>
        </CollapsibleSection>

        <CollapsibleSection id="sys-host" title="Host Resources" bodyClass="space-y-2">
            {[
              ["CPU", m?.cpu_percent != null ? `${m.cpu_percent}%` : "—"],
              ["Memory", m?.ram_used_gb != null ? `${m.ram_used_gb} / ${m.ram_total_gb} GB (${m.ram_percent}%)` : "—"],
              ["Disk", m?.disk_percent != null ? `${m.disk_percent}% used · ${m.disk_free_gb} GB free` : "—"],
              ["Network", m?.net_recv_mb != null ? `↓ ${Math.round(m.net_recv_mb)} MB · ↑ ${Math.round(m.net_sent_mb ?? 0)} MB` : "—"],
            ].map(([k, v]) => (
              <div key={k as string} className="flex justify-between border-b border-border-soft py-1.5 text-sm">
                <span className="text-text-muted">{k}</span>
                <span className="font-mono text-text">{v}</span>
              </div>
            ))}
        </CollapsibleSection>

        <CollapsibleSection id="sys-retention" title="Data Retention" bodyClass="space-y-2">
            <p className="mb-2 text-xs text-text-dim">
              Enforced by MongoDB TTL indexes — mongod expires old documents itself.
            </p>
            {Object.entries(ret?.collections ?? {}).map(([name, c]: [string, any]) => (
              <div key={name} className="flex justify-between border-b border-border-soft py-1.5 text-sm">
                <span className="text-text-muted">{name}</span>
                <span className="text-text">
                  {c.documents} docs
                  <span className="ml-2 text-text-dim">
                    {c.retention_days > 0 ? `${c.retention_days}d` : "kept"}
                  </span>
                </span>
              </div>
            ))}
        </CollapsibleSection>
      </div>
    </div>
  );
}
