"use client";

import { Server, Clock, Activity, Database } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fmtUptime } from "@/lib/utils";

export default function SystemPage() {
  const { data } = useApi<any>("/api/system/overview");
  const { data: svcs } = useApi<any>("/api/system/services");
  const { data: m } = useApi<any>("/api/system/metrics", { refreshInterval: 10000 });
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
        <Card>
          <CardHeader><CardTitle>System Information</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {info.map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-border-soft py-1.5 text-sm">
                <span className="text-text-muted">{k}</span>
                <span className="font-mono text-text">{v ?? "—"}</span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Service Status</CardTitle></CardHeader>
          <CardContent className="space-y-1">
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Host Resources</CardTitle></CardHeader>
          <CardContent className="space-y-2">
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Data Retention</CardTitle></CardHeader>
          <CardContent className="space-y-2">
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
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
